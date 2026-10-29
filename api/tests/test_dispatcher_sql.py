"""Campaign send scheduler + email step against real SQL; provider and token endpoint faked.

Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template.
"""

import email
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.errors import err, ok
from app.core.mail_threading import ProviderReceipt
from app.core.scope import acting_for, system
from app.core.system_reasons import SystemReason
from app.integrations import mailbox_sender, sender_state, token_crypto
from app.integrations.oauth_client import OAuthClient, TokenGrant
from app.integrations.providers import GOOGLE
from app.linkedin import api as linkedin_api
from app.outreach import dispatcher, due, unsubscribe
from app.outreach.domain.campaign import validate_steps
from app.outreach.freeze import freezing
from tests.conftest import (
    call_for,
    campaign_orgs,
    each_org,
    each_org_async,
    migrated_database,
    pool,
    pool_on,
    project,
    scoped_conn,
    sender,
)
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: dispatcher SQL test NOT run')

NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)  # Monday 10:00 in Rome
GMAIL_SEND = 'https://www.googleapis.com/auth/gmail.send'
ONE_STEP = [
    {
        'step': 1,
        'channel': 'email',
        'subject': 'Ciao {{firstName}}',
        'body': 'Ciao {{firstName}}, ...',
        'send_after_days': 0,
    }
]
TWO_STEPS = [
    *ONE_STEP,
    {'step': 2, 'channel': 'email', 'body': 'Ti riscrivo, {{firstName}}', 'send_after_days': 3, 'threaded': True},
]


class Provider:
    """Fake mailbox: records every message, answers ``result``."""

    def __init__(self):
        self.sent: list[tuple[email.message.Message, str]] = []
        self.result = ok(ProviderReceipt(message_id='g1', thread_id='th1'))

    async def send(self, provider, *, access_token, raw, thread_id=''):
        assert access_token == 'fresh-token'
        self.sent.append((email.message_from_bytes(raw), thread_id))
        return self.result


_DB: list = []  # the test's connection, for the organizations a beat runs


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        monkeypatch.setattr(dispatcher, 'get_conn', scoped_conn(c))
        monkeypatch.setattr('app.outreach.sender_slot.get_conn', scoped_conn(c))
        monkeypatch.setattr('app.outreach.holds.get_conn', scoped_conn(c))
        monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
        monkeypatch.setattr('app.notifications.service.get_conn', scoped_conn(c))
        monkeypatch.setattr(sys.modules[__name__], '_DB', [c])
        yield c


def _each_org(step, *args):
    return each_org(_DB[0], step, *args)


async def _each_org_async(step, *args):
    return await each_org_async(_DB[0], step, *args)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'outreach_reply_domain', 'in.zenbree.test')
    monkeypatch.setattr(settings, 'outreach_reply_secret', 's3cret')
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test/')

    class Client:
        def __init__(self, provider):
            pass

        async def refresh(self, token):
            assert token == 'refresh-1'
            return ok(TokenGrant(access_token='fresh-token', refresh_token=None, granted_scopes=()))

    fake = Provider()
    monkeypatch.setattr(dispatcher, 'OAuthClient', Client)
    monkeypatch.setattr(mailbox_sender, 'send', fake.send)
    return fake


@pytest.fixture
def switches(monkeypatch):
    on: set[str] = set()
    monkeypatch.setattr(dispatcher, 'is_disabled', lambda key: key in on)
    monkeypatch.setattr(sender_state, 'is_disabled', lambda key: key in on)
    return on


def _mailbox(c, uid, *, age_days=30, provider='google', projects=None, **cols) -> str:
    return sender(
        c,
        uid,
        provider,
        projects=projects,
        **{
            'refresh_token_encrypted': token_crypto.encrypt('refresh-1').value,
            'granted_scopes': ['openid', GMAIL_SEND],
            'account_label': 'me@acme.test',
            'sign_off': 'Marco',
            'created_at': NOW - timedelta(days=age_days),
            **cols,
        },
    )


def _campaign(c, uid, steps=ONE_STEP, *, approve=False, emails=('anna@client.test',)) -> str:
    cid = c.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,
               approve_before_sending, activated_at)
           VALUES (default_organization(%s), %s, %s, 'C', 'active', %s, %s, %s) RETURNING id""",
        (uid, project(c, uid), uid, json.dumps(validate_steps(steps)), approve, NOW - timedelta(hours=1)),
    ).fetchone()['id']
    pool(c, cid, ('google',))
    for addr in emails:
        c.execute(
            """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status)
               VALUES (%s, %s, %s, 'Anna', 'Client', 'active')""",
            (cid, uid, addr),
        )
    return str(cid)


def _sends(c):
    return c.execute('SELECT * FROM outreach_campaign_sends ORDER BY step, created_at').fetchall()


async def _beat(now=NOW):
    _each_org(dispatcher.plan, now)
    return await _each_org_async(dispatcher.send_due, now)


async def test_approval_gate_then_one_paced_send(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid, approve=True, emails=('anna@client.test', 'bea@client.test'))

    await _beat()
    claims = _sends(db)
    assert [s['status'] for s in claims] == ['awaiting_approval', 'awaiting_approval']
    assert claims[0]['subject'] == 'Ciao Anna' and claims[0]['body'] == 'Ciao Anna, ...'
    assert provider.sent == []  # nobody approved anything

    db.execute("UPDATE outreach_campaign_sends SET status = 'approved', approved_at = now()")
    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 1
    assert (await _each_org_async(dispatcher.send_due, NOW + timedelta(minutes=1)))[
        'paced'
    ] == 1  # random gap per mailbox
    ((msg, _),) = provider.sent
    assert msg['To'] in {'anna@client.test', 'bea@client.test'} and msg['From'] == 'me@acme.test'
    assert msg['Reply-To'].startswith('reply+')
    # #172: the HTTPS one-click first, then the mailto; the footer links the page with the same token.
    one_click, mailto = msg['List-Unsubscribe'].split(', ')
    assert one_click.startswith('<https://api.zenbree.test/v1/unsubscribe/') and mailto.startswith('<mailto:reply+')
    assert msg['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
    token = one_click.removeprefix('<https://api.zenbree.test/v1/unsubscribe/').removesuffix('>')
    assert unsubscribe.parse(token, secret='s3cret') == (uid, msg['To'])
    assert 'Marco' in msg.get_payload() and 'no grazie' in msg.get_payload()
    assert f'{settings.web_origin}/u/{token}' in msg.get_payload(decode=True).decode()
    sent = next(s for s in _sends(db) if s['status'] == 'sent')
    assert sent['delivered_at'] and sent['provider_receipt_id'] == 'g1' and sent['sender_provider'] == 'google'
    # Minted at send on the mailbox's own domain, stored before the provider call.
    assert msg['Message-ID'] == sent['message_id'] and sent['message_id'].endswith('@acme.test>')
    assert (
        db.execute('SELECT next_step FROM outreach_campaign_targets WHERE email = %s', (msg['To'],)).fetchone()[
            'next_step'
        ]
        == 2
    )

    await _each_org_async(dispatcher.send_due, NOW + timedelta(minutes=15))
    assert len(provider.sent) == 2


async def test_a_send_rechecks_bounces_that_came_before_twenty_sends(db, provider, switches):
    """#747 review: 3 hard bounces out of 19 sent emails cannot pause (under 20 sends); the 20th send does, with no
    new bounce (3 of 20 is 15 %)."""
    uid = _user(db)
    mailbox = _mailbox(db, uid)
    cid = _campaign(db, uid, emails=('anna@client.test', *(f'p{i}@prospect.test' for i in range(19))))
    db.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               idempotency_key, delivered_at, sender_id, bounced_at, bounce_kind)
           SELECT campaign_id, owner_id, id, 1, 'email', 'sent', email, gen_random_uuid()::text, %s, %s,
                  CASE WHEN email < 'p12' THEN %s END, CASE WHEN email < 'p12' THEN 'hard' END
           FROM outreach_campaign_targets WHERE email LIKE 'p%%'""",
        (NOW - timedelta(days=1), mailbox, NOW - timedelta(hours=20)),
    )
    db.execute("UPDATE outreach_campaign_targets SET next_step = 2 WHERE email LIKE 'p%%'")
    hard = db.execute("SELECT count(*) AS n FROM outreach_campaign_sends WHERE bounce_kind = 'hard'").fetchone()['n']
    assert hard == 3  # p0, p1, p10

    counts = await _beat()

    assert counts['sent'] == 1
    assert db.execute('SELECT status FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()['status'] == 'paused'
    (n,) = db.execute("SELECT data FROM notifications WHERE kind = 'bounces_high'").fetchall()
    assert n['data'] == {'campaign': 'C', 'invalid': 3, 'attempted': 20, 'paused': True}


async def test_kill_switches_hold_sending(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid)
    switches.add('channel_disabled:email')
    assert (await _beat())['channel_disabled'] == 1
    switches.clear()
    switches.add('vendor_disabled:google')
    assert (await _each_org_async(dispatcher.send_due, NOW))['vendor_disabled'] == 1
    assert provider.sent == [] and _sends(db)[0]['status'] == 'approved'


async def test_exclusion_is_checked_again_at_send_time(db, provider, switches, webhook_events):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid)
    _each_org(dispatcher.plan, NOW)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'client.test')",
        (project(db, uid), uid),
    )
    assert (await _each_org_async(dispatcher.send_due, NOW))['excluded'] == 1
    assert provider.sent == [] and _sends(db)[0]['error_code'] == 'EXCLUDED'
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'excluded'
    assert [(p['type'], p['data']['reason'], p['data']['email']) for _, p in webhook_events] == [
        ('sequence.stopped', 'excluded', 'anna@client.test')
    ]


def test_a_reply_during_the_send_keeps_its_stop(db, webhook_events):
    uid = _user(db)
    _campaign(db, uid)
    _each_org(dispatcher.plan, NOW)
    send = db.execute('SELECT * FROM outreach_campaign_sends').fetchone()
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied'")  # replies.record, after send_due read the row
    with acting_for(str(send['organization_id']), 'campaign beat'):
        dispatcher._settle(send, 'skipped', 'EXCLUDED', claimed=False, target_status='excluded')
    assert db.execute('SELECT status FROM outreach_campaign_targets').fetchone()['status'] == 'replied'
    assert webhook_events == []  # the reply's own sequence.stopped was the only one


async def test_unknown_outcome_is_never_resent(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid)
    provider.result = err('INTEGRATIONS.TRANSPORT', detail='timeout', delivery_ambiguous=True)
    assert (await _beat())['unknown'] == 1
    for minutes in (20, 60, 240):
        await _beat(NOW + timedelta(minutes=minutes))
    assert len(provider.sent) == 1
    row = _sends(db)[0]
    assert row['status'] == 'sending' and row['attempts'] == 1 and row['error_code'] == 'INTEGRATIONS.TRANSPORT'


async def test_definite_failure_is_retried_then_refused(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid)
    provider.result = err('INTEGRATIONS.TRANSPORT', detail='connect refused', delivery_ambiguous=False)
    await _beat()
    assert _sends(db)[0]['status'] == 'failed'
    for minutes in (20, 40, 60):
        await _beat(NOW + timedelta(minutes=minutes))
    assert len(provider.sent) == 3 and _sends(db)[0]['status'] == 'refused'


async def test_rate_limit_rests_the_mailbox_without_spending_an_attempt(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid)
    provider.result = err('INTEGRATIONS.RATE_LIMITED', detail='quota')
    assert (await _beat())['rate_limited'] == 1
    row = _sends(db)[0]
    assert row['status'] == 'approved' and row['attempts'] == 0
    assert (await _each_org_async(dispatcher.send_due, NOW + timedelta(minutes=30)))[
        'throttled'
    ] == 1  # resting for an hour
    provider.result = ok(ProviderReceipt(message_id='g1'))
    assert (await _each_org_async(dispatcher.send_due, NOW + timedelta(hours=1, minutes=15)))['sent'] == 1


async def test_follow_up_is_threaded_after_its_delay(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid, TWO_STEPS)
    await _beat()
    await _beat(NOW + timedelta(days=1))
    assert len(provider.sent) == 1  # step 2 waits three days from the first delivery

    later = NOW + timedelta(days=3, hours=1)
    await _beat(later)
    (first, _), (second, thread_id) = provider.sent
    assert second['Subject'] == 'Re: Ciao Anna'
    assert second['In-Reply-To'] == first['Message-ID'] and second['References'] == first['Message-ID']
    assert thread_id == 'th1'  # Gmail's own thread handle from the first send


def _assigned(c, cid: str, people: dict[str, str | None]) -> None:
    """People who joined the test, each with their copy; seed SQL may insert a variant (only an UPDATE is checked)."""
    c.execute('DELETE FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,))
    for name, variant in people.items():
        c.execute(
            """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status,
                   variant) VALUES (%s, (SELECT owner_id FROM outreach_campaigns WHERE id = %s), %s, %s, 'Client',
                   'active', %s)""",
            (cid, cid, f'{name.lower()}@client.test', name, variant),
        )


AB_STEPS = [
    ONE_STEP[0] | {'b': {'subject': 'Due minuti, {{firstName}}?', 'body': 'Copia B, {{firstName}}'}},
    TWO_STEPS[1],
]


async def test_each_person_is_sent_the_copy_of_their_variant(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    cid = _campaign(db, uid, AB_STEPS, approve=True)
    # Cy joined while the allocation was paused, or before the test existed: copy A, as before #934.
    _assigned(db, cid, {'Anna': 'a', 'Bea': 'b', 'Cy': None})

    _each_org(dispatcher.plan, NOW)
    _each_org(dispatcher.plan, NOW + timedelta(minutes=1))
    assert len(_sends(db)) == 3  # the second plan claims nothing
    assert {s['recipient']: (s['subject'], s['body']) for s in _sends(db)} == {
        'anna@client.test': ('Ciao Anna', 'Ciao Anna, ...'),
        'bea@client.test': ('Due minuti, Bea?', 'Copia B, Bea'),
        'cy@client.test': ('Ciao Cy', 'Ciao Cy, ...'),
    }


async def test_a_b_follow_up_threads_under_the_b_subject_with_the_shared_copy(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    cid = _campaign(db, uid, AB_STEPS)
    _assigned(db, cid, {'Bea': 'b'})

    await _beat()
    await _beat(NOW + timedelta(days=3, hours=1))
    (first, _), (second, _) = provider.sent
    assert (first['Subject'], second['Subject']) == ('Due minuti, Bea?', 'Re: Due minuti, Bea?')
    assert 'Ti riscrivo, Bea' in second.get_payload()  # step 2 has no B copy: B people get A's


async def test_caps_window_and_isolation(db, provider, switches):
    uid, other = _user(db), _user(db)
    mailbox = _mailbox(db, uid, age_days=1)
    cid = _campaign(db, uid)
    _campaign(db, other)  # no mailbox of their own: never sent through someone else's
    target = db.execute('SELECT id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchone()['id']
    for i in range(10):
        db.execute(
            """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, step, channel, status, recipient,
                   idempotency_key, sender_provider, sender_id, attempted_at, delivered_at)
               VALUES (%s, %s, 1, 'email', 'sent', 'x@y.test', %s, 'google',
                       (SELECT id FROM user_oauth_connections WHERE provider = 'google'), %s, %s)""",
            (cid, uid, f'old-{i}', NOW - timedelta(hours=1), NOW - timedelta(hours=1)),
        )
    counts = await _beat()
    assert counts['capped'] == 1 and len(counts) == 1 and provider.sent == []  # `other` has no mailbox: no turn

    db.execute('UPDATE user_oauth_connections SET created_at = %s', (NOW - timedelta(days=30),))
    saturday = NOW + timedelta(days=5)
    assert (await _each_org_async(dispatcher.send_due, saturday))['outside_mailbox_window'] == 1
    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 1
    pin = db.execute('SELECT sender_id::text FROM outreach_campaign_targets WHERE id = %s', (target,)).fetchone()
    assert pin == {'sender_id': mailbox}


async def test_draft_targets_and_paused_campaigns_send_nothing(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    cid = _campaign(db, uid)
    db.execute("UPDATE outreach_campaign_targets SET status = 'draft'")  # a refill nobody reviewed
    await _beat()
    assert _sends(db) == []
    with freezing(db):
        db.execute("UPDATE outreach_campaign_targets SET status = 'active'")
    _each_org(dispatcher.plan, NOW)
    db.execute("UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (cid,))
    await _each_org_async(dispatcher.send_due, NOW)
    assert provider.sent == [] and _sends(db)[0]['status'] == 'approved'


async def test_rotated_refresh_token_is_saved_and_revoked_grant_marks_error(db, provider, switches, monkeypatch):
    uid = _user(db)
    _mailbox(db, uid, provider='microsoft', granted_scopes=['User.Read', 'Mail.Send'])
    cid = _campaign(db, uid)
    pool(db, cid, ('microsoft',))
    refreshes = iter(
        [
            ok(TokenGrant(access_token='fresh-token', refresh_token='refresh-2', granted_scopes=())),
            err('INTEGRATIONS.AUTH', detail='invalid_grant'),
        ]
    )

    class Rotating:
        def __init__(self, provider):
            pass

        async def refresh(self, token):
            return next(refreshes)

    monkeypatch.setattr(dispatcher, 'OAuthClient', Rotating)
    assert (await _beat())['sent'] == 1
    stored = db.execute('SELECT refresh_token_encrypted, status FROM user_oauth_connections').fetchone()
    assert token_crypto.decrypt(stored['refresh_token_encrypted']).value == 'refresh-2'  # Microsoft rotates

    _campaign(db, uid, emails=('cleo@client.test',))
    pool(db, providers=('microsoft',))
    assert (await _beat(NOW + timedelta(minutes=20)))['token_failed'] == 1
    assert db.execute('SELECT status FROM user_oauth_connections').fetchone()['status'] == 'error'
    assert len(provider.sent) == 1


@pytest.mark.parametrize(
    ('http_status', 'body', 'mailbox_status'),
    [
        (401, {'error': 'invalid_client', 'error_description': 'The OAuth client was not found.'}, 'connected'),
        (400, {'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'}, 'error'),
    ],
)
async def test_only_a_dead_grant_marks_the_mailbox_not_our_rejected_client(
    db, provider, http, monkeypatch, http_status, body, mailbox_status
):
    """#680: a wrong client secret is the operator's to fix; Reconnect would not help the user."""
    monkeypatch.setattr(dispatcher, 'OAuthClient', OAuthClient)
    monkeypatch.setattr(settings, 'google_oauth_client_id', 'google-id')
    monkeypatch.setattr(settings, 'google_oauth_client_secret', 'google-secret')
    http.handler = lambda request: httpx.Response(http_status, json=body)
    uid = _user(db)
    mb = db.execute('SELECT * FROM user_oauth_connections WHERE id = %s', (_mailbox(db, uid),)).fetchone()
    with acting_for(str(mb['organization_id']), 'campaign beat'):
        assert await dispatcher._access_token(mb, GOOGLE) is None
    assert [str(r.url) for r in http.calls] == [GOOGLE.token_url]
    assert db.execute('SELECT status FROM user_oauth_connections').fetchone()['status'] == mailbox_status


async def test_one_owners_backlog_does_not_starve_another(db, provider, switches, monkeypatch):
    monkeypatch.setattr(dispatcher, 'QUEUE_PER_MAILBOX', 2)
    big, stuck, small = _user(db), _user(db), _user(db)
    _mailbox(db, big)
    _mailbox(db, stuck, status='error')  # its approved rows can never drain
    _mailbox(db, small)
    _campaign(db, big, emails=[f'p{i}@client.test' for i in range(6)])
    _campaign(db, stuck, emails=[f's{i}@client.test' for i in range(6)])
    _campaign(db, small, emails=('late@client.test',))
    _each_org(dispatcher.plan, NOW)
    db.execute(
        "UPDATE outreach_campaign_sends SET approved_at = now() - interval '1 day' WHERE owner_id <> %s", (small,)
    )
    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 2
    assert {m['To'] for m, _ in provider.sent} >= {'late@client.test'}


async def test_recipient_must_be_exactly_one_address(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid, emails=('v@x.com, w@y.com', ' Anna@Client.test '))
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'email', 'w@y.com')",
        (project(db, uid), uid),
    )
    await _beat()
    await _beat(NOW + timedelta(minutes=20))
    rows = {r['error_code'] or r['status']: r for r in _sends(db)}
    assert rows['INVALID_EMAIL']['status'] == 'skipped'
    assert [m['To'] for m, _ in provider.sent] == ['anna@client.test']  # normalised, one address


async def test_unbuildable_or_oversized_copy_is_refused_not_stuck(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid, display_name='Marco\r\nBcc: x@evil.test')
    cid = _campaign(db, uid)
    _each_org(dispatcher.plan, NOW)
    assert (await _each_org_async(dispatcher.send_due, NOW))['refused_copy'] == 1
    row = _sends(db)[0]
    assert row['status'] == 'refused' and row['error_code'] == 'INVALID_CONTENT' and row['attempts'] == 0
    assert provider.sent == []

    long = [{**ONE_STEP[0], 'body': 'x' * 3980 + ' {{companyName}}'}]
    db.execute('UPDATE outreach_campaigns SET steps = %s WHERE id = %s', (json.dumps(validate_steps(long)), cid))
    db.execute('DELETE FROM outreach_campaign_sends')
    db.execute('UPDATE outreach_campaign_targets SET company_name = %s', ('A' * 50,))
    assert _each_org(dispatcher.plan, NOW)['refused_copy'] == 1
    assert _sends(db)[0]['error_code'] == 'COPY_TOO_LONG'
    assert _each_org(dispatcher.plan, NOW) == {}  # refused once, not a failing insert every beat


def test_plan_batch_skips_steps_of_other_channels(db, monkeypatch):
    monkeypatch.setattr(due, 'PLAN_BATCH', 1)
    for module in (dispatcher, due):  # a channel with no sender yet
        monkeypatch.setattr(module, 'SENDABLE_CHANNELS', ('email',))
    uid = _user(db)
    linkedin = [{'step': 1, 'channel': 'linkedin_connect', 'body': '', 'send_after_days': 0}]
    _campaign(db, uid, linkedin, emails=('a@x.test',))
    _campaign(db, uid, emails=('b@x.test',))
    assert _each_org(dispatcher.plan, NOW)['approved'] == 1


async def test_paused_mailbox_sends_nothing_until_resumed(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid, paused=True)
    _campaign(db, uid)
    await _beat()
    assert provider.sent == [] and _sends(db)[0]['status'] == 'approved'
    db.execute('UPDATE user_oauth_connections SET paused = false')
    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 1


async def test_mailbox_window_gates_sending_on_top_of_the_campaign_window(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid)
    db.execute('UPDATE user_oauth_connections SET send_window_start_hour = 11')  # NOW is 10:00 in Rome
    assert (await _beat())['outside_mailbox_window'] == 1 and provider.sent == []
    db.execute('UPDATE user_oauth_connections SET send_window_start_hour = 10, send_window_days = ARRAY[1]::smallint[]')
    assert (await _each_org_async(dispatcher.send_due, NOW))[
        'outside_mailbox_window'
    ] == 1  # Monday is not in the mailbox's days
    db.execute('UPDATE user_oauth_connections SET send_window_days = ARRAY[0]::smallint[]')
    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 1


# ── LinkedIn step (#82) ──────────────────────────────────────────────────────

LINKEDIN_STEPS = [
    {'step': 1, 'channel': 'linkedin_connect', 'body': 'Ciao {{firstName}}', 'send_after_days': 0},
    {'step': 2, 'channel': 'linkedin_message', 'body': 'Grazie {{firstName}}!', 'send_after_days': 1},
    {'step': 3, 'channel': 'email', 'subject': 'Ciao {{firstName}}', 'body': 'Anna, ...', 'send_after_days': 2},
]


class Unipile:
    """Fake Unipile: one profile whose degree a test flips, records every write."""

    def __init__(self):
        self.connected = False
        self.calls: list[tuple] = []
        self.result = ok({'invitation_id': 'inv-1', 'chat_id': 'chat-1'})

    def __call__(self):
        return self

    def is_configured(self):
        return True

    async def resolve_profile(self, *, account_id, identifier):
        assert account_id == 'acc-1' and identifier == 'anna-rossi'
        return ok({'provider_id': 'ACo1', 'network_distance': 'FIRST_DEGREE' if self.connected else 'SECOND_DEGREE'})

    async def send_invitation(self, *, account_id, provider_id, message):
        self.calls.append(('invite', provider_id, message))
        return self.result

    async def send_dm(self, *, account_id, provider_id, text):
        self.calls.append(('dm', provider_id, text))
        return self.result


@pytest.fixture
def unipile(monkeypatch):
    fake = Unipile()
    monkeypatch.setattr(dispatcher, 'UnipileClient', fake)
    monkeypatch.setattr(dispatcher, 'record_vendor_call', lambda *a, **k: None)
    return fake


def _linkedin(c, uid, account='acc-1'):
    sender(c, uid, 'unipile', refresh_token_encrypted='sealed', provider_account_id=account, linkedin_premium=True)


def _li_campaign(c, uid, steps=LINKEDIN_STEPS) -> str:
    cid = _campaign(c, uid, steps)
    c.execute("UPDATE outreach_campaign_targets SET linkedin_url = 'https://www.linkedin.com/in/anna-rossi/'")
    return cid


def _target(c):
    return c.execute('SELECT * FROM outreach_campaign_targets ORDER BY created_at LIMIT 1').fetchone()


async def _li_beat(now=NOW):
    _each_org(dispatcher.plan, now)
    return await _each_org_async(dispatcher.send_linkedin_due, now)


async def test_linkedin_invite_then_dm_after_acceptance(db, unipile, switches, monkeypatch):
    uid, other = _user(db), _user(db)
    _linkedin(db, uid)
    _li_campaign(db, uid)
    assert (await _li_beat())['linkedin_sent'] == 1
    assert unipile.calls == [('invite', 'ACo1', 'Ciao Anna')]
    target = _target(db)
    assert target['next_step'] == 2 and target['linkedin_provider_id'] == 'ACo1'
    invite = _sends(db)[0]
    assert invite['status'] == 'sent' and invite['provider_receipt_id'] == 'inv-1'
    assert invite['sender_provider'] == 'unipile' and invite['recipient'].endswith('/in/anna-rossi/')

    wednesday = NOW + timedelta(days=2)
    await _li_beat(wednesday)
    assert len(_sends(db)) == 1  # the DM waits for the acceptance

    # Another owner's target with the same profile is not theirs to stamp.
    cid = _campaign(db, other, emails=('x@other.test',))
    db.execute("UPDATE outreach_campaign_targets SET linkedin_provider_id = 'ACo1' WHERE campaign_id = %s", (cid,))
    db.execute("UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (cid,))
    monkeypatch.setattr(linkedin_api.webhook_events, 'get_conn', dispatcher.get_conn)
    with system(SystemReason.LINKEDIN_WEBHOOK):  # as POST /v1/linkedin/webhook runs it
        accepted = linkedin_api.webhook_events.accepted(
            {'event': 'new_relation', 'account_id': 'acc-1', 'user_provider_id': 'ACo1'}
        )
    assert accepted['targets'] == 1
    assert db.execute(
        'SELECT linkedin_accepted_at FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)
    ).fetchone() == {'linkedin_accepted_at': None}

    db.execute('UPDATE outreach_campaign_targets SET linkedin_accepted_at = %s WHERE owner_id = %s', (wednesday, uid))
    unipile.connected = True
    await _li_beat(wednesday + timedelta(hours=1))
    assert len(_sends(db)) == 1  # its one-day delay counts from the acceptance
    assert (await _li_beat(wednesday + timedelta(days=1, minutes=1)))['linkedin_sent'] == 1
    assert unipile.calls[-1] == ('dm', 'ACo1', 'Grazie Anna!')
    assert _target(db)['next_step'] == 3


async def test_dm_without_acceptance_falls_back_to_email(db, unipile, provider, switches):
    uid = _user(db)
    _linkedin(db, uid)
    _mailbox(db, uid)
    _li_campaign(db, uid)
    await _li_beat()
    await _li_beat(NOW + timedelta(days=2, hours=23))
    assert len(_sends(db)) == 1  # still inside the three days

    thursday = NOW + timedelta(days=3, minutes=1)
    assert _each_org(dispatcher.plan, thursday)['skipped_not_accepted'] == 1
    dm = _sends(db)[1]
    assert dm['status'] == 'skipped' and dm['error_code'] == 'NOT_ACCEPTED' and _target(db)['next_step'] == 3
    db.execute('UPDATE outreach_campaign_sends SET created_at = %s WHERE id = %s', (thursday, dm['id']))
    friday = thursday + timedelta(days=1)
    assert (await _beat(friday))['sent'] == 1
    assert [c[0] for c in unipile.calls] == ['invite'] and provider.sent[0][0]['To'] == 'anna@client.test'


async def test_already_connected_skips_the_invite_and_unlocks_the_dm(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    _li_campaign(db, uid)
    unipile.connected = True
    assert (await _li_beat())['linkedin_already_connected'] == 1
    assert unipile.calls == [] and _sends(db)[0]['error_code'] == 'ALREADY_CONNECTED'
    target = _target(db)
    assert target['next_step'] == 2 and target['linkedin_accepted_at'] == NOW
    # A skipped row has no delivered_at, so the DM's delay counts from created_at: the wall clock, not NOW.
    db.execute('UPDATE outreach_campaign_sends SET created_at = %s', (NOW,))
    assert (await _li_beat(NOW + timedelta(days=1, minutes=1)))['linkedin_sent'] == 1
    assert unipile.calls == [('dm', 'ACo1', 'Grazie Anna!')]


async def test_linkedin_caps_window_cooldown_and_kill_switch(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    cid = _li_campaign(db, uid)
    db.execute('UPDATE outreach_campaigns SET send_window_start_hour = 0, send_window_end_hour = 24')
    evening = NOW + timedelta(hours=10)  # 20:00 in Rome: the campaign allows it, LinkedIn does not
    assert (await _li_beat(evening))['linkedin_outside_window'] == 1

    def history(n, **cols):
        for _ in range(n):
            row = {'channel': 'linkedin_connect', 'status': 'sent', 'attempted_at': NOW - timedelta(hours=1), **cols}
            db.execute(
                """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, step, channel, status, recipient,
                       idempotency_key, attempted_at, delivered_at, sender_id)
                   VALUES (%s, %s, 1, %s, %s, 'x', %s, %s, %s,
                           (SELECT id FROM user_oauth_connections WHERE provider = 'unipile'))""",
                (cid, uid, row['channel'], row['status'], uuid.uuid4().hex, row['attempted_at'], row['attempted_at']),
            )

    history(20)
    assert (await _each_org_async(dispatcher.send_linkedin_due, NOW))['linkedin_capped'] == 1
    db.execute("DELETE FROM outreach_campaign_sends WHERE recipient = 'x'")
    history(100, attempted_at=NOW - timedelta(days=3))
    assert (await _each_org_async(dispatcher.send_linkedin_due, NOW))['linkedin_capped'] == 1  # 100 a week
    db.execute("DELETE FROM outreach_campaign_sends WHERE recipient = 'x'")

    switches.add('channel_disabled:linkedin')
    assert (await _each_org_async(dispatcher.send_linkedin_due, NOW))['linkedin_disabled'] == 1
    switches.clear()
    assert (await _each_org_async(dispatcher.send_linkedin_due, NOW))['linkedin_sent'] == 1
    assert len(unipile.calls) == 1


async def test_linkedin_unknown_outcome_is_never_resent_and_no_account_holds(db, unipile, switches):
    uid = _user(db)
    _li_campaign(db, uid)
    assert not await _li_beat()  # no account: the claim waits, no attempt spent
    assert _sends(db)[0]['attempts'] == 0
    _linkedin(db, uid)
    unipile.result = err('INTEGRATIONS.TRANSPORT', detail='timeout')
    assert (await _li_beat())['linkedin_unknown'] == 1
    for minutes in (20, 60, 240):
        await _li_beat(NOW + timedelta(minutes=minutes))
    row = _sends(db)[0]
    assert len(unipile.calls) == 1 and row['status'] == 'sending' and row['attempts'] == 1


async def test_linkedin_limit_rests_the_account_for_a_day(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    _li_campaign(db, uid)
    unipile.result = err('OUTREACH.LINKEDIN_PROVIDER_LIMIT', detail='weekly invitations')
    assert (await _li_beat())['linkedin_failed'] == 1
    assert (await _li_beat(NOW + timedelta(hours=23)))['linkedin_paced'] == 1
    assert (await _li_beat(NOW + timedelta(hours=25)))['linkedin_failed'] == 1  # Tuesday: tried again
    assert len(unipile.calls) == 2 and _sends(db)[0]['attempts'] == 2


def _skip_invite(c, uid, cid):
    """Step 1 settled without an invitation going out (e.g. skipped), the person on step 2."""
    target = _target(c)['id']
    c.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               idempotency_key, created_at)
           VALUES (%s, %s, %s, 1, 'linkedin_connect', 'skipped', 'x-invite', %s, %s)""",
        (cid, uid, target, uuid.uuid4().hex, NOW),
    )
    c.execute('UPDATE outreach_campaign_targets SET next_step = 2')


async def test_dm_without_a_sent_invitation_checks_the_connection(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    cid = _li_campaign(db, uid)
    _skip_invite(db, uid, cid)
    assert (await _li_beat(NOW + timedelta(days=1, minutes=1)))['linkedin_skipped_not_connected'] == 1
    assert unipile.calls == [] and _sends(db)[1]['error_code'] == 'NOT_CONNECTED' and _target(db)['next_step'] == 3

    db.execute('DELETE FROM outreach_campaign_sends WHERE step = 2')
    db.execute('UPDATE outreach_campaign_targets SET next_step = 2')
    db.execute('UPDATE user_oauth_connections SET last_used_at = NULL')
    unipile.connected = True
    assert (await _li_beat(NOW + timedelta(days=1, minutes=1)))['linkedin_sent'] == 1
    assert unipile.calls == [('dm', 'ACo1', 'Grazie Anna!')]


async def test_linkedin_dm_daily_cap(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    cid = _li_campaign(db, uid)
    _skip_invite(db, uid, cid)
    db.execute('UPDATE outreach_campaign_targets SET linkedin_accepted_at = %s', (NOW,))
    for _ in range(30):
        db.execute(
            """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, step, channel, status, recipient,
                   idempotency_key, attempted_at, delivered_at, sender_id)
               VALUES (%s, %s, 2, 'linkedin_message', 'sent', 'x', %s, %s, %s,
                       (SELECT id FROM user_oauth_connections WHERE provider = 'unipile'))""",
            (cid, uid, uuid.uuid4().hex, NOW, NOW),
        )
    unipile.connected = True
    later = NOW + timedelta(days=1, minutes=1)  # Tuesday: the 30 DMs above were Monday's
    db.execute(
        "UPDATE outreach_campaign_sends SET attempted_at = %s WHERE recipient = 'x'", (later - timedelta(hours=1),)
    )
    assert (await _li_beat(later))['linkedin_capped'] == 1
    db.execute(
        "DELETE FROM outreach_campaign_sends WHERE recipient = 'x' AND ctid IN "
        "(SELECT ctid FROM outreach_campaign_sends WHERE recipient = 'x' LIMIT 1)"
    )
    assert (await _each_org_async(dispatcher.send_linkedin_due, later))['linkedin_sent'] == 1  # 29 today: one more goes


async def test_paused_linkedin_account_sends_nothing_until_resumed(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    _li_campaign(db, uid)
    db.execute("UPDATE user_oauth_connections SET paused = true WHERE provider = 'unipile'")
    assert not await _li_beat()  # no queue for a paused account
    assert unipile.calls == [] and _sends(db)[0]['status'] == 'approved' and _sends(db)[0]['attempts'] == 0
    db.execute("UPDATE user_oauth_connections SET paused = false WHERE provider = 'unipile'")
    assert (await _each_org_async(dispatcher.send_linkedin_due, NOW))['linkedin_sent'] == 1


def test_active_duplicate_is_held_until_the_first_campaign_lets_go(db):
    """#83: a person another active campaign (armed first) is still writing to gets no claim here."""
    uid, other = _user(db), _user(db)
    first = _campaign(db, uid, emails=('Anna@Client.test',))
    second = _campaign(db, uid, emails=(' anna@client.test',))
    _campaign(db, other)  # another owner's campaign never holds anyone
    db.execute(
        'UPDATE outreach_campaigns SET activated_at = activated_at - %s::interval WHERE id = %s', ('1 hour', first)
    )
    li = _campaign(db, uid, emails=())
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, linkedin_url, first_name, status)'
        " VALUES (%s, %s, 'https://www.linkedin.com/in/bo/', 'Bo', 'active')",
        (li, uid),
    )
    db.execute(
        'UPDATE outreach_campaigns SET activated_at = activated_at - %s::interval WHERE id = %s', ('2 hours', li)
    )
    bo = _campaign(db, uid, emails=('bo@client.test',))
    db.execute("UPDATE outreach_campaign_targets SET linkedin_url = 'linkedin.com/in/BO' WHERE campaign_id = %s", (bo,))

    _each_org(dispatcher.plan, NOW)
    claimed = {str(s['campaign_id']) for s in _sends(db)}
    assert first in claimed and second not in claimed and bo not in claimed  # held by email and by LinkedIn

    # The first sequence ends (or the second opts out of the rule): the hold lifts.
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE campaign_id = %s", (first,))
    db.execute('UPDATE outreach_campaigns SET block_active_duplicates = false WHERE id = %s', (bo,))
    _each_org(dispatcher.plan, NOW)
    claimed = {str(s['campaign_id']) for s in _sends(db)}
    assert second in claimed and bo in claimed


def test_active_duplicate_hold_lifts_when_the_holder_pauses_or_finishes(db):
    uid = _user(db)
    holder = _campaign(db, uid, emails=('anna@client.test', 'bea@client.test'))
    db.execute(
        'UPDATE outreach_campaigns SET activated_at = activated_at - %s::interval WHERE id = %s', ('1 hour', holder)
    )
    later = _campaign(db, uid, emails=('anna@client.test', 'bea@client.test'))
    db.execute(
        "UPDATE outreach_campaign_targets SET next_step = 2 WHERE campaign_id = %s AND email = 'anna@client.test'",
        (holder,),
    )  # anna: past the holder's last (only) step, so it no longer writes to her
    _each_org(dispatcher.plan, NOW)
    assert {s['recipient'] for s in _sends(db) if str(s['campaign_id']) == later} == {'anna@client.test'}

    db.execute(
        "UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (holder,)
    )  # a paused holder holds nobody
    _each_org(dispatcher.plan, NOW)
    assert {s['recipient'] for s in _sends(db) if str(s['campaign_id']) == later} == {
        'anna@client.test',
        'bea@client.test',
    }


def _client_campaign(c, owner, client, people, armed=NOW - timedelta(hours=1)) -> str:
    """An active campaign of ``owner`` for the client ``client``, with one active target per (email, LinkedIn URL)."""
    cid = c.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, activated_at)
           SELECT organization_id, id, %s, 'C', 'active', %s, %s FROM projects WHERE id = %s RETURNING id""",
        (owner, json.dumps(validate_steps(ONE_STEP)), armed, client),
    ).fetchone()['id']
    pool(c, cid, ('google',))
    for addr, linkedin in people:
        c.execute(
            """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, first_name, status)
               VALUES (%s, %s, %s, %s, 'Anna', 'active')""",
            (cid, owner, addr, linkedin),
        )
    return str(cid)


def _due(c) -> set[tuple[str, str]]:
    """(campaign, email or else LinkedIn URL) of everyone the beat finds due now."""

    def read():
        with dispatcher.get_conn() as conn:
            rows = due.due(
                conn, NOW, select='t.campaign_id::text AS cid, t.email, t.linkedin_url', gate_sql=due.GATE_PLAN
            )
        return [(r['cid'], r['email'] or r['linkedin_url']) for r in rows]

    return {person for org in campaign_orgs(c) for person in call_for(org, read)}


def test_active_duplicate_is_held_by_a_colleague_of_the_client_but_not_by_another_client(db):
    """#83 holds per client (#307), whoever owns the campaign that writes first."""
    uid, colleague = _user(db), _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt")'
        " VALUES (default_organization(%s), %s, 'member', now())",
        (uid, colleague),
    )
    client = project(db, uid)
    other_client = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'B') RETURNING id",
        (uid, uid),
    ).fetchone()['id']
    anna = [('anna@client.test', '')]
    holder = _client_campaign(db, colleague, client, anna, armed=NOW - timedelta(hours=2))
    _client_campaign(db, uid, client, anna)
    elsewhere = _client_campaign(db, uid, other_client, anna)

    assert {cid for cid, _ in _due(db)} == {holder, elsewhere}


def test_active_duplicate_is_held_by_a_paused_holder_but_not_a_draft_or_excluded_one(db):
    """A person an out-of-office paused (#346) gets the earlier campaign again on return; a draft or an excluded
    person gets nothing from it."""
    uid = _user(db)
    client = project(db, uid)
    people = [('anna@client.test', ''), ('bea@client.test', ''), ('carl@client.test', '')]
    holder = _client_campaign(db, uid, client, people, armed=NOW - timedelta(hours=2))
    for addr, status in (
        ('anna@client.test', 'paused'),
        ('bea@client.test', 'draft'),
        ('carl@client.test', 'excluded'),
    ):
        db.execute(
            'UPDATE outreach_campaign_targets SET status = %s, paused_until = %s WHERE campaign_id = %s AND email = %s',
            (status, NOW + timedelta(days=3) if status == 'paused' else None, holder, addr),
        )
    later = _client_campaign(db, uid, client, people)

    assert _due(db) == {(later, 'bea@client.test'), (later, 'carl@client.test')}


def test_campaigns_armed_at_the_same_moment_hold_in_id_order(db):
    uid = _user(db)
    client = project(db, uid)
    anna = [('anna@client.test', 'https://linkedin.com/in/anna')]
    both = sorted(_client_campaign(db, uid, client, anna) for _ in range(2))

    assert _due(db) == {(both[0], 'anna@client.test')}


def test_blank_email_or_linkedin_is_nobody_to_hold(db):
    uid = _user(db)
    client = project(db, uid)
    _client_campaign(
        db,
        uid,
        client,
        [('anna@client.test', ' '), ('  ', 'https://linkedin.com/in/bo')],
        armed=NOW - timedelta(hours=2),
    )
    later = _client_campaign(db, uid, client, [('bea@client.test', ''), (None, 'https://linkedin.com/in/cy')])

    assert {person for cid, person in _due(db) if cid == later} == {'bea@client.test', 'https://linkedin.com/in/cy'}


class _Explained:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params):
        return self.conn.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql, params, prepare=False)


def _loops(node: dict, alias: str) -> int:
    own = node['Actual Loops'] if node.get('Alias') == alias else 0
    return own + sum(_loops(child, alias) for child in node.get('Plans', []))


def test_the_due_query_reads_the_hold_only_for_the_people_who_are_due(db):
    """#1141: the hold costs about 20 µs a person, and most active people have their step claimed already or wait
    days for the next one."""
    uid = _user(db)
    cid = _campaign(db, uid, emails=('anna@client.test', 'bea@client.test'))
    assert _each_org(dispatcher.plan, NOW)['approved'] == 2
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status)'
        " VALUES (%s, %s, 'cy@x.test', 'Cy', 'active')",
        (cid, uid),
    )
    _campaign(db, uid, [{**ONE_STEP[0], 'send_after_days': 2}], emails=('dan@x.test', 'eva@x.test'))

    def plan():
        with dispatcher.get_conn() as conn:
            # The join order the planner picks for an agency's 250,000 people: the targets and the campaigns first.
            conn.execute('SET LOCAL join_collapse_limit = 1')
            return due.due(_Explained(conn), NOW, select='t.id', gate_sql=due.GATE_PLAN)[0]['QUERY PLAN'][0]['Plan']

    (org,) = campaign_orgs(db)
    assert _loops(call_for(org, plan), 'outreach_hold') == 1


def test_campaign_completes_once_every_sequence_has_ended(db, webhook_events):
    uid = _user(db)
    cid = _campaign(db, uid, emails=('anna@client.test', 'bea@client.test', 'carl@client.test'))

    def status():
        return db.execute('SELECT status FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()['status']

    def target(email, **cols):
        sets = ', '.join(f'{k} = %s' for k in cols)
        db.execute(
            f'UPDATE outreach_campaign_targets SET {sets} WHERE campaign_id = %s AND email = %s',
            (*cols.values(), cid, email),
        )

    target('anna@client.test', next_step=2)  # past the only step
    target('bea@client.test', status='replied')
    assert _each_org(dispatcher.complete_finished) == 0 and status() == 'active'  # carl is still on step 1

    target('carl@client.test', status='paused', paused_until=NOW)
    assert _each_org(dispatcher.complete_finished) == 0  # a paused person can be resumed

    target('carl@client.test', status='active', paused_until=None)
    carl = db.execute("SELECT id FROM outreach_campaign_targets WHERE email = 'carl@client.test'").fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, attempts,'
        " recipient, error_code, idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'failed', 1, 'carl@client.test',"
        " 'X', 'k')",
        (cid, uid, carl),
    )
    assert _each_org(dispatcher.complete_finished) == 0  # a failure with attempts left is retried

    db.execute("UPDATE outreach_campaign_sends SET status = 'refused' WHERE target_id = %s", (carl,))
    assert _each_org(dispatcher.complete_finished) == 1 and status() == 'completed'  # stuck for good on a refused step
    assert _each_org(dispatcher.complete_finished) == 0  # once
    assert [(p['type'], p['data']) for _, p in webhook_events] == [
        ('campaign.finished', {'campaign_id': str(cid), 'name': 'C'})
    ]


def _instantly_lead(c, uid, email, outcome='accepted'):
    pid, org = project(c, uid), c.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    conn = c.execute(
        "INSERT INTO instantly_connections (organization_id, project_id, name, workspace_id) VALUES (%s, %s, 'x', 'ws')"
        ' RETURNING id',
        (org, pid),
    ).fetchone()['id']
    enrollment = c.execute(
        'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, workspace_id, campaign_id,'
        " status) VALUES (%s, %s, %s, 'ws', 'camp', 'completed') RETURNING id",
        (org, pid, conn),
    ).fetchone()['id']
    c.execute(
        'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,'
        " outcome) VALUES (%s, %s, %s, 'ws', 'camp', %s, %s)",
        (org, pid, enrollment, email, outcome),
    )


def test_a_person_instantly_holds_is_not_planned_and_leaves_the_batch_to_the_others(db, monkeypatch):
    monkeypatch.setattr(due, 'PLAN_BATCH', 1)
    uid = _user(db)
    cid = _campaign(db, uid, emails=('held@x.test',))
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status)'
        " VALUES (%s, %s, 'free@x.test', 'Bea', 'active')",
        (cid, uid),
    )
    _instantly_lead(db, uid, 'held@x.test')
    assert _each_org(dispatcher.plan, NOW)['approved'] == 1
    assert [s['recipient'] for s in _sends(db)] == ['free@x.test']
    assert (
        db.execute("SELECT status FROM outreach_campaign_targets WHERE email = 'held@x.test'").fetchone()['status']
        == 'active'
    )
    # Held, not excluded: the person goes again when Instantly no longer holds them.
    db.execute("UPDATE instantly_leads SET outcome = 'failed'")
    assert _each_org(dispatcher.plan, NOW)['approved'] == 1
    assert sorted(s['recipient'] for s in _sends(db)) == ['free@x.test', 'held@x.test']


async def test_an_approved_email_waits_while_the_hold_keeps_its_person(db, provider, switches):
    uid = _user(db)
    mailbox = _mailbox(db, uid)
    _campaign(db, uid)
    _each_org(dispatcher.plan, NOW)
    _instantly_lead(db, uid, 'anna@client.test')
    assert (await _each_org_async(dispatcher.send_due, NOW)).get('sent', 0) == 0
    send = db.execute(
        'SELECT s.*, c.project_id FROM outreach_campaign_sends s JOIN outreach_campaigns c ON c.id = s.campaign_id'
    ).fetchone()
    mb = db.execute('SELECT * FROM user_oauth_connections WHERE id = %s', (mailbox,)).fetchone()
    with acting_for(str(send['organization_id']), 'campaign beat'):
        assert dispatcher._take_slot(send, mb, 'google', NOW) is False
    assert provider.sent == [] and _sends(db)[0]['status'] == 'approved'
    db.execute("UPDATE instantly_leads SET outcome = 'failed'")
    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 1


async def test_a_bracketed_company_name_is_sent_without_its_brackets(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    _campaign(db, uid, [{**ONE_STEP[0], 'body': 'Ciao {{firstName}} di {{companyName}}'}])
    db.execute("UPDATE outreach_campaign_targets SET company_name = 'Client [EU]'")
    assert (await _beat())['sent'] == 1
    ((msg, _),) = provider.sent
    assert 'Ciao Anna di Client EU' in msg.get_payload(decode=True).decode()
