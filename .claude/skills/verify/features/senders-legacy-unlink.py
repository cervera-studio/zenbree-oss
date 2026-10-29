"""#1099: one pass of the production campaign beat (dispatcher.plan, then dispatcher.send_due) in one organization,
with the provider boundary stubbed: the stack's refresh token is fake (``dispatcher._access_token``) and nothing is
sent over the network (``mailbox_sender.send`` records the message and answers a receipt). The pin, pool and send rules
that run are the real ones. It proves which account a conversation goes on from after a legacy row left.

Stop the beat first, so that only this script plans and sends: ``bin/stack compose stop beat``. Then:
  bin/stack compose cp features/senders-legacy-unlink.py worker:/tmp/senders-legacy-unlink.py
  bin/stack compose exec -T -w /srv -e PYTHONPATH=/srv worker python /tmp/senders-legacy-unlink.py <org id> [days]
``days`` moves the clock forward (default 0), for a step that waits a delay. It prints {plan, sent, messages, sends}:
``messages`` is the From, To, Message-ID, In-Reply-To and References of each message the stub took, ``sends`` the campaign's rows."""

import asyncio
import email
import json
import sys
from datetime import UTC, datetime, timedelta

from app.core.db import get_conn
from app.core.errors import ok
from app.core.mail_threading import ProviderReceipt
from app.core.scope import acting_for
from app.integrations import mailbox_sender
from app.outreach import dispatcher

org = sys.argv[1]
now = datetime.now(UTC) + timedelta(days=float(sys.argv[2]) if len(sys.argv) > 2 else 0)
messages = []


async def token(mb, provider, **kwargs):  # the token boundary
    return 'verify-token'


async def send(provider, *, access_token, raw, thread_id=''):  # the provider boundary
    msg = email.message_from_bytes(raw)
    messages.append(
        {
            'from': msg['From'],
            'to': msg['To'],
            'message_id': msg['Message-ID'],
            'in_reply_to': msg['In-Reply-To'],
            'references': msg['References'],
            'thread': thread_id,
        }
    )
    return ok(
        ProviderReceipt(message_id=f'verify-{len(messages)}', thread_id=thread_id or f'verify-thread-{len(messages)}')
    )


dispatcher._access_token = token
mailbox_sender.send = send

with acting_for(org, 'campaign beat'):
    planned = dispatcher.plan(now)
    sent = asyncio.run(dispatcher.send_due(now))
    with get_conn() as conn:
        sends = conn.execute(
            'SELECT recipient, step, status, sender_id::text AS sender_id FROM outreach_campaign_sends'
            ' ORDER BY recipient, step'
        ).fetchall()
print(json.dumps({'plan': dict(planned), 'sent': dict(sent), 'messages': messages, 'sends': sends}, default=str))
