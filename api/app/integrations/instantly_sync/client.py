"""The Instantly API v2 calls of #930, with the customer's key; errors are those of app/integrations/instantly_client.py.

Endpoints, checked against developer.instantly.ai on 2026-10-16:
- ``POST /api/v2/webhooks`` (``target_hook_url``, ``event_type``, ``campaign`` null for every campaign, ``headers``,
  ``name``; scope ``webhooks:create``) answers the webhook with its ``id``; 402 when the plan has no webhooks.
- ``GET`` / ``DELETE /api/v2/webhooks/{id}``: ``status`` 1 active, -1 disabled after failed deliveries.
- ``GET /api/v2/emails`` (``email_type`` sent or received, ``min_timestamp_created``, ``sort_order`` asc,
  ``starting_after``; scope ``emails:read``): 20 requests a minute for the workspace, so the caller budgets it.
- ``POST /api/v2/leads/list`` (``campaign``, ``contacts``: the leads of these emails, as instantly_client.find_leads).

The answers stay wire dicts: app/integrations/instantly_sync/mapping.py is the one reader of their fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.billing.paid_call import paid_call
from app.core.errors import Result, err, is_err, ok
from app.integrations.instantly_client import PAGE_SIZE, request

HEADER = 'X-Zenbree-Webhook-Key'  # the custom header Instantly sends back with every delivery


@dataclass(frozen=True, slots=True)
class Page:
    items: list[dict]
    next_cursor: str | None


async def _create(key: str, url: str, secret: str, name: str) -> Result[str]:
    body = {'target_hook_url': url, 'event_type': 'all_events', 'campaign': None, 'headers': {HEADER: secret}}
    answer = await request('POST', '/api/v2/webhooks', key, json=body | {'name': name})
    if is_err(answer):
        return answer
    remote = answer.value.get('id') if isinstance(answer.value, dict) else None
    return ok(str(remote)) if remote else err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly named no webhook')


async def create_webhook(key: str, *, url: str, secret: str, name: str) -> Result[str]:
    """Instantly's id of a webhook that posts every event of every campaign of the workspace to ``url``."""
    return await paid_call('instantly.create_webhook', lambda: _create(key, url, secret, name))


async def _status(key: str, remote_id: str) -> Result[int | None]:
    answer = await request('GET', f'/api/v2/webhooks/{remote_id}', key)
    if is_err(answer):
        return answer
    status = answer.value.get('status') if isinstance(answer.value, dict) else None
    return ok(status if isinstance(status, int) else None)


async def webhook_status(key: str, remote_id: str) -> Result[int | None]:
    return await paid_call('instantly.get_webhook', lambda: _status(key, remote_id))


async def delete_webhook(key: str, remote_id: str) -> Result[None]:
    async def call() -> Result[None]:
        answer = await request('DELETE', f'/api/v2/webhooks/{remote_id}', key)
        return answer if is_err(answer) else ok(None)

    return await paid_call('instantly.delete_webhook', call)


def _page(answer: Result[Any]) -> Result[Page]:
    if is_err(answer):
        return answer
    items = answer.value.get('items') if isinstance(answer.value, dict) else None
    if not isinstance(items, list):
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly sent no list')
    cursor = answer.value.get('next_starting_after')
    return ok(Page(items=[i for i in items if isinstance(i, dict)], next_cursor=str(cursor) if cursor else None))


async def list_emails(
    key: str, *, email_type: Literal['sent', 'received'], since: datetime, cursor: str | None
) -> Result[Page]:
    """The workspace's emails created at or after ``since``, oldest first, one page."""
    params: dict[str, Any] = {'email_type': email_type, 'sort_order': 'asc', 'limit': PAGE_SIZE}
    params |= {'min_timestamp_created': since.isoformat()}
    if cursor:
        params['starting_after'] = cursor

    async def call() -> Result[Page]:
        return _page(await request('GET', '/api/v2/emails', key, params=params))

    return await paid_call('instantly.list_emails', call)


async def list_leads(key: str, *, campaign_id: str, emails: list[str]) -> Result[Page]:
    """The campaign's leads that have one of ``emails`` (at most :data:`PAGE_SIZE`): one lead per email at most."""
    if not 0 < len(emails) <= PAGE_SIZE:
        raise ValueError(f'between 1 and {PAGE_SIZE} emails per call')
    body: dict[str, Any] = {'campaign': campaign_id, 'contacts': emails, 'limit': PAGE_SIZE}

    async def call() -> Result[Page]:
        return _page(await request('POST', '/api/v2/leads/list', key, json=body))

    return await paid_call('instantly.list_leads', call)
