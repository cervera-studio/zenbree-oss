"""The organization's credit ledger, with a link from each spend to its chat or list (#348)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, require_org_admin_or_api_key
from app.billing.routes import Transaction
from app.core.db import get_conn

router = APIRouter(prefix='/v1/billing', tags=['billing'])


class LinkedTransaction(Transaction):
    # what the credits were spent on, when it belongs to the caller's organization: its chat, its list
    session_id: UUID | None
    list_id: UUID | None


class Transactions(BaseModel):
    items: list[LinkedTransaction]
    next_offset: int | None


@router.get('/transactions', response_model=Transactions)
def transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_org_admin_or_api_key),
) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.id, t.type, t.status, t.amount, t.balance_after, t.description, t.tool_name,
                      t.reference_type, t.created_at, s.id AS session_id, l.id AS list_id
               FROM credit_transactions t
               LEFT JOIN chat_sessions s ON s.id = t.session_id AND s.organization_id = t.organization_id
               LEFT JOIN lists l ON t.reference_type = 'ai_column' AND l.organization_id = t.organization_id AND l.deleted_at IS NULL
                AND l.id::text = split_part(t.reference_id, ':', 1)
               WHERE t.organization_id = %s
               ORDER BY t.created_at DESC, t.id LIMIT %s OFFSET %s""",
            (user.org_id, limit + 1, offset),
        ).fetchall()
    more = len(rows) > limit
    return {'items': rows[:limit], 'next_offset': offset + limit if more else None}
