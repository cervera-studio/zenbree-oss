"""#389: a ban stops the user's running jobs and pauses their campaigns, scheduled searches and webhooks; an unban,
or the ban's expiry, resumes the last three. Real SQL; the ban is written as Better Auth's banUser writes it."""

import asyncio
import dataclasses
import uuid

import psycopg
import pytest

from app.admin.ban import after_user_ban
from app.agent import events as job_events
from app.agent.tools import registry
from app.core.config import settings
from app.core.scope import ActorGone, acting_as, system
from app.core.system_reasons import SystemReason
from app.integrations import webhooks
from app.integrations.webhooks import _subscribed
from app.llm.base import ToolCall
from app.outreach import dispatcher
from app.scheduling import runner, service
from tests.conftest import call_for, each_org, each_org_async, member, project
from tests.test_agent import _balance, _job, _people, _run, _user, needs_db, paid_emails
from tests.test_agent import db as db
from tests.test_creator_deleted_sql import _campaign, _mailbox, _team
from tests.test_dispatcher_sql import NOW
from tests.test_dispatcher_sql import provider as provider
from tests.test_job_cancel import _terminal
from tests.test_scheduling_sql import setup
from tests.test_webhooks import _endpoint, _payload
from tests.test_webhooks import fernet_key as fernet_key
from tests.test_webhooks import receiver as receiver
from tests.test_webhooks import retries as retries

pytestmark = needs_db


def _ban(db, uid) -> None:
    db.execute('UPDATE "user" SET banned = true, "banReason" = \'spam\', "banExpires" = NULL WHERE id = %s', (uid,))


def _lift(db, uid, how: str) -> None:
    if how == 'unban':
        db.execute('UPDATE "user" SET banned = false, "banReason" = NULL WHERE id = %s', (uid,))
    else:  # a timed ban whose end passed: nothing is written, no event
        db.execute('UPDATE "user" SET "banExpires" = now() - interval \'1 second\' WHERE id = %s', (uid,))


def _after_ban(uid):
    with system(SystemReason.BACK_OFFICE):  # the web's after-ban call is a back-office route
        return after_user_ban(uuid.UUID(uid))


def _schedule_due():
    with system(SystemReason.DUE_WORK):  # as app/worker.py dispatch_saved_searches
        return service.schedule_due()


_EMAILS = paid_emails('fake_banning_emails', credits=2, batch=2)


async def _banning_emails(args, ctx):
    """The admin bans the user while this tool runs: the ban, which Better Auth writes as the login role, then the
    web's after-ban call, which the back office makes."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        _ban(conn, ctx.user_id)
    with system(SystemReason.BACK_OFFICE):
        _banning_emails.after_ban = after_user_ban(uuid.UUID(ctx.user_id))
    await asyncio.sleep(0.5)  # the watcher's blocking read sees the request before this tool returns
    return await _EMAILS.handler(args, ctx)


if not registry.get(_EMAILS.name):
    registry.register(dataclasses.replace(_EMAILS, handler=_banning_emails))


async def test_a_ban_cancels_the_running_job_and_releases_its_hold(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'agenzie a Milano')
    _people(db, uid, sid, 2)
    calls = [ToolCall('t1', 'fake_banning_emails', {}), ToolCall('t2', 'fake_companies', {})]

    events, _ = await _run(jid, [('', calls), ('unused', [])])

    assert _banning_emails.after_ban == {'cancelled_jobs': 1}
    job, msg, held = _terminal(db, jid, sid)
    assert [d['tool_id'] for e, d in events if e == 'tool_start'] == ['t1']  # nothing after the ban
    # The tool's next database call ends the turn, before the stop request the after-ban call left
    assert [d['code'] for e, d in events if e == 'error'] == ['AUTH.NOT_A_MEMBER']
    assert (job['status'], msg['terminal_code']) == ('failed', None)
    assert held == 0 and _balance(db, uid) == 50 - job['credits_spent']


def test_after_ban_touches_nobody_who_is_not_banned(db):
    uid = _user(db)
    _, jid = _job(db, uid, 'x')
    assert _after_ban(uid) == {'cancelled_jobs': 0}
    assert not job_events.cancel_requested(jid)

    _ban(db, uid)
    db.execute('UPDATE "user" SET "banExpires" = now() - interval \'1 second\' WHERE id = %s', (uid,))
    assert _after_ban(uid) == {'cancelled_jobs': 0}  # expired


async def test_a_ban_the_web_never_reported_holds_no_credits(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'agenzie a Milano')
    _ban(db, uid)  # e.g. by SQL: no after-ban call, no stop request

    await _run(jid, [('', [ToolCall('t1', 'fake_emails', {})]), ('Fatto.', [])])

    job, _, held = _terminal(db, jid, sid)
    assert job['credits_spent'] == 0 and held == 0 and _balance(db, uid) == 50
    assert db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 0


@pytest.mark.parametrize('how', ['unban', 'expire'])
async def test_a_banned_owners_campaign_waits_and_resumes_without_touching_a_paused_one(db, provider, how):
    ada, bo, org, pid = _team(db)
    _mailbox(db, ada, org, pid, 'ada@agency.test')
    active = _campaign(db, bo, org, pid, emails=('anna@client.test',))
    paused = _campaign(db, bo, org, pid, emails=('bea@client.test',))
    db.execute("UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (paused,))
    _ban(db, bo)

    each_org(db, dispatcher.plan, NOW)
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_sends').fetchone()['n'] == 0
    _lift(db, bo, 'unban')
    each_org(db, dispatcher.plan, NOW)  # a claim made before the ban
    _ban(db, bo)
    assert (await each_org_async(db, dispatcher.send_due, NOW)).get('sent', 0) == 0 and provider.sent == []

    _lift(db, bo, how)
    assert (await each_org_async(db, dispatcher.send_due, NOW))['sent'] == 1
    assert [m['To'] for m, _ in provider.sent] == ['anna@client.test']
    status = {r['id']: r['status'] for r in db.execute('SELECT id::text, status FROM outreach_campaigns')}
    assert status == {active: 'active', paused: 'paused'}


@pytest.mark.parametrize('how', ['unban', 'expire'])
def test_a_banned_creators_scheduled_search_waits_and_resumes(db, how):
    user, search = setup(db)
    db.execute("UPDATE saved_searches SET next_run_at = now() - interval '1 hour' WHERE id = %s", (search['id'],))
    _ban(db, user)
    assert _schedule_due() == []
    assert db.execute('SELECT count(*) AS n FROM saved_search_runs').fetchone()['n'] == 0

    _lift(db, user, how)
    ((org, owner, run_id),) = _schedule_due()  # the missed run, once, queued as its creator
    assert owner == user

    _ban(db, user)  # queued, then banned before the worker claims it
    with acting_as(org, owner), pytest.raises(ActorGone):
        runner.claim(run_id)
    run = db.execute('SELECT status, error_code, reservation_id FROM saved_search_runs WHERE id = %s', (run_id,))
    assert run.fetchone() == {'status': 'cancelled', 'error_code': 'user_banned', 'reservation_id': None}
    assert db.execute('SELECT enabled FROM saved_searches WHERE id = %s', (search['id'],)).fetchone()['enabled']


@pytest.mark.parametrize('how', ['unban', 'expire'])
def test_a_banned_creators_webhook_receives_nothing_until_the_ban_ends(db, receiver, retries, how):
    uid = _user(db)
    org = member(db, uid).org_id
    endpoint = _endpoint(db, uid)
    campaign = db.execute(
        "INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name) VALUES (%s, %s, %s, 'C')"
        ' RETURNING id::text',
        (org, project(db, uid), uid),
    ).fetchone()['id']
    _ban(db, uid)

    assert call_for(org, _subscribed, 'campaign.finished', {'campaign_id': campaign}) == []
    assert call_for(org, webhooks.fire, org, endpoint, _payload()) is None  # a retry queued before the ban
    assert receiver.calls == []

    _lift(db, uid, how)
    assert call_for(org, _subscribed, 'campaign.finished', {'campaign_id': campaign}) == [(org, endpoint)]
    assert call_for(org, webhooks.fire, org, endpoint, _payload()) == 200


async def test_an_ai_column_fill_queued_before_the_ban_charges_nothing(db):
    """A plan's research fills, queued by the job the ban cancelled (``_fill_research`` → ``fill_plan_columns``),
    fail ``not_a_member`` when the worker claims them, holding nothing."""
    from psycopg.types.json import Jsonb

    from app.lists.ai_columns import fill_column, queue_fill
    from tests.test_ai_columns import FakeProvider, _list_with_rows

    uid = _user(db)
    lid = _list_with_rows(db, uid, ['Acme'])
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h', cap=10)
    _ban(db, uid)

    org = str(db.execute('SELECT organization_id FROM lists WHERE id = %s', (lid,)).fetchone()['organization_id'])
    with acting_as(org, uid), pytest.raises(ActorGone):  # the member fill_plan_columns queued it as
        await fill_column(lid, 'ai_h', provider=FakeProvider())

    fill = db.execute(
        "SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)
    ).fetchone()['f']
    assert (fill['status'], fill['error'], fill['credits']) == ('failed', 'not_a_member', 0)
    assert (
        db.execute("SELECT count(*) AS n FROM credit_transactions WHERE reference_type = 'ai_column'").fetchone()['n']
        == 0
    )
