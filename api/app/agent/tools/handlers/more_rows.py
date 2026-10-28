"""more_rows: the next page of the search that filled a list (the earlier implementation ``expand_discovery``).

The loop records every paged search on its list (``remember_search``); this tool re-runs it at
the following page, so a page already fetched is never bought again, and drops rows the list
already holds before billing (pay-if-found).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.agent.tools import registry
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.core.db import get_conn
from app.lists.last_search import remember_search
from app.lists.rows import dedupe_key


def _load(list_id: str, ctx: ToolContext) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            'SELECT l.entity, l.last_search FROM lists l'
            ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
            ' WHERE l.id = %s AND s.id = %s AND l.deleted_at IS NULL',
            (list_id, ctx.session_id),
        ).fetchone()


def _drop_known(list_id: str, entity: str, result: ToolResult) -> int:
    keys = [dedupe_key(entity, row) for row in result.rows]
    with get_conn() as conn:
        known = {
            r['dedupe_key']
            for r in conn.execute(
                'SELECT dedupe_key FROM list_rows WHERE list_id = %s AND dedupe_key = ANY(%s)',
                (list_id, [k for k in keys if k]),
            )
        }
    kept = [row for row, key in zip(result.rows, keys, strict=True) if key is None or key not in known]
    dropped = len(result.rows) - len(kept)
    result.rows = kept
    return dropped


def _mark_exhausted(list_id: str) -> None:
    with get_conn() as conn:
        conn.execute('UPDATE lists SET last_search = last_search || \'{"exhausted": true}\' WHERE id = %s', (list_id,))


async def more_rows(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        # Required, never ctx.list_id: a chat can hold a company and a person list.
        list_id = str(UUID(str(args.get('list_id'))))
    except ValueError:
        return ToolResult(ok=False, error='list_id is not a list of this chat')
    lst = await asyncio.to_thread(_load, list_id, ctx) if ctx.session_id else None
    if not lst:
        return ToolResult(ok=False, error='list_id is not a list of this chat')
    search = lst['last_search']
    tool = registry.get(search['tool']) if search else None
    if not search or not tool:
        return ToolResult(ok=False, error='This list has no paged search to continue: run a new search instead.')
    if search.get('exhausted'):
        return ToolResult(ok=False, error='The search behind this list has no more results: try other filters.')
    page = int(search['args'].get('page') or 1) + 1
    next_args = {**search['args'], 'page': page}
    result = await tool.handler(next_args, ctx)
    if not result.ok:
        return result
    if not result.rows:
        await asyncio.to_thread(_mark_exhausted, list_id)
        result.data = {**result.data, 'page': page, 'exhausted': True}
        return result
    fetched = result.data.get('count', len(result.rows))  # before the rows the list already holds are dropped
    dropped = await asyncio.to_thread(_drop_known, list_id, lst['entity'], result)
    await asyncio.to_thread(remember_search, list_id, search['tool'], next_args, result.data.get('total'), fetched)
    result.data = {**result.data, 'page': page, 'already_in_list': dropped}
    return result


register(
    ToolDef(
        name='more_rows',
        description=(
            'Add more rows to a list from the same search: fetches the next page of the last paged search '
            'that filled it (never a page already fetched) and skips rows already in the list. Costs what that '
            'search costs, only for new rows. Use it when the user asks for more rows or more results of the '
            'same kind; read list_row_feedback first if rows were rated. list_id is required: the id of the list to extend.'
        ),
        input_schema={'type': 'object', 'properties': {'list_id': {'type': 'string'}}, 'required': ['list_id']},
        handler=more_rows,
        found_noun='entity',
        provider=None,  # the re-run search checks its vendor's kill switch itself
    )
)
