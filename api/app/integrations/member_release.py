"""Revoking a sender at its provider when its owner goes (#471): a removed member (#411) or a deleted account (#333).

A Google grant is one per account and OAuth client, shared by every organization where the account is a sender, and
Google's revoke ends the whole grant. So a revoke happens only when no other live row shares it.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, require_org_admin
from app.core import scope
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.errors_http import api_error
from app.core.system_reasons import SystemReason
from app.integrations import token_crypto
from app.integrations.oauth_client import OAuthClient
from app.integrations.providers import HUBSPOT, MAILBOX_PROVIDERS
from app.integrations.senders import release_senders_of
from app.linkedin.unipile_client import UnipileClient

router = APIRouter(prefix='/v1/integrations/senders')
logger = structlog.get_logger()


def has_live_sibling(row: dict) -> bool:
    """Whether another live row, in any organization, holds the same ``(provider, provider_account_id)`` grant. The
    only cross-organization read here: a boolean, so no other organization's data leaves it."""
    with scope.system(SystemReason.SHARED_GRANT_CHECK), get_conn() as conn:
        return bool(
            conn.execute(
                'SELECT 1 FROM user_oauth_connections WHERE provider = %s AND provider_account_id = %s AND id <> %s'
                " AND status <> 'disconnected' AND refresh_token_encrypted <> '' LIMIT 1",
                (row['provider'], row['provider_account_id'], row['id']),
            ).fetchone()
        )


async def revoke_unless_shared(row: dict) -> bool:
    """Revoke the row's mailbox grant at its provider, unless a Google grant a live sibling row still uses.
    Best-effort; ``False`` when nothing was revoked. A row with no ``provider_account_id`` has no siblings to see."""
    provider = MAILBOX_PROVIDERS.get(row['provider'])
    if not provider or not row['refresh_token_encrypted']:
        return False
    if (
        row['provider'] == 'google'
        and row.get('provider_account_id')
        and await asyncio.to_thread(has_live_sibling, row)
    ):
        return False
    token = token_crypto.decrypt(row['refresh_token_encrypted'])
    return not is_err(token) and await OAuthClient(provider).revoke(token.value)


async def revoke_at_provider(row: dict) -> None:
    """Whatever the provider holds for this sender: the Unipile account, the HubSpot or the mailbox grant."""
    if row['provider'] == 'unipile':
        client = UnipileClient()
        if row['provider_account_id'] and client.is_configured():
            gone = await client.delete_account(account_id=row['provider_account_id'])
            if is_err(gone):
                logger.warning('integrations.release.unipile_failed', sender_id=str(row['id']))
    elif row['provider'] == HUBSPOT.name:
        if row['refresh_token_encrypted'] and not is_err(token := token_crypto.decrypt(row['refresh_token_encrypted'])):
            await OAuthClient(HUBSPOT).revoke(token.value)
    else:
        await revoke_unless_shared(row)


class Release(BaseModel):
    organization_id: UUID


class Released(BaseModel):
    released: int


def _is_member(org: UUID, user_id: UUID) -> bool:
    with get_conn() as conn:
        return bool(
            conn.execute(
                'SELECT 1 FROM member WHERE "organizationId" = %s AND "userId" = %s', (org, user_id)
            ).fetchone()
        )


@router.post('/members/{user_id}/release', response_model=Released)
async def release_member(user_id: UUID, body: Release, admin: CurrentUser = Depends(require_org_admin)) -> dict:
    """Before an admin removes a member (web: Better Auth's ``beforeRemoveMember``): their senders in this organization
    are revoked at the provider, disconnected as ``owner_removed`` and unlinked. 403 ``AUTH.ORG_ADMIN_ONLY`` when the
    body names another organization than the token's (``removeMember`` takes any); 404 when they are not a member."""
    if str(body.organization_id) != admin.org_id:
        raise api_error(403, 'AUTH.ORG_ADMIN_ONLY', 'Only an admin of the organization can do this')
    if not await asyncio.to_thread(_is_member, body.organization_id, user_id):
        raise api_error(404, 'REQUEST.NOT_FOUND', 'No such member in this organization')
    released = await release_senders_of(user_id, org=admin.org_id, detail='owner_removed')
    logger.info('integrations.members.released', admin_id=admin.id, user_id=str(user_id), released=released)
    return {'released': released}
