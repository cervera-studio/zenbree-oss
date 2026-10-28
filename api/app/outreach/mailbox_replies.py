"""Replies to our campaign sends, read from the sending mailbox (#1165).

``OUTREACH_REPLY_DOMAIN`` stays empty in production, so a send has no Reply-To and the answer lands in the user's own
mailbox. The push of ``app.integrations.mailbox_push`` lists each new message's headers; ``match`` decides from them
alone whether it answers one of our sends. Only then is the message fetched whole and handed to ``replies.record``,
which stops the person's sequences and files an opt-out as for a reply by Resend. Anything else is never fetched
and nothing of it is stored. The reply stays in the mailbox untouched: the scopes are read-only.

``authentication`` is the DMARC verdict in the topmost ``Authentication-Results`` header, pinned to the provider's
authserv-id: the provider prepends its own, so a sender cannot put one above it. A message with no such verdict has
none, as a ``gray`` one from Resend has, and is never announced by webhook.
"""

from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Awaitable, Callable
from email import message_from_bytes
from email.policy import default
from email.utils import parseaddr

import structlog

from app.core.db import get_conn
from app.core.reply_address import normalise_address
from app.outreach import replies, reply_senders
from app.outreach.inbound import message_ids
from app.outreach.reply_targets import QUOTED

logger = structlog.get_logger()

MAX_REPLY_BYTES = 5_000_000  # a reply with big attachments is recorded from its headers and subject alone
READ_SECONDS = 12
Whole = Callable[[int], Awaitable[bytes | None]]

# The newest send from this mailbox that the message quotes, else to this sender while they are an active target.
_MATCH = f"""SELECT s.campaign_id::text AS campaign, s.organization_id::text, s.owner_id::text
FROM outreach_campaign_sends s LEFT JOIN outreach_campaign_targets t ON t.id = s.target_id
WHERE s.sender_id = %(mailbox)s AND s.channel = 'email'
  AND ({QUOTED} OR (t.status = 'active' AND lower(btrim(t.email)) = %(sender)s))
ORDER BY ({QUOTED}) IS TRUE DESC, s.created_at DESC LIMIT 1"""
# The receiving provider's authserv-id: Gmail's front end; Exchange Online's protection hosts.
_AUTHSERV = {
    'google': re.compile(r'mx\.google\.com'),
    'microsoft': re.compile(r'[a-z0-9-]+(\.[a-z0-9-]+)*\.protection\.outlook\.com'),
}
# Printable ASCII and folding whitespace: ``str.split`` would also cut at U+00A0, U+2000 and the C0 separators, which
# no provider writes and a sender could use to plant a ``header.from`` token.
_PRINTABLE = re.compile(r'[\x20-\x7e\t\r\n]*')
_PROPERTY = re.compile(r'[A-Za-z][A-Za-z0-9._-]*=[A-Za-z0-9._@+:/=-]*')
_TAGS = re.compile(r'<[^>]+>')


def match(mailbox_id: str, sender: str, refs: list[str]) -> str | None:
    """The campaign whose send of this mailbox the message answers, or None."""
    with get_conn() as conn:
        found = conn.execute(_MATCH, {'mailbox': mailbox_id, 'sender': sender, 'refs': refs}).fetchone()
        if found and reply_senders.is_internal(conn, found['organization_id'], found['owner_id'], sender):
            return None
    return found['campaign'] if found else None


def _uncommented(value: str) -> str | None:
    """``value`` without its RFC 8601 comments, or None when it has a quote, an escape, an unbalanced or nested
    parenthesis: text the sender may have planted is never guessed at."""
    depth, out = 0, []
    for ch in value:
        if ch in '"\\' or (ch == '(' and depth) or (ch == ')' and not depth):
            return None
        depth += (ch == '(') - (ch == ')')
        if not depth and ch != ')':
            out.append(ch)
    return None if depth else ''.join(out)


def authentication(headers: dict[str, str], provider: str) -> dict | None:
    """``{'dmarc': <result>}`` as ``replies.record`` reads it, from the topmost ``Authentication-Results``: the one
    the receiving provider prepended, named by its authserv-id (``_AUTHSERV``). None for anything else or any
    ambiguity: another authserv-id, a quote or nested comment, a property value outside addr-spec and domain
    characters, no ``dmarc`` resinfo or more than one. The one ``dmarc`` resinfo must begin with the method ``dmarc``
    and carry ``header.from`` equal to the domain of the message's ``From``: a sender can plant a ``;`` and a
    ``dmarc=pass`` through its envelope address but never a whitespace-separated ``header.from`` after it.

    ponytail: Exchange Online often writes this header with no authserv-id: that reads as no verdict (an opt-out
    stops the person but excludes nobody) rather than trusting a header that cannot be pinned."""
    raw = headers.get('authentication-results', '')
    value = _uncommented(raw) if _PRINTABLE.fullmatch(raw) else None
    if value is None:
        return None
    head, *resinfos = ([t for t in re.split(r'[ \t\r\n]+', part) if t] for part in value.split(';'))
    if not head or not _AUTHSERV[provider].fullmatch(head[0].lower()) or head[1:] not in ([], ['1']):
        return None
    if any(not _PROPERTY.fullmatch(p) for r in resinfos for p in r[1:]):
        return None
    dmarc = [r for r in resinfos if r and r[0].lower().startswith('dmarc')]
    domain = normalise_address(parseaddr(headers.get('from', ''))[1]).partition('@')[2]
    found = re.fullmatch(r'dmarc=(\w+)', dmarc[0][0], re.IGNORECASE) if len(dmarc) == 1 else None
    aligned = (
        found
        and domain
        and [p.lower() for p in dmarc[0][1:] if p.lower().startswith('header.from=')] == [f'header.from={domain}']
    )
    return {'dmarc': found.group(1)} if found and aligned else None


def text_of(raw: bytes | None) -> str:
    """The reply's text, from its plain part, else its HTML stripped of tags; '' when there is none or it is unreadable."""
    if not raw:
        return ''
    try:
        part = message_from_bytes(raw, policy=default).get_body(('plain', 'html'))
        text = part.get_content() if part else ''
        return html.unescape(_TAGS.sub(' ', text)) if part and part.get_content_type() == 'text/html' else text
    except Exception as exc:  # a message the parser chokes on is still a reply: record it by its subject
        logger.warning('mailbox_replies.unreadable', error_type=type(exc).__name__)
        return ''


async def handle(mb: dict, message_id: str, headers: dict[str, str], size: int, *, whole: Whole) -> int:
    """1 when the message answers a send of ``mb`` and was recorded now, else 0. Raises on a provider or database
    failure: the caller retries, and ``record`` is idempotent."""
    name, address = parseaddr(headers.get('from', ''))
    sender = normalise_address(address)
    refs = message_ids(headers.get('in-reply-to', ''), headers.get('references', ''))
    own = (mb['account_label'] or '').strip().lower()
    campaign = await asyncio.to_thread(match, str(mb['id']), sender, refs) if sender and sender != own else None
    if campaign is None:
        return 0
    raw = await asyncio.wait_for(whole(MAX_REPLY_BYTES), READ_SECONDS) if size <= MAX_REPLY_BYTES else None
    done = await asyncio.to_thread(
        replies.record,
        None,
        campaign_id=campaign,
        channel='email',
        provider_message_id=f'mailbox:{mb["id"]}:{message_id}',
        sender=sender,
        sender_name=name,
        subject=headers.get('subject', ''),
        text=text_of(raw),
        headers=headers,
        authentication=authentication(headers, mb['provider']),
        references=refs,
        rfc_message_id=headers.get('message-id', ''),
    )
    return int(done['status'] == 'accepted')
