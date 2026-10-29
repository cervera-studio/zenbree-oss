"""The AI column fill stream (``GET /v1/lists/{id}/stream``, #252) after the connect check in ``routes.fill_stream``."""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from uuid import UUID

from app.agent import events
from app.core.db import get_conn
from app.core.event_stream import MEMBER_RECHECK_SECONDS
from app.lists.ai_columns import fill_stream_key

LIST_GONE = {'message': 'list not found', 'code': 'LIST.NOT_FOUND'}


def _listed(list_id: UUID, org_id: str) -> bool:
    # The membership is EventStream's re-check; this one is the list itself, deleted while its fill streams.
    with get_conn() as conn:
        row = conn.execute(
            'SELECT 1 FROM lists WHERE id = %s AND organization_id = %s AND deleted_at IS NULL', (list_id, org_id)
        ).fetchone()
    return row is not None


async def fill_sse(list_id: UUID, after: str, org_id: str):
    # #413: like the chat stream (#323), access ends with the list, not at the next reconnect.
    checked = time.monotonic()
    async with aclosing(events.read_stream(fill_stream_key(str(list_id)), after)) as entries:
        async for item in entries:
            if time.monotonic() - checked >= MEMBER_RECHECK_SECONDS:
                checked = time.monotonic()
                if not await asyncio.to_thread(_listed, list_id, org_id):
                    yield events.frame('error', LIST_GONE)
                    return
            if item is None:
                yield ': ping\n\n'
                continue
            entry_id, fields = item
            yield f'event: {fields["event"]}\nid: {entry_id}\ndata: {fields["data"]}\n\n'
