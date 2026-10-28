"""Who a chat turn acts as (PR 7 of the organization-scoping run).

The body runs as the ``Member`` who started the turn. The worker enters it from the task's header
(app/core/tasks.py), so row-level security holds every tool to the chat's organization, and a member removed or
banned mid-turn gets ``ActorGone`` at the next database call: the turn stops calling the model and the tools.

The end runs as ``ForOrg`` of the job's organization, which owns the job: the hold settles, the answer is saved and
the job closes even after the member left (interrogate, Act on 4).
"""

from contextlib import AbstractContextManager

import structlog

from app.core.db import get_conn
from app.core.scope import ActorGone, ForOrg, acting_for

logger = structlog.get_logger()

#: The ``error`` frame of a turn whose member left: the code the HTTP 401 and the streams already use.
GONE = {'message': 'You are no longer a member of this organization.', 'code': 'AUTH.NOT_A_MEMBER'}
#: ``jobs.error`` of that turn.
GONE_ERROR = 'member removed or banned'


def ending(org_id: str) -> AbstractContextManager[ForOrg]:
    return acting_for(org_id, 'turn end')


def claim(job_id: str) -> dict | None:
    """Mark the queued job running and return it. None when it is no longer queued, so a redelivered task never runs
    a turn twice; when it is another member's, so a message whose header names a teammate runs nothing (the job waits
    for the stale-job sweep); and when its member left while it waited: the organization then fails it, and no step
    runs."""
    try:
        with get_conn() as conn:
            return conn.execute(
                "UPDATE jobs SET status = 'running', started_at = now(), updated_at = now()"
                # the member who queued it: a header naming a teammate (a forged message) claims nothing
                " WHERE id = %s AND status = 'queued' AND user_id = zb.actor()"
                ' RETURNING id, organization_id, user_id, chat_session_id, pick, stages, retry_of, retry_stage',
                (job_id,),
            ).fetchone()
    except ActorGone as gone:
        with ending(gone.scope.org), get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = %s, finished_at = now(), updated_at = now()"
                " WHERE id = %s AND status = 'queued'",
                (GONE_ERROR, job_id),
            )
        logger.info('agent.job_member_gone', job_id=job_id)
        return None
