"""Drafts that no approval will ever promote (#939).

A review run adds people to the campaign as ``draft`` and only its own approval promotes them. Approval is allowed
for the automation's current version, so once an edit bumps ``version`` the older unapproved runs can never be
approved. The user did not approve what that version picked, so those drafts leave the campaign and the people are
forgotten, so the next run takes them again under the new rules. A target that was sent anything stays.
"""

from __future__ import annotations

from app.automations import instantly_handoff
from app.automations.models import Definition

# A review run that ended without its own approval while its version has an approved run and its campaign is still
# the automation's: that approval covers its drafts, so any later approval promotes them (``approval._promote``) and
# the discard spares them. A run whose campaign an edit replaced is not covered: no approval promotes in its campaign,
# so its drafts go and the next run takes those people again. One clause, so the two cannot disagree about which
# drafts are still reachable. Expects the run aliased ``r``.
_CAMPAIGN = (
    "(SELECT s ->> 'campaign_id' FROM jsonb_array_elements({0} -> 'steps') s WHERE s ->> 'type' = 'add_to_campaign')"
)
COVERED = (
    "r.approved_at IS NULL AND r.review AND r.status IN ('unknown', 'cancelled', 'failed') AND EXISTS (SELECT 1"
    ' FROM automation_runs p WHERE p.automation_id = r.automation_id AND p.version = r.version'
    f' AND p.approved_at IS NOT NULL) AND {_CAMPAIGN.format("r.definition")} IS NOT DISTINCT FROM'
    f' (SELECT {_CAMPAIGN.format("a.definition")} FROM automations a WHERE a.id = r.automation_id)'
)

# A finished review run that an approval accepts (``approval.approve``). One that died, was cancelled or failed counts
# when it left drafts in the campaign: they wait for an approval, and a run approved before it ended could not promote
# them. Expects the run aliased ``r``.
APPROVABLE = (
    "r.review AND r.status NOT IN ('queued', 'running') AND (r.status = 'succeeded'"
    ' OR EXISTS (SELECT 1 FROM automation_enrollments e WHERE e.run_id = r.id))'
)
# An unapproved version whose run left people that wait for its approval (drafts, or people held from HubSpot or
# Instantly). The webhook queue starts nothing while it holds, so the user approves what the version does once instead
# of one review run for each call. A subset of ``APPROVABLE``, so the queue never waits for an approval that would
# answer 409.
# Expects the automation aliased ``a``.
WAITING_FOR_APPROVAL = (
    'a.approved_version IS DISTINCT FROM a.version AND EXISTS (SELECT 1 FROM automation_runs r'
    f' WHERE r.automation_id = a.id AND r.version = a.version AND r.approved_at IS NULL AND {APPROVABLE}'
    ' AND (cardinality(r.hubspot_row_ids) > 0 OR EXISTS (SELECT 1 FROM automation_enrollments e WHERE e.run_id = r.id)'
    f' OR {instantly_handoff.HOLDS}))'
)


def discard_unapproved_drafts(conn, automation_id) -> int:
    """Remove the drafts of unapproved review runs of an older version than the automation's current one, except the
    drafts an approval still reaches (``COVERED``). A run that succeeded unapproved is never reached once its version
    is outdated, so its drafts go. Called when an edit bumps the version and when a run ends or is approved, because a
    run that was going at the edit adds its drafts after it. The Instantly holds of a replaced campaign go too, approved
    or not (``instantly_handoff.forget_replaced``). The caller holds the automation's lock; the Instantly
    holds, then the project, then the campaign are locked here, the order of a drain (``instantly_handoff.drain``),
    of approval and of enrollment."""
    current = conn.execute('SELECT version FROM automations WHERE id = %s', (automation_id,)).fetchone()
    if not current:
        return 0
    instantly_handoff.forget_replaced(conn, automation_id)
    stale = conn.execute(
        'SELECT id, definition FROM automation_runs r WHERE automation_id = %s AND version < %s AND review'
        f" AND approved_at IS NULL AND status NOT IN ('queued', 'running') AND NOT ({COVERED})",
        (automation_id, current['version']),
    ).fetchall()
    if not stale:
        return 0
    instantly_handoff.forget(conn, [r['id'] for r in stale])
    campaigns = {
        str(step.campaign_id)
        for run in stale
        if (step := Definition.model_validate(run['definition']).step('add_to_campaign'))
    }
    removed = 0
    for campaign_id in sorted(campaigns):
        row = conn.execute('SELECT project_id FROM outreach_campaigns WHERE id = %s', (campaign_id,)).fetchone()
        if not row:
            continue
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (row['project_id'],))
        conn.execute('SELECT id FROM outreach_campaigns WHERE id = %s FOR UPDATE', (campaign_id,))
        removed += conn.execute(
            'DELETE FROM outreach_campaign_targets t USING automation_enrollments e'
            " WHERE e.target_id = t.id AND e.run_id = ANY(%s) AND t.campaign_id = %s AND t.status = 'draft'"
            ' AND NOT EXISTS (SELECT 1 FROM outreach_campaign_sends s WHERE s.target_id = t.id)',
            ([r['id'] for r in stale], campaign_id),
        ).rowcount
    conn.execute(
        'DELETE FROM automation_rows WHERE automation_id = %s AND run_id = ANY(%s)',
        (automation_id, [r['id'] for r in stale]),
    )
    return removed
