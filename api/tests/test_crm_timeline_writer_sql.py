"""The writer of the HubSpot timeline ledger (#932) against real SQL; HubSpot is the in-memory portal of the #528
test, with notes. Needs TEST_DATABASE_URL."""

import json
from datetime import timedelta
from time import monotonic

import httpx
import pytest

from app.core.errors import err
from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.crm import activities, contacts, hubspot_client, timeline
from app.crm.hubspot_activity import refused
from app.crm.timeline import facts, ledger, writer
from tests.test_crm_activities_sql import Timeline, _reply, _sent
from tests.test_crm_contacts_sql import OBJECTS
from tests.test_crm_gate_sql import Tokens
from tests.test_crm_timeline_sql import (
    NOW,
    T0,
    as_user,
    hubspot,
    ingest,
    owner,
    rows,
    sent,
)
from tests.test_crm_timeline_sql import (
    db as db,
)
from tests.test_crm_timeline_sql import (
    dispatcher_db as dispatcher_db,
)
from tests.test_instantly_events_sql import email, hook, record, seed
from tests.test_instantly_events_sql import pytestmark as pytestmark  # skipped without TEST_DATABASE_URL


class Hub(Timeline):
    """The portal with notes: a note is on the timeline and leaves no trace on the contact."""

    def __init__(self):
        super().__init__()
        self.notes: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != f'{OBJECTS}/notes':
            return super().__call__(request)
        self.calls.append((request.method, request.url.path))
        if failure := self.fail.pop((request.method, request.url.path), None):
            return failure
        body = json.loads(request.content)
        to = {t['types'][0]['associationTypeId']: t['to']['id'] for t in body['associations']}
        if to[202] not in self.contacts:
            return httpx.Response(400, json={'category': 'VALIDATION_ERROR'})
        self.notes.append({'id': self._id(), 'contact': to[202], 'company': to.get(190), **body['properties']})
        if self.lose_answer:
            self.lose_answer = False
            raise httpx.ReadTimeout('no answer')
        return httpx.Response(201, json={'id': self.notes[-1]['id']})


@pytest.fixture
def portal(db, http, vendor_db):
    http.handler = Hub()
    return http.handler


async def sweep(tokens=None):
    with system(SystemReason.HUBSPOT_WEBHOOK):  # as app/worker.py sync_hubspot_contacts
        return dict(await timeline.sweep(tokens or Tokens(), deadline=monotonic() + 1000))


def stale(db):
    """A minute and more on: a claimed row is taken again."""
    db.execute("UPDATE crm_timeline_events SET claimed_at = claimed_at - interval '2 minutes'")


def ready(db, *, campaign='Autunno', emails=('ana@acme.test',)):
    """A project with an Instantly connection and its owner connected to HubSpot with write-back on."""
    b = seed(db, emails)
    db.execute('UPDATE instantly_enrollments SET campaign_name = %s', (campaign,))
    hubspot(db, owner(db))
    return b


async def test_an_instantly_send_is_on_the_timeline_of_a_contact_created_for_it(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    project = db.execute('SELECT name FROM projects').fetchone()['name']

    assert await sweep() == {'recorded': 1, 'logged': 1}
    contact = portal.by_email('ana@acme.test')
    assert contact['email'] == 'ana@acme.test'
    assert not {'hubspot_owner_id', 'lifecyclestage', 'hs_email_optout'} & contact.keys()  # nothing of the team's
    [mail] = portal.logged
    assert (mail['kind'], mail['hs_email_direction'], mail['hubspot_owner_id']) == ('emails', 'EMAIL', '77')
    assert mail['hs_timestamp'] == str(int(T0.timestamp() * 1000))
    assert mail['hs_email_text'] == f'— Zenbree · {project} · via Instantly · Autunno · step 1'
    assert 'hs_email_subject' not in mail
    assert rows(db) == [('logged', None)]

    portal.calls.clear()
    assert await sweep() == {} and portal.calls == []  # once


async def test_a_human_reply_and_an_opt_out_are_logged_and_a_bounce_is_not(db, portal):
    b = ready(db)
    record(db, b, email(2, 'r1', at=T0), hook('lead_unsubscribed', at=T0 + timedelta(hours=1)))
    record(db, b, hook('email_bounced', at=T0 + timedelta(hours=2)))
    record(db, b, email(2, 'r2', at=T0 + timedelta(hours=3), auto=1))

    assert await sweep() == {'recorded': 2, 'logged': 2}
    [reply] = portal.logged
    [note] = portal.notes
    assert reply['hs_email_direction'] == 'INCOMING_EMAIL' and json.loads(reply['hs_email_headers'])['from']
    assert note['hs_note_body'].startswith('Opted out of Autunno in Instantly. Recorded by Zenbree.')
    assert 'subscription preferences were not changed' in note['hs_note_body']
    assert note['hs_note_body'].endswith(f'zb:{str(rows(db, "source_id")[1][0]).replace("-", "")[:12]}')
    assert note['contact'] == next(iter(portal.contacts)) and note['hubspot_owner_id'] == '77'
    assert 'hs_email_optout' not in portal.by_email('ana@acme.test')


async def test_a_lost_answer_for_an_email_is_found_on_the_contact_and_not_logged_twice(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    portal.lose_answer = True
    assert (await sweep())['failed'] == 1
    assert rows(db) == [('claimed', None)] and len(portal.logged) == 1
    request = db.execute('SELECT request FROM crm_timeline_events').fetchone()['request']

    assert await sweep() == {} and len(portal.logged) == 1  # a new activity shows on the contact seconds later
    stale(db)
    assert await sweep() == {'logged': 1}
    assert len(portal.logged) == 1  # HubSpot had it
    assert rows(db, 'state', 'activity_id') == [('logged', '')]
    assert db.execute('SELECT request FROM crm_timeline_events').fetchone()['request'] == request


async def test_a_lost_answer_for_an_email_that_never_arrived_is_sent_again(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    portal.fail[('POST', f'{OBJECTS}/emails')] = httpx.Response(503)
    assert (await sweep())['failed'] == 1
    stale(db)
    assert await sweep() == {'logged': 1} and len(portal.logged) == 1
    assert rows(db, 'state')[0] == ('logged',)


async def test_a_lost_opt_out_is_uncertain_and_never_sent_again_on_its_own(db, portal):
    b = ready(db)
    record(db, b, hook('lead_unsubscribed', at=T0))
    portal.lose_answer = True
    assert (await sweep())['failed'] == 1
    stale(db)
    assert await sweep() == {'uncertain': 1}
    assert rows(db) == [('uncertain', facts.Code.ANSWER_LOST)] and len(portal.notes) == 1
    stale(db)
    assert await sweep() == {} and len(portal.notes) == 1

    uid = owner(db)
    status = as_user(db, uid, ledger.status, uid, b.project_id)
    assert [(i.code, i.state, i.action) for i in status.issues] == [(facts.Code.ANSWER_LOST, 'uncertain', 'resolve')]
    assert as_user(db, uid, ledger.resolve, uid, facts.Code.ANSWER_LOST, 'log_again') == 1
    assert await sweep() == {'logged': 1} and len(portal.notes) == 2  # the user accepted the duplicate


async def test_a_rate_limit_releases_the_claim_and_never_turns_an_opt_out_uncertain(db, portal):
    b = ready(db)
    record(db, b, hook('lead_unsubscribed', at=T0))
    portal.fail[('POST', f'{OBJECTS}/notes')] = httpx.Response(429, headers={'Retry-After': '1'})
    assert (await sweep())['failed'] == 1
    assert rows(db) == [('pending', None)]
    assert db.execute('SELECT request, attempts FROM crm_timeline_events').fetchone()['attempts'] == 0
    assert db.execute('SELECT request FROM crm_timeline_events').fetchone()['request'] is not None
    assert await sweep() == {'logged': 1} and len(portal.notes) == 1


async def test_an_activity_hubspot_refuses_five_times_fails_and_a_retry_repairs_the_contact(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    await sweep()
    portal.logged.clear()
    sent(db, b, 's2', at=T0 + timedelta(hours=1), step=2)
    portal.contacts.clear()  # the contact was deleted in HubSpot
    for _ in range(facts.MAX_ATTEMPTS):
        assert (await sweep()).get('refused') == 1
    assert rows(db, 'state', 'code')[1] == ('failed', facts.Code.REFUSED)
    portal.calls.clear()
    assert await sweep() == {} and portal.calls == []

    uid = owner(db)
    assert as_user(db, uid, ledger.retry, uid, facts.Code.REFUSED) == 1
    assert await sweep() == {'logged': 1}
    assert portal.by_email('ana@acme.test') and len(portal.logged) == 1


async def test_a_held_person_gets_no_contact_and_a_person_is_read_once(db, portal):
    b = ready(db)
    portal.add_contact(email='ana@acme.test', hubspot_owner_id='99')  # a colleague's contact
    sent(db, b, 's1')
    reads = lambda: [c for c in portal.calls if c[1].endswith('contacts/batch/read')]  # noqa: E731

    assert await sweep() == {'recorded': 1, 'held': 1}
    assert len(reads()) == 1 and portal.logged == []
    assert db.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 0
    uid = owner(db)
    status = as_user(db, uid, ledger.status, uid, b.project_id)
    assert [(i.code, i.state) for i in status.issues] == [(facts.Code.HELD, 'waiting')]

    portal.calls.clear()
    assert await sweep() == {} and portal.calls == []  # the verdict is stored: the row waits, with no call


async def test_a_row_waits_for_a_revoked_grant_a_removed_member_and_the_kill_switch(db, portal, monkeypatch):
    b = ready(db)
    uid = owner(db)
    sent(db, b, 's1')
    db.execute("UPDATE user_oauth_connections SET status = 'error'")
    assert await sweep() == {'recorded': 1} and portal.calls == []
    assert [i.code for i in as_user(db, uid, ledger.status, uid, b.project_id).issues] == [facts.Code.GRANT_REVOKED]

    db.execute("UPDATE user_oauth_connections SET status = 'connected'")
    db.execute('UPDATE "user" SET banned = true WHERE id = %s', (uid,))  # no longer a live member
    assert await sweep() == {} and portal.calls == []
    db.execute('UPDATE "user" SET banned = false WHERE id = %s', (uid,))

    for module in (writer, ledger):
        monkeypatch.setattr(module, 'is_disabled', lambda key: True)
    assert await sweep() == {} and portal.calls == []
    disabled = as_user(db, uid, ledger.status, uid, b.project_id)
    assert [i.code for i in disabled.issues] == [facts.Code.VENDOR_DISABLED] and disabled.pending == 1

    for module in (writer, ledger):
        monkeypatch.setattr(module, 'is_disabled', lambda key: False)
    assert await sweep() == {'logged': 1}  # every reason cleared: the same row is due, nobody moved it


async def test_a_switch_turned_off_after_the_record_skips_the_row_for_good(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    ingest(b)  # recorded while the switch is on
    db.execute('UPDATE user_oauth_connections SET crm_write_back = false, crm_write_back_since = NULL')
    assert await sweep() == {'skipped': 1}
    assert rows(db) == [('skipped', facts.Code.OUTSIDE_WRITE_BACK)] and portal.calls == []


async def test_an_owner_deleted_after_the_record_leaves_a_skipped_row(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    ingest(b)
    db.execute('UPDATE crm_timeline_events SET owner_id = NULL')  # what ON DELETE SET NULL does
    assert await sweep() == {}
    assert rows(db) == [('skipped', facts.Code.NO_OWNER)] and portal.calls == []


@pytest.mark.parametrize(
    ('state', 'code'),
    [('failed', facts.Code.REFUSED), ('uncertain', facts.Code.ANSWER_LOST)],
)
async def test_an_owner_deleted_after_a_failure_leaves_a_skipped_row_with_no_action(db, portal, state, code):
    b = ready(db)
    sent(db, b, 's1')
    ingest(b)
    db.execute(
        'UPDATE crm_timeline_events SET state = %s, code = %s, settled_at = now(), request = %s::jsonb',
        (state, code.value, '{"frozen": true}'),
    )
    db.execute('UPDATE crm_timeline_events SET owner_id = NULL')  # what ON DELETE SET NULL does
    uid = owner(db)  # still a member: the one who sees the card after the owner left
    before = as_user(db, uid, ledger.status, uid, b.project_id)
    assert [i.state for i in before.issues] == [state] and before.issues[0].action is not None

    assert await sweep() == {} and portal.calls == []
    assert rows(db) == [('skipped', facts.Code.NO_OWNER)]
    assert db.execute('SELECT request FROM crm_timeline_events').fetchone()['request'] == {'frozen': True}
    after = as_user(db, uid, ledger.status, uid, b.project_id)
    assert [(i.code, i.state, i.action) for i in after.issues] == [(facts.Code.NO_OWNER, 'skipped', None)]


async def test_an_ownerless_failed_row_is_skipped_while_the_kill_switch_is_on(db, portal, monkeypatch):
    b = ready(db)
    sent(db, b, 's1')
    ingest(b)
    db.execute(
        "UPDATE crm_timeline_events SET state = 'failed', code = %s, settled_at = now(), owner_id = NULL,"
        ' request = \'{"frozen": true}\'::jsonb',
        (facts.Code.REFUSED.value,),
    )
    for module in (writer, ledger):
        monkeypatch.setattr(module, 'is_disabled', lambda key: True)
    assert await sweep() == {} and portal.calls == []
    assert rows(db) == [('skipped', facts.Code.NO_OWNER)]


async def test_external_emails_count_as_zenbrees_own_touches_and_an_opt_out_does_not(db, portal):
    b = ready(db, emails=('ana@acme.test', 'bea@acme.test'))
    uid = owner(db)
    sent(db, b, 's1')
    record(db, b, hook('lead_unsubscribed', email='bea@acme.test', at=T0))
    assert await sweep() == {'recorded': 2, 'logged': 2}
    assert len(portal.notes) == 1  # the opt-out is on the timeline

    touches = as_user(db, uid, activities.own_touches, uid, ['ana@acme.test', 'bea@acme.test'])
    assert list(touches) == ['ana@acme.test']  # a note does not move "Last contacted"
    assert abs((touches['ana@acme.test'] - T0).total_seconds()) < 1


async def test_a_time_that_sharpens_after_the_claim_neither_stops_the_feed_nor_moves_the_request(db, portal):
    b = ready(db)
    record(db, b, email(1, 'em-1', at=T0, step=1))
    portal.lose_answer = True
    assert (await sweep())['failed'] == 1
    before = db.execute('SELECT occurred_at, request FROM crm_timeline_events').fetchone()

    record(db, b, hook('email_sent', at=T0 + timedelta(seconds=40), step=1))  # the webhook's exact time, same fact
    assert db.execute('SELECT count(*) AS n FROM instantly_events').fetchone()['n'] == 1
    cursor = db.execute('SELECT after FROM crm_timeline_cursors').fetchone()['after']
    assert await sweep() == {}  # no ingest_failed

    assert db.execute('SELECT after FROM crm_timeline_cursors').fetchone()['after'] > cursor
    assert db.execute('SELECT occurred_at, request FROM crm_timeline_events').fetchone() == before


async def test_an_address_the_ledger_refuses_is_left_out_and_the_feed_goes_on(db, portal):
    b = ready(db, emails=('ana@acme.test', 'odd@host'))
    record(db, b, email(1, 'em-1', 'odd@host', at=T0, step=1), email(1, 'em-2', at=T0 + timedelta(hours=1), step=2))
    assert await sweep() == {'recorded': 1, 'logged': 1}
    assert db.execute('SELECT after FROM crm_timeline_cursors').fetchone()['after'] > 0


def _native(db, uid, message_id):
    """A native send and a reply with this Message-ID, since before the external one."""
    _sent(db, uid, 'ana@acme.test', at=T0 - timedelta(hours=2))
    _reply(db, uid, 'ana@acme.test', at=T0 + timedelta(hours=1))
    db.execute('UPDATE outreach_inbound_messages SET rfc_message_id = %s', (message_id,))


async def test_the_native_sweep_skips_a_reply_the_timeline_already_holds(db, portal):
    b = ready(db)
    uid = owner(db)
    record(db, b, email(2, 'r1', at=T0 + timedelta(hours=1)))
    assert (await sweep())['logged'] == 1
    _native(db, uid, '<R1@Mail>')  # the same email, as the mailbox saw it
    with system(SystemReason.HUBSPOT_WEBHOOK):
        await contacts.sweep(NOW, Tokens())
        assert dict(await activities.sweep(Tokens())) == {'sent': 1}  # its send is logged, the reply is not
    assert [m['hs_email_direction'] for m in portal.logged] == ['INCOMING_EMAIL', 'EMAIL']
    assert db.execute("SELECT count(*) AS n FROM crm_activity_writes WHERE kind = 'reply'").fetchone()['n'] == 0


async def test_the_timeline_skips_a_reply_the_native_sweep_logged(db, portal):
    b = ready(db)
    uid = owner(db)
    _native(db, uid, '<r1@mail>')
    with system(SystemReason.HUBSPOT_WEBHOOK):
        await contacts.sweep(NOW, Tokens())
        assert dict(await activities.sweep(Tokens())) == {'sent': 1, 'replies': 1}
    record(db, b, email(2, 'r1', at=T0 + timedelta(hours=1)))
    assert await sweep() == {'recorded': 1, 'skipped': 1}
    assert rows(db) == [('skipped', facts.Code.NATIVE_DUPLICATE)]
    assert len(portal.logged) == 2


async def test_a_reply_with_no_message_id_never_matches_an_empty_one(db, portal):
    b = ready(db)
    uid = owner(db)
    _native(db, uid, '')
    record(db, b, email(2, 'r1', at=T0 + timedelta(hours=1)))
    ingest(b)
    db.execute('UPDATE crm_timeline_events SET message_id = NULL')
    with system(SystemReason.HUBSPOT_WEBHOOK):
        await contacts.sweep(NOW, Tokens())
        assert dict(await activities.sweep(Tokens())) == {'sent': 1, 'replies': 1}
    assert (await sweep())['logged'] == 1


def test_every_error_code_of_hubspot_is_classified():
    """``outcome`` reads the contact write (``_settle_contact``); the activity call branches on a 401 and a 403 itself,
    which the two tests below prove through the sweep."""
    codes = {c for c in hubspot_client.__doc__.split() if c.startswith('HUBSPOT.')}
    assert codes == {
        'HUBSPOT.AUTH',
        'HUBSPOT.RATE_LIMITED',
        'HUBSPOT.CONFLICT',
        'HUBSPOT.TRANSPORT',
        'HUBSPOT.HTTP_ERROR',
        'HUBSPOT.INVALID_JSON',
    }
    expected = {
        ('HUBSPOT.AUTH', 401): writer.Outcome.NOT_SENT,
        ('HUBSPOT.AUTH', 403): writer.Outcome.NOT_SENT,
        ('HUBSPOT.RATE_LIMITED', None): writer.Outcome.NOT_SENT,
        ('HUBSPOT.CONFLICT', None): writer.Outcome.REFUSED,
        ('HUBSPOT.HTTP_ERROR', 400): writer.Outcome.REFUSED,
        ('HUBSPOT.HTTP_ERROR', 422): writer.Outcome.REFUSED,
        ('HUBSPOT.HTTP_ERROR', 500): writer.Outcome.MAYBE,
        ('HUBSPOT.TRANSPORT', None): writer.Outcome.MAYBE,
        ('HUBSPOT.INVALID_JSON', None): writer.Outcome.MAYBE,
    }
    for (code, status), wanted in expected.items():
        error = err(code, status=status).error
        assert writer.outcome(error) is wanted, (code, status)
    assert refused(err('HUBSPOT.HTTP_ERROR', status=404).error)


NOTES = ('POST', f'{OBJECTS}/notes')


async def test_a_note_refused_for_its_scope_fails_visibly_and_the_owners_next_row_moves_on(db, portal):
    b = ready(db)
    record(db, b, hook('lead_unsubscribed', at=T0))
    sent(db, b, 's1', at=T0 + timedelta(hours=1))
    for _ in range(facts.MAX_ATTEMPTS):
        portal.fail[NOTES] = httpx.Response(403)
        assert (await sweep()).get('refused') == 1
    assert rows(db, 'kind', 'state', 'code')[0] == ('opt_out', 'failed', facts.Code.REFUSED)
    assert rows(db, 'kind', 'state')[1] == ('outgoing_email', 'logged')  # logged by the sweep of the 403 itself
    assert len(portal.logged) == 1


async def test_a_401_releases_the_row_uncounted_and_stops_the_owner_for_the_sweep(db, portal):
    b = ready(db)
    record(db, b, hook('lead_unsubscribed', at=T0))
    sent(db, b, 's1', at=T0 + timedelta(hours=1))
    portal.fail[NOTES] = httpx.Response(401)
    result = await sweep()
    assert (result['failed'], result.get('logged')) == (1, None)
    assert rows(db, 'kind', 'state', 'attempts') == [('opt_out', 'pending', 0), ('outgoing_email', 'pending', 0)]
    assert (await sweep())['logged'] == 2


async def test_a_reply_exhausted_by_the_native_sweep_no_longer_hides_the_timelines_copy(db, portal):
    b = ready(db)
    uid = owner(db)
    _native(db, uid, '<r1@mail>')
    reply = db.execute('SELECT id FROM outreach_inbound_messages').fetchone()['id']
    db.execute(
        'INSERT INTO crm_activity_writes (kind, source_id, owner_id, email, touched_at, attempts)'
        " VALUES ('reply', %s, %s, 'ana@acme.test', %s, %s)",
        (reply, uid, T0, activities.MAX_ATTEMPTS),
    )
    record(db, b, email(2, 'r1', at=T0 + timedelta(hours=1)))
    assert (await sweep())['logged'] == 1
    assert [m['hs_email_direction'] for m in portal.logged] == ['INCOMING_EMAIL']


async def test_an_unreadable_2xx_body_settles_the_activity_logged_with_a_lost_id(db, portal):
    b = ready(db)
    sent(db, b, 's1')
    portal.fail[('POST', f'{OBJECTS}/emails')] = httpx.Response(201, content=b'not json')
    assert (await sweep())['logged'] == 1
    assert rows(db, 'state', 'activity_id') == [('logged', '')]
