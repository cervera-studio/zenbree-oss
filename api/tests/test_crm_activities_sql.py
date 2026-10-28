"""HubSpot timeline write-back (#528) against real SQL; HubSpot is the in-memory portal of the contacts test,
which here also keeps activities and moves "Last contacted" as HubSpot does.

Needs TEST_DATABASE_URL.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.crm import activities, contacts, gate, write_back
from app.outreach import dispatcher
from tests.conftest import call_as, each_org_async, member, project, scoped_conn
from tests.test_crm_contacts_sql import OBJECTS, Portal, _connect, _sent
from tests.test_crm_gate_sql import Tokens, _beat
from tests.test_crm_gate_sql import gated as gated
from tests.test_dispatcher_sql import NOW, TWO_STEPS, _campaign, _mailbox
from tests.test_dispatcher_sql import db as db  # the dispatcher's throwaway-schema fixture
from tests.test_dispatcher_sql import provider as provider  # fake mailbox + token refresh
from tests.test_dispatcher_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL
from tests.test_dispatcher_sql import switches as switches
from tests.test_tools_sql import _user

CONTACT = {'emails': 198, 'communications': 81, 'tasks': 204}
COMPANY = {'emails': 186, 'communications': 87, 'tasks': 192}


def _iso(ms: str) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()


class Timeline(Portal):
    """The portal with activities: ``logged`` is every email and LinkedIn message put on a timeline."""

    def __init__(self):
        super().__init__()
        self.logged: list[dict] = []
        self.lose_answer = False  # the next create is stored, but its answer never arrives

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        kind = path.removeprefix(f'{OBJECTS}/').split('/')[0]
        if path == f'{OBJECTS}/companies/batch/read':
            wanted = {i['id'] for i in json.loads(request.content)['inputs']}
            found = [{'id': i, 'properties': p} for i, p in self.companies.items() if i in wanted]
            return httpx.Response(200, json={'results': found})
        if kind not in CONTACT or (method, path) in self.fail:
            return super().__call__(request)
        self.calls.append((method, path))
        body = json.loads(request.content)
        if method != 'POST' or path != f'{OBJECTS}/{kind}':  # the grant cannot read or search emails
            return httpx.Response(403, json={'category': 'MISSING_SCOPES'})
        to = {t['types'][0]['associationTypeId']: t['to']['id'] for t in body['associations']}
        if to[CONTACT[kind]] not in self.contacts:
            return httpx.Response(400, json={'category': 'VALIDATION_ERROR'})
        props = body['properties']
        self.logged.append(
            {'id': self._id(), 'kind': kind, 'contact': to[CONTACT[kind]], 'company': to.get(COMPANY[kind]), **props}
        )
        # HubSpot: a logged one-to-one email or LinkedIn message is a contact; an incoming email is a reply.
        trace = (
            'hs_sales_email_last_replied'
            if props.get('hs_email_direction') == 'INCOMING_EMAIL'
            else 'notes_last_contacted'
        )
        if kind != 'tasks':  # a task is work to do, not a contact made
            self.contacts[to[CONTACT[kind]]][trace] = _iso(props['hs_timestamp'])
        if self.lose_answer:
            self.lose_answer = False
            raise httpx.ReadTimeout('no answer')
        return httpx.Response(201, json={'id': self.logged[-1]['id']})


@pytest.fixture
def portal(db, http, monkeypatch, vendor_db):
    for module in (activities, contacts, write_back):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    for module in (activities, contacts):
        monkeypatch.setattr(module, 'is_disabled', lambda key: False)
    http.handler = Timeline()
    return http.handler


async def _contacts_sweep() -> None:
    with system(SystemReason.HUBSPOT_WEBHOOK):  # as app/worker.py sync_hubspot_contacts
        await contacts.sweep(NOW, Tokens())


async def _sweep(tokens=None):
    """One run of the beat task: contacts first, then their activities."""
    with system(SystemReason.HUBSPOT_WEBHOOK):
        await contacts.sweep(NOW, tokens or Tokens())
        return dict(await activities.sweep(tokens or Tokens()))


def _later(db):
    """Two minutes on: a failed activity is taken again."""
    db.execute("UPDATE crm_activity_writes SET attempted_at = attempted_at - interval '2 minutes'")


def _reply(db, uid, email, *, kind='reply', channel='email', at=NOW, subject='Re: s', body='Volentieri', name=''):
    target = db.execute(
        'SELECT id, campaign_id FROM outreach_campaign_targets WHERE owner_id = %s AND email = %s', (uid, email)
    ).fetchone()
    db.execute(
        """INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id,
               from_address, from_name, subject, body, target_id, campaign_id, received_at)
           VALUES (%s, %s, %s, %s, gen_random_uuid()::text, %s, %s, %s, %s, %s, %s, %s)""",
        (
            uid,
            project(db, uid),
            channel,
            kind,
            email if channel == 'email' else '',
            name,
            subject,
            body,
            *target.values(),
            at,
        ),
    )


async def test_a_send_and_its_reply_are_on_the_contacts_timeline_once(db, portal):
    uid = _user(db)
    _connect(db, uid)
    db.execute("UPDATE user_oauth_connections SET provider_owner_id = '77' WHERE user_id = %s", (uid,))
    _sent(db, uid, 'anna@acme.it')
    db.execute("UPDATE outreach_campaigns SET name = 'Autunno'")
    _reply(db, uid, 'anna@acme.it', at=NOW + timedelta(hours=2))

    assert await _sweep() == {'sent': 1, 'replies': 1}
    sent, reply = portal.logged
    contact_id = next(iter(portal.contacts))
    assert sent == {
        'id': sent['id'],
        'kind': 'emails',
        'contact': contact_id,
        'company': next(iter(portal.companies)),
        'hs_timestamp': str(int(NOW.timestamp() * 1000)),
        'hubspot_owner_id': '77',
        'hs_email_direction': 'EMAIL',
        'hs_email_status': 'SENT',
        'hs_email_subject': 's',
        'hs_email_text': 'b\n\n— Zenbree · Autunno',
        'hs_email_headers': json.dumps({'to': [{'email': 'anna@acme.it'}]}),
    }
    assert (reply['hs_email_direction'], reply['hs_email_subject']) == ('INCOMING_EMAIL', 'Re: s')
    assert reply['hs_email_text'] == 'Volentieri\n\n— Zenbree · Autunno'
    assert reply['hs_timestamp'] == str(int((NOW + timedelta(hours=2)).timestamp() * 1000))

    portal.calls.clear()
    assert await _sweep() == {} and portal.calls == []  # logged once: the next sweep asks HubSpot nothing


async def test_a_touch_read_before_a_portal_change_is_not_logged_in_the_new_portal(db, portal, monkeypatch):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    await _contacts_sweep()
    real_due = activities._due

    def due_then_switch():
        rows = real_due()
        db.execute("UPDATE user_oauth_connections SET provider_account_id = '9999'")  # reconnected to portal B
        return rows

    monkeypatch.setattr(activities, '_due', due_then_switch)
    with system(SystemReason.HUBSPOT_WEBHOOK):
        assert dict(await activities.sweep(Tokens())) == {}
    assert portal.logged == [] and db.execute('SELECT count(*) AS n FROM crm_activity_writes').fetchone()['n'] == 0


async def test_an_unsubscribe_says_so_and_an_automatic_reply_is_not_logged(db, portal):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    _reply(db, uid, 'anna@acme.it', kind='unsubscribe', body='Basta grazie')
    _reply(db, uid, 'anna@acme.it', kind='auto_reply', body='Sono in ferie')

    assert await _sweep() == {'sent': 1, 'replies': 1}
    assert portal.logged[1]['hs_email_text'] == 'Basta grazie\n\n— Zenbree · C · unsubscribed'


async def test_a_linkedin_message_and_its_answer_are_linkedin_messages(db, portal):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    db.execute("UPDATE outreach_campaign_sends SET channel = 'linkedin_message', subject = '', body = 'Ciao Anna'")
    _reply(db, uid, 'anna@acme.it', channel='linkedin', subject='', body='Ciao!', name='Anna Neri')

    assert await _sweep() == {'sent': 1, 'replies': 1}
    sent, reply = portal.logged
    assert sent['kind'] == reply['kind'] == 'communications'
    assert sent['hs_communication_channel_type'] == 'LINKEDIN_MESSAGE' and sent['hs_communication_logged_from'] == 'CRM'
    assert sent['hs_communication_body'] == 'Ciao Anna\n\n— Zenbree · C'
    assert reply['hs_communication_body'] == 'Anna Neri:\nCiao!\n\n— Zenbree · C'


async def test_a_lost_answer_does_not_put_the_send_on_the_timeline_twice(db, portal):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')

    portal.lose_answer = True
    assert await _sweep() == {'failed': 1}
    assert len(portal.logged) == 1  # HubSpot has it; we do not know
    # Not asked again at once: the contact shows a new activity seconds later. Her answer waits behind it, or
    # its own time on the contact would hide the send's.
    _reply(db, uid, 'anna@acme.it', channel='linkedin', at=NOW + timedelta(minutes=30))
    assert await _sweep() == {}
    _later(db)
    assert await _sweep() == {'sent': 1, 'replies': 1}
    assert len(portal.logged) == 2
    lost = db.execute("SELECT activity_id FROM crm_activity_writes WHERE kind = 'send'").fetchone()
    assert lost['activity_id'] == activities.ID_LOST

    # The same for a reply, which leaves its trace on another property; one that never arrived is logged.
    _reply(db, uid, 'anna@acme.it', at=NOW + timedelta(hours=1))
    portal.lose_answer = True
    assert await _sweep() == {'failed': 1}
    _sent(db, uid, 'anna@acme.it', at=NOW + timedelta(hours=2))
    portal.fail[('POST', f'{OBJECTS}/emails')] = httpx.Response(503)
    _later(db)
    assert await _sweep() == {'replies': 1, 'failed': 1}
    _later(db)
    assert await _sweep() == {'sent': 1}
    assert len(portal.logged) == 4


async def test_a_refused_activity_is_given_up_and_a_portal_in_trouble_is_retried(db, portal):
    uid = _user(db)
    _connect(db, uid)
    _sent(db, uid, 'anna@acme.it')
    await _contacts_sweep()

    # HubSpot is down: nothing is counted against the activity, however long it lasts.
    for _ in range(activities.MAX_ATTEMPTS + 1):
        portal.fail[('POST', f'{OBJECTS}/emails')] = httpx.Response(503)
        assert await _sweep() == {'failed': 1}
        _later(db)
    assert await _sweep() == {'sent': 1}

    # The contact was deleted in HubSpot: its next activity is refused, then left alone.
    portal.contacts.clear()
    _sent(db, uid, 'anna@acme.it', at=NOW + timedelta(hours=1))
    for _ in range(activities.MAX_ATTEMPTS):
        assert await _sweep() == {'refused': 1}
        _later(db)
    portal.calls.clear()
    assert await _sweep() == {} and portal.calls == []


async def test_nothing_is_logged_without_the_switch_a_contact_or_for_earlier_sends(db, portal):
    off, on = _user(db), _user(db)
    _connect(db, off, on=False)
    _sent(db, off, 'anna@acme.it')
    _connect(db, on)
    _sent(db, on, 'bea@acme.it', at=NOW - timedelta(days=2))  # before the switch went on
    _sent(db, on, 'cleo@acme.it', status='failed')

    assert await _sweep() == {} and portal.logged == []

    # The switch goes off again: what was not logged yet stays out.
    _sent(db, on, 'dora@acme.it')
    await _contacts_sweep()
    assert call_as(member(db, on), write_back.set_enabled, on, False) == 'ok'
    assert await _sweep() == {} and portal.logged == []


async def test_step_two_goes_out_after_zenbree_logged_step_one_but_not_after_someone_elses_call(
    db, portal, gated, provider, switches
):
    """Done when (#528): logging our own send moves HubSpot's "Last contacted", and the gate must not read that as
    a cooldown, or every campaign of a connected user would stop after its first step."""
    uid = _user(db)
    _connect(db, uid)
    _mailbox(db, uid)
    _campaign(db, uid, TWO_STEPS, emails=('anna@acme.it', 'bea@acme.it'))

    async def beat(now):
        await _beat(now)
        sent = 0
        for minutes in range(0, 120, 20):  # the mailbox paces its sends
            sent += (await each_org_async(db, dispatcher.send_due, now + timedelta(minutes=minutes)))['sent']
        return sent

    assert await beat(NOW) == 2
    assert await _sweep() == {'sent': 2}
    anna, bea = (portal.by_email(e) for e in ('anna@acme.it', 'bea@acme.it'))
    assert anna['notes_last_contacted'] and bea['notes_last_contacted']  # HubSpot counts our emails as contact

    bea['notes_last_contacted'] = (NOW + timedelta(days=1)).isoformat()  # a colleague calls bea the day after

    later = NOW + timedelta(days=3, hours=3)
    assert await beat(later) == 1
    steps = db.execute('SELECT recipient, step FROM outreach_campaign_sends ORDER BY step, recipient').fetchall()
    assert [(s['recipient'], s['step']) for s in steps] == [
        ('anna@acme.it', 1),
        ('bea@acme.it', 1),
        ('anna@acme.it', 2),
    ]
    held = db.execute("SELECT reason FROM crm_gate_checks WHERE email = 'bea@acme.it'").fetchone()
    assert held['reason'] == gate.COOLDOWN_REASON
