"""Send one message through a connected mailbox. Port of the earlier implementation ``integrations/adapters/mailbox_sender.py``.

Both providers take the whole RFC 822 message, so every header we need is set the
same way: ``Message-ID`` / ``In-Reply-To`` / ``References`` for threading
(``app.core.mail_threading``), the signed ``Reply-To`` and ``List-Unsubscribe``
(#75, one-click #172). Graph's JSON message object only accepts ``x-`` headers, which is why we
use its MIME form rather than the MAPI-property workaround the earlier implementation needed.

Error codes:
- INTEGRATIONS.AUTH          401/403: token rejected or send scope revoked
- INTEGRATIONS.RATE_LIMITED  429, and Gmail's 403 rate variant
- INTEGRATIONS.TRANSPORT     network failure
- INTEGRATIONS.HTTP_ERROR    any other non-2xx
``context['delivery_ambiguous']`` is True when the provider may have delivered anyway.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr

import httpx
import structlog

from app.core.errors import Result, err, ok
from app.core.mail_threading import ProviderReceipt, ThreadRef
from app.integrations.providers import MailboxProvider

logger = structlog.get_logger()
_TIMEOUT_SECONDS = 30.0
# RFC 5322's hard limit. At the default 78 a long unbreakable value (the signed Reply-To inside
# List-Unsubscribe, a References chain) is re-encoded as =?utf-8?q?...?= and stops being an address.
_POLICY = SMTP.clone(max_line_length=998)

# Failures before a byte left this host: the provider cannot have delivered anything.
_NEVER_LEFT_THE_HOST: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
)


def build_message(
    *,
    sender: str,
    sender_name: str,
    to: str,
    subject: str,
    text: str,
    message_id: str,
    reply_to: str | None,
    thread: ThreadRef | None,
    unsubscribe_url: str | None = None,
) -> bytes:
    msg = EmailMessage(policy=_POLICY)
    if sender:  # the mailbox's own address; Gmail would stamp it anyway, Graph's MIME form wants it
        msg['From'] = formataddr((sender_name, sender)) if sender_name else sender
    msg['To'] = to
    msg['Subject'] = subject
    msg['Message-ID'] = message_id
    if thread is not None and thread.message_id:
        msg['In-Reply-To'] = thread.message_id
        msg['References'] = thread.header_references
    if reply_to:
        msg['Reply-To'] = reply_to
    # #75: a mailto at the signed Reply-To, so the opt-out reply lands where we read it; #172: the HTTPS
    # one-click (RFC 8058) first, which Gmail and Yahoo want from bulk senders.
    unsubscribe = [f'<{unsubscribe_url}>'] if unsubscribe_url else []
    unsubscribe += [f'<mailto:{reply_to}?subject=unsubscribe>'] if reply_to else []
    if unsubscribe:
        msg['List-Unsubscribe'] = ', '.join(unsubscribe)
    if unsubscribe_url:
        msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    msg.set_content(text)
    return msg.as_bytes()


async def send(
    provider: MailboxProvider, *, access_token: str, raw: bytes, thread_id: str = ''
) -> Result[ProviderReceipt]:
    auth = {'Authorization': f'Bearer {access_token}'}
    if provider.thread_id_key:  # Gmail: JSON with base64url raw, plus its native thread handle
        body: dict = {'raw': base64.urlsafe_b64encode(raw).decode()}
        if thread_id:
            body['threadId'] = thread_id
        request: dict = {'json': body, 'headers': auth}
    else:  # Graph: base64 MIME as text/plain; saved to Sent Items like any send
        request = {'content': base64.b64encode(raw), 'headers': {**auth, 'Content-Type': 'text/plain'}}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(provider.send_url, **request)
    except httpx.HTTPError as exc:
        ambiguous = not isinstance(exc, _NEVER_LEFT_THE_HOST)
        logger.warning('integrations.send.transport_error', provider=provider.name, error=repr(exc)[:200])
        return err('INTEGRATIONS.TRANSPORT', detail='mailbox did not confirm delivery', delivery_ambiguous=ambiguous)
    return _decode(resp, provider)


def _decode(resp: httpx.Response, provider: MailboxProvider) -> Result[ProviderReceipt]:
    if resp.status_code in (401, 403):
        # Gmail also answers 403 for "User-rate limit exceeded": not a reason to reconnect.
        if resp.status_code == 403 and 'ratelimitexceeded' in resp.text.lower()[:2000]:
            return err('INTEGRATIONS.RATE_LIMITED', detail='provider send quota exceeded')
        return err('INTEGRATIONS.AUTH', detail='the mailbox rejected the send authorization')
    if resp.status_code == 429:
        return err('INTEGRATIONS.RATE_LIMITED', detail='provider send quota exceeded')
    if resp.status_code >= 300:
        logger.warning('integrations.send.http_error', provider=provider.name, status=resp.status_code)
        # A 5xx is the provider saying it does not know either; a 4xx refused before queueing.
        return err(
            'INTEGRATIONS.HTTP_ERROR',
            detail=f'mailbox send returned {resp.status_code}',
            delivery_ambiguous=resp.status_code >= 500,
        )
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return ok(ProviderReceipt())
    thread_id = payload.get(provider.thread_id_key) if provider.thread_id_key else None
    return ok(ProviderReceipt(message_id=str(payload.get('id') or ''), thread_id=str(thread_id or '')))
