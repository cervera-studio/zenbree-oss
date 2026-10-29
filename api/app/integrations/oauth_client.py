"""OAuth 2.0 authorization-code client for every OAuth provider (mailboxes, HubSpot). Port of the earlier implementation
``integrations/adapters/oauth_client.py``: plain httpx, no vendor SDK.

Error codes:
- INTEGRATIONS.NOT_CONFIGURED   client id/secret missing (no call made)
- INTEGRATIONS.AUTH             400/401 for a dead grant (revoked or bad code, a new sign-in needed) or an app the
                                user's tenant removed or never consented to: reconnect
- INTEGRATIONS.CLIENT_REJECTED  400/401 ``invalid_client`` (our wrong or expired secret) or Google's ``deleted_client`` (our
                                client deleted): the operator fixes it
- INTEGRATIONS.UNAVAILABLE      400 ``temporarily_unavailable`` (a Microsoft outage)
- INTEGRATIONS.RATE_LIMITED     429
- INTEGRATIONS.TRANSPORT        network failure / timeout
- INTEGRATIONS.HTTP_ERROR       any other non-2xx, and a 400/401 whose body names none of the above
- INTEGRATIONS.INVALID_JSON     2xx body we cannot read
- INTEGRATIONS.NO_REFRESH_TOKEN 2xx code exchange without a refresh token
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import settings
from app.core.errors import Err, Result, err, ok
from app.integrations.providers import OAuthProvider

logger = structlog.get_logger()
_TIMEOUT_SECONDS = 15.0

#: A 400/401 body's HubSpot ``status`` or RFC 6749 §5.2 ``error`` (looked up in that order; the two do not
#: collide) → our code. Only AUTH marks a mailbox for Reconnect, so a value missing here never does.
_TOKEN_ERRORS: Final[dict[str, str]] = {
    'invalid_grant': 'INTEGRATIONS.AUTH',
    'interaction_required': 'INTEGRATIONS.AUTH',  # Microsoft MFA or conditional access: a new sign-in fixes it
    'BAD_REFRESH_TOKEN': 'INTEGRATIONS.AUTH',  # HubSpot: its `error` is invalid_request for a revoked token
    'BAD_AUTH_CODE': 'INTEGRATIONS.AUTH',
    # Microsoft: the user's tenant removed or blocked our app, or its consent is gone; a new consent fixes it
    'unauthorized_client': 'INTEGRATIONS.AUTH',
    'consent_required': 'INTEGRATIONS.AUTH',
    'invalid_client': 'INTEGRATIONS.CLIENT_REJECTED',
    'deleted_client': 'INTEGRATIONS.CLIENT_REJECTED',  # Google deletes clients unused for 6 months
    'temporarily_unavailable': 'INTEGRATIONS.UNAVAILABLE',
}


@dataclass(frozen=True, slots=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None
    granted_scopes: tuple[str, ...]


def new_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)  # 86 chars, inside RFC 7636's 43-128


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip('=')


def _credentials(provider: str) -> tuple[str, str]:
    if provider == 'google':
        return settings.google_oauth_client_id, settings.google_oauth_client_secret
    if provider == 'microsoft':
        return settings.microsoft_oauth_client_id, settings.microsoft_oauth_client_secret
    if provider == 'hubspot':
        return settings.hubspot_oauth_client_id, settings.hubspot_oauth_client_secret
    return '', ''


class OAuthClient:
    def __init__(self, provider: OAuthProvider) -> None:
        self._provider = provider
        self._client_id, self._client_secret = _credentials(provider.name)

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _not_configured(self) -> Result:
        return err('INTEGRATIONS.NOT_CONFIGURED', detail=f'{self._provider.name} OAuth client is not set')

    def authorize_url(self, *, state: str, redirect_uri: str, code_verifier: str) -> Result[str]:
        if not self.is_configured:
            return self._not_configured()
        params = {
            'client_id': self._client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(self._provider.scopes),
            'state': state,
            **(
                {'code_challenge': pkce_challenge(code_verifier), 'code_challenge_method': 'S256'}
                if self._provider.pkce
                else {}
            ),
            **dict(self._provider.extra_authorize_params),
        }
        return ok(f'{self._provider.authorize_url}?{urlencode(params)}')

    async def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> Result[TokenGrant]:
        form = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            **({'code_verifier': code_verifier} if self._provider.pkce else {}),
        }
        return await self._token_request(form, require_refresh_token=True)

    async def refresh(self, refresh_token: str) -> Result[TokenGrant]:
        """Microsoft rotates the refresh token, Google does not: persist a new one when returned."""
        form = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
        return await self._token_request(form, require_refresh_token=False)

    async def _token_request(self, form: dict[str, str], *, require_refresh_token: bool) -> Result[TokenGrant]:
        if not self.is_configured:
            return self._not_configured()
        data = {**form, 'client_id': self._client_id, 'client_secret': self._client_secret}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(self._provider.token_url, data=data)
        except httpx.HTTPError as exc:
            logger.warning('integrations.token.transport_error', provider=self._provider.name, error=repr(exc)[:200])
            return err('INTEGRATIONS.TRANSPORT', detail='OAuth token endpoint unreachable')
        return _decode_grant(resp, provider=self._provider.name, require_refresh_token=require_refresh_token)

    async def revoke(self, refresh_token: str) -> bool:
        """Best-effort: a failed revoke must not keep the user connected. ``False`` when not revoked."""
        if not self._provider.revoke_url:
            return False
        form = {'token': refresh_token}
        if self._provider.revoke_with_client_auth:
            form |= {'client_id': self._client_id, 'client_secret': self._client_secret}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(self._provider.revoke_url, data=form)
        except httpx.HTTPError as exc:
            logger.warning('integrations.revoke.transport_error', provider=self._provider.name, error=repr(exc)[:200])
            return False
        if resp.status_code != 200:  # 400 = already revoked or expired
            logger.info('integrations.revoke.refused', provider=self._provider.name, status=resp.status_code)
        return resp.status_code == 200

    async def fetch_account(self, access_token: str) -> tuple[str | None, str | None]:
        """(address for the settings card, the provider's stable account id). Best-effort: never fails a
        connection; a missing id only means the mailbox cannot be re-authorised in place by a colleague."""
        if not self._provider.profile_url:
            return None, None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.get(self._provider.profile_url, headers={'Authorization': f'Bearer {access_token}'})
            payload = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.info('integrations.profile.unavailable', provider=self._provider.name, error=repr(exc)[:200])
            return None, None
        if not isinstance(payload, dict):
            return None, None
        label = next(
            (
                v.strip()[:320]
                for k in self._provider.profile_email_keys
                if isinstance(v := payload.get(k), str) and v.strip()
            ),
            None,
        )
        subject = payload.get(self._provider.profile_subject_key)
        return label, str(subject)[:500] if isinstance(subject, str | int) and str(subject).strip() else None


def _decode_grant(resp: httpx.Response, *, provider: str, require_refresh_token: bool) -> Result[TokenGrant]:
    if resp.status_code in (400, 401):
        return _rejected(resp, provider=provider)
    if resp.status_code == 429:
        return err('INTEGRATIONS.RATE_LIMITED', detail='OAuth token endpoint rate limited')
    if resp.status_code >= 300:
        logger.warning('integrations.token.http_error', provider=provider, status=resp.status_code)
        return err('INTEGRATIONS.HTTP_ERROR', detail=f'OAuth token endpoint returned {resp.status_code}')
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not payload.get('access_token'):
        return err('INTEGRATIONS.INVALID_JSON', detail='token response has no access_token')
    refresh_token = payload.get('refresh_token') or None
    if require_refresh_token and not refresh_token:
        # Without it the connection dies within the hour: refuse to store it.
        return err('INTEGRATIONS.NO_REFRESH_TOKEN', detail='provider returned no refresh token; re-consent')
    return ok(
        TokenGrant(
            access_token=str(payload['access_token']),
            refresh_token=str(refresh_token) if refresh_token else None,
            granted_scopes=_granted_scopes(payload),
        )
    )


def _rejected(resp: httpx.Response, *, provider: str) -> Err:
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    body = payload if isinstance(payload, dict) else {}
    reason = next((v for k in ('status', 'error') if isinstance(v := body.get(k), str) and v in _TOKEN_ERRORS), None)
    code = _TOKEN_ERRORS[reason] if reason else 'INTEGRATIONS.HTTP_ERROR'
    # Never the request form: it holds the client secret and the refresh token.
    fields = {
        'provider': provider,
        'status': resp.status_code,
        'error': reason or str(body.get('status') or body.get('error') or '')[:100],
        'description': str(body.get('error_description') or '')[:200],
    }
    if code == 'INTEGRATIONS.AUTH':
        logger.info('integrations.token.rejected', **fields)
    elif code == 'INTEGRATIONS.CLIENT_REJECTED':
        logger.error('integrations.token.client_rejected', **fields)  # every connection fails until fixed
    else:
        logger.warning('integrations.token.http_error', **fields)
    return err(code, detail=f'OAuth token endpoint refused the request ({reason or resp.status_code})')


def _granted_scopes(payload: dict) -> tuple[str, ...]:
    """RFC 6749 ``scope`` (space-separated), or HubSpot's ``scopes`` list."""
    scopes = payload.get('scopes')
    if isinstance(scopes, list):
        return tuple(str(s) for s in scopes)
    return tuple(str(payload.get('scope') or '').split())
