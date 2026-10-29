"""Bulk row actions (#834): add the people of chosen rows to a draft campaign, mark them out of target, or rate them
in bulk (the undo of mark-off-target). The bulk routes are scoped to the active project (``X-Project-Id``): a list or
campaign of another project is a 404. The per-row rating route moved here with them and stays organization-scoped."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.auth.middleware import CurrentUser
from app.billing.row_charges import finalize
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.lists.models import ListRow
from app.lists.routes import Auth, _own_list
from app.lists.row_view import read_row
from app.lists.rows import DELIVERED_SQL
from app.outreach.drafts import MAX_TARGETS, enroll_rows
from app.projects.routes import current_project_or_api_key
from app.scheduling.runner import enrollment_context

router = APIRouter(prefix='/v1/lists')

RowIds = Field(min_length=1, max_length=MAX_TARGETS)


class MarkOffTargetIn(BaseModel):
    row_ids: list[UUID] = RowIds


class MarkedOffTarget(BaseModel):
    marked: int
    not_found: int


class RateRowsIn(BaseModel):
    row_ids: list[UUID] = RowIds
    # No default: a body that forgets the rating must not clear it.
    rating: Literal['good', 'bad'] | None


class RatedRows(BaseModel):
    rated: int
    not_found: int


class AddToCampaignIn(BaseModel):
    row_ids: list[UUID] = RowIds
    campaign_id: UUID


class AddedToCampaign(BaseModel):
    added: int
    skipped: dict[str, int]


def _project_list(conn, list_id: UUID, user: CurrentUser, project_id: str) -> dict:
    row = conn.execute(
        'SELECT id, entity, project_id FROM lists WHERE id = %s AND organization_id = %s AND project_id = %s'
        ' AND deleted_at IS NULL',
        (list_id, user.org_id, project_id),
    ).fetchone()
    if not row:
        raise api_error(404, 'LIST.NOT_FOUND', 'list not found')
    return row


class RowRating(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    rating: Literal['good', 'bad'] | None
    note: str | None = Field(default=None, max_length=500)


@router.put('/{list_id}/rows/{row_id}/rating', response_model=ListRow, response_model_exclude_unset=True)
def rate_row(list_id: UUID, row_id: UUID, body: RowRating, user: CurrentUser = Auth) -> dict:
    """Rate a delivered row (null clears it); the agent reads it with list_row_feedback."""
    with get_conn() as conn:
        _own_list(conn, list_id, user.org_id)
        updated = conn.execute(
            'UPDATE list_rows SET rating = %s, rating_note = %s WHERE id = %s AND list_id = %s RETURNING id',
            (body.rating, (body.note or None) if body.rating else None, row_id, list_id),
        ).fetchone()
        if not updated:
            raise api_error(404, 'ROW.NOT_FOUND', 'row not found')
        return read_row(conn, row_id, list_id)


def _rate_rows(
    list_id: UUID, user: CurrentUser, project_id: str, row_ids: list[UUID], rating: str | None
) -> tuple[int, int]:
    """Sets ``rating``; a null clears the note too, as ``PUT .../rating`` does. ``good`` and ``bad`` keep it, and a row
    with no rating has no note, so a bulk rate still undoes a mark-off-target exactly. Answers (rated, not_found)."""
    ids = set(row_ids)
    with get_conn() as conn:
        _project_list(conn, list_id, user, project_id)
        rated = conn.execute(
            'UPDATE list_rows SET rating = %s, rating_note = CASE WHEN %s::text IS NULL THEN NULL ELSE rating_note END'
            ' WHERE list_id = %s AND id = ANY(%s::uuid[])',
            (rating, rating, list_id, list(ids)),
        ).rowcount
    return rated, len(ids) - rated


@router.post('/{list_id}/rows/mark-off-target', response_model=MarkedOffTarget)
def mark_off_target(
    list_id: UUID,
    body: MarkOffTargetIn,
    user: CurrentUser = Auth,
    project_id: str = Depends(current_project_or_api_key),
) -> dict:
    """The rating ``bad`` of ``PUT .../rating``, which exports hide with ``hide_off_target``, for many rows at once."""
    marked, not_found = _rate_rows(list_id, user, project_id, body.row_ids, 'bad')
    return {'marked': marked, 'not_found': not_found}


@router.post('/{list_id}/rows/rate', response_model=RatedRows)
def rate_rows(
    list_id: UUID,
    body: RateRowsIn,
    user: CurrentUser = Auth,
    project_id: str = Depends(current_project_or_api_key),
) -> dict:
    """One rating (null clears it) for many rows; the undo of mark-off-target restores each earlier rating this way."""
    rated, not_found = _rate_rows(list_id, user, project_id, body.row_ids, body.rating)
    return {'rated': rated, 'not_found': not_found}


@router.post('/{list_id}/rows/add-to-campaign', response_model=AddedToCampaign)
def add_to_campaign(
    list_id: UUID,
    body: AddToCampaignIn,
    user: CurrentUser = Auth,
    project_id: str = Depends(current_project_or_api_key),
) -> dict:
    """Enrolls the people of the rows in a draft campaign of the same project, by the rule every enrollment applies
    (``enroll_rows``). ``skipped`` counts the rows left out by reason: ``not_found``, ``not_delivered``, ``excluded``,
    ``duplicate``, ``no_contact``, ``in_other_campaign``, ``held_by_instantly``, ``stopped``, ``over_limit``."""
    ids = set(body.row_ids)
    with get_conn() as conn:
        lst = _project_list(conn, list_id, user, project_id)
        if lst['entity'] != 'person':
            raise api_error(422, 'CAMPAIGN.LIST_NOT_PEOPLE', 'a campaign needs a list of people')
        # Serialises with refill and other enrollments of the project, so they see each other's targets.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        campaign = conn.execute(
            'SELECT * FROM outreach_campaigns WHERE id = %s AND organization_id = %s AND project_id = %s FOR UPDATE',
            (body.campaign_id, user.org_id, project_id),
        ).fetchone()
        if not campaign:
            raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')
        if campaign['status'] != 'draft':
            raise api_error(409, 'CAMPAIGN.NOT_DRAFT', 'only a draft campaign takes more people')
        rows = conn.execute(
            f'SELECT r.id, r.cells, ({DELIVERED_SQL}) AS delivered FROM list_rows r'
            ' WHERE r.list_id = %s AND r.id = ANY(%s::uuid[]) ORDER BY r.relevance_score DESC NULLS LAST, r.seq',
            (list_id, list(ids)),
        ).fetchall()
        have = conn.execute(
            'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id = %s', (campaign['id'],)
        ).fetchone()['n']
        deliverable = [r for r in rows if r['delivered']]
        enrolled, skipped = enroll_rows(
            conn,
            campaign,
            campaign['owner_id'],
            deliverable,
            enrollment_context(conn, campaign),
            room=MAX_TARGETS - have,
        )
        # A pending charge on an enrolled row becomes final (#462), as in a campaign draft.
        finalize(conn, enrolled)
    skipped.update(
        {
            k: v
            for k, v in {'not_found': len(ids) - len(rows), 'not_delivered': len(rows) - len(deliverable)}.items()
            if v
        }
    )
    return {'added': len(enrolled), 'skipped': dict(skipped)}
