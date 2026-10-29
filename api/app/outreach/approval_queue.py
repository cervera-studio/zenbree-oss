from __future__ import annotations

from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from pydantic import BaseModel

from app.core.config import settings
from app.outreach import holds, pool_state, sender_holds, sending
from app.outreach.domain.campaign import Channel
from app.outreach.holds import HoldReason
from app.outreach.sender_holds import SenderHold
from app.outreach.variants import Variant

_SENDS = """outreach_campaign_sends q
    JOIN outreach_campaign_targets t ON t.id = q.target_id AND t.owner_id IS NOT DISTINCT FROM q.owner_id"""
_WAITING = "q.campaign_id = c.id AND q.status = 'awaiting_approval' AND t.status = 'active' AND t.next_step = q.step"
_PERSON = ('c.project_id', 'q.channel', 't.email', 't.linkedin_url')

# The held people are read once and each waiting message is looked up in them: a hold probe per message made the
# nav count 10x slower at 20,000 waiting messages.
_COUNTS_SQL = f"""
WITH keys AS MATERIALIZED (SELECT kind, value, channel FROM outreach_held_keys(%(project)s))
SELECT q.campaign_id, count(*) AS waiting,
       count(*) FILTER (WHERE {holds.in_keys('keys', 'q.channel', 't.email', 't.linkedin_alias')}) AS held
  FROM outreach_campaigns c, {_SENDS}
 WHERE c.project_id = %(project)s AND {_WAITING}{{campaign}}
 GROUP BY q.campaign_id"""


class ApprovalCount(NamedTuple):
    awaiting: int
    held: int


NO_APPROVALS = ApprovalCount(0, 0)


def counts(conn, project_id, campaign_id=None) -> dict[UUID, ApprovalCount]:
    sql = _COUNTS_SQL.format(campaign=' AND c.id = %(campaign)s' if campaign_id else '')
    rows = conn.execute(sql, {'project': project_id, 'campaign': campaign_id}).fetchall()
    return {r['campaign_id']: ApprovalCount(r['waiting'] - r['held'], r['held']) for r in rows}


def with_counts(campaigns: list[dict], approvals: dict[UUID, ApprovalCount]) -> list[dict]:
    def counted(c: dict) -> dict:
        n = approvals.get(c['id'], NO_APPROVALS)
        return {**c, 'awaiting_approval': n.awaiting, 'awaiting_approval_held': n.held}

    return [counted(c) for c in campaigns]


def owned_awaiting(conn, user_id: str) -> int:
    owned = conn.execute(
        'SELECT c.id, c.project_id FROM outreach_campaigns c'
        ' WHERE c.owner_id = %s AND zb.is_live_member(c.owner_id, c.organization_id)',
        (user_id,),
    ).fetchall()
    approvals: dict[UUID, ApprovalCount] = {}
    for project_id in {r['project_id'] for r in owned}:
        approvals |= counts(conn, project_id)
    return sum(approvals.get(r['id'], NO_APPROVALS).awaiting for r in owned)


_QUEUE_SQL = f"""
SELECT q.id, q.step, q.channel, q.recipient, q.subject, q.body, q.created_at,
       t.first_name, t.last_name, t.company_name, t.variant, q.target_id, t.sender_id AS target_sender,
       t.sender_pinned_at AS target_pinned_at,
       {holds.reason_sql(*_PERSON)} AS hold, {sender_holds.reason_sql('t', 'c', '%(held)s')} AS sender_hold
FROM outreach_campaigns c, {_SENDS}
WHERE {_WAITING} AND c.id = %(campaign)s AND q.owner_id IS NOT DISTINCT FROM %(owner)s{{send}}
ORDER BY q.created_at LIMIT %(limit)s"""


def items(conn, c: dict, now: datetime, limit: int, send_id: UUID | None = None) -> list[dict]:
    params = {
        'campaign': c['id'],
        'owner': c['owner_id'],
        'send': send_id,
        'limit': limit,
        'held': sender_holds.held_accounts(conn, c['project_id'], now),
    }
    rows = conn.execute(_QUEUE_SQL.format(send=' AND q.id = %(send)s' if send_id else ''), params).fetchall()
    return _delivery(conn, c, rows, now)


def _delivery(conn, c: dict, rows: list[dict], now: datetime) -> list[dict]:
    """ponytail: only the windows are read; the mailbox's queue, pacing gap and daily cap can push the real send
    later."""
    pool = pool_state.accounts(conn, c, now)
    pinned = {
        a['id']: a
        for a in conn.execute(
            'SELECT * FROM user_oauth_connections WHERE id = ANY(%s)',
            ([r['target_sender'] for r in rows if r['target_pinned_at'] and r['target_sender']],),
        ).fetchall()
    }
    linkedin = conn.execute(
        'SELECT u.account_label, u.display_name FROM user_oauth_connections u'
        ' JOIN project_senders ps ON ps.sender_id = u.id'
        " WHERE ps.project_id = %s AND u.provider = 'unipile' AND u.status = 'connected' AND NOT u.paused"
        ' ORDER BY u.created_at LIMIT 1',
        (c['project_id'],),
    ).fetchone()
    out = []
    for r in rows:
        item = {k: v for k, v in r.items() if k not in ('target_id', 'target_sender', 'target_pinned_at')}
        if r['channel'] == 'email':
            target = {'id': r['target_id'], 'sender_id': r['target_sender'], 'sender_pinned_at': r['target_pinned_at']}
            mb = pinned.get(r['target_sender']) if r['target_pinned_at'] else pool_state.sender_of(pool, target)
            zone = (mb or {}).get('send_timezone') or settings.outreach_send_window_timezone
            windows = sending.email_windows(c, zone) + (sending.email_windows(mb, zone) if mb else [])
            sender = {'address': mb['account_label'], 'name': mb['display_name']} if mb else None
        else:
            windows = sending.linkedin_windows(c, settings.outreach_send_window_timezone)
            sender = (
                {'address': None, 'name': linkedin['display_name'] or linkedin['account_label']} if linkedin else None
            )
        # A held conversation waits for the account, not for a window: any time shown would not happen.
        send_at = None if r['sender_hold'] else sending.next_send_time(now, windows)
        item.update(send_at=send_at, sender=sender)
        out.append(item)
    return out


class QueuedSender(BaseModel):
    address: str | None
    name: str | None


class QueuedSend(BaseModel):
    id: UUID
    step: int
    channel: Channel
    recipient: str
    subject: str
    body: str | None
    created_at: datetime
    first_name: str
    last_name: str
    company_name: str
    variant: Variant | None
    send_at: datetime | None
    sender: QueuedSender | None
    hold: HoldReason | None
    sender_hold: SenderHold | None


class QueuedSends(BaseModel):
    items: list[QueuedSend]
