from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.admin.settings import is_disabled
from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.integrations import instantly_enroll, instantly_store
from app.outreach import stop_outbox
from app.outreach.stop_outbox import ConfirmedByHand, Retry, StopReason, StopStatus
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/integrations/instantly/stops', tags=['Instantly'])
Project = Depends(current_project)
LIMIT = 200


class StopOut(BaseModel):
    id: UUID
    email: str
    reason: StopReason
    status: StopStatus
    attempts: int
    error_code: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    confirmed_at: datetime | None
    campaign_url: str


class StopsOut(BaseModel):
    items: list[StopOut]
    pending: int
    failed: int
    paused: bool
    may_confirm: bool


def _may_confirm(connection: dict | None, user: CurrentUser) -> bool:
    # Not may_manage: that opens a disconnected connection to every member, so that one can connect it again.
    connected_by = connection and connection['connected_by']
    return user.org_role == 'admin' or (connected_by is not None and str(connected_by) == user.id)


def _out(row: dict) -> dict:
    campaign_id = row.pop('campaign_id')
    return {**row, 'campaign_url': instantly_enroll.campaign_url(campaign_id)}


@router.get('', response_model=StopsOut)
async def list_stops(user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    """The stops Instantly has not confirmed, failed first, then the oldest (at most 200), and their counts."""

    def read() -> dict:
        with get_conn() as conn:
            found = stop_outbox.open_stops(conn, project_id, LIMIT)
            may_confirm = _may_confirm(instantly_store.row(conn, project_id), user)
        return {**found, 'items': [_out(r) for r in found['items']], 'may_confirm': may_confirm}

    found = await asyncio.to_thread(read)
    return {**found, 'paused': await asyncio.to_thread(is_disabled, 'vendor_disabled:instantly')}


def _move(project_id: str, stop_id: UUID, event: stop_outbox.Event, check=None) -> dict:
    with get_conn() as conn:
        stop = stop_outbox.find(conn, project_id, str(stop_id))
        if stop is None:
            raise api_error(404, 'INSTANTLY.STOP_NOT_FOUND', 'stop not found')
        if stop.status is not StopStatus.FAILED:
            raise api_error(409, 'INSTANTLY.STOP_NOT_FAILED', 'only a failed stop can be retried or confirmed')
        if check:
            check(instantly_store.row(conn, project_id))
        if stop_outbox.settle(conn, stop, event, datetime.now(UTC)) is None:
            raise api_error(409, 'INSTANTLY.STOP_NOT_FAILED', 'only a failed stop can be retried or confirmed')
        return _out(stop_outbox.view(conn, project_id, str(stop_id)))


def _require_connected(connection: dict | None) -> None:
    if connection is None or connection['status'] != 'connected':
        raise api_error(409, 'INSTANTLY.NOT_CONNECTED', 'reconnect Instantly to retry the stop')


@router.post('/{stop_id}/retry', response_model=StopOut)
async def retry_stop(stop_id: UUID, user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    """A failed stop is attempted again from the start, at the next pass."""
    return await asyncio.to_thread(_move, project_id, stop_id, Retry(), _require_connected)


@router.post('/{stop_id}/confirm', response_model=StopOut)
async def confirm_stop(stop_id: UUID, user: CurrentUser = Depends(current_user), project_id: str = Project) -> dict:
    """The user removed the person in Instantly: a failed stop ends confirmed by them."""

    def allowed(connection: dict | None) -> None:
        if not _may_confirm(connection, user):
            raise api_error(403, 'INSTANTLY.NOT_ALLOWED', 'only who connected Instantly, or an admin, can confirm it')

    return await asyncio.to_thread(_move, project_id, stop_id, ConfirmedByHand(user.id), allowed)
