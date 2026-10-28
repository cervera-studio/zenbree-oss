"""A signal adds the account's people to a campaign (#648) against real SQL, with the vendor faked."""

import contextlib
import contextvars
import threading
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.watchlists import enroll, runner, service
from app.watchlists.models import WatchlistPatch
from tests import test_watchlists_sql as base
from tests.conftest import conninfo_of, project, scoped_conn, wait_for_a_lock
from tests.test_campaign_targets_row_sql import linked
from tests.test_watchlists_sql import DB_URL, _as_owner, _posting, _run, _setup, pytestmark

db, vendor = base.db, base.vendor  # the fixtures of the watchlist tests
__all__ = ['DB_URL', 'pytestmark']


def _people(db, uid, people):
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        " VALUES (default_organization(%s), %s, %s, 'People', 'person') RETURNING id",
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    for i, cells in enumerate(people):
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s)', (lid, Jsonb(cells), f'p{i}')
        )


def _campaign(db, uid, watchlist, threshold=60, status='draft'):
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " VALUES (default_organization(%s), %s, %s, 'C', %s) RETURNING id",
        (uid, project(db, uid), uid, status),
    ).fetchone()['id']
    db.execute(
        'UPDATE watchlists SET campaign_id = %s, campaign_threshold = %s WHERE id = %s',
        (cid, threshold, watchlist['id']),
    )
    return cid


def _targets(db, cid):
    return db.execute(
        'SELECT t.email, t.status, t.source, s.data ->> %s AS why FROM outreach_campaign_targets t'
        ' LEFT JOIN signals s ON s.id = t.signal_id WHERE t.campaign_id = %s ORDER BY t.email',
        ('title', cid),
    ).fetchall()


@pytest.fixture(autouse=True)
def _enroll_on_the_test_connection(db, monkeypatch):
    monkeypatch.setattr(enroll, 'get_conn', runner.get_conn)


async def test_a_signal_over_the_threshold_enrolls_the_accounts_people_once(db, vendor):
    uid, watchlist, _ = _setup(
        db,
        [
            {'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'},
            {'name': 'Empty', 'domain': 'empty.it', 'apollo_id': 'o2'},
            {'name': 'Old', 'domain': 'old.it', 'apollo_id': 'o3'},
        ],
    )
    _people(
        db,
        uid,
        [
            {'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'www.acme.it'},
            {'full_name': 'Bob Neri', 'email': 'bob@acme.it', 'company_name': 'ACME'},  # by name: no domain on the row
            {'full_name': 'Eve Elsewhere', 'email': 'eve@other.it', 'company_domain': 'other.it'},
            {'full_name': 'Old Timer', 'email': 'old@old.it', 'company_domain': 'old.it'},
        ],
    )
    cid = _campaign(db, uid, watchlist)
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    vendor.postings['o2'] = [_posting('SDR', 'https://jobs.test/2')]  # no known person
    vendor.postings['o3'] = [_posting('SDR', 'https://jobs.test/3', days_ago=90)]  # decayed under the threshold

    run = await _run(db, watchlist)
    assert (run['people_enrolled'], run['accounts_without_people']) == (2, 1)
    assert [(t['email'], t['status'], t['source'], t['why']) for t in _targets(db, cid)] == [
        ('ada@acme.it', 'draft', 'signal', 'SDR'),
        ('bob@acme.it', 'draft', 'signal', 'SDR'),
    ]
    assert linked(db, cid) == [('ada@acme.it', 'ada@acme.it'), ('bob@acme.it', 'bob@acme.it')]

    # A retry of the enrollment and a later run enter nobody twice for the same signal.
    with _as_owner(db, watchlist):
        enroll.enroll_signals(run['id'])
    again = await _run(db, watchlist)
    assert len(_targets(db, cid)) == 2 and again['people_enrolled'] == 0
    assert db.execute('SELECT count(*) AS n FROM signals WHERE enrolled_at IS NOT NULL').fetchone()['n'] == 1

    # The account with no known person keeps its signal: a person who joins a list later still enters.
    _people(db, uid, [{'full_name': 'Late Joiner', 'email': 'late@empty.it', 'company_domain': 'empty.it'}])
    await _run(db, watchlist)
    assert [t['email'] for t in _targets(db, cid)] == ['ada@acme.it', 'bob@acme.it', 'late@empty.it']


async def test_enrollment_follows_the_one_rule_and_stops_with_the_campaign(db, vendor):
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    cid = _campaign(db, uid, watchlist)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'email', 'ada@acme.it')",
        (project(db, uid), uid),
    )
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = await _run(db, watchlist)
    assert run['people_enrolled'] == 0 and _targets(db, cid) == []

    db.execute("UPDATE outreach_campaigns SET status = 'stopped' WHERE id = %s", (cid,))
    db.execute('DELETE FROM exclusions')
    assert (await _run(db, watchlist))['people_enrolled'] == 0 and _targets(db, cid) == []


def test_a_campaign_and_a_threshold_come_together(db):
    assert uuid.UUID(str(_setup(db, [{'name': 'A'}])[1]['id']))
    from pydantic import ValidationError

    from app.watchlists.models import WatchlistIn

    base = {'name': 'W', 'list_id': str(uuid.uuid4()), 'job_titles': ['SDR']}
    for bad in ({'campaign_id': str(uuid.uuid4())}, {'campaign_threshold': 50}):
        try:
            WatchlistIn(**base, **bad)
        except ValidationError:
            continue
        raise AssertionError('a lone campaign or threshold was accepted')


async def test_the_api_names_the_campaign_and_the_campaign_shows_the_signal(db, vendor, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import middleware
    from app.main import app
    from app.outreach import routes as outreach_routes
    from app.watchlists import service

    monkeypatch.setattr(middleware, 'get_conn', service.get_conn)
    monkeypatch.setattr('app.projects.routes.get_conn', service.get_conn)
    monkeypatch.setattr('app.outreach.routes.get_conn', service.get_conn)
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    cid = _campaign(db, uid, watchlist)
    db.execute('UPDATE watchlists SET campaign_id = NULL, campaign_threshold = NULL WHERE id = %s', (watchlist['id'],))
    db.execute(
        'INSERT INTO user_api_keys (organization_id, user_id, label, key_hash)'
        " VALUES (default_organization(%s), %s, 'W', %s)",
        (uid, uid, middleware.hash_api_key('sbk_signal-campaign')),
    )
    tc = TestClient(app)
    tc.headers['Authorization'] = 'Bearer sbk_signal-campaign'
    url = f'/v1/watchlists/{watchlist["id"]}'

    assert tc.patch(url, json={'campaign_id': str(cid)}).status_code == 422  # a threshold goes with it
    named = tc.patch(url, json={'campaign_id': str(cid), 'campaign_threshold': 60}).json()
    assert (named['campaign_id'], named['campaign_threshold']) == (str(cid), 60)
    missing = tc.patch(url, json={'campaign_id': str(uuid.uuid4()), 'campaign_threshold': 60})
    assert missing.json()['detail']['code'] == 'WATCHLIST.CAMPAIGN_NOT_FOUND'

    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = await _run(db, watchlist)
    assert run['people_enrolled'] == 1
    org = db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    with _as_owner(db, watchlist):
        (person,) = outreach_routes.targets(cid, SimpleNamespace(org_id=str(org)))['items']
    assert (person['first_name'], person['signal_title']) == ('Ada', 'SDR')

    cleared = tc.patch(url, json={'campaign_id': None, 'campaign_threshold': None}).json()
    assert (cleared['campaign_id'], cleared['campaign_threshold']) == (None, None)


@pytest.mark.parametrize('status', ['active', 'paused'])
async def test_people_enter_a_running_campaign_as_active_so_it_contacts_them(db, vendor, status):
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    cid = _campaign(db, uid, watchlist, status=status)
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    await _run(db, watchlist)
    assert [(t['email'], t['status']) for t in _targets(db, cid)] == [('ada@acme.it', 'active')]


async def test_a_signal_is_kept_while_its_people_are_in_another_campaign(db, vendor):
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    cid = _campaign(db, uid, watchlist)
    db.execute('UPDATE outreach_campaigns SET block_active_duplicates = true WHERE id = %s', (cid,))
    other = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " VALUES (default_organization(%s), %s, %s, 'Other', 'active') RETURNING id",
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status)'
        " VALUES (%s, %s, 'ada@acme.it', 'active')",
        (other, uid),
    )
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = await _run(db, watchlist)
    assert (run['people_enrolled'], run['people_left_out']) == (0, 1) and _targets(db, cid) == []
    assert db.execute('SELECT count(*) AS n FROM signals WHERE enrolled_at IS NOT NULL').fetchone()['n'] == 0

    db.execute('DELETE FROM outreach_campaign_targets WHERE campaign_id = %s', (other,))
    await _run(db, watchlist)
    assert [t['email'] for t in _targets(db, cid)] == ['ada@acme.it']


async def test_a_signal_is_kept_while_instantly_holds_its_people(db, vendor):
    """A person Instantly holds is left out as held_by_instantly (#929); the signal waits for the hold to end."""
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    cid = _campaign(db, uid, watchlist)
    db.execute('UPDATE outreach_campaigns SET steps = \'[{"channel": "email"}]\'::jsonb WHERE id = %s', (cid,))
    org, pid = db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o'], project(db, uid)
    conn = db.execute(
        "INSERT INTO instantly_connections (organization_id, project_id, name, workspace_id) VALUES (%s, %s, 'x', 'ws')"
        ' RETURNING id',
        (org, pid),
    ).fetchone()['id']
    batch = db.execute(
        'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, workspace_id, campaign_id,'
        " status) VALUES (%s, %s, %s, 'ws', 'camp', 'completed') RETURNING id",
        (org, pid, conn),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,'
        " outcome) VALUES (%s, %s, %s, 'ws', 'camp', 'ada@acme.it', 'accepted')",
        (org, pid, batch),
    )
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = await _run(db, watchlist)
    assert (run['people_enrolled'], run['people_left_out']) == (0, 1) and _targets(db, cid) == []
    assert db.execute('SELECT count(*) AS n FROM signals WHERE enrolled_at IS NOT NULL').fetchone()['n'] == 0

    db.execute("UPDATE instantly_leads SET outcome = 'skipped'")
    await _run(db, watchlist)
    assert [t['email'] for t in _targets(db, cid)] == ['ada@acme.it']


def _change_link_after_the_read(db, monkeypatch, sql, args):
    """The update that commits between enrollment's first read of the link and its locked transaction."""
    known = enroll._known_people

    def read_then_update(watchlist):
        people = known(watchlist)
        db.execute(sql, args)
        return people

    monkeypatch.setattr(enroll, '_known_people', read_then_update)


async def _linked_run(db, vendor):
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    cid = _campaign(db, uid, watchlist)
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    return watchlist, cid


async def test_a_link_cleared_before_the_commit_enrolls_nobody(db, vendor, monkeypatch):
    watchlist, cid = await _linked_run(db, vendor)
    _change_link_after_the_read(
        db,
        monkeypatch,
        'UPDATE watchlists SET campaign_id = NULL, campaign_threshold = NULL WHERE id = %s',
        (watchlist['id'],),
    )
    run = await _run(db, watchlist)
    assert run['people_enrolled'] == 0 and _targets(db, cid) == []
    assert db.execute('SELECT count(*) AS n FROM signals WHERE enrolled_at IS NOT NULL').fetchone()['n'] == 0


async def test_a_link_moved_before_the_commit_enrolls_nobody_in_the_old_campaign(db, vendor, monkeypatch):
    watchlist, cid = await _linked_run(db, vendor)
    other = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " SELECT organization_id, project_id, owner_id, 'Other', 'draft' FROM outreach_campaigns WHERE id = %s"
        ' RETURNING id',
        (cid,),
    ).fetchone()['id']
    _change_link_after_the_read(
        db, monkeypatch, 'UPDATE watchlists SET campaign_id = %s WHERE id = %s', (other, watchlist['id'])
    )
    await _run(db, watchlist)
    assert _targets(db, cid) == []


async def test_an_unchanged_link_still_enrolls(db, vendor, monkeypatch):
    watchlist, cid = await _linked_run(db, vendor)
    _change_link_after_the_read(
        db, monkeypatch, 'UPDATE watchlists SET updated_at = now() WHERE id = %s', (watchlist['id'],)
    )
    assert (await _run(db, watchlist))['people_enrolled'] == 1
    assert [t['email'] for t in _targets(db, cid)] == ['ada@acme.it']


class _Gate:
    """A connection that stops before the first statement containing ``before`` until ``release`` is set."""

    def __init__(self, conn, before, reached, release):
        self._conn, self._before, self._reached, self._release = conn, before, reached, release

    def execute(self, sql, *args):
        if self._before in sql:
            self._reached.set()
            assert self._release.wait(10), 'the gate was never released'
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _second_session(db, conninfo_of_db):
    conn = psycopg.connect(conninfo_of_db, autocommit=True, row_factory=dict_row)
    return conn, scoped_conn(conn)


def _in_thread(fn, errors):
    ctx = contextvars.copy_context()

    def run():
        try:
            ctx.run(fn)
        except Exception as e:
            errors.append(type(e).__name__)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def _sessions(db, monkeypatch, gate_before):
    """Enrollment on its own connection, stopped before ``gate_before``, and the API's update on another."""
    info = conninfo_of(db)
    enroller, enroll_conn = _second_session(db, info)
    updater, update_conn = _second_session(db, info)
    reached, release = threading.Event(), threading.Event()

    @contextlib.contextmanager
    def gated(timeout=None):
        with enroll_conn(timeout) as c:
            yield _Gate(c, gate_before, reached, release)

    monkeypatch.setattr(enroll, 'get_conn', gated)
    monkeypatch.setattr(service, 'get_conn', update_conn)
    return info, (enroller, updater), reached, release


def _owner(db, watchlist):
    row = db.execute('SELECT organization_id::text AS org FROM lists WHERE id = %s', (watchlist['list_id'],)).fetchone()
    return SimpleNamespace(id=str(watchlist['user_id']), org_id=row['org'], org_role='admin')


async def _signalled_watchlist(db, vendor):
    """A watchlist whose signals wait for a campaign; the campaign is linked after, so no run enrolled them."""
    uid, watchlist, _ = _setup(db, [{'name': 'Acme', 'domain': 'acme.it', 'apollo_id': 'o1'}])
    _people(db, uid, [{'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'company_domain': 'acme.it'}])
    vendor.postings['o1'] = [_posting('SDR', 'https://jobs.test/1')]
    run = await _run(db, watchlist)
    return uid, watchlist, run['id'], _campaign(db, uid, watchlist)


async def test_an_update_waits_for_the_enrollment_that_holds_the_link(db, vendor, monkeypatch):
    uid, watchlist, run_id, cid = await _signalled_watchlist(db, vendor)
    info, conns, reached, release = _sessions(db, monkeypatch, 'enrolled_at IS NULL FOR UPDATE')
    errors: list[str] = []
    try:
        with _as_owner(db, watchlist):
            enrolling = _in_thread(lambda: enroll.enroll_signals(run_id), errors)
            assert reached.wait(10)
            clear = WatchlistPatch(campaign_id=None, campaign_threshold=None)
            updating = _in_thread(
                lambda: service.update(_owner(db, watchlist), project(db, uid), watchlist['id'], clear), errors
            )
            wait_for_a_lock(info, 'the update', conns[1].info.backend_pid, unless=updating)
            assert updating.is_alive(), errors  # the clear does not commit while the enrollment holds the link
            release.set()
            enrolling.join(10)
            updating.join(10)
    finally:
        release.set()
        for c in conns:
            c.close()
    assert errors == []
    assert [t['email'] for t in _targets(db, cid)] == ['ada@acme.it']  # the enrollment began first: it finishes
    assert (
        db.execute('SELECT campaign_id FROM watchlists WHERE id = %s', (watchlist['id'],)).fetchone()['campaign_id']
        is None
    )


async def test_moving_the_link_back_and_forth_while_a_run_enrolls_does_not_deadlock(db, vendor, monkeypatch):
    uid, watchlist, run_id, held = await _signalled_watchlist(db, vendor)
    away = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " SELECT organization_id, project_id, owner_id, 'Away', 'draft' FROM outreach_campaigns WHERE id = %s"
        ' RETURNING id',
        (held,),
    ).fetchone()['id']
    info, conns, reached, release = _sessions(db, monkeypatch, 'FROM watchlists WHERE id')
    errors: list[str] = []
    pid = project(db, uid)

    def move(campaign):
        service.update(
            _owner(db, watchlist), pid, watchlist['id'], WatchlistPatch(campaign_id=campaign, campaign_threshold=60)
        )

    try:
        with _as_owner(db, watchlist):
            enrolling = _in_thread(lambda: enroll.enroll_signals(run_id), errors)  # holds the campaign, wants the link
            assert reached.wait(10)
            move(away)  # the first move commits
            back = _in_thread(lambda: move(held), errors)  # holds the link, wants the campaign
            wait_for_a_lock(info, 'the move back', conns[1].info.backend_pid, unless=back)
            release.set()
            enrolling.join(10)
            back.join(10)
    finally:
        release.set()
        for c in conns:
            c.close()
    assert errors == []
    assert _targets(db, held) == []  # the link had moved when the run looked
    assert (
        db.execute('SELECT campaign_id FROM watchlists WHERE id = %s', (watchlist['id'],)).fetchone()['campaign_id']
        == held
    )
