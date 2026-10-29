"""Connect a HubSpot portal with a read-only OAuth grant (E13.1 #120), one per user.

Same flow and protections as :mod:`app.integrations.mailboxes`: ``connect`` (JWT) → HubSpot consent →
``callback`` bounces ``code``/``state`` to the web app → the web calls ``complete`` with the user's JWT.
The signed ``state`` must name the caller and is single-use. HubSpot documents no PKCE, so the state
carries no verifier. The refresh token is Fernet-encrypted before it is stored and never logged; there is
no global HubSpot token, so one user's leads never reach another user's portal.
"""

from __future__ import annotations

import asyncio
from typing import Literal
from urllib.parse import urlencode

import httpx
import redis
import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, SystemReason, current_user, public
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import Result, err, is_err, ok
from app.core.errors_http import api_error, unwrap_or_raise
from app.crm import gate, hubspot_client, write_back
from app.integrations import hubspot_timeline, mailboxes, token_crypto
from app.integrations.mailboxes import AuthorizationUrl, Disconnected
from app.integrations.oauth_client import OAuthClient
from app.integrations.providers import HUBSPOT, HUBSPOT_READ_SCOPES
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/hubspot')
router.include_router(hubspot_timeline.router)
logger = structlog.get_logger()

INTROSPECT_URL = 'https://api.hubspot.com/oauth/v3/token/introspect'
_TIMEOUT_SECONDS = 15.0


def _redirect_uri() -> str:
    """Must match the HubSpot app's redirect URL byte for byte."""
    return f'{settings.api_public_base_url.rstrip("/")}/v1/integrations/hubspot/callback'


def _configured() -> bool:
    return OAuthClient(HUBSPOT).is_configured and bool(settings.api_public_base_url) and token_crypto.is_configured()


def connection(user_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            'SELECT status, account_label, provider_account_id FROM user_oauth_connections'
            " WHERE user_id = %s AND provider = 'hubspot'",
            (user_id,),
        ).fetchone()


def _store(
    user_id: str,
    org_id: str,
    *,
    encrypted: str,
    scopes: list[str],
    label: str | None,
    portal_id: str | None,
    owner_id: str | None = None,
) -> None:
    with get_conn() as conn:
        # Locked until commit, so a write-back sweep recording for the old portal waits and then finds it changed.
        old = conn.execute(
            "SELECT provider_account_id FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot'"
            ' FOR UPDATE',
            (user_id,),
        ).fetchone()
        conn.execute(
            """INSERT INTO user_oauth_connections
                   (user_id, organization_id, provider, refresh_token_encrypted, granted_scopes, account_label,
                    provider_account_id, provider_owner_id, status, status_detail, connected_at)
               VALUES (%s, %s, 'hubspot', %s, %s, %s, %s, %s, 'connected', NULL, now())
               ON CONFLICT (user_id) WHERE provider = 'hubspot' DO UPDATE SET
                   organization_id = EXCLUDED.organization_id,
                   refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                   granted_scopes = EXCLUDED.granted_scopes,
                   account_label = EXCLUDED.account_label,
                   provider_account_id = EXCLUDED.provider_account_id,
                   provider_owner_id = EXCLUDED.provider_owner_id,
                   status = 'connected', status_detail = NULL, connected_at = now(),
                   crm_write_back_since = CASE
                       WHEN user_oauth_connections.provider_account_id IS DISTINCT FROM EXCLUDED.provider_account_id
                       THEN CASE WHEN user_oauth_connections.crm_write_back_since IS NOT NULL THEN now() END
                       ELSE user_oauth_connections.crm_write_back_since END""",
            (user_id, org_id, encrypted, scopes, label, portal_id, owner_id),
        )
        if old and old['provider_account_id'] != portal_id:
            # Contact and company ids belong to one portal: another portal's history would mark people as written,
            # and its write-back switch restarts from now, so the new portal is not backfilled with earlier campaigns.
            conn.execute('DELETE FROM crm_contact_writes WHERE owner_id = %s', (user_id,))
        # A (re)connection re-evaluates everyone: verdicts of another portal, or blocks whose lifting
        # event was missed while disconnected, must not outlive it.
        conn.execute('DELETE FROM crm_gate_checks WHERE owner_id = %s', (user_id,))


async def _portal(access_token: str) -> tuple[str | None, str | None, str | None]:
    """``(hub_id, hub_domain, granting user's email)`` of the granted portal; Nones when unreadable."""
    form = {
        'client_id': settings.hubspot_oauth_client_id,
        'client_secret': settings.hubspot_oauth_client_secret,
        'token_type_hint': 'access_token',
        'token': access_token,  # RFC 7662 name
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(INTROSPECT_URL, data=form)
        payload = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.info('integrations.hubspot.introspect_unavailable', error=repr(exc)[:200])
        return None, None, None
    if not isinstance(payload, dict) or not payload.get('hub_id'):
        # Field names only: the values name the portal and the user.
        fields = sorted(payload) if isinstance(payload, dict) else None
        logger.warning('integrations.hubspot.introspect_no_portal', status=resp.status_code, fields=fields)
        return None, None, None
    hub_id, domain, user = payload.get('hub_id'), payload.get('hub_domain'), payload.get('user')
    return (
        str(hub_id)[:500],
        str(domain)[:320] if domain else None,
        user.strip() if isinstance(user, str) and user.strip() else None,
    )


async def _owner_id(access_token: str, email: str | None) -> str | None:
    """The connected user's own HubSpot owner id, so the gate blocks only accounts owned by someone
    else. Best-effort: unknown reads as "owns nothing", which blocks every owned account."""
    if not email:
        return None
    found = await hubspot_client.find_owner(access_token, email)
    return None if is_err(found) else found.value


async def complete(*, user_id: str, org_id: str, code: str, state: str) -> Result[str | None]:
    """Code → stored grant, for the authenticated caller only. Returns the portal's domain."""
    verified = token_crypto.verify_state(state, provider=HUBSPOT.name)
    if is_err(verified):
        return verified
    if verified.value != user_id:
        logger.warning('integrations.hubspot.state_user_mismatch', user_id=user_id)
        return err('INTEGRATIONS.INVALID_STATE', detail='this connection was started by another account')
    try:
        fresh = await asyncio.to_thread(mailboxes._spend_state, state)
    except redis.RedisError:
        return err('INTEGRATIONS.UNAVAILABLE', detail='try again later')
    if not fresh:
        return err('INTEGRATIONS.INVALID_STATE', detail='this connection link was already used')
    grant = await OAuthClient(HUBSPOT).exchange_code(code=code, redirect_uri=_redirect_uri(), code_verifier='')
    if is_err(grant):
        return grant
    granted = list(grant.value.granted_scopes)
    # The gate reads all of these; a grant missing one would block or pass people on partial data.
    if not set(HUBSPOT_READ_SCOPES) <= set(granted):
        return err('INTEGRATIONS.SCOPE_DECLINED', detail='the HubSpot read permissions were not granted')
    encrypted = token_crypto.encrypt(grant.value.refresh_token or '')
    if is_err(encrypted):
        return encrypted
    portal_id, domain, user_email = await _portal(grant.value.access_token)
    if portal_id is None:
        # Webhooks name the portal: without its id no event would ever lift a block for this user.
        return err('INTEGRATIONS.HTTP_ERROR', detail='could not identify the HubSpot portal')
    owner_id = await _owner_id(grant.value.access_token, user_email)
    await asyncio.to_thread(
        _store,
        user_id,
        org_id,
        encrypted=encrypted.value,
        scopes=granted,
        label=domain,
        portal_id=portal_id,
        owner_id=owner_id,
    )
    gate.resume(user_id)  # the reconnection is the event that re-evaluates this user's people
    logger.info('integrations.hubspot.connected', user_id=user_id, portal_id=portal_id)
    return ok(domain)


class HubSpotStatus(BaseModel):
    connected: bool
    status: Literal['connected', 'error', 'disconnected']
    account_label: str | None
    configured: bool
    can_write: bool  # the grant may write contacts (#526); false until a reconnection gives the permission
    can_write_companies: bool  # ... and companies: an optional permission the user ticks separately at HubSpot
    write_back: bool  # can_write and the user turned the switch on
    timeline: hubspot_timeline.TimelineStatus


class HubSpotConnected(BaseModel):
    provider: Literal['hubspot']
    account_label: str | None


@router.get('', response_model=HubSpotStatus)
async def status(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> dict:
    row = await asyncio.to_thread(connection, user.id)
    state = row['status'] if row else 'disconnected'
    writes = await asyncio.to_thread(write_back.state, user.id) if state == 'connected' else write_back.OFF
    return {
        'connected': state == 'connected',
        'status': state,
        'account_label': row['account_label'] if row and state != 'disconnected' else None,
        'configured': _configured(),
        **writes,
        # Also for a grant in error or after a disconnect: the rows wait on exactly that, and the card says so.
        'timeline': await hubspot_timeline.read(user.id, project_id) if row else hubspot_timeline.EMPTY,
    }


class WriteBackIn(BaseModel):
    enabled: bool


_WRITE_BACK_REFUSALS = {
    'not_connected': ('INTEGRATIONS.HUBSPOT_NOT_CONNECTED', 'connect HubSpot first'),
    'no_write_scope': (
        'INTEGRATIONS.HUBSPOT_WRITE_NOT_GRANTED',
        'reconnect HubSpot and tick the contacts write permission',
    ),
}


@router.put('/write-back', response_model=HubSpotStatus)
async def set_write_back(
    body: WriteBackIn, user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)
) -> dict:
    """The user's switch for writing in their HubSpot (#526). Nothing writes yet: the parts of #121 read it."""
    outcome = await asyncio.to_thread(write_back.set_enabled, user.id, body.enabled)
    if outcome != 'ok':
        code, message = _WRITE_BACK_REFUSALS[outcome]
        raise api_error(409, code, message)
    return await status(user, project_id)


@router.post('/connect', response_model=AuthorizationUrl)
async def connect(user: CurrentUser = Depends(current_user)) -> dict:
    if not settings.api_public_base_url:
        unwrap_or_raise(err('INTEGRATIONS.NOT_CONFIGURED', detail='public API URL is not configured'))
    state = unwrap_or_raise(token_crypto.issue_state(user_id=user.id, provider=HUBSPOT.name))
    url = OAuthClient(HUBSPOT).authorize_url(state=state, redirect_uri=_redirect_uri(), code_verifier='')
    return {'authorization_url': unwrap_or_raise(url)}


@router.get('/callback', include_in_schema=False, dependencies=[public(SystemReason.OAUTH_CALLBACK)])
async def callback(
    code: str = Query(default='', max_length=4_000),
    state: str = Query(default='', max_length=4_000),
    error: str = Query(default='', max_length=200),
) -> RedirectResponse:
    """HubSpot → browser → here, without a JWT: hands code/state to the web app, which completes it."""
    query = {'error': error} if error else {'code': code, 'state': state}
    web = settings.web_origin.rstrip('/')
    return RedirectResponse(f'{web}/settings/integrations/callback/hubspot?{urlencode(query)}', status_code=302)


class CompleteIn(BaseModel):
    code: str = Field(min_length=1, max_length=4_000)
    state: str = Field(min_length=1, max_length=4_000)


@router.post('/complete', response_model=HubSpotConnected)
async def complete_route(body: CompleteIn, user: CurrentUser = Depends(current_user)) -> dict:
    done = await complete(user_id=user.id, org_id=user.org_id, code=body.code, state=body.state)
    if is_err(done):
        logger.warning('integrations.hubspot.connect_failed', code=done.error.code)
    return {'provider': HUBSPOT.name, 'account_label': unwrap_or_raise(done)}


@router.delete('', response_model=Disconnected)
async def disconnect(user: CurrentUser = Depends(current_user)) -> dict:
    """Revokes the refresh token at HubSpot (best-effort), then drops the stored token either way."""

    def _token() -> str:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT refresh_token_encrypted FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot'",
                (user.id,),
            ).fetchone()
        return row['refresh_token_encrypted'] if row else ''

    def _mark() -> None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE user_oauth_connections SET status = 'disconnected', status_detail = NULL,"
                " refresh_token_encrypted = '', granted_scopes = '{}', crm_write_back = false,"
                ' crm_write_back_since = NULL'
                " WHERE user_id = %s AND provider = 'hubspot'",
                (user.id,),
            )

    if ciphertext := await asyncio.to_thread(_token):
        plaintext = token_crypto.decrypt(ciphertext)
        if not is_err(plaintext):
            await OAuthClient(HUBSPOT).revoke(plaintext.value)
    await asyncio.to_thread(_mark)
    return {'connected': False}
