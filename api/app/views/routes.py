"""Saved views (#827): a name for the tab, the search and the fit floor of one list. Scoped by the active project."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints

from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.projects.routes import current_project_or_api_key

router = APIRouter(prefix='/v1/saved-views', tags=['Saved views'])
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)
MAX_PER_PROJECT = 200

Tab = Literal['all', 'delivered', 'pending', 'no_contact', 'excluded']
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SavedViewIn(BaseModel):
    list_id: UUID
    name: Name
    tab: Tab = 'all'
    query: str = Field(default='', max_length=200)
    min_fit: int | None = Field(default=None, ge=0, le=100, strict=True)


class SavedViewRename(BaseModel):
    name: Name


class SavedView(BaseModel):
    id: UUID
    name: str
    list_id: UUID
    list_name: str
    tab: Tab
    query: str
    min_fit: int | None
    created_at: datetime


class SavedViews(BaseModel):
    views: list[SavedView]


# A view of a deleted list is hidden here and in the sidebar count (api/app/home/nav.py).
_SELECT = (
    'SELECT v.id, v.name, v.list_id, l.name AS list_name, v.tab, v.query, v.min_fit, v.created_at FROM saved_views v'
    ' JOIN lists l ON l.id = v.list_id AND l.organization_id = v.organization_id AND l.deleted_at IS NULL'
)


def _one(conn, view_id: UUID, project_id: str) -> dict:
    row = conn.execute(f'{_SELECT} WHERE v.id = %s AND v.project_id = %s', (view_id, project_id)).fetchone()
    if not row:
        raise api_error(404, 'SAVED_VIEW.NOT_FOUND', 'saved view not found')
    return row


@router.get('', response_model=SavedViews)
def list_views(project_id: str = Project) -> dict:
    with get_conn() as conn:
        rows = conn.execute(f'{_SELECT} WHERE v.project_id = %s ORDER BY v.created_at DESC', (project_id,)).fetchall()
    return {'views': rows}


@router.post('', response_model=SavedView, status_code=201)
def create_view(body: SavedViewIn, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    with get_conn() as conn:
        # Serialise one project's saves so the cap below holds under concurrency.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        if not conn.execute(
            'SELECT 1 FROM lists WHERE id = %s AND project_id = %s AND organization_id = %s AND deleted_at IS NULL',
            (body.list_id, project_id, user.org_id),
        ).fetchone():
            raise api_error(404, 'LIST.NOT_FOUND', 'list not found')
        have = conn.execute(
            f'SELECT count(*) AS n FROM ({_SELECT} WHERE v.project_id = %s) v', (project_id,)
        ).fetchone()['n']
        if have >= MAX_PER_PROJECT:
            raise api_error(409, 'SAVED_VIEW.LIMIT', f'at most {MAX_PER_PROJECT} saved views per project')
        new = conn.execute(
            'INSERT INTO saved_views (organization_id, project_id, list_id, name, tab, query, min_fit)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
            (user.org_id, project_id, body.list_id, body.name, body.tab, body.query, body.min_fit),
        ).fetchone()
        return _one(conn, new['id'], project_id)


@router.patch('/{view_id}', response_model=SavedView)
def rename_view(view_id: UUID, body: SavedViewRename, project_id: str = Project) -> dict:
    with get_conn() as conn:
        _one(conn, view_id, project_id)
        conn.execute('UPDATE saved_views SET name = %s WHERE id = %s', (body.name, view_id))
        return _one(conn, view_id, project_id)


@router.delete('/{view_id}', status_code=204)
def delete_view(view_id: UUID, project_id: str = Project) -> None:
    with get_conn() as conn:
        n = conn.execute('DELETE FROM saved_views WHERE id = %s AND project_id = %s', (view_id, project_id)).rowcount
    if not n:
        raise api_error(404, 'SAVED_VIEW.NOT_FOUND', 'saved view not found')
