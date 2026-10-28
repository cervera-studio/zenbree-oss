"""The automation step that hands a run's people to an Instantly campaign (#1032), against real SQL with Instantly and
the model faked: what the step holds, the one batch a held run becomes, when it waits, and the beat that repairs it."""

import os
import uuid
from datetime import UTC, datetime

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.agent.tools.handlers import enrich_email
from app.auth.middleware import CurrentUser
from app.automations import approval, beat, hubspot, instantly_handoff, instantly_step, qualify, runner, service
from app.automations.discard import WAITING_FOR_APPROVAL
from app.automations.models import AutomationIn, AutomationPatch, Definition
from app.core import scope as scopes
from app.core.config import settings
from app.core.scope import acting_as
from app.core.system_reasons import SystemReason
from app.integrations import instantly_publish, token_crypto
from app.worker import celery_app
from tests import conftest
from tests.automations_support import client, connect_hubspot, database, make_campaign, make_list, teammate, user
from tests.conftest import call_system, member, project
from tests.test_automations_run_sql import Vendors
from tests.test_instantly_sql import CAMPAIGN, KEY, FakeInstantly

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')
db = pytest.fixture(database)


class Broker:
    """Celery's ``send_task`` for the batch task: who queued what, and the batch as another connection sees it then."""

    def __init__(self, db):
        self.db = db
        self.down = False
        self.sent: list[dict] = []

    def send_task(self, name, args=None, *_, **__):
        if self.down:
            raise ConnectionError('broker down')
        scope = scopes.current()
        conninfo = make_conninfo(os.environ['TEST_DATABASE_URL'], dbname=self.db.info.dbname)
        with psycopg.connect(conninfo) as other:
            seen = other.execute('SELECT status FROM instantly_enrollments WHERE id = %s', (args[0],)).fetchone()
        self.sent.append({'task': name, 'batch': args[0], 'as': scope.user, 'seen': seen[0] if seen else None})


@pytest.fixture
def fake(http, monkeypatch):
    state = FakeInstantly()
    http.handler = state.handler
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(instantly_step, 'is_disabled', lambda key: False)
    monkeypatch.setattr(instantly_publish, 'vendor_disabled_now', lambda provider: False)
    monkeypatch.setattr(instantly_publish, 'PAUSE_SECONDS', 0)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(runner.asyncio, 'sleep', no_wait)
    return state


@pytest.fixture
def broker(db, monkeypatch):
    spy = Broker(db)
    monkeypatch.setattr(celery_app, 'send_task', spy.send_task)
    monkeypatch.setattr(instantly_publish, 'get_conn', instantly_handoff.get_conn)
    monkeypatch.setattr('app.notifications.service.get_conn', instantly_handoff.get_conn)
    return spy


@pytest.fixture
def emails(monkeypatch):
    found = Vendors()
    monkeypatch.setattr(enrich_email, 'handle', found.enrich)
    return found


def person(n: int, **cells) -> dict:
    return {
        'full_name': f'Pat Person{n}',
        'first_name': 'Pat',
        'last_name': f'Person{n}',
        'email': f'p{n}@acme{n}.test',
        'email_status': 'verified',
        'title': 'CEO',
        'company_name': 'Acme',
        **cells,
    }


class Handed:
    """A user, their person list, their project's Instantly connection, and an automation that ends in the step."""

    def __init__(self, db, people, *, steps=('add_to_instantly',), cap=20, connected=True, hubspot=False):
        self.db = db
        self.uid = user(db)
        self.org = member(db, self.uid).org_id
        self.project = project(db, self.uid)
        self.list_id, self.rows = make_list(db, self.uid, 'person', people)
        if connected:
            self.connect()
        if hubspot:
            connect_hubspot(db, self.uid)
        by_kind = {
            'qualify': {'type': 'qualify', 'brief': 'sales leaders', 'min_score': 50},
            'find_emails': {'type': 'find_emails'},
            'add_to_instantly': {'type': 'add_to_instantly', 'campaign_id': CAMPAIGN},
            'write_hubspot': {'type': 'write_hubspot'},
        }
        body = AutomationIn.model_validate(
            {
                'name': 'To Instantly',
                'credit_cap': cap,
                'definition': {'list_id': self.list_id, 'steps': [by_kind[k] for k in steps]},
            }
        )
        with self.scope():
            self.id = str(service.create(member(db, self.uid), self.project, body)['id'])

    def connect(self, workspace='ws-1'):
        self.db.execute(
            'INSERT INTO instantly_connections (organization_id, project_id, connected_by, name, api_key_encrypted,'
            " workspace_id, workspace_name, status, connected_at) VALUES (%s, %s, %s, 'Main', %s, %s, 'Acme', 'connected',"
            " now()) ON CONFLICT (project_id) DO UPDATE SET status = 'connected', api_key_encrypted ="
            ' EXCLUDED.api_key_encrypted, workspace_id = EXCLUDED.workspace_id',
            (self.org, self.project, self.uid, token_crypto.encrypt(KEY).value, workspace),
        )

    def add(self, cells: dict) -> str:
        """A person who joins the list later."""
        return str(
            self.db.execute(
                'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id',
                (self.list_id, Jsonb(cells), str(uuid.uuid4())),
            ).fetchone()['id']
        )

    def disconnect(self):
        self.db.execute(
            "UPDATE instantly_connections SET status = 'disconnected', api_key_encrypted = '' WHERE project_id = %s",
            (self.project,),
        )

    def scope(self):
        return acting_as(self.org, self.uid)

    async def run(self) -> dict:
        with self.scope(), self.db.transaction():
            automation = self.db.execute('SELECT * FROM automations WHERE id = %s', (self.id,)).fetchone()
            queued = service.enqueue(self.db, automation, datetime.now(UTC), 'manual')
        with self.scope():
            await runner.run_automation(queued['id'])
        return self.db.execute('SELECT * FROM automation_runs WHERE id = %s', (queued['id'],)).fetchone()

    async def approved_run(self) -> dict:
        """A first run, approved: the next runs are not in review."""
        first = await self.run()
        self.approve(first['id'])
        return first

    def approve(self, run_id, by=None):
        approver = by or member(self.db, self.uid)
        with acting_as(self.org, approver.id):
            return approval.approve(approver, self.project, self.id, run_id)

    def detail(self, run_id) -> dict:
        with self.scope():
            return service.run_detail(member(self.db, self.uid), self.project, self.id, run_id)

    def step(self, run_id) -> dict:
        return self.db.execute(
            "SELECT * FROM automation_run_steps WHERE run_id = %s AND kind = 'add_to_instantly'", (run_id,)
        ).fetchone()

    def hold(self, run_id) -> dict | None:
        return self.db.execute('SELECT * FROM automation_instantly WHERE run_id = %s', (run_id,)).fetchone()

    def batches(self) -> list[dict]:
        return self.db.execute(
            'SELECT * FROM instantly_enrollments WHERE project_id = %s ORDER BY created_at', (self.project,)
        ).fetchall()

    def leads(self, batch_id) -> dict[str, str]:
        rows = self.db.execute(
            'SELECT row_id::text, outcome, reason FROM instantly_leads WHERE enrollment_id = %s', (batch_id,)
        ).fetchall()
        return {r['row_id']: r['outcome'] + (f':{r["reason"]}' if r['reason'] else '') for r in rows}

    def taken(self) -> set[str]:
        rows = self.db.execute('SELECT row_id::text FROM automation_rows WHERE automation_id = %s', (self.id,))
        return {r['row_id'] for r in rows}

    async def publish(self, batch_id):
        with self.scope():
            await instantly_publish.run(str(batch_id))

    def waiting(self) -> bool:
        return self.db.execute(
            f'SELECT {WAITING_FOR_APPROVAL} AS w FROM automations a WHERE a.id = %s', (self.id,)
        ).fetchone()['w']


def beat_tick():
    return call_system(SystemReason.DUE_WORK, beat.dispatch, celery_app)


def busy_batch(world: Handed) -> str:
    """A batch of the project's own, queued from the enrollment route: the project has a live batch."""
    return str(
        world.db.execute(
            'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, created_by, workspace_id,'
            ' campaign_id) SELECT organization_id, project_id, id, connected_by, workspace_id, %s FROM'
            ' instantly_connections WHERE project_id = %s RETURNING id',
            (CAMPAIGN, world.project),
        ).fetchone()['id']
    )


# ── the step ────────────────────────────────────────────────────────────────


async def test_a_review_run_holds_the_people_who_would_go_and_counts_the_others(db, fake, broker):
    world = Handed(db, [person(1), person(2), person(3, email=None)])

    run = await world.run()

    assert (run['status'], run['review']) == ('succeeded', True)
    step = world.step(run['id'])
    assert step['output'] == {
        'held': 2,
        'eligible': 2,
        'skipped': {'no_email': 1},
        'campaign': {'id': CAMPAIGN, 'name': 'Q4 outreach', 'status': 0, 'may_send': False},
    }
    hold = world.hold(run['id'])
    assert sorted(map(str, hold['row_ids'])) == sorted(world.rows[:2])
    assert (hold['workspace_id'], hold['enrollment_id']) == ('ws-1', None)
    assert world.taken() == set(world.rows)
    assert world.batches() == [] and broker.sent == []
    assert world.detail(run['id'])['instantly'] == {
        'people': 2,
        'batch_id': None,
        'waiting_for': 'approval',
        'campaign': {'id': CAMPAIGN, 'name': 'Q4 outreach', 'status': 0, 'may_send': False},
    }


def found_on_an_unfinished_row(db, emails) -> Handed:
    """A person with no email on a list that requires a phone they lack: the run's email charge stays held until the
    run ends, and the list hides the email meanwhile."""
    world = Handed(db, [person(1, email=None, email_status=None)], steps=('find_emails', 'add_to_instantly'))
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'phone', 'Phone', 'code',"
        """ 0, '{"required": true}')""",
        (world.list_id,),
    )
    emails.emails['Pat Person1'] = 'p1@acme1.test'
    return world


async def test_a_person_whose_email_the_run_found_goes_to_instantly_and_pays_once(db, fake, broker, emails):
    world = found_on_an_unfinished_row(db, emails)

    run = await world.run()
    assert world.step(run['id'])['output']['held'] == 1
    world.approve(run['id'])
    await world.publish(world.batches()[0]['id'])

    assert [lead['email'] for lead in fake.add_calls[0]] == ['p1@acme1.test']
    charges = db.execute('SELECT state, credits FROM row_charges WHERE row_id = %s', (world.rows[0],)).fetchall()
    assert [c['state'] for c in charges] == ['charged']
    assert run['credits_spent'] == charges[0]['credits'] > 0


async def test_the_step_leaves_a_charge_of_another_hold_on_the_row_held(db, fake, broker, emails):
    world = found_on_an_unfinished_row(db, emails)
    other = conftest.hold(db, world.uid)
    db.execute(
        "INSERT INTO row_charges (hold_id, row_id, keys, credits, state) VALUES (%s, %s, '{title}', 1, 'held')",
        (other, world.rows[0]),
    )

    await world.run()

    assert db.execute('SELECT state FROM row_charges WHERE hold_id = %s', (other,)).fetchone()['state'] == 'held'


async def test_a_review_run_with_nobody_who_would_go_holds_nothing_and_the_queue_does_not_wait(db, fake, broker):
    world = Handed(db, [person(1, email=None)])

    run = await world.run()

    assert world.step(run['id'])['output']['eligible'] == 0
    assert world.hold(run['id']) is None
    assert world.detail(run['id'])['instantly'] is None
    assert world.waiting() is False


async def test_a_campaign_gone_from_instantly_fails_the_step_once_and_nobody_is_taken(db, fake, broker):
    fake.campaign_gone = True
    world = Handed(db, [person(1)])

    run = await world.run()

    assert (run['status'], run['error_code']) == ('failed', 'instantly_campaign_not_found')
    assert world.step(run['id'])['attempts'] == 1
    assert world.taken() == set() and world.hold(run['id']) is None


async def test_an_unreachable_instantly_is_tried_three_times_then_fails_the_run_and_nobody_is_taken(db, fake, broker):
    fake.refuse = 503
    world = Handed(db, [person(1)])

    run = await world.run()

    assert (run['status'], run['error_code']) == ('failed', 'instantly_unreachable')
    step = world.step(run['id'])
    assert (step['attempts'], step['output']) == (3, {'instantly_error': 'INSTANTLY.UNKNOWN_OUTCOME'})
    assert world.taken() == set() and world.hold(run['id']) is None


async def test_a_refused_key_fails_the_step_without_trying_again(db, fake, broker):
    fake.valid_key = 'another key'
    world = Handed(db, [person(1)])

    run = await world.run()

    assert (run['status'], run['error_code']) == ('failed', 'instantly_refused')
    assert world.step(run['id'])['attempts'] == 1


def unreadable_key(monkeypatch, code: str) -> None:
    """The stored key cannot be decrypted: the encryption key changed, or the server has none."""
    key = Fernet.generate_key().decode() if code == 'INTEGRATIONS.CRYPTO_FAILED' else ''
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', key)


@pytest.mark.parametrize('code', ['INTEGRATIONS.CRYPTO_FAILED', 'INTEGRATIONS.NOT_CONFIGURED'])
async def test_a_key_that_cannot_be_decrypted_fails_the_step_as_not_connected_without_trying_again(
    db, fake, broker, http, monkeypatch, code
):
    world = Handed(db, [person(1)])
    unreadable_key(monkeypatch, code)
    http.calls.clear()

    run = await world.run()

    assert (run['status'], run['error_code']) == ('failed', 'instantly_not_connected')
    step = world.step(run['id'])
    assert (step['attempts'], step['output']) == (1, {'instantly_error': code})
    assert http.calls == [] and world.taken() == set()


async def test_the_vendor_kill_switch_stops_the_step_before_any_call(db, fake, broker, http, monkeypatch):
    monkeypatch.setattr(instantly_step, 'is_disabled', lambda key: key == 'vendor_disabled:instantly')
    world = Handed(db, [person(1)])

    run = await world.run()

    assert (run['status'], run['error_code']) == ('failed', 'vendor_disabled')
    assert http.calls == []


async def test_a_read_tried_again_still_makes_one_hold(db, fake, broker, http):
    world = Handed(db, [person(1)])
    answer, failures = http.handler, [1]

    def first_fails(request):
        if failures and request.url.path.startswith('/api/v2/campaigns/'):
            failures.pop()
            return httpx.Response(503, json={})
        return answer(request)

    http.handler = first_fails

    run = await world.run()

    assert world.step(run['id'])['attempts'] == 2
    assert world.hold(run['id']) is not None
    assert db.execute('SELECT count(*) AS n FROM automation_instantly').fetchone()['n'] == 1


# ── taken and held commit together ──────────────────────────────────────────


async def test_a_run_that_fails_after_the_step_holds_nobody_and_the_next_run_takes_them_again(
    db, fake, broker, monkeypatch
):
    world = Handed(db, [person(1)], steps=('add_to_instantly', 'write_hubspot'), hubspot=True)
    await world.approved_run()
    db.execute("UPDATE instantly_enrollments SET status = 'completed'")
    late = world.add(person(2))
    monkeypatch.setattr(hubspot, 'is_disabled', lambda key: True)

    failed = await world.run()
    again = await world.run()

    assert (failed['status'], failed['error_code'], failed['review']) == ('failed', 'vendor_disabled', False)
    assert world.step(failed['id'])['status'] == 'succeeded'
    assert world.hold(failed['id']) is None and late not in world.taken()
    assert again['summary']['taken']['people'] == 1


async def test_a_run_that_dies_after_the_step_holds_nobody(db, fake, broker, monkeypatch):
    world = Handed(db, [person(1)])
    monkeypatch.setattr(runner, 'finish', lambda run, status, code: None)  # the worker dies before it closes the run

    run = await world.run()
    assert world.step(run['id'])['status'] == 'succeeded'
    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour' WHERE id = %s", (run['id'],))
    call_system(SystemReason.DUE_WORK, runner.maintain)

    assert (
        db.execute('SELECT status FROM automation_runs WHERE id = %s', (run['id'],)).fetchone()['status'] == 'unknown'
    )
    assert world.hold(run['id']) is None and world.taken() == set()


async def test_people_the_credit_cap_left_unreached_are_not_held(db, fake, broker, monkeypatch):
    monkeypatch.setattr(qualify, 'get_provider', lambda: Vendors())
    world = Handed(db, [person(1), person(2), person(3)], steps=('qualify', 'add_to_instantly'), cap=1)

    run = await world.run()

    assert run['error_code'] == 'credit_cap_reached'
    hold = world.hold(run['id'])
    assert len(hold['row_ids']) == 1
    assert world.taken() == {str(i) for i in hold['row_ids']}


# ── the one batch of a held run ─────────────────────────────────────────────


async def test_a_run_that_is_not_in_review_makes_one_queued_batch_once_it_ends(db, fake, broker):
    world = Handed(db, [person(1)])
    await world.approved_run()
    db.execute("UPDATE instantly_enrollments SET status = 'completed'")
    late = world.add(person(2))
    broker.sent.clear()

    run = await world.run()

    (_, batch) = world.batches()
    assert (batch['status'], str(batch['created_by']), batch['campaign_name']) == ('queued', world.uid, 'Q4 outreach')
    assert world.leads(batch['id']) == {late: 'pending'}
    hold = world.hold(run['id'])
    assert (hold['enrollment_id'], hold['row_ids']) == (batch['id'], [])
    assert broker.sent == [
        {'task': 'run_instantly_enrollment', 'batch': str(batch['id']), 'as': world.uid, 'seen': 'queued'}
    ]
    shown = world.detail(run['id'])['instantly']
    assert (shown['people'], shown['batch_id'], shown['waiting_for']) == (1, batch['id'], None)


async def test_approval_makes_the_batch_as_the_creator_and_a_second_approval_makes_no_other(db, fake, broker):
    world = Handed(db, [person(1), person(2)])
    run = await world.run()
    admin = teammate(db, world.org)
    db.execute('UPDATE member SET role = %s WHERE "userId" = %s', ('admin', admin))

    world.approve(run['id'], by=CurrentUser(id=admin, org_id=world.org, org_role='admin'))
    world.approve(run['id'])

    (batch,) = world.batches()
    assert str(batch['created_by']) == world.uid
    assert sorted(world.leads(batch['id'])) == sorted(world.rows)
    assert broker.sent == [
        {'task': 'run_instantly_enrollment', 'batch': str(batch['id']), 'as': world.uid, 'seen': 'queued'}
    ]
    assert world.detail(run['id'])['instantly']['batch_id'] == batch['id']


async def test_the_batch_is_made_once_when_two_drains_race(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    conninfo = make_conninfo(os.environ['TEST_DATABASE_URL'], dbname=db.info.dbname)
    with psycopg.connect(conninfo) as other:
        other.execute('SELECT 1 FROM automation_instantly WHERE run_id = %s FOR UPDATE', (run['id'],))
        world.approve(run['id'])  # the other drain holds the row: this one makes nothing
        assert world.batches() == []
    world.approve(run['id'])

    assert len(world.batches()) == 1


def waits_on_a_beat(world: Handed, run_id) -> None:
    """The run is approved while its project has a live batch, which then ends: only the beat can make its batch."""
    other = busy_batch(world)
    world.approve(run_id)
    world.db.execute("UPDATE instantly_enrollments SET status = 'completed' WHERE id = %s", (other,))


def older(db, run_id) -> None:
    db.execute("UPDATE automation_instantly SET created_at = now() - interval '1 hour' WHERE run_id = %s", (run_id,))


# How a project's connection stops its hold, and the ``waiting_for`` the run then shows.
BLOCKS = {
    'other_workspace': (lambda world: world.connect(workspace='ws-2'), 'other_workspace'),
    'other_batch': (busy_batch, 'other_batch'),
    'connection_in_error': (
        lambda world: world.db.execute(
            "UPDATE instantly_connections SET status = 'error' WHERE project_id = %s", (world.project,)
        ),
        'connection',
    ),
    'no_key': (
        lambda world: world.db.execute(
            "UPDATE instantly_connections SET api_key_encrypted = '' WHERE project_id = %s", (world.project,)
        ),
        'connection',
    ),
}


@pytest.mark.parametrize('blocked_by', list(BLOCKS))
async def test_a_hold_that_waits_does_not_take_the_beat_from_another_organization(
    db, fake, broker, monkeypatch, blocked_by
):
    monkeypatch.setattr(instantly_handoff, 'BATCH', 1)
    block, waiting_for = BLOCKS[blocked_by]
    stuck = Handed(db, [person(1)])
    held = await stuck.run()
    block(stuck)
    stuck.approve(held['id'])
    older(db, held['id'])
    ready = Handed(db, [person(2)])
    run = await ready.run()
    waits_on_a_beat(ready, run['id'])

    beat_tick()

    assert stuck.detail(held['id'])['instantly']['waiting_for'] == waiting_for
    (_, made) = ready.batches()
    assert ready.hold(run['id'])['enrollment_id'] == made['id']
    assert [s['batch'] for s in broker.sent] == [str(made['id'])]


async def test_the_beat_makes_one_batch_for_each_project_each_tick(db, fake, broker, monkeypatch):
    monkeypatch.setattr(instantly_handoff, 'BATCH', 2)
    crowded = Handed(db, [person(1)])
    first = await crowded.run()
    waits_on_a_beat(crowded, first['id'])
    crowded.add(person(3))
    other = busy_batch(crowded)
    second = await crowded.run()
    db.execute("UPDATE instantly_enrollments SET status = 'completed' WHERE id = %s", (other,))
    older(db, first['id'])
    older(db, second['id'])
    ready = Handed(db, [person(2)])
    run = await ready.run()
    waits_on_a_beat(ready, run['id'])

    beat_tick()

    assert [crowded.hold(r['id'])['enrollment_id'] is None for r in (first, second)].count(False) == 1
    assert ready.hold(run['id'])['enrollment_id'] is not None


async def test_a_paused_automation_keeps_the_hold_until_it_is_resumed(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    with world.scope():
        service.update(member(db, world.uid), world.project, world.id, AutomationPatch(enabled=False))

    world.approve(run['id'])
    beat_tick()

    assert world.batches() == []
    assert world.detail(run['id'])['instantly']['waiting_for'] == 'paused'
    with world.scope():
        service.update(member(db, world.uid), world.project, world.id, AutomationPatch(enabled=True))
    beat_tick()
    assert len(world.batches()) == 1


async def test_a_disconnected_workspace_keeps_the_hold_until_it_is_connected_again(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    world.disconnect()

    world.approve(run['id'])
    beat_tick()

    assert world.batches() == []
    assert world.detail(run['id'])['instantly']['waiting_for'] == 'connection'
    world.connect()
    beat_tick()
    assert len(world.batches()) == 1


async def test_a_creator_who_is_gone_keeps_the_hold(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    other = busy_batch(world)
    world.approve(run['id'])
    db.execute("UPDATE instantly_enrollments SET status = 'completed' WHERE id = %s", (other,))
    db.execute('UPDATE automations SET user_id = NULL WHERE id = %s', (world.id,))

    beat_tick()

    assert world.hold(run['id'])['enrollment_id'] is None
    assert world.detail(run['id'])['instantly']['waiting_for'] == 'creator_gone'


async def test_a_held_run_whose_people_left_the_list_drops_the_hold(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    db.execute('DELETE FROM list_rows WHERE list_id = %s', (world.list_id,))

    world.approve(run['id'])

    assert world.batches() == [] and world.hold(run['id']) is None
    assert world.detail(run['id'])['instantly'] is None


async def test_the_batch_judges_each_person_again(db, fake, broker):
    world = Handed(db, [person(1), person(2)])
    run = await world.run()
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (world.rows[1],))

    world.approve(run['id'])

    (batch,) = world.batches()
    assert world.leads(batch['id']) == {world.rows[0]: 'pending', world.rows[1]: 'skipped:excluded'}


async def test_the_batch_sends_the_default_fields(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    world.approve(run['id'])

    await world.publish(world.batches()[0]['id'])

    assert fake.add_calls == [
        [
            {
                'email': 'p1@acme1.test',
                'first_name': 'Pat',
                'last_name': 'Person1',
                'company_name': 'Acme',
                'job_title': 'CEO',
            }
        ]
    ]


# ── the enqueue and its repair ──────────────────────────────────────────────


async def test_a_broker_that_is_down_leaves_the_batch_queued_and_the_beat_sends_it_after_two_minutes(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    broker.down = True

    world.approve(run['id'])

    (batch,) = world.batches()
    assert batch['status'] == 'queued'
    broker.down = False
    beat_tick()
    assert broker.sent == []
    db.execute("UPDATE instantly_enrollments SET touched_at = now() - interval '3 minutes'")
    beat_tick()
    assert [s['batch'] for s in broker.sent] == [str(batch['id'])]


async def test_a_message_sent_twice_publishes_the_batch_once(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    world.approve(run['id'])
    (batch,) = world.batches()

    await world.publish(batch['id'])
    await world.publish(batch['id'])

    assert len(fake.add_calls) == 1


async def test_the_beat_cancels_a_queued_batch_whose_creator_left(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    broker.down = True
    world.approve(run['id'])
    broker.down = False
    db.execute('DELETE FROM member WHERE "userId" = %s', (world.uid,))
    db.execute("UPDATE instantly_enrollments SET touched_at = now() - interval '3 minutes'")

    beat_tick()

    (batch,) = world.batches()
    assert batch['status'] == 'cancelled' and broker.sent == []


# ── approval, edits and the webhook queue ───────────────────────────────────


async def test_the_webhook_queue_waits_while_a_review_run_holds_people_and_resumes_after_approval(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()

    assert world.waiting() is True
    world.approve(run['id'])
    assert world.waiting() is False


async def test_an_edit_lets_go_of_an_unapproved_review_run_and_its_people_are_taken_again(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await world.run()
    edited = Definition.model_validate(
        {'list_id': world.list_id, 'steps': [{'type': 'add_to_instantly', 'campaign_id': str(uuid.uuid4())}]}
    )

    with world.scope():
        service.update(member(db, world.uid), world.project, world.id, AutomationPatch(definition=edited))

    assert world.hold(run['id']) is None and world.taken() == set()
    assert world.waiting() is False
    assert (await world.run())['summary']['taken']['people'] == 1


def edit(world: Handed, patch: AutomationPatch) -> None:
    with world.scope():
        service.update(member(world.db, world.uid), world.project, world.id, patch)


async def an_approved_hold(world: Handed) -> dict:
    """An approved run whose batch waits: the project has a live batch."""
    run = await world.run()
    busy_batch(world)
    world.approve(run['id'])
    assert world.hold(run['id'])['enrollment_id'] is None
    return run


async def test_an_edit_that_replaces_the_campaign_lets_go_of_an_approved_hold_and_the_next_run_takes_them(
    db, fake, broker, http
):
    world = Handed(db, [person(1)])
    run = await an_approved_hold(world)
    replaced = str(uuid.uuid4())
    answer = http.handler
    http.handler = lambda request: (
        httpx.Response(200, json={'id': replaced, 'name': 'Q1 outreach', 'status': 0})
        if request.url.path == f'/api/v2/campaigns/{replaced}'
        else answer(request)
    )

    steps = [{'type': 'add_to_instantly', 'campaign_id': replaced}]
    edit(world, AutomationPatch(definition=Definition.model_validate({'list_id': world.list_id, 'steps': steps})))

    assert world.hold(run['id']) is None and world.taken() == set()
    again = await world.run()
    hold = world.hold(again['id'])
    assert (hold['campaign_id'], [str(i) for i in hold['row_ids']]) == (replaced, world.rows)


async def test_a_run_going_at_an_edit_that_replaces_the_campaign_holds_nobody_once_it_ends(db, fake, broker, http):
    world = Handed(db, [person(1)])
    await world.approved_run()
    db.execute("UPDATE instantly_enrollments SET status = 'completed'")
    late = world.add(person(2))
    answer, edits = http.handler, [str(uuid.uuid4())]

    def edited_during_the_read(request):
        if edits:
            steps = [{'type': 'add_to_instantly', 'campaign_id': edits.pop()}]
            edit(
                world, AutomationPatch(definition=Definition.model_validate({'list_id': world.list_id, 'steps': steps}))
            )
        return answer(request)

    http.handler = edited_during_the_read

    run = await world.run()

    assert (run['status'], world.hold(run['id'])) == ('succeeded', None)
    assert late not in world.taken()


async def test_an_edit_that_removes_the_step_lets_go_of_an_approved_hold(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await an_approved_hold(world)

    steps = [{'type': 'find_emails'}]
    edit(world, AutomationPatch(definition=Definition.model_validate({'list_id': world.list_id, 'steps': steps})))

    assert world.hold(run['id']) is None and world.taken() == set()


async def test_an_edit_that_keeps_the_campaign_keeps_the_approved_hold(db, fake, broker):
    world = Handed(db, [person(1)])
    run = await an_approved_hold(world)

    edit(world, AutomationPatch(credit_cap=30))

    assert world.hold(run['id'])['row_ids'] == [uuid.UUID(world.rows[0])]
    assert world.taken() == set(world.rows)


async def test_an_edit_of_another_step_keeps_the_approved_hold_and_the_beat_makes_its_batch(
    db, fake, broker, monkeypatch
):
    monkeypatch.setattr(qualify, 'get_provider', lambda: Vendors())
    world = Handed(db, [person(1)], steps=('qualify', 'add_to_instantly'))
    run = await an_approved_hold(world)
    steps = [
        {'type': 'qualify', 'brief': 'heads of revenue', 'min_score': 50},
        {'type': 'add_to_instantly', 'campaign_id': CAMPAIGN},
    ]

    edit(world, AutomationPatch(definition=Definition.model_validate({'list_id': world.list_id, 'steps': steps})))

    assert db.execute('SELECT version FROM automations WHERE id = %s', (world.id,)).fetchone()['version'] == 2
    assert world.hold(run['id'])['row_ids'] == [uuid.UUID(world.rows[0])]
    db.execute("UPDATE instantly_enrollments SET status = 'completed' WHERE project_id = %s", (world.project,))
    beat_tick()
    (_, made) = world.batches()
    assert world.hold(run['id'])['enrollment_id'] == made['id']


# ── the definition and its checks ───────────────────────────────────────────


def test_a_definition_adds_people_to_one_system(db, monkeypatch):
    uid = user(db)
    people, _ = make_list(db, uid, 'person')
    steps = [
        {'type': 'add_to_campaign', 'campaign_id': make_campaign(db, uid)},
        {'type': 'add_to_instantly', 'campaign_id': CAMPAIGN},
    ]
    with pytest.raises(ValidationError, match='not both'):
        Definition.model_validate({'list_id': people, 'steps': steps})
    body = {'name': 'Both', 'credit_cap': 5, 'definition': {'list_id': people, 'steps': steps}}
    refused = client(db, monkeypatch, uid).post('/v1/automations', json=body)
    assert (refused.status_code, refused.json()['detail']['code']) == (422, 'REQUEST.INVALID_INPUT')


async def test_a_disconnected_workspace_is_a_problem_on_save_and_at_the_claim(db, fake, broker, monkeypatch):
    with pytest.raises(HTTPException) as refused:
        Handed(db, [person(1)], connected=False)
    assert refused.value.detail['code'] == 'AUTOMATION.INSTANTLY_NOT_CONNECTED'
    world = Handed(db, [person(1)])
    world.disconnect()

    run = await world.run()

    assert (run['status'], run['error_code']) == ('failed', 'invalid_configuration')


# ── the preview ─────────────────────────────────────────────────────────────


def _preview(world: Handed, monkeypatch, steps=None) -> dict:
    api = client(world.db, monkeypatch, world.uid)
    definition = {'list_id': world.list_id, 'steps': steps or [{'type': 'add_to_instantly', 'campaign_id': CAMPAIGN}]}
    answer = api.post('/v1/automations/preview', json={'definition': definition, 'credit_cap': 5})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_the_preview_reads_the_campaign_once(db, fake, broker, http, monkeypatch):
    world = Handed(db, [person(1), person(2, email=None)])
    http.calls.clear()

    shown = _preview(world, monkeypatch)

    assert shown['problems'] == []
    assert shown['instantly'] == {
        'connected': True,
        'campaign': {
            'id': CAMPAIGN,
            'name': 'Q4 outreach',
            'status': 0,
            'may_send': False,
            'url': f'https://app.instantly.ai/app/campaign/{CAMPAIGN}',
        },
        'unreadable': None,
    }
    assert (shown['people'], shown['with_email']) == (2, 1)
    assert [c.url.path for c in http.calls] == [f'/api/v2/campaigns/{CAMPAIGN}']


async def test_the_preview_names_a_campaign_gone_from_instantly(db, fake, broker, monkeypatch):
    world = Handed(db, [person(1)])
    fake.campaign_gone = True

    shown = _preview(world, monkeypatch)

    assert [p['code'] for p in shown['problems']] == ['AUTOMATION.INSTANTLY_CAMPAIGN_NOT_FOUND']
    assert shown['instantly'] == {'connected': True, 'campaign': None, 'unreadable': None}


async def test_an_unreachable_instantly_is_no_problem_in_the_preview(db, fake, broker, monkeypatch):
    world = Handed(db, [person(1)])
    fake.refuse = 429

    shown = _preview(world, monkeypatch)

    assert shown['problems'] == []
    assert shown['instantly'] == {'connected': True, 'campaign': None, 'unreadable': 'INSTANTLY.RATE_LIMITED'}


@pytest.mark.parametrize('code', ['INTEGRATIONS.CRYPTO_FAILED', 'INTEGRATIONS.NOT_CONFIGURED'])
async def test_a_key_that_cannot_be_decrypted_is_unreadable_in_the_preview(db, fake, broker, http, monkeypatch, code):
    world = Handed(db, [person(1)])
    unreadable_key(monkeypatch, code)
    http.calls.clear()

    shown = _preview(world, monkeypatch)

    assert shown['problems'] == []
    assert shown['instantly'] == {'connected': True, 'campaign': None, 'unreadable': code}
    assert http.calls == []


async def test_a_disconnected_preview_names_the_problem_and_reads_no_vendor(db, fake, broker, http, monkeypatch):
    world = Handed(db, [person(1)])
    world.disconnect()
    http.calls.clear()

    shown = _preview(world, monkeypatch)

    assert [p['code'] for p in shown['problems']] == ['AUTOMATION.INSTANTLY_NOT_CONNECTED']
    assert shown['instantly'] == {'connected': False, 'campaign': None, 'unreadable': None}
    assert http.calls == []


async def test_a_preview_without_the_step_reads_no_vendor(db, fake, broker, http, monkeypatch):
    world = Handed(db, [person(1)])
    http.calls.clear()

    shown = _preview(world, monkeypatch, steps=[{'type': 'find_emails'}])

    assert shown['instantly'] is None and http.calls == []


# ── refused before any credit ───────────────────────────────────────────────


async def test_take_refuses_people_instantly_or_a_native_campaign_holds_before_any_credit(
    db, fake, broker, monkeypatch
):
    monkeypatch.setattr(qualify, 'get_provider', lambda: Vendors())
    world = Handed(db, [person(1), person(2), person(3)], steps=('qualify', 'add_to_instantly'))
    db.execute(
        'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, row_id, workspace_id, campaign_id,'
        " email, outcome) VALUES (%s, %s, %s, %s, 'ws-1', %s, 'p1@acme1.test', 'accepted')",
        (world.org, world.project, busy_batch(world), world.rows[0], CAMPAIGN),
    )
    native = make_campaign(db, world.uid, status='active')
    db.execute(
        "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, %s, 'active')",
        (native, world.uid, 'p2@acme2.test'),
    )

    run = await world.run()

    assert run['summary']['taken'] == {
        'people': 1,
        'refused_before_any_credit': {'held_by_instantly': 1, 'in_other_campaign': 1},
    }
    assert run['credits_spent'] == 1
    assert world.taken() == set(world.rows)


async def test_take_does_not_refuse_a_person_without_an_email(db, fake, broker):
    world = Handed(db, [person(1, email=None)])

    run = await world.run()

    assert run['summary']['taken'] == {'people': 1, 'refused_before_any_credit': {}}
    assert world.step(run['id'])['output']['skipped'] == {'no_email': 1}
