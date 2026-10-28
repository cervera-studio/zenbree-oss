from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.outreach.drafts import drop_blocked_drafts
from app.outreach.fields import FieldCatalog, catalog
from app.outreach.freeze import WAITING_FOR_A_VALUE, drop_unsent, freeze
from app.outreach.routes import _own

router = APIRouter(prefix='/v1/campaigns')
Auth = Depends(current_user)

_REGENERABLE = f"""
SELECT id, status FROM outreach_campaign_targets
 WHERE campaign_id = %s AND (status IN ('active', 'paused') OR ({WAITING_FOR_A_VALUE}))"""


class Regenerated(BaseModel):
    active: int
    renewed: int
    waiting: int
    kept: int


@router.get('/{campaign_id}/fields', response_model=FieldCatalog)
def campaign_fields(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        return catalog(conn, _own(conn, campaign_id, user.org_id))


@router.post('/{campaign_id}/targets/regenerate', response_model=Regenerated)
def regenerate(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    """Freeze each person's values again from the current cells. Claims no account has taken yet are deleted, and the
    dispatcher plans those steps again from the new values.

    A claim in ``sending`` stays. If the provider rate-limits it, the dispatcher gives it back as ``approved`` with the
    copy planned before the regenerate, and the next beat sends that copy: the user approved it."""
    with get_conn() as conn, conn.transaction():
        project_id = _own(conn, campaign_id, user.org_id)['project_id']
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        # NO KEY UPDATE, as routes.undo_reject: a claim that skips a step holds the person and then inserts a send,
        # whose foreign key takes KEY SHARE on the campaign.
        c = conn.execute('SELECT * FROM outreach_campaigns WHERE id = %s FOR NO KEY UPDATE', (campaign_id,)).fetchone()
        if c['status'] not in ('active', 'paused'):
            raise api_error(409, 'CAMPAIGN.INVALID_TRANSITION', f'a {c["status"]} campaign has no frozen values')
        people = conn.execute(_REGENERABLE, (campaign_id,)).fetchall()
        waiting = {p['id'] for p in people if p['status'] == 'draft'}
        kept = set(drop_blocked_drafts(conn, c, only=waiting)) if waiting else set()
        frozen = freeze(conn, c, [p['id'] for p in people if p['id'] not in waiting or p['id'] in kept])
        if frozen.renewed:
            drop_unsent(conn, frozen.renewed)
        return {
            'active': len(frozen.active),
            'renewed': len(frozen.renewed),
            'waiting': len(frozen.waiting),
            'kept': len(frozen.kept),
        }
