"""Projects: an organization's clients (#307). A project holds its chats, lists, campaigns and inbox, plus the
client's business profile, exclusions and senders. Scoped by organization_id; user_id is who created one.

The web names the active project in the ``X-Project-Id`` header (``current_project``); a caller that names none,
or one outside its organization, gets the organization's default project."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.agent.language import user_locale
from app.auth.middleware import CurrentUser, current_user, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.locale import Locale
from app.integrations import project_senders

router = APIRouter(prefix='/v1/projects')
Auth = Depends(current_user_or_api_key)
# The name is data once written: the user renames it, and it does not follow a later change of language.
DEFAULT_NAMES: dict[Locale, str] = {
    'en': 'My client',
    'it': 'Il mio cliente',
    'es': 'Mi cliente',
    'fr': 'Mon client',
}
# The monthly cap (#307) and what the project spent this month: `soft` only reports it, `hard` also stops holds.
_COLS = (
    'id, name, created_at, credit_cap, credit_cap_mode, project_month_spend(id) AS credits_this_month,'
    ' coalesce(project_month_spend(id) >= credit_cap, false) AS over_cap'
)


def _not_found() -> HTTPException:
    return api_error(404, 'PROJECT.NOT_FOUND', 'project not found')


def default_project(conn, user: CurrentUser) -> str:
    """The organization's oldest project, created on first use (no signup hook) and named in the language of the
    user who first needs it. The organization row lock makes
    concurrent first requests (a page loads several at once) create one, not one each."""
    sql = 'SELECT id FROM projects WHERE organization_id = %s ORDER BY created_at LIMIT 1'
    if row := conn.execute(sql, (user.org_id,)).fetchone():
        return str(row['id'])
    conn.execute('SELECT 1 FROM organization WHERE id = %s FOR NO KEY UPDATE', (user.org_id,))
    if row := conn.execute(sql, (user.org_id,)).fetchone():
        return str(row['id'])
    return str(
        conn.execute(
            'INSERT INTO projects (organization_id, user_id, name, name_is_placeholder) VALUES (%s, %s, %s, true)'
            ' RETURNING id',
            (user.org_id, user.id, DEFAULT_NAMES[user_locale(conn, user.id)]),
        ).fetchone()['id']
    )


def own_project(conn, project_id: UUID | str, org_id: str) -> str:
    if not conn.execute(
        'SELECT 1 FROM projects WHERE id = %s AND organization_id = %s', (project_id, org_id)
    ).fetchone():
        raise _not_found()
    return str(project_id)


def _active(user: CurrentUser, header: str | None) -> str:
    with get_conn() as conn:
        try:
            wanted = UUID(header) if header else None
        except ValueError:
            wanted = None
        row = (
            wanted
            and conn.execute(
                'SELECT id FROM projects WHERE id = %s AND organization_id = %s', (wanted, user.org_id)
            ).fetchone()
        )
        return str(row['id']) if row else default_project(conn, user)


ProjectHeader = Header(default=None, alias='X-Project-Id', max_length=64)


def current_project(user: CurrentUser = Depends(current_user), x_project_id: str | None = ProjectHeader) -> str:
    """The active project of a JWT caller."""
    return _active(user, x_project_id)


def current_project_or_api_key(
    user: CurrentUser = Depends(current_user_or_api_key), x_project_id: str | None = ProjectHeader
) -> str:
    """The active project of a JWT or API-key caller."""
    return _active(user, x_project_id)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class Project(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    credit_cap: int | None
    credit_cap_mode: Literal['hard', 'soft'] | None
    credits_this_month: int
    over_cap: bool


class Projects(BaseModel):
    projects: list[Project]


class ProjectResponse(BaseModel):
    project: Project


class ProjectDeleted(BaseModel):
    status: Literal['ok']


@router.get('', response_model=Projects)
def list_projects(user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        default_project(conn, user)
        rows = conn.execute(
            f'SELECT {_COLS} FROM projects WHERE organization_id = %s ORDER BY created_at', (user.org_id,)
        ).fetchall()
    return {'projects': rows}


@router.post('', response_model=ProjectResponse)
def create_project(body: ProjectIn, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            f'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING {_COLS}',
            (user.org_id, user.id, body.name.strip()),
        ).fetchone()
    return {'project': row}


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Both together; both null removes the cap.
    credit_cap: int | None = Field(default=None, gt=0)
    credit_cap_mode: Literal['hard', 'soft'] | None = None

    @model_validator(mode='after')
    def _cap_pair(self) -> ProjectPatch:
        if (self.credit_cap is None) != (self.credit_cap_mode is None):
            raise ValueError('credit_cap and credit_cap_mode go together')
        return self


@router.patch('/{project_id}', response_model=ProjectResponse)
def update_project(project_id: UUID, body: ProjectPatch, user: CurrentUser = Auth) -> dict:
    """Rename, and set or remove the monthly credit cap: the cap is the organization's money, so admins only
    (403 ``AUTH.ORG_ADMIN_ONLY``)."""
    cap = body.model_fields_set & {'credit_cap', 'credit_cap_mode'}
    if cap and user.org_role != 'admin':
        raise api_error(403, 'AUTH.ORG_ADMIN_ONLY', 'Only an admin of the organization can do this')
    with get_conn() as conn:
        row = conn.execute(
            'UPDATE projects SET name = coalesce(%s, name), name_is_placeholder = name_is_placeholder AND %s::text IS NULL,'
            ' credit_cap = CASE WHEN %s THEN %s ELSE credit_cap END,'
            ' credit_cap_mode = CASE WHEN %s THEN %s ELSE credit_cap_mode END'
            f' WHERE id = %s AND organization_id = %s RETURNING {_COLS}',
            (
                body.name.strip() if body.name else None,
                body.name.strip() if body.name else None,
                bool(cap),
                body.credit_cap,
                bool(cap),
                body.credit_cap_mode,
                project_id,
                user.org_id,
            ),
        ).fetchone()
    if not row:
        raise _not_found()
    return {'project': row}


@router.delete('/{project_id}', response_model=ProjectDeleted)
def delete_project(project_id: UUID, user: CurrentUser = Auth) -> dict:
    """Deletes the project's chats, lists, campaigns, inbox, profile and exclusions too (FK cascade), and unlinks its
    senders, which stay with the organization. The last project stays, and so does one using a colleague's live
    sender unless the caller is an admin: unlinking it is for its connector or an admin (409 ``PROJECT.HAS_SENDERS``)."""
    with get_conn() as conn:
        own_project(conn, project_id, user.org_id)
        if not project_senders.may_unlink_all(conn, project_id, user):
            raise api_error(409, 'PROJECT.HAS_SENDERS', "it sends from a colleague's account: ask them or an admin")
        if (
            conn.execute('SELECT count(*) AS n FROM projects WHERE organization_id = %s', (user.org_id,)).fetchone()[
                'n'
            ]
            <= 1
        ):
            raise api_error(409, 'PROJECT.LAST', 'cannot delete the last project')
        conn.execute('DELETE FROM projects WHERE id = %s', (project_id,))
    return {'status': 'ok'}
