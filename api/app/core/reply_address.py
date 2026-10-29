"""The signed ``Reply-To`` address, minted here and parsed here.

Cross-cutting on purpose: ``integrations`` mints one on every mailbox send,
``outreach`` parses one on every inbound message. Neither context owns the
scheme, and putting it in either would make the other import across a
bounded-context boundary for a pure string function.

Why the address exists at all (#1424). Outreach goes out of the user's own
Gmail or Microsoft mailbox under a send-only grant, deliberately: reading a
Gmail mailbox is a Google *restricted* scope (verification plus an annual
security assessment) while sending is merely sensitive. So the reply cannot
be read where it lands. It has to be routed to us instead, and ``Reply-To``
is the only header that does that without touching the ``From`` the
recipient sees.

    reply+c<campaign-uuid-hex>.<hmac12>@<inbound domain>   minted for every campaign send and inbox answer
    reply+<owner-uuid-hex>.<hmac12>@<inbound domain>       the old form, still parsed

``hmac12`` is the first 12 hex chars of HMAC-SHA256(secret, id), over ``campaign:<id>`` for the campaign form, so
a signature of one form never verifies as the other. It is not confidentiality (the id is right there in the
clear): it stops an outsider guessing an id and posting mail into an inbox. Verification is constant-time.

The campaign form names one campaign, so a reply is filed in that campaign's organization with one lookup. The old
form names a person (or since #333 the campaign's client, once its creator's account was deleted), and the inbound
code has to guess which of their campaigns, in which organization, a reply answers (#414, #467).
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256
from typing import NamedTuple
from uuid import UUID

# Local-part prefix of every inbound address we mint. Anything arriving at
# the inbound domain without it is not ours to route.
REPLY_LOCAL_PREFIX = 'reply+'

# Marks the campaign form: ``c`` and 32 hex chars, where an owner's id is 32. ``c`` is itself a hex digit, so the
# length tells the forms apart, never the first char alone.
_CAMPAIGN_MARK = 'c'
_SIGNATURE_CHARS = 12

# ``Foo Bar <foo@bar.com>`` and bare ``foo@bar.com``.
_ANGLE_ADDRESS_RE = re.compile(r'<([^>]+)>')
_ADDRESS_RE = re.compile(r'[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+')


def normalise_address(raw: str) -> str:
    """Strip the display name and case-fold. ``''`` when there is no address."""
    if not raw:
        return ''
    candidate = raw.strip()
    angled = _ANGLE_ADDRESS_RE.search(candidate)
    if angled:
        candidate = angled.group(1).strip()
    match = _ADDRESS_RE.search(candidate)
    return match.group(0).lower() if match else ''


class ReplyKey(NamedTuple):
    """What a signed reply address names: a campaign, or (the old form) a person or a client."""

    id: str
    campaign: bool


def reply_key(owner_id, project_id) -> str:
    """The id an unsubscribe link carries (and the old reply address did): the campaign's creator, or its client
    (project) once the creator's account is deleted (#333). ``outreach.replies`` tells the two apart."""
    return str(owner_id or project_id)


def _signature(signed: str, secret: str) -> str:
    digest = hmac.new(secret.encode('utf-8'), signed.encode('utf-8'), sha256)
    return digest.hexdigest()[:_SIGNATURE_CHARS]


def build_campaign_reply_address(campaign_id, *, domain: str, secret: str) -> str | None:
    """Mint the ``Reply-To`` of a campaign's mail, or ``None`` when unconfigured (see ``build_reply_address``)."""
    if not domain or not secret:
        return None
    try:
        campaign = UUID(str(campaign_id))
    except ValueError:
        return None
    signature = _signature(f'campaign:{campaign}', secret)
    return f'{REPLY_LOCAL_PREFIX}{_CAMPAIGN_MARK}{campaign.hex}.{signature}@{domain}'


def build_reply_address(owner_id: str, *, domain: str, secret: str) -> str | None:
    """Mint the old-form ``Reply-To`` for this key, or ``None`` when unconfigured.

    ``None`` is the inert path: without an inbound domain and a secret there
    is nowhere for a reply to land, and stamping a header that routes into a
    black hole is worse than not stamping one.
    """
    if not domain or not secret:
        return None
    try:
        owner_hex = UUID(owner_id).hex
    except ValueError, AttributeError, TypeError:
        return None
    return f'{REPLY_LOCAL_PREFIX}{owner_hex}.{_signature(owner_id, secret)}@{domain}'


def parse_reply_address(address: str, *, secret: str) -> ReplyKey | None:
    """Recover what an address we minted names, or ``None``.

    ``None`` covers every rejection the same way on purpose: not ours, not
    well formed, or a bad signature are all "do not route this".
    """
    if not secret:
        return None
    local, _, _domain = normalise_address(address).partition('@')
    if not local.startswith(REPLY_LOCAL_PREFIX):
        return None
    named, _, signature = local[len(REPLY_LOCAL_PREFIX) :].partition('.')
    campaign = len(named) == 33 and named.startswith(_CAMPAIGN_MARK)
    if not signature:
        return None
    try:
        key = str(UUID(named[1:] if campaign else named))
    except ValueError:
        return None
    signed = f'campaign:{key}' if campaign else key
    if not hmac.compare_digest(signature.encode(), _signature(signed, secret).encode()):
        return None
    return ReplyKey(key, campaign)
