"""The public URL of a webhook automation (#1031): one signed call names one person and starts one run.

The sender signs as Standard Webhooks do (``webhook-id``, ``webhook-timestamp``, ``webhook-signature``), with the
secret its automation's creator made (``hook.rotate``). The URL names a hook id, never an organization: the one read
across organizations finds the hook, its secret and its automation; everything after it runs in that organization
(``deliveries.record``). A call that fails the signature writes nothing. The logs carry the automation and the answer,
never the hook id, the secret, the body or a person.
"""

from __future__ import annotations

import asyncio
import base64
import time
from uuid import UUID

import psycopg
import structlog
from fastapi import APIRouter, HTTPException, Request

from app.auth.middleware import SystemReason, public
from app.automations import deliveries
from app.core.db import get_conn
from app.core.errors import is_ok
from app.core.errors_http import api_error
from app.core.rate_limit import hit
from app.core.scope import acting_for
from app.integrations import token_crypto
from app.outreach.inbound import MAX_SKEW_SECONDS, verify_svix

router = APIRouter()
logger = structlog.get_logger()

MAX_BODY_BYTES = 16 * 1024  # one person: an email and a LinkedIn URL, with what a sender adds around them
MAX_WEBHOOK_ID = 200
PER_AUTOMATION = 60  # calls a minute
PER_ORGANIZATION = 300
# Checked when no hook matches, so an unknown hook id still runs the HMAC that a wrong secret runs.
_UNKNOWN = 'whsec_' + base64.b64encode(bytes(32)).decode()


def _unauthorized(why: str) -> HTTPException:
    logger.warning('automation.webhook.unauthorized', reason=why)
    return api_error(deliveries.UNAUTHORIZED.status, deliveries.UNAUTHORIZED.code, deliveries.UNAUTHORIZED.message)


def _hook(hook_id: UUID) -> dict | None:
    """As System: the hook and its automation, whatever organization they are in."""
    with get_conn() as conn:
        return conn.execute(
            'SELECT automation_id::text, organization_id::text, secret_ciphertext FROM automation_webhooks'
            ' WHERE hook_id = %s',
            (hook_id,),
        ).fetchone()


def _secret(found: dict | None) -> str | None:
    if found is None:
        return None
    opened = token_crypto.decrypt(found['secret_ciphertext'])
    return opened.value if is_ok(opened) else None


def _record(found: dict, webhook_id: str, body: bytes) -> deliveries.Answer:
    with acting_for(found['organization_id'], 'automation webhook delivery'):
        return deliveries.record(found['automation_id'], webhook_id, found['secret_ciphertext'], body)


def _limit(found: dict) -> None:
    message = 'too many calls: try again later'
    code = 'AUTOMATION.WEBHOOK_RATE_LIMITED'
    hit(f'automation:{found["automation_id"]}', 'automation_webhook', PER_AUTOMATION, 60, code, message)
    hit(f'organization:{found["organization_id"]}', 'automation_webhook_org', PER_ORGANIZATION, 60, code, message)


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


@router.post(
    '/v1/automation-hooks/{hook_id}',
    status_code=202,
    include_in_schema=False,
    dependencies=[public(SystemReason.AUTOMATION_WEBHOOK)],
)
async def automation_webhook(hook_id: str, request: Request) -> dict:
    """202 once the call is stored; a refusal is a 4xx, and the same ``webhook-id`` again gets the same answer."""
    webhook_id = request.headers.get('webhook-id', '')
    timestamp = request.headers.get('webhook-timestamp', '')
    signature = request.headers.get('webhook-signature', '')
    if not (webhook_id and timestamp and signature) or len(webhook_id) > MAX_WEBHOOK_ID:
        raise _unauthorized('headers')
    try:
        signed_at = int(timestamp)
    except ValueError:
        raise _unauthorized('headers') from None
    # Before any lookup: a stale call learns nothing about the hook.
    if abs(time.time() - signed_at) > MAX_SKEW_SECONDS:
        raise api_error(401, 'AUTOMATION.WEBHOOK_STALE', 'the webhook-timestamp is more than 5 minutes off')
    body = b''
    async for chunk in request.stream():  # never buffer more than the cap
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise api_error(413, 'REQUEST.TOO_LARGE', 'payload too large')
    parsed = _parse_uuid(hook_id)
    found = await asyncio.to_thread(_hook, parsed) if parsed else None
    secret = _secret(found)
    signed = verify_svix(
        secret=secret or _UNKNOWN, msg_id=webhook_id, timestamp=timestamp, signature=signature, body=body
    )
    if not signed or secret is None or found is None:
        raise _unauthorized('signature')
    await asyncio.to_thread(_limit, found)
    try:
        answer = await asyncio.to_thread(_record, found, webhook_id, body)
    except psycopg.Error as exc:
        logger.error('automation.webhook.store_failed', error_type=type(exc).__name__)
        raise api_error(503, 'AUTOMATION.WEBHOOK_UNAVAILABLE', 'try again') from exc
    match answer:
        case deliveries.Accepted(delivery_id):
            logger.info('automation.webhook', automation_id=found['automation_id'], answer='accepted')
            return {'delivery_id': delivery_id}
        case deliveries.Refused(code):
            logger.info('automation.webhook', automation_id=found['automation_id'], answer=code)
            raise api_error(*deliveries.REFUSALS[code])
        case deliveries.Transient(status, code, message, retry_after):
            logger.info('automation.webhook', automation_id=found['automation_id'], answer=code)
            headers = {'Retry-After': str(retry_after)} if retry_after else None
            raise api_error(status, code, message, headers=headers)
