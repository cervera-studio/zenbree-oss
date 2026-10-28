"""The HubSpot writes that a run in review held, done once the run is approved (#939).

Approval commits and queues ``write_held_hubspot``; the request never waits on HubSpot. The task claims the run's held
people in one statement, so a second approval, a retry or a redelivery that overlaps it finds the claim and writes
nobody. People written are recorded in ``crm_contact_writes`` as they are written, and a contact HubSpot already has is
found by its email, so a task that died after a claim goes again once the claim is stale and repeats no write. A write
that fails keeps the people held: approving the run again queues the task again.
"""

from __future__ import annotations

from datetime import timedelta

import structlog
from psycopg.types.json import Jsonb

from app.automations import hubspot
from app.automations.models import Definition
from app.automations.run import StepFailed
from app.core.db import get_conn

logger = structlog.get_logger()
STALE_AFTER = timedelta(minutes=17)  # above the task's hard limit: a live task is never claimed over

_CLAIM_SQL = """
UPDATE automation_runs r SET hubspot_claimed_at = now()
FROM automations a
WHERE r.id = %s AND a.id = r.automation_id AND r.approved_at IS NOT NULL AND cardinality(r.hubspot_row_ids) > 0
  AND (r.hubspot_claimed_at IS NULL OR r.hubspot_claimed_at < now() - %s)
RETURNING r.id, r.definition, r.hubspot_row_ids, a.user_id, a.project_id"""


def queue(run_id: str) -> None:
    """Hand the run's held writes to a worker, as the member who approved."""
    from app.worker import write_held_hubspot

    try:
        write_held_hubspot.delay(run_id)
    except Exception as exc:  # the approval stands: approving again queues it again
        logger.error('automation.hubspot_queue_failed', run_id=run_id, error_type=type(exc).__name__)


async def write(run_id: str) -> None:
    with get_conn() as conn:
        claimed = conn.execute(_CLAIM_SQL, (run_id, STALE_AFTER)).fetchone()
    if not claimed or claimed['user_id'] is None:
        return
    done: dict = {}
    error: str | None = 'write_crashed'  # until the write returns: a crash of any kind keeps the people held
    try:
        done = await hubspot.write(
            run_id,
            str(claimed['user_id']),
            str(claimed['project_id']),
            str(claimed['definition']['list_id']),
            [str(i) for i in claimed['hubspot_row_ids']],
            Definition.model_validate(claimed['definition']),
        )
        error = None
    except StepFailed as failure:  # one with a cause is a crash after a write, which HubSpot did not refuse
        done, error = failure.output, 'write_crashed' if failure.__cause__ else failure.code
    finally:
        with get_conn() as conn:  # the claim is let go; the people stay held unless the write returned
            conn.execute(
                'UPDATE automation_runs SET hubspot_claimed_at = NULL, hubspot_row_ids = CASE WHEN %s::text IS NULL'
                " THEN '{}' ELSE hubspot_row_ids END,"
                " summary = summary || jsonb_build_object('hubspot_after_approval', %s::jsonb) WHERE id = %s",
                (error, Jsonb({**done, 'error_code': error}), run_id),
            )
