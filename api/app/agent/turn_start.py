"""Opening a chat turn: the chat session, the job row and the user's message, in one transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
from psycopg.types.json import Jsonb

from app.agent import plan as plans
from app.agent import retry
from app.agent.jobs import FAIL_STALE_JOBS_SQL
from app.agent.language import user_locale
from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.onboarding.first_list import FirstList, stored_first_list
from app.projects.routes import default_project, own_project

if TYPE_CHECKING:
    from app.agent.chat_request import ChatRequest


TITLE_MAX = 80


def chat_title(text: str) -> str:
    """A chat or list name from a message: its first line, cut at a word so a number is never shown short.
    ``web/lib/chat/chat-view.ts`` cuts a title the same way."""
    line = ' '.join(next((ln for ln in text.splitlines() if ln.strip()), '').split())
    if len(line) <= TITLE_MAX:
        return line
    word = line.rfind(' ', 0, TITLE_MAX)
    return line[: word if word >= TITLE_MAX // 2 else TITLE_MAX - 1] + '…'


def _picked_search(conn: psycopg.Connection[dict], session_id: str, index: int) -> dict | None:
    """``{tool, args}``: the search the funnel alternative at ``index`` runs."""
    last = conn.execute(
        'SELECT role, question FROM chat_messages WHERE session_id = %s ORDER BY created_at DESC LIMIT 1',
        (session_id,),
    ).fetchone()
    options = (last['question'] or {}).get('options', []) if last and last['role'] == 'assistant' else []
    option = options[index] if index < len(options) else {}
    filters = option.get('filters')
    return {'tool': option.get('tool', 'search_companies'), 'args': filters} if filters else None


def open_turn(
    user: CurrentUser, req: ChatRequest, active: str | None = None, *, opening: FirstList | None = None
) -> tuple[str, str]:
    """``active``: the caller's active project (#307), where a new chat goes when the body names none. Raises the
    409 ``CHAT.JOB_RUNNING`` while a job of the user is live: ``routes._start_turn`` handles the dead ones.

    A first list (``opening`` from onboarding, ``req.segment`` from a Home card) shows its one-line message, takes
    the segment's name as title and stores its brief in ``chat_sessions.brief`` with the session, so a failed write
    leaves no job."""
    user_id = user.id
    message = req.message
    with get_conn() as conn:
        if req.session_id:
            if not conn.execute(
                'SELECT 1 FROM chat_sessions WHERE id = %s AND organization_id = %s', (req.session_id, user.org_id)
            ).fetchone():
                raise api_error(404, 'SESSION.NOT_FOUND', 'session not found')
            sid = str(req.session_id)
        else:
            pid = (
                own_project(conn, req.project_id, user.org_id)
                if req.project_id
                else active or default_project(conn, user)
            )
            if req.segment is not None and opening is None:
                fallback = req.locale or user_locale(conn, user_id)
                if not (opening := stored_first_list(conn, pid, req.segment, fallback)):
                    raise api_error(422, 'CHAT.SEGMENT_GONE', 'This segment is no longer available')
            message = opening.message if opening else req.message
            title = (opening and chat_title(opening.title)) or chat_title(message)
            sid = str(
                conn.execute(
                    'INSERT INTO chat_sessions (organization_id, user_id, project_id, title, goal, brief)'
                    ' VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
                    (user.org_id, user_id, pid, title, req.goal, opening and opening.brief),
                ).fetchone()['id']
            )
        conn.execute(f'{FAIL_STALE_JOBS_SQL} AND (user_id = %s OR chat_session_id = %s)', (user_id, sid))
        # this message answers the question a paused turn ended on (ask_user)
        conn.execute(
            "UPDATE jobs SET status = 'succeeded', updated_at = now()"
            " WHERE chat_session_id = %s AND user_id = %s AND status = 'needs_input'",
            (sid, user_id),
        )
        picked = None if req.pick is None else _picked_search(conn, sid, req.pick)
        if req.pick is not None and not picked:
            raise api_error(422, 'CHAT.PICK_INVALID', 'That alternative is gone')
        try:
            with conn.transaction():
                job_id = str(
                    conn.execute(
                        'INSERT INTO jobs (organization_id, user_id, chat_session_id, pick, locale)'
                        ' VALUES (%s, %s, %s, %s, %s) RETURNING id',
                        (user.org_id, user_id, sid, Jsonb(picked) if picked else None, req.locale),
                    ).fetchone()['id']
                )
        except psycopg.errors.UniqueViolation:
            raise api_error(409, 'CHAT.JOB_RUNNING', 'Another request is still running') from None
        except psycopg.errors.ForeignKeyViolation:  # #494: the chat was deleted after the check above
            raise api_error(404, 'SESSION.NOT_FOUND', 'session not found') from None
        if r := req.retry_stage:
            try:
                retry.begin(
                    conn, user_id=user_id, session_id=sid, source_job_id=str(r.job_id), stage=r.stage, job_id=job_id
                )
            except retry.RetryNotFound:
                raise api_error(404, 'RETRY.NOT_FOUND', 'job not found') from None
            except retry.RetryStale:
                raise api_error(409, 'RETRY.STALE', 'This step can no longer be retried') from None
        if a := req.plan_approval:
            try:
                plans.approve(
                    conn,
                    org_id=user.org_id,
                    session_id=sid,
                    list_id=str(a.list_id),
                    version=a.version,
                    rows=a.rows,
                    job_id=job_id,
                    dropped=a.dropped_stages,
                )
            except plans.PlanNotFound:
                raise api_error(404, 'PLAN.NOT_FOUND', 'plan not found') from None
            except plans.StageUnknown:
                raise api_error(422, 'PLAN.STAGE_UNKNOWN', 'The plan has no such stage') from None
            except plans.StageRequired:
                raise api_error(422, 'PLAN.STAGE_REQUIRED', 'A required stage cannot be dropped') from None
            except plans.PlanStale:
                raise api_error(409, 'PLAN.STALE', 'A newer version of this plan exists') from None
        conn.execute(
            'INSERT INTO chat_messages (session_id, user_id, role, content, job_id) VALUES (%s, %s, %s, %s, %s)',
            (sid, user_id, 'user', message, job_id),
        )
    return sid, job_id
