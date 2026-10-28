from uuid import UUID

from fastapi import APIRouter

from app.agent.jobs import LIVE_JOB_SQL
from app.agent.routes import Auth, Ok
from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.errors_http import api_error

router = APIRouter(prefix='/v1')


@router.delete('/chat/sessions/{session_id}', response_model=Ok)
def delete_session(session_id: UUID, user: CurrentUser = Auth) -> dict:
    with get_conn() as conn:
        # the lock makes a concurrent POST /v1/chat either visible to the check or fail its FK
        if not conn.execute(
            'SELECT 1 FROM chat_sessions WHERE id = %s AND organization_id = %s FOR UPDATE', (session_id, user.org_id)
        ).fetchone():
            raise api_error(404, 'SESSION.NOT_FOUND', 'session not found')
        # a turn still running would insert its reply into the gone chat and never finish (#434)
        if conn.execute(
            f'SELECT 1 FROM jobs j WHERE j.chat_session_id = %s AND {LIVE_JOB_SQL} LIMIT 1', (session_id,)
        ).fetchone():
            raise api_error(409, 'CHAT.JOB_RUNNING', 'The chat is still answering')
        conn.execute('DELETE FROM chat_sessions WHERE id = %s', (session_id,))
    return {'status': 'ok'}
