from __future__ import annotations

import structlog
from psycopg.types.json import Jsonb

from app.core.db import get_conn
from app.lists import fill_limits
from app.lists.ai_columns import FILL_LIVE_SQL, queue_fill
from app.lists.rows import DELIVERED_SQL

logger = structlog.get_logger()


def queue_plan_fills(
    list_id: str, keys: list[str], cap: int, by: str | None, job: str | None
) -> tuple[list[str], list[str]]:
    """(queued, refused): the plan's columns now queued, and those the hourly quota refused, marked failed so the
    list shows why. One transaction a column, so a refusal rolls back only its own claim. Columns left in neither
    are busy (a live fill, or the organization's live cap): the task's retry tries them again."""
    queued: list[str] = []
    for i, k in enumerate(keys):
        try:
            with get_conn() as conn:
                if queue_fill(conn, list_id, k, cap, by=by, job=job):
                    queued.append(k)
        except fill_limits.FillLimited as limited:
            logger.warning('ai_column.plan_fill_limited', list_id=list_id, code=limited.code)
            if limited.code != fill_limits.RATE_LIMITED:
                break
            with get_conn() as conn:
                refused = [
                    r['key']
                    for r in conn.execute(
                        "UPDATE list_columns SET config = jsonb_set(config, '{fill}', %s) WHERE list_id = %s"
                        f" AND key = ANY(%s) AND kind = 'ai' AND deleted_at IS NULL AND NOT {FILL_LIVE_SQL}"
                        # a complete column was never refused: it has nothing to research
                        ' AND EXISTS (SELECT 1 FROM list_rows r WHERE r.list_id = list_columns.list_id'
                        f" AND {DELIVERED_SQL} AND coalesce(r.cell_meta->list_columns.key->>'status', 'failed') = 'failed')"
                        ' RETURNING key',
                        (Jsonb({'status': 'failed', 'error': 'fill_rate_limited'}), list_id, keys[i:]),
                    )
                ]
            return queued, refused
    return queued, []
