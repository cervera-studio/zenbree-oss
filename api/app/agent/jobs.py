"""When the app stops waiting for a job that still reads ``running``."""

# Older than run_job's hard time limit (16 min, app.worker): the worker is gone.
STALE_JOB_MINUTES = 20

# A job of alias `j` that still has its worker: the one the sidebar shows as running and a delete waits for.
LIVE_JOB_SQL = f"j.status IN ('queued', 'running') AND j.updated_at >= now() - interval '{STALE_JOB_MINUTES} minutes'"

_HIDDEN_TOOLS = ('ask_user', 'set_brief')
_VISIBLE_CALL = ' && '.join(f'@.name != "{name}"' for name in _HIDDEN_TOOLS)
_FAILED_CALL_PATH = f'strict $[*] ? (@.verdict == "failed" && {_VISIBLE_CALL})'
JOB_TOOL_CALLS_SQL = "(SELECT m.tool_calls FROM chat_messages m WHERE m.id = (j.result ->> 'message_id')::uuid)"


def failed_step_sql(tool_calls: str = JOB_TOOL_CALLS_SQL) -> str:
    """A finished job of alias `j` with the chat header's step error: a stage with an error, or a failed tool call
    the user sees. ``tool_calls`` is the SQL of the job's message's tool calls. False, never NULL, without stages or
    a message, so ``NOT`` keeps such a job."""
    return f"coalesce(j.stages @? 'strict $[*] ? (exists(@.error))' OR {tool_calls} @? '{_FAILED_CALL_PATH}', false)"


# Fails the jobs whose worker is gone, so uq_jobs_one_active_per_user lets their user start a new turn. A new turn
# runs it for its own user and chat in its organization, then, when the unique key still refuses it, for the user's
# jobs in every organization (app.agent.routes._start_turn): a turn cannot see another organization's jobs.
FAIL_STALE_JOBS_SQL = (
    "UPDATE jobs SET status = 'failed', error = 'stale', finished_at = now(), updated_at = now()"
    f" WHERE status IN ('queued', 'running') AND updated_at < now() - interval '{STALE_JOB_MINUTES} minutes'"
)
