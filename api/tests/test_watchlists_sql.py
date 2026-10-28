"""Watchlists and job-posting signals (#647) against real SQL, with the vendor faked."""

import contextlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from app.core.errors import err, ok
from app.core.scope import acting_as
from app.core.system_reasons import SystemReason
from app.people.apollo_models import JobPosting, JobPostingsResult
from app.watchlists import runner, service
from tests.conftest import act_in_own_org, call_system, migrated_database, pool_on, project, scoped_conn

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL required for watchlist SQL checks')

NOW = datetime.now(UTC)


class Vendor:
    """Apollo as the runner sees it: ``postings`` by vendor id, ``domains`` to vendor ids, ``down`` ids fail."""

    def __init__(self):
        self.is_configured = True
        self.postings: dict[str, list[JobPosting]] = {}
        self.domains: dict[str, str] = {}
        self.down: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    async def enrich_organization(self, domain):
        self.calls.append(('enrich', domain))
        if domain in self.domains:
            return ok(SimpleNamespace(id=self.domains[domain]))
        return err('CONTACTS.ORG_NOT_FOUND', detail='unknown domain')

    async def get_job_postings(self, *, org_id, org_name='', page=1):
        self.calls.append(('postings', org_id))
        if org_id == 'gone':
            return err('CONTACTS.ORG_NOT_FOUND', detail='no such organization')
        if org_id in self.down:
            return err('CONTACTS.UPSTREAM', detail='vendor unavailable')
        found = self.postings.get(org_id, [])
        return ok(JobPostingsResult(org_name=org_name, total=len(found), postings=found))

    def read(self) -> list[str]:
        return [org for kind, org in self.calls if kind == 'postings']


@pytest.fixture
def db(monkeypatch, balance_events):
    with migrated_database() as (conn, conninfo), pool_on(conninfo):
        scoped = scoped_conn(conn)

        @contextlib.contextmanager
        def connection(timeout=None):
            with conn.transaction(), scoped(timeout) as c:
                yield c

        monkeypatch.setattr(service, 'get_conn', connection)
        monkeypatch.setattr(runner, 'get_conn', connection)
        yield conn


@pytest.fixture
def vendor(monkeypatch):
    fake = Vendor()
    monkeypatch.setattr(runner, 'ApolloClient', lambda: fake)
    monkeypatch.setattr(runner, 'vendor_disabled_now', lambda provider: False)
    return fake


def _posting(title, url=None, *, days_ago=0.0, city='Milano'):
    posted = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    return JobPosting(title=title, city=city, country='Italy', url=url, posted_at=posted)


def _setup(db, accounts, *, credits=200, titles=('SDR',), entity='company'):
    """A user with credits and a company list of ``accounts`` (cells), watched for ``titles``.
    ``(user, watchlist, row ids)``."""
    uid = str(uuid.uuid4())
    db.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt")'
        " VALUES (%s, 'T', %s, false, now(), now())",
        (uid, f'{uid}@example.test'),
    )
    db.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining)'
        ' VALUES (default_organization(%s), %s, %s)',
        (uid, uid, credits),
    )
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        " VALUES (default_organization(%s), %s, %s, 'Accounts', %s) RETURNING id",
        (uid, project(db, uid), uid, entity),
    ).fetchone()['id']
    rows = [
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id',
            (lid, Jsonb(cells), str(i)),
        ).fetchone()['id']
        for i, cells in enumerate(accounts)
    ]
    if entity != 'company':
        return uid, {'list_id': lid}, rows
    wid = db.execute(
        'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
        " VALUES (%s, %s, 'Hiring', %s, 'weekly', now() + interval '1 day') RETURNING id",
        (uid, lid, list(titles)),
    ).fetchone()['id']
    return uid, db.execute('SELECT * FROM watchlists WHERE id = %s', (wid,)).fetchone(), rows


def _as_owner(db, watchlist):
    """The scope of the run task (app/worker.py ``run_watchlist``): the watchlist's creator, in its organization."""
    org = db.execute('SELECT organization_id::text AS o FROM lists WHERE id = %s', (watchlist['list_id'],)).fetchone()
    return acting_as(org['o'], str(watchlist['user_id']))


async def _run(db, watchlist):
    """Queue a run as the beat does, run it, and answer its row."""
    run = service.enqueue(db, watchlist['id'], datetime.now(UTC))
    with _as_owner(db, watchlist):
        await runner.run_watchlist(run['id'])
    return db.execute('SELECT * FROM watchlist_runs WHERE id = %s', (run['id'],)).fetchone()


def _balance(db, uid):
    return db.execute('SELECT credits_remaining AS n FROM user_credits WHERE user_id = %s', (uid,)).fetchone()['n']


def _signals(db):
    rows = db.execute(
        "SELECT s.data ->> 'title' AS title, signal_score(s.score, s.occurred_at) AS score FROM signals s"
        ' ORDER BY s.first_seen_at, title'
    ).fetchall()
    return [(r['title'], float(r['score'])) for r in rows]


async def test_a_run_records_each_matching_posting_once_and_only_new_ones_later(db, vendor):
    """Done when (#647): a posting in a watched function is one signal, whatever is run again."""
    uid, watchlist, _ = _setup(
        db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}, {'name': 'Quiet', 'apollo_id': 'o2'}]
    )
    vendor.postings['o1'] = [
        _posting('Senior SDR', 'https://jobs.test/1'),
        _posting('Backend engineer', 'https://jobs.test/2'),  # not a watched function
        _posting('SDR Italia', days_ago=30),  # no link: its title and place identify it
    ]

    run = await _run(db, watchlist)
    assert (run['status'], run['credits_spent'], run['accounts_checked'], run['signals_new']) == ('succeeded', 1, 2, 2)
    assert run['accounts_left'] == 0
    assert _balance(db, uid) == 199  # one credit: the account that has postings; the quiet one is free
    # The decision on #122: a posting a month old is worth half a fresh one.
    assert sorted(_signals(db)) == [('SDR Italia', 50.0), ('Senior SDR', 100.0)]

    again = await _run(db, watchlist)
    assert (again['signals_new'], again['credits_spent']) == (0, 1) and len(_signals(db)) == 2

    vendor.postings['o1'].append(_posting('SDR Team Lead', 'https://jobs.test/3'))
    third = await _run(db, watchlist)
    assert third['signals_new'] == 1 and len(_signals(db)) == 3


async def test_the_credit_cap_stops_a_run_and_the_rest_goes_first_next_time(db, vendor, monkeypatch):
    monkeypatch.setattr(runner, 'CREDIT_CAP', 2)
    uid, watchlist, _ = _setup(db, [{'name': f'A{i}', 'apollo_id': f'o{i}'} for i in range(5)])
    for i in range(5):
        vendor.postings[f'o{i}'] = [_posting('SDR', f'https://jobs.test/{i}')]

    first = await _run(db, watchlist)
    assert (first['credits_spent'], first['accounts_checked'], first['accounts_left']) == (2, 2, 3)
    assert vendor.read() == ['o0', 'o1'] and _balance(db, uid) == 198

    vendor.calls.clear()
    second = await _run(db, watchlist)
    assert vendor.read() == ['o2', 'o3'] and second['accounts_left'] == 3  # the five, less the two it read

    vendor.calls.clear()
    await _run(db, watchlist)
    assert vendor.read() == ['o4', 'o0']  # the one left, then the rotation starts again from the oldest check


def _read_past(monkeypatch, then=None):
    """A reader that ignores its bound and reads every account, then runs ``then``: the store must still stop the
    run at its quote."""
    real = runner.check_accounts

    async def every(accounts, _amount):
        read = await real(accounts, len(accounts))
        if then:
            then()
        return read

    monkeypatch.setattr(runner, 'check_accounts', every)


async def test_a_run_stops_at_its_quote_and_leaves_the_rest_unchecked(db, vendor, monkeypatch):
    monkeypatch.setattr(runner, 'CREDIT_CAP', 2)
    _read_past(monkeypatch)
    uid, watchlist, _ = _setup(db, [{'name': f'A{i}', 'apollo_id': f'o{i}'} for i in range(5)])
    for i in range(5):
        vendor.postings[f'o{i}'] = [_posting('SDR', f'https://jobs.test/{i}')]

    run = await _run(db, watchlist)
    assert (run['status'], run['credits_spent'], run['accounts_checked'], run['accounts_left']) == (
        'succeeded',
        2,
        2,
        3,
    )
    assert _balance(db, uid) == 198 and len(_signals(db)) == 2
    # The three past the quote were never checked, so the next run reads them first.
    assert db.execute('SELECT count(*) AS n FROM watchlist_accounts').fetchone()['n'] == 2


async def test_an_account_deleted_during_the_run_is_not_billed(db, vendor, monkeypatch):
    uid, watchlist, rows = _setup(db, [{'name': 'Acme', 'apollo_id': 'o1'}, {'name': 'Gone', 'apollo_id': 'o2'}])
    vendor.postings = {'o1': [_posting('SDR', 'https://jobs.test/1')], 'o2': [_posting('SDR', 'https://jobs.test/2')]}
    _read_past(monkeypatch, then=lambda: db.execute('DELETE FROM list_rows WHERE id = %s', (rows[1],)))

    run = await _run(db, watchlist)
    assert (run['status'], run['credits_spent'], run['signals_new']) == ('succeeded', 1, 1)
    assert _balance(db, uid) == 199


async def test_a_vendor_failure_fails_the_run_and_keeps_what_was_read(db, vendor, monkeypatch):
    monkeypatch.setattr(runner, 'CONCURRENCY', 1)
    uid, watchlist, _ = _setup(db, [{'name': f'A{i}', 'apollo_id': f'o{i}'} for i in range(3)])
    vendor.postings['o0'] = [_posting('SDR', 'https://jobs.test/0')]
    vendor.down.add('o1')

    run = await _run(db, watchlist)
    assert (run['status'], run['error_code']) == ('failed', 'vendor_failed')
    assert (run['credits_spent'], run['accounts_checked'], run['accounts_skipped'], run['accounts_left']) == (
        1,
        1,
        1,
        1,
    )
    assert _balance(db, uid) == 199 and len(_signals(db)) == 1  # what was read is kept and paid, nothing else

    vendor.down.clear()
    vendor.calls.clear()
    assert (await _run(db, watchlist))['status'] == 'succeeded'
    # The one the failed run did not reach comes first. The one the vendor failed on went to the back with the
    # rest, so an account it always fails on cannot hold the rotation.
    assert vendor.read() == ['o2', 'o0', 'o1']


async def test_an_account_is_found_by_its_domain_once_and_one_nothing_identifies_is_skipped(db, vendor):
    _, watchlist, rows = _setup(
        db,
        [
            {'name': 'Acme', 'domain': 'acme.it'},
            {'name': 'No domain'},
            {'name': 'Ghost', 'domain': 'ghost.it'},
            {'name': 'Stale id', 'apollo_id': 'gone'},
        ],
    )
    vendor.domains['acme.it'] = 'o9'
    vendor.postings['o9'] = [_posting('SDR', 'https://jobs.test/9')]

    run = await _run(db, watchlist)
    assert (run['status'], run['accounts_checked'], run['accounts_skipped'], run['signals_new']) == (
        'succeeded',
        1,
        3,
        1,
    )
    state = db.execute('SELECT row_id, vendor_id, skip_reason FROM watchlist_accounts').fetchall()
    assert {(r['row_id'], r['vendor_id'], r['skip_reason']) for r in state} == {
        (rows[0], 'o9', None),
        (rows[1], None, 'no_domain'),
        (rows[2], None, 'not_found'),
        (rows[3], None, 'not_found'),
    }

    vendor.calls.clear()
    await _run(db, watchlist)
    assert ('enrich', 'acme.it') not in vendor.calls  # resolved once


async def test_nothing_is_read_without_credits_while_paused_or_with_the_vendor_off(db, vendor, monkeypatch):
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'apollo_id': 'o1'}], credits=0)
    run = await _run(db, watchlist)
    assert (run['status'], run['error_code']) == ('failed', 'insufficient_credits') and vendor.calls == []

    db.execute('UPDATE user_credits SET credits_remaining = 10 WHERE user_id = %s', (uid,))
    db.execute('UPDATE watchlists SET enabled = false')
    run = await _run(db, watchlist)
    assert (run['status'], run['error_code']) == ('cancelled', 'watchlist_paused')

    db.execute('UPDATE watchlists SET enabled = true')
    monkeypatch.setattr(runner, 'vendor_disabled_now', lambda provider: provider == 'apollo')
    run = await _run(db, watchlist)
    assert (run['status'], run['error_code'], run['credits_spent']) == ('failed', 'vendor_disabled', 0)
    assert vendor.calls == [] and _balance(db, uid) == 10  # the hold went back


async def test_a_signal_is_first_seen_when_the_run_inserts_it_not_when_its_transaction_began(db, vendor):
    """A signal automation reads ``first_seen_at`` against its last evaluation (``signal_trigger.LATE_COMMIT``): the
    run's lock waits must not move the signal back in time."""
    _, watchlist, _ = _setup(db, [{'name': 'Acme', 'apollo_id': 'o1'}])
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = await _run(db, watchlist)
    seen = db.execute('SELECT first_seen_at FROM signals').fetchone()['first_seen_at']
    assert seen > run['finished_at']  # finished_at is now(): the start of the transaction that inserted the signal


async def test_a_run_whose_hold_expired_writes_nothing(db, vendor):
    _, watchlist, _ = _setup(db, [{'name': 'Acme', 'apollo_id': 'o1'}])
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = service.enqueue(db, watchlist['id'], datetime.now(UTC))
    act_in_own_org(db, watchlist['user_id'])
    claimed = runner.claim(run['id'])
    done, error = await runner.check_accounts(claimed['accounts'], claimed['quote'].rows)
    db.execute(
        "UPDATE credit_transactions SET status = 'released' WHERE id = (SELECT reservation_id FROM watchlist_runs)"
    )

    runner.finish(run['id'], ['SDR'], claimed['quote'], done, error)
    row = db.execute('SELECT status, error_code FROM watchlist_runs').fetchone()
    assert (row['status'], row['error_code']) == ('unknown', 'reservation_expired') and _signals(db) == []


def test_the_beat_queues_a_due_watchlist_once_and_moves_it_to_its_next_slot(db):
    _setup(db, [{'name': 'Acme', 'apollo_id': 'o1'}])
    was = datetime.now(UTC) - timedelta(days=15, hours=1)
    db.execute('UPDATE watchlists SET next_run_at = %s', (was,))

    queued = call_system(SystemReason.DUE_WORK, service.schedule_due)
    assert (
        len(queued) == 1 and call_system(SystemReason.DUE_WORK, service.schedule_due) == queued
    )  # still the same queued run
    moved = db.execute('SELECT next_run_at FROM watchlists').fetchone()['next_run_at']
    assert moved == was + timedelta(days=21)  # two missed weeks are one run, on the same weekday and hour

    db.execute("UPDATE watchlist_runs SET status = 'running', started_at = now() - interval '1 hour'")
    call_system(SystemReason.DUE_WORK, runner.maintain)
    stale = db.execute('SELECT status, error_code FROM watchlist_runs').fetchone()
    assert (stale['status'], stale['error_code']) == ('unknown', 'worker_outcome_unknown')


def test_api_crud_is_scoped_to_the_project_and_takes_only_company_lists(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import middleware
    from app.main import app

    monkeypatch.setattr(middleware, 'get_conn', service.get_conn)
    monkeypatch.setattr('app.projects.routes.get_conn', service.get_conn)
    uid, watchlist, rows = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _, foreign, _ = _setup(db, [{'name': 'Other'}])
    _, people, _ = _setup(db, [], entity='person')
    key = 'sbk_watchlists-test'
    db.execute(
        'INSERT INTO user_api_keys (organization_id, user_id, label, key_hash)'
        " VALUES (default_organization(%s), %s, 'W', %s)",
        (uid, uid, middleware.hash_api_key(key)),
    )
    tc = TestClient(app)
    assert tc.get('/v1/watchlists').status_code == 401
    tc.headers['Authorization'] = f'Bearer {key}'

    (listed,) = tc.get('/v1/watchlists').json()['watchlists']
    assert (listed['id'], listed['list_name'], listed['accounts'], listed['last_run']) == (
        str(watchlist['id']),
        'Accounts',
        1,
        None,
    )
    body = {'name': 'Sales hires', 'list_id': str(watchlist['list_id']), 'job_titles': [' SDR ', 'SDR', 'AE']}
    made = tc.post('/v1/watchlists', json=body)
    assert made.status_code == 201 and made.json()['job_titles'] == ['SDR', 'AE']
    for bad in (people['list_id'], foreign['list_id']):
        refused = tc.post('/v1/watchlists', json={**body, 'list_id': str(bad)})
        assert refused.status_code == 404 and refused.json()['detail']['code'] == 'WATCHLIST.LIST_NOT_FOUND'
    assert tc.post('/v1/watchlists', json={**body, 'job_titles': [' ']}).status_code == 422

    url = f'/v1/watchlists/{made.json()["id"]}'
    assert tc.get(f'/v1/watchlists/{foreign["id"]}/signals').status_code == 404
    assert tc.post(f'{url}/run').status_code == 202
    assert tc.get(f'{url}/runs').json()['runs'][0]['status'] == 'queued'
    paused = tc.patch(url, json={'enabled': False, 'cadence': 'daily'}).json()
    assert (paused['enabled'], paused['cadence'], paused['last_run']['status']) == (False, 'daily', 'cancelled')
    assert tc.post(f'{url}/run').json()['detail']['code'] == 'WATCHLIST.PAUSED'

    db.execute(
        'INSERT INTO signals (watchlist_id, row_id, type, key, data, score, occurred_at)'
        " VALUES (%s, %s, 'job_posting', 'k', %s, 100, now() - interval '60 days')",
        (made.json()['id'], rows[0], Jsonb({'title': 'SDR', 'city': 'Milano', 'url': 'https://jobs.test/1'})),
    )
    (signal,) = tc.get(f'{url}/signals').json()['signals']
    assert (signal['account_name'], signal['account_domain'], signal['title'], signal['score']) == (
        'Acme',
        'acme.it',
        'SDR',
        25.0,
    )
    assert tc.delete(url).status_code == 204 and tc.get(f'{url}/runs').status_code == 404
    assert db.execute('SELECT count(*) AS n FROM signals').fetchone()['n'] == 0

    monkeypatch.setattr(service, 'MAX_PER_PROJECT', 1)
    assert tc.post('/v1/watchlists', json=body).json()['detail']['code'] == 'WATCHLIST.LIMIT'
