"""Back office: a user's chat sessions and one chat's transcript with what it cost (#352). Admin-only."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.admin.routes import _not_found
from app.agent.tool_calls import AdminToolCalls
from app.auth.middleware import require_admin
from app.core.config import settings
from app.core.db import get_conn

router = APIRouter(prefix='/v1/admin', tags=['admin'], dependencies=[Depends(require_admin)])


class ChatSessionSummary(BaseModel):
    id: UUID
    user_id: UUID | None
    email: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    companies: int
    people: int
    credits_spent: int
    outcome: Literal['results', 'stuck', 'no_results'] | None


class ChatSessionPage(BaseModel):
    items: list[ChatSessionSummary]
    next_offset: int | None


@router.get('/chat-sessions', response_model=ChatSessionPage)
def chat_sessions(
    user_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.user_id, u.email, s.title, s.created_at, s.updated_at,
                      (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id)::int AS message_count,
                      r.companies, r.people, j.credits_spent,
                      CASE WHEN r.companies + r.people > 0 THEN 'results'
                           WHEN j.last_status IN ('failed', 'needs_input') THEN 'stuck'
                           WHEN j.last_status IN ('succeeded', 'cancelled') THEN 'no_results' END AS outcome
               FROM chat_sessions s LEFT JOIN "user" u ON u.id = s.user_id
               CROSS JOIN LATERAL (
                   SELECT (count(*) FILTER (WHERE l.entity = 'company'))::int AS companies,
                          (count(*) FILTER (WHERE l.entity = 'person'))::int AS people
                   FROM list_rows lr JOIN lists l ON l.id = lr.list_id
                   WHERE l.chat_session_id = s.id AND l.deleted_at IS NULL AND NOT lr.excluded) r
               CROSS JOIN LATERAL (
                   SELECT COALESCE(sum(credits_spent), 0)::int AS credits_spent,
                          (array_agg(status ORDER BY created_at DESC))[1] AS last_status
                   FROM jobs WHERE chat_session_id = s.id) j
               WHERE %(uid)s::uuid IS NULL OR s.user_id = %(uid)s::uuid
               ORDER BY s.updated_at DESC LIMIT %(lim)s OFFSET %(off)s""",
            {'uid': user_id, 'lim': limit + 1, 'off': offset},
        ).fetchall()
    return {'items': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None}


class AdminChatMessage(BaseModel):
    id: UUID
    role: Literal['user', 'assistant']
    content: str
    tool_calls: AdminToolCalls
    question: dict[str, Any] | None
    credits_used: int
    job_id: UUID | None
    created_at: datetime


class SessionMoney(BaseModel):
    job_id: str | None
    credits_spent: int
    credits_returned: int
    llm_cost_usd: float
    vendor_cost_usd: float
    companies: int
    people: int
    eur_per_usd: float
    eur_per_1000_credits: float | None
    revenue_eur: float | None
    margin_eur: float | None


class AdminChatSession(BaseModel):
    id: UUID
    user_id: UUID | None
    email: str | None
    name: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[AdminChatMessage]
    summary: SessionMoney


@router.get('/chat-sessions/{session_id}', response_model=AdminChatSession, response_model_exclude_unset=True)
def chat_session(session_id: UUID) -> dict:
    with get_conn() as conn:
        s = conn.execute(
            """SELECT s.id, s.user_id, u.email, u.name, s.title, s.created_at, s.updated_at
               FROM chat_sessions s LEFT JOIN "user" u ON u.id = s.user_id WHERE s.id = %s::uuid""",
            (session_id,),
        ).fetchone()
        if not s:
            raise _not_found('Chat session')
        messages = conn.execute(
            """SELECT id, role, content, tool_calls, question, credits_used, job_id, created_at FROM chat_messages
               WHERE session_id = %s::uuid ORDER BY created_at, id""",
            (session_id,),
        ).fetchall()
        summary = conn.execute(_SESSION_SUMMARY, {'sid': session_id, 'uid': s['user_id']}).fetchone()
    return {**s, 'messages': messages, 'summary': _session_money(summary)}


# Credits and our cost for one chat. vendor_calls carries no session or job id,
# so a vendor call counts when it falls inside one of the session's jobs.
# ponytail: one live job per user keeps that exact for chat turns; a list task
# (AI column fill) running for the same user meanwhile is counted too. A job_id
# on vendor_calls is the upgrade.
_SESSION_SUMMARY = """
WITH j AS (SELECT id, credits_spent, started_at, COALESCE(finished_at, now()) AS ended,
                  COALESCE((result->>'refunded')::int, 0) AS refunded
           FROM jobs WHERE chat_session_id = %(sid)s::uuid),
paid AS (SELECT COALESCE(sum(amount_cents - refunded_cents), 0)::int AS cents,
                COALESCE(sum(credits), 0)::int AS credits
         FROM purchases WHERE user_id = %(uid)s::uuid)
SELECT (SELECT id::text FROM jobs WHERE chat_session_id = %(sid)s::uuid ORDER BY created_at DESC LIMIT 1) AS job_id,
       (SELECT COALESCE(sum(credits_spent), 0)::int FROM j) AS credits_spent,
       (SELECT COALESCE(sum(refunded), 0)::int FROM j) AS credits_returned,
       (SELECT COALESCE(sum(cost_usd), 0)::float8 FROM llm_usage_costed
         WHERE session_id = %(sid)s::uuid) AS llm_cost_usd,
       (SELECT COALESCE(sum(v.cost_usd), 0)::float8 FROM vendor_calls v JOIN j
          ON v.created_at >= j.started_at AND v.created_at <= j.ended
         WHERE v.user_id = %(uid)s::uuid) AS vendor_cost_usd,
       (SELECT count(*) FROM list_rows r JOIN lists l ON l.id = r.list_id
         WHERE l.chat_session_id = %(sid)s::uuid AND l.deleted_at IS NULL AND l.entity = 'company' AND NOT r.excluded)::int AS companies,
       (SELECT count(*) FROM list_rows r JOIN lists l ON l.id = r.list_id
         WHERE l.chat_session_id = %(sid)s::uuid AND l.deleted_at IS NULL AND l.entity = 'person' AND NOT r.excluded)::int AS people,
       paid.cents AS paid_cents, paid.credits AS paid_credits
FROM paid
"""


def _session_money(r: dict) -> dict:
    """Revenue is estimated at the user's average paid price per credit; none without a purchase."""
    paid_cents, paid_credits = r.pop('paid_cents'), r.pop('paid_credits')
    cost_eur = (r['llm_cost_usd'] + r['vendor_cost_usd']) * settings.eur_per_usd
    eur_per_credit = paid_cents / 100 / paid_credits if paid_credits else None
    revenue = round(r['credits_spent'] * eur_per_credit, 4) if eur_per_credit is not None else None
    return {
        **r,
        'eur_per_usd': settings.eur_per_usd,
        'eur_per_1000_credits': round(eur_per_credit * 1000, 2) if eur_per_credit is not None else None,
        'revenue_eur': revenue,
        'margin_eur': round(revenue - cost_eur, 4) if revenue is not None else None,
    }
