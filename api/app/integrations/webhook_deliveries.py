"""The delivery history of the outbound webhooks (#943): what an admin reads to see why an endpoint fails.

One row per attempt, written by ``webhooks.fire`` after the POST. A row holds the event id (the ``webhook-id``
header, stable across retries), its type, the attempt number, the receiver's HTTP status and a failure category.
It never holds the payload (a reply's sender, a target's email), the URL (Zapier-style URLs carry a token) or the
response body. The newest ``KEPT_PER_ENDPOINT`` rows of an endpoint stay: the writer prunes in its own transaction,
so the table is bounded without a beat task that would cross organizations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, require_org_admin_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error

logger = structlog.get_logger()
router = APIRouter(prefix='/v1/integrations/webhooks', tags=['Webhooks'])

TEST_EVENT = 'webhook.test'
KEPT_PER_ENDPOINT = 50
Category = Literal['timeout', 'blocked', 'unreachable', 'rate_limited', 'server_error', 'rejected', 'gone']
# No answer: why. ``timeout`` is the connect/read timeout or the deadline, ``blocked`` the SSRF guard, ``unreachable`` DNS and connection.
NoAnswer = Literal['timeout', 'blocked', 'unreachable']


def categorize(status: int | None, no_answer: NoAnswer | None = None) -> Category | None:
    """The failure category of an attempt; ``None`` for a 2xx. 3xx and other 4xx are ``rejected``: not retried.
    A 410 is ``gone``: the endpoint is marked revoked and stays listed."""
    if status is None:
        return no_answer or 'unreachable'
    if status == 410:
        return 'gone'
    if status == 429:
        return 'rate_limited'
    if status >= 500:
        return 'server_error'
    return None if 200 <= status < 300 else 'rejected'


def record(
    *,
    org_id: str,
    webhook_id: str,
    event_id: str,
    event_type: str,
    attempt: int,
    status: int | None,
    category: Category | None,
    will_retry: bool,
) -> None:
    """Add one attempt and drop the endpoint's rows past the newest ``KEPT_PER_ENDPOINT``. Never raises: losing a
    history row must not stop a retry or the 410 mark."""
    try:
        with get_conn() as conn:
            # Only while the endpoint is live: a delivery that outlives a delete (up to 30 s) must leave no row.
            conn.execute(
                'INSERT INTO webhook_deliveries (organization_id, webhook_id, event_id, event_type, attempt,'
                ' status_code, category, will_retry) SELECT %(org)s, %(w)s, %(e)s, %(t)s, %(a)s, %(s)s, %(c)s, %(r)s'
                ' WHERE EXISTS (SELECT 1 FROM user_webhooks WHERE id = %(w)s AND organization_id = %(org)s'
                ' AND revoked_at IS NULL AND gone_at IS NULL)',
                {
                    'org': org_id,
                    'w': webhook_id,
                    'e': event_id,
                    't': event_type,
                    'a': attempt,
                    's': status,
                    'c': category,
                    'r': will_retry,
                },
            )
            conn.execute(
                'DELETE FROM webhook_deliveries WHERE webhook_id = %(w)s AND id NOT IN'
                ' (SELECT id FROM webhook_deliveries WHERE webhook_id = %(w)s'
                ' ORDER BY attempted_at DESC, id DESC LIMIT %(n)s)',
                {'w': webhook_id, 'n': KEPT_PER_ENDPOINT},
            )
    except Exception as exc:
        logger.warning('webhooks.history_not_recorded', webhook_id=webhook_id, error_type=type(exc).__name__)


class Delivery(BaseModel):
    id: UUID
    event_id: str
    event_type: str
    # a ``webhook.test`` is never a production event: the list marks it
    is_test: bool
    attempt: int
    attempted_at: datetime
    status_code: int | None
    category: Category | None
    will_retry: bool


class Deliveries(BaseModel):
    deliveries: list[Delivery]


@router.get('/{webhook_id}/deliveries', response_model=Deliveries)
def list_deliveries(webhook_id: UUID, user: CurrentUser = Depends(require_org_admin_or_api_key)) -> dict:
    """The newest attempts to one endpoint, newest first, at most ``KEPT_PER_ENDPOINT``. No payload, no URL."""
    with get_conn() as conn:
        if not conn.execute(
            'SELECT 1 FROM user_webhooks WHERE id = %s AND organization_id = %s AND revoked_at IS NULL',
            (webhook_id, user.org_id),
        ).fetchone():
            raise api_error(404, 'WEBHOOKS.NOT_FOUND', 'webhook not found')
        rows = conn.execute(
            'SELECT id, event_id, event_type, event_type = %s AS is_test, attempt, attempted_at, status_code,'
            ' category, will_retry FROM webhook_deliveries WHERE webhook_id = %s AND organization_id = %s'
            ' ORDER BY attempted_at DESC, id DESC LIMIT %s',
            (TEST_EVENT, webhook_id, user.org_id, KEPT_PER_ENDPOINT),
        ).fetchall()
    return {'deliveries': rows}
