from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.lists.ai_columns import MAX_CELLS_PER_RUN
from app.lists.rows import DELIVERED_SQL
from app.outreach.routes import _own

router = APIRouter(prefix='/v1/campaigns')
Auth = Depends(current_user)


class SnippetRows(BaseModel):
    list_id: UUID | None
    row_ids: list[UUID]
    total: int


_ROWS = f"""
SELECT r.id, count(*) OVER () AS total
  FROM outreach_campaign_targets t
  JOIN list_rows r ON r.id = t.row_id AND r.list_id = %(list_id)s AND {DELIVERED_SQL}
 WHERE t.campaign_id = %(campaign_id)s AND t.status IN ('draft', 'active', 'paused')
 ORDER BY t.created_at, t.id
 LIMIT %(limit)s"""


@router.get('/{campaign_id}/snippet-rows', response_model=SnippetRows)
def snippet_rows(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    """The delivered rows of the campaign's list behind its people who can still get a step, first enrolled
    first: at most one snippet fill's rows. ``total`` counts them all."""
    with get_conn() as conn:
        campaign = _own(conn, campaign_id, user.org_id)
        live = conn.execute(
            'SELECT 1 FROM lists WHERE id = %s AND organization_id = %s AND deleted_at IS NULL',
            (campaign['list_id'], user.org_id),
        ).fetchone()
        if not live:
            return {'list_id': None, 'row_ids': [], 'total': 0}
        rows = conn.execute(
            _ROWS, {'list_id': campaign['list_id'], 'campaign_id': campaign_id, 'limit': MAX_CELLS_PER_RUN}
        ).fetchall()
    return {
        'list_id': campaign['list_id'],
        'row_ids': [r['id'] for r in rows],
        'total': rows[0]['total'] if rows else 0,
    }
