"""The signal trigger of automations (#1030) against real SQL: the migration, the trigger's checks, the beat that
starts one run for each account whose unused signals reach the minimum, and the run that works on that account."""

import os
import re
import shutil
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import get_args

import psycopg
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb

from app.agent.tools.handlers import search_people
from app.agent.tools.registry import ToolResult
from app.automations import gate, runner, service, signal_trigger
from app.automations.models import Definition
from app.automations.run import Run
from app.automations.triggers import TriggerKind
from app.core.db import get_conn
from app.core.migrate import MIGRATIONS, migrate
from app.core.scope import acting_as, acting_for
from app.core.system_reasons import SystemReason
from app.lists.people_match import PeopleIndex
from app.watchlists import enroll
from tests.automations_support import client, database, make_campaign, make_list, user
from tests.conftest import call_system, empty_database, member, migrated_database, pool_on, project

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')
db = pytest.fixture(database)
MIGRATION = '20261021105227_automation_signals.sql'
SINCE = datetime(2026, 10, 1, tzinfo=UTC)
EARLY = SINCE + timedelta(hours=1)  # first seen long before any evaluation a test makes


@pytest.fixture
def before(tmp_path):
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < MIGRATION:
            shutil.copy(path, tmp_path)
    with empty_database() as c:
        migrate(c, tmp_path)
        yield c, tmp_path


def _check_kinds(c, constraint: str) -> set[str]:
    definition = c.execute(
        'SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = %s', (constraint,)
    ).fetchone()['d']
    return set(re.findall(r"'(\w+)'::text", definition))


def test_the_migration_keeps_every_existing_automation_and_run_and_admits_the_signal_trigger(before):
    c, directory = before
    uid = user(c)
    org = member(c, uid).org_id
    list_id, (row_id,) = make_list(c, uid, 'person', [{'full_name': 'Ada'}])
    definition = Jsonb({'list_id': list_id, 'steps': [{'type': 'find_emails'}]})
    for kind, cadence, target in (('manual', None, None), ('schedule', 'daily', None), ('webhook', None, row_id)):
        automation = c.execute(
            'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, cadence, credit_cap,'
            " definition) VALUES (%s, %s, %s, 'A', %s, %s, 5, %s) RETURNING id",
            (org, project(c, uid), uid, kind, cadence, definition),
        ).fetchone()['id']
        c.execute(
            'INSERT INTO automation_runs (organization_id, automation_id, version, definition, credit_cap, trigger_kind,'
            ' target_row_id, scheduled_for) VALUES (%s, %s, 1, %s, 5, %s, %s, now())',
            (org, automation, definition, kind, target),
        )

    shutil.copy(MIGRATIONS / MIGRATION, directory)
    assert migrate(c, directory) == [MIGRATION]

    for constraint in ('automations_trigger_kind_check', 'automation_runs_trigger_kind_check'):
        assert _check_kinds(c, constraint) == set(get_args(TriggerKind))
    kept = c.execute(
        'SELECT count(*) FILTER (WHERE watchlist_id IS NULL AND signal_min_score IS NULL AND signals_since IS NULL)'
        ' AS plain, count(*) AS n FROM automations'
    ).fetchone()
    assert (kept['plain'], kept['n']) == (3, 3)
    assert c.execute('SELECT count(*) AS n FROM automation_runs WHERE target_account_id IS NULL').fetchone()['n'] == 3
    with pytest.raises(Exception, match='automations_signal_trigger'):
        c.execute("UPDATE automations SET trigger_kind = 'signal' WHERE trigger_kind = 'manual'")
    with pytest.raises(Exception, match='automation_runs_signal_has_account'):
        c.execute("UPDATE automation_runs SET trigger_kind = 'signal' WHERE trigger_kind = 'manual'")


def _code(response) -> str:
    return response.json()['detail']['code']


def _watchlist(db, uid, list_id, *, enabled=True) -> str:
    return str(
        db.execute(
            'INSERT INTO watchlists (organization_id, user_id, list_id, name, job_titles, cadence, next_run_at, enabled)'
            " VALUES (default_organization(%s), %s, %s, 'Hiring SDRs', ARRAY['SDR'], 'daily', now(), %s) RETURNING id",
            (uid, uid, list_id, enabled),
        ).fetchone()['id']
    )


class Watched:
    """A user, a company list with a watchlist on it, a person list and an active campaign, and an enabled signal
    automation over them whose version needs no review. Its ``signals_since`` is ``SINCE``."""

    def __init__(self, db, monkeypatch, *, accounts=None, people=(), steps=None, min_score=120, credits=200):
        self.db = db
        self.uid = user(db, credits=credits)
        self.org = member(db, self.uid).org_id
        accounts = accounts or [{'name': 'Acme', 'domain': 'acme.it'}]
        self.accounts, self.account_rows = make_list(db, self.uid, 'company', accounts)
        self.people, self.people_rows = make_list(db, self.uid, 'person', people)
        self.watchlist = _watchlist(db, self.uid, self.accounts)
        self.campaign = make_campaign(db, self.uid, status='active')
        self.api = client(db, monkeypatch, self.uid)
        self.steps = steps or [{'type': 'add_to_campaign', 'campaign_id': self.campaign}]
        made = self.api.post('/v1/automations', json=self.body(min_score=min_score))
        assert made.status_code == 201, made.text
        self.id = made.json()['id']
        db.execute(
            'UPDATE automations SET approved_version = version, signals_since = %s WHERE id = %s', (SINCE, self.id)
        )

    def trigger(self, **change) -> dict:
        return {'kind': 'signal', 'watchlist_id': self.watchlist, 'min_score': 120} | change

    def body(self, **trigger) -> dict:
        return {
            'name': 'On signals',
            'credit_cap': 10,
            'trigger': self.trigger(**trigger),
            'definition': {'list_id': self.people, 'steps': self.steps},
        }

    def patch(self, **body):
        return self.api.patch(f'/v1/automations/{self.id}', json=body)

    def row(self) -> dict:
        return self.db.execute('SELECT * FROM automations WHERE id = %s', (self.id,)).fetchone()

    def shown(self) -> dict:
        return next(a for a in self.api.get('/v1/automations').json()['automations'] if a['id'] == self.id)

    def signal(self, account: int, score: float, *, first_seen=None, days_ago=0, title='SDR') -> str:
        """A signal of the account at index ``account``, first seen now unless told otherwise."""
        return str(
            self.db.execute(
                'INSERT INTO signals (organization_id, watchlist_id, row_id, type, key, data, score, occurred_at,'
                " first_seen_at) VALUES (%s, %s, %s, 'job_posting', %s, %s, %s, now() - %s * interval '1 day',"
                ' coalesce(%s, now())) RETURNING id',
                (
                    self.org,
                    self.watchlist,
                    self.account_rows[account],
                    str(uuid.uuid4()),
                    Jsonb({'title': title}),
                    score,
                    days_ago,
                    first_seen,
                ),
            ).fetchone()['id']
        )

    def pickup(self) -> int:
        return call_system(SystemReason.DUE_WORK, signal_trigger.pickup)

    def runs(self) -> list[dict]:
        return self.db.execute(
            'SELECT * FROM automation_runs WHERE automation_id = %s ORDER BY created_at', (self.id,)
        ).fetchall()

    def used(self) -> dict[str, str]:
        """Each signal the automation used, and the run it started."""
        rows = self.db.execute(
            'SELECT signal_id::text, run_id::text FROM automation_signals WHERE automation_id = %s', (self.id,)
        )
        return {r['signal_id']: r['run_id'] for r in rows}

    async def work(self, run_id) -> dict:
        with acting_as(self.org, self.uid):
            await runner.run_automation(run_id)
        return self.db.execute('SELECT * FROM automation_runs WHERE id = %s', (run_id,)).fetchone()

    def held(self) -> int:
        """How many signals a run of the automation holds and has not used."""
        return self.db.execute(
            'SELECT count(*) AS n FROM automation_signals WHERE automation_id = %s AND used_at IS NULL', (self.id,)
        ).fetchone()['n']

    def finds_people(self) -> None:
        """The automation finds people at the run's account, then adds them to the campaign."""
        find = {'type': 'find_people', 'accounts_list_id': self.accounts, 'titles': ['Head of Sales']}
        steps = [find, {'type': 'add_to_campaign', 'campaign_id': self.campaign}]
        assert self.patch(definition={'list_id': self.people, 'steps': steps}).status_code == 200
        self.db.execute('UPDATE automations SET approved_version = version WHERE id = %s', (self.id,))

    def later(self, minutes: int) -> None:
        """The automation's clock moves ``minutes`` on: its last evaluation and its accounts' waits go back that long."""
        shift = timedelta(minutes=minutes)
        self.db.execute(
            'UPDATE automations SET signals_checked_at = signals_checked_at - %s WHERE id = %s', (shift, self.id)
        )
        self.db.execute(
            'UPDATE automation_signal_waits SET retry_at = retry_at - %s WHERE automation_id = %s', (shift, self.id)
        )

    def worked(self) -> set[str]:
        rows = self.db.execute('SELECT row_id::text FROM automation_rows WHERE automation_id = %s', (self.id,))
        return {r['row_id'] for r in rows}

    def targets(self) -> list[str]:
        rows = self.db.execute(
            'SELECT email FROM outreach_campaign_targets WHERE campaign_id = %s ORDER BY email', (self.campaign,)
        )
        return [r['email'] for r in rows]


def _person(name: str, **cells) -> dict:
    first = name.lower()
    return {'full_name': f'{name} Rossi', 'email': f'{first}@acme.it', 'email_status': 'verified', **cells}


def test_the_bodies_the_editor_and_older_callers_send_still_save(db, monkeypatch):
    uid = user(db)
    people, _ = make_list(db, uid, 'person')
    api = client(db, monkeypatch, uid)
    definition = {'list_id': people, 'steps': [{'type': 'find_emails'}]}
    sent = [
        {'kind': 'manual'},
        {'kind': 'schedule', 'cadence': 'weekly'},
        {'kind': 'webhook'},
        {},
        {'kind': 'manual', 'cadence': None},
        {'kind': 'webhook', 'cadence': None},
    ]
    saved = []
    for trigger in sent:
        made = api.post(
            '/v1/automations', json={'name': 'A', 'credit_cap': 5, 'trigger': trigger, 'definition': definition}
        )
        assert made.status_code == 201, (trigger, made.text)
        saved.append((made.json()['trigger_kind'], made.json()['cadence'], made.json()['signal']))
    assert saved == [
        ('manual', None, None),
        ('schedule', 'weekly', None),
        ('webhook', None, None),
        ('manual', None, None),
        ('manual', None, None),
        ('webhook', None, None),
    ]
    for wrong in ({'kind': 'manual', 'cadence': 'daily'}, {'kind': 'schedule'}, {'kind': 'signal', 'min_score': 50}):
        refused = api.post(
            '/v1/automations', json={'name': 'A', 'credit_cap': 5, 'trigger': wrong, 'definition': definition}
        )
        assert refused.status_code == 422, wrong


def test_a_signal_automation_names_its_watchlist_and_runs_on_signals_only(db, monkeypatch):
    watched = Watched(db, monkeypatch)
    assert watched.shown()['signal'] | {'since': None} == {
        'watchlist_id': watched.watchlist,
        'watchlist_name': 'Hiring SDRs',
        'min_score': 120.0,
        'since': None,
    }
    assert watched.shown()['trigger_kind'] == 'signal'
    ran = watched.api.post(f'/v1/automations/{watched.id}/run')
    assert (ran.status_code, _code(ran)) == (409, 'AUTOMATION.SIGNAL_TRIGGERED')

    db.execute('DELETE FROM watchlists WHERE id = %s', (watched.watchlist,))
    assert watched.shown()['signal'] | {'since': None} == {
        'watchlist_id': None,
        'watchlist_name': None,
        'min_score': 120.0,
        'since': None,
    }


def test_signals_count_from_the_trigger_its_watchlist_or_the_resume_on(db, monkeypatch):
    watched = Watched(db, monkeypatch)
    assert watched.patch(trigger=watched.trigger(min_score=80)).status_code == 200
    assert watched.patch(name='Renamed').status_code == 200
    assert (watched.row()['signals_since'], watched.row()['signal_min_score']) == (SINCE, 80)

    assert watched.patch(enabled=False).status_code == 200
    assert watched.row()['signals_since'] == SINCE
    assert watched.patch(enabled=True).status_code == 200
    resumed = watched.row()['signals_since']
    assert resumed > SINCE

    other = _watchlist(db, watched.uid, watched.accounts)
    assert watched.patch(trigger=watched.trigger(watchlist_id=other)).status_code == 200
    assert watched.row()['signals_since'] > resumed

    assert watched.patch(trigger={'kind': 'manual'}).status_code == 200
    assert {k: watched.row()[k] for k in ('watchlist_id', 'signal_min_score', 'signals_since')} == dict.fromkeys(
        ('watchlist_id', 'signal_min_score', 'signals_since')
    )
    assert watched.shown()['signal'] is None


def test_a_trigger_whose_watchlist_is_unusable_is_refused_and_previewed(db, monkeypatch):
    watched = Watched(db, monkeypatch)
    stranger = user(db)
    theirs = _watchlist(db, stranger, make_list(db, stranger, 'company')[0])
    two = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Two') RETURNING id",
        (watched.org, watched.uid),
    ).fetchone()['id']
    elsewhere = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'B', 'company')"
        ' RETURNING id',
        (watched.org, two, watched.uid),
    ).fetchone()['id']
    paused = _watchlist(db, watched.uid, watched.accounts, enabled=False)
    other_accounts, _ = make_list(db, watched.uid, 'company')
    find = {'type': 'find_people', 'accounts_list_id': other_accounts, 'titles': ['CEO']}

    def preview(trigger, steps=None):
        definition = {'list_id': watched.people, 'steps': steps or watched.steps}
        got = watched.api.post(
            '/v1/automations/preview', json={'definition': definition, 'credit_cap': 5, 'trigger': trigger}
        )
        assert got.status_code == 200, got.text
        return [p['code'] for p in got.json()['problems']]

    not_found = {
        'missing': str(uuid.uuid4()),
        'other org': theirs,
        'other project': _watchlist(db, watched.uid, elsewhere),
    }
    for why, watchlist in not_found.items():
        trigger = watched.trigger(watchlist_id=watchlist)
        assert _code(watched.api.post('/v1/automations', json=watched.body(watchlist_id=watchlist))) == (
            'AUTOMATION.WATCHLIST_NOT_FOUND'
        ), why
        assert _code(watched.patch(trigger=trigger)) == 'AUTOMATION.WATCHLIST_NOT_FOUND', why
        assert preview(trigger) == ['AUTOMATION.WATCHLIST_NOT_FOUND'], why
    assert _code(watched.patch(trigger=watched.trigger(watchlist_id=None))) == 'AUTOMATION.WATCHLIST_NOT_FOUND'

    mismatch = watched.patch(definition={'list_id': watched.people, 'steps': [find]})
    assert _code(mismatch) == 'AUTOMATION.SIGNAL_ACCOUNTS_LIST'
    assert preview(watched.trigger(), [find]) == ['AUTOMATION.SIGNAL_ACCOUNTS_LIST']
    assert _code(watched.patch(enabled=False, definition={'list_id': watched.people, 'steps': [find]})) == (
        'AUTOMATION.SIGNAL_ACCOUNTS_LIST'
    )

    # Paused is refused only where the automation would run: a paused automation may name it.
    assert _code(watched.patch(trigger=watched.trigger(watchlist_id=paused))) == 'AUTOMATION.WATCHLIST_PAUSED'
    assert preview(watched.trigger(watchlist_id=paused)) == ['AUTOMATION.WATCHLIST_PAUSED']
    assert watched.patch(enabled=False, trigger=watched.trigger(watchlist_id=paused)).status_code == 200
    assert _code(watched.patch(enabled=True)) == 'AUTOMATION.WATCHLIST_PAUSED'

    # A deleted watchlist leaves the automation, which says so, and a paused one may still be renamed.
    db.execute('DELETE FROM watchlists WHERE id = %s', (paused,))
    assert watched.row()['watchlist_id'] is None
    assert watched.patch(name='Still here').status_code == 200
    assert _code(watched.patch(enabled=True)) == 'AUTOMATION.WATCHLIST_NOT_FOUND'
    saved = watched.api.post(
        '/v1/automations/preview',
        json={
            'definition': {'list_id': watched.people, 'steps': watched.steps},
            'credit_cap': 5,
            'automation_id': watched.id,
        },
    )
    assert [p['code'] for p in saved.json()['problems']] == ['AUTOMATION.WATCHLIST_NOT_FOUND']


async def test_an_account_whose_unused_signals_reach_the_minimum_starts_one_run_that_uses_them(db, monkeypatch):
    accounts = [{'name': 'Acme', 'domain': 'acme.it'}, {'name': 'Beta', 'domain': 'beta.it'}]
    watched = Watched(db, monkeypatch, accounts=accounts, people=[_person('Ada', company_domain='acme.it')])
    first = watched.signal(0, 100)
    assert watched.pickup() == 0
    second = watched.signal(0, 50)
    watched.signal(1, 200, days_ago=60)  # 50 now: with 60 more, 110 stays under 120
    watched.signal(1, 60)

    assert watched.pickup() == 1
    (run,) = watched.runs()
    assert (run['trigger_kind'], str(run['target_account_id'])) == ('signal', watched.account_rows[0])
    assert watched.used() == {first: str(run['id']), second: str(run['id'])}
    shown = watched.api.get(f'/v1/automations/{watched.id}/runs/{run["id"]}').json()['signal']
    assert shown == {
        'account_id': watched.account_rows[0],
        'account_name': 'Acme',
        'signals': [
            {'type': 'job_posting', 'title': 'SDR', 'score': 100.0, 'occurred_at': shown['signals'][0]['occurred_at']},
            {'type': 'job_posting', 'title': 'SDR', 'score': 50.0, 'occurred_at': shown['signals'][1]['occurred_at']},
        ],
    }

    done = await watched.work(run['id'])
    assert (done['status'], done['summary']['taken']['people']) == ('succeeded', 1)
    assert watched.targets() == ['ada@acme.it']
    watched.signal(0, 60)  # alone under the minimum: the used signals never count again
    assert watched.pickup() == 0
    assert len(watched.runs()) == 1


async def test_a_signal_starts_one_run_however_many_beats_see_it(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    used = watched.signal(0, 150)
    assert watched.pickup() == 1
    assert watched.pickup() == 0  # its run is live
    await watched.work(watched.runs()[0]['id'])
    assert watched.pickup() == 0  # its signal is used
    with acting_for(watched.org, 'test'):
        assert signal_trigger.start_next(watched.id) is False
    assert (len(watched.runs()), list(watched.used())) == (1, [used])


def test_two_beats_at_once_start_one_run(monkeypatch):
    """Two connections: the second beat finds the automation locked and starts nothing, then nothing is left."""
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        uid = user(c)
        org = member(c, uid).org_id
        accounts, (account,) = make_list(c, uid, 'company', [{'name': 'Acme', 'domain': 'acme.it'}])
        people, _ = make_list(c, uid, 'person')
        watchlist = _watchlist(c, uid, accounts)
        automation = c.execute(
            'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, watchlist_id,'
            " signal_min_score, signals_since, credit_cap, definition) VALUES (%s, %s, %s, 'A', 'signal', %s, 100, %s,"
            ' 5, %s) RETURNING id::text',
            (
                org,
                project(c, uid),
                uid,
                watchlist,
                SINCE,
                Jsonb({'list_id': people, 'steps': [{'type': 'find_emails'}]}),
            ),
        ).fetchone()['id']
        c.execute(
            'INSERT INTO signals (organization_id, watchlist_id, row_id, type, key, score, occurred_at, first_seen_at)'
            " SELECT %s, %s, %s, 'job_posting', 'k' || i, 60, now(), %s FROM generate_series(1, 2) i",
            (org, watchlist, account, SINCE + timedelta(hours=1)),
        )
        with c.transaction():
            c.execute('SELECT 1 FROM automations WHERE id = %s FOR UPDATE', (automation,))
            with acting_for(org, 'test'):
                assert signal_trigger.start_next(automation) is False
        with acting_for(org, 'test'):
            assert signal_trigger.start_next(automation) is True
            assert signal_trigger.start_next(automation) is False
        counts = c.execute(
            'SELECT (SELECT count(*) FROM automation_runs) AS runs, (SELECT count(*) FROM automation_signals) AS used'
        ).fetchone()
        assert (counts['runs'], counts['used']) == (1, 2)


def _ready_automation(c) -> tuple[str, str, str]:
    """``(org, watchlist, automation)``: a signal automation whose one account has a signal over its minimum."""
    uid = user(c)
    org = member(c, uid).org_id
    accounts, (account,) = make_list(c, uid, 'company', [{'name': 'Acme', 'domain': 'acme.it'}])
    people, _ = make_list(c, uid, 'person')
    watchlist = _watchlist(c, uid, accounts)
    automation = c.execute(
        'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, watchlist_id,'
        " signal_min_score, signals_since, credit_cap, definition) VALUES (%s, %s, %s, 'A', 'signal', %s, 100, %s,"
        ' 5, %s) RETURNING id::text',
        (org, project(c, uid), uid, watchlist, SINCE, Jsonb({'list_id': people, 'steps': [{'type': 'find_emails'}]})),
    ).fetchone()['id']
    c.execute(
        'INSERT INTO signals (organization_id, watchlist_id, row_id, type, key, score, occurred_at, first_seen_at)'
        " VALUES (%s, %s, %s, 'job_posting', 'k', 150, now(), %s)",
        (org, watchlist, account, EARLY),
    )
    return org, watchlist, automation


def _delete_waits(c, deleting: threading.Thread) -> None:
    """Until the watchlist delete waits on a lock the other transaction holds."""
    waiting = "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE 'DELETE FROM watchlists%'"
    for _ in range(100):
        if c.execute(waiting).fetchone():
            return
        deleting.join(0.05)


def _delete(conninfo: str, watchlist: str, failed: list[Exception]) -> threading.Thread:
    def delete():
        try:
            with psycopg.connect(conninfo) as other:
                other.execute('DELETE FROM watchlists WHERE id = %s', (watchlist,))
        except Exception as exc:
            failed.append(exc)

    return threading.Thread(target=delete)


def test_a_pickup_and_a_watchlist_delete_at_once_never_deadlock(monkeypatch):
    """A delete of the watchlist locks it, then its signals, then the automation (ON DELETE SET NULL). A pickup that
    has locked the automation and then uses the signals must not wait on the delete while the delete waits on it."""
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        org, watchlist, automation = _ready_automation(c)
        queued, go = threading.Event(), threading.Event()
        insert_run = signal_trigger.insert_run

        def insert_then_wait(*args, **kwargs):
            run = insert_run(*args, **kwargs)
            queued.set()
            go.wait(10)
            return run

        monkeypatch.setattr(signal_trigger, 'insert_run', insert_then_wait)
        failed: list[Exception] = []

        def pickup():
            try:
                with acting_for(org, 'test'):
                    signal_trigger.start_next(automation)
            except Exception as exc:
                failed.append(exc)

        beat, user_delete = threading.Thread(target=pickup), _delete(conninfo, watchlist, failed)
        beat.start()
        assert queued.wait(10)
        user_delete.start()
        _delete_waits(c, user_delete)
        go.set()
        beat.join(10)
        user_delete.join(10)
        assert failed == []
        left = c.execute('SELECT watchlist_id FROM automations').fetchone()
        assert (left['watchlist_id'], c.execute('SELECT count(*) AS n FROM automation_runs').fetchone()['n']) == (
            None,
            1,
        )


@pytest.mark.parametrize('end', ['failed before the take', 'took its people'])
def test_a_run_that_ends_or_takes_while_its_watchlist_is_deleted_never_deadlocks(end):
    """A run's end or take holds the automation, then writes the run's signals. The watchlist delete has already
    deleted those signals in its cascade and waits on the automation (ON DELETE SET NULL)."""
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        org, watchlist, automation = _ready_automation(c)
        with acting_for(org, 'test'):
            assert signal_trigger.start_next(automation)
        run_id = c.execute('SELECT id::text FROM automation_runs').fetchone()['id']
        locked, go = threading.Event(), threading.Event()
        failed: list[Exception] = []

        def run_ends():
            try:
                with acting_for(org, 'test'), get_conn() as conn:
                    _, run = runner._locked(conn, run_id)
                    locked.set()
                    go.wait(10)
                    if end == 'took its people':
                        signal_trigger.took(conn, run_id)
                    else:
                        runner._stop(conn, run, 'failed', 'vendor_failed')
            except Exception as exc:
                failed.append(exc)

        worker, user_delete = threading.Thread(target=run_ends), _delete(conninfo, watchlist, failed)
        worker.start()
        assert locked.wait(10)
        user_delete.start()
        _delete_waits(c, user_delete)
        go.set()
        worker.join(10)
        user_delete.join(10)
        assert failed == []
        assert c.execute('SELECT count(*) AS n FROM automation_signals').fetchone()['n'] == 0


def test_only_signals_first_seen_since_the_trigger_or_the_resume_count(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    watched.signal(0, 500, first_seen=SINCE - timedelta(minutes=1))
    assert watched.pickup() == 0

    assert watched.patch(enabled=False).status_code == 200
    assert watched.patch(enabled=True).status_code == 200
    resumed = watched.row()['signals_since']
    watched.signal(0, 500, first_seen=resumed - timedelta(seconds=1))  # came while it was paused
    assert watched.pickup() == 0
    assert watched.patch(trigger=watched.trigger(min_score=50)).status_code == 200
    assert watched.pickup() == 0  # a new minimum counts from the same time
    watched.signal(0, 60, first_seen=resumed + timedelta(seconds=1))
    assert watched.pickup() == 1


def test_an_evaluation_that_finds_nothing_waits_for_a_new_signal(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (watched.account_rows[0],))
    watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 0
    db.execute('UPDATE list_rows SET excluded = false WHERE id = %s', (watched.account_rows[0],))
    assert watched.pickup() == 0  # ready now, but no signal came since the evaluation that found nothing
    used = watched.signal(0, 10)
    assert watched.pickup() == 1
    assert used in watched.used()
    assert len(watched.used()) == 2


def test_a_lower_minimum_starts_a_run_with_no_new_signal(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    watched.signal(0, 100, first_seen=EARLY)
    assert watched.pickup() == 0
    assert watched.patch(trigger=watched.trigger(min_score=80)).status_code == 200
    assert watched.pickup() == 1


def test_a_signal_whose_watchlist_committed_after_an_empty_evaluation_counts(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    watched.signal(0, 100, first_seen=EARLY)
    assert watched.pickup() == 0
    # Stamped with its transaction's start, five minutes before the evaluation that could not see it yet.
    watched.signal(0, 50, first_seen=datetime.now(UTC) - timedelta(minutes=5))
    assert watched.pickup() == 1


async def test_a_run_that_ends_while_another_account_is_ready_starts_the_next_with_no_new_signal(db, monkeypatch):
    accounts = [{'name': 'Acme', 'domain': 'acme.it'}, {'name': 'Beta', 'domain': 'beta.it'}]
    people = [_person('Ada', company_domain='acme.it'), _person('Eve', company_domain='beta.it', email='eve@beta.it')]
    watched = Watched(db, monkeypatch, accounts=accounts, people=people)
    watched.signal(0, 10, first_seen=EARLY)
    assert watched.pickup() == 0
    # An hour later in the automation's time: the two signals come well after that evaluation, and well before now.
    db.execute("UPDATE automations SET signals_checked_at = now() - interval '1 hour' WHERE id = %s", (watched.id,))
    an_hour_on = datetime.now(UTC) - timedelta(minutes=59)
    watched.signal(0, 150, first_seen=an_hour_on)
    watched.signal(1, 150, first_seen=an_hour_on)
    assert watched.pickup() == 1
    await watched.work(watched.runs()[-1]['id'])
    assert watched.pickup() == 1
    await watched.work(watched.runs()[-1]['id'])
    assert watched.targets() == ['ada@acme.it', 'eve@beta.it']


def test_automations_that_cannot_start_never_hold_the_batch(db, monkeypatch):
    monkeypatch.setattr(signal_trigger, 'AUTOMATIONS_PER_TICK', 1)
    stuck = [Watched(db, monkeypatch) for _ in range(3)]
    for each in stuck:
        each.signal(0, 150)
        db.execute("UPDATE outreach_campaigns SET status = 'stopped' WHERE id = %s", (each.campaign,))
    ready = Watched(db, monkeypatch)
    ready.signal(0, 150)
    db.execute('UPDATE automations SET signals_since = %s WHERE id = %s', (SINCE + timedelta(minutes=1), ready.id))
    assert [ready.pickup() for _ in range(4)].count(1) == 1
    assert len(ready.runs()) == 1


async def test_a_run_takes_only_the_known_people_at_its_account_and_says_why_when_nobody(db, monkeypatch):
    accounts = [{'name': 'Acme', 'domain': 'acme.it'}, {'name': 'Beta', 'domain': 'beta.it'}]
    people = [
        _person('Ada', company_domain='www.acme.it'),
        _person('Bob', company_name='ACME'),  # by name: no domain on the row
        _person('Eve', company_domain='beta.it'),
        # Their cells contain the account's host or name, but they work elsewhere.
        _person('Nat', company_domain='notacme.it'),
        _person('Kai', company_name='Acme Holding'),
    ]
    watched = Watched(db, monkeypatch, accounts=accounts, people=people)
    watched.signal(0, 150)
    assert watched.pickup() == 1
    done = await watched.work(watched.runs()[-1]['id'])
    assert (done['status'], done['summary']['taken']['people']) == ('succeeded', 2)
    assert watched.worked() == set(watched.people_rows[:2])
    assert watched.targets() == ['ada@acme.it', 'bob@acme.it']

    watched.signal(0, 150)  # Acme again: its people were all taken
    assert watched.pickup() == 1
    again = await watched.work(watched.runs()[-1]['id'])
    assert again['summary']['taken'] == {'people': 0, 'refused_before_any_credit': {}, 'nobody': 'no_new_person'}

    watched.signal(1, 150)
    assert watched.pickup() == 1
    db.execute('DELETE FROM list_rows WHERE id = %s', (watched.account_rows[1],))
    gone = await watched.work(watched.runs()[-1]['id'])
    assert gone['summary']['taken']['nobody'] == 'account_gone'
    assert watched.worked() == set(watched.people_rows[:2])  # never the best of the list instead


# Cells as lists hold them: hosts with a scheme or "www.", case, spaces Python strips and Postgres does not, letters
# whose lower case differs between Python and Postgres (İ, a final Σ, the Kelvin sign), and any other text.
_PIECES = ['acme', 'ACME', 'Acme', 'acme.it', 'www.Acme.IT', 'https://acme.it/jobs', 'AI', 'ai', ' ', '\t', '\u00a0']
_PIECES += ['\u2003', 'İ', 'i\u0307', 'Σ', 'σ', 'ς', 'K', '\u212a', 'é', 'É', '.', '/']
_CELL = st.one_of(
    st.none(),
    st.lists(st.sampled_from(_PIECES), max_size=4).map(''.join),
    st.text(st.characters(exclude_categories=('Cs',), exclude_characters='\x00'), max_size=6),
)
_ACCOUNT = st.fixed_dictionaries({}, optional={'domain': _CELL, 'website': _CELL, 'name': _CELL})
_PEOPLE = st.lists(st.fixed_dictionaries({}, optional={'company_domain': _CELL, 'company_name': _CELL}), max_size=6)


def test_a_signal_run_reads_every_person_the_campaign_link_would_match_at_its_account(db):
    """The SQL that keeps a large list out of Python drops nobody: the run takes the people that ``PeopleIndex``, the
    campaign link's match, finds among every person of the list."""
    uid = user(db)
    accounts, _ = make_list(db, uid, 'company')
    people, _ = make_list(db, uid, 'person')
    org = member(db, uid).org_id
    definition = Definition.model_validate({'list_id': people, 'steps': [{'type': 'find_emails'}]})

    def insert(list_id, cells: dict) -> str:
        return str(
            db.execute(
                'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id',
                (list_id, Jsonb(cells), str(uuid.uuid4())),
            ).fetchone()['id']
        )

    @settings(max_examples=300, deadline=None)
    @given(account=_ACCOUNT, persons=_PEOPLE)
    @example(account={'name': 'οδος'}, persons=[{'company_name': 'ΟΔΟΣ'}])
    @example(account={'name': 'i\u0307nci'}, persons=[{'company_name': 'İnci'}])
    @example(account={'name': 'Acme'}, persons=[{'company_name': n} for n in (' ACME\t', '\u00a0ACME', 'Acme Inc')])
    @example(account={'domain': 'i\u0307nci.it'}, persons=[{'company_domain': 'www.İNCI.IT'}])
    def reads_every_match(account, persons):
        db.execute('DELETE FROM list_rows WHERE list_id IN (%s, %s)', (accounts, people))
        row = insert(accounts, account)
        everyone = [{'id': insert(people, cells), 'cells': cells} for cells in persons]
        index = PeopleIndex()
        for person in everyone:
            cells = person['cells']
            index.add(person, domain=cells.get('company_domain'), company_name=cells.get('company_name'))
        expected = [p['id'] for p in everyone if any(p is m for m in index.at(account))]
        run = Run(str(uuid.uuid4()), org, uid, project(db, uid), str(uuid.uuid4()), definition, 1, False, 0, None, 0)
        run.target_account_id = row
        read, _ = gate._untaken(db, run)
        assert [str(p['id']) for p in read] == expected

    reads_every_match()


def test_a_short_company_name_reads_only_the_people_of_that_name(db):
    uid = user(db)
    names = ['AI', ' ai ', 'Airbnb', 'Maine AI', 'OpenAI']
    people, rows = make_list(db, uid, 'person', [{'company_name': n} for n in names])
    params = {'list': people, 'automation': str(uuid.uuid4()), 'host': None, 'name': 'ai', 'space': gate._ASCII_SPACE}
    read = db.execute(gate._NEAR_ACCOUNT, params).fetchall()
    assert {str(r['id']) for r in read} == set(rows[:2])


async def test_a_run_finds_people_at_its_account_only_and_takes_the_people_it_found(db, monkeypatch):
    searched: list[str] = []

    async def search(args, ctx):
        searched.append(args['company_domains'][0])
        # As Apollo's search answers: no company domain, and a company name that is not the account's.
        found = [
            {'full_name': f'{n} Neri', 'title': 'Head of Sales', 'company_name': 'Acme S.p.A.', 'company_domain': None,
             'linkedin_url': f'https://www.linkedin.com/in/{n.lower()}'}
            for n in ('Cleo', 'Dan')
        ]  # fmt: skip
        return ToolResult(ok=True, rows=found, entity='person')

    monkeypatch.setattr(search_people, 'handle', search)
    accounts = [{'name': 'Acme', 'domain': 'acme.it'}, {'name': 'Beta', 'domain': 'beta.it'}]
    watched = Watched(db, monkeypatch, accounts=accounts)
    find = {'type': 'find_people', 'accounts_list_id': watched.accounts, 'titles': ['Head of Sales'], 'per_account': 5}
    steps = [find, {'type': 'add_to_campaign', 'campaign_id': watched.campaign}]
    assert watched.patch(definition={'list_id': watched.people, 'steps': steps}).status_code == 200
    db.execute('UPDATE automations SET approved_version = version, accounts_seq = 1 WHERE id = %s', (watched.id,))
    watched.signal(1, 150)

    assert watched.pickup() == 1
    done = await watched.work(watched.runs()[-1]['id'])
    assert done['status'] == 'succeeded', done
    assert searched == ['beta.it']
    added = db.execute('SELECT id::text FROM list_rows WHERE list_id = %s', (watched.people,)).fetchall()
    assert len(added) == 2
    assert watched.worked() == {r['id'] for r in added}
    assert (
        db.execute('SELECT accounts_seq FROM automations WHERE id = %s', (watched.id,)).fetchone()['accounts_seq'] == 1
    )


async def test_a_run_that_only_finds_people_uses_its_signals_when_it_succeeds(db, monkeypatch):
    async def search(args, ctx):
        found = [{'full_name': 'Cleo Neri', 'title': 'Head of Sales', 'company_domain': 'beta.it',
                  'linkedin_url': 'https://www.linkedin.com/in/cleo'}]  # fmt: skip
        return ToolResult(ok=True, rows=found, entity='person')

    monkeypatch.setattr(search_people, 'handle', search)
    watched = Watched(db, monkeypatch, accounts=[{'name': 'Acme', 'domain': 'acme.it'}])
    find = {'type': 'find_people', 'accounts_list_id': watched.accounts, 'titles': ['Head of Sales'], 'per_account': 5}
    assert watched.patch(definition={'list_id': watched.people, 'steps': [find]}).status_code == 200
    db.execute('UPDATE automations SET approved_version = version WHERE id = %s', (watched.id,))
    watched.signal(0, 150)
    assert watched.pickup() == 1
    run = await watched.work(watched.runs()[-1]['id'])
    assert (run['status'], run['summary'].get('signals')) == ('succeeded', None), run

    held = db.execute(
        'SELECT count(*) AS n FROM automation_signals WHERE run_id = %s AND used_at IS NULL', (run['id'],)
    ).fetchone()['n']
    assert held == 0


async def test_a_misconfigured_automation_starts_nothing_and_keeps_its_signals(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    watched.signal(0, 150, first_seen=EARLY)
    db.execute("UPDATE outreach_campaigns SET status = 'stopped' WHERE id = %s", (watched.campaign,))
    assert watched.pickup() == 0
    db.execute('UPDATE watchlists SET enabled = false WHERE id = %s', (watched.watchlist,))
    db.execute("UPDATE outreach_campaigns SET status = 'active' WHERE id = %s", (watched.campaign,))
    assert watched.pickup() == 0
    assert watched.used() == {}
    db.execute('UPDATE watchlists SET enabled = true WHERE id = %s', (watched.watchlist,))
    assert watched.pickup() == 1


async def test_a_queued_run_goes_when_the_trigger_leaves_its_watchlist_and_stays_for_a_new_minimum(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    watched.signal(0, 150)
    assert watched.pickup() == 1
    assert watched.patch(trigger=watched.trigger(min_score=100)).status_code == 200
    assert watched.runs()[-1]['status'] == 'queued'
    other = _watchlist(db, watched.uid, watched.accounts)
    assert watched.patch(trigger=watched.trigger(watchlist_id=other)).status_code == 200
    assert (watched.runs()[-1]['status'], watched.runs()[-1]['error_code']) == ('cancelled', 'automation_edited')


def _vendor_down(monkeypatch, module, name: str) -> None:
    """``module.name`` answers that its vendor is down, and the runner tries it again with no wait."""

    async def down(args, ctx):
        return ToolResult(ok=False, error='vendor unavailable')

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(module, name, down)
    monkeypatch.setattr(runner.asyncio, 'sleep', no_wait)


def test_an_empty_wallet_or_a_reached_cap_starts_no_run_and_keeps_the_signals(db, monkeypatch):
    lookup = [{'type': 'find_emails'}]
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')], credits=0, steps=lookup)
    watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 0
    assert (watched.runs(), watched.used()) == ([], {})

    db.execute('UPDATE user_credits SET credits_remaining = 10 WHERE user_id = %s', (watched.uid,))
    capped = watched.row()['project_id']
    db.execute("UPDATE projects SET credit_cap = 1, credit_cap_mode = 'hard' WHERE id = %s", (capped,))
    db.execute(
        "SELECT reserve_project_credits(%s, %s, %s, 1, 'test', NULL, NULL, NULL, 'the month so far')",
        (capped, watched.org, watched.uid),
    )
    assert watched.pickup() == 0
    assert watched.runs() == []

    db.execute('UPDATE projects SET credit_cap = NULL, credit_cap_mode = NULL WHERE id = %s', (capped,))
    assert watched.pickup() == 1  # no new signal: the ones that waited start it


async def test_a_queued_run_cancelled_by_an_edit_gives_its_signals_back(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    signal = watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    steps = [{'type': 'find_emails'}, *watched.steps]
    assert watched.patch(definition={'list_id': watched.people, 'steps': steps}).status_code == 200
    db.execute('UPDATE automations SET approved_version = version WHERE id = %s', (watched.id,))
    cancelled = watched.runs()[-1]
    assert (cancelled['status'], cancelled['summary'].get('signals'), watched.used()) == (
        'cancelled',
        {'given_back': True},
        {},
    )

    assert watched.pickup() == 1
    run = watched.runs()[-1]
    assert (str(run['target_account_id']), watched.used()) == (watched.account_rows[0], {signal: str(run['id'])})


async def test_a_run_that_took_its_people_keeps_its_signals_whatever_its_end(db, monkeypatch):
    from app.agent.tools.handlers import enrich_email

    _vendor_down(monkeypatch, enrich_email, 'handle')
    no_email = _person('Ada', company_domain='acme.it', email=None, email_status=None)
    watched = Watched(db, monkeypatch, people=[no_email], steps=[{'type': 'find_emails'}])
    signal = watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    run = await watched.work(watched.runs()[-1]['id'])
    assert (run['status'], run['error_code']) == ('failed', 'vendor_failed')

    assert (watched.used(), run['summary'].get('signals')) == ({signal: str(run['id'])}, None)
    assert watched.pickup() == 0
    assert len(watched.runs()) == 1


async def test_a_run_that_failed_before_it_took_anyone_gives_its_signals_back_and_its_account_waits(db, monkeypatch):
    _vendor_down(monkeypatch, search_people, 'handle')
    watched = Watched(db, monkeypatch)
    watched.finds_people()
    signal = watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    run = await watched.work(watched.runs()[-1]['id'])
    assert (run['status'], run['error_code']) == ('failed', 'vendor_failed')
    assert (watched.used(), run['summary']['signals']) == ({}, {'given_back': True})

    assert watched.pickup() == 0  # a vendor that is down now is down a minute later
    watched.later(14)
    assert watched.pickup() == 0
    watched.later(1)
    assert watched.pickup() == 1  # no new signal: the wait ended
    assert watched.used() == {signal: str(watched.runs()[-1]['id'])}


async def test_an_account_whose_runs_fail_never_holds_the_other_accounts(db, monkeypatch):
    async def acme_is_down(args, ctx):
        if args['company_domains'] == ['acme.it']:
            return ToolResult(ok=False, error='vendor unavailable')
        return ToolResult(ok=True, rows=[], entity='person')

    _vendor_down(monkeypatch, search_people, 'handle')
    monkeypatch.setattr(search_people, 'handle', acme_is_down)
    accounts = [{'name': 'Acme', 'domain': 'acme.it'}, {'name': 'Beta', 'domain': 'beta.it'}]
    watched = Watched(db, monkeypatch, accounts=accounts)
    watched.finds_people()
    watched.signal(0, 150, first_seen=EARLY)
    watched.signal(1, 150, first_seen=EARLY + timedelta(minutes=1))
    assert watched.pickup() == 1
    acme = await watched.work(watched.runs()[-1]['id'])
    assert (str(acme['target_account_id']), acme['status']) == (watched.account_rows[0], 'failed')

    assert watched.pickup() == 1
    beta = await watched.work(watched.runs()[-1]['id'])
    assert (str(beta['target_account_id']), beta['status']) == (watched.account_rows[1], 'succeeded')


async def test_after_three_failed_runs_an_account_is_given_up_until_a_new_signal(db, monkeypatch):
    _vendor_down(monkeypatch, search_people, 'handle')
    watched = Watched(db, monkeypatch)
    watched.finds_people()
    watched.signal(0, 150, first_seen=EARLY)
    failed = []
    for _ in range(3):
        watched.later(15)
        assert watched.pickup() == 1
        failed.append(await watched.work(watched.runs()[-1]['id']))
    assert [run['status'] for run in failed] == ['failed'] * 3
    assert [run['summary'].get('signals') for run in failed[:2]] == [{'given_back': True}] * 2
    assert failed[2]['summary']['signals'] == {'given_up': True, 'failed_runs': 3}
    assert watched.held() == 0

    watched.later(15)
    assert watched.pickup() == 0
    watched.signal(0, 150)
    assert watched.pickup() == 1  # the new signal alone, at once: the given-up account starts over


async def test_a_cancel_by_an_edit_counts_no_failed_run(db, monkeypatch):
    _vendor_down(monkeypatch, search_people, 'handle')
    watched = Watched(db, monkeypatch)
    watched.finds_people()
    watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    await watched.work(watched.runs()[-1]['id'])
    watched.later(15)
    assert watched.pickup() == 1
    assert watched.patch(credit_cap=11).status_code == 200  # a new version cancels the queued run
    db.execute('UPDATE automations SET approved_version = version WHERE id = %s', (watched.id,))
    assert watched.runs()[-1]['error_code'] == 'automation_edited'

    assert watched.pickup() == 1  # at once: the cancel started no wait
    second = await watched.work(watched.runs()[-1]['id'])
    watched.later(15)
    assert watched.pickup() == 1
    third = await watched.work(watched.runs()[-1]['id'])
    assert [run['summary'].get('signals') for run in (second, third)] == [
        {'given_back': True},
        {'given_up': True, 'failed_runs': 3},
    ]


async def test_a_resume_ends_the_wait_of_a_failed_account(db, monkeypatch):
    _vendor_down(monkeypatch, search_people, 'handle')
    watched = Watched(db, monkeypatch)
    watched.finds_people()
    watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    await watched.work(watched.runs()[-1]['id'])
    assert watched.patch(enabled=False).status_code == 200
    assert watched.patch(enabled=True).status_code == 200
    watched.signal(0, 150)
    assert watched.pickup() == 1


class _HostAhead(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) + timedelta(seconds=5)


def test_a_publish_counts_from_the_database_clock_when_the_host_clock_runs_ahead(db, monkeypatch):
    watched = Watched(db, monkeypatch)
    monkeypatch.setattr(service, 'datetime', _HostAhead)
    made = watched.api.post('/v1/automations', json=watched.body() | {'name': 'Again'})
    assert made.status_code == 201, made.text
    since = db.execute(
        'SELECT signals_since <= now() AS before_now FROM automations WHERE id = %s', (made.json()['id'],)
    ).fetchone()
    assert since['before_now']


def test_a_signal_right_after_a_resume_counts_when_the_host_clock_runs_ahead(db, monkeypatch):
    watched = Watched(db, monkeypatch)
    monkeypatch.setattr(service, 'datetime', _HostAhead)
    assert watched.patch(enabled=False).status_code == 200
    assert watched.patch(enabled=True).status_code == 200
    watched.signal(0, 150)
    assert watched.pickup() == 1


async def test_a_run_the_worker_lost_before_it_took_anyone_gives_its_signals_back(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    db.execute(
        "UPDATE automation_runs SET status = 'running', started_at = now() - interval '1 hour' WHERE automation_id = %s",
        (watched.id,),
    )
    call_system(SystemReason.DUE_WORK, runner.maintain)
    lost = watched.runs()[-1]
    assert (lost['status'], lost['summary'].get('signals'), watched.used()) == ('unknown', {'given_back': True}, {})


async def test_a_run_the_worker_lost_after_it_took_its_people_says_nothing_of_its_signals(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    signal = watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    run_id = watched.runs()[-1]['id']
    signal_trigger.took(db, run_id)
    db.execute(
        "UPDATE automation_runs SET status = 'running', started_at = now() - interval '1 hour' WHERE id = %s",
        (run_id,),
    )
    call_system(SystemReason.DUE_WORK, runner.maintain)
    lost = watched.runs()[-1]
    assert (lost['status'], lost['summary'].get('signals'), watched.used()) == ('unknown', None, {signal: str(run_id)})


async def test_a_run_the_user_cancelled_keeps_its_signals(db, monkeypatch):
    watched = Watched(db, monkeypatch, people=[_person('Ada', company_domain='acme.it')])
    signal = watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    run = watched.runs()[-1]
    cancelled = watched.api.post(f'/v1/automations/{watched.id}/runs/{run["id"]}/cancel')
    assert cancelled.status_code == 200, cancelled.text
    assert (watched.used(), watched.held()) == ({signal: str(run['id'])}, 0)
    assert watched.runs()[-1]['summary'].get('signals') is None
    assert watched.pickup() == 0


async def test_a_running_run_the_user_cancelled_uses_its_signals(db, monkeypatch):
    async def cancel_while_searching(args, ctx):
        db.execute('UPDATE automation_runs SET cancel_requested = true WHERE automation_id = %s', (watched.id,))
        return ToolResult(ok=True, rows=[], entity='person')

    monkeypatch.setattr(search_people, 'handle', cancel_while_searching)
    watched = Watched(db, monkeypatch)
    watched.finds_people()
    signal = watched.signal(0, 150, first_seen=EARLY)
    assert watched.pickup() == 1
    run = await watched.work(watched.runs()[-1]['id'])
    assert (run['status'], run['error_code'], run['summary'].get('signals')) == ('cancelled', 'cancelled', None)
    assert (watched.used(), watched.held()) == ({signal: str(run['id'])}, 0)
    assert watched.pickup() == 0


async def test_a_review_run_waiting_for_approval_holds_the_next_start(db, monkeypatch):
    accounts = [{'name': 'Acme', 'domain': 'acme.it'}, {'name': 'Beta', 'domain': 'beta.it'}]
    people = [_person('Ada', company_domain='acme.it'), _person('Eve', company_domain='beta.it', email='eve@beta.it')]
    watched = Watched(db, monkeypatch, accounts=accounts, people=people)
    db.execute('UPDATE automations SET approved_version = NULL WHERE id = %s', (watched.id,))
    watched.signal(0, 150)
    assert watched.pickup() == 1
    first = await watched.work(watched.runs()[-1]['id'])
    assert (first['review'], watched.targets()) == (True, ['ada@acme.it'])

    watched.signal(1, 150)
    assert watched.pickup() == 0
    approved = watched.api.post(f'/v1/automations/{watched.id}/runs/{first["id"]}/approve')
    assert approved.status_code == 200, approved.text
    assert watched.pickup() == 1


@pytest.mark.parametrize('campaign_link_first', [True, False])
async def test_the_campaign_link_and_an_automation_on_one_campaign_enroll_a_person_once(
    db, monkeypatch, campaign_link_first
):
    from app.agent.tools.handlers import enrich_email
    from app.lists import lookup_outcome

    async def finds_her_address(args, ctx):
        updates = [
            {'row_id': str(i), 'cells': {'email': 'ada@acme.it', 'email_status': 'verified'}} for i in args['row_ids']
        ]
        return ToolResult(
            ok=True, row_updates=updates, entity='person', outcome=lookup_outcome.searched('email', updates)
        )

    monkeypatch.setattr(enrich_email, 'handle', finds_her_address)
    # No address yet: the automation pays a credit to look one up, unless the campaign link already enrolled her.
    ada = _person(
        'Ada', company_domain='acme.it', email=None, email_status=None, linkedin_url='https://linkedin.com/in/ada'
    )
    watched = Watched(db, monkeypatch, people=[ada])
    steps = [{'type': 'find_emails'}, {'type': 'add_to_campaign', 'campaign_id': watched.campaign}]
    assert watched.patch(definition={'list_id': watched.people, 'steps': steps}).status_code == 200
    db.execute('UPDATE automations SET approved_version = version WHERE id = %s', (watched.id,))
    db.execute(
        'UPDATE watchlists SET campaign_id = %s, campaign_threshold = 120 WHERE id = %s',
        (watched.campaign, watched.watchlist),
    )
    monkeypatch.setattr(enroll, 'get_conn', service.get_conn)
    watched.signal(0, 150)
    balance = 'SELECT credits_remaining AS n FROM user_credits WHERE user_id = %s'
    before = db.execute(balance, (watched.uid,)).fetchone()['n']

    def link():
        run = db.execute(
            'INSERT INTO watchlist_runs (organization_id, watchlist_id, scheduled_for) VALUES (%s, %s, now())'
            ' RETURNING id',
            (watched.org, watched.watchlist),
        ).fetchone()['id']
        with acting_as(watched.org, watched.uid):
            enroll.enroll_signals(run)

    async def automation():
        assert watched.pickup() == 1
        assert (await watched.work(watched.runs()[-1]['id']))['status'] == 'succeeded'

    if campaign_link_first:
        link()
        await automation()
    else:
        await automation()
        link()
    enrolled = db.execute(
        'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id = %s', (watched.campaign,)
    )
    assert enrolled.fetchone()['n'] == 1
    spent = before - db.execute(balance, (watched.uid,)).fetchone()['n']
    assert spent == (0 if campaign_link_first else enrich_email.PRICE.credits)


def test_the_preview_shows_the_accounts_recent_signals_would_have_started(db, monkeypatch):
    accounts = [{'name': n, 'domain': f'{n.lower()}.it'} for n in ('Acme', 'Beta', 'Gamma')]
    watched = Watched(db, monkeypatch, accounts=accounts)
    recent = datetime.now(UTC) - timedelta(days=1)
    watched.signal(0, 100, first_seen=recent, title='SDR')
    watched.signal(0, 40, first_seen=recent, title='AE')
    watched.signal(1, 200, first_seen=datetime.now(UTC) - timedelta(days=40))  # older than the preview looks
    watched.signal(2, 100, first_seen=recent)  # under the minimum

    def preview(**extra):
        body = {'definition': {'list_id': watched.people, 'steps': watched.steps}, 'credit_cap': 10}
        got = watched.api.post('/v1/automations/preview', json=body | extra)
        assert got.status_code == 200, got.text
        return got.json()

    shown = preview(trigger=watched.trigger())
    assert shown['signals']['accounts'] == 1
    assert [(a['name'], a['title'], a['score']) for a in shown['signals']['recent']] == [('Acme', 'SDR', 140.0)]
    assert (shown['people_per_run'], shown['accounts_per_run'], shown['max_credits']) == (25, 1, 0)
    assert preview(automation_id=watched.id)['signals']['accounts'] == 1

    db.execute('UPDATE automations SET signals_since = %s WHERE id = %s', (recent - timedelta(hours=1), watched.id))
    assert watched.pickup() == 1
    assert preview(automation_id=watched.id)['signals']['accounts'] == 0  # its signals are used
    assert preview(trigger=watched.trigger())['signals']['accounts'] == 1  # a draft has used none
    assert preview(trigger={'kind': 'manual'})['signals'] is None
    db.execute('UPDATE watchlists SET enabled = false WHERE id = %s', (watched.watchlist,))
    paused = preview(trigger=watched.trigger())
    assert ([p['code'] for p in paused['problems']], paused['signals']) == (['AUTOMATION.WATCHLIST_PAUSED'], None)


def test_the_due_automations_and_the_ready_accounts_are_read_through_the_first_seen_index(db, monkeypatch):
    watched = Watched(db, monkeypatch)
    db.execute(
        'INSERT INTO signals (organization_id, watchlist_id, row_id, type, key, score, occurred_at, first_seen_at)'
        " SELECT %s, %s, %s, 'job_posting', 'k' || i, 10, now(), %s - i * interval '1 minute'"
        ' FROM generate_series(1, 20000) i',
        (watched.org, watched.watchlist, watched.account_rows[0], SINCE + timedelta(days=10)),
    )
    db.execute('ANALYZE signals')
    params = {'automation': watched.id, 'watchlist': watched.watchlist, 'since': SINCE + timedelta(days=9), 'min': 120}
    plan = db.execute(f'EXPLAIN {signal_trigger._NEXT_ACCOUNT}', params).fetchall()
    shown = '\n'.join(r['QUERY PLAN'] for r in plan)
    assert 'signals_watchlist_first_seen' in shown, shown
    db.execute('UPDATE automations SET signals_checked_at = %s WHERE id = %s', (SINCE + timedelta(days=11), watched.id))
    plan = db.execute(f'EXPLAIN {signal_trigger._DUE}', (signal_trigger.AUTOMATIONS_PER_TICK,)).fetchall()
    shown = '\n'.join(r['QUERY PLAN'] for r in plan)
    assert 'Seq Scan on signals' not in shown, shown
