"""Chat (the earlier implementation ``api-routes.ts`` ``chat.*`` paths under /v1) and jobs.

POST /v1/chat stores the user message, queues a job and streams its events;
GET /v1/chat/sessions/{id}/stream re-attaches (``Last-Event-ID`` replays).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.agent import event_payloads, events, vendors
from app.agent.chat_message import ChatMessage
from app.agent.chat_request import ChatRequest
from app.agent.jobs import FAIL_STALE_JOBS_SQL
from app.agent.session_list import session_list
from app.agent.stage_models import RunStage
from app.agent.turn_start import open_turn
from app.auth.middleware import CurrentUser, current_user_or_api_key
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.event_stream import EventStream
from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.lists.rows import fill_credits_sql
from app.onboarding.first_list import FirstList
from app.projects.routes import current_project_or_api_key

router = APIRouter(prefix='/v1')
Auth = Depends(current_user_or_api_key)
Project = Depends(current_project_or_api_key)
TERMINAL = ('needs_input', 'succeeded', 'failed', 'cancelled')


def _org_job(conn: psycopg.Connection[dict], user: CurrentUser, job_id: UUID) -> dict | None:
    """A job of the organization (#323): runs are shared like their chats. One whose chat was deleted stays its starter's."""
    return conn.execute(
        'SELECT * FROM jobs WHERE id = %s AND organization_id = %s AND (chat_session_id IS NOT NULL OR user_id = %s)',
        (job_id, user.org_id, user.id),
    ).fetchone()


class RunAccepted(BaseModel):
    id: UUID
    chat_session_id: UUID


class RunProgress(BaseModel):
    done: int
    total: int


class RunCredits(BaseModel):
    reserved: int
    spent: int


class RunStatus(BaseModel):
    id: UUID
    chat_session_id: UUID | None
    status: Literal['queued', 'running', 'needs_input', 'succeeded', 'failed', 'cancelled']
    phase: str | None
    progress: RunProgress
    list_id: UUID | None
    credits: RunCredits
    stages: list[RunStage] | None = None  # the approved plan's stages, when the run executes one
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class ChatSession(BaseModel):
    id: UUID
    project_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    pinned_at: datetime | None


class ChatSessionItem(ChatSession):
    """A row of the session list (#342): the sidebar's status dot and meta line."""

    status: Literal['asking', 'plan', 'running', 'done', 'error'] | None
    progress_done: int
    progress_total: int
    list_count: int
    people: int
    companies: int


class ChatSessions(BaseModel):
    sessions: list[ChatSessionItem]


class SessionList(BaseModel):
    id: UUID
    name: str
    entity: Literal['company', 'person']
    created_at: datetime
    row_count: int


class ActiveJob(BaseModel):
    id: UUID
    status: Literal['queued', 'running', 'needs_input', 'succeeded', 'failed', 'cancelled']
    stages: list[RunStage] | None
    phase: str | None
    credits_spent: int
    list_id: UUID | None
    retry_stage: int | None


class ChatSessionDetail(BaseModel):
    session: ChatSession
    messages: list[ChatMessage]
    lists: list[SessionList]
    active_job: ActiveJob | None
    fill_credits: int


class Ok(BaseModel):
    status: Literal['ok']


def _start_turn(
    user: CurrentUser, req: ChatRequest, active: str | None = None, *, opening: FirstList | None = None
) -> tuple[str, str]:
    """``open_turn``, once more after a 409 that a dead job of the user in another organization caused. The sweep
    runs here, after ``open_turn``'s connection closed: a scope cannot change while one is open."""
    try:
        return open_turn(user, req, active, opening=opening)
    except HTTPException as e:
        job_running = isinstance(e.detail, dict) and e.detail.get('code') == 'CHAT.JOB_RUNNING'
        if not (job_running and _fail_stale_jobs(user.id)):
            raise
    return open_turn(user, req, active, opening=opening)


def _fail_stale_jobs(user_id: str) -> int:
    """The user's dead jobs in every organization: ``uq_jobs_one_active_per_user`` spans them, and the turn's own
    sweep sees only its organization's. Run when the user starts a turn, as on main, never on a timer: a turn that
    waits in a worker backlog still runs when a worker takes it."""
    with system(SystemReason.STALE_JOBS), get_conn() as conn:
        return conn.execute(f'{FAIL_STALE_JOBS_SQL} AND user_id = %s', (user_id,)).rowcount


def _enqueue(job_id: str) -> None:
    from app.worker import run_job

    try:
        run_job.delay(job_id)
    except Exception as e:
        with get_conn() as conn:
            conn.execute("UPDATE jobs SET status = 'failed', error = 'enqueue failed' WHERE id = %s", (job_id,))
        raise api_error(503, 'CHAT.QUEUE_UNAVAILABLE', 'Try again in a moment') from e


def _drop_unqueued_chat(session_id: str) -> None:
    """The chat a turn opened, when the queue refused that turn (503): nothing ran, so a retry opens one chat and the
    chat list shows no failed one beside it. The job stays ``failed``, with no chat."""
    with get_conn() as conn:
        conn.execute('DELETE FROM chat_sessions WHERE id = %s', (session_id,))


def _job_status(job_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute('SELECT status FROM jobs WHERE id = %s', (job_id,)).fetchone()
    return row['status'] if row else None


async def _sse(job_id: str, session_id: str, after: int):
    yield events.frame('session', {'session_id': session_id, 'job_id': job_id})
    async for item in events.tail(job_id, after):
        if item is None:
            # idle: a finished job with nothing left to send means the worker died before `done`
            if await asyncio.to_thread(_job_status, job_id) in TERMINAL:
                return
            yield ': ping\n\n'
            continue
        seq, event, data = item
        yield f'event: {event}\nid: {seq}\ndata: {data}\n\n'


def _stream(job_id: str, session_id: str, after: int = 0) -> EventStream:
    # #323: the web's chat store ends a turn on `done`, so a member who left gets one after the `error`.
    done = events.frame('done', {'sources': [], 'list_id': None, 'job_id': job_id})
    return EventStream(_sse(job_id, session_id, after), closing_frames=(done,))


@router.post('/chat', response_class=EventStream, responses=event_payloads.CHAT_FRAMES)
async def send(req: ChatRequest, user: CurrentUser = Auth, active: str = Project) -> EventStream:
    sid, job_id = await asyncio.to_thread(_start_turn, user, req, active)
    try:
        await asyncio.to_thread(_enqueue, job_id)
    except HTTPException:
        if not req.session_id:
            await asyncio.to_thread(_drop_unqueued_chat, sid)
        raise
    return _stream(job_id, sid)


@router.post(
    '/runs',
    status_code=202,
    response_model=RunAccepted,
    tags=['Runs'],
    responses={
        401: {'description': 'Missing or invalid bearer JWT/API key'},
        404: {'description': 'Session or project not found for this user'},
        409: {'description': 'Another job is queued or running for this user or on this chat'},
        422: {'description': 'Invalid body, or `CHAT.SEGMENT_GONE`: no stored segment has that key'},
        503: {'description': 'Queue unavailable; the job is marked failed'},
    },
)
def start_run(req: ChatRequest, user: CurrentUser = Auth, active: str = Project) -> dict:
    """Start the existing agent in the background, without opening an SSE stream.

    Poll GET /v1/runs/{id}; read its list_id with the existing /v1/lists endpoints.
    Credits and vendor protections are the same as for chat.
    """
    sid, job_id = _start_turn(user, req, active)
    try:
        _enqueue(job_id)
    except HTTPException:
        if not req.session_id:
            _drop_unqueued_chat(sid)
        raise
    return {'id': job_id, 'chat_session_id': sid}


class CancelAccepted(BaseModel):
    status: Literal['cancelling', 'needs_input', 'succeeded', 'failed', 'cancelled']


@router.post('/jobs/{job_id}/cancel', response_model=CancelAccepted)
@router.post(
    '/runs/{job_id}/cancel',
    response_model=CancelAccepted,
    tags=['Runs'],
    summary='Stop a run',
    responses={
        401: {'description': 'Missing or invalid bearer JWT/API key'},
        403: {'description': 'A member stopping a run somebody else started (JOB.NOT_OWNER)'},
        404: {'description': 'Run not found in this organization'},
        503: {'description': 'The stop request could not be queued; try again'},
    },
)
def cancel_job(job_id: UUID, user: CurrentUser = Auth) -> dict:
    """Ask a queued or running job to stop. The worker ends it at its next safe point (a running tool
    finishes first), settles its credits and closes the stream with `done`; the job ends `cancelled`.
    A finished job is left alone: its status comes back. Whoever started the run, or an admin, stops it."""
    with get_conn() as conn:
        j = _org_job(conn, user, job_id)
    if not j:
        raise api_error(404, 'JOB.NOT_FOUND', 'job not found')
    if str(j['user_id']) != user.id and user.org_role != 'admin':
        raise api_error(403, 'JOB.NOT_OWNER', 'Only who started the run, or an admin')
    if j['status'] in TERMINAL:
        return {'status': j['status']}
    if not events.request_cancel(str(job_id)):
        raise api_error(503, 'JOB.CANCEL_UNAVAILABLE', 'Try again in a moment')
    return {'status': 'cancelling'}


@router.get('/chat/sessions/{session_id}/stream', response_class=EventStream)
def reattach(
    session_id: UUID, user: CurrentUser = Auth, last_event_id: str | None = Header(default=None)
) -> EventStream:
    with get_conn() as conn:
        job = conn.execute(
            'SELECT j.id FROM jobs j JOIN chat_sessions s ON s.id = j.chat_session_id'
            " WHERE s.id = %s AND s.organization_id = %s AND j.status IN ('queued', 'running')"
            ' ORDER BY j.created_at DESC LIMIT 1',
            (session_id, user.org_id),
        ).fetchone()
    if not job:
        raise api_error(404, 'RUN.NOT_FOUND', 'run not found')
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return _stream(str(job['id']), str(session_id), after)


_SESSION_COLS = 'id, project_id, title, created_at, updated_at, pinned_at'


@router.get('/chat/sessions', response_model=ChatSessions)
def list_sessions(user: CurrentUser = Auth, project_id: UUID | None = None) -> dict:
    with get_conn() as conn:
        rows = session_list(conn, user.org_id, project_id)
    return {'sessions': [{k: v for k, v in r.items() if k != 'job_updated_at'} for r in rows]}


@router.get('/chat/sessions/{session_id}', response_model=ChatSessionDetail, response_model_exclude_unset=True)
def get_session(session_id: UUID, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        session = conn.execute(
            f'SELECT {_SESSION_COLS} FROM chat_sessions WHERE id = %s AND organization_id = %s',
            (session_id, user.org_id),
        ).fetchone()
        if not session:
            raise api_error(404, 'SESSION.NOT_FOUND', 'session not found')
        messages = vendors.user_messages(conn, session_id)
        lists = conn.execute(
            'SELECT l.id, l.name, l.entity, l.created_at,'
            ' (SELECT count(*) FROM list_rows r WHERE r.list_id = l.id) AS row_count'
            ' FROM lists l WHERE l.chat_session_id = %s AND l.organization_id = %s AND l.deleted_at IS NULL'
            ' ORDER BY l.created_at',
            (session_id, user.org_id),
        ).fetchall()
        job = conn.execute(
            'SELECT id, status, stages, phase, credits_spent, list_id, CASE WHEN retry_of IS NOT NULL THEN retry_stage END AS retry_stage'
            ' FROM jobs WHERE chat_session_id = %s'
            ' ORDER BY created_at DESC LIMIT 1',
            (session_id,),
        ).fetchone()
        fills = conn.execute(
            f'SELECT {fill_credits_sql("fl.chat_session_id = %s AND fl.organization_id = %s")} AS n',
            (session_id, user.org_id),
        ).fetchone()['n']
    return {'session': session, 'messages': messages, 'lists': lists, 'active_job': job, 'fill_credits': fills}


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None


@router.patch('/chat/sessions/{session_id}', response_model=Ok)
def update_session(session_id: UUID, body: SessionPatch, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        n = conn.execute(
            'UPDATE chat_sessions SET title = coalesce(%s, title),'
            ' pinned_at = CASE WHEN %s::boolean IS NULL THEN pinned_at WHEN %s THEN coalesce(pinned_at, now()) END,'
            ' updated_at = now() WHERE id = %s AND organization_id = %s',
            (body.title, body.pinned, body.pinned, session_id, user.org_id),
        ).rowcount
    if not n:
        raise api_error(404, 'SESSION.NOT_FOUND', 'session not found')
    return {'status': 'ok'}


@router.get('/jobs/{job_id}', response_model=RunStatus, response_model_exclude_unset=True)
@router.get(
    '/runs/{job_id}',
    response_model=RunStatus,
    tags=['Runs'],
    summary='Get run status',
    responses={
        401: {'description': 'Missing or invalid bearer JWT/API key'},
        404: {'description': 'Run not found in this organization'},
    },
)
def get_job(job_id: UUID, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        j = _org_job(conn, user, job_id)
    if not j:
        raise api_error(404, 'JOB.NOT_FOUND', 'job not found')
    return {
        'id': j['id'],
        'status': j['status'],
        'phase': j['phase'],
        'progress': {'done': j['progress_done'], 'total': j['progress_total']},
        'list_id': j['list_id'],
        'credits': {'reserved': j['credits_reserved'], 'spent': j['credits_spent']},
        'stages': j['stages'],
        'error': j['error'],
        'chat_session_id': j['chat_session_id'],
        'created_at': j['created_at'],
        'finished_at': j['finished_at'],
    }
