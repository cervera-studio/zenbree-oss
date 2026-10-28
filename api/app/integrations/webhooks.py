"""Signed outbound webhooks (#93): an organization's endpoints receive the events they subscribed to.

Ported from the earlier implementation ``app/shared/webhooks.py`` and the webhook routes of ``app/integrations/api.py``
. the earlier implementation rode its events bus; here an emitting code path calls ``emit`` after its
transaction commits. ``emit`` resolves the event's organization (#323: from its list, campaign or inbound
message, so a member's events reach the endpoints the team's admins set up), looks up that organization's
subscribed endpoints and queues one ``deliver_webhooks`` task per endpoint on the ``webhooks`` queue, which
has its own worker (compose ``worker-webhooks``): a slow or dead receiver never holds a chat or campaign slot,
and its task's time limit is the POST's total deadline.
Emitting never raises and never waits on a receiver.

Signature: Standard Webhooks (https://www.standardwebhooks.com), as Svix and Resend sign:

    signed = f'{webhook_id}.{webhook_timestamp}.{raw_body}'
    header = 'v1,' + base64(hmac_sha256(key=base64decode(secret without 'whsec_'), msg=signed))

``webhook-id`` is the SHA-256 of the body, truncated: a retry of the same event carries the same id, so the
receiver can dedupe on it. The secret is stored Fernet-encrypted (``token_crypto``) and shown once, on create.

Only a failure the receiver may recover from is retried (no answer, 5xx, 429), on ``RETRY_DELAYS_S``.
``410 Gone`` revokes the endpoint. The secret and the URL (Zapier-style URLs carry a token) are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple, get_args
from uuid import UUID

import httpx
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from celery.exceptions import TimeoutError as ResultTimeout
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, creator_not_banned, require_org_admin_or_api_key
from app.core import tasks
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.errors_http import api_error
from app.core.rate_limit import claim, hit, release
from app.core.security.ssrf_validator import SSRFBlockedError, safe_httpx_post, validate_url
from app.integrations import token_crypto, webhook_deliveries
from app.integrations.webhook_deliveries import TEST_EVENT

logger = structlog.get_logger()
router = APIRouter(prefix='/v1/integrations/webhooks', tags=['Webhooks'])
Auth = Depends(require_org_admin_or_api_key)  # admins of the organization only (#308)

# Owner decision on #93: these four, more only when a customer asks.
EventName = Literal['list.completed', 'contact.replied', 'campaign.finished', 'sequence.stopped']
EVENTS: tuple[str, ...] = get_args(EventName)
RETRY_DELAYS_S = (30, 300, 1800)
DELIVERY_TIMEOUT_S = 10.0  # per connect / read
DELIVERY_DEADLINE_S = 30  # the whole POST: deliver_webhooks' soft time limit
MAX_ENDPOINTS = 10
_PUBLIC_COLUMNS = 'id, url, label, event_names, created_at, gone_at'
# #389: a banned creator's endpoint receives nothing (events in the meantime are dropped, retries included) and
# receives again on unban or when the ban expires.
# ``gone_at`` is a 410 from the receiver: the endpoint stays listed, with its history, and receives nothing more.
_LIVE = f'revoked_at IS NULL AND gone_at IS NULL AND {creator_not_banned("user_id")}'


def new_secret() -> str:
    return f'whsec_{base64.b64encode(secrets.token_bytes(32)).decode()}'


def sign(*, secret: str, webhook_id: str, timestamp: int, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix('whsec_'))
    digest = hmac.new(key, f'{webhook_id}.{timestamp}.'.encode() + body, hashlib.sha256).digest()
    return f'v1,{base64.b64encode(digest).decode()}'


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(',', ':'), sort_keys=True, default=str).encode()


# ── Emitting ───────────────────────────────────────────────────────────────


# The organization an event belongs to, from the ids in its data: every event names a list, a campaign or an
# inbound message (``contact.replied`` with no campaign), and each of those is in exactly one organization.
_EVENT_ORG_SQL = """coalesce(
    (SELECT organization_id FROM lists WHERE id = %(list)s::uuid),
    (SELECT organization_id FROM outreach_campaigns WHERE id = %(campaign)s::uuid),
    (SELECT p.organization_id FROM outreach_inbound_messages m JOIN projects p ON p.id = m.project_id
      WHERE m.id = %(message)s::uuid))"""


def _subscribed(event: str, data: dict[str, Any]) -> list[tuple[str, str]]:
    """``(organization id, endpoint id)`` of each live endpoint of the event's organization subscribed to it."""
    ids = {k: data.get(f'{k}_id') for k in ('list', 'campaign', 'message')}
    with get_conn() as conn:
        rows = conn.execute(
            f'SELECT organization_id, id FROM user_webhooks WHERE organization_id = {_EVENT_ORG_SQL}'
            f' AND {_LIVE} AND %(event)s = ANY(event_names)',
            {**ids, 'event': event},
        ).fetchall()
    return [(str(r['organization_id']), str(r['id'])) for r in rows]


def emit(event: str, data: dict[str, Any]) -> None:
    """Queue ``event`` for each subscribed endpoint of its organization; nothing when there are none. Call it
    after the change committed. Never raises: an outage loses the webhook, never the work that caused it."""
    from app.worker import deliver_webhooks

    try:
        endpoints = _subscribed(event, data)
        payload = {'type': event, 'timestamp': datetime.now(UTC).isoformat(), 'data': data}
        for org_id, endpoint in endpoints:
            # the endpoint's organization owns the delivery; an inbound reply or the dispatcher emits as System
            with tasks.queued_for(org_id, 'webhook delivery'):
                deliver_webhooks.delay(org_id, endpoint, payload)
    except Exception as exc:
        logger.warning('webhooks.enqueue_failed', event_name=event, error_type=type(exc).__name__)


def sequence_stopped(targets: list[dict], reason: str) -> None:
    """One ``sequence.stopped`` per campaign target row (``id``, ``campaign_id``, ``email``, ``linkedin_url``)."""
    for t in targets:
        emit(
            'sequence.stopped',
            {
                'campaign_id': str(t['campaign_id']),
                'target_id': str(t['id']),
                'reason': reason,
                'email': t['email'],
                'linkedin_url': t['linkedin_url'],
            },
        )


# ── Delivery ───────────────────────────────────────────────────────────────


def is_retryable(code: int | None) -> bool:
    return code is None or code == 429 or 500 <= code < 600


class Attempt(NamedTuple):
    status: int | None
    event_id: str
    no_answer: webhook_deliveries.NoAnswer | None


def event_id_of(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_encode(payload)).hexdigest()[:32]


def attempt_delivery(*, url: str, secret: str, payload: dict[str, Any]) -> Attempt:
    """POST one signed payload behind the SSRF guard. Never raises: no answer (SSRF block, DNS, timeout, TLS, past
    the deadline) is ``status=None`` with the reason in ``no_answer``."""
    body = _encode(payload)
    webhook_id = event_id_of(payload)
    timestamp = int(time.time())
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Zenbree-Webhooks/1',
        'webhook-id': webhook_id,
        'webhook-timestamp': str(timestamp),
        'webhook-signature': sign(secret=secret, webhook_id=webhook_id, timestamp=timestamp, body=body),
    }
    try:
        response = safe_httpx_post(url, content=body, headers=headers, timeout=DELIVERY_TIMEOUT_S)
    except (httpx.HTTPError, OSError, SoftTimeLimitExceeded) as exc:
        logger.warning('webhooks.delivery_failed', event_name=payload.get('type'), error_type=type(exc).__name__)
        if isinstance(exc, SSRFBlockedError):
            return Attempt(None, webhook_id, 'blocked')
        if isinstance(exc, httpx.TimeoutException | SoftTimeLimitExceeded):
            return Attempt(None, webhook_id, 'timeout')
        return Attempt(None, webhook_id, 'unreachable')
    logger.info(
        'webhooks.delivered', event_name=payload.get('type'), status=response.status_code, webhook_id=webhook_id
    )
    return Attempt(response.status_code, webhook_id, None)


def fire(org_id: str, webhook_id: str, payload: dict[str, Any], attempt: int = 0) -> int | None:
    """Deliver ``payload`` to one live endpoint of the organization, if it is still subscribed; schedule the retry
    or revoke on ``410``. The HTTP status, ``None`` when there was no answer or nothing to deliver to."""
    event = payload['type']
    with get_conn() as conn:
        row = conn.execute(
            'SELECT url, secret_encrypted FROM user_webhooks WHERE id = %s AND organization_id = %s'
            f' AND {_LIVE} AND (%s OR %s = ANY(event_names))',
            (webhook_id, org_id, event == TEST_EVENT, event),
        ).fetchone()
    if row is None:
        return None
    secret = token_crypto.decrypt(row['secret_encrypted'])
    if is_err(secret):  # a key rotation: an admin creates the endpoint again
        logger.error('webhooks.secret_unreadable', webhook_id=webhook_id, error_code=secret.error.code)
        return None
    made = attempt_delivery(url=row['url'], secret=secret.value, payload=payload)
    code = made.status
    queued = False
    if code != 410 and is_retryable(code) and event != TEST_EVENT:
        queued = _schedule_retry(org_id, webhook_id, payload, attempt)
    # recorded after the scheduling, so a row never claims a retry the broker refused
    webhook_deliveries.record(
        org_id=org_id,
        webhook_id=webhook_id,
        event_id=made.event_id,
        event_type=event,
        attempt=attempt + 1,
        status=code,
        category=webhook_deliveries.categorize(code, made.no_answer),
        will_retry=queued,
    )
    if code == 410:  # the receiver says stop: kept and shown as revoked, so the admin sees why it went quiet
        _mark_gone(org_id, webhook_id)
        logger.info('webhooks.deregistered_gone', webhook_id=webhook_id)
    return code


def _schedule_retry(org_id: str, webhook_id: str, payload: dict[str, Any], attempt: int) -> bool:
    """Queue the next attempt on ``RETRY_DELAYS_S``; whether it is queued (False once the delays are used up)."""
    from app.worker import deliver_webhooks

    if attempt >= len(RETRY_DELAYS_S):
        logger.warning('webhooks.retries_exhausted', webhook_id=webhook_id, event_name=payload['type'])
        return False
    try:
        deliver_webhooks.apply_async(
            (org_id, webhook_id, payload), {'attempt': attempt + 1}, countdown=RETRY_DELAYS_S[attempt]
        )
    except Exception as exc:
        logger.warning('webhooks.retry_not_scheduled', webhook_id=webhook_id, error_type=type(exc).__name__)
        return False
    return True


def _mark_gone(org_id: str, webhook_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            'UPDATE user_webhooks SET gone_at = now() WHERE id = %s AND organization_id = %s'
            ' AND revoked_at IS NULL AND gone_at IS NULL',
            (webhook_id, org_id),
        )


def _revoke(org_id: str, webhook_id: str) -> bool:
    """An admin's delete: soft-delete the endpoint and drop its delivery history."""
    with get_conn() as conn:
        revoked = conn.execute(
            'UPDATE user_webhooks SET revoked_at = now() WHERE id = %s AND organization_id = %s AND revoked_at IS NULL',
            (webhook_id, org_id),
        ).rowcount
        if revoked:
            conn.execute(
                'DELETE FROM webhook_deliveries WHERE webhook_id = %s AND organization_id = %s',
                (webhook_id, org_id),
            )
        return bool(revoked)


# ── Routes ─────────────────────────────────────────────────────────────────


class Webhook(BaseModel):
    id: UUID
    url: str
    label: str
    event_names: list[str]
    created_at: datetime
    gone_at: datetime | None


class WebhookWithSecret(Webhook):
    secret: str


class Webhooks(BaseModel):
    webhooks: list[Webhook]
    events: list[str]


class WebhookTestResult(BaseModel):
    status: int | None


class WebhookIn(BaseModel):
    url: str = Field(min_length=9, max_length=2000)
    label: str = Field(min_length=1, max_length=120)
    event_names: list[EventName] = Field(min_length=1, max_length=len(EVENTS))


def _invalid_url(message: str) -> HTTPException:
    return api_error(422, 'WEBHOOKS.INVALID_URL', message)


@router.get('', response_model=Webhooks)
def list_webhooks(user: CurrentUser = Auth) -> dict:
    """The organization's live endpoints (never their secrets) and the event names they can subscribe to."""
    with get_conn() as conn:
        rows = conn.execute(
            f'SELECT {_PUBLIC_COLUMNS} FROM user_webhooks WHERE organization_id = %s AND revoked_at IS NULL'
            ' ORDER BY created_at',
            (user.org_id,),
        ).fetchall()
    return {'webhooks': rows, 'events': list(EVENTS)}


@router.post('', status_code=status.HTTP_201_CREATED, response_model=WebhookWithSecret)
def create_webhook(body: WebhookIn, user: CurrentUser = Auth) -> dict:
    """The response is the only time the signing ``secret`` is readable."""
    if not body.url.startswith('https://'):
        raise _invalid_url('Webhook URL must be https.')
    # Checked here as well as on every delivery, so a user learns now about an endpoint that could never fire.
    if is_err(check := validate_url(body.url)):
        raise _invalid_url(check.error.detail)
    secret = new_secret()
    if is_err(encrypted := token_crypto.encrypt(secret)):
        raise api_error(503, 'WEBHOOKS.NOT_CONFIGURED', 'webhooks are not available')
    with get_conn() as conn:
        # the cap holds
        conn.execute('SELECT id FROM organization WHERE id = %s FOR NO KEY UPDATE', (user.org_id,))
        live = conn.execute(
            'SELECT count(*) AS n FROM user_webhooks WHERE organization_id = %s AND revoked_at IS NULL', (user.org_id,)
        ).fetchone()['n']
        if live >= MAX_ENDPOINTS:
            raise api_error(409, 'WEBHOOKS.CAP_REACHED', f'at most {MAX_ENDPOINTS} webhook endpoints')
        row = conn.execute(
            'INSERT INTO user_webhooks (organization_id, user_id, url, secret_encrypted, event_names, label)'
            f' VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_PUBLIC_COLUMNS}',
            (user.org_id, user.id, body.url, encrypted.value, sorted(set(body.event_names)), body.label),
        ).fetchone()
    return {**row, 'secret': secret}


@router.delete('/{webhook_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(webhook_id: UUID, user: CurrentUser = Auth) -> None:
    if not _revoke(user.org_id, str(webhook_id)):
        raise api_error(404, 'WEBHOOKS.NOT_FOUND', 'webhook not found')


_IN_FLIGHT = threading.BoundedSemaphore(4)


@router.post('/{webhook_id}/test', response_model=WebhookTestResult)
def send_test(webhook_id: UUID, user: CurrentUser = Auth) -> dict:
    """Send a signed ``webhook.test`` to one endpoint now and answer with its HTTP status (``null``: no answer).
    Delivered by the webhooks worker like any event, so the API never waits on a stranger past the deadline."""
    # ponytail: 10 tests a minute per user, 1 in flight per organization, 4 per process (one API process: each
    # pins a threadpool slot up to 45 s); a shared Redis counter if the API ever runs more than one.
    hit(user.id, 'webhook_test', 10, 60, 'WEBHOOKS.RATE_LIMITED', 'Too many webhook tests; try again later')
    with get_conn() as conn:
        row = conn.execute(
            'SELECT gone_at FROM user_webhooks WHERE id = %s AND organization_id = %s AND revoked_at IS NULL',
            (webhook_id, user.org_id),
        ).fetchone()
    if row is None:
        raise api_error(404, 'WEBHOOKS.NOT_FOUND', 'webhook not found')
    if row['gone_at']:
        raise api_error(409, 'WEBHOOKS.REVOKED', 'the receiver revoked this webhook with a 410; delete it')
    payload = {'type': TEST_EVENT, 'timestamp': datetime.now(UTC).isoformat(), 'data': {}}
    org_key = f'webhook_test:{user.org_id}'
    if not _IN_FLIGHT.acquire(blocking=False):
        raise _test_busy(user.org_id, 'process')
    try:
        if not claim(org_key, DELIVERY_DEADLINE_S + 15):  # one test in flight an organization
            raise _test_busy(user.org_id, 'organization')
        try:
            return _deliver_test(user.org_id, webhook_id, payload)
        finally:
            release(org_key)
    finally:
        _IN_FLIGHT.release()


def _test_busy(org_id: str, cap: str) -> HTTPException:
    logger.warning('webhooks.test_busy', org_id=org_id, cap=cap)
    return api_error(429, 'WEBHOOKS.TEST_BUSY', 'a webhook test is running, try again in a moment')


def _deliver_test(org_id: str, webhook_id: UUID, payload: dict) -> dict:
    from app.worker import deliver_webhooks

    try:
        result = deliver_webhooks.apply_async(
            (org_id, str(webhook_id), payload),
            ignore_result=False,
            expires=DELIVERY_DEADLINE_S + 15,  # the caller stops waiting then: a task still queued is moot
        )
    except Exception as exc:
        logger.warning('webhooks.test_enqueue_failed', error_type=type(exc).__name__)
        raise api_error(503, 'WEBHOOKS.QUEUE_UNAVAILABLE', 'try again in a moment') from None
    try:  # the result backend pushes the answer (Redis pub/sub): no polling
        code = result.get(timeout=DELIVERY_DEADLINE_S + 15, propagate=False)
    except ResultTimeout:
        code = None
    return {'status': code if isinstance(code, int) else None}
