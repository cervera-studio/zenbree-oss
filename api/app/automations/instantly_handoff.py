"""The people a succeeded run hands to Instantly, and the one batch they become (#1032). The only writer of
``automation_instantly``.

``finish`` holds the people (``record``) in the transaction that marks them taken, so a run that fails, is cancelled
or dies holds nobody and the next run takes them again. ``drain`` turns a hold into an ``instantly_enrollments`` batch
in one transaction: it locks the hold, creates the batch (``instantly_enroll.create``, which judges every person again
under the project lock) and links it. A second drain finds the link and makes nothing, so the end of the run, an
approval, a second approval and the beat can all call it. No call to Instantly happens here: the batch's worker
(``instantly_publish``) sends, and reconciles the people whose outcome is in doubt.

The message to the worker goes after the commit (``send``). The beat (``pickup``) drains the holds that waited (another
batch of the project, a paused automation, a disconnection), sends again the batches whose message was lost, and
cancels a queued batch whose creator left, so it does not block the project.
"""

from __future__ import annotations

from datetime import timedelta
from typing import NamedTuple

import structlog

from app.automations import instantly_step
from app.automations.run import Run
from app.core import tasks
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.scope import acting_for
from app.integrations import instantly_enroll, instantly_store
from app.integrations.instantly_client import Campaign
from app.integrations.instantly_fields import Mapping

logger = structlog.get_logger()

TASK = 'run_instantly_enrollment'
BATCH = 100  # holds and batches the beat works on each minute
# A queued batch nobody claimed for this long lost its message: a worker claims a queued batch within seconds.
RESEND_AFTER = timedelta(seconds=120)


class Queued(NamedTuple):
    """A batch to hand to the worker, as ``tasks.queue_as_owners`` takes it."""

    org_id: str
    owner_id: str
    enrollment_id: str


# A held run of the automation's live creator, whose batch may be made now: the run is not in review, or the user
# approved it, and the automation is not paused. Expects the hold, its run and its automation aliased ``i``, ``r``, ``a``.
_DUE = 'a.enabled AND zb.is_live_member(a.user_id, a.organization_id) AND (NOT r.review OR r.approved_at IS NOT NULL)'
_HELD = (
    'FROM automation_instantly i JOIN automation_runs r ON r.id = i.run_id JOIN automations a ON a.id = r.automation_id'
    ' WHERE i.enrollment_id IS NULL'
)
# A run that waits for its approval while it holds people (``discard.WAITING_FOR_APPROVAL``). Expects the run aliased r.
HOLDS = 'EXISTS (SELECT 1 FROM automation_instantly i WHERE i.run_id = r.id AND i.enrollment_id IS NULL)'


def record(conn, run: Run) -> None:
    """In ``finish``, when the run succeeded: the people the step would send, minus those a later step did not reach
    (they are not taken, so the next run takes them)."""
    if not run.handoff:
        return
    ids = [i for i in run.handoff.row_ids if i not in run.unreached]
    if not ids:
        return
    campaign = run.handoff.campaign
    conn.execute(
        'INSERT INTO automation_instantly (run_id, organization_id, project_id, workspace_id, campaign_id,'
        ' campaign_name, campaign_status, row_ids) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::uuid[])'
        ' ON CONFLICT (run_id) DO NOTHING',
        (
            run.id,
            run.org_id,
            run.project_id,
            run.handoff.workspace_id,
            campaign.id,
            campaign.name[:300],
            campaign.status,
            ids,
        ),
    )


def forget(conn, run_ids: list) -> None:
    """The holds of review runs no approval can reach any more: their people are taken again by the next run."""
    conn.execute('DELETE FROM automation_instantly WHERE run_id = ANY(%s) AND enrollment_id IS NULL', (run_ids,))


# The ``add_to_instantly`` campaign of a definition, NULL without the step. Compared definition to definition: the
# hold's ``campaign_id`` is the id Instantly answered with, which need not be spelled as the user's pick.
_CAMPAIGN = (
    "(SELECT s ->> 'campaign_id' FROM jsonb_array_elements({0} -> 'steps') s WHERE s ->> 'type' = 'add_to_instantly')"
)


def forget_replaced(conn, automation_id) -> None:
    """The holds of the automation's runs, approved or not, whose campaign an edit replaced or removed: the user no
    longer picks that campaign, and another workspace's key may have replaced the one that read it. The held people
    are taken again by the next run, as with the drafts of a replaced native campaign (``discard.COVERED``). A hold
    whose batch a drain made first is kept, with its people."""
    conn.execute(
        'WITH gone AS (DELETE FROM automation_instantly i USING automation_runs r, automations a'
        ' WHERE r.id = i.run_id AND a.id = r.automation_id AND a.id = %s AND i.enrollment_id IS NULL'
        f' AND {_CAMPAIGN.format("r.definition")} IS DISTINCT FROM {_CAMPAIGN.format("a.definition")}'
        ' RETURNING i.run_id, i.row_ids)'
        ' DELETE FROM automation_rows t USING gone'
        ' WHERE t.automation_id = %s AND t.run_id = gone.run_id AND t.row_id = ANY(gone.row_ids)',
        (automation_id, automation_id),
    )


def drain(run_id: str) -> Queued | None:
    """The batch of a held run that is due, created and linked in one transaction; ``None`` when it is not due, waits
    (see ``view``), or another drain has it."""
    with get_conn() as conn:
        held = conn.execute(
            'SELECT i.*, a.user_id, r.definition ->> %s AS list_id'
            f' {_HELD} AND i.run_id = %s AND {_DUE} FOR UPDATE OF i SKIP LOCKED',
            ('list_id', run_id),
        ).fetchone()
        if not held:
            return None
        # ``pickup`` leaves out the holds whose connection is not usable only so that they do not take the beat's places.
        # This is the check that decides: the batch is made with this connection row, and the drains after a run and
        # after an approval come here without the pickup.
        connection = instantly_store.row(conn, str(held['project_id']))
        if not instantly_step.connected(connection) or connection['workspace_id'] != held['workspace_id']:
            return None
        rows = instantly_enroll.load_rows(conn, held['list_id'], [str(i) for i in held['row_ids']])
        if not rows:  # every one of them left the list
            conn.execute('DELETE FROM automation_instantly WHERE run_id = %s', (run_id,))
            return None
        made = instantly_enroll.create(
            conn,
            owner_id=str(held['user_id']),
            org_id=str(held['organization_id']),
            project_id=str(held['project_id']),
            connection=connection,
            campaign=Campaign(held['campaign_id'], held['campaign_name'], held['campaign_status']),
            list_id=held['list_id'],
            rows=rows,
            mapping=Mapping(),  # every field from its default column
        )
        if is_err(made):  # INSTANTLY.BUSY: another batch of the project is live, and nothing was written
            return None
        conn.execute(
            "UPDATE automation_instantly SET enrollment_id = %s, row_ids = '{}' WHERE run_id = %s", (made.value, run_id)
        )
    return Queued(str(held['organization_id']), str(held['user_id']), made.value)


def send(batches: list[Queued]) -> int:
    """Hand each batch to the worker, as its creator. A second message for a queued batch is harmless: only one claim
    takes it (``instantly_publish.claim``). A broker that is down leaves the batch queued, and the beat sends it again."""
    from app.worker import celery_app

    return tasks.queue_as_owners(celery_app, TASK, list(batches))


def after_run(run: Run) -> None:
    """The batch of a run that is not in review, right after ``finish``. The run is closed already: a failure here is
    logged, and the beat drains what this missed."""
    if run.handoff:
        _drain_and_send(run.id)


def after_approval(run_id: str) -> None:
    """The batch of a run just approved, after the approval committed. Approving again drains again: that is how a
    user who saw ``waiting_for`` retries at once."""
    _drain_and_send(run_id)


def _drain_and_send(run_id: str) -> None:
    try:
        made = drain(run_id)
    except Exception as exc:
        logger.error('automation.instantly_drain_failed', run_id=run_id, error_type=type(exc).__name__)
        return
    if made:
        send([made])


# The oldest due hold of each project that can make a batch now: its connection can call Instantly, as
# ``instantly_step.connected`` says, and it has no live batch, so ``instantly_enroll.create`` would not answer
# ``INSTANTLY.BUSY``. It starts from the projects, not the holds: the holds that wait for good pile up across clients,
# and a project that cannot make a batch reads none of its own.
_PICKUP = (
    'SELECT h.run_id::text, h.organization_id::text FROM instantly_connections c CROSS JOIN LATERAL'
    ' (SELECT i.run_id, i.organization_id, i.created_at FROM automation_instantly i'
    ' JOIN automation_runs r ON r.id = i.run_id JOIN automations a ON a.id = r.automation_id'
    f' WHERE i.project_id = c.project_id AND i.enrollment_id IS NULL AND i.workspace_id = c.workspace_id AND {_DUE}'
    ' ORDER BY i.created_at LIMIT 1) h'
    " WHERE c.status = 'connected' AND c.api_key_encrypted <> '' AND NOT EXISTS (SELECT 1 FROM instantly_enrollments e"
    " WHERE e.project_id = c.project_id AND e.status IN ('queued', 'running'))"
    ' ORDER BY h.created_at LIMIT %s'
)
_RESEND = (
    'SELECT e.id::text, e.organization_id::text, e.project_id::text, e.created_by::text,'
    ' zb.is_live_member(e.created_by, e.organization_id) AS live'
    ' FROM instantly_enrollments e JOIN automation_instantly i ON i.enrollment_id = e.id'
    " WHERE e.status = 'queued' AND e.touched_at < now() - %s ORDER BY e.created_at LIMIT %s"
)


def pickup() -> list[Queued]:
    """The beat's share: the batches to send now. Called in the beat's System scope, which only selects: each hold is
    drained, and each batch of a creator who left is cancelled, as its organization."""
    with get_conn() as conn:
        # A hold that cannot be made now would keep its place at the head for good, so the pickup takes only the holds
        # that can, and the oldest one of each project: a project makes one batch at a time.
        due = conn.execute(_PICKUP, (BATCH,)).fetchall()
        stale = conn.execute(_RESEND, (RESEND_AFTER, BATCH)).fetchall()
    out: list[Queued] = [
        made for hold in due if (made := _as_its_organization(hold['organization_id'], drain, hold['run_id']))
    ]
    for batch in stale:
        if batch['live']:
            out.append(Queued(batch['organization_id'], batch['created_by'], batch['id']))
        else:  # its worker would stop at once, and the queued batch would block the project's next one
            _as_its_organization(batch['organization_id'], _cancel, batch['project_id'], batch['id'])
    return out


def _cancel(project_id: str, enrollment_id: str) -> None:
    with get_conn() as conn:
        instantly_enroll.request_cancel(conn, project_id, enrollment_id)


def _as_its_organization(org_id: str, work, *args):
    # One organization that fails must not stop the others, nor the rest of the beat.
    try:
        with acting_for(org_id, 'automation instantly handoff'):
            return work(*args)
    except Exception as exc:
        logger.error('automation.instantly_pickup_failed', org_id=org_id, error_type=type(exc).__name__)
        return None


def view(conn, run_id) -> dict | None:
    """``RunInstantly`` of a run: ``None`` when it holds nobody for Instantly."""
    row = conn.execute(
        'SELECT i.*, r.review, r.approved_at, a.enabled,'
        ' zb.is_live_member(a.user_id, a.organization_id) AS live,'
        ' (SELECT count(*) FROM instantly_leads l WHERE l.enrollment_id = i.enrollment_id) AS leads'
        ' FROM automation_instantly i JOIN automation_runs r ON r.id = i.run_id JOIN automations a ON a.id = r.automation_id'
        ' WHERE i.run_id = %s',
        (run_id,),
    ).fetchone()
    if not row:
        return None
    campaign = Campaign(row['campaign_id'], row['campaign_name'], row['campaign_status'])
    return {
        'people': row['leads'] if row['enrollment_id'] else len(row['row_ids']),
        'batch_id': row['enrollment_id'],
        'waiting_for': None if row['enrollment_id'] else _waiting_for(conn, row),
        'campaign': instantly_step.snapshot(campaign),
    }


def _waiting_for(conn, row: dict) -> str:
    if row['review'] and not row['approved_at']:
        return 'approval'
    if not row['enabled']:
        return 'paused'
    if not row['live']:
        return 'creator_gone'
    connection = instantly_store.row(conn, str(row['project_id']))
    if not instantly_step.connected(connection):
        return 'connection'
    if connection['workspace_id'] != row['workspace_id']:
        return 'other_workspace'
    live = conn.execute(
        "SELECT 1 FROM instantly_enrollments WHERE project_id = %s AND status IN ('queued', 'running')",
        (row['project_id'],),
    ).fetchone()
    return 'other_batch' if live else 'starting'
