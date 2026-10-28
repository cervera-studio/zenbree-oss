"""The claim of one send by one account, in one transaction (#935): pacing slot, cap, the conversation's account, then
``sending``.

Locks run in one order, account row, then target, then send. ``claims.claim``, the reply paths, ``rejects.reject``,
``field_routes.regenerate`` and ``dispatcher._settle`` lock a target before its send too, so no two of them wait on
each other in a cycle. The success write in ``_deliver`` locks the send first, on a send no claim reaches: the account
compare-and-set and the pin turn away a stale claim of a send in flight. The pool row is read, not locked. Unlink,
link, attach and a member release lock the account row before they write a link or pool row, so the slot's lock
already orders them against this claim.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from app.core.db import get_conn
from app.outreach import send_states, sending
from app.outreach.send_queue import NOT_HELD, OWNER_NOT_BANNED

# Locked, then pinned only when no conversation exists: a pin is final (trigger outreach_target_pin_is_final), and
# rewriting a pinned row would touch the home feed on every send.
_CONVERSATION = """
SELECT t.sender_pinned_at FROM outreach_campaign_targets t
WHERE t.id = %(target)s AND t.status = 'active' AND t.next_step = %(step)s
  AND (t.sender_pinned_at IS NULL OR t.sender_id = %(account)s)
  AND EXISTS (SELECT 1 FROM outreach_campaign_senders p WHERE p.campaign_id = t.campaign_id AND p.sender_id = %(account)s)
FOR NO KEY UPDATE OF t"""

_PIN = """
UPDATE outreach_campaign_targets SET sender_id = %(account)s, sender_pinned_at = %(now)s
WHERE id = %(target)s AND sender_pinned_at IS NULL"""

_CLAIM = f"""
UPDATE outreach_campaign_sends s SET status = 'sending', attempts = attempts + 1, attempted_at = %(now)s,
       sender_provider = %(provider)s, sender_id = %(account)s, error_code = NULL,
       message_id = COALESCE(%(message_id)s, message_id)
FROM outreach_campaigns c, outreach_campaign_targets t
WHERE s.id = %(send)s AND {send_states.guard('sending', column='s.status')} AND s.attempts < %(attempts)s
  AND c.id = s.campaign_id AND c.owner_id IS NOT DISTINCT FROM s.owner_id AND c.status = 'active'
  AND {OWNER_NOT_BANNED}
  AND EXISTS (SELECT 1 FROM project_senders ps WHERE ps.sender_id = %(account)s AND ps.project_id = c.project_id)
  AND t.id = s.target_id AND t.owner_id IS NOT DISTINCT FROM s.owner_id AND t.status = 'active' AND t.next_step = s.step
  AND {NOT_HELD}"""


def take(s: dict, mb: dict, provider: str, now: datetime, message_id: str | None = None) -> bool:
    """Whether ``mb`` claimed send ``s``. An email claim also starts the person's conversation on ``mb``, or checks
    that it is on ``mb`` and that ``mb`` is in the campaign's pool. False writes nothing."""
    args = {
        'account': mb['id'],
        'provider': provider,
        'now': now,
        'send': s['id'],
        'target': s['target_id'],
        'step': s['step'],
        'message_id': message_id,
        'attempts': sending.MAX_ATTEMPTS,
    }
    with get_conn() as conn, conn.transaction():
        slot = conn.execute(
            "UPDATE user_oauth_connections SET last_used_at = %s WHERE id = %s AND status = 'connected'"
            ' AND last_used_at IS NOT DISTINCT FROM %s',
            (now, mb['id'], mb['last_used_at']),
        ).rowcount
        if not slot or sending.at_cap(conn, s['channel'], mb['id'], now):  # counted under the slot's row lock
            raise psycopg.Rollback
        if s['channel'] == 'email':
            conversation = conn.execute(_CONVERSATION, args).fetchone()
            if conversation is None:
                raise psycopg.Rollback
            if conversation['sender_pinned_at'] is None:
                conn.execute(_PIN, args)
        if conn.execute(_CLAIM, args).rowcount:
            return True
        raise psycopg.Rollback  # the row moved since the queue read it: nothing was claimed
    return False
