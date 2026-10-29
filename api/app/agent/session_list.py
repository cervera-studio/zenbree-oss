"""The chat list of the sidebar and of `GET /v1/chat/sessions`: each chat with the status of its latest job."""

from __future__ import annotations

from uuid import UUID

from app.agent.jobs import LIVE_JOB_SQL, failed_step_sql

# The latest job decides the status; a run past STALE_JOB_MINUTES lost its worker. Only the CASE branch of a finished
# job reads tool_calls, which holds every call's preview (tens of KB a message). The 200 sessions are picked
# before the laterals, so only they are counted. ponytail: counts every row of
# the chats' lists on each call, fine until lists are large, then keep a row count on lists.
_SESSION_LIST_SQL = f"""
SELECT s.id, s.project_id, s.title, s.created_at, s.updated_at, s.pinned_at,
       CASE WHEN j.status IS NULL THEN NULL
            WHEN {LIVE_JOB_SQL} THEN 'running'
            WHEN j.status IN ('queued', 'running', 'failed') THEN 'error'
            WHEN j.status = 'needs_input' AND j.question ? 'plan' THEN 'plan'
            WHEN j.status = 'needs_input' THEN 'asking'
            WHEN {failed_step_sql('j.tool_calls')} THEN 'error'
            ELSE 'done' END AS status,
       j.updated_at AS job_updated_at, coalesce(j.progress_done, 0) AS progress_done,
       coalesce(j.progress_total, 0) AS progress_total, ls.list_count, ls.people, ls.companies
  FROM (SELECT * FROM chat_sessions WHERE organization_id = %s AND (%s::uuid IS NULL OR project_id = %s)
         ORDER BY pinned_at DESC NULLS LAST, updated_at DESC LIMIT 200) s
  LEFT JOIN LATERAL (
       SELECT j.status, j.updated_at, j.progress_done, j.progress_total, j.stages, m.question, m.tool_calls
         FROM (SELECT * FROM jobs j WHERE j.chat_session_id = s.id ORDER BY j.created_at DESC LIMIT 1) j
         LEFT JOIN chat_messages m ON m.id = (j.result ->> 'message_id')::uuid) j ON true
 CROSS JOIN LATERAL (
       SELECT count(DISTINCT l.id) AS list_count,
              count(r.id) FILTER (WHERE l.entity = 'person' AND NOT r.excluded) AS people,
              count(r.id) FILTER (WHERE l.entity = 'company' AND NOT r.excluded) AS companies
         FROM lists l LEFT JOIN list_rows r ON r.list_id = l.id
        WHERE l.chat_session_id = s.id AND l.deleted_at IS NULL) ls
 ORDER BY s.pinned_at DESC NULLS LAST, s.updated_at DESC"""


def session_list(conn, org_id: str, project_id: UUID | str | None) -> list[dict]:
    return conn.execute(_SESSION_LIST_SQL, (org_id, project_id, project_id)).fetchall()
