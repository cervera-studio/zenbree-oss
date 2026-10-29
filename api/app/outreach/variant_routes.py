"""How the people who join an A/B test split between its copies (#934).

``b_percent`` changes only in the draft: a share changed after launch would fill the gap first and confound the
comparison. A live campaign can only pause or resume the allocation; people who already joined keep their copy.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.outreach.campaign_models import Campaign
from app.outreach.routes import _detail, _own

router = APIRouter(prefix='/v1/campaigns')
Auth = Depends(current_user)


class SharePercent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    b_percent: int = Field(ge=10, le=90, multiple_of=10)


class PauseAllocation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    paused: bool


@router.put('/{campaign_id}/allocation', response_model=Campaign, response_model_exclude_unset=True)
def allocation(
    campaign_id: UUID, body: Annotated[SharePercent | PauseAllocation, Body()], user: CurrentUser = Auth
) -> dict:
    """``{"b_percent": n}`` in a draft; ``{"paused": true|false}`` while it runs."""
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id, lock=True)
        if isinstance(body, SharePercent):
            if c['status'] != 'draft':
                raise api_error(409, 'CAMPAIGN.NOT_DRAFT', 'the share of copy B can only change in a draft')
            conn.execute('UPDATE outreach_campaigns SET b_percent = %s WHERE id = %s', (body.b_percent, campaign_id))
        else:
            if c['status'] not in ('active', 'paused'):
                raise api_error(
                    409, 'CAMPAIGN.INVALID_TRANSITION', f'a {c["status"]} campaign has no allocation to pause'
                )
            conn.execute(
                'UPDATE outreach_campaigns SET allocation_paused_at = CASE WHEN %s'
                ' THEN coalesce(allocation_paused_at, now()) END WHERE id = %s',
                (body.paused, campaign_id),
            )
        return _detail(conn, campaign_id, user.org_id)
