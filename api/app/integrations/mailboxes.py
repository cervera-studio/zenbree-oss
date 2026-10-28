"""Connect a sending mailbox (Gmail / Microsoft): an OAuth grant that sends and reads the bounces (#747).

Adapted from the earlier implementation ``integrations/use_cases/{start,complete}_connection.py`` and
``integrations/api.py``. The refresh token is Fernet-encrypted before it is
stored and is never logged; disconnecting revokes it (Google) and drops it.

Flow: ``connect`` (JWT) → consent screen → provider redirects the browser to our
``callback``, which only bounces ``code``/``state`` to the web app → the web calls
``complete`` with the user's JWT. The exchange happens only for the user who
started it: the signed ``state`` must name the caller (otherwise anyone could
send a victim a consent link and attach the victim's mailbox to their own
account), is single-use, and carries the PKCE verifier and the project the mailbox joins (#307).

A mailbox is one row of the organization used by projects (``app.integrations.project_senders``): any member sends
from a project's mailbox; only whoever connected it, or an admin, replaces or disconnects it.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import redis
import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, SystemReason, current_user, public
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import Result, err, is_err, ok
from app.core.errors_http import unwrap_or_raise
from app.integrations import mailbox_watch, project_senders, token_crypto
from app.integrations.member_release import revoke_unless_shared
from app.integrations.oauth_client import OAuthClient, new_pkce_verifier
from app.integrations.project_senders import in_org, may_disconnect
from app.integrations.providers import MAILBOX_PROVIDERS
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/mailboxes')
logger = structlog.get_logger()

Provider = Literal['google', 'microsoft']


class MailboxSetup(BaseModel):
    """Whether this server can connect a mailbox of the provider."""

    provider: Provider
    configured: bool


class Mailbox(BaseModel):
    id: UUID
    provider: Provider
    connected: bool
    status: Literal['connected', 'error']
    account_label: str | None
    can_disconnect: bool
    configured: bool
    # #747: false for a mailbox connected before the read scope, or with it unticked: a reconnect reads its bounces
    can_read_bounces: bool


class Mailboxes(BaseModel):
    mailboxes: list[Mailbox]
    providers: list[MailboxSetup]


class AuthorizationUrl(BaseModel):
    authorization_url: str


class MailboxConnected(BaseModel):
    provider: Provider
    account_label: str | None


class Disconnected(BaseModel):
    connected: bool


def _redirect_uri(provider: str) -> str:
    """Must match the provider console registration byte for byte."""
    return f'{settings.api_public_base_url.rstrip("/")}/v1/integrations/mailboxes/{provider}/callback'


def list_mailboxes(project_id: str) -> list[dict]:
    with get_conn() as conn:
        return [
            {k: r[k] for k in ('id', 'provider', 'status', 'account_label', 'user_id', 'granted_scopes')}
            for r in project_senders.linked(conn, project_id)
            if r['status'] != 'disconnected'
        ]


def _store(
    user_id: str,
    project_id: str,
    provider: str,
    *,
    encrypted: str,
    scopes: list[str],
    label: str | None,
    subject: str | None,
) -> Result[None]:
    """``subject``: the provider's stable account id (Google ``sub``, Microsoft ``id``), never the label."""
    return project_senders.attach(
        user_id,
        project_id,
        provider,
        account=subject or '',
        cols={
            'refresh_token_encrypted': encrypted,
            'granted_scopes': scopes,
            'account_label': label,
            'provider_account_id': subject,
        },
    )


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def _spend_state(state: str) -> bool:
    """Single use: the first completion wins. Fails closed without Redis."""
    key = f'oauth:state:{hashlib.sha256(state.encode()).hexdigest()[:40]}'
    return bool(_redis().set(key, 1, nx=True, ex=token_crypto.STATE_TTL_SECONDS))


async def complete(provider_name: str, *, user_id: str, org_id: str, code: str, state: str) -> Result[str | None]:
    """Code → stored grant, for the authenticated caller only, in the project the connection was started in.
    Returns the account label."""
    provider = MAILBOX_PROVIDERS[provider_name]
    if not code:
        return err('INTEGRATIONS.INVALID_CALLBACK', detail='authorization code missing')
    verified = token_crypto.verify_state(state, provider=provider.name)
    if is_err(verified):
        return verified
    if verified.value != user_id:
        logger.warning('integrations.mailbox.state_user_mismatch', user_id=user_id, provider=provider.name)
        return err('INTEGRATIONS.INVALID_STATE', detail='this connection was started by another account')
    project_id = token_crypto.state_project(state)
    if not await asyncio.to_thread(in_org, project_id, org_id):
        return err('INTEGRATIONS.INVALID_STATE', detail='this connection was started in another team')
    try:
        fresh = await asyncio.to_thread(_spend_state, state)
    except redis.RedisError:
        return err('INTEGRATIONS.UNAVAILABLE', detail='try again later')
    if not fresh:
        return err('INTEGRATIONS.INVALID_STATE', detail='this connection link was already used')
    client = OAuthClient(provider)
    grant = await client.exchange_code(
        code=code, redirect_uri=_redirect_uri(provider.name), code_verifier=token_crypto.pkce_verifier(state)
    )
    if is_err(grant):
        return grant
    granted = list(grant.value.granted_scopes)
    # Refuse a grant whose send permission was unticked (or not reported): fail at connect, not mid-campaign.
    if not provider.grants_send(granted):
        return err('INTEGRATIONS.SCOPE_DECLINED', detail='the send permission was not granted')
    encrypted = token_crypto.encrypt(grant.value.refresh_token or '')
    if is_err(encrypted):
        return encrypted
    label, subject = await client.fetch_account(grant.value.access_token)
    stored = await asyncio.to_thread(
        _store,
        user_id,
        project_id,
        provider.name,
        encrypted=encrypted.value,
        scopes=granted,
        label=label,
        subject=subject,
    )
    if is_err(stored):  # nothing kept: the fresh grant is dropped with this request
        return stored
    logger.info('integrations.mailbox.connected', user_id=user_id, provider=provider.name)
    return ok(label)


@router.get('', response_model=Mailboxes)
async def mailboxes(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> dict:
    """The active project's live mailboxes, one entry per account. ``can_disconnect``: the caller connected it or is
    an admin."""
    configured = {
        name: OAuthClient(provider).is_configured
        and bool(settings.api_public_base_url)
        and token_crypto.is_configured()
        for name, provider in MAILBOX_PROVIDERS.items()
    }
    out = [
        {
            'id': row['id'],
            'provider': row['provider'],
            'connected': row['status'] == 'connected',
            'status': row['status'],
            'account_label': row['account_label'],
            'can_disconnect': may_disconnect(row, user),
            'can_read_bounces': MAILBOX_PROVIDERS[row['provider']].grants_read(row['granted_scopes'] or []),
            'configured': configured[row['provider']],
        }
        for row in await asyncio.to_thread(list_mailboxes, project_id)
    ]
    return {'mailboxes': out, 'providers': [{'provider': n, 'configured': c} for n, c in configured.items()]}


@router.post('/{provider}/connect', response_model=AuthorizationUrl)
async def connect(
    provider: Provider, user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)
) -> dict:
    if not settings.api_public_base_url:
        unwrap_or_raise(err('INTEGRATIONS.NOT_CONFIGURED', detail='public API URL is not configured'))
    verifier = new_pkce_verifier()
    state = unwrap_or_raise(
        token_crypto.issue_state(user_id=user.id, provider=provider, pkce_verifier=verifier, project_id=project_id)
    )
    url = OAuthClient(MAILBOX_PROVIDERS[provider]).authorize_url(
        state=state, redirect_uri=_redirect_uri(provider), code_verifier=verifier
    )
    return {'authorization_url': unwrap_or_raise(url)}


@router.get('/{provider}/callback', include_in_schema=False, dependencies=[public(SystemReason.OAUTH_CALLBACK)])
async def callback(
    provider: Provider,
    code: str = Query(default='', max_length=4_000),
    state: str = Query(default='', max_length=4_000),
    error: str = Query(default='', max_length=200),
) -> RedirectResponse:
    """Provider → browser → here, without a JWT. Exchanges nothing: hands code/state to the web app,
    which completes with the user's session (``complete``)."""
    query = {'error': error} if error else {'code': code, 'state': state}
    web = settings.web_origin.rstrip('/')
    return RedirectResponse(f'{web}/settings/integrations/callback/{provider}?{urlencode(query)}', status_code=302)


class MailboxCompleteIn(BaseModel):
    code: str = Field(min_length=1, max_length=4_000)
    state: str = Field(min_length=1, max_length=4_000)


@router.post('/{provider}/complete', response_model=MailboxConnected)
async def complete_route(
    provider: Provider, body: MailboxCompleteIn, user: CurrentUser = Depends(current_user)
) -> dict:
    done = await complete(provider, user_id=user.id, org_id=user.org_id, code=body.code, state=body.state)
    if is_err(done):
        logger.warning('integrations.mailbox.connect_failed', provider=provider, code=done.error.code)
    return {'provider': provider, 'account_label': unwrap_or_raise(done)}


@router.delete('/{sender_id}', response_model=Disconnected)
async def disconnect(
    sender_id: UUID, user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)
) -> dict:
    """Revokes the grant at Google (best-effort), then drops the stored token either way. Only whoever
    connected the mailbox, or an admin, may (403 ``INTEGRATIONS.DISCONNECT_FORBIDDEN``). It stops the mailbox for
    every project it serves.

    ponytail: Microsoft has no per-app refresh-token revoke (``revokeSignInSessions`` signs the user out
    everywhere); the UI tells them to remove the app from their Microsoft account.
    """

    def _row() -> dict | None:
        with get_conn() as conn:
            return project_senders.by_id(conn, project_id, sender_id)

    def _mark(row: dict) -> None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE user_oauth_connections SET status = 'disconnected', status_detail = NULL,"
                " refresh_token_encrypted = '', granted_scopes = '{}', inbound_subscription_id = NULL,"
                ' inbound_subscription_expires_at = NULL, inbound_history_id = NULL WHERE id = %s',
                (row['id'],),
            )

    row = await asyncio.to_thread(_row)
    if not may_disconnect(row, user):
        unwrap_or_raise(
            err('INTEGRATIONS.DISCONNECT_FORBIDDEN', detail='only whoever connected it or an admin can disconnect it')
        )
    if row:
        await mailbox_watch.unwatch(row)  # #747: before the revoke, while the grant still works
        await revoke_unless_shared(row)
        await asyncio.to_thread(_mark, row)
    return {'connected': False}
