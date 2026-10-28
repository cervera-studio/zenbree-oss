"""A reject in the approval queue: the person skips the step (``skipped``/``REJECTED``) and moves to the next one.

Locks run in the order of every path that stops a person (see ``sender_slot``): the people, then their sends. A reply,
an unsubscribe or a bounce (``coordination._stop``) locks the person and then skips the same send, so a reject that
took the send first waited on it in a cycle (#1114). The project lock orders a reject against
``field_routes.regenerate``, which locks many people in no fixed order while this locks them in id order.
"""

from __future__ import annotations

from app.outreach import claims, holds, send_states

_REJECTABLE = send_states.guard('skipped', frm=('awaiting_approval',))

_LOCK_PEOPLE = """
SELECT 1 FROM outreach_campaign_targets
 WHERE id IN (SELECT target_id FROM outreach_campaign_sends WHERE id = ANY(%s) AND campaign_id = %s)
 ORDER BY id FOR NO KEY UPDATE"""

_SKIP = f"""
UPDATE outreach_campaign_sends SET status = 'skipped', error_code = 'REJECTED'
 WHERE id = ANY(%s) AND campaign_id = %s AND owner_id IS NOT DISTINCT FROM %s AND {_REJECTABLE}
RETURNING target_id, step"""


def reject(conn, campaign: dict, send_ids: list) -> int:
    holds.lock_out_enrollments(conn, [campaign['project_id']], wait=True)
    conn.execute(_LOCK_PEOPLE, (send_ids, campaign['id']))
    rows = conn.execute(_SKIP, (send_ids, campaign['id'], campaign['owner_id'])).fetchall()
    for r in rows:
        claims.advance(conn, r['target_id'], r['step'])
    return len(rows)
