"""Which approved emails each mailbox account may send this beat (#935), and whether the account is ready.

A campaign sends from its pool (``outreach_campaign_senders``). A person's conversation stays on the account that
sent their first email (``outreach_campaign_targets.sender_id``): their rows reach that account's queue only, and only
while it is in the pool. A conversation whose account cannot send waits for it and reaches no other account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.auth.middleware import creator_not_banned
from app.core.config import settings
from app.integrations.sender_state import AccountState, account_state, kill_switches
from app.outreach import holds, sending
from app.outreach.due import GATE_SEND

# #389: a banned owner's active campaign sends nothing, and goes on by itself on unban or when the ban expires.
OWNER_NOT_BANNED = creator_not_banned('c.owner_id')
NOT_HELD = holds.not_held('c.project_id', 's.channel', 't.email', 't.linkedin_url')

# A thread's next step sorts before a new conversation, so new people never starve a follow-up.
_EMAIL_QUEUES = f"""
SELECT mb.id AS mailbox_id, q.*
FROM user_oauth_connections mb
CROSS JOIN LATERAL (
    SELECT s.*, c.project_id, c.steps, c.send_window_start_hour, c.send_window_end_hour, c.send_window_days,
           t.email, t.company_name, t.linkedin_url
    FROM outreach_campaign_senders p
    JOIN outreach_campaigns c ON c.id = p.campaign_id
    JOIN outreach_campaign_sends s ON s.campaign_id = c.id AND s.owner_id IS NOT DISTINCT FROM c.owner_id
    JOIN outreach_campaign_targets t ON t.id = s.target_id AND t.owner_id IS NOT DISTINCT FROM s.owner_id
    WHERE p.sender_id = mb.id AND s.channel = 'email'
      AND s.status IN ('approved', 'failed') AND s.attempts < %s
      AND c.status = 'active' AND t.status = 'active' AND t.next_step = s.step
      AND (t.sender_pinned_at IS NULL OR t.sender_id = mb.id)
      AND {OWNER_NOT_BANNED}
      {GATE_SEND} AND {NOT_HELD}
    ORDER BY t.sender_pinned_at IS NULL, s.approved_at NULLS LAST, s.created_at
    LIMIT %s
) q
WHERE mb.status = 'connected' AND NOT mb.paused
"""


@dataclass(frozen=True)
class Mailbox:
    row: dict
    state: AccountState
    queue: list[dict]  # its sends, in queue order, whose campaign window is open in its zone


def mailboxes(conn, now: datetime, limit: int) -> list[Mailbox]:
    """Every connected, unpaused mailbox with at least one send queued for it, judged by ``account_state``."""
    rows = conn.execute(_EMAIL_QUEUES, (sending.MAX_ATTEMPTS, now, limit)).fetchall()
    queues: dict = {}
    for r in rows:
        queues.setdefault(r['mailbox_id'], []).append(r)
    accounts = conn.execute('SELECT * FROM user_oauth_connections WHERE id = ANY(%s)', (list(queues),)).fetchall()
    found = []
    for mb in accounts:
        tz = mb['send_timezone'] or settings.outreach_send_window_timezone
        used = sending.sent_today(conn, mb['id'], now, tz)
        state = account_state(mb, now=now, used_today=used, **kill_switches(mb['provider']))
        found.append(Mailbox(mb, state, [s for s in queues[mb['id']] if sending.window_open(s, now, tz)]))
    return found
