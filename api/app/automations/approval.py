"""Approving the first run of an automation version (#939).

The people of a run that is in review entered the campaign as ``draft``. Approving the run records that the user has
seen what this version does: the next runs add people with the campaign's normal status (``enrolled_status``). The
run's own people are promoted when the campaign already runs, after the enrollment rules are checked again; in a
draft campaign they stay drafts, and activating the campaign promotes them. Approval never changes the campaign.

The people the run's HubSpot step held are written by a task that approval queues once its transaction commits
(``held_writes``): the request never waits on HubSpot, and approving again queues the task again while people stay held.
"""

from __future__ import annotations

from uuid import UUID

from app.auth.middleware import CurrentUser
from app.automations import held_writes, instantly_handoff, service
from app.automations.discard import APPROVABLE, COVERED, discard_unapproved_drafts
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.outreach.drafts import drop_blocked_drafts
from app.outreach.freeze import freeze


def _campaign_id(definition: dict) -> str | None:
    return next((s['campaign_id'] for s in definition['steps'] if s['type'] == 'add_to_campaign'), None)


def _promote(conn, run: dict) -> int:
    """Draft people of the run in a running campaign become active, minus those the rules now refuse."""
    campaign_id = _campaign_id(run['definition'])
    if not campaign_id:
        return 0
    campaign = conn.execute('SELECT project_id FROM outreach_campaigns WHERE id = %s', (campaign_id,)).fetchone()
    if not campaign:
        return 0
    # Same lock order as enrollment and activation: the project first, then the campaign.
    conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (campaign['project_id'],))
    campaign = conn.execute(
        "SELECT * FROM outreach_campaigns WHERE id = %s AND status IN ('active', 'paused') FOR UPDATE", (campaign_id,)
    ).fetchone()
    if not campaign:
        return 0
    # Also the drafts of every earlier run that died, was cancelled or failed after it added people to this campaign, of
    # a version the user approved (this one included): the next run refuses those people as already handled, so only
    # an approval promotes them.
    mine = {
        r['target_id']
        for r in conn.execute(
            'SELECT e.target_id FROM automation_enrollments e JOIN automation_runs r ON r.id = e.run_id'
            f' WHERE r.automation_id = %s AND (r.id = %s OR ({COVERED}))',
            (run['automation_id'], run['id']),
        )
    }
    return len(freeze(conn, campaign, drop_blocked_drafts(conn, campaign, only=mine)).active)


def _approvable(conn, run_id) -> bool:
    row = conn.execute(f'SELECT {APPROVABLE} AS ok FROM automation_runs r WHERE r.id = %s', (run_id,)).fetchone()
    return bool(row['ok'])


def approve(user: CurrentUser, project_id: str, automation_id: UUID, run_id: UUID) -> dict:
    with get_conn() as conn:
        automation = service.owned(conn, user, project_id, automation_id, lock=True)
        run = conn.execute(
            'SELECT * FROM automation_runs WHERE id = %s AND automation_id = %s FOR UPDATE', (run_id, automation_id)
        ).fetchone()
        if not run:
            raise api_error(404, 'AUTOMATION.RUN_NOT_FOUND', 'run not found')
        if not run['approved_at']:
            if not _approvable(conn, run_id):
                raise api_error(
                    409,
                    'AUTOMATION.NOT_IN_REVIEW',
                    'only a finished run that is in review, or left drafts, is approved',
                )
            if run['version'] != automation['version']:
                raise api_error(409, 'AUTOMATION.RUN_OUTDATED', 'the automation was edited: run it again to review it')
            conn.execute('UPDATE automations SET approved_version = %s WHERE id = %s', (run['version'], automation_id))
            conn.execute(
                'UPDATE automation_runs SET approved_at = now(), approved_by = %s WHERE id = %s', (user.id, run_id)
            )
            discard_unapproved_drafts(conn, automation_id)
            promoted = _promote(conn, run)
            conn.execute(
                "UPDATE automation_runs SET summary = summary || jsonb_build_object('promoted', %s::int) WHERE id = %s",
                (promoted, run_id),
            )
    if run['hubspot_row_ids'] and automation['user_id'] is not None:
        held_writes.queue(str(run_id))
    instantly_handoff.after_approval(str(run_id))
    return service.run_detail(user, project_id, automation_id, run_id)
