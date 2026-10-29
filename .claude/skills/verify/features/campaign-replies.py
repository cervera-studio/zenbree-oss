"""#1165: a Gmail push read through app.integrations.mailbox_push.read, as the read_mailbox_bounces task runs it, in the
mailbox's organization, against a stand-in Gmail API on 127.0.0.1 (the provider boundary) and a stand-in access token
(the seeded refresh token is fake). The mailbox and its 20 sends are those of features/campaign-bounces-seed.sql on the
dev seed; this script gives the mailbox the read scope first. The history holds a reply to send
<v5@zenbree.local> from its recipient (In-Reply-To, a passing DMARC verdict), an opt-out in the recipient's own words
of send <v6@...> (also DMARC pass), an unrelated message and two internal messages, which must never be fetched whole.
Two more internal messages drive the signed Resend route in-process with only its content boundary stubbed.
Run round 1, then round 2: the same push delivered again, which must record nothing.

Copy it into the worker and run it there:
  bin/stack compose cp features/campaign-replies.py worker:/tmp/campaign-replies.py
  bin/stack compose exec -T -w /srv -e PYTHONPATH=/srv worker python /tmp/campaign-replies.py <round> <org id> <mailbox id>
It prints {recorded, fetched_whole, stored, targets, step2}: ``stored`` is the inbound messages of the organization,
``targets`` the status of the two people that wrote, ``step2`` the status of a queued (``approved``) second send that
this script adds for the first one: ``skipped`` once the reply stopped them."""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import settings
from app.core.db import get_conn
from app.core.reply_address import build_campaign_reply_address
from app.core.scope import acting_for
from app.integrations import mailbox_fetch, mailbox_push
from app.main import app
from app.outreach import dispatcher, inbox_routes

org, conn_id = sys.argv[2], sys.argv[3]
START = '100'


def send_to(message_id):
    with acting_for(org, 'verify'), get_conn() as conn:
        return conn.execute('SELECT recipient FROM outreach_campaign_sends WHERE message_id = %s', (message_id,)).fetchone()[
            'recipient'
        ]


def mail(sender, mid, subject, text, refs=None):
    head = f'From: {sender}\r\nTo: sales@zenbree.local\r\nSubject: {subject}\r\nMessage-ID: {mid}\r\n'
    head += f'In-Reply-To: {refs}\r\nReferences: {refs}\r\n' if refs else ''
    domain = sender.rpartition('@')[2]  # the verdict names the From's domain, as Gmail's own header does
    head += f'Authentication-Results: mx.google.com; dkim=pass; spf=pass; dmarc=pass (p=NONE) header.from={domain}\r\n'
    return (head + f'Content-Type: text/plain\r\n\r\n{text}\r\n').encode()


ada, bob = send_to('<v5@zenbree.local>'), send_to('<v6@zenbree.local>')
with acting_for(org, 'verify'), get_conn() as conn:
    campaign = conn.execute(
        'SELECT s.campaign_id::text, u.email FROM outreach_campaign_sends s JOIN "user" u ON u.id = s.owner_id'
        " WHERE message_id = '<v7@zenbree.local>'"
    ).fetchone()
owner = campaign['email']
colleague = 'colleague@' + owner.rpartition('@')[2]
internal_ids = [f'<v{i}@zenbree.local>' for i in (7, 8, 9, 10)]
STEP2 = '''INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient, subject, body,
    idempotency_key, message_id, sender_id)
  SELECT campaign_id, owner_id, target_id, 2, 'email', 'approved', recipient, 'Re: Ciao', 'Ti riscrivo', gen_random_uuid()::text,
    replace(message_id, '@', '-step2@'), sender_id FROM outreach_campaign_sends s
  WHERE message_id = ANY(%s)
    AND NOT EXISTS (SELECT 1 FROM outreach_campaign_sends x WHERE x.target_id = s.target_id AND x.step = 2)'''
with acting_for(org, 'verify'), get_conn() as conn:  # a queued second step proves which people the reply stops
    conn.execute(STEP2, (['<v5@zenbree.local>', *internal_ids],))
messages = {
    'reply': mail(ada, '<reply1@zenbree.local>', 'Re: Ciao', 'Volentieri, sentiamoci martedi', '<v5@zenbree.local>'),
    'optout': mail(bob, '<reply2@zenbree.local>', 'Re: Ciao', 'no grazie, non scrivetemi piu', '<v6@zenbree.local>'),
    'other': mail('anna@client.test', '<o1@client.test>', 'hello', 'private text'),
    'owner': mail(owner, '<internal7@zenbree.local>', 'Re: Ciao', 'private internal text', internal_ids[0]),
    'colleague': mail(colleague, '<internal8@zenbree.local>', 'Re: Ciao', 'private internal text', internal_ids[1]),
}
fetched_raw = []


class Gmail(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path.endswith('/history'):
            assert q['startHistoryId'] == [START], q
            records = [{'id': str(int(START) + n + 1), 'messagesAdded': [{'message': {'id': i}}]} for n, i in enumerate(messages)]
            body = {'history': records, 'historyId': '110'}
        else:
            mid = url.path.rsplit('/', 1)[-1]
            raw = messages[mid]
            if q.get('format') == ['raw']:
                fetched_raw.append(mid)
                body = {'raw': base64.urlsafe_b64encode(raw).decode()}
            else:
                headers = [
                    {'name': k, 'value': v.strip()}
                    for k, v in (line.split(':', 1) for line in raw.split(b'\r\n\r\n')[0].decode().split('\r\n'))
                ]
                body = {'payload': {'headers': headers}, 'sizeEstimate': len(raw)}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(out)


server = HTTPServer(('127.0.0.1', 0), Gmail)
threading.Thread(target=server.serve_forever, daemon=True).start()
mailbox_push.GMAIL_API = mailbox_fetch.GMAIL_API = f'http://127.0.0.1:{server.server_port}/gmail/v1/users/me'


async def token(mb, provider):  # the stack's refresh token is fake: the token boundary
    return 'verify-token'


dispatcher._access_token = token


async def resend_internal():
    key = b'0123456789abcdef0123456789abcdef'
    settings.outreach_inbound_webhook_secret = 'whsec_' + base64.b64encode(key).decode()
    settings.outreach_reply_secret = 'verify-reply-secret'
    settings.resend_api_key = 'verify-resend-token'

    async def content(email_id):
        return (
            'Private internal text',
            {'in-reply-to': internal_ids[2 if email_id == 'owner' else 3]},
            {'dmarc': 'pass'},
        )

    inbox_routes._content = content
    address = build_campaign_reply_address(
        campaign['campaign_id'],
        domain='in.zenbree.test',
        secret=settings.outreach_reply_secret,
    )
    results = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://verify') as client:
        for kind, sender in [('owner', owner), ('colleague', colleague)]:
            body = json.dumps(
                {
                    'type': 'email.received',
                    'data': {
                        'email_id': kind,
                        'from': sender,
                        'to': [address],
                        'subject': 'Re: Ciao',
                    },
                }
            ).encode()
            ts = str(int(time.time()))
            signature = base64.b64encode(
                hmac.new(key, f'{kind}.{ts}.'.encode() + body, hashlib.sha256).digest()
            ).decode()
            response = await client.post(
                '/v1/inbound/email',
                content=body,
                headers={
                    'svix-id': kind,
                    'svix-timestamp': ts,
                    'svix-signature': 'v1,' + signature,
                },
            )
            assert response.status_code == 200, response.text
            results[kind] = response.json()
            assert results[kind] == {'status': 'ignored'}, results
    return results


resend = asyncio.run(resend_internal())

with acting_for(org, 'mailbox push'):
    with get_conn() as conn:  # the push's start: the history id the mailbox held, with the read grant it needs
        conn.execute(
            'UPDATE user_oauth_connections SET inbound_history_id = %s,'
            " granted_scopes = ARRAY(SELECT DISTINCT unnest(granted_scopes || '{https://www.googleapis.com/auth/gmail.readonly}'))"
            ' WHERE id = %s',
            (START, conn_id),
        )
    recorded = asyncio.run(mailbox_push.read(conn_id, []))
    with get_conn() as conn:
        stored = conn.execute('SELECT count(*) AS n FROM outreach_inbound_messages').fetchone()['n']
        step2 = conn.execute("SELECT status FROM outreach_campaign_sends WHERE message_id = '<v5-step2@zenbree.local>'").fetchone()
        targets = conn.execute(
            'SELECT email, status FROM outreach_campaign_targets WHERE email = ANY(%s) ORDER BY email', ([ada, bob],)
        ).fetchall()
        internal = conn.execute(
            'SELECT s.message_id, t.status AS target_status, s.status AS send_status FROM outreach_campaign_sends s'
            ' JOIN outreach_campaign_targets t ON t.id = s.target_id WHERE s.step = 2 AND t.id IN'
            ' (SELECT target_id FROM outreach_campaign_sends WHERE message_id = ANY(%s)) ORDER BY s.message_id',
            (internal_ids,),
        ).fetchall()
        internal_stored = conn.execute(
            'SELECT count(*) AS n FROM outreach_inbound_messages WHERE from_address = ANY(%s)',
            ([owner, colleague],),
        ).fetchone()['n']
assert set(fetched_raw) == {'reply', 'optout'}, fetched_raw
assert len(internal) == 4 and all(
    r['target_status'] == 'active' and r['send_status'] == 'approved' for r in internal
), internal
assert internal_stored == 0, internal_stored
print(
    json.dumps(
        {
            'recorded': recorded,
            'fetched_whole': fetched_raw,
            'stored': stored,
            'targets': targets,
            'step2': step2['status'],
            'internal': internal,
            'internal_stored': internal_stored,
            'resend': resend,
        }
    )
)
