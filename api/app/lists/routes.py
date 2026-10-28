"""Lists: read, page rows, patch a row, AI column fill stream, CSV/Excel export, CSV upload. Owner-scoped."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.agent import events
from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.event_stream import EventStream
from app.lists.ai_columns import (
    FILL_LIVE_SQL,
    MAX_CELLS_PER_RUN,
    PUBLIC_CONFIG_SQL,
    column_key,
    fill_stream_key,
    queue_fill,
)
from app.lists.ai_snippets import MAX_INPUTS, snippet_config
from app.lists.fill_limits import COLUMNS_PER_LIST, FillLimited
from app.lists.fill_stream import fill_sse
from app.lists.last_search import FINDABLE_SQL
from app.lists.lookups import patch_statuses
from app.lists.models import ListColumn, ListRow
from app.lists.prices import CreditPrices, credit_prices
from app.lists.rate_limit import limit_list_reads
from app.lists.row_edit import write_edit
from app.lists.row_view import ROW_COLS, read_row, with_lookups
from app.lists.rows import (
    DELIVERED_SQL,
    add_people_counts,
    fill_credits_sql,
    public_columns,
)

router = APIRouter(prefix='/v1/lists')
Auth = Depends(current_user_or_api_key)
logger = structlog.get_logger()


_FILLING = ('queued', 'running')


def _own_list(conn, list_id: UUID, org_id: str) -> dict:
    row = conn.execute(
        'SELECT id, name, entity, chat_session_id FROM lists WHERE id = %s AND organization_id = %s AND deleted_at IS NULL',
        (list_id, org_id),
    ).fetchone()
    if not row:
        raise api_error(404, 'LIST.NOT_FOUND', 'list not found')
    return row


# What the lists screens show (#228). ``status`` follows the chat's latest job: a turn paused on this
# list's unapproved plan is ``plan_pending``. ``credits_used`` is what the chat spent, as its header says
# (#239): every turn (net of refunds) plus the AI column fills of its lists (just this list's without a chat).
_LIST_SQL = (
    'SELECT l.id, l.name, l.entity, l.mode, l.chat_session_id, l.project_id, l.created_at,'
    ' c.row_count, c.delivered_count,'
    " coalesce(NOT l.last_search ? 'exhausted', false) AS can_expand,"
    + FINDABLE_SQL
    + " CASE WHEN j.status IN ('queued', 'running') THEN 'running'"
    "  WHEN j.status = 'needs_input' AND p.plan IS NOT NULL AND p.approved_at IS NULL THEN 'plan_pending'"
    "  WHEN j.status = 'needs_input' THEN 'needs_input' WHEN c.delivered_count = 0 THEN 'empty' ELSE 'ready' END AS status,"
    " coalesce(p.approved_rows, (p.plan->>'target_rows')::int) AS target_rows,"
    ' (SELECT coalesce(sum(credits_spent), 0)::int FROM jobs WHERE chat_session_id = l.chat_session_id) + '
    + fill_credits_sql(
        'fl.organization_id = l.organization_id AND (fl.id = l.id OR fl.chat_session_id = l.chat_session_id)'
    )
    + ' AS credits_used,'
    ' (SELECT left(content, 300) FROM chat_messages m WHERE m.session_id = l.chat_session_id'
    "  AND m.role = 'user' ORDER BY m.created_at LIMIT 1) AS brief,"
    ' s.title AS chat_title'
    ' FROM lists l'
    f' CROSS JOIN LATERAL (SELECT count(*) AS row_count, count(*) FILTER (WHERE {DELIVERED_SQL}) AS delivered_count'
    '  FROM list_rows r WHERE r.list_id = l.id) c'
    ' LEFT JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
    ' LEFT JOIN LATERAL (SELECT status FROM jobs WHERE chat_session_id = l.chat_session_id'
    '  ORDER BY created_at DESC LIMIT 1) j ON true'
    ' LEFT JOIN LATERAL (SELECT plan, approved_at, approved_rows FROM list_plans WHERE list_id = l.id'
    '  ORDER BY version DESC LIMIT 1) p ON true'
    ' WHERE l.organization_id = %s AND l.deleted_at IS NULL'
)


def _columns(conn, list_id: UUID) -> list[dict]:
    return conn.execute(
        f'SELECT key, label, kind, position, {PUBLIC_CONFIG_SQL} AS config FROM list_columns'
        ' WHERE list_id = %s AND deleted_at IS NULL ORDER BY position',
        (list_id,),
    ).fetchall()


Entity = Literal['company', 'person']


class ListSummary(BaseModel):
    id: UUID
    name: str
    entity: Entity
    mode: Literal['build', 'enrich']
    chat_session_id: UUID | None
    project_id: UUID
    created_at: datetime
    row_count: int
    delivered_count: int
    can_expand: bool
    findable: int | None
    status: Literal['running', 'plan_pending', 'needs_input', 'empty', 'ready']
    target_rows: int | None
    credits_used: int
    brief: str | None
    chat_title: str | None


class Lists(BaseModel):
    lists: list[ListSummary]


class ListDetail(ListSummary):
    columns: list[ListColumn]
    fill_cursor: str | None
    prices: CreditPrices


class ListRows(BaseModel):
    rows: list[ListRow]
    next_cursor: int | None


class FillQueued(BaseModel):
    status: Literal['queued']


@router.get('', response_model=Lists)
def list_lists(user: CurrentUser = Auth, project_id: UUID | None = None) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            _LIST_SQL + ' AND (%s::uuid IS NULL OR l.project_id = %s) ORDER BY l.created_at DESC LIMIT 500',
            (user.org_id, project_id, project_id),
        ).fetchall()
    return {'lists': rows}


@router.get('/{list_id}', response_model=ListDetail)
def get_list(list_id: UUID, user: CurrentUser = Auth) -> dict:
    """``fill_cursor`` only while a column fills (else no Redis round trip): read before the columns are
    read again, so a fill frame is either in them or after it."""
    with get_conn() as conn:
        lst = conn.execute(_LIST_SQL + ' AND l.id = %s', (user.org_id, list_id)).fetchone()
        if not lst:
            raise api_error(404, 'LIST.NOT_FOUND', 'list not found')
        cols = _columns(conn, list_id)
    cursor = None
    if any(c['kind'] == 'ai' and ((c['config'] or {}).get('fill') or {}).get('status') in _FILLING for c in cols):
        cursor = events.last_id(fill_stream_key(str(list_id)))
        with get_conn() as conn:
            cols = _columns(conn, list_id)
    return {**lst, 'columns': public_columns(cols), 'fill_cursor': cursor, 'prices': credit_prices()}


@router.get('/{list_id}/stream', response_class=EventStream)
async def fill_stream(
    list_id: UUID, user: CurrentUser = Auth, last_event_id: str | None = Header(default=None)
) -> EventStream:
    """SSE: AI column ``fill`` and ``row`` frames after ``Last-Event-ID`` (else from now), ``: ping`` every 15 s."""

    def own() -> None:
        with get_conn() as conn:
            _own_list(conn, list_id, user.org_id)

    await asyncio.to_thread(own)
    after = events.entry_id(last_event_id) or await asyncio.to_thread(events.last_id, fill_stream_key(str(list_id)))
    if after is None:
        raise api_error(503, 'LIST.STREAM_UNAVAILABLE', 'stream unavailable')
    return EventStream(fill_sse(list_id, after, user.org_id))


@router.get(
    '/{list_id}/rows',
    dependencies=[Depends(limit_list_reads)],
    response_model=ListRows,
    response_model_exclude_unset=True,
)
def get_rows(
    list_id: UUID,
    user: CurrentUser = Auth,
    cursor: int = 0,
    limit: int = Query(default=100, ge=1, le=500),
    min_fit: Annotated[int | None, Query(ge=0, le=100)] = None,
) -> dict:
    """Insertion order; ``cursor`` is the ``next_cursor`` of the previous page. ``min_fit``: only rows whose
    ``relevance_score`` is at least that (unscored rows are out)."""
    with get_conn() as conn:
        lst = _own_list(conn, list_id, user.org_id)
        rows = conn.execute(
            f'SELECT {ROW_COLS} FROM list_rows r WHERE list_id = %s AND seq > %s'
            ' AND (%s::int IS NULL OR r.relevance_score >= %s) ORDER BY seq LIMIT %s',
            (list_id, cursor, min_fit, min_fit, limit),
        ).fetchall()
        if lst['entity'] == 'company':
            add_people_counts(conn, lst['chat_session_id'], rows)
    return {'rows': [with_lookups(r) for r in rows], 'next_cursor': rows[-1]['seq'] if len(rows) == limit else None}


class RowPatch(BaseModel):
    cells: dict | None = None
    excluded: bool | None = None


@router.patch('/{list_id}/rows/{row_id}', response_model=ListRow, response_model_exclude_unset=True)
def patch_row(list_id: UUID, row_id: UUID, body: RowPatch, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        lst = _own_list(conn, list_id, user.org_id)
        cells = patch_statuses(conn, list_id, row_id, body.cells or {})
        write_edit(conn, list_id, lst['entity'], row_id, cells, body.excluded, by=user.id)
        return read_row(conn, row_id, list_id)


class AiColumnIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=3, max_length=500)
    mode: Literal['research', 'snippet'] = 'research'
    inputs: list[str] | None = Field(default=None, max_length=MAX_INPUTS)
    row_ids: list[UUID] | None = Field(default=None, max_length=MAX_CELLS_PER_RUN)
    approved_credits: int | None = Field(default=None, ge=1)


def _enqueue_fill(list_id: str, key: str) -> None:
    from app.worker import fill_ai_column

    try:
        fill_ai_column.delay(list_id, key)
    except Exception as e:
        with get_conn() as conn:
            conn.execute(
                'UPDATE list_columns SET config = jsonb_set(config, \'{fill}\', \'{"status": "failed"}\')'
                ' WHERE list_id = %s AND key = %s',
                (list_id, key),
            )
        raise api_error(503, 'LIST.QUEUE_UNAVAILABLE', 'Try again in a moment') from e


def _ai_column_room(conn, list_id: UUID) -> None:
    """Lock the list, then refuse a column past COLUMNS_PER_LIST: the add and the restore both count under the lock."""
    conn.execute('SELECT 1 FROM lists WHERE id = %s FOR UPDATE', (list_id,))
    live = conn.execute(
        "SELECT count(*) AS n FROM list_columns WHERE list_id = %s AND kind = 'ai' AND deleted_at IS NULL", (list_id,)
    ).fetchone()['n']
    if live >= COLUMNS_PER_LIST:
        raise api_error(422, 'LIST.TOO_MANY_AI_COLUMNS', f'A list holds at most {COLUMNS_PER_LIST} AI columns')


def _queue(conn, list_id: str, key: str, by: str, cap: int | None = None, refill: list[str] | None = None) -> bool:
    try:
        return queue_fill(conn, list_id, key, cap, by=by, refill=refill)
    except FillLimited as e:
        raise api_error(429, e.code, 'Too many AI column fills', headers={'Retry-After': str(e.retry_after)}) from e


@router.post('/{list_id}/columns', status_code=201, response_model=ListColumn)
def add_ai_column(list_id: UUID, body: AiColumnIn, user: CurrentUser = Auth) -> dict:
    """Add an AI column and queue its fill (credits per filled cell, charged by the task)."""
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        config = {'prompt': body.prompt}
        if body.mode == 'snippet':
            config = snippet_config(conn, str(list_id), body.prompt, body.inputs, body.row_ids, body.approved_credits)
        _ai_column_room(conn, list_id)  # also serialises key/position
        # Deleted columns too: their key and position stay theirs, so a restore never collides.
        cols = conn.execute('SELECT key, position FROM list_columns WHERE list_id = %s', (list_id,)).fetchall()
        key = column_key(body.label, {c['key'] for c in cols})
        col = conn.execute(
            "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, %s, %s, 'ai', %s, %s)"
            ' RETURNING key, label, kind, position, config',
            (
                list_id,
                key,
                body.label,
                max((c['position'] for c in cols), default=-1) + 1,
                Jsonb(config),
            ),
        ).fetchone()
        _queue(conn, str(list_id), key, user.id)
    _enqueue_fill(str(list_id), key)
    return {**col, 'config': {**col['config'], 'fill': {'status': 'queued'}}}


@router.post('/{list_id}/columns/{key}/fill', status_code=202, response_model=FillQueued)
def fill_column(list_id: UUID, key: str, user: CurrentUser = Auth) -> dict:
    """Fill the cells still missing or failed on delivered rows. 409 while a fill of this column is live."""
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        col = conn.execute(
            "SELECT 1 FROM list_columns WHERE list_id = %s AND key = %s AND kind = 'ai' AND deleted_at IS NULL",
            (list_id, key),
        ).fetchone()
        if not col:
            raise api_error(404, 'LIST.COLUMN_NOT_FOUND', 'AI column not found')
        if not _queue(conn, str(list_id), key, user.id):
            raise api_error(409, 'LIST.COLUMN_FILLING', 'This column is already being filled')
    _enqueue_fill(str(list_id), key)
    return {'status': 'queued'}


def _busy(code: str, message: str) -> HTTPException:
    return api_error(409, code, message)


@router.delete('/{list_id}/columns/{key}', status_code=204)
def delete_column(list_id: UUID, key: str, user: CurrentUser = Auth) -> None:
    """Hide an AI column (the ones users add); its cells stay in the rows for the undo."""
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        col = conn.execute(
            f'SELECT {FILL_LIVE_SQL} AS live FROM list_columns'
            " WHERE list_id = %s AND key = %s AND kind = 'ai' AND deleted_at IS NULL FOR UPDATE",
            (list_id, key),
        ).fetchone()
        if not col:
            raise api_error(404, 'LIST.COLUMN_NOT_FOUND', 'AI column not found')
        if col['live']:
            raise _busy('LIST.COLUMN_FILLING', 'This column is being filled')
        conn.execute('UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = %s', (list_id, key))


@router.post('/{list_id}/columns/{key}/restore', response_model=ListColumn)
def restore_column(list_id: UUID, key: str, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        gone = conn.execute(
            'SELECT kind FROM list_columns WHERE list_id = %s AND key = %s AND deleted_at IS NOT NULL', (list_id, key)
        ).fetchone()
        if gone and gone['kind'] == 'ai':
            _ai_column_room(conn, list_id)
        col = conn.execute(
            'UPDATE list_columns SET deleted_at = NULL WHERE list_id = %s AND key = %s AND deleted_at IS NOT NULL'
            f' RETURNING key, label, kind, position, {PUBLIC_CONFIG_SQL} AS config',
            (list_id, key),
        ).fetchone()
    if not col:
        raise api_error(404, 'LIST.COLUMN_NOT_FOUND', 'deleted column not found')
    return col


def _owned_list(conn, list_id: UUID, user: CurrentUser, *, deleted: bool) -> None:
    """Lock the organization's list; only who created it, or an admin, deletes or restores it."""
    lst = conn.execute(
        'SELECT user_id FROM lists WHERE id = %s AND organization_id = %s AND (deleted_at IS NOT NULL) = %s FOR UPDATE',
        (list_id, user.org_id, deleted),
    ).fetchone()
    if not lst:
        raise api_error(404, 'LIST.NOT_FOUND', 'list not found')
    if str(lst['user_id']) != user.id and user.org_role != 'admin':
        raise api_error(403, 'LIST.NOT_OWNER', 'Only who created the list, or an admin')


@router.delete('/{list_id}', status_code=204)
def delete_list(list_id: UUID, user: CurrentUser = Auth) -> None:
    """Hide the list with its rows and columns (undo: restore). Refused while its chat runs a turn or a column
    fills, so nothing writes into a deleted list; the chat's next turn starts a new list."""
    with get_conn() as conn:
        _owned_list(conn, list_id, user, deleted=False)
        busy = conn.execute(
            'SELECT EXISTS (SELECT 1 FROM jobs j JOIN lists l ON l.chat_session_id = j.chat_session_id'
            "  WHERE l.id = %s AND j.status IN ('queued', 'running'))"
            f' OR EXISTS (SELECT 1 FROM list_columns WHERE list_id = %s AND deleted_at IS NULL AND {FILL_LIVE_SQL})'
            ' AS busy',
            (list_id, list_id),
        ).fetchone()['busy']
        if busy:
            raise _busy('LIST.BUSY', 'Wait for the list to finish filling')
        # ponytail: a turn whose job commits between the check above and this commit, and whose loader reads the
        # list before it, still writes into the deleted list (rows hidden, a restore shows them). Guard add_rows
        # on lists.deleted_at if that window ever matters.
        conn.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (list_id,))


@router.post('/{list_id}/restore', status_code=204)
def restore_list(list_id: UUID, user: CurrentUser = Auth) -> None:
    with get_conn() as conn:
        _owned_list(conn, list_id, user, deleted=True)
        conn.execute('UPDATE lists SET deleted_at = NULL WHERE id = %s', (list_id,))
