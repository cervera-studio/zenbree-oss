"""A campaign's recent activity (#835): what happened to its people, newest first. The events are those the
per-target view already shows (``routes._TARGETS_SQL``): a delivered send, an approval, and the inbound messages
(reply, auto reply, opt-out). Read-only; the campaign is looked up through ``routes._own`` in the active project."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.outreach.routes import _own
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/campaigns')

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# One row per event. `id` is the send's or the inbound message's own id (a send gives two events, one id), so a
# page ends at (at, id, kind), the keyset that `after` carries.
_ACTIVITY_SQL = """
SELECT * FROM (
    SELECT s.id, 'sent' AS kind, s.delivered_at AS at, s.target_id, s.step, s.channel, NULL AS label
      FROM outreach_campaign_sends s
     WHERE s.campaign_id = %(cid)s AND s.owner_id IS NOT DISTINCT FROM %(owner)s AND s.status = 'sent'
    UNION ALL
    SELECT s.id, 'approved', s.approved_at, s.target_id, s.step, s.channel, NULL
      FROM outreach_campaign_sends s
     WHERE s.campaign_id = %(cid)s AND s.owner_id IS NOT DISTINCT FROM %(owner)s AND s.approved_at > s.created_at
    UNION ALL
    SELECT m.id, m.kind, m.received_at, m.target_id, NULL, m.channel, m.label
      FROM outreach_inbound_messages m
     WHERE m.campaign_id = %(cid)s AND m.owner_id IS NOT DISTINCT FROM %(owner)s
) e
LEFT JOIN LATERAL (
    SELECT t.first_name, t.last_name, t.company_name FROM outreach_campaign_targets t WHERE t.id = e.target_id
) t ON true
WHERE %(at)s::timestamptz IS NULL OR (e.at, e.id, e.kind) < (%(at)s, %(id)s::uuid, %(kind)s)
ORDER BY e.at DESC, e.id DESC, e.kind DESC
LIMIT %(limit)s"""


class ActivityEvent(BaseModel):
    id: UUID
    kind: Literal['sent', 'approved', 'reply', 'auto_reply', 'unsubscribe']
    at: datetime
    target_id: UUID | None
    first_name: str | None
    last_name: str | None
    company_name: str | None
    step: int | None
    channel: Literal['email', 'linkedin', 'linkedin_connect', 'linkedin_message', 'hubspot_task']
    label: Literal['interested', 'meeting_request', 'not_interested', 'not_now', 'other'] | None


class Activity(BaseModel):
    items: list[ActivityEvent]
    next_cursor: str | None


def _cursor(e: dict) -> str:
    return base64.urlsafe_b64encode(f'{e["at"].isoformat()}|{e["id"]}|{e["kind"]}'.encode()).decode()


def _after(cursor: str | None) -> dict:
    if cursor is None:
        return {'at': None, 'id': None, 'kind': None}
    try:
        at, id_, kind = base64.urlsafe_b64decode(cursor.encode()).decode().split('|')
        return {'at': datetime.fromisoformat(at), 'id': UUID(id_), 'kind': kind}
    except ValueError:  # bad base64, field count, timestamp or uuid
        raise api_error(422, 'REQUEST.INVALID_INPUT', 'invalid cursor') from None


@router.get('/{campaign_id}/activity', response_model=Activity)
def activity(
    campaign_id: UUID,
    after: str | None = Query(None, max_length=200),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: CurrentUser = Depends(current_user),
    project_id: str = Depends(current_project),
) -> dict:
    """The campaign's recent events, newest first. ``after`` is the previous page's ``next_cursor``."""
    keyset = _after(after)
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id)
        if str(c['project_id']) != project_id:
            raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')
        items = conn.execute(
            _ACTIVITY_SQL, {'cid': campaign_id, 'owner': c['owner_id'], 'limit': limit, **keyset}
        ).fetchall()
    return {'items': items, 'next_cursor': _cursor(items[-1]) if len(items) == limit else None}
