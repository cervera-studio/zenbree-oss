"""The public one-click unsubscribe link (#172): token-only, so no route here reads a cookie or a bearer."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

import redis
import structlog
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.middleware import SystemReason, public
from app.core.config import settings
from app.core.errors_http import api_error
from app.outreach import replies
from app.outreach import unsubscribe as unsubscribe_token

router = APIRouter(tags=['Inbox'])
logger = structlog.get_logger()
# The token names a person, not an organization, and an opt-out stops them in every organization (#460).
_OPT_OUT = [public(SystemReason.OPT_OUT)]

# Public and token-only: no auth dependency reads a cookie or a bearer here. Every route verifies the HMAC
# before touching the database, and every bad token gets the same 404.

ERASURE_EVERY_SECONDS = 86_400  # one data-deletion email to the sender per recipient per day


def _link(token: str) -> tuple[str, str]:
    parsed = unsubscribe_token.parse(token, secret=settings.outreach_reply_secret)
    if parsed is None:
        raise api_error(404, 'UNSUBSCRIBE.INVALID_LINK', 'this unsubscribe link is not valid')
    return parsed


@router.get('/v1/unsubscribe/{token}', include_in_schema=False, dependencies=[public(None)])
def unsubscribe_redirect(token: str) -> RedirectResponse:
    """The List-Unsubscribe URL opened in a browser (or fetched by a link scanner): the page, never an opt-out."""
    return RedirectResponse(unsubscribe_token.page_url(quote(token, safe='')), status_code=303)


UnsubscribeReason = Literal['not_interested', 'wrong_person', 'too_many', 'other']


class UnsubscribeResult(BaseModel):
    status: Literal['unsubscribed', 'already_unsubscribed']


class UnsubscribeState(BaseModel):
    sender_name: str
    sender_email: str | None
    company_name: str | None
    email: str
    last_subject: str | None
    last_sent_at: str | None
    status: Literal['unsubscribed', 'subscribed']
    unsubscribed_at: str | None
    undo_available: bool


class Resubscribed(BaseModel):
    status: Literal['subscribed']


class ErasureRequested(BaseModel):
    status: Literal['sent']


@router.post('/v1/unsubscribe/{token}', response_model=UnsubscribeResult, dependencies=_OPT_OUT)
def unsubscribe_one_click(token: str, reason: UnsubscribeReason | None = None) -> dict:
    """RFC 8058 one-click (body ``List-Unsubscribe=One-Click``, ignored: the token is the authority) and the
    page's button, which may pass the optional ``?reason=``. Idempotent: a repeat answers ``already_unsubscribed``.

    ponytail: the reason is only logged (no owner-visible column yet); store it when Exclusions shows one."""
    owner_id, email = _link(token)
    status = replies.unsubscribe(owner_id, email)
    if reason and status:
        logger.info('unsubscribe.reason', user_id=owner_id, reason=reason)
    if status is None:
        raise api_error(404, 'UNSUBSCRIBE.INVALID_LINK', 'this unsubscribe link is not valid')
    return {'status': status}


@router.get('/v1/unsubscribe/{token}/state', response_model=UnsubscribeState, dependencies=_OPT_OUT)
def unsubscribe_state(token: str) -> dict:
    """What the page shows. Reads only."""
    state = replies.link_state(*_link(token))
    if state is None:
        raise api_error(404, 'UNSUBSCRIBE.INVALID_LINK', 'this unsubscribe link is not valid')
    return state


@router.post('/v1/unsubscribe/{token}/undo', response_model=Resubscribed, dependencies=_OPT_OUT)
def unsubscribe_undo(token: str) -> dict:
    owner_id, email = _link(token)
    try:
        undone = replies.undo_unsubscribe(owner_id, email)
    except redis.RedisError:
        raise api_error(503, 'UNSUBSCRIBE.UNAVAILABLE', 'try again later') from None
    if not undone:
        raise api_error(409, 'UNSUBSCRIBE.UNDO_EXPIRED', 'this unsubscribe can no longer be undone')
    return {'status': 'subscribed'}


@router.post('/v1/unsubscribe/{token}/erasure', response_model=ErasureRequested, dependencies=_OPT_OUT)
def unsubscribe_erasure(token: str) -> dict:
    owner_id, email = _link(token)
    key = replies.link_key('rl:unsubscribe-erasure', owner_id, email)
    try:  # fails closed: each request is an email to the sender
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        if not r.set(key, 1, nx=True, ex=ERASURE_EVERY_SECONDS):
            raise api_error(429, 'UNSUBSCRIBE.RATE_LIMITED', 'the request was already sent')
        status = replies.request_erasure(owner_id, email)
        if status != 'sent':
            r.delete(key)
    except redis.RedisError:
        raise api_error(503, 'UNSUBSCRIBE.UNAVAILABLE', 'try again later') from None
    if status == 'not_unsubscribed':
        raise api_error(409, 'UNSUBSCRIBE.NOT_UNSUBSCRIBED', 'unsubscribe first')
    if status == 'failed':
        raise api_error(502, 'UNSUBSCRIBE.ERASURE_FAILED', 'the request could not be sent, reply to the email instead')
    return {'status': 'sent'}
