"""«Cerca il telefono» on one person row (#344): the agent's ``enrich_phone`` path and price, one row at a time.

The handler does the vendor work and the kill switches; this module holds the credits around it, on a ``Tab``. The
store's transaction records the charge of a found number and closes the hold from that record, so a worker killed
after the store still pays for the number it stored. The hold is released when nothing is found, so the user pays
only for a number they get (pay-if-found), and a per-row lock keeps two clicks from buying the same number twice."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from typing import Any, Literal
from uuid import UUID

import redis
import structlog
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.agent.tools import registry
from app.agent.tools.handlers import enrich_phone
from app.agent.tools.registry import ToolContext, ToolDef
from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.billing import balance, credits
from app.billing.price import Quote
from app.billing.tab import HoldGone, Tab
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.exclusions.rules import is_excluded, load_rules
from app.lists.delivery import deliver
from app.lists.lookups import phone_lookup
from app.lists.models import ListRow
from app.lists.row_view import ROW_COLS, read_row, with_lookups

logger = structlog.get_logger()
router = APIRouter(prefix='/v1/lists')

# The API process imports no tool handler otherwise: the import registers the tool this route prices and runs.
TOOL = enrich_phone.NAME
QUOTE = Quote(enrich_phone.PRICE, 1)
# Apollo polls a phone job for up to ~25s; the lock outlives that, and expires alone if the worker dies.
_LOCK_SECONDS = 90


class RowPhone(BaseModel):
    status: Literal['found', 'not_found', 'already', 'no_input', 'unavailable', 'failed']
    credits: int
    row: ListRow


def _lock_key(row_id: UUID) -> str:
    return f'row_phone:{row_id}'


def _load(row_id: UUID, list_id: UUID, org_id: str) -> dict:
    """The row and its list's project, only when the list is the organization's person list. ``_excluded``: excluded
    in the list or by its project's exclusions, the rows ``enrich_phone`` never searches."""
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {ROW_COLS}, l.project_id AS _project, r.cells->>'phone' AS _phone, r.cells AS _cells"
            ' FROM list_rows r JOIN lists l ON l.id = r.list_id'
            " WHERE r.id = %s AND l.id = %s AND l.organization_id = %s AND l.deleted_at IS NULL AND l.entity = 'person'",
            (row_id, list_id, org_id),
        ).fetchone()
        if not row:
            raise api_error(404, 'ROW.NOT_FOUND', 'row not found')
        row['_excluded'] = row['excluded'] or is_excluded(load_rules(conn, row['_project']), row['_cells'] or {})
    return with_lookups(row)


def _open(payer: credits.Payer, list_id: UUID) -> Tab:
    with get_conn() as conn:
        tab = Tab.open(
            conn,
            payer,
            QUOTE.credits,
            kind=TOOL,
            reference_id=str(list_id),
            tool_name=TOOL,
            description='Phone lookup',
        )
    balance.changed(payer.org_id, refused=isinstance(tab, credits.Refused))
    if isinstance(tab, credits.Refused):
        raise api_error(
            402,
            'ROW.PROJECT_CAP' if tab.capped else 'ROW.NO_CREDITS',
            'The project reached its monthly cap' if tab.capped else 'Not enough credits',
        )
    return tab


def _close(org_id: str, tab: Tab) -> None:
    """Releases the hold of a click that stored nothing: it raised first, or had no answer to store. After the
    store's own close it changes nothing."""
    with get_conn() as conn, suppress(HoldGone):
        tab.close(conn)
    balance.changed(org_id)


def _current(row_id: UUID, list_id: UUID) -> dict:
    with get_conn() as conn:
        return read_row(conn, row_id, list_id)


def _store(tab: Tab, answer: dict, row_id: UUID, list_id: UUID) -> int | None:
    """Stores the lookup through the chat's own delivery (``deliver``: the column appears, cells merge alike, a found
    number is recorded on the tab) and closes the tab in the same transaction. Returns what the close kept. None when
    a number arrived meanwhile, a hand edit or the chat's own search: nothing is stored or billed. HoldGone when the
    reaper closed the hold before a found number was recorded: nothing is stored either."""
    cells = {k: v for k, v in answer.items() if v is not None}
    update = {'row_id': str(row_id), 'cells': cells, 'if_empty': 'phone'}
    with get_conn() as conn:
        # The call id names this click's charge (row_charges.tool_call_id).
        if not deliver(conn, tab, QUOTE, uuid.uuid4().hex, [update], str(list_id), 'person').landed:
            return None
        if cells.get('phone'):
            meta = {'phone': {'status': 'filled', 'provider': cells.get('phone_source')}}
            conn.execute('UPDATE list_rows SET cell_meta = cell_meta || %s WHERE id = %s', (Jsonb(meta), row_id))
        # Gone at the close, the hold carries no record of this click (a record locks it until this commit): the
        # miss is stored and bills nothing.
        with suppress(HoldGone):
            return tab.close(conn)
        return 0


# Releases the lock only while it is still ours: it may have expired and been taken by another request.
_UNLOCK = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


def _lock(client: redis.Redis, row_id: UUID, token: str) -> None:
    try:
        locked = client.set(_lock_key(row_id), token, nx=True, ex=_LOCK_SECONDS)
    except redis.RedisError as e:
        raise api_error(503, 'ROW.PHONE_UNAVAILABLE', 'try again in a moment') from e
    if not locked:
        raise api_error(409, 'ROW.PHONE_RUNNING', 'a phone lookup is already running for this row')


def _unlock(client: redis.Redis, row_id: UUID, token: str) -> None:
    try:
        client.eval(_UNLOCK, 1, _lock_key(row_id), token)
    except redis.RedisError:
        logger.warning('row_phone.unlock_failed', row_id=str(row_id))  # expires alone after _LOCK_SECONDS


async def _lookup(
    tab: Tab, tool: ToolDef, row: dict, row_id: UUID, list_id: UUID, user: CurrentUser, project_id: str
) -> dict[str, Any]:
    """The vendor waterfall and the write; ``credits`` is what the store's close kept."""
    ctx = ToolContext(user_id=user.id, list_id=str(list_id), project_id=str(project_id), max_units=QUOTE.rows)
    try:
        result = await tool.handler({'row_ids': [str(row_id)]}, ctx)
    except Exception:
        logger.exception('row_phone.failed', row_id=str(row_id))
        return {'status': 'failed', 'credits': 0, 'row': row}
    if not result.ok:
        return {'status': 'unavailable', 'credits': 0, 'row': row}
    if not result.row_updates:
        return {'status': 'not_found', 'credits': 0, 'row': row}
    update = result.row_updates[0]
    state = phone_lookup(update['cells'], {}).state
    if state == 'not_searched':  # the row lost what it is searched by since the click (an API-key edit)
        return {'status': 'no_input', 'credits': 0, 'row': row}
    try:
        spent = await asyncio.to_thread(_store, tab, update['cells'], row_id, list_id)
    except HoldGone:  # the store rolled back: as after a crash, the web offers the search again
        return {'status': 'failed', 'credits': 0, 'row': row}
    current = await asyncio.to_thread(_current, row_id, list_id)
    if spent is None:
        return {'status': 'already', 'credits': 0, 'row': current}
    # found, not_found or failed: a failure is stored, so the web offers the search again (#659)
    return {'status': state, 'credits': spent, 'row': current}


async def search_phone(row_id: UUID, list_id: UUID, user: CurrentUser) -> dict[str, Any]:
    tool = registry.get(TOOL)
    if tool is None:
        raise api_error(503, 'ROW.PHONE_UNAVAILABLE', 'phone lookup is not available')
    row = await asyncio.to_thread(_load, row_id, list_id, user.org_id)
    project_id, has_phone, cells, excluded = (
        row.pop('_project'),
        row.pop('_phone'),
        row.pop('_cells'),
        row.pop('_excluded'),
    )
    if excluded:
        raise api_error(409, 'ROW.EXCLUDED', 'the row is excluded')
    if has_phone:
        return {'status': 'already', 'credits': 0, 'row': row}
    if not enrich_phone.searchable(cells):  # nothing to ask a vendor: no hold, and no verdict on the row
        return {'status': 'no_input', 'credits': 0, 'row': row}

    # ponytail: the lock covers clicks on this endpoint only; the chat's enrich_phone on the same row does not take
    # it. A click that loses is not billed (_store refuses), and a chat miss never erases its number (if_empty, #507);
    # a chat that finds one after the click overwrites and bills it. The vendor call may be paid twice. A shared lock
    # if it bites.
    client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    token = uuid.uuid4().hex
    _lock(client, row_id, token)
    try:
        payer = credits.Payer(user.org_id, user.id, str(project_id))
        tab = await asyncio.to_thread(_open, payer, list_id)
        try:
            return await _lookup(tab, tool, row, row_id, list_id, user, project_id)
        finally:
            await asyncio.shield(asyncio.to_thread(_close, user.org_id, tab))
    finally:
        _unlock(client, row_id, token)


@router.post('/{list_id}/rows/{row_id}/phone', response_model=RowPhone, response_model_exclude_unset=True)
async def row_phone(list_id: UUID, row_id: UUID, user: CurrentUser = Depends(current_user_or_api_key)) -> dict:
    """Look up the direct phone of one person. Charges ``credits`` only when a number is found."""
    return await search_phone(row_id, list_id, user)
