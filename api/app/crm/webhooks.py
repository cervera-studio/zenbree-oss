"""HubSpot webhooks (E13.1 #120): a changed contact, company or deal re-opens the gate's verdict.

No polling: this is how a block lifts (deal closed, lifecycle changed, owner reassigned) or lands
(a sales call logged, an unsubscribe) between two steps. The route only authenticates and queues;
``app.worker.apply_hubspot_events`` re-reads the affected people (``app.crm.gate_recheck.recheck``).

Subscriptions of the HubSpot app (``ops/hubspot``), target ``<API_PUBLIC_BASE_URL>/v1/webhooks/hubspot``:
``contact.propertyChange`` on ``notes_last_contacted``, ``hs_email_optout``, ``hubspot_owner_id``,
``associatedcompanyid``; ``company.propertyChange`` on ``lifecyclestage``, ``hubspot_owner_id``;
``deal.propertyChange`` on ``dealstage``. Any contact/company/deal event is handled the same way.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256

import structlog
from fastapi import APIRouter, Request

from app.auth.middleware import SystemReason, public
from app.core.config import settings
from app.core.errors_http import api_error

router = APIRouter()
logger = structlog.get_logger()

MAX_BODY_BYTES = 1_000_000  # HubSpot sends at most 100 small events per request
# HubSpot's own rule for v3 signatures: bounds replay of a captured delivery.
MAX_AGE_MS = 300_000
KINDS = ('contact', 'company', 'deal')


def verify_signature(
    *, secret: str, method: str, uri: str, body: bytes, timestamp: str, signature: str, now_ms: float | None = None
) -> bool:
    """``X-HubSpot-Signature-v3``: base64 HMAC-SHA256, keyed by the app's client secret, of
    method + full request URI + body + ``X-HubSpot-Request-Timestamp`` (milliseconds)."""
    if not (secret and timestamp and signature) or not (timestamp.isascii() and timestamp.isdigit()):
        return False
    if abs((now_ms if now_ms is not None else time.time() * 1000) - int(timestamp)) > MAX_AGE_MS:
        return False
    signed = method.encode() + uri.encode() + body + timestamp.encode()
    expected = base64.b64encode(hmac.new(secret.encode(), signed, sha256).digest()).decode()
    return hmac.compare_digest(signature.encode(), expected.encode())


def parse_events(payload: object) -> list[dict]:
    """``[{'portal', 'kind', 'id'}]``, deduplicated: all the recheck needs from a delivery."""
    events: list[dict] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get('subscriptionType') or '').split('.', 1)[0]
        portal, record = item.get('portalId'), item.get('objectId')
        if kind not in KINDS or not portal or not record:
            continue
        event = {'portal': str(portal), 'kind': kind, 'id': str(record)}
        if event not in events:
            events.append(event)
    return events


def _enqueue(events: list[dict]) -> None:
    from app.worker import apply_hubspot_events

    apply_hubspot_events.delay(events)


@router.post('/v1/webhooks/hubspot', include_in_schema=False, dependencies=[public(SystemReason.HUBSPOT_WEBHOOK)])
async def hubspot_webhook(request: Request) -> dict:
    """Unauthenticated by nature: the v3 signature is the only thing between the internet and a forged
    event. 2xx only once the events are queued, so HubSpot retries anything we could not take."""
    secret = settings.hubspot_oauth_client_secret
    if not (secret and settings.api_public_base_url):
        raise api_error(503, 'HUBSPOT.NOT_CONFIGURED', 'HubSpot is not configured')
    body = b''
    async for chunk in request.stream():  # never buffer more than the cap
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise api_error(413, 'HUBSPOT.TOO_LARGE', 'payload too large')
    # The public URL HubSpot signed: behind the tunnel request.url is the container's own address.
    uri = settings.api_public_base_url.rstrip('/') + request.url.path
    if request.url.query:
        uri += f'?{request.url.query}'
    if not verify_signature(
        secret=secret,
        method=request.method,
        uri=uri,
        body=body,
        timestamp=request.headers.get('x-hubspot-request-timestamp', ''),
        signature=request.headers.get('x-hubspot-signature-v3', ''),
    ):
        logger.warning('hubspot.webhook.bad_signature')
        raise api_error(401, 'HUBSPOT.UNAUTHORIZED', 'signature verification failed')
    try:
        events = parse_events(json.loads(body))
    except ValueError:
        raise api_error(400, 'HUBSPOT.INVALID_INPUT', 'body is not JSON') from None
    if events:
        try:
            _enqueue(events)
        except Exception as exc:
            logger.error('hubspot.webhook.enqueue_failed', error_type=type(exc).__name__)
            raise api_error(503, 'HUBSPOT.QUEUE_UNAVAILABLE', 'try again') from exc
    return {'received': len(events)}
