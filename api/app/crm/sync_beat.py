"""The bodies of the HubSpot tasks of app/worker.py: the write-back sweep (#527, #528) and the retry of a gate read
(#120)."""

import asyncio
from datetime import UTC, datetime
from time import monotonic

from app.core import tasks
from app.crm import activities, contacts, gate, timeline
from app.outreach.dispatcher import _access_token, gate_candidates

# The task's soft limit is 240 s: the timeline sweep stops at this many seconds from its start.
_TIMELINE_DEADLINE_SECONDS = 200


async def _sweeps() -> dict:
    started = monotonic()
    # Contacts first: a touch is logged on a contact that is there (#528).
    written = await contacts.sweep(datetime.now(UTC), _access_token)
    logged = await activities.sweep(_access_token)
    external = await timeline.sweep(_access_token, deadline=started + _TIMELINE_DEADLINE_SECONDS)
    return {
        **written,
        **{f'activity_{k}': v for k, v in logged.items()},
        **{f'timeline_{k}': v for k, v in external.items()},
    }


def run() -> dict:
    return asyncio.run(_sweeps())


def retry_read(task, owner_id: str, org_id: str) -> dict:
    """The retry of a gate read that failed in one organization's beat, as its Member(org, owner), who reads nothing
    once gone. ``task`` is the bound Celery task: it re-queues itself with a growing countdown."""
    now = datetime.now(UTC)
    try:
        with tasks.as_member(org_id, owner_id):
            return dict(asyncio.run(gate.retry(owner_id, gate_candidates(now).get(owner_id, []), now, _access_token)))
        gate.resume(owner_id)
        return {}
    except gate.ReadFailed as exc:
        delay = gate.retry_delay(task.request.retries + 1, exc.retry_after)
        gate.wait_for_retry(owner_id, delay)
        raise task.retry(exc=exc, countdown=delay) from exc
