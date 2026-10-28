"""LinkedIn connect via Unipile: hosted link (preferred) or credentials + checkpoint.

Adapted from the earlier implementation ``outreach/linkedin_api.py`` + ``use_cases/linkedin_connections.py``
without the campaign half. We store only the Unipile account id; the LinkedIn
password is forwarded to Unipile and dropped, never stored or logged.

The account is one row of the organization used by projects (#307, ``app.integrations.project_senders``): any
member sends from a project's account; only whoever connected it, or an admin, replaces or disconnects it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Literal, cast

import redis
import structlog
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, SecretStr

from app.auth.middleware import CurrentUser, SystemReason, current_user, public
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import Result, err, is_err, ok
from app.core.errors_http import api_error, unwrap_or_raise
from app.core.scope import acting_as
from app.integrations import project_senders, token_crypto
from app.integrations.mailboxes import Disconnected
from app.integrations.project_senders import may_disconnect
from app.linkedin import webhook_events
from app.linkedin.direct_messages import message_received
from app.linkedin.unipile_client import UnipileClient
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/linkedin')
_UNIPILE = [public(SystemReason.LINKEDIN_WEBHOOK)]  # the caller is Unipile: the account id names the connection
logger = structlog.get_logger()

RISK_NOTICE = (
    "LinkedIn non consente l'automazione: collegando l'account accetti che LinkedIn possa "
    'limitarlo o sospenderlo. Zenbree rispetta limiti giornalieri, pause casuali e orari '
    'lavorativi, ma il rischio non è zero. La password resta a Unipile, noi non la salviamo.'
)
_HEALTHY = {'OK', 'CONNECTED', 'RECONNECTED', 'CREATION_SUCCESS', 'SYNC_SUCCESS'}
_UNHEALTHY = {'ERROR', 'STOPPED', 'CREDENTIALS'}


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def _rate_limit(*keys: str, per_minute: int = 5) -> None:
    """Fixed 60s window in Redis, shared by every API process. Keyed by user AND by the
    LinkedIn login, so new Zenbree accounts can't be used to keep guessing one password.
    Fails closed: without Redis there are no password attempts."""
    try:
        r = _redis()
        for key in keys:
            name = f'rl:linkedin:{hashlib.sha256(key.encode()).hexdigest()[:32]}'
            n = cast(int, r.incr(name))  # sync client; redis-py types replies as Awaitable | Any
            if n == 1:
                r.expire(name, 60)
            if n > per_minute:
                raise api_error(429, 'INTEGRATIONS.RATE_LIMITED', 'Too many attempts')
    except redis.RedisError:
        raise api_error(503, 'INTEGRATIONS.UNAVAILABLE', 'Try again later') from None


def _client() -> UnipileClient:
    return UnipileClient()


# ── storage ──────────────────────────────────────────────────────────────────


def get_account(project_id: str) -> dict | None:
    with get_conn() as conn:
        return project_senders.linked_one(conn, project_id, 'unipile')


def connected_account_id(project_id: str) -> str | None:
    """The project's Unipile account id tools act with, or None when LinkedIn is not usable."""
    row = get_account(project_id)
    if row and row['status'] == 'connected' and row['provider_account_id']:
        return row['provider_account_id']
    return None


def _project_organization(project_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT organization_id::text AS org_id FROM projects WHERE id = %s', (project_id,)
        ).fetchone()
    return row['org_id'] if row else None


def _store(user_id: str, project_id: str, account_id: str, *, name: str | None, premium: bool | None) -> Result[None]:
    encrypted = token_crypto.encrypt(account_id)
    if is_err(encrypted):
        return encrypted
    return project_senders.attach(
        user_id,
        project_id,
        'unipile',
        account=account_id,
        cols={
            'refresh_token_encrypted': encrypted.value,
            'provider_account_id': account_id,
            'account_label': name,
            'linkedin_account_name': name,
            'linkedin_premium': premium,
            'linkedin_premium_checked_at': datetime.now(UTC),
        },
    )


async def _refuse_foreign_replace(user: CurrentUser, project_id: str) -> None:
    """The replace rule, checked before Unipile creates or re-authorises anything: a refusal after the fact would
    leave the new account live with no row to revoke it, and a reconnect would re-bind a colleague's account."""
    # ponytail: the sender can change between this read and attach (attach re-checks under lock, but the
    # Unipile account then stays orphaned); closing it needs a compensating delete, which needs a SystemReason.
    row = await asyncio.to_thread(get_account, project_id)
    if row and row['status'] != 'disconnected' and not may_disconnect(row, user):
        unwrap_or_raise(
            err(
                'INTEGRATIONS.REPLACE_FORBIDDEN',
                detail="this client already sends from a colleague's account: only they or an admin can replace it",
            )
        )


def _pending_key(user_id: str, project_id: str, account_id: str) -> str:
    return f'linkedin:checkpoint:{user_id}:{project_id}:{account_id}'


async def _settle(user_id: str, project_id: str, payload: dict) -> Result[dict]:
    """A Unipile answer → a stored account, or a checkpoint the UI must answer."""
    checkpoint = payload.get('checkpoint')
    if isinstance(checkpoint, dict) or payload.get('object') == 'Checkpoint':
        kind = checkpoint.get('type') if isinstance(checkpoint, dict) else None
        pending = str(payload.get('account_id') or '')
        if pending:  # /checkpoint answers only for the account this user's own login started
            try:
                _redis().setex(_pending_key(user_id, project_id, pending), 900, '1')
            except redis.RedisError:
                raise api_error(503, 'INTEGRATIONS.UNAVAILABLE', 'Try again later') from None
        return ok(
            {
                'connected': False,
                'checkpoint': str(kind or 'UNKNOWN'),
                'account_id': str(payload.get('account_id') or ''),
            }
        )
    account_id = str(payload.get('account_id') or payload.get('id') or '')
    if not account_id:
        return err('INTEGRATIONS.INVALID_JSON', detail='LinkedIn connected but returned no account id')
    return await _attach(user_id, project_id, account_id)


async def _attach(user_id: str, project_id: str, account_id: str) -> Result[dict]:
    # Best-effort plan + name read; attaching never fails on it.
    read = await _client().read_account(account_id=account_id)
    name = premium = None
    if not is_err(read):
        name = read.value.name
        features = read.value.premium_features
        premium = None if features is None else 'premium' in features
    stored = await asyncio.to_thread(_store, user_id, project_id, account_id, name=name, premium=premium)
    if is_err(stored):
        return stored
    return ok({'connected': True, 'checkpoint': None, 'account_id': account_id})


# ── routes ───────────────────────────────────────────────────────────────────


class LinkedinAccount(BaseModel):
    connected: bool
    checkpoint: str | None
    account_id: str


class LinkedinConnect(BaseModel):
    risk_notice: str
    connected: bool | None = None
    checkpoint: str | None = None
    account_id: str | None = None
    url: str | None = None


class LinkedinStatus(BaseModel):
    connected: bool
    status: Literal['connected', 'error', 'disconnected']
    account_label: str | None
    premium: bool | None
    can_disconnect: bool
    risk_notice: str


class WebhookHandled(BaseModel):
    handled: bool
    event: Literal['hosted_auth', 'new_relation', 'message_received', 'account_status', 'ignored']
    targets: int | None = None
    reason: str | None = None
    healthy: bool | None = None
    status: Literal['ignored', 'excluded', 'duplicate', 'accepted'] | None = None
    id: str | None = None
    kind: Literal['reply', 'auto_reply', 'unsubscribe'] | None = None


class ConnectIn(BaseModel):
    # The user must accept the restriction risk; enforced here, not only in the UI.
    risk_accepted: bool = False
    # Both set → credentials path; both absent → hosted link.
    username: str | None = Field(default=None, min_length=3, max_length=254)
    # SecretStr keeps the password out of repr, validation errors and logs.
    password: SecretStr | None = Field(default=None, min_length=1, max_length=256)


class CheckpointIn(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    code: SecretStr = Field(min_length=1, max_length=32)


@router.post('/connect', response_model=LinkedinConnect, response_model_exclude_unset=True)
async def connect(
    body: ConnectIn, user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)
) -> dict:
    """No credentials → Unipile hosted-auth ``{url}``. Credentials → connected or a checkpoint."""
    if not body.risk_accepted:
        raise api_error(400, 'LINKEDIN.RISK_NOT_ACCEPTED', RISK_NOTICE)
    await _refuse_foreign_replace(user, project_id)
    if body.username and body.password:
        _rate_limit(f'user:{user.id}', f'login:{body.username.strip().lower()}')
        attached = await _client().connect_linkedin(
            username=body.username,
            password=body.password.get_secret_value(),
        )
        if not is_err(attached):
            attached = await _settle(user.id, project_id, attached.value)
        return {**unwrap_or_raise(attached), 'risk_notice': RISK_NOTICE}

    base = settings.api_public_base_url.rstrip('/')
    if not base:
        unwrap_or_raise(err('INTEGRATIONS.NOT_CONFIGURED', detail='public API URL is not configured'))
    state = unwrap_or_raise(token_crypto.issue_state(user_id=user.id, provider='unipile', project_id=project_id))
    row = await asyncio.to_thread(get_account, project_id)
    # A broken account is re-authorised in place so it keeps its id.
    broken = str(row['provider_account_id'] or '') if row and row['status'] != 'connected' else ''
    web = settings.web_origin.rstrip('/')
    link = {
        'state': state,
        'notification_name': user.id,
        'notify_url': f'{base}/v1/integrations/linkedin/webhook',
        'success_redirect_url': f'{web}/settings/integrations?linkedin=connected',
        'failure_redirect_url': f'{web}/settings/integrations?linkedin=failed',
    }
    hosted = await _client().create_hosted_link(**link, reconnect_account=broken)
    if broken and is_err(hosted) and hosted.error.code == 'OUTREACH.PROVIDER_REJECTED':
        hosted = await _client().create_hosted_link(**link)  # Unipile no longer has it
    return {'url': unwrap_or_raise(hosted), 'risk_notice': RISK_NOTICE}


@router.post('/checkpoint', response_model=LinkedinAccount)
async def checkpoint(
    body: CheckpointIn, user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)
) -> dict:
    await _refuse_foreign_replace(user, project_id)
    _rate_limit(f'user:{user.id}', f'account:{body.account_id}', per_minute=10)
    pending = _pending_key(user.id, project_id, body.account_id)
    try:
        started = _redis().get(pending)
    except redis.RedisError:
        raise api_error(503, 'INTEGRATIONS.UNAVAILABLE', 'Try again later') from None
    if not started:
        raise api_error(422, 'INTEGRATIONS.INVALID_INPUT', 'no pending LinkedIn login for this account')
    solved = await _client().solve_checkpoint(account_id=body.account_id, code=body.code.get_secret_value())
    if not is_err(solved):
        solved = await _settle(user.id, project_id, solved.value)
    if not is_err(solved) and solved.value['connected']:
        _redis().delete(pending)
    return unwrap_or_raise(solved)


@router.get('', response_model=LinkedinStatus)
async def status(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> dict:
    """The active project's account. ``can_disconnect``: the caller connected it or is an admin."""
    row = await asyncio.to_thread(get_account, project_id)
    connected = bool(row and row['status'] == 'connected')
    return {
        'connected': connected,
        'status': row['status'] if row else 'disconnected',
        'account_label': row['account_label'] if row else None,
        'premium': row['linkedin_premium'] if row else None,
        'can_disconnect': may_disconnect(row, user),
        'risk_notice': RISK_NOTICE,
    }


@router.delete('', response_model=Disconnected)
async def disconnect(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> dict:
    row = await asyncio.to_thread(get_account, project_id)
    if not may_disconnect(row, user):
        unwrap_or_raise(
            err('INTEGRATIONS.DISCONNECT_FORBIDDEN', detail='only whoever connected it or an admin can disconnect it')
        )

    def _set(row: dict, status: str, detail: str | None) -> None:
        with get_conn() as conn:
            conn.execute(
                'UPDATE user_oauth_connections SET status = %s, status_detail = %s WHERE id = %s',
                (status, detail, row['id']),
            )

    if not row:
        return {'connected': False}
    # Before the delete call: Unipile's own DELETED report must find the row disconnected, not in error.
    await asyncio.to_thread(_set, row, 'disconnected', None)
    if row['provider_account_id']:
        deleted = await _client().delete_account(account_id=row['provider_account_id'])
        if is_err(deleted):
            await asyncio.to_thread(_set, row, row['status'], row['status_detail'])
            unwrap_or_raise(deleted)
    return {'connected': False}


@router.post('/webhook', response_model=WebhookHandled, response_model_exclude_unset=True, dependencies=_UNIPILE)
async def webhook(request: Request, state: str | None = Query(default=None, max_length=4_000)) -> dict:
    """Two Unipile callers on one route; the second sends account status and ``new_relation``.

    - hosted-auth notify (``?state=`` we minted): the Fernet state IS the auth,
      it names the user and the project and expires in 15 min.
    - account-status webhook: Unipile does not sign, it sends a static header
      chosen at webhook creation; compared constant-time, unset secret → 401. Both before parsing (#419).
    """
    secret = settings.unipile_webhook_secret
    if state is not None:
        owner = unwrap_or_raise(token_crypto.verify_state(state, provider='unipile'))
    elif not secret or not hmac.compare_digest(
        (request.headers.get(settings.unipile_webhook_auth_header) or '').encode(), secret.encode()
    ):
        raise api_error(401, 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED', 'invalid webhook secret')
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise api_error(422, 'INTEGRATIONS.INVALID_INPUT', 'object payload required')
    if state is not None:
        account_id = str(payload.get('account_id') or payload.get('id') or '')
        if not account_id:
            raise api_error(422, 'INTEGRATIONS.INVALID_INPUT', 'account_id is required')
        project_id = token_crypto.state_project(state)
        if not project_id:
            raise api_error(422, 'INTEGRATIONS.INVALID_STATE', 'state names no project')
        # ponytail: an account another user holds answers Unipile 409 and stores nothing, but the hosted flow
        # has already redirected; the user sees LinkedIn not connected with no reason. Telling them needs the
        # failure stored for the integrations page to read.
        org_id = await asyncio.to_thread(_project_organization, project_id)
        if org_id is None:
            raise api_error(422, 'INTEGRATIONS.INVALID_STATE', 'state names no project')
        # As the user the state names, in the project's organization: one who left since is refused (401).
        with acting_as(org_id, owner):
            unwrap_or_raise(await _attach(owner, project_id, account_id))
        return {'handled': True, 'event': 'hosted_auth'}

    event = str(payload.get('event') or '').lower()
    if event == 'new_relation':
        return await asyncio.to_thread(webhook_events.accepted, payload)
    if event in ('message_received', 'new_message'):
        return await asyncio.to_thread(message_received, payload)

    body = payload.get('AccountStatus')
    if not isinstance(body, dict):
        return {'handled': False, 'event': 'ignored'}
    account_id = str(body.get('account_id') or '')
    message = str(body.get('message') or '').upper()
    healthy = message in _HEALTHY
    if not healthy and message not in _UNHEALTHY:
        logger.info('linkedin.account_status', message=message, handled=False)
        return {'handled': False, 'event': 'account_status', 'healthy': None}

    handled = await asyncio.to_thread(webhook_events.account_status, account_id, healthy, message)
    logger.info('linkedin.account_status', healthy=healthy, message=message, handled=handled)
    return {'handled': handled, 'event': 'account_status', 'healthy': healthy}
