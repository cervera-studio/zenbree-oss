"""Instantly API v2 (#929): plain httpx, the customer's own API key, one workspace for each project.

Endpoints, all confirmed against https://api.instantly.ai/openapi/api_v2.json on 2026-10-16:
``GET /api/v2/workspaces/current`` (id and name of the key's workspace), ``GET /api/v2/campaigns`` (``limit`` up to 100,
``starting_after`` / ``next_starting_after``), ``GET /api/v2/campaigns/{id}``, ``POST /api/v2/leads/add`` (up to 1000
leads for one ``campaign_id``; the answer holds counts and ``created_leads`` with each lead's input ``index`` and id)
``POST /api/v2/leads/list`` (``campaign`` and ``contacts`` find a campaign's leads by email), and ``GET`` / ``DELETE
/api/v2/leads/{id}`` (#931: a lead's ``status``, and removing the lead, which answers it or 404). Auth is
``Authorization: Bearer <key>``. The workspace allows 100 requests a second and 6,000 a minute, and answers 429.

The caller needs a key that may read the workspace and its campaigns, and create, read and delete leads (scopes
``workspaces:read``, ``campaigns:read``, ``leads:create``, ``leads:read``, and ``leads:delete``, ``leads:all`` or
``all:delete``; or ``all:all``). A key without them works for everything else: only its stops fail
(app/integrations/instantly_sync/stops.py). Instantly answers its DELETE with 401, not 403, after a good GET.
Only the Senders page reads the mailboxes, so their scope is optional: ``accounts:read`` (or ``accounts:all``,
``all:read``, ``all:all``). Without it that page says so and everything else works.

Error codes:
- INSTANTLY.INVALID_KEY      401 (a wrong or revoked key)
- INSTANTLY.PLAN_REQUIRED    402 (the workspace has no active paid plan)
- INSTANTLY.MISSING_SCOPE    403 (the key lacks a scope)
- INSTANTLY.NOT_FOUND        404 (the campaign is gone)
- INSTANTLY.RATE_LIMITED     429 (the request was refused, so nothing was created: safe to retry)
- INSTANTLY.UNREACHABLE      the connection never opened (the request was not sent: safe to retry)
- INSTANTLY.UNKNOWN_OUTCOME  the request went out and no answer came back, or a 5xx or unreadable 2xx: whether it
                             took effect is not known. A caller that creates something never retries blindly.
- INSTANTLY.REJECTED         any other 4xx

ponytail: the API reference documents 401, 402, 404 and 429 only. 403 for a key that lacks a scope is the usual
answer but is not confirmed; if Instantly answers 401 instead, the user sees "invalid key" for a scope problem.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
import structlog

from app.billing.paid_call import paid_call
from app.core.config import settings
from app.core.errors import Result, err, is_err, ok

logger = structlog.get_logger()

PAGE_SIZE = 100  # the largest ``limit`` of a list call
ADD_LIMIT = 1000  # leads in one ``leads/add`` call; the service sends fewer (see instantly_enroll.CHUNK)
MAX_CAMPAIGNS = 500  # five pages: a picker of more than this is a search box, not a list
MAX_ACCOUNTS = 500
_TIMEOUT_SECONDS = 20.0
# The request never reached the server: nothing was created.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# Instantly's campaign status numbers (the ``status`` enum of the API reference).
ACTIVE, RUNNING_SUBSEQUENCES = 1, 4
CAMPAIGN_STATUS = {
    0: 'draft',
    1: 'active',
    2: 'paused',
    3: 'completed',
    4: 'running_subsequences',
    -99: 'account_suspended',
    -1: 'accounts_unhealthy',
    -2: 'bounce_protect',
}
# Instantly's account ``status`` and ``warmup_status`` enums. A value outside them reads ``unknown``.
ACCOUNT_STATUS = {
    1: 'active',
    2: 'paused',
    3: 'maintenance',
    -1: 'connection_error',
    -2: 'soft_bounce_error',
    -3: 'sending_error',
}
WARMUP_STATUS = {0: 'paused', 1: 'active', -1: 'banned', -2: 'spam_folder_unknown', -3: 'permanent_suspension'}
# -1 is a real value of both enums, so the value outside them is one neither uses.
UNKNOWN_STATUS = -999
# Lead statuses at which Instantly sends nothing more: completed, bounced, unsubscribed, skipped.
LEAD_STOPPED = frozenset({3, -1, -2, -3})


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Campaign:
    id: str
    name: str
    status: int

    @property
    def may_send(self) -> bool:
        """Adding a lead can start a send: the campaign is running."""
        return self.status in (ACTIVE, RUNNING_SUBSEQUENCES)


@dataclass(frozen=True, slots=True)
class Account:
    email: str
    status: int
    warmup_status: int
    daily_limit: int | None


@dataclass(frozen=True, slots=True)
class Lead:
    status: int | None  # None: a value outside the API's enum

    @property
    def stopped(self) -> bool:
        return self.status in LEAD_STOPPED


@dataclass(frozen=True, slots=True)
class Added:
    """The answer of one ``leads/add`` call: the created leads by input index (``created``) and the counts."""

    created: dict[int, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


async def request(method: str, path: str, key: str, *, json: dict | None = None, params: dict | None = None):
    """One call with ``key``, its answer mapped to the error codes above. Not billed: callers wrap it in ``paid_call``
    (app/integrations/instantly_sync/client.py calls it too)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.request(
                method,
                f'{settings.instantly_api_base_url.rstrip("/")}{path}',
                headers={'Authorization': f'Bearer {key}'},
                json=json,
                params=params,
            )
    except _NOT_SENT as exc:
        logger.warning('instantly.unreachable', error_type=type(exc).__name__)
        return err('INSTANTLY.UNREACHABLE', detail='Instantly could not be reached')
    except httpx.HTTPError as exc:
        logger.warning('instantly.unknown_outcome', error_type=type(exc).__name__)
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly did not answer')
    return _answer(resp)


_REFUSALS = {
    401: ('INSTANTLY.INVALID_KEY', 'Instantly refused the API key'),
    402: ('INSTANTLY.PLAN_REQUIRED', 'the Instantly workspace has no active paid plan'),
    403: ('INSTANTLY.MISSING_SCOPE', 'the Instantly API key lacks a permission'),
    404: ('INSTANTLY.NOT_FOUND', 'Instantly has no such campaign'),
}


def _answer(resp: httpx.Response) -> Result[Any]:
    status = resp.status_code
    if status in _REFUSALS:
        code, detail = _REFUSALS[status]
        return err(code, detail=detail)
    if status == 429:
        wait = resp.headers.get('Retry-After', '')
        return err(
            'INSTANTLY.RATE_LIMITED', detail='Instantly rate limited', retry_after=int(wait) if wait.isdigit() else None
        )
    if status >= 500:
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail=f'Instantly returned {status}', status=status)
    if not 200 <= status < 300:
        return err('INSTANTLY.REJECTED', detail=f'Instantly returned {status}', status=status)
    try:
        return ok(resp.json())
    except ValueError:
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly sent an unreadable answer')


async def _workspace(key: str) -> Result[Workspace]:
    body = await request('GET', '/api/v2/workspaces/current', key)
    if is_err(body):
        return body
    data = body.value
    if not isinstance(data, dict) or not data.get('id'):
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly named no workspace')
    return ok(Workspace(id=str(data['id'])[:200], name=str(data.get('name') or '')[:200]))


async def workspace(key: str) -> Result[Workspace]:
    """The workspace the key belongs to: the proof that the key works."""
    return await paid_call('instantly.workspace', lambda: _workspace(key))


def _campaign(item: object) -> Campaign | None:
    if not isinstance(item, dict) or not item.get('id'):
        return None
    status = item.get('status')
    return Campaign(
        id=str(item['id']), name=str(item.get('name') or '')[:300], status=status if isinstance(status, int) else -1
    )


async def _paged[T](key: str, path: str, parse: Callable[[object], T | None], cap: int) -> Result[list[T]]:
    """Every item of a list endpoint, page by page with its cursor, up to ``cap``; items ``parse`` rejects are left
    out."""
    found: list[T] = []
    cursor: str | None = None
    while len(found) < cap:
        params: dict[str, Any] = {'limit': PAGE_SIZE}
        if cursor:
            params['starting_after'] = cursor
        body = await request('GET', path, key, params=params)
        if is_err(body):
            return body
        items = body.value.get('items') if isinstance(body.value, dict) else None
        if not isinstance(items, list):
            return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly sent no list')
        found.extend(x for x in map(parse, items) if x)
        cursor = body.value.get('next_starting_after')
        if not cursor or not items:
            break
    return ok(found[:cap])


async def list_campaigns(key: str) -> Result[list[Campaign]]:
    return await paid_call(
        'instantly.list_campaigns', lambda: _paged(key, '/api/v2/campaigns', _campaign, MAX_CAMPAIGNS)
    )


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _account(item: object) -> Account | None:
    if not isinstance(item, dict):
        return None
    email = item.get('email')
    if not isinstance(email, str) or not email.strip():
        return None
    status, warmup = _int(item.get('status')), _int(item.get('warmup_status'))
    return Account(
        email=email.strip()[:320],
        status=status if status in ACCOUNT_STATUS else UNKNOWN_STATUS,
        warmup_status=warmup if warmup in WARMUP_STATUS else UNKNOWN_STATUS,
        daily_limit=_int(item.get('daily_limit')),
    )


async def list_accounts(key: str) -> Result[list[Account]]:
    """The workspace's sending mailboxes, at most :data:`MAX_ACCOUNTS`. Read only: Zenbree changes none of them."""
    return await paid_call('instantly.list_accounts', lambda: _paged(key, '/api/v2/accounts', _account, MAX_ACCOUNTS))


async def _get_campaign(key: str, campaign_id: str) -> Result[Campaign]:
    try:
        campaign_path = str(UUID(campaign_id))
    except ValueError:
        return err('INSTANTLY.NOT_FOUND', detail='Instantly has no such campaign')
    body = await request('GET', f'/api/v2/campaigns/{campaign_path}', key)
    if is_err(body):
        return body
    campaign = _campaign(body.value)
    return (
        ok(campaign) if campaign else err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly sent an unreadable campaign')
    )


async def get_campaign(key: str, campaign_id: str) -> Result[Campaign]:
    return await paid_call('instantly.get_campaign', lambda: _get_campaign(key, campaign_id))


async def _add_leads(key: str, campaign_id: str, leads: list[dict]) -> Result[Added]:
    body: dict[str, Any] = {'campaign_id': campaign_id, 'leads': leads}
    answer = await request('POST', '/api/v2/leads/add', key, json=body)
    if is_err(answer):
        return answer
    data = answer.value
    created = data.get('created_leads') if isinstance(data, dict) else None
    if not isinstance(created, list):
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly sent no result for the leads')
    by_index = {
        item['index']: str(item['id'])
        for item in created
        if isinstance(item, dict) and isinstance(item.get('index'), int) and item.get('id')
    }
    counts = {
        k: v
        for k, v in data.items()
        if k.endswith('_count') or k in ('in_blocklist', 'duplicated_leads')
        if isinstance(v, int)
    }
    return ok(Added(created=by_index, counts=counts))


async def add_leads(key: str, campaign_id: str, leads: list[dict]) -> Result[Added]:
    """Add ``leads`` (``email`` and the supported fields) to the campaign, at most :data:`ADD_LIMIT`. The leads that
    are not in ``created`` were left out by Instantly (blocklist, duplicate, invalid email); its answer gives
    the counts, not a reason for each."""
    if not 0 < len(leads) <= ADD_LIMIT:
        raise ValueError(f'between 1 and {ADD_LIMIT} leads per call')
    return await paid_call('instantly.add_leads', lambda: _add_leads(key, campaign_id, leads))


async def _find_leads(key: str, campaign_id: str, emails: list[str]) -> Result[dict[str, str]]:
    body = {'campaign': campaign_id, 'contacts': emails, 'limit': PAGE_SIZE}
    answer = await request('POST', '/api/v2/leads/list', key, json=body)
    if is_err(answer):
        return answer
    items = answer.value.get('items') if isinstance(answer.value, dict) else None
    if not isinstance(items, list):
        return err('INSTANTLY.UNKNOWN_OUTCOME', detail='Instantly sent no lead list')
    found = {}
    for item in items:
        email = item.get('email') if isinstance(item, dict) else None
        if isinstance(email, str) and item.get('id'):
            found[email.strip().lower()] = str(item['id'])
    return ok(found)


async def find_leads(key: str, campaign_id: str, emails: list[str]) -> Result[dict[str, str]]:
    """The leads of the campaign that have one of ``emails`` (at most :data:`PAGE_SIZE`): email to lead id."""
    if len(emails) > PAGE_SIZE:
        raise ValueError(f'at most {PAGE_SIZE} emails per call')
    return await paid_call('instantly.find_leads', lambda: _find_leads(key, campaign_id, emails))


def _lead_path(lead_id: str) -> str:
    return f'/api/v2/leads/{quote(lead_id, safe="")}'


async def _get_lead(key: str, lead_id: str) -> Result[Lead | None]:
    answer = await request('GET', _lead_path(lead_id), key)
    if is_err(answer):
        return ok(None) if answer.error.code == 'INSTANTLY.NOT_FOUND' else answer
    status = answer.value.get('status') if isinstance(answer.value, dict) else None
    if isinstance(status, float) and status.is_integer():
        status = int(status)
    return ok(Lead(status if isinstance(status, int) and not isinstance(status, bool) else None))


async def get_lead(key: str, lead_id: str) -> Result[Lead | None]:
    """The lead as Instantly has it now, None when it has no such lead."""
    return await paid_call('instantly.get_lead', lambda: _get_lead(key, lead_id))


async def _delete_lead(key: str, lead_id: str) -> Result[None]:
    answer = await request('DELETE', _lead_path(lead_id), key)
    if is_err(answer) and answer.error.code != 'INSTANTLY.NOT_FOUND':
        return answer
    return ok(None)


async def delete_lead(key: str, lead_id: str) -> Result[None]:
    """Removes the lead from its campaign. Ok also when Instantly has no such lead: either way it sends no more."""
    return await paid_call('instantly.delete_lead', lambda: _delete_lead(key, lead_id))
