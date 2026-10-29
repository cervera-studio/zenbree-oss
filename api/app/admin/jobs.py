"""Backoffice jobs: one row per chat turn, with the user message that started it (chat sessions are in chats.py)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.middleware import require_admin
from app.core.db import get_conn

router = APIRouter(prefix='/v1/admin', tags=['admin'], dependencies=[Depends(require_admin)])


class Job(BaseModel):
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    chat_session_id: UUID | None
    kind: str
    status: Literal['queued', 'running', 'needs_input', 'succeeded', 'failed', 'cancelled']
    phase: str | None
    progress_done: int
    progress_total: int
    list_id: UUID | None
    reservation_id: UUID | None
    credits_reserved: int
    credits_spent: int
    error: str | None
    result: Any
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    retry_of: UUID | None
    retry_stage: int | None
    first_row_at: datetime | None
    pick: dict[str, Any] | None
    stages: list[dict[str, Any]] | None
    locale: str | None  # the language the turn's request named; None = the user's saved one
    email: str | None
    brief: str | None


class JobPage(BaseModel):
    items: list[Job]
    next_offset: int | None
    counts: dict[str, int]


@router.get('/jobs', response_model=JobPage)
def jobs(
    user_id: UUID | None = None,
    job_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    # ponytail: j.* keeps this in step with the schema; brief = the user message that started the turn.
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT j.*, u.email,
                      (SELECT m.content FROM chat_messages m
                        WHERE m.session_id = j.chat_session_id AND m.role = 'user' AND m.created_at <= j.created_at
                        ORDER BY m.created_at DESC LIMIT 1) AS brief
               FROM jobs j LEFT JOIN "user" u ON u.id = j.user_id
               WHERE (%(uid)s::uuid IS NULL OR j.user_id = %(uid)s::uuid)
                 AND (%(jid)s::uuid IS NULL OR j.id = %(jid)s::uuid)
                 AND (%(st)s::text IS NULL OR j.status = %(st)s::text)
               ORDER BY j.created_at DESC LIMIT %(lim)s OFFSET %(off)s""",
            {'uid': user_id, 'jid': job_id, 'st': status, 'lim': limit + 1, 'off': offset},
        ).fetchall()
        # ponytail: a full count per status on every load; a window (e.g. last 30 days) if jobs grows large.
        counts = conn.execute(
            """SELECT status, count(*)::int AS n FROM jobs
               WHERE (%(uid)s::uuid IS NULL OR user_id = %(uid)s::uuid)
                 AND (%(jid)s::uuid IS NULL OR id = %(jid)s::uuid)
               GROUP BY status""",
            {'uid': user_id, 'jid': job_id},
        ).fetchall()
    return {
        'items': rows[:limit],
        'next_offset': offset + limit if len(rows) > limit else None,
        'counts': {r['status']: r['n'] for r in counts},
    }
