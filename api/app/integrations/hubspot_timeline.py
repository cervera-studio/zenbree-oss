"""What the HubSpot card shows of the external-activity timeline (#932), and the two things its user can do about it.

The ledger (``app.crm.timeline``) owns the rows and the rules; this module is the HTTP face of ``status``,
``retry`` and ``resolve``. Each reads and writes the caller's own rows only, in the caller's scope.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.auth.middleware import CurrentUser, current_user
from app.crm.timeline import ledger
from app.crm.timeline.facts import Code

router = APIRouter(prefix='/timeline')

# The request models list the codes a user can act on, so a code that never needs the user is a 422, not a no-op.
RetryCode = Literal[Code.REFUSED, Code.CONTACT_REFUSED]
ResolveCode = Literal[Code.ANSWER_LOST]


class TimelineIssue(BaseModel):
    code: Code
    state: Literal['waiting', 'skipped', 'failed', 'uncertain']
    count: int
    action: Literal['retry', 'resolve'] | None
    latest_at: datetime


class TimelineStatus(BaseModel):
    logged: int
    pending: int
    issues: list[TimelineIssue]


EMPTY = TimelineStatus(logged=0, pending=0, issues=[])


async def read(user_id: str, project_id: str) -> TimelineStatus:
    """The caller's rows, and the active project's ownerless ones."""
    found = await asyncio.to_thread(ledger.status, user_id, project_id)
    return TimelineStatus.model_validate(asdict(found))


class TimelineRetry(BaseModel):
    code: RetryCode


class TimelineResolve(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: ResolveCode
    as_: Literal['log_again', 'logged'] = Field(alias='as')


class TimelineChanged(BaseModel):
    changed: int


@router.post('/retry', response_model=TimelineChanged)
async def retry(body: TimelineRetry, user: CurrentUser = Depends(current_user)) -> dict:
    """Failed rows of this code go back to pending with their attempts reset. Only the caller's own."""
    return {'changed': await asyncio.to_thread(ledger.retry, user.id, body.code)}


@router.post('/resolve', response_model=TimelineChanged)
async def resolve(body: TimelineResolve, user: CurrentUser = Depends(current_user)) -> dict:
    """Uncertain rows of this code: logged again (the user accepts a duplicate) or taken as logged."""
    return {'changed': await asyncio.to_thread(ledger.resolve, user.id, body.code, body.as_)}
