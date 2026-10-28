"""The builder's test email: one saved email step, in copy A or B, to the sending mailbox's own address.

Adapted from the earlier implementation ``outreach/use_cases/send_test_email.py``. The only provider call outside the beat dispatcher.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.admin.settings import is_disabled
from app.auth.middleware import CurrentUser, current_user
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.errors_http import api_error
from app.core.mail_threading import new_message_id
from app.integrations import mailbox_sender
from app.integrations.providers import MAILBOX_PROVIDERS
from app.outreach import dispatcher, pool_state, sending
from app.outreach.fields import parse_fields, resolve, sample_inputs
from app.outreach.preview import render_steps
from app.outreach.routes import _own
from app.outreach.step_copy import SAMPLE_CONTACT
from app.outreach.variants import Variant, sequence

router = APIRouter(prefix='/v1/campaigns')
Auth = Depends(current_user)

TEST_EMAILS_PER_HOUR = 10


class TestEmailSent(BaseModel):
    to: str


class TestEmailIn(BaseModel):
    step: int = Field(ge=1, le=12)
    variant: Variant = 'a'


def _rate_limit(user_id: str) -> None:
    """Fixed hourly window in Redis. Fails closed: without Redis there are no test sends."""
    name = f'rl:test-email:{hashlib.sha256(user_id.encode()).hexdigest()[:32]}'
    try:
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        n = cast(int, r.incr(name))  # sync client; redis-py types replies as Awaitable | Any
        if n == 1:
            r.expire(name, 3600)
    except redis.RedisError:
        raise api_error(503, 'CAMPAIGN.UNAVAILABLE', 'try again later') from None
    if n > TEST_EMAILS_PER_HOUR:
        raise api_error(429, 'CAMPAIGN.RATE_LIMITED', 'too many test emails, try again later')


@router.post('/{campaign_id}/test-email', response_model=TestEmailSent)
async def send_test_email(campaign_id: UUID, body: TestEmailIn, user: CurrentUser = Auth) -> dict:
    """One saved email step, filled with ``SAMPLE_CONTACT``, from a pool mailbox of the campaign's project to that
    same mailbox. ``variant`` 'b' sends B's copy, which is A's on a step without one, as a B person gets it.

    The recipient is never caller-supplied (that would be an open relay on the project's grant): it is the
    mailbox's own address. No window or pacing (someone is pressing the button), no send row, no thread.
    """
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        pool = pool_state.accounts(conn, c, datetime.now(UTC))
    if body.step > len(c['steps']) or c['steps'][body.step - 1]['channel'] != 'email':
        raise api_error(422, 'CAMPAIGN.NOT_EMAIL_STEP', 'pick a saved email step')
    mb = pool_state.sender_of(pool, {'id': c['id'], 'sender_pinned_at': None})
    if mb is None:
        raise api_error(422, 'CAMPAIGN.NO_SENDER', 'pick a connected mailbox to send from')
    if not mb['account_label']:
        raise api_error(422, 'CAMPAIGN.TEST_RECIPIENT_UNKNOWN', 'the mailbox address is unknown, reconnect it')
    if is_disabled('channel_disabled:email') or is_disabled(f'vendor_disabled:{mb["provider"]}'):
        raise api_error(503, 'CAMPAIGN.SENDING_DISABLED', 'email sending is paused, try again later')
    fields = parse_fields(c['fields'])
    sample = resolve(fields, SAMPLE_CONTACT, sample_inputs(fields)).values
    rendered = render_steps(sequence(c['steps'], body.variant), SAMPLE_CONTACT, sample)[body.step - 1]
    if rendered['error']:
        raise api_error(422, rendered['error'], 'this step has a placeholder that cannot be filled')
    provider = MAILBOX_PROVIDERS[mb['provider']]
    if not provider.grants_send(mb['granted_scopes'] or []):
        raise api_error(409, 'CAMPAIGN.MAILBOX_UNAVAILABLE', 'the mailbox lost its send permission, reconnect it')
    _rate_limit(user.id)
    token = await dispatcher._access_token(mb, provider, raise_outage=True)
    if token is None:
        raise api_error(409, 'CAMPAIGN.MAILBOX_UNAVAILABLE', 'the mailbox could not be reached, check Settings')
    try:
        raw = mailbox_sender.build_message(
            sender=mb['account_label'],
            sender_name=mb['display_name'] or '',
            to=mb['account_label'],
            subject=f'[Test] {rendered["subject"]}',
            text=sending.compose_body(rendered['body'], mb['sign_off'] or ''),
            message_id=new_message_id(domain=mb['account_label'].rpartition('@')[2] or 'zenbree.local'),
            reply_to=None,
            thread=None,
        )
    except ValueError:  # a CR/LF in a header value (subject, display name)
        raise api_error(
            422, 'CAMPAIGN.INVALID_CONTENT', 'the subject or sender name cannot go in an email header'
        ) from None
    sent = await mailbox_sender.send(provider, access_token=token, raw=raw)
    if is_err(sent):
        raise api_error(502, 'CAMPAIGN.TEST_FAILED', 'the test email was not sent')
    return {'to': mb['account_label']}
