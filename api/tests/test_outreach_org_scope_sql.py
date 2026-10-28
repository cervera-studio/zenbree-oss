"""Outreach acts inside one organization (PR 9 of the organization-scoping run), end to end against real SQL.

The campaign beat runs each organization as ``ForOrg`` of it, a reply is filed in the organization its match picked,
the LinkedIn webhook writes in the organization of the account, and HubSpot is read and written only as its owner,
a live member of the campaign's organization. The database decides: each test runs the code under the scope it
enters, with row-level security, and a probe trigger records the organization a write ran in (``zb.org()``).

Mailboxes, Unipile and HubSpot are fakes: nothing leaves the process.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app import worker
from app.core.db import get_conn
from app.core.scope import acting_for, system
from app.core.system_reasons import SystemReason
from app.crm import contacts, gate
from app.integrations import token_crypto
from app.linkedin import api as linkedin_api
from app.main import app
from app.notifications import service as notifications
from app.outreach import campaign_beat, crm_tasks, due, reply_targets
from app.outreach.domain.campaign import validate_steps
from tests.conftest import pool, project, scoped_conn, sender
from tests.test_agent import db as pool_db
from tests.test_agent import needs_db
from tests.test_creator_deleted_sql import _mailbox, _org, _team
from tests.test_crm_activities_sql import Timeline
from tests.test_crm_contacts_sql import WRITE
from tests.test_dispatcher_sql import NOW, ONE_STEP
from tests.test_dispatcher_sql import provider as provider
from tests.test_dispatcher_sql import switches as switches
from tests.test_holds_sql import person
from tests.test_replies_orgs_sql import World
from tests.test_replies_sql import _campaign as _reply_campaign
from tests.test_replies_sql import _post, _target
from tests.test_replies_sql import db as reply_db

pytestmark = needs_db
fixtures = (pool_db, reply_db, provider, switches)

TASK_STEP = [
    {'step': 1, 'channel': 'hubspot_task', 'subject': 'Idea', 'body': 'Ciao {{firstName}}', 'send_after_days': 0}
]


def _probe(c, table: str) -> None:
    """Every row written to ``table`` leaves the organization of the scope that wrote it in probe.orgs (NULL: a
    ``System`` scope, or none). Outside public, where the catalog checks of row-level security look."""
    c.execute('CREATE SCHEMA IF NOT EXISTS probe')
    c.execute('CREATE TABLE IF NOT EXISTS probe.orgs (tbl text, org uuid)')
    c.execute(
        'CREATE OR REPLACE FUNCTION probe.org() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS'
        ' $$ BEGIN INSERT INTO probe.orgs VALUES (TG_TABLE_NAME, zb.org()); RETURN NULL; END $$'
    )
    c.execute(f'CREATE TRIGGER probe_org AFTER INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION probe.org()')


def _probed(c, table: str) -> set:
    return {
        r['org'] and str(r['org']) for r in c.execute('SELECT org FROM probe.orgs WHERE tbl = %s', (table,)).fetchall()
    }


# ── replies and the LinkedIn webhook ─────────────────────────────────────────


@pytest.fixture
def world(reply_db, webhook_events):
    return World(reply_db)


def test_a_reply_matched_to_one_organization_stops_no_target_in_another(world, monkeypatch):
    """#414: whatever the match returns, the filing runs in the organization it picked, so another organization's
    targets do not change. The match is made to name both organizations' targets, the picked one first."""
    w = world
    _, b_project = w.client()
    own, theirs = w.campaign(age_days=1), w.campaign(b_project)
    own_project = project(w.c, w.uid)
    monkeypatch.setattr(
        reply_targets, 'scope_to_organization', lambda ts: sorted(ts, key=lambda t: str(t['project_id']) != own_project)
    )
    res = _post(TestClient(app), w.uid)
    assert res.status_code == 200 and res.json()['kind'] == 'reply'
    assert _target(w.c, own['target'])['status'] == 'replied'
    assert _target(w.c, theirs['target'])['status'] == 'active'
    filed = w.c.execute('SELECT project_id::text AS p FROM outreach_inbound_messages').fetchone()
    assert filed == {'p': own_project}


def test_an_unsubscribe_by_reply_stops_the_person_in_every_organization(world):
    """#460: an opt-out is not scoped to the organization the reply was filed in."""
    w = world
    _, b_project = w.client()
    own, theirs = w.campaign(age_days=2), w.campaign(b_project)
    res = _post(TestClient(app), w.uid, subject='unsubscribe', text='')
    assert res.json()['kind'] == 'unsubscribe' and res.json()['targets'] == 2
    assert [_target(w.c, t['target'])['status'] for t in (own, theirs)] == ['replied', 'replied']
    assert w.excluded_in() == {project(w.c, w.uid), b_project}


@pytest.mark.parametrize(
    ('subject', 'kind'), [('Out of office: Re: Ciao', 'auto_reply'), ('unsubscribe', 'unsubscribe')]
)
def test_a_duplicate_delivery_changes_no_organization(world, subject, kind):
    """A webhook retry is a no-op: it must not stop or pause again a person that someone resumed in the other
    organization between the two deliveries."""
    w = world
    _, b_project = w.client()
    own, theirs = w.campaign(age_days=3), w.campaign(b_project, age_days=1)
    client = TestClient(app)
    first = _post(client, w.uid, subject=subject, text='Sono via', email_id='dup-1')
    assert first.json()['kind'] == kind
    w.c.execute(
        "UPDATE outreach_campaign_targets SET status = 'active', paused_until = NULL, replied_at = NULL WHERE id = %s",
        (own['target'],),
    )
    again = _post(client, w.uid, subject=subject, text='Sono via', email_id='dup-1')
    assert again.json() == {'status': 'duplicate'}
    assert _target(w.c, own['target'])['status'] == 'active'
    assert _target(w.c, theirs['target'])['status'] == ('paused' if kind == 'auto_reply' else 'replied')


def _linkedin_world(w) -> tuple[dict, dict]:
    """The owner's LinkedIn account serves their own project; a client organization writes to the same person."""
    _, b_project = w.client()
    url = 'https://www.linkedin.com/in/anna/'
    own = _reply_campaign(w.c, w.uid, email=None, linkedin_url=url, provider_id='ACo1')
    theirs = _reply_campaign(w.c, w.uid, email=None, linkedin_url=url, provider_id='ACo1', client=b_project)
    sender(w.c, w.uid, 'unipile', provider_account_id='acc-1')
    _probe(w.c, 'outreach_campaign_targets')
    _probe(w.c, 'outreach_inbound_messages')
    return own, theirs


def _unipile(payload: dict) -> dict:
    res = TestClient(app).post(
        '/v1/integrations/linkedin/webhook', json=payload, headers={'Unipile-Auth': 'uni-secret'}
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_a_linkedin_reply_is_filed_in_the_organization_of_the_account(world):
    w = world
    own, theirs = _linkedin_world(w)
    done = _unipile(
        {
            'event': 'message_received',
            'account_id': 'acc-1',
            'account_info': {'user_id': 'ME'},
            'message_id': 'dm-1',
            'message': 'Ciao, parliamone',
            'sender': {'attendee_provider_id': 'ACo1', 'attendee_profile_url': 'https://www.linkedin.com/in/anna/'},
        }
    )
    assert done['kind'] == 'reply' and done['targets'] == 1
    assert [_target(w.c, t['target'])['status'] for t in (own, theirs)] == ['replied', 'active']
    assert _probed(w.c, 'outreach_inbound_messages') == _probed(w.c, 'outreach_campaign_targets') == {w.own}


def test_a_linkedin_acceptance_is_dated_in_the_organization_of_the_account(world, monkeypatch):
    w = world
    monkeypatch.setattr(linkedin_api.webhook_events, 'get_conn', scoped_conn(w.c))
    own, _ = _linkedin_world(w)
    client_org = w.c.execute(
        'SELECT "organizationId"::text AS org FROM member WHERE "userId" = %s AND "organizationId" <> %s',
        (w.uid, w.own),
    ).fetchone()['org']
    w.c.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, %s, 'agent_question', '/')",
        (w.uid, client_org),
    )
    pushed: list[int] = []
    monkeypatch.setattr(notifications, 'publish', lambda user, event, data: pushed.append(data['unread']))
    monkeypatch.setattr(notifications.push, 'send', lambda *a, **k: None)
    monkeypatch.setattr(notifications, 'send_email', lambda *a, **k: False)
    done = _unipile({'event': 'new_relation', 'account_id': 'acc-1', 'user_provider_id': 'ACo1'})
    assert done == {'handled': True, 'event': 'new_relation', 'targets': 1}
    accepted = w.c.execute('SELECT id FROM outreach_campaign_targets WHERE linkedin_accepted_at IS NOT NULL').fetchall()
    assert [r['id'] for r in accepted] == [own['target']]
    assert _probed(w.c, 'outreach_campaign_targets') == {w.own}
    # The push carries the bell's unread count, every organization's: the client's notification counts too.
    assert pushed == [2]


# ── the campaign beat and HubSpot ────────────────────────────────────────────


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
def beat(pool_db, provider, switches, http, vendor_db, monkeypatch):
    """The beat as the worker runs it, at ``NOW``, on the real pool: HubSpot is ``http.handler``, the mailbox and
    the token endpoint ``provider``, and a gate retry is recorded, not queued."""
    monkeypatch.setattr(campaign_beat, 'datetime', _Frozen)
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(gate, '_redis', lambda: redis)
    monkeypatch.setattr(gate, 'is_disabled', lambda key: False)
    monkeypatch.setattr(crm_tasks, 'is_disabled', lambda key: False)
    monkeypatch.setattr(contacts, 'is_disabled', lambda key: False)
    monkeypatch.setattr('app.notifications.service.deliver', lambda notified: None)
    retries: list = []
    monkeypatch.setattr(worker.retry_hubspot_read, 'apply_async', lambda args, countdown: retries.append(args))
    http.handler = Timeline()
    http.retries = retries
    return http


def _hubspot(c, uid: str, org: str, *, label: str) -> str:
    """A connected HubSpot grant of ``uid`` stored in ``org``, with write-back on since a day before ``NOW``."""
    return str(
        c.execute(
            'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,'
            ' granted_scopes, status, provider_account_id, provider_owner_id, crm_write_back, crm_write_back_since)'
            " VALUES (%s, %s, 'hubspot', %s, %s, 'connected', %s, '77', true, %s) RETURNING id",
            (org, uid, token_crypto.encrypt('refresh-1').value, WRITE, label, NOW - timedelta(days=1)),
        ).fetchone()['id']
    )


def _campaign(c, owner: str, org: str, pid: str, steps, emails: tuple[str, ...]) -> str:
    cid = c.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,
               activated_at, approve_before_sending)
           VALUES (%s, %s, %s, 'C', 'active', %s, %s, false) RETURNING id""",
        (org, pid, owner, json.dumps(validate_steps(steps)), NOW - timedelta(hours=1)),
    ).fetchone()['id']
    pool(c, cid, ('google',))
    for addr in emails:
        c.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status)'
            " VALUES (%s, %s, %s, 'Anna', 'Client', 'active')",
            (cid, owner, addr),
        )
    return str(cid)


def _remove(c, uid: str, org: str) -> None:
    c.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (uid, org))


def test_a_removed_owners_campaign_keeps_sending_as_its_organization(pool_db, beat, provider):
    """#333: the campaign is the organization's. Ada created it and leaves; Bo's mailbox keeps sending it, and every
    send is written as ``ForOrg`` of the organization."""
    ada, bo, org, pid = _team(pool_db)
    _mailbox(pool_db, bo, org, pid, 'bo@agency.test')
    cid = _campaign(pool_db, ada, org, pid, ONE_STEP, ('anna@client.test',))
    _probe(pool_db, 'outreach_campaign_sends')
    _remove(pool_db, ada, org)

    worker.dispatch_campaign_sends()

    sends = pool_db.execute('SELECT status FROM outreach_campaign_sends WHERE campaign_id = %s', (cid,)).fetchall()
    assert sends == [{'status': 'sent'}] and len(provider.sent) == 1
    assert _probed(pool_db, 'outreach_campaign_sends') == {org}


def _tasks_by_email(portal: Timeline) -> set[str]:
    return {portal.contacts[t['contact']]['email'] for t in portal.logged if t['kind'] == 'tasks'}


def test_a_removed_campaign_owner_gets_no_hubspot_task_and_a_live_owner_does(pool_db, beat):
    """Bo's grant is stored in his own organization, so his removal from Ada's leaves it connected: the task step of
    his campaign there must still stop, as the task is written with his grant."""
    ada, bo, org, pid = _team(pool_db)
    _hubspot(pool_db, ada, org, label='portal-ada')
    _hubspot(pool_db, bo, _org(pool_db, bo), label='portal-bo')
    _campaign(pool_db, ada, org, pid, TASK_STEP, ('anna@acme.it',))
    _campaign(pool_db, bo, org, pid, TASK_STEP, ('bea@acme.it',))
    # Read clear and claimed while Bo was a member, as the beat does: the delivery is what his removal must stop.
    with acting_for(org, 'test claims'):
        asyncio.run(campaign_beat._gate(org, NOW))
        campaign_beat.dispatcher.plan(NOW)
    _remove(pool_db, bo, org)

    worker.dispatch_campaign_sends()

    assert _tasks_by_email(beat.handler) == {'anna@acme.it'}


@pytest.mark.parametrize('grant_in', ['his own organization', 'the campaign organization'])
def test_a_stored_hubspot_opt_out_still_excludes_after_its_owner_leaves(pool_db, beat, provider, grant_in):
    """An opt-out is the person's wish, not the owner's sales choice: a verdict the gate stored for Bo's campaign and
    the beat has not applied yet still stops the person once Bo leaves, wherever his grant is stored."""
    ada, bo, org, pid = _team(pool_db)
    _mailbox(pool_db, ada, org, pid, 'ada@agency.test')
    own = grant_in == 'his own organization'
    grant = _hubspot(pool_db, bo, _org(pool_db, bo) if own else org, label='portal-bo')
    cid = _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    pool_db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, 'bea@acme.it', %s, %s)",
        (bo, gate.OPTED_OUT_REASON, NOW),
    )
    _remove(pool_db, bo, org)
    status = pool_db.execute('SELECT status FROM user_oauth_connections WHERE id = %s', (grant,)).fetchone()
    assert status == {'status': 'connected' if own else 'disconnected'}  # the removal releases a grant stored there

    worker.dispatch_campaign_sends()

    assert provider.sent == []
    assert pool_db.execute('SELECT 1 FROM outreach_campaign_sends WHERE campaign_id = %s', (cid,)).fetchall() == []
    target = pool_db.execute('SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,))
    assert target.fetchone() == {'status': 'excluded'}


def _departed_opt_outs() -> int:
    with system(SystemReason.OPT_OUT), get_conn() as conn:
        return due.exclude_opted_out_of_departed_owners(conn)


def _statuses(c, cid: str) -> list[str]:
    rows = c.execute('SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s ORDER BY email', (cid,))
    return [r['status'] for r in rows]


def test_a_departed_owners_opt_out_excludes_only_their_open_targets_in_the_organization_they_left(pool_db):
    """Per (owner, organization): Bo leaves Ada's organization and stays in his own. His stored opt-out excludes his
    open targets in Ada's organization only. A customer verdict holds nothing after he leaves, a replied target keeps
    its status, a colleague's campaign for the same person is not his, and a second run changes nothing. While Bo is
    a live member nothing changes."""
    ada, bo, org, pid = _team(pool_db)
    own = _org(pool_db, bo)
    own_pid = project(pool_db, bo)
    in_team = _campaign(pool_db, bo, org, pid, ONE_STEP, (' Bea@Acme.IT ', 'zed@acme.it'))
    replied = _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    pool_db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE campaign_id = %s", (replied,))
    in_own = _campaign(pool_db, bo, own, own_pid, ONE_STEP, ('bea@acme.it',))
    adas = _campaign(pool_db, ada, org, pid, ONE_STEP, ('bea@acme.it',))
    pool_db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, 'bea@acme.it', %s, %s),"
        " (%s, 'zed@acme.it', 'HUBSPOT_CUSTOMER', %s)",
        (bo, gate.OPTED_OUT_REASON, NOW, bo, NOW),
    )

    assert _departed_opt_outs() == 0  # a live owner's targets: the organization's own beat applies the opt-out
    assert _statuses(pool_db, in_team) == ['active', 'active']
    _remove(pool_db, bo, org)
    assert _departed_opt_outs() == 1
    assert _statuses(pool_db, in_team) == ['excluded', 'active']  # ' Bea@Acme.IT ' sorts first
    assert (_statuses(pool_db, replied), _statuses(pool_db, in_own)) == (['replied'], ['active'])
    assert _statuses(pool_db, adas) == ['active']
    assert _departed_opt_outs() == 0


def _people(c, cid: str) -> dict[str, str]:
    """Each target's address, or else its LinkedIn URL, and its status."""
    rows = c.execute('SELECT email, linkedin_url, status FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,))
    return {r['email'] or r['linkedin_url']: r['status'] for r in rows}


def test_a_departed_owners_opt_out_follows_the_person_aliases_in_their_client(pool_db):
    """#1121: Bo's stored opt-out names bea@. The person who holds it in the client also holds b.rossi@ and a
    LinkedIn profile: those targets leave too once Bo departs. Another client of the organization does not know the
    link, so there only bea@ itself leaves."""
    ada, bo, org, pid = _team(pool_db)
    other = pool_db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other client') RETURNING id",
        (org, ada),
    ).fetchone()['id']
    person(
        pool_db, pid, ('email', 'bea@acme.it'), ('email', 'b.rossi@acme.it'), ('linkedin_url', 'linkedin.com/in/bea')
    )
    reached = _campaign(pool_db, bo, org, pid, ONE_STEP, ('B.Rossi@acme.it', 'zed@acme.it'))
    elsewhere = _campaign(pool_db, bo, org, str(other), ONE_STEP, ('bea@acme.it', 'b.rossi@acme.it'))
    for cid in (reached, elsewhere):
        pool_db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, linkedin_url, first_name, status)'
            " VALUES (%s, %s, 'https://linkedin.com/in/bea/', 'Bea', 'active')",
            (cid, bo),
        )
    pool_db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, 'bea@acme.it', %s, %s)",
        (bo, gate.OPTED_OUT_REASON, NOW),
    )
    _remove(pool_db, bo, org)

    assert _departed_opt_outs() == 3
    assert _people(pool_db, reached) == {
        'B.Rossi@acme.it': 'excluded',
        'zed@acme.it': 'active',
        'https://linkedin.com/in/bea/': 'excluded',
    }
    assert _people(pool_db, elsewhere) == {
        'bea@acme.it': 'excluded',
        'b.rossi@acme.it': 'active',
        'https://linkedin.com/in/bea/': 'active',
    }
    assert _departed_opt_outs() == 0


def _pause(c, cid: str) -> None:
    c.execute(
        "UPDATE outreach_campaign_targets SET status = 'paused', paused_until = %s WHERE campaign_id = %s",
        (NOW + timedelta(days=3), cid),
    )


def _pause_dates(c, cid: str) -> list:
    rows = c.execute('SELECT paused_until FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchall()
    return [r['paused_until'] for r in rows]


def test_a_departed_owners_paused_target_with_an_opt_out_is_excluded_and_loses_its_pause_date(pool_db):
    """The check ``(status = 'paused') = (paused_until IS NOT NULL)`` refuses an excluded row that keeps its pause
    date: the write failed, and the beat with it, every minute."""
    _, bo, org, pid = _team(pool_db)
    cid = _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    _pause(pool_db, cid)
    pool_db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, 'bea@acme.it', %s, %s)",
        (bo, gate.OPTED_OUT_REASON, NOW),
    )
    _remove(pool_db, bo, org)

    assert _departed_opt_outs() == 1
    assert (_statuses(pool_db, cid), _pause_dates(pool_db, cid)) == (['excluded'], [None])


def test_a_live_owners_paused_target_with_an_opt_out_is_excluded_and_loses_its_pause_date(pool_db, beat):
    _, bo, org, pid = _team(pool_db)
    _hubspot(pool_db, bo, _org(pool_db, bo), label='portal-bo')
    cid = _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    _pause(pool_db, cid)
    pool_db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, 'bea@acme.it', %s, %s)",
        (bo, gate.OPTED_OUT_REASON, NOW),
    )

    with acting_for(org, 'test opt-out'), get_conn() as conn:
        assert due.exclude_opted_out(conn) == 1
    assert (_statuses(pool_db, cid), _pause_dates(pool_db, cid)) == (['excluded'], [None])


def test_the_opted_out_index_names_the_reason_the_gate_stores(pool_db):
    """crm_gate_checks_opted_out_idx serves exclude_opted_out_of_departed_owners only while its predicate names the
    reason the gate writes. A renamed reason on either side leaves the beat reading every verdict, every minute."""
    predicate = pool_db.execute(
        "SELECT pg_get_expr(indpred, indrelid) AS p FROM pg_index WHERE indexrelid = 'crm_gate_checks_opted_out_idx'::regclass"
    ).fetchone()['p']
    assert predicate == f"(reason = '{gate.OPTED_OUT_REASON}'::text)"


def test_a_failing_departed_owner_write_does_not_stop_the_other_organizations(pool_db, monkeypatch):
    def broken(conn):
        raise RuntimeError('the write failed')

    ran = []

    async def each_org(orgs, label, unit):
        ran.append(label)
        return []

    monkeypatch.setattr(due, 'exclude_opted_out_of_departed_owners', broken)
    monkeypatch.setattr(campaign_beat.tasks, 'each_org', each_org)
    with capture_logs() as logs, system(SystemReason.DUE_WORK):  # as app/worker.py dispatch_campaign_sends
        campaign_beat.run()

    assert ran == ['campaign beat']
    assert [e['event'] for e in logs if e['log_level'] == 'error'] == ['campaign.dispatch.departed_opt_outs_failed']


def test_a_failed_beat_write_logs_the_constraint_it_broke_never_the_message(pool_db, monkeypatch):
    """The error line names the constraint, which tells which rule the data broke; the message quotes the row."""

    def duplicate(conn):
        conn.execute('CREATE TEMP TABLE sent (email text CONSTRAINT one_mail_per_person UNIQUE)')
        conn.execute("INSERT INTO sent VALUES ('bea@acme.it'), ('bea@acme.it')")

    async def each_org(orgs, label, unit):
        return []

    monkeypatch.setattr(due, 'exclude_opted_out_of_departed_owners', duplicate)
    monkeypatch.setattr(campaign_beat.tasks, 'each_org', each_org)
    with capture_logs() as logs, system(SystemReason.DUE_WORK):
        campaign_beat.run()

    (line,) = [e for e in logs if e['log_level'] == 'error']
    assert (line['error_type'], line['constraint_name']) == ('UniqueViolation', 'one_mail_per_person')
    assert 'bea@acme.it' not in json.dumps(logs)


def test_a_banned_owners_opt_out_excludes_their_targets(pool_db):
    """A banned owner is not a live member: their campaign sends nothing, and the person stays excluded after an
    unban."""
    _, bo, org, pid = _team(pool_db)
    cid = _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    pool_db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, 'bea@acme.it', %s, %s)",
        (bo, gate.OPTED_OUT_REASON, NOW),
    )
    pool_db.execute('UPDATE "user" SET banned = true WHERE id = %s', (bo,))
    assert _departed_opt_outs() == 1
    assert _statuses(pool_db, cid) == ['excluded']


def test_a_gate_retry_reads_as_the_owner_in_the_campaign_organization_until_they_leave(pool_db, beat):
    """A read that failed in the beat is retried by a task that names the owner and the campaign's organization, and
    reads as that member: once they left, it reads nothing."""
    _, bo, org, pid = _team(pool_db)
    _hubspot(pool_db, bo, _org(pool_db, bo), label='portal-bo')
    _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    portal = beat.handler
    beat.handler = lambda request: httpx.Response(429, headers={'Retry-After': '60'})

    worker.dispatch_campaign_sends()

    assert beat.retries == [(bo, org)]
    beat.handler = portal
    assert worker.retry_hubspot_read.apply(args=beat.retries[0]).get() == {'checked': 1}
    calls = len(beat.calls)
    _remove(pool_db, bo, org)
    assert worker.retry_hubspot_read.apply(args=beat.retries[0]).get() == {}
    assert len(beat.calls) == calls


def test_a_refused_grant_stored_in_another_organization_is_marked_by_its_owner(pool_db, beat):
    """The gate writes the owner's grant as the owner. Bo's grant is stored in his own organization and read for a
    campaign of Ada's: as ``ForOrg`` of Ada's organization the error would change no row, with no error."""
    _, bo, org, pid = _team(pool_db)
    grant = _hubspot(pool_db, bo, _org(pool_db, bo), label='portal-bo')
    _campaign(pool_db, bo, org, pid, ONE_STEP, ('bea@acme.it',))
    beat.handler = lambda request: httpx.Response(401)

    worker.dispatch_campaign_sends()

    row = pool_db.execute('SELECT status, status_detail FROM user_oauth_connections WHERE id = %s', (grant,))
    assert row.fetchone() == {'status': 'error', 'status_detail': 'HUBSPOT.AUTH'}


def test_concurrent_beats_each_see_only_their_own_organization(pool_db, beat, monkeypatch):
    """Both organizations' beats are inside their scopes while each reads: a scope one of them entered never
    reaches the other at an ``await``."""
    ada, _, org_a, pid_a = _team(pool_db)
    cy, _, org_c, pid_c = _team(pool_db)
    _campaign(pool_db, ada, org_a, pid_a, ONE_STEP, ('anna@client.test',))
    _campaign(pool_db, cy, org_c, pid_c, ONE_STEP, ('anna@client.test',))
    both = asyncio.Barrier(2)
    seen: dict[str, dict] = {}

    async def phases(org_id: str, now: datetime) -> dict:
        await both.wait()  # every unit entered its scope
        with get_conn() as conn:
            seen[org_id] = conn.execute(
                'SELECT zb.org()::text AS org, array(SELECT organization_id::text FROM outreach_campaigns) AS orgs'
            ).fetchone()
        await both.wait()  # and none has left it before every unit read
        return {}

    monkeypatch.setattr(campaign_beat, '_phases', phases)

    worker.dispatch_campaign_sends()

    assert seen == {org_a: {'org': org_a, 'orgs': [org_a]}, org_c: {'org': org_c, 'orgs': [org_c]}}


def _user_with_id(c, uid: str) -> str:
    now = datetime.now(UTC)
    c.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt") VALUES (%s, %s, %s, false,'
        ' %s, %s)',
        (uid, 'T', f'{uid}@x.test', now, now),
    )
    return uid


def test_a_removed_owners_contacts_do_not_fill_the_sweep(pool_db, beat, monkeypatch):
    """The sweep takes a batch of people, each written as the owner in the send's organization. A removed owner's
    people are not taken: one batch of them would otherwise hide a live owner's person on every sweep."""
    monkeypatch.setattr(contacts, 'BATCH', 1)
    ada, _, org, pid = _team(pool_db)
    gone = _user_with_id(pool_db, str(uuid.UUID(int=1)))  # first in the sweep's order (by owner)
    # In their own organization, made before they join Ada's: the removal leaves this grant connected.
    _hubspot(pool_db, gone, _org(pool_db, gone), label='portal-gone')
    _hubspot(pool_db, ada, org, label='portal-ada')
    pool_db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, gone),
    )
    for uid, addr in ((ada, 'anna@acme.it'), (gone, 'bea@acme.it')):
        cid = _campaign(pool_db, uid, org, pid, ONE_STEP, (addr,))
        pool_db.execute(
            """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
                   subject, body, idempotency_key, delivered_at)
               SELECT t.campaign_id, t.owner_id, t.id, 1, 'email', 'sent', t.email, 's', 'b', t.id::text, %s
               FROM outreach_campaign_targets t WHERE t.campaign_id = %s""",
            (NOW, cid),
        )
    _remove(pool_db, gone, org)

    worker.sync_hubspot_contacts()

    written = pool_db.execute('SELECT owner_id::text, email FROM crm_contact_writes').fetchall()
    assert written == [{'owner_id': ada, 'email': 'anna@acme.it'}]
