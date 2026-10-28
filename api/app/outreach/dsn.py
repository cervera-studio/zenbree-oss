"""Delivery-status notifications (RFC 3464), the bounces a mailbox receives for what it sent (#747). Pure: no I/O.

A DSN is ``multipart/report; report-type=delivery-status`` whose direct parts are a human-readable text, one
``message/delivery-status`` part (a per-message block, then one block per recipient: ``Final-Recipient``,
``Action``, ``Status``) and the original message or its headers, whose ``Message-ID`` names the send.

Anyone can mail a fake bounce to the user's mailbox, and a bounce refunds, excludes and can pause a campaign, so
``read`` fails closed: one strict parse (``email.policy.default``), and anything ambiguous is refused rather than
guessed at, because a reader that picks another header or part than the provider did reads another message.

- **Who wrote it.** A report Gmail or Exchange writes into its own mailbox never crossed the internet, so it carries
  no ``Authentication-Results`` or ARC header; every message that arrived from outside carries the one the receiving
  provider prepended, and its content (``smtp.mailfrom``, comments) is partly the sender's. So any such header refuses
  the message: none is parsed or trusted. The ``From`` must then be exactly one address, the provider's bounce
  sender, and parse without defects.
- **One reading.** Exactly one ``From``, ``Content-Type`` and ``report-type``; exactly one delivery-status part and
  one original among the direct parts (never one nested in the quoted original); exactly one recipient block (we
  send to one address), each critical field once and well formed; exactly one ``Message-ID`` in the original.
- **Whose send.** The quoted ``Message-ID`` must name a send of that mailbox to that recipient
  (``app.outreach.bounces``): a uuid4 only the recipient ever saw.

ponytail: a bounce in plain text only (old MTAs) is refused; add a text heuristic if one shows up in real mailboxes.
ponytail: "a provider-written report carries no Authentication-Results" is how Gmail and Exchange Online deliver
their own reports, not a documented contract: if a real one ever carries one, it is refused (no refund), never
wrongly accepted. Signing an ENVID per send would replace the rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import message_from_bytes
from email.message import EmailMessage, Message
from email.policy import default
from email.utils import parseaddr
from typing import Literal

from app.core.mail_threading import normalise_message_id

_DAEMONS = frozenset({'mailer-daemon', 'postmaster'})
# Exchange Online's report comes from an address of its own, MicrosoftExchange<hex>@<tenant>, under this subject.
_SUBJECTS = ('undeliverable', 'delivery status notification')
# Who writes a mailbox's bounces: Gmail one address; Microsoft the postmaster of the account's own domain, or of its
# consumer domains for outlook.com accounts.
_GMAIL_SENDER = 'mailer-daemon@googlemail.com'
_MICROSOFT_DOMAINS = frozenset({'outlook.com', 'hotmail.com', 'live.com', 'msn.com'})
# Headers only a receiving server adds: their presence means the message came from outside the provider.
_RECEIVED_FROM_OUTSIDE = ('authentication-results', 'arc-authentication-results', 'arc-seal', 'arc-message-signature')
_ORIGINALS = frozenset({'message/rfc822', 'text/rfc822-headers'})
_STATUS = re.compile(r'[245]\.\d{1,3}\.\d{1,3}')
_ACTIONS = frozenset({'failed', 'delayed', 'delivered', 'relayed', 'expanded'})


@dataclass(frozen=True, slots=True)
class Bounce:
    recipient: str  # lowercased
    kind: Literal['hard', 'soft']
    status: str
    message_id: str  # the original's, normalised


class Refused(Exception):
    """Why a message is not read as a bounce; ``read`` returns it, nothing else sees it."""


def looks_like_dsn(headers: dict[str, str]) -> bool:
    """From a message's headers alone (lowercased names): worth fetching whole. Every other message stays unread."""
    content_type = headers.get('content-type', '').lower()
    if 'multipart/report' in content_type and 'delivery-status' in content_type:
        return True
    if headers.get('subject', '').strip().lower().startswith(_SUBJECTS):
        return True
    return parseaddr(headers.get('from', ''))[1].lower().partition('@')[0] in _DAEMONS


def _once(msg: Message, name: str) -> str:
    values = msg.get_all(name) or []
    if len(values) != 1:
        raise Refused(f'{name.lower()}_not_once')
    return str(values[0]).strip()


def _sender_is_the_provider(msg: Message, provider: str, mailbox: str) -> None:
    if any(msg.get_all(h) for h in _RECEIVED_FROM_OUTSIDE):
        raise Refused('received_from_outside')
    _once(msg, 'From')
    header = msg['From']
    addresses: tuple = getattr(header, 'addresses', ())
    if len(addresses) != 1 or getattr(header, 'defects', None) or not addresses[0].domain:
        raise Refused('from_not_one_address')
    local, domain = addresses[0].username.lower(), addresses[0].domain.lower()
    if provider == 'google':
        ok = f'{local}@{domain}' == _GMAIL_SENDER
    else:
        own = mailbox.lower().rpartition('@')[2]
        ok = (local == 'postmaster' or local.startswith('microsoftexchange')) and (
            domain == own or domain in _MICROSOFT_DOMAINS
        )
    if not ok:
        raise Refused('not_from_the_provider')


def _parts(msg: EmailMessage) -> tuple[Message, Message]:
    """The delivery-status part and the original, each exactly once among the report's direct parts."""
    _once(msg, 'Content-Type')
    if msg.get_content_type() != 'multipart/report' or msg.defects:
        raise Refused('not_a_delivery_report')
    raw_type = str(msg['Content-Type']).lower()
    if raw_type.count('report-type') != 1 or msg.get_param('report-type') != 'delivery-status':
        raise Refused('not_a_delivery_report')
    payload = msg.get_payload()
    children = [p for p in payload if isinstance(p, Message)] if isinstance(payload, list) else []
    statuses = [p for p in children if p.get_content_type() == 'message/delivery-status']
    originals = [p for p in children if p.get_content_type() in _ORIGINALS]
    if len(statuses) != 1 or len(originals) != 1 or any(p.defects for p in children):
        raise Refused('parts_not_once')
    return statuses[0], originals[0]


def _original_message_id(part: Message) -> str:
    if part.get_content_type() == 'message/rfc822':
        payload = part.get_payload()
        original = payload[0] if isinstance(payload, list) and len(payload) == 1 else None
    else:
        text = part.get_payload(decode=True)
        original = message_from_bytes(text, policy=default) if isinstance(text, bytes) else None
    if not isinstance(original, Message):
        raise Refused('no_original')
    if not (message_id := normalise_message_id(_once(original, 'Message-ID'))):
        raise Refused('no_original_message_id')
    return message_id


def _recipient(part: Message) -> tuple[str, str, str]:
    """(recipient, action, status) of the report's one recipient block."""
    blocks = part.get_payload()
    fields, block = blocks if isinstance(blocks, list) and len(blocks) == 2 else (None, None)
    if not isinstance(fields, Message) or not isinstance(block, Message):
        raise Refused('recipients_not_one')  # the per-message block, then exactly one recipient block
    kind, _, address = _once(block, 'Final-Recipient').partition(';')
    recipient = address.strip().lower()
    if kind.strip().lower() != 'rfc822' or parseaddr(recipient)[1] != recipient or '@' not in recipient:
        raise Refused('bad_recipient')
    action, status = _once(block, 'Action').lower(), _once(block, 'Status')
    if action not in _ACTIONS or not _STATUS.fullmatch(status):
        raise Refused('bad_status')
    return recipient, action, status


def _kind(action: str, status: str) -> Literal['hard', 'soft'] | None:
    if action == 'failed' and status.startswith('5'):
        return 'hard'
    if action == 'delayed' or (action == 'failed' and status.startswith('4')):
        return 'soft'
    return None  # delivered, relayed, expanded


def read(raw: bytes, *, provider: str, mailbox: str) -> tuple[list[Bounce], str | None]:
    """The bounce a raw RFC 822 message reports, and why it was refused when it was (then no bounce). ``mailbox``
    is the address of the mailbox it arrived in, ``provider`` its provider."""
    msg = message_from_bytes(raw, policy=default)
    try:
        if not isinstance(msg, EmailMessage):
            raise Refused('not_a_delivery_report')
        _sender_is_the_provider(msg, provider, mailbox)
        status_part, original = _parts(msg)
        message_id = _original_message_id(original)
        recipient, action, status = _recipient(status_part)
    except Refused as refused:
        return [], str(refused)
    except ValueError, TypeError, IndexError, LookupError:  # a header the strict parser cannot read
        return [], 'unreadable'
    kind = _kind(action, status)
    return ([Bounce(recipient, kind, status, message_id)] if kind else []), None
