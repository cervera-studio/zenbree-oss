"""Enrollment to Instantly (#929): the preview, the confirmed batch, and what a batch shows.

A batch is one list's selected people sent to one campaign. Confirming records every selected person (``pending`` or
``skipped`` with a reason) and queues the run (:mod:`app.integrations.instantly_publish`). An enrollment is not a send:
no ``outreach_campaign_sends`` row, no credit and no CRM write come from it, so it is never counted as an email sent.
"""

from __future__ import annotations

from collections import Counter

from psycopg.types.json import Jsonb

from app.core.errors import Result, err, ok
from app.integrations import instantly_fields, instantly_verdicts
from app.integrations.instantly_client import Campaign
from app.integrations.instantly_fields import Mapping
from app.integrations.instantly_verdicts import Verdict
from app.lists.row_view import VISIBLE_CELLS_SQL

MAX_ROWS = 1000  # one batch, as MAX_TARGETS of a native campaign
PREVIEW_ROWS = 25
# The worker touches its batch before each chunk: a live batch silent for this long lost its worker.
STALL_SECONDS = 300
# ponytail: the URL pattern is not in the API reference or the docs (they name no app URL); it is the pattern the app
# shows in its address bar. If Instantly changes it the link 404s there; nothing else depends on it.
CAMPAIGN_URL = 'https://app.instantly.ai/app/campaign/{id}'


def campaign_url(campaign_id: str) -> str:
    return CAMPAIGN_URL.format(id=campaign_id)


def lock_project(conn, project_id: str) -> None:
    """The lock a native enrollment takes (``scheduling.runner.refill``): two enrollments of one project queue up."""
    conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))


def list_columns(conn, project_id: str, list_id: str) -> set[str] | None:
    """The column keys of a person list of the project, or None when it is not one (or not the project's)."""
    lst = conn.execute(
        "SELECT 1 FROM lists WHERE id = %s AND project_id = %s AND entity = 'person' AND deleted_at IS NULL",
        (list_id, project_id),
    ).fetchone()
    if not lst:
        return None
    return {r['key'] for r in conn.execute('SELECT key FROM list_columns WHERE list_id = %s', (list_id,)).fetchall()}


def load_rows(conn, list_id: str, row_ids: list[str]) -> list[dict]:
    """The selected rows, in the order of the selection; an id of another list is simply absent."""
    found = {
        str(r['id']): r
        for r in conn.execute(
            f'SELECT r.id, {VISIBLE_CELLS_SQL} AS cells, r.excluded FROM list_rows r'
            ' WHERE r.list_id = %s AND r.id = ANY(%s)',
            (list_id, row_ids),
        ).fetchall()
    }
    return [found[i] for i in dict.fromkeys(row_ids) if i in found]


def _counts(verdicts: list[Verdict]) -> dict:
    reasons = Counter(v.reason for v in verdicts if v.reason)
    return {
        'selected': len(verdicts),
        'eligible': sum(1 for v in verdicts if not v.reason),
        'skipped': dict(reasons),
    }


def preview(conn, *, owner_id: str, project_id: str, rows: list[dict], mapping: Mapping, campaign: Campaign) -> dict:
    """No side effect: what confirming now would send, and what it would leave out and why."""
    verdicts = instantly_verdicts.judge(conn, project_id=project_id, owner_id=owner_id, rows=rows)
    by_id = {str(r['id']): r for r in rows}
    sample = [
        {'row_id': v.row_id, 'lead': instantly_fields.lead(by_id[v.row_id]['cells'], v.email, mapping)}
        for v in verdicts
        if not v.reason
    ][:PREVIEW_ROWS]
    return {
        'campaign': _campaign(campaign),
        **_counts(verdicts),
        'rows': [{'row_id': v.row_id, 'email': v.email or None, 'reason': v.reason} for v in verdicts],
        'sample': sample,
        'custom_variables': list(mapping.custom),
    }


def _campaign(campaign: Campaign) -> dict:
    return {
        'id': campaign.id,
        'name': campaign.name,
        'status': campaign.status,
        'may_send': campaign.may_send,
        'url': campaign_url(campaign.id),
    }


def create(
    conn,
    *,
    owner_id: str,
    org_id: str,
    project_id: str,
    connection: dict,
    campaign: Campaign,
    list_id: str,
    rows: list[dict],
    mapping: Mapping,
) -> Result[str]:
    """Records the batch and every selected person, as the verdicts of this instant say. The caller commits, then
    queues the run. One live batch for each project (queued or running)."""
    lock_project(conn, project_id)
    if conn.execute(
        "SELECT 1 FROM instantly_enrollments WHERE project_id = %s AND status IN ('queued', 'running')", (project_id,)
    ).fetchone():
        return err('INSTANTLY.BUSY', detail='another Instantly batch of this project is still running')
    verdicts = instantly_verdicts.judge(conn, project_id=project_id, owner_id=owner_id, rows=rows)
    enrollment = conn.execute(
        'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, list_id, created_by,'
        ' workspace_id, campaign_id, campaign_name, campaign_status, mapping)'
        ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id',
        (
            org_id,
            project_id,
            connection['id'],
            list_id,
            owner_id,
            connection['workspace_id'],
            campaign.id,
            campaign.name[:300],
            campaign.status,
            mapping.model_dump_json(),
        ),
    ).fetchone()['id']
    by_id = {str(r['id']): r for r in rows}
    for v in verdicts:
        payload = None
        if not v.reason:
            _supersede(conn, connection['workspace_id'], campaign.id, v.email)
            payload = instantly_fields.lead(by_id[v.row_id]['cells'], v.email, mapping)
        _record(conn, org_id, project_id, enrollment, connection['workspace_id'], campaign.id, v, payload)
    return ok(str(enrollment))


def _supersede(conn, workspace_id: str, campaign_id: str, email: str) -> None:
    """A failed batch keeps its people `pending` so that Retry can resume them. A new batch for the same person takes
    them over: the live one is the one the unique index may hold."""
    conn.execute(
        "UPDATE instantly_leads l SET outcome = 'skipped', reason = 'superseded', updated_at = now()"
        " FROM instantly_enrollments e WHERE e.id = l.enrollment_id AND e.status = 'failed' AND l.outcome = 'pending'"
        ' AND l.workspace_id = %s AND l.campaign_id = %s AND l.email = %s',
        (workspace_id, campaign_id, email),
    )


def _record(
    conn,
    org_id: str,
    project_id: str,
    enrollment,
    workspace_id: str,
    campaign_id: str,
    v: Verdict,
    payload: dict | None,
) -> None:
    reason = v.reason
    if not reason:
        # The unique index is the last guard: a concurrent batch of another project on the same workspace.
        inserted = conn.execute(
            'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, row_id, workspace_id,'
            " campaign_id, email, outcome, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)"
            ' ON CONFLICT DO NOTHING',
            (org_id, project_id, enrollment, v.row_id, workspace_id, campaign_id, v.email, Jsonb(payload)),
        ).rowcount
        if inserted:
            return
        reason = 'already_in_instantly'
    conn.execute(
        'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, row_id, workspace_id, campaign_id,'
        " email, outcome, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, 'skipped', %s)",
        (org_id, project_id, enrollment, v.row_id, workspace_id, campaign_id, v.email or None, reason),
    )


def request_cancel(conn, project_id: str, enrollment_id: str) -> str | None:
    """A queued or failed batch, or a running one whose worker died, ends now; a live one stops before its next
    chunk. Returns the status, None if not found.

    The cancel is written on the people, not only on the batch: whoever may already be in Instantly is ``uncertain``
    with ``cancelled_in_flight``, and no run sends that person again. Only a reconcile settles them."""
    row = conn.execute(
        'SELECT status, (touched_at < now() - make_interval(secs => %s)) AS stalled FROM instantly_enrollments'
        ' WHERE id = %s AND project_id = %s FOR UPDATE',
        (STALL_SECONDS, enrollment_id, project_id),
    ).fetchone()
    if not row:
        return None
    if row['status'] in ('queued', 'failed') or (row['status'] == 'running' and row['stalled']):
        conn.execute(
            "UPDATE instantly_leads SET outcome = 'skipped', reason = 'cancelled', updated_at = now()"
            " WHERE enrollment_id = %s AND outcome IN ('pending', 'failed')",
            (enrollment_id,),
        )
        # What was in flight may have gone out: it stays held until someone looks in Instantly. updated_at stays:
        # the age of the doubt is the age of the request.
        conn.execute(
            "UPDATE instantly_leads SET outcome = 'uncertain', reason = 'cancelled_in_flight'"
            " WHERE enrollment_id = %s AND outcome IN ('publishing', 'uncertain')",
            (enrollment_id,),
        )
        conn.execute(
            "UPDATE instantly_enrollments SET status = 'cancelled', cancel_requested = true, finished_at = now()"
            ' WHERE id = %s',
            (enrollment_id,),
        )
        return 'cancelled'
    if row['status'] == 'running':
        conn.execute('UPDATE instantly_enrollments SET cancel_requested = true WHERE id = %s', (enrollment_id,))
    return row['status']


def view(conn, project_id: str, enrollment_id: str) -> dict | None:
    e = conn.execute(
        'SELECT id, status, error_code, campaign_id, campaign_name, campaign_status, cancel_requested, created_at,'
        ' finished_at, (touched_at < now() - make_interval(secs => %s)) AS stalled FROM instantly_enrollments'
        ' WHERE id = %s AND project_id = %s',
        (STALL_SECONDS, enrollment_id, project_id),
    ).fetchone()
    if not e:
        return None
    leads = conn.execute(
        'SELECT row_id, email, outcome, reason, updated_at FROM instantly_leads WHERE enrollment_id = %s ORDER BY seq',
        (enrollment_id,),
    ).fetchall()
    counts = Counter(lead['outcome'] for lead in leads)
    live = e['status'] in ('queued', 'running')
    left = any(counts.get(k) for k in ('pending', 'publishing', 'failed', 'uncertain'))
    return {
        'id': e['id'],
        'status': e['status'],
        'error_code': e['error_code'],
        'campaign': {'id': e['campaign_id'], 'name': e['campaign_name'], 'url': campaign_url(e['campaign_id'])},
        'created_at': e['created_at'],
        'finished_at': e['finished_at'],
        'counts': {
            k: counts.get(k, 0) for k in ('pending', 'publishing', 'accepted', 'skipped', 'failed', 'uncertain')
        },
        'cancel_requested': e['cancel_requested'],
        # A failed batch with people left can be dismissed, so no batch stays on the card for good.
        'can_cancel': (live and (not e['cancel_requested'] or e['stalled'])) or (e['status'] == 'failed' and left),
        # A live batch with an old heartbeat died with its worker; a failed one resumes what is left.
        # A cancelled batch with people in doubt can only be reconciled: no run sends a cancelled person.
        'can_retry': ((e['status'] == 'failed' or (live and e['stalled'])) and left)
        or (e['status'] == 'cancelled' and bool(counts.get('uncertain'))),
        'rows': [
            {'row_id': lead['row_id'], 'email': lead['email'], 'outcome': lead['outcome'], 'reason': lead['reason']}
            for lead in leads
        ],
    }


def latest(conn, project_id: str) -> dict | None:
    """The newest batch of the project that still needs a decision: live, failed with people left, or in doubt. The
    user reaches Cancel and Retry through it after the dialog that made the batch is gone."""
    found = conn.execute(
        "SELECT e.id FROM instantly_enrollments e WHERE e.project_id = %s AND (e.status IN ('queued', 'running')"
        " OR (e.status = 'failed' AND EXISTS (SELECT 1 FROM instantly_leads l WHERE l.enrollment_id = e.id"
        " AND l.outcome IN ('pending', 'publishing', 'failed', 'uncertain')))"
        " OR (e.status = 'cancelled' AND EXISTS (SELECT 1 FROM instantly_leads l WHERE l.enrollment_id = e.id"
        " AND l.outcome = 'uncertain'))) ORDER BY e.created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return view(conn, project_id, str(found['id'])) if found else None
