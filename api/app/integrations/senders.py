"""The senders page (#89): each sending mailbox with its DNS health, today's usage and a pause switch.

Adapted from the earlier implementation ``integrations/use_cases/list_senders.py`` and
``integrations/adapters/dns_deliverability_checker.py``. The DNS grade is a warning for the
screen and is never read by the send path. Pausing is: the dispatcher skips a paused mailbox. The active
project's mailboxes (#307); any member may pause one, for every project it serves.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import dns.asyncresolver
import dns.exception
import dns.resolver
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from app.auth.middleware import CurrentUser, current_user
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import err
from app.core.errors_http import unwrap_or_raise
from app.integrations import project_senders, sender_state
from app.outreach import sender_holds, sending
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/senders')
logger = structlog.get_logger()

Provider = Literal['google', 'microsoft']
Grade = Literal['pass', 'warn', 'fail', 'unknown', 'managed']


class Dns(BaseModel):
    spf: Grade
    dkim: Grade
    dmarc: Grade


class Sender(BaseModel):
    id: UUID
    provider: Provider
    account_label: str | None
    status: Literal['connected', 'error', 'disconnected']
    paused: bool
    state: Literal['ready', 'waiting', 'held']
    reason: sender_state.Reason | None
    resumes_at: datetime | None
    sent_today: int
    daily_cap: int
    warming_up: bool
    domain: str | None
    dns: Dns | None
    send_window_start_hour: int
    send_window_end_hour: int
    send_window_days: list[int]
    send_timezone: str
    held: int


class Senders(BaseModel):
    senders: list[Sender]


class SenderPause(BaseModel):
    id: UUID
    paused: bool


class SenderAccount(BaseModel):
    id: UUID
    provider: Literal['google', 'microsoft', 'bullhorn', 'unipile']
    account_label: str | None
    status: Literal['connected', 'error', 'disconnected']
    connected_by: str | None
    project_ids: list[str]
    can_manage: bool


class SenderAccounts(BaseModel):
    accounts: list[SenderAccount]


class LinkAck(BaseModel):
    status: Literal['ok']


# The SPF include each provider sends through, and its default DKIM selectors
# (Microsoft publishes two CNAMEs and rotates between them).
_PROVIDER_DNS: dict[str, tuple[str, tuple[str, ...]]] = {
    'google': ('_spf.google.com', ('google',)),
    'microsoft': ('spf.protection.outlook.com', ('selector1', 'selector2')),
}
# The provider's own consumer domains: their DNS is not the user's to fix.
_MANAGED_DOMAINS = frozenset(
    {'gmail.com', 'googlemail.com', 'outlook.com', 'outlook.it', 'hotmail.com', 'hotmail.it', 'live.com', 'live.it'}
)
_DOMAIN = re.compile(r'^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$')
_SPF_TARGET = re.compile(r'(?:include:|redirect=)(\S+)', re.IGNORECASE)
_DKIM_KEY = re.compile(r'(?:^|;)\s*p=[A-Za-z0-9+/]', re.IGNORECASE)
_LIFETIME_SECONDS = 3.0
_CHECK_SECONDS = 8.0  # a chain of 3 s lookups (SPF includes) must not hold the page longer
_SPF_LOOKUP_LIMIT = 10  # RFC 7208 §4.6.4
_CACHE_SECONDS = 600
# ponytail: per-process and unbounded; its size is the number of distinct sender domains. Redis if that grows.
_cache: dict[tuple[str, str, str], tuple[float, dict[str, Grade]]] = {}


async def _txt(name: str) -> list[str] | None:
    """TXT strings at ``name``; ``[]`` when there is none, ``None`` when the lookup failed."""
    try:
        answer = await dns.asyncresolver.resolve(name, 'TXT', lifetime=_LIFETIME_SECONDS)
    except dns.resolver.NXDOMAIN, dns.resolver.NoAnswer:
        return []
    except dns.exception.DNSException as exc:
        logger.warning('senders.dns_lookup_failed', name=name, error_type=type(exc).__name__)
        return None
    return [b''.join(r.strings).decode(errors='replace') for r in answer]


def _spf_records(records: list[str] | None) -> list[str]:
    return [r for r in records or [] if r.lower().startswith('v=spf1')]


async def _spf(domain: str, want: str) -> Grade:
    """``pass`` when the provider's include is reachable from the domain's SPF, following includes."""
    root = await _txt(domain)
    if root is None:
        return 'unknown'
    records = _spf_records(root)
    if not records:
        return 'fail'
    seen = {domain}  # the root lookup counts toward the limit
    while records and len(seen) < _SPF_LOOKUP_LIMIT:
        targets = [t.lower().rstrip('.') for r in records for t in _SPF_TARGET.findall(r)]
        if want in targets:
            return 'pass'
        batch = [t for t in dict.fromkeys(targets) if t not in seen][: _SPF_LOOKUP_LIMIT - len(seen)]
        seen.update(batch)
        records = [r for a in await asyncio.gather(*(_txt(t) for t in batch)) for r in _spf_records(a)]
    return 'warn'  # an SPF record that does not authorise this provider


async def _dkim(domain: str, selectors: tuple[str, ...]) -> Grade:
    answers = await asyncio.gather(*(_txt(f'{s}._domainkey.{domain}') for s in selectors))
    if any(_DKIM_KEY.search(r) for a in answers if a for r in a):
        return 'pass'
    return 'unknown' if any(a is None for a in answers) else 'fail'


async def _dmarc(domain: str) -> Grade:
    records = await _txt(f'_dmarc.{domain}')
    if records is None:
        return 'unknown'
    return 'pass' if any(r.lower().startswith('v=dmarc1') for r in records) else 'fail'


async def check_domain(domain: str, provider: str, dkim_selector: str = '') -> dict[str, Grade]:
    """SPF/DKIM/DMARC for a sender domain, cached briefly. Each lookup times out after 3 s, the whole check after 8 s."""
    if domain in _MANAGED_DOMAINS:
        return {'spf': 'managed', 'dkim': 'managed', 'dmarc': 'managed'}
    key = (domain, provider, dkim_selector)
    if (hit := _cache.get(key)) and hit[0] > time.monotonic():
        return hit[1]
    include, selectors = _PROVIDER_DNS[provider]
    checks = {
        'spf': _spf(domain, include),
        'dkim': _dkim(domain, (dkim_selector,) if dkim_selector else selectors),
        'dmarc': _dmarc(domain),
    }
    tasks = {name: asyncio.ensure_future(check) for name, check in checks.items()}
    await asyncio.wait(tasks.values(), timeout=_CHECK_SECONDS)
    grades: dict[str, Grade] = {}
    for name, task in tasks.items():
        if task.done():
            grades[name] = task.result()
        else:  # out of time: what finished is still shown
            task.cancel()
            grades[name] = 'unknown'
    if 'unknown' not in grades.values():  # a failed lookup is retried on the next load
        _cache[key] = (time.monotonic() + _CACHE_SECONDS, grades)
    return grades


def sender_domain(account_label: str | None) -> str | None:
    domain = (account_label or '').rpartition('@')[2].strip().lower().rstrip('.')
    return domain if _DOMAIN.match(domain) else None


def _list(project_id: str, now: datetime) -> list[dict]:
    switches = sender_state.mailbox_kill_switches()
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, provider, status, account_label, paused, daily_limit, send_timezone, dkim_selector, created_at,'
            ' last_used_at, send_window_start_hour, send_window_end_hour, send_window_days'
            " FROM user_oauth_connections WHERE id IN (SELECT sender_id FROM project_senders WHERE project_id = %s) AND provider IN ('google', 'microsoft')"
            " AND status <> 'disconnected' ORDER BY provider, account_label, created_at, id",
            (project_id,),
        ).fetchall()
        held = sender_holds.held_counts(conn, project_id, [r['id'] for r in rows], now)
        for row in rows:
            tz = row['send_timezone'] or settings.outreach_send_window_timezone
            row['sent_today'] = sending.sent_today(conn, row['id'], now, tz)
            row['switches'] = switches[row['provider']]
            row['held'] = held.get(row['id'], 0)
    return rows


async def _sender(row: dict, now: datetime, dns: bool) -> dict:
    domain = sender_domain(row['account_label'])
    full_cap = min(row['daily_limit'] or sending.DAILY_SEND_LIMIT, sending.DAILY_SEND_LIMIT)
    account = sender_state.account_state(row, now=now, used_today=row['sent_today'], **row['switches'])
    cap = account.daily_cap
    return {
        'id': row['id'],
        'provider': row['provider'],
        'account_label': row['account_label'],
        'status': row['status'],
        'paused': row['paused'],
        'state': account.state,
        'reason': account.reason,
        'resumes_at': account.resumes_at,
        'sent_today': account.sent_today,
        'daily_cap': cap,
        'warming_up': cap < full_cap,
        'send_window_start_hour': row['send_window_start_hour'],
        'send_window_end_hour': row['send_window_end_hour'],
        'send_window_days': row['send_window_days'],
        'send_timezone': row['send_timezone'] or settings.outreach_send_window_timezone,
        'held': row['held'],
        'domain': domain,
        'dns': await check_domain(domain, row['provider'], row['dkim_selector']) if domain and dns else None,
    }


@router.get('', response_model=Senders)
async def senders(project_id: str = Depends(current_project), dns: bool = True) -> dict:
    """``dns=false``: caps and states only, for the campaign builder's sender pool (#217), without the lookups."""
    now = datetime.now(UTC)
    rows = await asyncio.to_thread(_list, project_id, now)
    return {'senders': await asyncio.gather(*(_sender(r, now, dns) for r in rows))}


class PauseIn(BaseModel):
    paused: bool


@router.patch('/{sender_id}', response_model=SenderPause)
async def set_paused(
    sender_id: UUID,
    body: PauseIn,
    user: CurrentUser = Depends(current_user),
    project_id: str = Depends(current_project),
) -> dict:
    """Pauses one account, for every project that uses it. 404 ``INTEGRATIONS.MAILBOX_NOT_FOUND`` for an account
    the active project does not use."""

    def _update() -> bool:
        with get_conn() as conn:
            if not project_senders.by_id(conn, project_id, sender_id):
                return False
            conn.execute('UPDATE user_oauth_connections SET paused = %s WHERE id = %s', (body.paused, sender_id))
            return True

    if not await asyncio.to_thread(_update):
        unwrap_or_raise(err('INTEGRATIONS.MAILBOX_NOT_FOUND', detail='no such mailbox'))
    logger.info(
        'integrations.sender.paused' if body.paused else 'integrations.sender.resumed',
        user_id=user.id,
        sender_id=str(sender_id),
    )
    return {'id': sender_id, 'paused': body.paused}


class WindowIn(BaseModel):
    send_window_start_hour: int = Field(ge=0, le=23)
    send_window_end_hour: int = Field(ge=1, le=24)
    send_window_days: list[Annotated[int, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)

    @model_validator(mode='after')
    def _ordered(self) -> WindowIn:
        if self.send_window_start_hour >= self.send_window_end_hour:
            raise ValueError('send_window_start_hour must be before send_window_end_hour')
        return self


@router.put('/{sender_id}/window', response_model=WindowIn)
async def set_window(
    sender_id: UUID,
    body: WindowIn,
    user: CurrentUser = Depends(current_user),
    project_id: str = Depends(current_project),
) -> WindowIn:
    """The hours and weekdays (Monday=0) the mailbox sends in, judged in its own zone; campaigns keep their own."""

    def _update() -> dict | None:
        with get_conn() as conn:
            if not project_senders.by_id(conn, project_id, sender_id):
                return None
            return conn.execute(
                'UPDATE user_oauth_connections SET send_window_start_hour = %s, send_window_end_hour = %s,'
                ' send_window_days = %s WHERE id = %s'
                ' RETURNING send_window_start_hour, send_window_end_hour, send_window_days',
                (body.send_window_start_hour, body.send_window_end_hour, sorted(set(body.send_window_days)), sender_id),
            ).fetchone()

    stored = await asyncio.to_thread(_update)
    if not stored:
        unwrap_or_raise(err('INTEGRATIONS.MAILBOX_NOT_FOUND', detail='no such mailbox'))
        raise LookupError  # unreachable: unwrap_or_raise raised
    logger.info('integrations.sender.window_set', user_id=user.id, sender_id=str(sender_id))
    return WindowIn(**stored)


# ── The organization's accounts and the projects using them (#327) ──


@router.get('/accounts', response_model=SenderAccounts)
async def accounts(user: CurrentUser = Depends(current_user)) -> dict:
    return {'accounts': await asyncio.to_thread(project_senders.accounts, user)}


@router.put('/accounts/{sender_id}/projects/{project_id}', response_model=LinkAck)
async def link(sender_id: UUID, project_id: UUID, user: CurrentUser = Depends(current_user)) -> dict:
    """Use the account in that project too (403 ``INTEGRATIONS.LINK_FORBIDDEN`` unless the caller connected it or is an
    admin; 403 ``INTEGRATIONS.REPLACE_FORBIDDEN`` over a colleague's live sender of the same provider)."""
    unwrap_or_raise(await asyncio.to_thread(project_senders.link, user, sender_id, project_id))
    logger.info('integrations.sender.linked', user_id=user.id, sender_id=str(sender_id), project_id=str(project_id))
    return {'status': 'ok'}


@router.delete('/accounts/{sender_id}/projects/{project_id}', response_model=LinkAck)
async def unlink(sender_id: UUID, project_id: UUID, user: CurrentUser = Depends(current_user)) -> dict:
    """Stop using the account in that project; it stays connected for the organization."""
    unwrap_or_raise(await asyncio.to_thread(project_senders.unlink, user, sender_id, project_id))
    logger.info('integrations.sender.unlinked', user_id=user.id, sender_id=str(sender_id), project_id=str(project_id))
    return {'status': 'ok'}


# ── Releasing a deleted account's senders (#333; app.auth.account_delete calls it) ──

_RELEASED = (
    "UPDATE user_oauth_connections SET status = 'disconnected', status_detail = %s,"
    " refresh_token_encrypted = '', granted_scopes = '{}', updated_at = now(),"
    # a LinkedIn id is unique across organizations (#317): freed, so the account can be connected again
    " provider_account_id = CASE WHEN provider = 'unipile' THEN NULL ELSE provider_account_id END WHERE id = %s"
)


async def release_senders_of(user_id: UUID | str, org: UUID | str | None = None, detail: str = 'owner_deleted') -> int:
    """Revoke each sender the user connected at its provider, best-effort (Google's grant unless another organization
    still uses it, the Unipile account, HubSpot), drop the token, mark it disconnected as ``detail`` and unlink it from
    every client. All organizations before an account is deleted (#333; app.auth.account_delete calls it); only
    ``org`` for a removed member (#471), whose campaigns keep their owner. The row stays for the sends that name it;
    the migrations' triggers do the same without the provider calls if this never ran."""
    from app.integrations.member_release import revoke_at_provider

    def _rows() -> list[dict]:
        with get_conn() as conn:
            return conn.execute(
                'SELECT id, provider, refresh_token_encrypted, provider_account_id FROM user_oauth_connections'
                " WHERE user_id = %s AND status <> 'disconnected' AND (%s::uuid IS NULL OR organization_id = %s)",
                (user_id, org, org),
            ).fetchall()

    def _release(sender_id) -> None:
        with get_conn() as conn:
            conn.execute('SELECT unlink_sender_accounts(ARRAY[%s]::uuid[])', (sender_id,))
            conn.execute(_RELEASED, (detail, sender_id))

    def _mark_campaigns() -> None:  # the trigger does it too, at the delete itself
        with get_conn() as conn:
            conn.execute('UPDATE outreach_campaigns SET former_owner_id = owner_id WHERE owner_id = %s', (user_id,))

    if org is None:
        await asyncio.to_thread(_mark_campaigns)
    rows = await asyncio.to_thread(_rows)
    for row in rows:
        await revoke_at_provider(row)
        await asyncio.to_thread(_release, row['id'])
    return len(rows)
