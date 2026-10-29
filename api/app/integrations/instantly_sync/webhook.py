"""The callback of the webhook Zenbree registers in an Instantly workspace (#930).

Instantly does not sign a delivery. It sends back the custom header Zenbree gave it at creation, a secret of 32 random
bytes that only this subscription's sha256 matches. The URL names the subscription, never an organization: the one
read across organizations finds the subscription, its secret and its connection; every write after it runs for the
organization that subscription belongs to. A retired subscription, or one of a disconnected connection, is refused.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import structlog
from fastapi import APIRouter, Request

from app.auth.middleware import SystemReason, public
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.scope import acting_for
from app.integrations.instantly_sync import effects, mapping, reconcile, store, subscription
from app.integrations.instantly_sync.client import HEADER
from app.integrations.instantly_sync.vocabulary import Fact, ReplyNature

router = APIRouter()
logger = structlog.get_logger()

MAX_BODY_BYTES = 1_000_000  # one event, but a reply carries its HTML and the quoted thread (as the HubSpot cap)
_UNKNOWN = bytes(32)  # compared when no row matches, so an unknown id takes as long as a wrong secret


def _subscription(subscription_id: str) -> dict | None:
    """As System: the subscription and its connection, whatever organization they are in."""
    with get_conn() as conn:
        return conn.execute(
            'SELECT s.id::text, s.organization_id::text, s.project_id::text, s.connection_id::text, s.workspace_id,'
            ' s.secret_sha256 FROM instantly_webhook_subscriptions s'
            ' JOIN instantly_connections c ON c.id = s.connection_id'
            " WHERE s.id = %s AND s.status <> 'retired' AND c.status <> 'disconnected'",
            (subscription_id,),
        ).fetchone()


def _record(found: dict, payload: dict) -> str:
    binding = store.Binding(
        organization_id=found['organization_id'],
        project_id=found['project_id'],
        connection_id=found['connection_id'],
        workspace_id=found['workspace_id'],
    )
    fact = mapping.from_webhook(payload, now=datetime.now(UTC))
    with acting_for(binding.organization_id, 'instantly webhook delivery'), get_conn() as conn:
        applied = effects.record_and_apply(conn, binding, [fact])
        subscription.delivered(conn, found['id'])
        done = applied.recorded
        if done.changed and isinstance(fact, Fact) and fact.reply_nature is ReplyNature.UNCLASSIFIED:
            reconcile.classify_soon(conn, binding.connection_id)
    applied.announce()
    if done.changed:
        return 'recorded'
    return 'duplicate' if done.duplicate else 'ignored'


@router.post(
    '/v1/integrations/instantly/webhooks/{subscription_id}',
    include_in_schema=False,
    dependencies=[public(SystemReason.INSTANTLY_WEBHOOK)],
)
async def instantly_webhook(subscription_id: UUID, request: Request) -> dict:
    """2xx only once the fact is stored, so Instantly retries what could not be taken."""
    secret = request.headers.get(HEADER, '')
    if not secret:
        raise api_error(401, 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED', 'invalid webhook secret')
    body = b''
    async for chunk in request.stream():  # never buffer more than the cap
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise api_error(413, 'REQUEST.TOO_LARGE', 'payload too large')
    found = await asyncio.to_thread(_subscription, str(subscription_id))
    expected = found['secret_sha256'] if found else _UNKNOWN
    if not hmac.compare_digest(subscription.digest(secret), bytes(expected)) or found is None:
        logger.warning('instantly.webhook.unauthorized', subscription_id=str(subscription_id))
        raise api_error(401, 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED', 'invalid webhook secret')
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise api_error(422, 'INTEGRATIONS.INVALID_INPUT', 'object payload required')
    try:
        status = await asyncio.to_thread(_record, found, payload)
    except psycopg.Error as exc:
        logger.error('instantly.webhook.store_failed', error_type=type(exc).__name__)
        raise api_error(503, 'INTEGRATIONS.UNAVAILABLE', 'try again') from exc
    logger.info('instantly.webhook', status=status, event_type=str(payload.get('event_type'))[:60])
    return {'status': status}
