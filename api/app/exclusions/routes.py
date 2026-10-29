"""Exclusions: list, add (one or a CSV import), delete. Scoped by the active project (#307); user_id is who added one."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.exclusions.rules import Kind, guess_kind, normalise
from app.outreach.replies import UNDO_SECONDS
from app.projects.routes import current_project_or_api_key

router = APIRouter(prefix='/v1/exclusions', tags=['Exclusions'])
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)
MAX_ITEMS = 5000  # per request
MAX_PER_PROJECT = 50_000
INVALID_PREVIEW = 50


Origin = Literal['profile', 'unsubscribed', 'own_domain', 'manual', 'bounced']


class ExclusionIn(BaseModel):
    kind: Kind | None = None  # guessed from the value when absent (CSV import)
    value: str = Field(max_length=1000)


class ExclusionsIn(BaseModel):
    items: list[ExclusionIn] = Field(min_length=1, max_length=MAX_ITEMS)


class Exclusion(BaseModel):
    id: UUID
    kind: Kind
    value: str
    origin: Origin
    created_at: datetime


class Exclusions(BaseModel):
    exclusions: list[Exclusion]


class ExclusionsAdded(BaseModel):
    added: int
    invalid: list[str]
    invalid_count: int


@router.get('', response_model=Exclusions)
def list_exclusions(project_id: str = Project) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, kind, value, origin, created_at FROM exclusions WHERE project_id = %s ORDER BY created_at DESC, value',
            (project_id,),
        ).fetchall()
    return {'exclusions': rows}


@router.post('', response_model=ExclusionsAdded)
def add_exclusions(body: ExclusionsIn, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    """Idempotent: an existing (kind, value) is not added twice. Unparseable values come back in ``invalid``."""
    valid: dict[tuple[str, str], None] = {}
    invalid: list[str] = []
    for item in body.items:
        kind = item.kind or guess_kind(item.value)
        if value := normalise(kind, item.value):
            valid[(kind, value)] = None
        else:
            invalid.append(item.value)
    if not valid:
        raise api_error(422, 'EXCLUSIONS.INVALID_VALUE', 'no valid exclusion to add')
    kinds, values = zip(*valid, strict=True)
    with get_conn() as conn:
        # Serialise one project's imports so the cap below holds under concurrency.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        have = conn.execute('SELECT count(*) AS n FROM exclusions WHERE project_id = %s', (project_id,)).fetchone()['n']
        if have + len(valid) > MAX_PER_PROJECT:
            raise api_error(409, 'EXCLUSIONS.LIMIT', f'at most {MAX_PER_PROJECT} exclusions per project')
        # A rule the profile created, or an unsubscribe link did within the undo window, becomes the user's (origin
        # included), so neither a later profile edit nor the recipient's undo (#988) can delete it. A reply opt-out
        # or an older link row keeps its origin: nothing can undo it.
        rows = conn.execute(
            'INSERT INTO exclusions (project_id, user_id, kind, value, origin)'
            " SELECT %s, %s, k, v, 'manual' FROM unnest(%s::text[], %s::text[]) AS t(k, v)"
            " ON CONFLICT (project_id, kind, value) DO UPDATE SET source = NULL, origin = 'manual'"
            " WHERE exclusions.source IS NOT NULL OR (exclusions.origin = 'unsubscribed'"
            ' AND exclusions.reply_opt_out_at IS NULL AND exclusions.created_at > now() - make_interval(secs => %s))'
            ' RETURNING xmax = 0 AS inserted',
            (project_id, user.id, list(kinds), list(values), UNDO_SECONDS),
        ).fetchall()
        added = sum(r['inserted'] for r in rows)
    return {'added': added, 'invalid': invalid[:INVALID_PREVIEW], 'invalid_count': len(invalid)}


@router.delete('/{exclusion_id}', status_code=204)
def delete_exclusion(exclusion_id: UUID, project_id: str = Project) -> None:
    with get_conn() as conn:
        n = conn.execute(
            'DELETE FROM exclusions WHERE id = %s AND project_id = %s', (exclusion_id, project_id)
        ).rowcount
    if not n:
        raise api_error(404, 'EXCLUSIONS.NOT_FOUND', 'exclusion not found')
