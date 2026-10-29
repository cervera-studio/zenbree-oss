"""The opaque one-click unsubscribe token (#172), minted per campaign email and parsed by the public routes.

A Fernet token (AES-CBC + HMAC) over ``owner-uuid 16 bytes || recipient address``, unpadded base64url.
The key derives from ``OUTREACH_REPLY_SECRET`` with a domain-separation label, so it never doubles as
the Reply-To signature. Encrypted, not just signed: the token lands in access logs, the Cloudflare log
and a redirect ``Location``, and none of them may hold the recipient's address. Authenticated: a link
cannot be forged for another person or sender, and nothing is looked up by a guessable id. Stateless:
the link keeps working after the campaign or the send is deleted. No I/O.

URLs (#220): with ``UNSUBSCRIBE_ORIGIN`` the page and the one-click share ``<origin>/x/<token>`` (GET shows the
page, POST is the one-click); without it, the page is ``WEB_ORIGIN/u/<token>`` and the one-click the API's.
"""

from __future__ import annotations

import base64
import binascii
import re
from hashlib import sha256
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.exclusions.rules import normalise

_LABEL = b'zenbree-unsubscribe-v1\x00'
_FORMAT = re.compile(r'[A-Za-z0-9_-]{1,600}')


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(sha256(_LABEL + secret.encode()).digest()))


def mint(owner_id: str, email: str, *, secret: str) -> str | None:
    """``None`` when unconfigured or the address is not one: the email then goes out without the link."""
    address = normalise('email', email)
    if not secret or not address:
        return None
    try:
        owner = UUID(owner_id).bytes
    except ValueError, AttributeError, TypeError:
        return None
    return _fernet(secret).encrypt(owner + address.encode()).decode().rstrip('=')


def parse(token: str, *, secret: str) -> tuple[str, str] | None:
    """(owner_id, email) from a token we minted, or ``None`` for anything else, all alike.

    Strict: exactly the canonical unpadded base64url we mint. A lenient decoder would accept many
    spellings of one token, and each spelling would be a fresh key for anything keyed on the raw token.
    """
    if not secret or not _FORMAT.fullmatch(token or ''):
        return None
    padded = token + '=' * (-len(token) % 4)
    try:
        if base64.urlsafe_b64encode(base64.urlsafe_b64decode(padded)).decode() != padded:
            return None
        raw = _fernet(secret).decrypt(padded)
    except binascii.Error, ValueError, InvalidToken:
        return None
    if len(raw) <= 16:
        return None
    try:
        return str(UUID(bytes=raw[:16])), raw[16:].decode()
    except UnicodeDecodeError:
        return None


def page_url(token: str) -> str:
    """Where a person reads and confirms the opt-out."""
    if origin := settings.unsubscribe_origin.rstrip('/'):
        return f'{origin}/x/{token}'
    return f'{settings.web_origin.rstrip("/")}/u/{token}'


def one_click_url(token: str) -> str | None:
    """The List-Unsubscribe URL a mail client POSTs to (RFC 8058); ``None`` with nowhere to post."""
    if settings.unsubscribe_origin:
        return page_url(token)
    api_base = settings.api_public_base_url.rstrip('/')
    return f'{api_base}/v1/unsubscribe/{token}' if api_base else None
