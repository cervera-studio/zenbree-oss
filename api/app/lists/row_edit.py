"""A hand edit of one list row."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from app.audience.link import IDENTITY_KEYS, mark_rows
from app.lists.rows import ensure_columns


def write_edit(conn, list_id, entity: str, row_id, cells: dict, excluded: bool | None, *, by: str) -> None:
    """Write ``cells`` (already checked by ``patch_statuses``) and ``excluded`` to a row of list ``list_id``. A
    hand-edited cell is marked manual: it loses its source, an AI refill skips it and a vendor row merge
    (``add_rows``) never overwrites it. A new email or profile joins the person's identity (#937)."""
    conn.execute(
        'UPDATE list_rows r SET cells = cells || %s, excluded = coalesce(%s, excluded), cell_meta = cell_meta || %s'
        ' WHERE id = %s AND list_id = %s',
        (Jsonb(cells), excluded, Jsonb({k: {'status': 'manual', 'by': by} for k in cells}), row_id, list_id),
    )
    if cells:
        ensure_columns(conn, str(list_id), entity, set(cells))
    if cells.keys() & IDENTITY_KEYS:
        mark_rows(conn, [str(row_id)], 'manual')  # the user's own value is the most trusted one
