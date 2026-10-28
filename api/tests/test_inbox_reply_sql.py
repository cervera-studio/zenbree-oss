"""Answering a reply from the inbox (#187) against real SQL: thread, read state, counts, the send through
the mailbox or LinkedIn under caps, kill switches, exclusions and scoping, and the agent's draft.

Needs TEST_DATABASE_URL. The mailbox, Unipile and the LLM are faked.
"""

import asyncio
import contextlib
import contextvars
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import err, ok
from app.core.reply_address import ReplyKey, parse_reply_address
from app.integrations import project_senders
from app.llm.base import ProviderAPIError
from app.main import app
from app.outreach import dispatcher, draft_store, inbox_reply, sender_pool, sender_slot
from tests import test_dispatcher_sql, test_replies_sql
from tests.conftest import act_for, call_as, conninfo_of, member, pool, project, scoped_conn, sender, sign_in
from tests.test_dispatcher_sql import _mailbox
from tests.test_replies_sql import DB_URL, QUEUED, _campaign, _post
from tests.test_tools_sql import _user

# The fixtures those modules define, under the same names.
db = test_replies_sql.db
provider = test_dispatcher_sql.provider

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: inbox reply SQL test NOT run')


@pytest.fixture
def owner(db, provider, monkeypatch):
    fake = scoped_conn(db)
    for module in (dispatcher, inbox_reply, sender_slot):
        monkeypatch.setattr(module, 'get_conn', fake)
    monkeypatch.setattr(settings, 'outreach_reply_secret', 'reply-secret')  # the provider fixture sets its own
    off: set[str] = set()
    monkeypatch.setattr(inbox_reply, 'is_disabled', lambda key: key in off)
    uid = _user(db)
    camp = _campaign(db, uid)
    db.execute("UPDATE outreach_campaign_targets SET role = 'CEO'")
    _mailbox(db, uid)
    pool(db, providers=('google',))
    sign_in(app, member(db, uid))
    client = TestClient(app)
    res = _post(
        client,
        uid,
        email_id='r1',
        text='Volentieri, giovedì?',
        headers={'Message-ID': '<their-1@client.test>', 'In-Reply-To': camp['message_id']},
    )
    return {'uid': uid, 'client': client, 'id': res.json()['id'], 'camp': camp, 'off': off, 'sent': provider.sent}


def test_thread_read_state_and_counts(owner, db):
    client, mid = owner['client'], owner['id']
    page = client.get('/v1/inbox').json()
    assert page['counts']['all'] == 1 and page['counts']['unread'] == 1 and page['counts']['none'] == 1
    assert page['messages'][0]['read_at'] is None and page['messages'][0]['target_role'] == 'CEO'
    assert client.get('/v1/inbox?unread=true').json()['messages'][0]['id'] == mid
    assert client.get('/v1/inbox?kind=unsubscribe').json()['messages'] == []

    peek = client.get(f'/v1/inbox/{mid}?mark_read=false').json()
    assert peek['message']['read_at'] is None
    thread = client.get(f'/v1/inbox/{mid}').json()
    assert thread['message']['read_at'] is not None
    assert [(s['step'], s['subject']) for s in thread['sent']] == [(1, 'Ciao')]  # step 2 never went out
    assert thread['reply_from'] == 'me@acme.test' and thread['reply_blocked'] is None
    assert client.get('/v1/inbox').json()['counts']['unread'] == 0

    stranger = _user(db)
    sign_in(app, member(db, stranger))
    assert client.get(f'/v1/inbox/{mid}').status_code == 404
    assert client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao'}).status_code == 404
    assert owner['sent'] == []


def test_email_answer_threads_under_their_message_and_counts_against_the_cap(owner, db):
    client, mid = owner['client'], owner['id']
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Giovedì alle 11 va bene.'})
    assert res.status_code == 200 and res.json()['status'] == 'sent'
    msg, thread_id = owner['sent'][0]
    assert msg['To'] == 'anna@client.test' and msg['Subject'] == 'Re: Ciao'
    assert msg['In-Reply-To'] == '<their-1@client.test>'
    assert msg['References'].split() == [owner['camp']['message_id'], '<their-1@client.test>']
    assert parse_reply_address(msg['Reply-To'], secret='reply-secret') == ReplyKey(
        str(owner['camp']['campaign']), campaign=True
    )
    assert msg.get_payload(decode=True).decode().rstrip().endswith('Marco')  # sign-off
    assert thread_id == ''  # the seeded campaign send has no Gmail handle
    now = datetime.now(UTC)
    with contextlib.nullcontext(db) as conn:
        box = conn.execute("SELECT id FROM user_oauth_connections WHERE provider = 'google'").fetchone()['id']
        assert dispatcher.sending.sent_today(conn, box, now, 'Europe/Rome') == 1
    replies = client.get(f'/v1/inbox/{mid}').json()['replies']
    assert [(r['status'], r['body']) for r in replies] == [('sent', 'Giovedì alle 11 va bene.')]

    db.execute('UPDATE user_oauth_connections SET daily_limit = 1')
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ancora io'})
    assert res.status_code == 429 and res.json()['detail']['code'] == 'INBOX.MAILBOX_CAPPED'
    assert len(owner['sent']) == 1


def test_no_answer_to_an_opt_out_an_excluded_person_or_a_paused_channel(owner, db):
    client, mid = owner['client'], owner['id']
    owner['off'].add('channel_disabled:email')
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao'})
    assert res.status_code == 503 and res.json()['detail']['code'] == 'INBOX.CHANNEL_DISABLED'
    owner['off'].clear()
    assert client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao [Nome]'}).status_code == 422

    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'client.test')",
        (project(db, owner['uid']), owner['uid']),
    )
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao'})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'INBOX.EXCLUDED'
    assert client.get(f'/v1/inbox/{mid}').json()['reply_blocked'] == 'INBOX.EXCLUDED'

    out = _post(client, owner['uid'], email_id='r2', sender='bea@other.test', subject='no grazie', text='no grazie')
    stop = out.json()['id']
    res = client.post(f'/v1/inbox/{stop}/reply', json={'body': 'Peccato'})
    assert res.status_code == 403 and res.json()['detail']['code'] == 'INBOX.UNSUBSCRIBED'
    assert client.get(f'/v1/inbox/{stop}').json()['reply_blocked'] == 'INBOX.UNSUBSCRIBED'
    assert client.get('/v1/inbox?kind=unsubscribe').json()['messages'][0]['id'] == stop
    assert db.execute("SELECT count(*) AS n FROM exclusions WHERE value = 'bea@other.test'").fetchone()['n'] == 1
    assert owner['sent'] == [] and db.execute('SELECT count(*) AS n FROM outreach_inbox_replies').fetchone()['n'] == 0


def test_an_unconfirmed_send_is_kept_as_unknown(owner, provider, db):
    provider.result = err('INTEGRATIONS.TRANSPORT', detail='x', delivery_ambiguous=True)
    res = owner['client'].post(f'/v1/inbox/{owner["id"]}/reply', json={'body': 'Ciao'})
    assert res.status_code == 502 and res.json()['detail']['code'] == 'INBOX.SEND_UNKNOWN'
    assert db.execute('SELECT status FROM outreach_inbox_replies').fetchone()['status'] == 'unknown'


def test_linkedin_answer_is_a_dm_under_the_daily_dm_cap(db, monkeypatch):
    for module in (dispatcher, inbox_reply):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(inbox_reply, 'is_disabled', lambda key: False)
    dms: list[dict] = []

    class Unipile:
        def is_configured(self):
            return True

        async def send_dm(self, **kw):
            dms.append(kw)
            return ok({'chat_id': 'chat-1'})

    monkeypatch.setattr(inbox_reply, 'UnipileClient', Unipile)
    uid = _user(db)
    _campaign(db, uid, email=None, linkedin_url='https://www.linkedin.com/in/anna', provider_id='ANNA')
    sender(db, uid, 'unipile', provider_account_id='acc_1')
    payload = {
        'event': 'message_received',
        'account_id': 'acc_1',
        'account_info': {'user_id': 'ME'},
        'message_id': 'li-1',
        'message': 'Sì, sentiamoci',
        'sender': {'attendee_provider_id': 'ANNA', 'attendee_profile_url': 'https://www.linkedin.com/in/anna'},
    }
    client = TestClient(app)
    mid = client.post('/v1/integrations/linkedin/webhook', json=payload, headers={'Unipile-Auth': 'uni-secret'}).json()[
        'id'
    ]
    sign_in(app, member(db, uid))
    assert client.get(f'/v1/inbox/{mid}').json()['reply_from'] == 'linkedin'
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ottimo, ti scrivo domani.'})
    assert res.status_code == 200 and res.json()['status'] == 'sent'
    assert dms == [{'account_id': 'acc_1', 'provider_id': 'ANNA', 'text': 'Ottimo, ti scrivo domani.'}]
    db.execute(
        """INSERT INTO outreach_inbox_replies (owner_id, inbound_id, channel, sender_provider, sender_id, recipient,
               body, status, delivered_at)
           SELECT %s, %s, 'linkedin', 'unipile', (SELECT id FROM user_oauth_connections WHERE provider = 'unipile'),
                  'x', 'y', 'sent', now() FROM generate_series(1, 29)""",
        (uid, mid),
    )
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ancora'})
    assert res.status_code == 429 and len(dms) == 1

    # The person's email on the target excludes them on LinkedIn too.
    db.execute("UPDATE outreach_campaign_targets SET email = 'anna@client.test'")
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'email', 'anna@client.test')",
        (project(db, uid), uid),
    )
    assert client.get(f'/v1/inbox/{mid}').json()['reply_blocked'] == 'INBOX.EXCLUDED'
    db.execute('DELETE FROM exclusions; UPDATE user_oauth_connections SET paused = true')
    assert client.get(f'/v1/inbox/{mid}').json()['reply_blocked'] == 'INBOX.ACCOUNT_PAUSED'


async def test_the_agent_drafts_an_answer_once_and_never_for_a_decline(owner, db, monkeypatch):
    prompts: list[str] = []

    class Llm:
        async def chat(self, **kw):
            prompts.append(kw['messages'][0]['content'])
            return type('R', (), {'text': json.dumps({'body': 'Giovedì alle 11 va benissimo.'})})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Llm())
    mid = owner['id']
    assert [mid] == QUEUED
    act_for(member(db, owner['uid']).org_id, 'draft reply')  # the draft task, an organization task
    assert await inbox_reply.draft(mid) is False  # not labelled yet
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested'")
    assert await inbox_reply.draft(mid) is True
    assert await inbox_reply.draft(mid) is False  # once
    assert 'Volentieri, giovedì?' in prompts[0] and len(prompts) == 1
    assert owner['client'].get(f'/v1/inbox/{mid}').json()['draft'] == 'Giovedì alle 11 va benissimo.'
    db.execute("UPDATE outreach_inbound_messages SET label = 'not_interested', draft_body = NULL")
    assert await inbox_reply.draft(mid) is False


def test_a_paused_mailbox_sends_no_answer(owner, db):
    db.execute('UPDATE user_oauth_connections SET paused = true')
    client, mid = owner['client'], owner['id']
    assert client.get(f'/v1/inbox/{mid}').json()['reply_blocked'] == 'INBOX.ACCOUNT_PAUSED'
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao'})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'INBOX.ACCOUNT_PAUSED'
    assert owner['sent'] == []


def test_a_stuck_claim_does_not_hold_the_thread(owner, db):
    client, mid = owner['client'], owner['id']
    db.execute(
        """INSERT INTO outreach_inbox_replies (owner_id, inbound_id, channel, sender_provider, recipient, body,
               created_at) VALUES (%s, %s, 'email', 'google', 'anna@client.test', 'prima', now() - interval '1 minute')""",
        (owner['uid'], mid),
    )
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao'})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'INBOX.REPLY_IN_PROGRESS'  # still fresh
    db.execute("UPDATE outreach_inbox_replies SET created_at = now() - interval '6 minutes'")
    assert client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Ciao'}).json()['status'] == 'sent'
    rows = db.execute('SELECT body, status, error_code FROM outreach_inbox_replies ORDER BY created_at').fetchall()
    assert [(r['body'], r['status'], r['error_code']) for r in rows] == [
        ('prima', 'unknown', 'STALE_CLAIM'),
        ('Ciao', 'sent', None),
    ]


def test_a_provider_outage_asks_to_retry_and_keeps_the_mailbox_connected(owner, db, monkeypatch):
    class Down:
        def __init__(self, provider):
            pass

        async def refresh(self, token):
            return err('INTEGRATIONS.HTTP_ERROR', detail='503')

    monkeypatch.setattr(dispatcher, 'OAuthClient', Down)
    res = owner['client'].post(f'/v1/inbox/{owner["id"]}/reply', json={'body': 'Ciao'})
    assert (res.status_code, res.json()['detail']['code']) == (503, 'MAILBOX.TEMPORARILY_UNAVAILABLE')
    assert db.execute('SELECT status FROM user_oauth_connections').fetchone()['status'] == 'connected'
    assert owner['sent'] == []


def test_an_answer_to_a_message_no_campaign_matched_carries_the_old_owner_address(owner, db):
    """The only path left that mints the old form; it goes with the old form's parse (``sending.reply_to``)."""
    db.execute('UPDATE outreach_inbound_messages SET campaign_id = NULL WHERE id = %s', (owner['id'],))
    res = owner['client'].post(f'/v1/inbox/{owner["id"]}/reply', json={'body': 'Giovedì alle 11 va bene.'})
    assert res.status_code == 200 and res.json()['status'] == 'sent'
    msg, _ = owner['sent'][0]
    assert parse_reply_address(msg['Reply-To'], secret='reply-secret') == ReplyKey(owner['uid'], campaign=False)


def test_a_member_regenerates_the_draft_metered_to_them_and_not_for_another_organization(owner, db, monkeypatch):
    texts = iter(['Prima bozza.', 'Seconda bozza.'])
    seen: list = []

    class Llm:
        async def chat(self, **kw):
            return type('R', (), {'text': json.dumps({'body': next(texts)})})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Llm())
    monkeypatch.setattr(inbox_reply, 'set_metering_context', seen.append)
    client, mid = owner['client'], owner['id']
    assert client.post(f'/v1/inbox/{mid}/draft').json()['detail']['code'] == 'INBOX.NO_DRAFT'  # not labelled yet
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested'")
    assert client.post(f'/v1/inbox/{mid}/draft').json() == {'draft': 'Prima bozza.'}
    assert client.post(f'/v1/inbox/{mid}/draft').json() == {'draft': 'Seconda bozza.'}  # overwritten
    assert client.get(f'/v1/inbox/{mid}').json()['draft'] == 'Seconda bozza.'
    assert [(c.user_id, c.endpoint) for c in seen] == [(owner['uid'], 'reply_draft')] * 2
    sign_in(app, member(db, _user(db)))
    res = client.post(f'/v1/inbox/{mid}/draft')
    assert res.status_code == 404 and res.json()['detail']['code'] == 'INBOX.NOT_FOUND'
    assert db.execute('SELECT draft_body FROM outreach_inbound_messages').fetchone()['draft_body'] == 'Seconda bozza.'


def test_a_sent_answer_clears_its_draft_and_a_refused_or_unconfirmed_one_keeps_it(owner, provider, db):
    client, mid = owner['client'], owner['id']
    other = _post(client, owner['uid'], email_id='r2', text='E il prezzo?').json()['id']
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested', draft_body = 'Bozza ' || id")
    draft, delivered = f'Bozza {mid}', provider.result

    provider.result = err('INTEGRATIONS.HTTP_ERROR', detail='550')
    assert client.post(f'/v1/inbox/{mid}/reply', json={'body': draft}).json()['detail']['code'] == 'INBOX.SEND_FAILED'
    assert client.get(f'/v1/inbox/{mid}').json()['draft'] == draft
    provider.result = err('INTEGRATIONS.TRANSPORT', detail='x', delivery_ambiguous=True)
    assert client.post(f'/v1/inbox/{mid}/reply', json={'body': draft}).json()['detail']['code'] == 'INBOX.SEND_UNKNOWN'
    assert client.get(f'/v1/inbox/{mid}').json()['draft'] == draft

    provider.result = delivered
    assert client.post(f'/v1/inbox/{mid}/reply', json={'body': draft}).json()['status'] == 'sent'
    assert client.get(f'/v1/inbox/{mid}').json()['draft'] is None
    rows = db.execute('SELECT id, draft_body FROM outreach_inbound_messages').fetchall()
    assert {str(r['id']): r['draft_body'] for r in rows} == {mid: None, other: f'Bozza {other}'}


def _member_answers_while_the_model_writes(db, owner) -> None:
    db.execute(
        """INSERT INTO outreach_inbox_replies (owner_id, inbound_id, channel, sender_provider, recipient, body, status,
               delivered_at) VALUES (%s, %s, 'email', 'google', 'anna@client.test', 'Sì.', 'sent', now())""",
        (owner['uid'], owner['id']),
    )


async def test_an_answered_message_gets_no_agent_draft_until_a_member_asks(owner, db, monkeypatch):
    calls: list[str] = []

    class Llm:
        async def chat(self, **kw):
            calls.append('draft')
            if len(calls) == 1:
                _member_answers_while_the_model_writes(db, owner)
            return type('R', (), {'text': json.dumps({'body': f'Bozza {len(calls)}.'})})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Llm())
    client, mid = owner['client'], owner['id']
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested'")
    act_for(member(db, owner['uid']).org_id, 'draft reply')
    await inbox_reply.draft(mid)
    assert db.execute('SELECT draft_body FROM outreach_inbound_messages').fetchone()['draft_body'] is None
    assert await inbox_reply.draft(mid) is False and len(calls) == 1
    assert client.post(f'/v1/inbox/{mid}/draft').json() == {'draft': 'Bozza 2.'}


def _until_it_waits_on_a_lock(db, write) -> None:
    wait = "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    while not db.execute(wait).fetchone():
        assert not write.done(), 'the draft write never waited on the settle'


def test_a_draft_write_that_waits_on_the_settle_writes_nothing_after_the_sent_answer(owner, db, monkeypatch):
    mid = owner['id']
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested'")
    reply = db.execute(
        """INSERT INTO outreach_inbox_replies (owner_id, inbound_id, channel, sender_provider, recipient, body)
           VALUES (%s, %s, 'email', 'google', 'anna@client.test', 'Sì.') RETURNING id""",
        (owner['uid'], mid),
    ).fetchone()['id']
    act_for(member(db, owner['uid']).org_id, 'draft reply')
    settle = psycopg.connect(conninfo_of(db))
    monkeypatch.setattr(inbox_reply, 'get_conn', lambda timeout=None: contextlib.nullcontext(settle))
    try:
        inbox_reply._settle(reply, 'sent')
        with ThreadPoolExecutor(1) as pool:
            write = pool.submit(contextvars.copy_context().run, draft_store.store, mid, 'Bozza.', False)
            _until_it_waits_on_a_lock(db, write)
            settle.commit()
            write.result()
    finally:
        settle.close()
    assert db.execute('SELECT draft_body FROM outreach_inbound_messages').fetchone()['draft_body'] is None


def test_the_list_sorts_oldest_first_on_request(owner, db):
    _post(owner['client'], owner['uid'], email_id='r2', text='secondo', headers={'Message-ID': '<their-2@client.test>'})
    db.execute(
        "UPDATE outreach_inbound_messages SET received_at = now() - interval '1 day' WHERE body LIKE 'Volentieri%'"
    )
    client = owner['client']
    newest = [m['body'] for m in client.get('/v1/inbox').json()['messages']]
    assert [m['body'] for m in client.get('/v1/inbox?sort=oldest').json()['messages']] == newest[::-1]


def test_a_bad_model_answer_or_an_outage_keeps_the_old_draft(owner, db, monkeypatch):
    answers = iter([json.dumps({'body': 'Buona.'}), 'not json', ProviderAPIError('down', status_code=503)])

    class Llm:
        async def chat(self, **kw):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return type('R', (), {'text': a})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Llm())
    client, mid = owner['client'], owner['id']
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested'")
    assert client.post(f'/v1/inbox/{mid}/draft').json() == {'draft': 'Buona.'}
    bad = client.post(f'/v1/inbox/{mid}/draft')
    assert bad.status_code == 409 and bad.json()['detail']['code'] == 'INBOX.NO_DRAFT'
    down = client.post(f'/v1/inbox/{mid}/draft')
    assert down.status_code == 502 and down.json()['detail']['code'] == 'INBOX.LLM_FAILED'
    assert db.execute('SELECT draft_body FROM outreach_inbound_messages').fetchone()['draft_body'] == 'Buona.'


def test_a_slow_model_times_out_and_keeps_the_old_draft(owner, db, monkeypatch):
    class Slow:
        async def chat(self, **kw):
            await asyncio.sleep(5)
            return type('R', (), {'text': json.dumps({'body': 'Troppo tardi.'})})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Slow())
    monkeypatch.setattr('app.outreach.inbox_routes.DRAFT_TIMEOUT_S', 0.1)
    client, mid = owner['client'], owner['id']
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested', draft_body = 'Vecchia.'")
    res = client.post(f'/v1/inbox/{mid}/draft')
    assert res.status_code == 502 and res.json()['detail']['code'] == 'INBOX.LLM_FAILED'
    assert db.execute('SELECT draft_body FROM outreach_inbound_messages').fetchone()['draft_body'] == 'Vecchia.'


def test_draft_regeneration_is_limited_per_user_and_holds_no_threadpool_slot(owner, db, monkeypatch):
    from app.core import rate_limit
    from app.outreach import inbox_routes

    counts: dict = {}

    class Counter:
        def eval(self, script, n, key, seconds):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

    class Llm:
        async def chat(self, **kw):
            return type('R', (), {'text': json.dumps({'body': 'Ok.'})})()

    monkeypatch.setattr(rate_limit, '_redis', Counter)
    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Llm())
    assert inspect.iscoroutinefunction(inbox_routes.regenerate_draft)  # awaits the model on the loop
    client, mid = owner['client'], owner['id']
    db.execute("UPDATE outreach_inbound_messages SET label = 'interested'")
    for _ in range(10):
        assert client.post(f'/v1/inbox/{mid}/draft').status_code == 200
    res = client.post(f'/v1/inbox/{mid}/draft')
    assert res.status_code == 429 and res.json()['detail']['code'] == 'INBOX.RATE_LIMITED'
    assert res.headers['retry-after'] == '60'


def test_a_competing_claim_during_the_oauth_round_trip_takes_the_last_allowance(owner, db, monkeypatch):
    """#987: the early cap read passes for both answers; the claim recounts under the mailbox row lock."""
    db.execute('UPDATE user_oauth_connections SET daily_limit = 1')
    client, mid = owner['client'], owner['id']
    other = _post(
        client,
        owner['uid'],
        email_id='r2',
        text='Anche io, giovedì?',
        headers={'Message-ID': '<their-2@client.test>', 'In-Reply-To': owner['camp']['message_id']},
    ).json()['id']  # a second message: the per-message index cannot be what stops the first answer

    async def token_while_another_claims(box, provider, **kw):
        db.execute(
            """INSERT INTO outreach_inbox_replies (owner_id, inbound_id, channel, sender_provider, sender_id,
                   recipient, body, status) VALUES (%s, %s, 'email', 'google', %s, 'x@y.test', 'b', 'sending')""",
            (owner['uid'], other, box['id']),
        )
        return 'fresh-token'

    monkeypatch.setattr(dispatcher, '_access_token', token_while_another_claims)
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Giovedì'})
    assert res.status_code == 429 and res.json()['detail']['code'] == 'INBOX.MAILBOX_CAPPED'
    assert owner['sent'] == []


def test_a_campaign_send_cannot_take_the_slot_of_a_mailbox_at_its_cap(owner, db):
    """#987: the slot re-counts the cap, replies included, under the lock the slot update took."""
    db.execute('UPDATE user_oauth_connections SET daily_limit = 1')
    mb = db.execute("SELECT * FROM user_oauth_connections WHERE provider = 'google'").fetchone()
    send = db.execute("SELECT * FROM outreach_campaign_sends WHERE channel = 'email' ORDER BY step DESC").fetchone()
    db.execute("UPDATE outreach_campaign_sends SET status = 'approved' WHERE id = %s", (send['id'],))
    db.execute("UPDATE outreach_campaign_targets SET next_step = %s, status = 'active'", (send['step'],))
    send = {**send, 'status': 'approved'}
    now = datetime.now(UTC)
    act_for(member(db, owner['uid']).org_id, 'campaign beat')
    db.execute(
        """INSERT INTO outreach_inbox_replies (owner_id, inbound_id, channel, sender_provider, sender_id,
               recipient, body, status) VALUES (%s, %s, 'email', 'google', %s, 'x@y.test', 'b', 'sending')""",
        (owner['uid'], owner['id'], mb['id']),
    )
    assert dispatcher._take_slot(send, mb, 'google', now) is False
    assert (
        db.execute('SELECT status FROM outreach_campaign_sends WHERE id = %s', (send['id'],)).fetchone()['status']
        == 'approved'
    )
    db.execute('UPDATE user_oauth_connections SET daily_limit = 5')
    assert dispatcher._take_slot(send, mb, 'google', now) is True


def test_the_cap_count_holds_the_mailbox_row_until_the_claim_commits(owner, db):
    """#987: a second connection (a campaign slot update, another answer) waits for the first claim to commit."""
    from app.outreach import sending

    box = db.execute("SELECT id FROM user_oauth_connections WHERE provider = 'google'").fetchone()['id']
    act_for(member(db, owner['uid']).org_id, 'campaign beat')
    other = psycopg.connect(conninfo_of(db), autocommit=True)
    other.execute("SET lock_timeout = '200ms'")
    try:
        with db.transaction():
            assert sending.at_cap(db, 'email', box, datetime.now(UTC)) is False
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute('UPDATE user_oauth_connections SET last_used_at = now() WHERE id = %s', (box,))
        other.execute('UPDATE user_oauth_connections SET last_used_at = now() WHERE id = %s', (box,))
    finally:
        other.close()


def test_an_unlinked_mailbox_cannot_take_a_campaign_slot(owner, db, monkeypatch):
    """#985: the queue was picked while the mailbox served the project; an unlink that commits before the claim wins."""
    mb = db.execute("SELECT * FROM user_oauth_connections WHERE provider = 'google'").fetchone()
    send = db.execute("SELECT * FROM outreach_campaign_sends WHERE channel = 'email' ORDER BY step DESC").fetchone()
    db.execute("UPDATE outreach_campaign_sends SET status = 'approved' WHERE id = %s", (send['id'],))
    db.execute("UPDATE outreach_campaign_targets SET next_step = %s, status = 'active'", (send['step'],))
    act_for(member(db, owner['uid']).org_id, 'campaign beat')
    link = db.execute('DELETE FROM project_senders WHERE sender_id = %s RETURNING *', (mb['id'],)).fetchone()
    assert dispatcher._take_slot(send, mb, 'google', datetime.now(UTC)) is False
    status = db.execute('SELECT status FROM outreach_campaign_sends WHERE id = %s', (send['id'],)).fetchone()['status']
    assert status == 'approved'
    monkeypatch.setattr(project_senders, 'get_conn', scoped_conn(db))
    user = member(db, owner['uid'])
    assert call_as(user, project_senders.link, user, mb['id'], link['project_id']).is_ok()
    sender_pool.pick(db, send['campaign_id'], [mb['id']])  # a link joins no pool by itself: a person picks it
    assert dispatcher._take_slot(send, mb, 'google', datetime.now(UTC)) is True


def test_an_answer_is_refused_when_the_mailbox_is_unlinked_during_the_oauth_round_trip(owner, db, monkeypatch):
    """#985: the reply was routed while the mailbox served the project; the claim rechecks the link."""
    client, mid = owner['client'], owner['id']

    async def token_while_unlinked(box, provider, **kw):
        db.execute('DELETE FROM project_senders WHERE sender_id = %s', (box['id'],))
        return 'fresh-token'

    monkeypatch.setattr(dispatcher, '_access_token', token_while_unlinked)
    res = client.post(f'/v1/inbox/{mid}/reply', json={'body': 'Giovedì'})
    assert res.status_code == 409 and res.json()['detail']['code'] == 'INBOX.NO_MAILBOX'
    assert owner['sent'] == []
    assert db.execute('SELECT count(*) AS n FROM outreach_inbox_replies').fetchone()['n'] == 0
