"""Audience identity (#937): what one person or account is known by in the active project, and the explicit way out
of an ambiguous mapping. Scoped by the active project: another project's rows, aliases and conflicts are not found."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.audience import conflicts
from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.projects.routes import current_project_or_api_key

router = APIRouter(prefix='/v1/audience', tags=['Audience'])
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)


class Alias(BaseModel):
    kind: Literal['email', 'linkedin_url', 'domain', 'crm_object', 'external_lead']
    namespace: str
    value: str
    source: str
    observed_at: datetime


class RowIdentity(BaseModel):
    row_id: UUID
    identity_id: UUID | None
    aliases: list[Alias]
    open_conflicts: list[UUID]


class ConflictSide(BaseModel):
    id: UUID
    aliases: list[Alias]
    rows: int


class Evidence(BaseModel):
    """A value that tied the two identities, with the source and time of the alias it clashed with (an ISO time)."""

    identity_id: UUID
    kind: str
    namespace: str
    value: str
    source: str | None
    observed_at: str | None


class Conflict(BaseModel):
    id: UUID
    entity: Literal['person', 'company']
    status: Literal['open', 'merged', 'kept_apart']
    created_at: datetime
    resolved_at: datetime | None
    row_id: UUID | None
    evidence: list[Evidence]
    identities: list[ConflictSide]


class Conflicts(BaseModel):
    conflicts: list[Conflict]
    next_cursor: int | None


class ResolveIn(BaseModel):
    action: Literal['merge', 'keep_apart']
    into: UUID | None = None  # merge: the identity that stays; the older one when absent


class Resolved(BaseModel):
    status: Literal['merged', 'kept_apart']
    identity_id: UUID | None


@router.get('/rows/{row_id}', response_model=RowIdentity)
def row_identity(row_id: UUID, project_id: str = Project) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT w.id, k.identity_id FROM list_rows w JOIN lists l ON l.id = w.list_id'
            ' AND l.organization_id = w.organization_id LEFT JOIN audience_row_links k ON k.list_row_id = w.id'
            ' WHERE w.id = %s AND l.project_id = %s',
            (row_id, project_id),
        ).fetchone()
        if not row:
            raise api_error(404, 'AUDIENCE.ROW_NOT_FOUND', 'row not found')
        aliases = (
            conn.execute(
                'SELECT kind, namespace, value, source, observed_at FROM audience_aliases'
                ' WHERE identity_id = %s ORDER BY observed_at, id',
                (row['identity_id'],),
            ).fetchall()
            if row['identity_id']
            else []
        )
        open_ids = conn.execute(
            "SELECT id FROM audience_conflicts WHERE project_id = %s AND status = 'open'"
            ' AND (list_row_id = %s OR identity_a = %s OR identity_b = %s) ORDER BY created_at, id',
            (project_id, row_id, row['identity_id'], row['identity_id']),
        ).fetchall()
    return {
        'row_id': row_id,
        'identity_id': row['identity_id'],
        'aliases': aliases,
        'open_conflicts': [c['id'] for c in open_ids],
    }


@router.get('/conflicts', response_model=Conflicts)
def list_conflicts(
    status: Literal['open', 'merged', 'kept_apart'] = 'open',
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    project_id: str = Project,
) -> dict:
    with get_conn() as conn:
        found = conflicts.list_conflicts(conn, project_id, status, cursor, limit)
    keys = ('id', 'entity', 'status', 'created_at', 'resolved_at', 'identities')
    return {
        'conflicts': [
            {**{k: c[k] for k in keys}, 'row_id': c['list_row_id'], 'evidence': _evidence(c['evidence'])} for c in found
        ],
        'next_cursor': found[-1]['seq'] if len(found) == limit else None,
    }


def _evidence(items: list[dict]) -> list[dict]:
    keys = ('identity_id', 'kind', 'value', 'source', 'observed_at')
    return [{**{k: i.get(k) for k in keys}, 'namespace': i.get('namespace') or ''} for i in items]


@router.post('/conflicts/{conflict_id}/resolve', response_model=Resolved)
def resolve_conflict(conflict_id: UUID, body: ResolveIn, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    with get_conn() as conn:
        return conflicts.resolve(conn, conflict_id, project_id, user.id, body.action, body.into)
