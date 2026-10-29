"""Actual scheduled execution/credits/refill with fake vendor handlers and disposable SQL."""

import asyncio
import contextlib
import os
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.price import Price
from app.core.scope import ActorGone, ScopeMissing, acting_as, for_another_thread, system
from app.core.system_reasons import SystemReason
from app.outreach import dispatcher, drafts
from app.scheduling import run_credits, runner, service
from app.scheduling.models import SearchInput
from tests.conftest import (
    act_as,
    call_system,
    conninfo_of,
    each_org,
    hold,
    migrated_database,
    pool_on,
    project,
    scoped_conn,
)
from tests.test_campaign_fields_sql import NEWS, STEP
from tests.test_campaign_targets_row_sql import linked
from tests.test_holds_sql import stop

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL required for scheduling SQL checks')


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (conn, conninfo), pool_on(conninfo):
        scoped = scoped_conn(conn)

        @contextlib.contextmanager
        def connection(timeout=None):
            with conn.transaction(), scoped(timeout) as c:
                yield c

        monkeypatch.setattr(service, 'get_conn', connection)
        monkeypatch.setattr(runner, 'get_conn', connection)
        yield conn


def setup(db, *, entity='person', campaign=True, credits=20):
    user = str(uuid.uuid4())
    db.execute(
        'INSERT INTO "user" (id,name,email,"emailVerified","createdAt","updatedAt")'
        " VALUES (%s,'T',%s,false,now(),now())",
        (user, f'{user}@example.test'),
    )
    db.execute(
        'INSERT INTO user_credits (organization_id,user_id,credits_remaining) VALUES (default_organization(%s),%s,%s)',
        (user, user, credits),
    )
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id,name,entity) VALUES (default_organization(%s), %s, %s,'People',%s) RETURNING id",
        (user, project(db, user), user, entity),
    ).fetchone()['id']
    cid = None
    if campaign:
        cid = db.execute(
            "INSERT INTO outreach_campaigns(organization_id, project_id, owner_id,name,status,steps) VALUES (default_organization(%s), %s, %s,'C','active',%s) RETURNING id",
            (
                user,
                project(db, user),
                user,
                Jsonb([{'step': 1, 'channel': 'email', 'subject': 'Hi', 'body': 'Hello', 'send_after_days': 0}]),
            ),
        ).fetchone()['id']
    act_as(db.execute('SELECT default_organization(%s) AS id', (user,)).fetchone()['id'], user)
    body = SearchInput(
        name='Weekly',
        tool_name='search_people' if entity == 'person' else 'search_companies',
        list_id=lid,
        campaign_id=cid,
        next_run_at=datetime.now(UTC) + timedelta(days=1),
    )
    return user, service.save(user, body)


# A paid search: every row it finds costs 1 credit. The real searches are free (no Price).
PAID = Price(1, (), lambda cells: True)


def fake(monkeypatch, *, rows=None, ok=True, callback=None, price=PAID):
    calls = []

    async def handler(args, ctx):
        calls.append(ctx)
        if callback:
            callback()
        return ToolResult(
            ok=ok,
            rows=rows
            if rows is not None
            else [{'first_name': 'Ada', 'linkedin_url': 'https://www.linkedin.com/in/ada/?trk=x', 'email': None}],
            entity='person',
        )

    tool = ToolDef('search_people', '', {}, handler, price=price)
    monkeypatch.setattr(runner.registry, 'get', lambda _: tool)
    return calls, tool


def run_row(db, run_id):
    return db.execute('SELECT * FROM saved_search_runs WHERE id=%s', (run_id,)).fetchone()


def balance(db, user):
    return db.execute('SELECT credits_remaining AS n FROM user_credits WHERE user_id=%s', (user,)).fetchone()['n']


async def test_refill_into_an_active_campaign_is_planned_by_the_dispatcher(db, monkeypatch):
    user, search = setup(db)
    fake(monkeypatch, rows=[{'first_name': 'Ada', 'email': 'ada@client.test'}])
    db.execute('UPDATE outreach_campaigns SET activated_at = now() WHERE id=%s', (search['campaign_id'],))
    await runner.run_search(service.run_now(user, search['id'])['id'])
    monkeypatch.setattr(dispatcher, 'get_conn', scoped_conn(db))
    plan = each_org(db, dispatcher.plan, datetime.now(UTC) + timedelta(minutes=1))
    assert plan['approved'] + plan['awaiting_approval'] == 1
    target = db.execute('SELECT id FROM outreach_campaign_targets').fetchone()
    assert db.execute('SELECT target_id FROM outreach_campaign_sends').fetchone()['target_id'] == target['id']
    assert linked(db, search['campaign_id']) == [('ada@client.test', 'ada@client.test')]


async def test_refill_stops_at_the_campaign_cap_and_is_done(db, monkeypatch):
    user, search = setup(db)
    monkeypatch.setattr(drafts, 'MAX_TARGETS', 1)
    fake(
        monkeypatch,
        price=None,
        rows=[
            {'first_name': 'Ada', 'email': 'ada@client.test', 'linkedin_url': 'https://www.linkedin.com/in/ada/'},
            {'first_name': 'Bo', 'email': 'bo@client.test', 'linkedin_url': 'https://www.linkedin.com/in/bo/'},
        ],
    )
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 1
    result = run_row(db, run['id'])
    assert result['refill_done'] and result['pending_row_ids'] == []
    checked = result['refill_checked_at']
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert run_row(db, run['id'])['refill_checked_at'] == checked  # a done run is not picked again


async def test_duplicate_delivery_atomic_charge_and_email_campaign_draft(db, monkeypatch, balance_events):
    user, search = setup(db)
    calls, _ = fake(monkeypatch)
    first = service.run_now(user, search['id'])
    assert service.run_now(user, search['id'])['id'] == first['id']
    await runner.run_search(first['id'])
    await runner.run_search(first['id'])
    assert len(calls) == 1 and balance(db, user) == 19
    org = str(db.execute('SELECT default_organization(%s) AS id', (user,)).fetchone()['id'])
    assert balance_events == [(org, False)] * 2  # #243: the hold and the settle; the redelivery claims nothing
    result = run_row(db, first['id'])
    assert result['status'] == 'succeeded' and result['credits_spent'] == 1 and len(result['new_row_ids']) == 1
    target = db.execute('SELECT * FROM outreach_campaign_targets').fetchone()
    assert target['status'] == 'active' and target['source'] == 'refill' and target['email'] is None
    assert target['linkedin_url'] == 'https://www.linkedin.com/in/ada/?trk=x'
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_sends').fetchone()['n'] == 0
    second = service.run_now(user, search['id'])
    await runner.run_search(second['id'])
    assert run_row(db, second['id'])['new_row_ids'] == []
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 1


@pytest.mark.parametrize('ok', [True, False])
async def test_empty_failure_refund(db, monkeypatch, ok):
    user, search = setup(db)
    fake(monkeypatch, rows=[], ok=ok)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert balance(db, user) == 20
    assert db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 0
    assert run_row(db, run['id'])['credits_spent'] == 0 and balance(db, user) == 20  # nothing delivered, nothing billed


async def test_no_credits_disabled_and_pause_never_call(db, monkeypatch, balance_events):
    user, search = setup(db, credits=0)
    calls, tool = fake(monkeypatch)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert not calls and run_row(db, run['id'])['error_code'] == 'insufficient_credits'
    org = str(db.execute('SELECT default_organization(%s) AS id', (user,)).fetchone()['id'])
    assert balance_events == [(org, True)]  # #243: the refused hold queues the rules on available credits
    db.execute('UPDATE user_credits SET credits_remaining=10 WHERE user_id=%s', (user,))
    monkeypatch.setattr(runner.registry, 'get', lambda _: replace(tool, provider='apollo'))
    monkeypatch.setattr(runner, 'vendor_disabled_now', lambda _: True)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert not calls and balance(db, user) == 10
    run = service.run_now(user, search['id'])
    service.toggle(user, search['id'], enabled=False)
    await runner.run_search(run['id'])
    assert not calls and run_row(db, run['id'])['status'] == 'cancelled'


async def test_pause_during_call_discards_result_refunds(db, monkeypatch):
    user, search = setup(db)
    fake(monkeypatch, callback=lambda: service.toggle(user, search['id'], enabled=False))
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert balance(db, user) == 20 and run_row(db, run['id'])['status'] == 'cancelled'
    assert db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 0
    assert run_row(db, run['id'])['credits_spent'] == 0 and balance(db, user) == 20  # nothing delivered, nothing billed


async def test_db_failure_rolls_back_output_and_claim_prevents_rebuy(db, monkeypatch):
    user, search = setup(db)
    calls, _ = fake(monkeypatch)
    run = service.run_now(user, search['id'])
    original = run_credits.add_rows

    def broken(*args):
        original(*args)
        raise RuntimeError('simulated storage failure')

    monkeypatch.setattr(run_credits, 'add_rows', broken)
    with pytest.raises(RuntimeError):
        await runner.run_search(run['id'])
    assert run_row(db, run['id'])['status'] == 'running'
    assert db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 0
    await runner.run_search(run['id'])
    assert len(calls) == 1
    db.execute("UPDATE saved_search_runs SET started_at=now()-interval '18 minutes' WHERE id=%s", (run['id'],))
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert run_row(db, run['id'])['status'] == 'unknown' and balance(db, user) == 20


def test_late_result_cannot_publish_after_hold_expired(db, monkeypatch):
    user, search = setup(db)
    fake(monkeypatch)
    run = service.run_now(user, search['id'])
    claimed = runner.claim(run['id'])
    db.execute('SELECT release_reservation(%s)', (claimed['reservation_id'],))
    runner.finish(run['id'], ToolResult(ok=True, entity='person', rows=[{'email': 'ada@example.test'}]))
    assert run_row(db, run['id'])['status'] == 'unknown'
    assert balance(db, user) == 20 and db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 0


async def test_paused_campaign_refill_resumes_without_new_paid_call(db, monkeypatch):
    user, search = setup(db)
    db.execute("UPDATE outreach_campaigns SET status='paused' WHERE id=%s", (search['campaign_id'],))
    calls, _ = fake(monkeypatch)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert not run_row(db, run['id'])['refill_done']
    db.execute("UPDATE outreach_campaigns SET status='active' WHERE id=%s", (search['campaign_id'],))
    call_system(SystemReason.DUE_WORK, runner.maintain)
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert len(calls) == 1 and balance(db, user) == 19
    assert run_row(db, run['id'])['refill_done']
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 1


@pytest.mark.parametrize('status', ['excluded', 'replied'])
async def test_blocked_person_in_other_campaign_not_revived(db, monkeypatch, status):
    user, search = setup(db)
    other = db.execute(
        "INSERT INTO outreach_campaigns(organization_id, project_id, owner_id,name,status) VALUES (default_organization(%s), %s, %s,'Other','stopped') RETURNING id",
        (
            user,
            project(db, user),
            user,
        ),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_targets(campaign_id,owner_id,linkedin_url,status) VALUES (%s,%s,%s,%s)',
        (other, user, 'http://linkedin.com/in/ada/#blocked', status),
    )
    fake(monkeypatch)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert (
        db.execute(
            'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id=%s', (search['campaign_id'],)
        ).fetchone()['n']
        == 0
    )


async def test_excluded_new_list_row_not_refilled(db, monkeypatch):
    user, search = setup(db)
    db.execute("UPDATE outreach_campaigns SET status='paused' WHERE id=%s", (search['campaign_id'],))
    fake(monkeypatch)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    db.execute('UPDATE list_rows SET excluded=true')
    db.execute("UPDATE outreach_campaigns SET status='active' WHERE id=%s", (search['campaign_id'],))
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert run_row(db, run['id'])['refill_done']
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 0


async def _paused_refill(db, monkeypatch, rows):
    """A run of ``rows`` held while its campaign is paused; the caller changes the list, then calls ``refill``."""
    user, search = setup(db)
    db.execute("UPDATE outreach_campaigns SET status='paused' WHERE id=%s", (search['campaign_id'],))
    fake(monkeypatch, rows=rows, price=None)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    db.execute("UPDATE outreach_campaigns SET status='active' WHERE id=%s", (search['campaign_id'],))
    return user, search, run['id']


def _targets(db):
    return [
        (t['first_name'], t['email']) for t in db.execute('SELECT * FROM outreach_campaign_targets ORDER BY first_name')
    ]


async def test_refill_enrolls_a_row_whose_required_email_is_still_empty_by_its_linkedin(db, monkeypatch):
    """A saved search never enriches, so a list that requires ``email`` must still take its people by LinkedIn."""
    rows = [{'first_name': 'Ada', 'linkedin_url': 'https://linkedin.com/in/ada', 'email': None}]
    _, search, run_id = await _paused_refill(db, monkeypatch, rows)
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'email', 'Email', 'static', 99, '{\"required\": true}')",
        (search['list_id'],),
    )
    runner.refill(run_id)
    assert _targets(db) == [('Ada', None)]
    assert run_row(db, run_id)['refill_done']


@pytest.mark.parametrize(('state', 'email'), [('charged', 'ada@acme.test'), ('refunded', None)])
async def test_refill_holds_a_row_with_a_pending_charge_until_its_turn_settles(db, monkeypatch, state, email):
    """#462: the running turn may still refund Ada's email, so she waits and enrolls with what the turn left."""
    rows = [{'first_name': 'Ada', 'linkedin_url': 'https://linkedin.com/in/ada', 'email': 'ada@acme.test'}]
    user, _, run_id = await _paused_refill(db, monkeypatch, rows)
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id)'
        ' VALUES (default_organization(%s), %s, %s) RETURNING id',
        (user, project(db, user), user),
    ).fetchone()['id']
    jid = db.execute(
        'INSERT INTO jobs (organization_id, user_id, chat_session_id, status)'
        " VALUES ((SELECT organization_id FROM chat_sessions WHERE id = %s), %s, %s, 'running') RETURNING id",
        (sid, user, sid),
    ).fetchone()['id']
    ada = db.execute(
        "INSERT INTO row_charges (hold_id, job_id, row_id, keys, credits, state) SELECT %s, %s, id, '{email}', 1,"
        " 'held' FROM list_rows RETURNING row_id",
        (hold(db, user, jid), jid),
    ).fetchone()['row_id']

    runner.refill(run_id)
    run = run_row(db, run_id)
    assert (_targets(db), run['pending_row_ids'], run['refill_done']) == ([], [ada], False)

    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (jid,))
    db.execute('UPDATE row_charges SET state = %s', (state,))
    if state == 'refunded':
        db.execute("UPDATE list_rows SET cells = cells - 'email'")
    runner.refill(run_id)
    assert _targets(db) == [('Ada', email)]
    assert run_row(db, run_id)['refill_done']


async def test_refill_that_waits_on_the_turns_row_lock_still_holds_the_row(db, monkeypatch):
    """#462: the refill locks Ada while a turn stores and charges her email, then reads her after the turn commits."""
    rows = [{'first_name': 'Ada', 'linkedin_url': 'https://linkedin.com/in/ada', 'email': None}]
    user, _, run_id = await _paused_refill(db, monkeypatch, rows)
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id)'
        ' VALUES (default_organization(%s), %s, %s) RETURNING id',
        (user, project(db, user), user),
    ).fetchone()['id']
    jid = db.execute(
        'INSERT INTO jobs (organization_id, user_id, chat_session_id, status)'
        " VALUES ((SELECT organization_id FROM chat_sessions WHERE id = %s), %s, %s, 'running') RETURNING id",
        (sid, user, sid),
    ).fetchone()['id']
    hid = hold(db, user, jid)
    turn = psycopg.connect(conninfo_of(db))
    ada = turn.execute(
        """UPDATE list_rows SET cells = cells || '{"email": "ada@acme.test"}' RETURNING id"""
    ).fetchone()[0]
    turn.execute(
        'INSERT INTO row_charges (hold_id, job_id, row_id, keys, credits, state)'
        " VALUES (%s, %s, %s, '{email}', 1, 'held')",
        (hid, jid, ada),
    )
    # The run's scope goes with it, as the worker enters it for the refill task.
    thread = threading.Thread(target=for_another_thread().run, args=(runner.refill, run_id))
    thread.start()
    with psycopg.connect(conninfo_of(db)) as watch:
        deadline = time.monotonic() + 10
        while not watch.execute(  # the refill waits on the row the turn wrote
            "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone():
            assert time.monotonic() < deadline, 'the refill never waited on the turn'
            await asyncio.sleep(0.01)
    turn.commit()
    turn.close()
    thread.join()

    run = run_row(db, run_id)
    assert (_targets(db), run['pending_row_ids'], run['refill_done']) == ([], [ada], False)


def _as(db, user, org=None):
    """The scope the API enters for ``user``'s request, and the worker for a run they queued: their own organization
    unless ``org`` names another."""
    org = org or str(db.execute('SELECT default_organization(%s) AS id', (user,)).fetchone()['id'])
    return acting_as(org, user)


def test_owner_links_and_recheck_at_worker(db):
    user, search = setup(db)
    other, other_search = setup(db)
    with _as(db, other), pytest.raises(HTTPException) as exc:
        service.run_now(other, search['id'])
    assert exc.value.status_code == 404
    for updates in ({'list_id': other_search['list_id']}, {'campaign_id': other_search['campaign_id']}):
        body = SearchInput.model_validate({**{k: search[k] for k in SearchInput.model_fields}, **updates})
        with _as(db, user), pytest.raises(HTTPException):
            service.save(user, body)
    with _as(db, user):
        run = service.run_now(user, search['id'])
    db.execute('UPDATE lists SET user_id=%s WHERE id=%s', (other, search['list_id']))
    with _as(db, user):
        assert runner.claim(run['id']) is None
    assert run_row(db, run['id'])['status'] == 'failed' and balance(db, user) == 20


def _campaign_of_another_project(db, user) -> str:
    """An active campaign of the user's, in their organization, but in a second project (client, #307)."""
    project_b = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'B') RETURNING id",
        (user, user),
    ).fetchone()['id']
    return str(
        db.execute(
            'INSERT INTO outreach_campaigns(organization_id, project_id, owner_id, name, status, steps)'
            " VALUES (default_organization(%s), %s, %s, 'B', 'active', %s) RETURNING id",
            (user, project_b, user, Jsonb([{**STEP, 'body': 'Hello'}])),
        ).fetchone()['id']
    )


def test_a_search_refuses_a_campaign_of_another_project(db):
    """#1086: a list of one client never refills a campaign of another, on create or on edit."""
    user, search = setup(db)
    links = {**{k: search[k] for k in SearchInput.model_fields}, 'campaign_id': _campaign_of_another_project(db, user)}
    for search_id in (None, search['id']):
        with _as(db, user), pytest.raises(HTTPException) as exc:
            service.save(user, SearchInput.model_validate(links), search_id)
        assert (exc.value.status_code, exc.value.detail['code']) == (404, 'CAMPAIGN.NOT_FOUND')


async def test_refill_skips_a_campaign_of_another_project_stored_before_the_check(db, monkeypatch):
    """#1086: a run stored before the save refused another client's campaign enrolls nobody into it."""
    user, search, run_id = await _paused_refill(db, monkeypatch, [{'first_name': 'Ada', 'email': 'ada@client.test'}])
    other = _campaign_of_another_project(db, user)
    db.execute('UPDATE saved_searches SET campaign_id=%s WHERE id=%s', (other, search['id']))
    db.execute(
        "UPDATE saved_search_runs SET config=jsonb_set(config, '{campaign_id}', to_jsonb(%s::text)) WHERE id=%s",
        (other, run_id),
    )
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert (_targets(db), run_row(db, run_id)['refill_done']) == ([], True)
    assert db.execute('SELECT count(*) AS n FROM list_rows WHERE list_id=%s', (search['list_id'],)).fetchone()['n'] == 1


@pytest.mark.parametrize(
    'read',
    [
        lambda c, user, search: service.owned(c, user, search['id']),
        lambda c, user, search: service.validate_links(c, user, search),
        lambda c, user, search: service.list_searches(),
    ],
    ids=['owned', 'validate_links', 'list_searches'],
)
def test_a_system_scope_reads_no_saved_search(db, read):
    """System bypasses row-level security, so these reads would see every organization's searches: each holds itself
    to the scope's organization and refuses a scope with none."""
    user, search = setup(db)
    with system(SystemReason.DUE_WORK), service.get_conn() as c, pytest.raises(ScopeMissing):
        read(c, user, search)


def test_beat_catches_up_once_and_requeues_broker_failure(db):
    user, search = setup(db)
    db.execute("UPDATE saved_searches SET next_run_at=now()-interval '30 days' WHERE id=%s", (search['id'],))
    ids = call_system(SystemReason.DUE_WORK, service.schedule_due)
    assert len(ids) == 1 and call_system(SystemReason.DUE_WORK, service.schedule_due) == ids
    current = db.execute('SELECT next_run_at FROM saved_searches WHERE id=%s', (search['id'],)).fetchone()
    assert current['next_run_at'] > datetime.now(UTC)
    service.toggle(user, search['id'], enabled=False)
    assert call_system(SystemReason.DUE_WORK, service.schedule_due) == []


def test_pending_refill_fairness_beyond_one_batch(db, monkeypatch):
    user, search = setup(db)
    config = SearchInput.model_validate({k: search[k] for k in SearchInput.model_fields}).model_dump(mode='json')
    ids = [
        db.execute(
            'INSERT INTO saved_search_runs(saved_search_id,user_id,scheduled_for,config,status,finished_at)'
            " VALUES (%s,%s,%s,%s,'succeeded',now()) RETURNING id",
            (search['id'], user, datetime.now(UTC) + timedelta(days=i), Jsonb(config)),
        ).fetchone()['id']
        for i in range(101)
    ]
    called = []

    def pending(run_id):
        called.append(run_id)
        db.execute('UPDATE saved_search_runs SET refill_checked_at=now() WHERE id=%s', (run_id,))

    monkeypatch.setattr(runner, 'refill', pending)
    call_system(SystemReason.DUE_WORK, runner.maintain)
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert set(called) == set(ids)
    service.toggle(user, search['id'], enabled=False)
    called.clear()
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert not called


async def test_prior_sent_recipient_survives_target_deletion(db, monkeypatch):
    user, search = setup(db)
    other = db.execute(
        "INSERT INTO outreach_campaigns(organization_id, project_id, owner_id,name,status) VALUES (default_organization(%s), %s, %s,'Old','stopped') RETURNING id",
        (
            user,
            project(db, user),
            user,
        ),
    ).fetchone()['id']
    target = db.execute(
        'INSERT INTO outreach_campaign_targets(campaign_id,owner_id,linkedin_url) VALUES (%s,%s,%s) RETURNING id',
        (other, user, 'https://linkedin.com/in/ada'),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends(campaign_id,owner_id,target_id,step,channel,status,recipient,'
        "idempotency_key,delivered_at) VALUES (%s,%s,%s,1,'linkedin_connect','sent',%s,'old-send',now())",
        (other, user, target, 'https://www.linkedin.com/in/ada/?tracking=old'),
    )
    db.execute('DELETE FROM outreach_campaign_targets WHERE id=%s', (target,))
    fake(monkeypatch)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 0


async def test_opaque_linkedin_id_case_preserved(db, monkeypatch):
    user, search = setup(db)
    fake(monkeypatch, rows=[{'linkedin_url': 'https://www.linkedin.com/in/ACoSecretMemberID/'}])
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert db.execute('SELECT linkedin_url FROM outreach_campaign_targets').fetchone()['linkedin_url'] == (
        'https://www.linkedin.com/in/ACoSecretMemberID/'
    )


def test_api_key_crud_and_tenant_scoping(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import middleware
    from app.main import app
    from app.scheduling import routes

    monkeypatch.setattr(middleware, 'get_conn', service.get_conn)
    monkeypatch.setattr(routes, 'get_conn', service.get_conn)
    user, search = setup(db)
    _foreign, foreign_search = setup(db)
    key = 'sbk_scheduling-test'
    db.execute(
        "INSERT INTO user_api_keys(organization_id, user_id,label,key_hash) VALUES (default_organization(%s), %s,'Schedule',%s)",
        (user, user, middleware.hash_api_key(key)),
    )
    tc = TestClient(app)
    assert tc.get('/v1/saved-searches').status_code == 401
    tc.headers['Authorization'] = f'Bearer {key}'
    assert tc.get('/v1/saved-searches').json()['saved_searches'][0]['id'] == str(search['id'])
    foreign = tc.post(f'/v1/saved-searches/{foreign_search["id"]}/run')
    assert (foreign.status_code, foreign.json()['detail']['code']) == (404, 'SAVED_SEARCH.NOT_FOUND')
    assert tc.get(f'/v1/saved-searches/{foreign_search["id"]}/runs').status_code == 404
    url = f'/v1/saved-searches/{search["id"]}'
    assert tc.patch(url, json={'enabled': False}).status_code == 200
    paused = tc.post(f'{url}/run')
    assert (paused.status_code, paused.json()['detail']['code']) == (409, 'SAVED_SEARCH.PAUSED')
    assert tc.patch(url, json={'enabled': True}).status_code == 200
    assert tc.post(f'{url}/run').status_code == 202
    body = SearchInput.model_validate({k: search[k] for k in SearchInput.model_fields}).model_dump(mode='json')
    running = tc.put(url, json=body)
    assert (running.status_code, running.json()['detail']['code']) == (409, 'SAVED_SEARCH.RUN_IN_PROGRESS')
    assert tc.get(f'{url}/runs').json()['runs'][0]['status'] == 'queued'
    assert tc.delete(url).status_code == 204
    assert tc.get('/v1/saved-searches').json()['saved_searches'] == []
    assert tc.post(f'{url}/run').status_code == 404


def test_schedule_edit_does_not_deadlock_worker_foreign_key_claim(db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    user, search = setup(db)
    body = SearchInput.model_validate({k: search[k] for k in SearchInput.model_fields})
    user_locked, search_locked = Event(), Event()

    class ObservedConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, args=None):
            result = self.conn.execute(sql, args)
            if sql.startswith('SELECT id FROM "user"'):
                user_locked.set()
                assert search_locked.wait(5)
            return result

    @contextlib.contextmanager
    def connection():
        with psycopg.connect(conninfo_of(db), row_factory=dict_row, options='-c statement_timeout=3000') as conn:
            yield ObservedConnection(conn)

    monkeypatch.setattr(service, 'get_conn', connection)

    def claim_occurrence():
        with psycopg.connect(conninfo_of(db), options='-c statement_timeout=3000') as conn:
            conn.execute('SELECT id FROM saved_searches WHERE id=%s FOR UPDATE', (search['id'],))
            search_locked.set()
            assert user_locked.wait(5)
            conn.execute(
                'INSERT INTO saved_search_runs(saved_search_id,user_id,scheduled_for,config) VALUES (%s,%s,now(),%s)',
                (search['id'], user, Jsonb(body.model_dump(mode='json'))),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(claim_occurrence)
        assert search_locked.wait(5)
        # The pool thread starts with an empty context: it carries the member scope setup() entered.
        edit = pool.submit(for_another_thread().run, service.save, user, body, search['id'])
        claim.result(timeout=8)
        with pytest.raises(HTTPException) as exc:
            edit.result(timeout=8)
        assert exc.value.status_code == 409


def test_concurrent_campaign_refills_respect_active_duplicate_guard(db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    user, search = setup(db)
    cid = db.execute(
        "INSERT INTO outreach_campaigns(organization_id, project_id, owner_id,name,status) VALUES (default_organization(%s), %s, %s,'Other','active') RETURNING id",
        (
            user,
            project(db, user),
            user,
        ),
    ).fetchone()['id']
    lid = db.execute(
        "INSERT INTO lists(organization_id, project_id, user_id,name,entity) VALUES (default_organization(%s), %s, %s,'Other','person') RETURNING id",
        (
            user,
            project(db, user),
            user,
        ),
    ).fetchone()['id']
    other = service.save(user, SearchInput(name='Other', tool_name='search_people', list_id=lid, campaign_id=cid))
    run_ids = []
    for saved in (search, other):
        run = service.run_now(user, saved['id'])
        runner.claim(run['id'])
        runner.finish(
            run['id'], ToolResult(ok=True, entity='person', rows=[{'linkedin_url': 'https://linkedin.com/in/ada'}])
        )
        run_ids.append(run['id'])

    @contextlib.contextmanager
    def connection():
        with psycopg.connect(conninfo_of(db), row_factory=dict_row, options='-c statement_timeout=3000') as conn:
            yield conn

    monkeypatch.setattr(runner, 'get_conn', connection)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(for_another_thread().run, runner.refill, run_id) for run_id in run_ids]
        for future in futures:
            future.result(timeout=8)
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 1
    assert sorted(run_row(db, rid)['refill_done'] for rid in run_ids) == [False, True]


async def test_a_tool_priced_past_the_runs_one_credit_fails_before_any_hold(db, monkeypatch):
    """A scheduled run spends at most 1 credit: a search that prices more no longer fits it, so the run fails
    before it holds anything or calls the vendor."""
    user, search = setup(db)
    calls, _ = fake(monkeypatch, price=Price(2, (), lambda cells: True))
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    row = run_row(db, run['id'])
    assert (row['status'], row['error_code'], row['reservation_id']) == ('failed', 'tool_contract_changed', None)
    assert not calls and balance(db, user) == 20
    assert db.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0


async def test_a_free_scheduled_search_takes_no_hold_and_runs_on_an_empty_wallet(db, monkeypatch, balance_events):
    user, search = setup(db, credits=0)
    calls, _ = fake(monkeypatch, price=None)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    row = run_row(db, run['id'])
    assert (row['status'], row['credits_spent'], row['reservation_id']) == ('succeeded', 0, None)
    assert len(calls) == 1 and len(row['new_row_ids']) == 1 and balance(db, user) == 0
    assert db.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0
    assert balance_events == []


async def test_a_scheduled_search_past_the_daily_free_rows_never_reaches_the_vendor(db, monkeypatch):
    from app.agent import free_rows

    user, search = setup(db, credits=0)
    calls, _ = fake(monkeypatch, price=None)
    monkeypatch.setattr(free_rows, 'PER_DAY', 0)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    row = run_row(db, run['id'])
    assert (row['status'], row['error_code'], row['reservation_id']) == ('failed', 'over_budget', None)
    assert not calls


async def test_a_scheduled_search_settles_the_daily_free_rows_with_the_rows_returned(db, monkeypatch):
    from app.agent import free_rows

    user, search = setup(db)
    calls, _ = fake(monkeypatch, price=None)
    org = db.execute('SELECT default_organization(%s) AS o', (user,)).fetchone()['o']
    before = free_rows.today(str(org))
    await runner.run_search(service.run_now(user, search['id'])['id'])
    assert len(calls) == 1 and free_rows.today(str(org)) == before + 1


async def test_a_failed_scheduled_search_gives_its_daily_free_rows_back(db, monkeypatch):
    from app.agent import free_rows

    user, search = setup(db)

    def fail():
        raise RuntimeError('vendor down')

    fake(monkeypatch, price=None, callback=fail)
    org = db.execute('SELECT default_organization(%s) AS o', (user,)).fetchone()['o']
    before = free_rows.today(str(org))
    await runner.run_search(service.run_now(user, search['id'])['id'])
    assert free_rows.today(str(org)) == before


def test_the_reaper_queues_no_balance_event_for_a_free_run_it_closes(db, monkeypatch, balance_events):
    user, search = setup(db, credits=0)
    fake(monkeypatch, price=None)
    run = service.run_now(user, search['id'])
    assert runner.claim(run['id'])['reservation_id'] is None
    db.execute("UPDATE saved_search_runs SET started_at=now()-interval '18 minutes' WHERE id=%s", (run['id'],))
    runner.maintain()
    assert run_row(db, run['id'])['status'] == 'unknown'
    assert balance_events == []


async def test_handler_exception_logs_no_secret_message_or_traceback(db, monkeypatch):
    import structlog
    from structlog.testing import capture_logs

    user, search = setup(db)
    secret = 'synthetic-vendor-secret-DO-NOT-LOG'
    raw_error = f'upstream rejected credential {secret} for private@example.test'

    def fail():
        raise RuntimeError(raw_error)

    fake(monkeypatch, callback=fail)
    # Fresh lazy logger: previously cached loggers retain their old processors.
    monkeypatch.setattr(runner, 'logger', structlog.get_logger())
    run = service.run_now(user, search['id'])
    with capture_logs() as logs:
        await runner.run_search(run['id'])
    assert logs == [
        {
            'event': 'saved_search.call_failed',
            'run_id': str(run['id']),
            'error_type': 'RuntimeError',
            'log_level': 'error',
        }
    ]
    assert all('exc_info' not in entry for entry in logs)
    assert secret not in str(logs) and raw_error not in str(logs)
    assert run_row(db, run['id'])['error_code'] == 'search_failed'
    assert balance(db, user) == 20


async def test_user_exclusions_skip_scheduled_rows_and_refill(db, monkeypatch):
    user, search = setup(db)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain','blocked.test')",
        (project(db, user), user),
    )
    fake(monkeypatch, rows=[{'first_name': 'Bo', 'email': 'bo@blocked.test'}])
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert run_row(db, run['id'])['new_row_ids'] == []
    assert db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 0
    assert run_row(db, run['id'])['credits_spent'] == 0 and balance(db, user) == 20  # nothing delivered, nothing billed

    # An exclusion added while the campaign is paused still blocks the pending refill.
    db.execute("UPDATE outreach_campaigns SET status='paused' WHERE id=%s", (search['campaign_id'],))
    fake(monkeypatch)
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert balance(db, user) == 19
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'linkedin_url','linkedin.com/in/ada')",
        (project(db, user), user),
    )
    db.execute("UPDATE outreach_campaigns SET status='active' WHERE id=%s", (search['campaign_id'],))
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert run_row(db, run['id'])['refill_done']
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 0


def _team_search(db):
    """Bo, a member of Ada's team, saved a search on a team list feeding a team campaign (#306/#308)."""
    ada, _ = setup(db)  # Ada's own organization is the team; its wallet holds 20
    team = str(db.execute('SELECT default_organization(%s) AS id', (ada,)).fetchone()['id'])
    bo = str(uuid.uuid4())
    db.execute(
        'INSERT INTO "user" (id,name,email,"emailVerified","createdAt","updatedAt") VALUES (%s,\'B\',%s,false,now(),now())',
        (bo, f'{bo}@example.test'),
    )
    own = str(db.execute('SELECT default_organization(%s) AS id', (bo,)).fetchone()['id'])
    db.execute(
        'INSERT INTO member ("organizationId","userId",role,"createdAt") VALUES (%s,%s,\'member\',now())', (team, bo)
    )
    client = project(db, ada)  # a client (#307) of the team
    lid = db.execute(
        "INSERT INTO lists (organization_id,project_id,user_id,name,entity) VALUES (%s,%s,%s,'Team people','person')"
        ' RETURNING id',
        (team, client, bo),
    ).fetchone()['id']
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id,project_id,owner_id,name,status,steps)'
        " VALUES (%s,%s,%s,'C','active',%s) RETURNING id",
        (
            team,
            client,
            bo,
            Jsonb([{'step': 1, 'channel': 'email', 'subject': 'Hi', 'body': 'Hello', 'send_after_days': 0}]),
        ),
    ).fetchone()['id']
    body = SearchInput(name='Team', tool_name='search_people', list_id=lid, campaign_id=cid)
    with _as(db, bo, team):
        search = service.save(bo, body)
    return bo, team, own, search


def _wallet(db, org):
    return db.execute('SELECT credits_remaining AS n FROM user_credits WHERE organization_id=%s', (org,)).fetchone()[
        'n'
    ]


def test_a_removed_member_cannot_run_a_saved_search_on_the_team(db, monkeypatch):
    bo, team, own, search = _team_search(db)
    calls, _ = fake(monkeypatch)
    with _as(db, bo, own), pytest.raises(HTTPException):
        service.run_now(bo, search['id'])  # Bo's own-org token or key: not the list's organization
    with _as(db, bo, team):
        queued = service.run_now(bo, search['id'])
    db.execute('DELETE FROM member WHERE "userId"=%s AND "organizationId"=%s', (bo, team))

    with _as(db, bo, team), pytest.raises(ActorGone):
        service.run_now(bo, search['id'])  # the API, after the removal
    with _as(db, bo, team), pytest.raises(ActorGone):
        service.save(bo, SearchInput(name='Again', tool_name='search_people', list_id=search['list_id']))
    with _as(db, bo, team), pytest.raises(ActorGone):  # the worker, for the run queued before the removal
        runner.claim(queued['id'])
    row = run_row(db, queued['id'])
    assert (row['status'], row['error_code'], row['reservation_id']) == ('failed', 'creator_left', None)
    assert not calls and _wallet(db, team) == 20


async def test_a_member_removed_during_the_run_gets_nothing_written_and_the_hold_back(db, monkeypatch):
    bo, team, _, search = _team_search(db)
    fake(
        monkeypatch,
        callback=lambda: db.execute('DELETE FROM member WHERE "userId"=%s AND "organizationId"=%s', (bo, team)),
    )
    with _as(db, bo, team):
        run = service.run_now(bo, search['id'])
        with pytest.raises(ActorGone):
            await runner.run_search(run['id'])
    row = run_row(db, run['id'])
    assert (row['status'], row['error_code']) == ('failed', 'creator_left')
    assert _wallet(db, team) == 20
    assert not db.execute('SELECT 1 FROM list_rows WHERE list_id=%s', (search['list_id'],)).fetchone()


async def test_a_hard_project_cap_refuses_a_scheduled_run_before_the_vendor(db, monkeypatch):
    """#307: the list's client is at its hard monthly cap: the run is refused with credits in the wallet, nothing
    is held, the vendor is never called. A soft cap lets it run."""
    user, search = setup(db)
    calls, _ = fake(monkeypatch)
    pid = db.execute('SELECT project_id FROM lists WHERE id = %s', (search['list_id'],)).fetchone()['project_id']
    db.execute("UPDATE projects SET credit_cap = 1, credit_cap_mode = 'hard' WHERE id = %s", (pid,))
    db.execute(
        'INSERT INTO credit_transactions (organization_id, user_id, project_id, type, amount, balance_after, status)'
        " VALUES (default_organization(%s), %s, %s, 'deduction', 1, 20, 'committed')",
        (user, user, pid),
    )  # this month's 1 credit already spent
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    row = run_row(db, run['id'])
    assert (row['status'], row['error_code'], row['reservation_id']) == ('failed', 'project_cap_reached', None)
    assert not calls and balance(db, user) == 20
    db.execute("UPDATE projects SET credit_cap_mode = 'soft' WHERE id = %s", (pid,))
    run = service.run_now(user, search['id'])
    await runner.run_search(run['id'])
    assert run_row(db, run['id'])['status'] == 'succeeded' and len(calls) == 1


async def test_the_agent_lists_and_pauses_the_searches_its_user_saved(db):
    from app.agent.tools.handlers.scheduler import cancel, list_schedules
    from app.agent.tools.registry import ToolContext

    user, search = setup(db)
    other, _ = setup(db)
    with _as(db, user):  # a turn runs as the member who started it
        listed = await list_schedules({}, ToolContext(user_id=user))
    assert [s['id'] for s in listed.data['schedules']] == [str(search['id'])]
    with _as(db, other):
        assert (await list_schedules({}, ToolContext(user_id=other))).data['schedules'][0]['id'] != str(search['id'])

    with _as(db, user):
        paused = await cancel({'schedule_id': str(search['id'])}, ToolContext(user_id=user))
        assert paused.data == {'schedule_id': str(search['id']), 'enabled': False}
        assert (await list_schedules({}, ToolContext(user_id=user))).data['schedules'][0]['enabled'] is False


async def test_refill_holds_a_row_instantly_holds_and_enrolls_it_when_released(db, monkeypatch):
    rows = [{'first_name': 'Ada', 'linkedin_url': 'https://linkedin.com/in/ada', 'email': 'ada@acme.test'}]
    user, _, run_id = await _paused_refill(db, monkeypatch, rows)
    pid, org = project(db, user), db.execute('SELECT default_organization(%s) AS o', (user,)).fetchone()['o']
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
        " outcome) VALUES (%s, %s, %s, 'ws', 'camp', 'ada@acme.test', 'accepted')",
        (org, pid, batch),
    )
    runner.refill(run_id)
    run = run_row(db, run_id)
    assert (_targets(db), len(run['pending_row_ids']), run['refill_done']) == ([], 1, False)
    db.execute("UPDATE instantly_leads SET outcome = 'skipped'")
    runner.refill(run_id)
    assert _targets(db) == [('Ada', 'ada@acme.test')]
    assert run_row(db, run_id)['refill_done']


async def test_refill_never_enrolls_a_person_being_stopped_and_does_not_wait_for_them(db, monkeypatch):
    rows = [{'first_name': 'Ada', 'linkedin_url': 'https://linkedin.com/in/ada', 'email': 'ada@acme.test'}]
    user, _, run_id = await _paused_refill(db, monkeypatch, rows)
    stop(db, project(db, user), 'ada@acme.test')
    runner.refill(run_id)
    run = run_row(db, run_id)
    assert (_targets(db), run['pending_row_ids'], run['refill_done']) == ([], [], True)


async def test_a_refill_freezes_each_new_person_and_one_with_no_value_waits(db, monkeypatch):
    user, search = setup(db)
    rows = [
        {'first_name': 'Ada', 'email': 'ada@client.test', 'recent_news': 'a new office'},
        {'first_name': 'Bo', 'email': 'bo@client.test'},
    ]
    fake(monkeypatch, rows=rows, price=None)
    db.execute(
        'UPDATE outreach_campaigns SET fields = %s, steps = %s, activated_at = now() WHERE id = %s',
        (Jsonb([NEWS]), Jsonb([STEP | {'body': '{{recentNews}}'}]), search['campaign_id']),
    )
    await runner.run_search(service.run_now(user, search['id'])['id'])
    targets = {
        t['first_name']: (t['status'], t['personalization']['values']['recentNews'], t['personalization']['missing'])
        for t in db.execute('SELECT * FROM outreach_campaign_targets')
    }
    assert targets == {'Ada': ('active', 'a new office', []), 'Bo': ('draft', '', ['recentNews'])}
