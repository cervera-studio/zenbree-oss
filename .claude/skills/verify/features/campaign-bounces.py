"""#747: a Gmail push read through app.integrations.mailbox_push.read, as the read_mailbox_bounces task runs it, in the
mailbox's organization, against a stand-in Gmail API on 127.0.0.1 (the provider boundary) and a stand-in access token
(the seeded refresh token is fake). Round 1: a hard bounce of <v1@zenbree.local>, a soft one of <v2@...>, an
ordinary message that must never be fetched whole, and a forged hard bounce of <v4@...> (not from Gmail's bounce
sender) that must change nothing. Round 2: a hard bounce of <v3@...>, the second of 20 sends: the
campaign pauses. The recipients are those of features/campaign-bounces-seed.sql on the dev seed.

Copy it into the worker and run it there:
  bin/stack compose cp features/campaign-bounces.py worker:/tmp/campaign-bounces.py
  bin/stack compose exec -T -w /srv -e PYTHONPATH=/srv worker python /tmp/campaign-bounces.py <round> <org id> <mailbox id> [verdict]
It prints {recorded, fetched_whole, cursor}."""

import asyncio
import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from app.core.db import get_conn
from app.core.scope import acting_for
from app.integrations import mailbox_fetch, mailbox_push
from app.outreach import dispatcher


def dsn(recipient, message_id, action='failed', status='5.1.1'):
    return (
        'From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\nTo: sales@zenbree.local\r\n'
        'Subject: Delivery Status Notification (Failure)\r\nMIME-Version: 1.0\r\n'
        'Content-Type: multipart/report; boundary="b1"; report-type=delivery-status\r\n\r\n'
        '--b1\r\nContent-Type: text/plain\r\n\r\nAddress not found\r\n'
        '--b1\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; googlemail.com\r\n\r\n'
        f'Final-Recipient: rfc822; {recipient}\r\nAction: {action}\r\nStatus: {status}\r\n\r\n'
        f'--b1\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: {message_id}\r\nSubject: Ciao\r\n\r\n--b1--\r\n'
    ).encode()


org, conn_id = sys.argv[2], sys.argv[3]


def to(message_id):
    with acting_for(org, 'verify'), get_conn() as conn:
        return conn.execute('SELECT recipient FROM outreach_campaign_sends WHERE message_id = %s', (message_id,)).fetchone()[
            'recipient'
        ]


DAEMON = 'mailer-daemon@googlemail.com'
ROUNDS = {  # (start history id, Gmail's current one, the messages added)
    '1': lambda: ('100', '110', {
        'd1': (DAEMON, dsn(to('<v1@zenbree.local>'), '<v1@zenbree.local>')),
        'd2': (DAEMON, dsn(to('<v2@zenbree.local>'), '<v2@zenbree.local>', 'delayed', '4.4.7')),
        'm3': ('anna@client.test', b'From: anna@client.test\r\nSubject: hello\r\n\r\nprivate text\r\n'),
        # a forged report of a real send: fetched (its headers look like a bounce), refused, never recorded
        'f4': (DAEMON, dsn(to('<v4@zenbree.local>'), '<v4@zenbree.local>').replace(b'googlemail.com>', b'attacker.test>', 1)),
    }),
    '2': lambda: ('110', '120', {'d3': (DAEMON, dsn(to('<v3@zenbree.local>'), '<v3@zenbree.local>'))}),
}
start, current, messages = ROUNDS[sys.argv[1]]()
fetched_raw = []


class Gmail(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path.endswith('/history'):
            assert q['startHistoryId'] == [start], q
            records = [{'id': str(int(start) + n + 1), 'messagesAdded': [{'message': {'id': i}}]} for n, i in enumerate(messages)]
            body = {'history': records, 'historyId': current}
        else:
            mid = url.path.rsplit('/', 1)[-1]
            sender, raw = messages[mid]
            if q.get('format') == ['raw']:
                fetched_raw.append(mid)
                body = {'raw': base64.urlsafe_b64encode(raw).decode()}
            else:
                body = {'payload': {'headers': [{'name': 'From', 'value': sender}]}}
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


# The verify stack has no MillionVerifier key: the refund's re-verification (owner decision on #919) answers here,
# at the vendor boundary. Fourth argument: its verdict, default invalid (refund); ok, catch_all or none refund nothing.
VERDICT = sys.argv[4] if len(sys.argv) > 4 else 'invalid'


async def verdict(email, **kw):
    return None if VERDICT == 'none' else VERDICT


mailbox_push.millionverifier_status = verdict

with acting_for(org, 'mailbox push'):
    recorded = asyncio.run(mailbox_push.read(conn_id, []))
    with get_conn() as conn:
        cursor = conn.execute('SELECT inbound_history_id FROM user_oauth_connections WHERE id = %s', (conn_id,)).fetchone()
print(json.dumps({'recorded': recorded, 'fetched_whole': fetched_raw, 'cursor': cursor['inbound_history_id']}))
