"""The Instantly mailboxes of a project's workspace, for the Senders page (#936). Read only, and always 200.

The answer never goes through ``instantly._refusal``: Instantly answers a key without ``accounts:read`` with 401, and
``_refusal`` would mark that working key invalid and stop its enrollments and stops. Nothing here writes the
connection.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends

from app.admin.settings import is_disabled
from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors import is_err
from app.integrations import instantly_client, instantly_store
from app.integrations.instantly_models import AccountsState, InstantlyAccountsOut
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/instantly', tags=['Instantly'])
logger = structlog.get_logger()

# What the page shows for each failure; any other code (rate limit, unreachable, a key that cannot be decrypted)
# is `unavailable`.
_STATE_OF: dict[str, AccountsState] = {
    'INSTANTLY.NOT_CONNECTED': 'not_connected',
    'INSTANTLY.INVALID_KEY': 'no_permission',
    'INSTANTLY.MISSING_SCOPE': 'no_permission',
}


def _failed(code: str) -> dict:
    return {'state': _STATE_OF.get(code, 'unavailable'), 'accounts': []}


def _out(a: instantly_client.Account) -> dict:
    return {
        'email': a.email,
        'status': instantly_client.ACCOUNT_STATUS.get(a.status, 'unknown'),
        'warmup': instantly_client.WARMUP_STATUS.get(a.warmup_status, 'unknown'),
        'daily_limit': a.daily_limit,
    }


@router.get('/accounts', response_model=InstantlyAccountsOut)
async def accounts(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> dict:
    """The workspace's sending mailboxes with Instantly's own status, warm-up and daily limit."""

    def read() -> dict | None:
        with get_conn() as conn:
            return instantly_store.row(conn, project_id)

    key = instantly_store.api_key(await asyncio.to_thread(read))
    # The connection is read before the kill switch, so a project without Instantly still shows nothing while it is on.
    if is_err(key) and key.error.code == 'INSTANTLY.NOT_CONNECTED':
        return _failed(key.error.code)
    if is_disabled('vendor_disabled:instantly'):
        return _failed('INSTANTLY.DISABLED')
    if is_err(key):
        logger.warning('instantly.accounts_key_unreadable', project_id=project_id, code=key.error.code)
        return _failed(key.error.code)
    # paid_call already logs a vendor error with its code, so it is not logged again on every page view.
    found = await instantly_client.list_accounts(key.value)
    if is_err(found):
        return _failed(found.error.code)
    return {'state': 'ok', 'accounts': [_out(a) for a in found.value]}
