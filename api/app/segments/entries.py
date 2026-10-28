"""The entry feed of a segment (#938): its ``enter`` records, oldest first, behind a cursor."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.segments.rules import Entity


class Entry(BaseModel):
    """The entry event ``segment.entered``. Its idempotency key is ``(segment_id, identity_id, episode)``."""

    seq: int
    segment_id: UUID
    project_id: UUID
    identity_id: UUID
    entity: Entity
    episode: int
    list_row_id: UUID | None
    segment_version: int
    entered_at: datetime


_ENTRIES = """
SELECT t.seq, t.segment_id, s.project_id, t.identity_id, s.entity, t.episode, t.list_row_id, t.segment_version,
       t.created_at AS entered_at
FROM segment_transitions t JOIN segments s ON s.id = t.segment_id
WHERE t.segment_id = %s AND t.kind = 'enter' AND t.seq > %s AND s.deleted_at IS NULL
ORDER BY t.seq LIMIT %s
"""


def entries_after(conn, segment_id: UUID | str, after_seq: int, limit: int) -> list[Entry]:
    """The segment's entries after the cursor ``after_seq``, oldest first. Pass the last ``seq`` back as the next
    cursor. ``baseline``, ``exit`` and ``merged`` records are history, never entries. A deleted segment has none."""
    return [Entry.model_validate(r) for r in conn.execute(_ENTRIES, (segment_id, after_seq, limit)).fetchall()]
