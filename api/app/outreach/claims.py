"""The durable claim of one campaign step for one person, shared by every channel's planner.

A row in ``outreach_campaign_sends`` written while the person is locked on that step: the dispatcher and
``app.outreach.crm_tasks`` both plan through it, so no two of them can claim the same step.
"""

import structlog

from app.outreach import holds

logger = structlog.get_logger()
_PLANNED = ('approved', 'awaiting_approval')


def claim(
    conn,
    t: dict,
    step: int,
    status: str,
    *,
    recipient: str,
    channel: str = 'email',
    subject: str = '',
    body: str | None = None,
    code=None,
) -> bool:
    if status in _PLANNED:
        if not holds.lock_out_enrollments(conn, [t['project_id']], wait=False):
            logger.info('outreach.claim_busy', campaign_id=str(t['campaign_id']))
            return False
        email = recipient if channel == 'email' else t['email']
        if reason := holds.hold(conn, t['project_id'], channel, email, t['linkedin_url']):
            logger.info('outreach.claim_held', code=reason.upper(), campaign_id=str(t['campaign_id']))
            return False
    # Locks the person until this claim commits, and only on the step they are still on: undoing a reject
    # (routes.undo_reject, #230) moves a person back under the same lock, so the two cannot interleave.
    if not conn.execute(
        'SELECT 1 FROM outreach_campaign_targets WHERE id = %(id)s AND next_step = %(step)s'
        " AND status = 'active' AND frozen_at IS NOT DISTINCT FROM %(values_planned_from)s FOR UPDATE",
        {'id': t['id'], 'step': step, 'values_planned_from': t['frozen_at']},
    ).fetchone():
        return False
    row = conn.execute(
        """INSERT INTO outreach_campaign_sends
               (campaign_id, owner_id, target_id, step, channel, status, recipient, subject, body,
                error_code, idempotency_key, approved_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   CASE WHEN %s = 'approved' THEN now() END)
           ON CONFLICT DO NOTHING RETURNING id""",
        (
            t['campaign_id'],
            t['owner_id'],
            t['id'],
            step,
            channel,
            status,
            recipient,
            subject,
            body,
            code,
            f'{t["campaign_id"]}:{t["id"]}:{step}',
            status,
        ),
    ).fetchone()
    return row is not None


def advance(conn, target_id, step: int) -> None:
    conn.execute(
        'UPDATE outreach_campaign_targets SET next_step = next_step + 1 WHERE id = %s AND next_step = %s',
        (target_id, step),
    )
