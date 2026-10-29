"""What a ban does to work already started (#389)."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agent import events
from app.auth.middleware import creator_not_banned, require_admin
from app.core.db import get_conn
from app.core.errors_http import api_error

router = APIRouter(prefix='/v1/admin', tags=['admin'], dependencies=[Depends(require_admin)])


class AfterBan(BaseModel):
    cancelled_jobs: int


@router.post('/users/{user_id}/after-ban', response_model=AfterBan)
def after_user_ban(user_id: UUID) -> dict:
    """After an admin bans an account (the web calls it from a Better Auth hook after admin/ban-user): stop the
    user's queued and running jobs as «Ferma» does, so each settles and releases its credit hold. Nothing for a user
    who is not banned. Campaigns, scheduled searches and webhooks need nothing here: they skip a banned creator when
    they act (``creator_not_banned``), so an unban, or the ban's expiry, resumes them."""
    with get_conn() as conn:
        jobs = conn.execute(
            "SELECT j.id FROM jobs j WHERE j.user_id = %s AND j.status IN ('queued', 'running')"
            f' AND NOT {creator_not_banned("j.user_id")}',
            (user_id,),
        ).fetchall()
    if [j for j in jobs if not events.request_cancel(str(j['id']))]:  # every job asked, then the outcome
        raise api_error(503, 'JOB.CANCEL_UNAVAILABLE', 'Try again in a moment')
    return {'cancelled_jobs': len(jobs)}
