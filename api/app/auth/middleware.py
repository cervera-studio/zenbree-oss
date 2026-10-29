"""
JWT authentication — verifies Better Auth JWKS tokens (jwt plugin) and, for
routes that opt in, personal API keys (#1074). Adapted from API.
"""

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import jwt
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWKSetError

from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.scope import ActorGone, enter_member, enter_system_for_request, system
from app.core.system_reasons import SystemReason

logger = structlog.get_logger()
# auto_error=False so a header-less caller gets our 401 body, not Fastthe API's 403.
security = HTTPBearer(auto_error=False)

# Public keys from the web app's Better Auth JWKS endpoint, cached.
_jwks_client = PyJWKClient(settings.auth_jwks_url, cache_keys=True)

# Better Auth signs with EdDSA (Ed25519) by default; ES256 if configured.
_ALGORITHMS = ['EdDSA', 'ES256']

# Broken JWKS: unreachable, malformed body, or unusable key set.
_JWKS_FAULT = (PyJWKClientError, PyJWKSetError)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who the bearer says they are, before the database agrees: the token's claims, or an API key's creator and
    organization. Tests override ``authenticate`` with one, so the membership read of ``current_user`` still runs."""

    user_id: str
    org_id: str
    role: str | None = None  # Better Auth's global user.role claim (back office)
    email: str | None = None


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: str
    org_id: str  # the active organization (#305): every shared row is scoped by it
    role: str | None = None  # Better Auth's global user.role (back office), not the role in the organization
    email: str | None = None
    org_role: str | None = None  # 'admin' | 'member' in org_id (#308), from zb.enter_member on every request


def _unauthorized(code: str, message: str) -> HTTPException:
    return api_error(401, code, message, headers={'WWW-Authenticate': 'Bearer'})


def not_a_member() -> HTTPException:
    return _unauthorized('AUTH.NOT_A_MEMBER', 'Not a member of this organization')


async def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> Principal:
    """Verify the bearer JWT.

    ponytail: the earlier implementation's failed_auth_guard (per-IP failed-auth bucket, which also
    stops bogus-``kid`` tokens from forcing a JWKS refetch each) is not ported.
    PyJWKClient refetches on a kid miss; add the guard before public launch.
    """
    if credentials is None:
        raise _unauthorized('AUTH.TOKEN_MISSING', 'Not authenticated')

    token = credentials.credentials
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            audience=settings.auth_audience,
            issuer=settings.auth_issuer,
        )
    except _JWKS_FAULT as e:
        # Unknown kid lands here too; either way the token can't be verified.
        logger.warning('auth.verify.jwks_unavailable', reason=str(e))
        raise _unauthorized('AUTH.TOKEN_INVALID', 'Invalid or expired token') from e
    except Exception as e:
        logger.warning('auth.verify.failed', reason=str(e))
        raise _unauthorized('AUTH.TOKEN_INVALID', 'Invalid or expired token') from e

    user_id: str | None = payload.get('sub')
    # Falsy, not None: an empty sub is no identity.
    if not user_id:
        raise _unauthorized('AUTH.TOKEN_INVALID', 'Missing user ID in token')
    # A token minted before #305 has no org claim: the user's own organization, as the web picks it.
    org_id = payload.get('org') or await asyncio.to_thread(_default_org, user_id)
    if not org_id:
        raise _unauthorized('AUTH.TOKEN_INVALID', 'No organization for this user')
    return Principal(user_id=user_id, org_id=org_id, role=payload.get('role'), email=payload.get('email') or None)


def _default_org(user_id: str) -> str | None:
    # default_organization() creates the organization of a user who has none, so it runs before any scope exists.
    with system(SystemReason.NO_ORG_CLAIM), get_conn() as conn:
        row = conn.execute('SELECT default_organization(%s) AS id', (user_id,)).fetchone()
    return str(row['id']) if row and row['id'] else None


async def current_user(request: Request, principal: Principal = Depends(authenticate)) -> CurrentUser:
    """The caller as a live member of the token's organization: the request runs as ``Member(org, user)`` from
    here on, and every checkout re-reads the membership. Async on purpose: a sync dependency's scope never reaches
    the endpoint."""
    return await _enter(request, principal)


async def _enter(request: Request, principal: Principal) -> CurrentUser:
    # The claim is only as fresh as the token (15 minutes): a member removed since then is refused now (#308).
    try:
        org_role = await enter_member(principal.org_id, principal.user_id)
    except ActorGone:
        raise not_a_member() from None
    request.state.user_id = principal.user_id
    structlog.contextvars.bind_contextvars(user_id=principal.user_id)
    return CurrentUser(
        id=principal.user_id, org_id=principal.org_id, role=principal.role, email=principal.email, org_role=org_role
    )


# Better Auth's banUser sets these and deletes the sessions, but leaves the JWTs already minted and the API keys (#360).
# Checked when a creator's work runs, never revoked at ban time: an unban, or a ban that expires, resumes it. A
# member's own calls go through zb.is_live_member, the same rule in SQL.
_NOT_BANNED = '(u.banned IS NOT TRUE OR (u."banExpires" IS NOT NULL AND u."banExpires" < now()))'


def creator_not_banned(column: str) -> str:
    """SQL: the user in ``column``, if any, has no active ban (#389). The dispatcher, the scheduler and the webhook
    sender skip what a banned user created and pick it up again on unban or when the ban expires: nothing is
    written at ban time, so a pause the user set stays, and a ban written by any path is honoured."""
    return f'NOT EXISTS (SELECT 1 FROM "user" u WHERE u.id = {column} AND NOT {_NOT_BANNED})'


def _user_role(user_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute('SELECT role FROM "user" WHERE id = %s', (user_id,)).fetchone()
    return row['role'] if row else None


def _has_admin(role: str | None) -> bool:
    # The admin plugin stores multiple roles comma-separated ("admin,user").
    return 'admin' in {r.strip() for r in (role or '').split(',')}


async def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Admin = Better Auth ``user.role``, in the JWT claim and still in the database: setRole revokes no token, so a
    demotion applies on the next call, not when the token expires (#431). A ban is refused by ``current_user``. The
    back office reads every organization, so the rest of the request runs as ``System(BACK_OFFICE)``."""
    if not (_has_admin(user.role) and _has_admin(await asyncio.to_thread(_user_role, user.id))):
        raise api_error(403, 'AUTH.FORBIDDEN', 'Admin only')
    enter_system_for_request(SystemReason.BACK_OFFICE)
    return user


def _require_org_admin(user: CurrentUser) -> CurrentUser:
    if user.org_role != 'admin':
        raise api_error(403, 'AUTH.ORG_ADMIN_ONLY', 'Only an admin of the organization can do this')
    return user


async def require_org_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """An admin of the active organization (#308): billing, invites, API keys, webhooks. Not ``require_admin``,
    which is the back office."""
    return _require_org_admin(user)


# ── Personal API keys (#1074) ─────────────────────────────────────────────

# Plaintext keys are `sbk_<random>`. The prefix is what lets one bearer slot
# carry either credential: anything else is handed to `current_user` untouched.
API_KEY_PREFIX = 'sbk_'


def hash_api_key(key: str) -> str:
    """Hex SHA-256 of a plaintext key — the only form we ever persist.

    Unsalted on purpose: the key is full-entropy random, so there is no
    dictionary to precompute against.
    """
    return hashlib.sha256(key.encode()).hexdigest()


def _lookup_api_key(key_hash: str) -> Principal | None:
    """Creator and organization of the live key with this digest, or None. Sync — call in a thread.

    A revoked key is filtered out in SQL, so it is indistinguishable from a
    key that was never issued. So is the key of a creator who left the
    organization (#308), or whose ban is active (#360); a creator demoted to member acts as a member.
    """
    # The key names its organization, so it is read before a scope exists.
    with system(SystemReason.API_KEY_LOOKUP), get_conn() as conn:
        row = conn.execute(
            'SELECT k.user_id, k.organization_id, k.key_hash FROM user_api_keys k'
            ' WHERE k.key_hash = %s AND k.revoked_at IS NULL AND zb.is_live_member(k.user_id, k.organization_id)'
            ' LIMIT 1',
            (key_hash,),
        ).fetchone()
    if not row or not hmac.compare_digest(str(row['key_hash']), key_hash):
        return None
    return Principal(user_id=str(row['user_id']), org_id=str(row['organization_id']))


async def authenticate_or_api_key(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> Principal:
    """``authenticate``, plus ``Authorization: Bearer sbk_…`` personal API keys.

    An API-key caller carries no role: admin routes stay JWT-only. The
    plaintext never leaves this function, only its digest is queried.
    """
    if credentials is None or not credentials.credentials.startswith(API_KEY_PREFIX):
        return await authenticate(credentials)

    try:
        principal = await asyncio.to_thread(_lookup_api_key, hash_api_key(credentials.credentials))
    except Exception as e:  # transport failure is an auth failure
        logger.warning('auth.api_key.lookup_failed', reason=str(e)[:200])
        principal = None

    if not principal:
        raise _unauthorized('AUTH.API_KEY_INVALID', 'Invalid or revoked API key')
    return principal


async def current_user_or_api_key(
    request: Request, principal: Principal = Depends(authenticate_or_api_key)
) -> CurrentUser:
    """``current_user`` for the routes that also take a personal API key."""
    return await _enter(request, principal)


async def require_org_admin_or_api_key(user: CurrentUser = Depends(current_user_or_api_key)) -> CurrentUser:
    """``require_org_admin`` for the routes that also take a key: the key's creator must be an admin."""
    return _require_org_admin(user)


class Public:
    """The entry of a route with no caller: a webhook, an OAuth callback, the unsubscribe link, the health check, a
    catalog. With a reason, the request runs as ``System(reason)``: the route finds its organization itself. With
    none, it reads no tenant data. tests/test_scope_entry_points.py finds it by ``zb_public``."""

    def __init__(self, reason: SystemReason | None) -> None:
        self.zb_public = reason

    async def __call__(self) -> None:
        if self.zb_public is not None:
            enter_system_for_request(self.zb_public)


def public(reason: SystemReason | None) -> Any:
    """``dependencies=[public(SystemReason.X)]`` on a route with no caller."""
    return Depends(Public(reason))
