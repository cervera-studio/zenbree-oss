"""What a mailbox's provider API gives of one message (#747, #1165): its headers, or the whole of it."""

from __future__ import annotations

import base64
import json

import httpx
import structlog

logger = structlog.get_logger()

GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me'
GRAPH_API = 'https://graph.microsoft.com/v1.0'


def _raw(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))


async def _bounded(http: httpx.AsyncClient, url: str, limit: int, **params) -> bytes | None:
    """The body of a GET, or None past ``limit`` bytes (read no further) or on a non-200."""
    async with http.stream('GET', url, params=params or None) as resp:
        if resp.status_code != 200:
            return None
        body = b''
        async for chunk in resp.aiter_bytes():
            body += chunk
            if len(body) > limit:
                logger.warning('mailbox_fetch.too_large', limit=limit)
                return None
    return body


# The headers that decide, before any body is read, whether a message is a report, a reply to our sends or neither.
HEADERS = [
    'From',
    'Subject',
    'Content-Type',
    'In-Reply-To',
    'References',
    'Message-ID',
    'Authentication-Results',
    'Auto-Submitted',
    'Precedence',
    'X-Autoreply',
    'X-Autorespond',
]
Meta = tuple[dict[str, str], int]  # lowercased header names (the first of a repeated one), size in bytes


def _first(pairs) -> dict[str, str]:
    """The first of a repeated header: the receiving provider prepends its own ``Authentication-Results``."""
    headers: dict[str, str] = {}
    for h in pairs:
        headers.setdefault(h['name'].lower(), h['value'])
    return headers


async def meta(http: httpx.AsyncClient, mb: dict, message_id: str) -> Meta | None:
    """None when the message is gone. Never reads a body."""
    if mb['provider'] == 'google':
        resp = await http.get(
            f'{GMAIL_API}/messages/{message_id}', params={'format': 'metadata', 'metadataHeaders': HEADERS}
        )
        if resp.status_code != 200:  # deleted since
            return None
        body = resp.json()
        return _first(body.get('payload', {}).get('headers', [])), int(body.get('sizeEstimate') or 0)
    resp = await http.get(
        f'{GRAPH_API}/me/messages/{message_id}', params={'$select': 'from,subject,internetMessageHeaders'}
    )
    if resp.status_code != 200:
        return None
    body = resp.json()
    headers = _first(body.get('internetMessageHeaders') or [])
    headers.setdefault('from', ((body.get('from') or {}).get('emailAddress') or {}).get('address') or '')
    headers.setdefault('subject', body.get('subject') or '')
    return headers, 0  # Graph states no size here: the body read is bounded instead


async def whole(http: httpx.AsyncClient, mb: dict, message_id: str, limit: int) -> bytes | None:
    """The whole message, or None past ``limit`` bytes."""
    if mb['provider'] == 'google':
        # base64url inside JSON: a third more than the message
        full = await _bounded(http, f'{GMAIL_API}/messages/{message_id}', limit * 2, format='raw')
        return _raw(json.loads(full)['raw']) if full else None
    return await _bounded(http, f'{GRAPH_API}/me/messages/{message_id}/$value', limit)
