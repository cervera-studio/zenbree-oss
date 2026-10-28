"""Home screen: what needs the user's attention, one row per kind (#209). User-scoped; reads other
modules' state through their own queries."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.event_stream import EventStream
from app.core.locale import DEFAULT_LOCALE, Locale
from app.home import live
from app.home.nav import load_nav
from app.home.overview import Overview, awaiting_approval, load_overview, running_jobs
from app.home.stats import Stats, load_stats
from app.home.welcome import Welcome, close_welcome, load_welcome
from app.notifications.service import unread_replies
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/home')


class TodoItem(BaseModel):
    kind: Literal['approvals', 'replies', 'running']
    count: int
    title: str | None
    href: str


class Todo(BaseModel):
    items: list[TodoItem]


@router.get('/todo')
def todo(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> Todo:
    items: list[TodoItem] = []
    with get_conn() as conn:
        campaigns = awaiting_approval(conn, user.org_id, project_id)
        if campaigns:
            items.append(
                TodoItem(
                    kind='approvals',
                    count=sum(c['n'] for c in campaigns),
                    title=campaigns[0]['name'],
                    href=f'/campaigns/{campaigns[0]["id"]}',
                )
            )
        replies, latest = unread_replies(conn, user.id, user.org_id)
        if replies:
            name = str((latest or {}).get('name') or '').strip()
            items.append(TodoItem(kind='replies', count=replies, title=name or None, href='/inbox'))
        running = running_jobs(conn, project_id, user.id)
        if running:
            items.append(
                TodoItem(
                    kind='running',
                    count=len(running),
                    title=running[0]['title'],
                    href=f'/?s={running[0]["session_id"]}',
                )
            )
    return Todo(items=items)


@router.get('/overview')
def overview(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> Overview:
    with get_conn() as conn:
        return load_overview(conn, user.id, user.org_id, project_id).overview


@router.get('/stats')
def stats(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> Stats:
    with get_conn() as conn:
        return load_stats(conn, user.org_id, project_id)


@router.get('/stream', response_class=EventStream)
async def stream(user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)) -> EventStream:
    """SSE of the overview: ``snapshot`` at once, ``overview`` when it changes, ``: ping`` every 15 s. No ids: a
    reconnect gets a fresh ``snapshot``. Ends when the project is gone, and with ``error`` when the caller leaves the
    organization (``EventStream``)."""
    return EventStream(live.frames(user, project_id))


@router.get('/nav/stream', response_class=EventStream)
async def nav_stream(
    user: CurrentUser = Depends(current_user), project_id: str = Depends(current_project)
) -> EventStream:
    """SSE of the sidebar and the phone menu (#342), on the Home stream's wake-ups: ``snapshot`` at once, ``nav``
    when it changes."""
    return EventStream(live.frames(user, project_id, load_nav, live.NAV_EVENT))


@router.get('/welcome')
def welcome(
    locale: Locale = DEFAULT_LOCALE,
    user: CurrentUser = Depends(current_user),
    project_id: str = Depends(current_project),
) -> Welcome | None:
    return load_welcome(user.id, project_id, locale)


@router.post('/welcome/dismiss', status_code=status.HTTP_204_NO_CONTENT)
def welcome_dismiss(project_id: str = Depends(current_project)) -> Response:
    close_welcome(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
