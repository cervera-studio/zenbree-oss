from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.lists.ai_columns import AI_CELL, MAX_CELLS_PER_RUN
from app.lists.ai_snippets import MAX_INPUTS, check_inputs, research_rows
from app.lists.routes import Auth, FillQueued, _enqueue_fill, _own_list, _queue

router = APIRouter(prefix='/v1/lists')


class SnippetEstimateIn(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=MAX_INPUTS)
    row_ids: list[UUID] = Field(min_length=1, max_length=MAX_CELLS_PER_RUN)


class SnippetEstimate(BaseModel):
    research_rows: int  # rows of ``row_ids`` that are delivered and hold research in one of ``inputs``
    credits: int  # the most the fill charges: one AI cell for each of those rows


class RegenerateIn(BaseModel):
    row_ids: list[UUID] = Field(min_length=1, max_length=MAX_CELLS_PER_RUN)
    approved_credits: int = Field(ge=1)


@router.post('/{list_id}/columns/estimate', response_model=SnippetEstimate)
def estimate_snippet(list_id: UUID, body: SnippetEstimateIn, user: CurrentUser = Auth) -> dict:
    """What a snippet over ``inputs`` costs at most on ``row_ids``. A POST: 200 row ids do not fit in a URL."""
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        check_inputs(conn, str(list_id), body.inputs)
        rows = research_rows(conn, str(list_id), body.inputs, body.row_ids)
    return {'research_rows': rows, 'credits': rows * AI_CELL.credits}


@router.post('/{list_id}/columns/{key}/regenerate', status_code=202, response_model=FillQueued)
def regenerate_snippet(list_id: UUID, key: str, body: RegenerateIn, user: CurrentUser = Auth) -> dict:
    """Write the snippet of ``row_ids`` again, at most ``approved_credits`` of them: 1 credit for each new snippet."""
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        col = conn.execute(
            "SELECT 1 FROM list_columns WHERE list_id = %s AND key = %s AND kind = 'ai' AND deleted_at IS NULL"
            " AND config->>'mode' = 'snippet'",
            (list_id, key),
        ).fetchone()
        if not col:
            raise api_error(404, 'LIST.COLUMN_NOT_FOUND', 'AI snippet column not found')
        rows = [str(r) for r in dict.fromkeys(body.row_ids)]
        if not _queue(conn, str(list_id), key, user.id, cap=body.approved_credits, refill=rows):
            raise api_error(409, 'LIST.COLUMN_FILLING', 'This column is already being filled')
    _enqueue_fill(str(list_id), key)
    return {'status': 'queued'}
