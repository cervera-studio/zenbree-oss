"""The sidebar and the phone menu (#342): the counts next to each entry and the chats with their status."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.agent.jobs import STALE_JOB_MINUTES
from app.agent.routes import ChatSessionItem
from app.agent.session_list import session_list
from app.home.overview import awaiting_approval
from app.notifications.service import unread_replies


class Nav(BaseModel):
    lists: int
    campaigns: int
    approvals: int
    views: int
    replies: int
    sessions: list[ChatSessionItem]


def load_nav(conn, user_id: str, org_id: str, project_id: str) -> tuple[Nav, datetime | None]:
    counts = conn.execute(
        'SELECT (SELECT count(*) FROM lists l WHERE l.organization_id = %s AND l.project_id = %s'
        ' AND l.deleted_at IS NULL AND EXISTS (SELECT 1 FROM list_rows r WHERE r.list_id = l.id)) AS lists,'
        ' (SELECT count(*) FROM outreach_campaigns WHERE organization_id = %s AND project_id = %s) AS campaigns,'
        ' (SELECT count(*) FROM saved_views v JOIN lists l ON l.id = v.list_id AND l.deleted_at IS NULL'
        ' WHERE v.organization_id = %s AND v.project_id = %s) AS views',
        (org_id, project_id, org_id, project_id, org_id, project_id),
    ).fetchone()
    sessions = session_list(conn, org_id, project_id)
    nav = Nav(
        lists=counts['lists'],
        campaigns=counts['campaigns'],
        views=counts['views'],
        approvals=sum(w['n'] for w in awaiting_approval(conn, org_id, project_id)),
        replies=unread_replies(conn, user_id, org_id)[0],
        sessions=[ChatSessionItem(**s) for s in sessions],
    )
    # A running chat turns to `error` once its job goes stale, with no write to wake the stream.
    stale_at = min(
        (s['job_updated_at'] + timedelta(minutes=STALE_JOB_MINUTES) for s in sessions if s['status'] == 'running'),
        default=None,
    )
    return nav, stale_at
