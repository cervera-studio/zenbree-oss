"""The query behind a campaign's people (#218)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.outreach import holds, sender_holds
from app.outreach.holds import HoldReason
from app.outreach.sender_holds import SenderHold
from app.outreach.variants import Variant

EmailSender = Literal['native', 'instantly']

# ponytail: the newest-message lateral scans outreach_inbound_messages by target with no index on target_id;
# the #288 review measured ~500 ms for a campaign page against 20k messages. Add `CREATE INDEX ... ON outreach_inbound_messages
# (target_id, received_at DESC)` as a migration when inboxes grow.
# Newest reply first, then by name. A step counts as sent once delivered.
TARGETS_SQL = f"""
SELECT t.id, t.first_name, t.last_name, t.company_name, t.status, t.next_step, t.paused_until, t.frozen_at, t.variant,
       coalesce(t.personalization -> 'missing', '[]') AS missing,
       EXISTS (SELECT 1 FROM outreach_campaign_sends s WHERE s.campaign_id = t.campaign_id AND s.target_id = t.id
                  AND s.status = 'awaiting_approval' AND s.step = t.next_step
                  AND {holds.not_held('%(project)s', 's.channel', 't.email', 't.linkedin_url')}) AS awaiting_approval,
       EXISTS (SELECT 1 FROM outreach_campaign_sends s WHERE s.campaign_id = t.campaign_id AND s.target_id = t.id
                  AND s.error_code = 'INVALID_EMAIL') AS invalid_email,
       ls.step AS last_sent_step, ls.delivered_at AS last_sent_at,
       m.id AS message_id, m.kind AS message_kind, m.label AS message_label, m.received_at AS message_at,
       sg.data ->> 'title' AS signal_title, coalesce(btrim(t.email), '') <> '' AS has_email,
       {holds.reason_sql('%(project)s', '%(channel)s', 't.email', 't.linkedin_url')} AS hold,
       {holds.reason_sql('%(project)s', "'email'", 't.email', 't.linkedin_url')} AS email_hold,
       a.account_label AS sender, {sender_holds.reason_sql('t', 'c', '%(held)s')} AS sender_hold
FROM outreach_campaign_targets t
JOIN outreach_campaigns c ON c.id = t.campaign_id
LEFT JOIN user_oauth_connections a ON a.id = t.sender_id
LEFT JOIN signals sg ON sg.id = t.signal_id
LEFT JOIN LATERAL (
    SELECT s.step, s.delivered_at FROM outreach_campaign_sends s
     WHERE s.campaign_id = t.campaign_id AND s.target_id = t.id AND s.status = 'sent'
     ORDER BY s.step DESC LIMIT 1
) ls ON true
LEFT JOIN LATERAL (
    SELECT m.id, m.kind, m.label, m.received_at FROM outreach_inbound_messages m
     WHERE m.target_id = t.id AND m.owner_id IS NOT DISTINCT FROM t.owner_id ORDER BY m.received_at DESC LIMIT 1
) m ON true
WHERE t.campaign_id = %(campaign)s AND t.owner_id IS NOT DISTINCT FROM %(owner)s
ORDER BY m.received_at DESC NULLS LAST, t.last_name, t.first_name, t.id
LIMIT %(limit)s"""


class Target(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    company_name: str
    status: Literal['active', 'draft', 'paused', 'excluded', 'replied']
    next_step: int
    awaiting_approval: bool
    invalid_email: bool
    last_sent_step: int | None
    last_sent_at: datetime | None
    message_id: UUID | None
    message_kind: Literal['reply', 'auto_reply', 'unsubscribe'] | None
    message_label: Literal['interested', 'meeting_request', 'not_interested', 'not_now', 'other'] | None
    message_at: datetime | None
    paused_until: datetime | None
    # Why a watchlist signal enrolled the person (#648): the posting title; null for anyone else.
    signal_title: str | None
    hold: HoldReason | None
    email_sender: EmailSender | None
    sender: str | None
    sender_hold: SenderHold | None
    frozen_at: datetime | None
    missing: list[str]
    # The copy the person gets in an A/B test; null: no test, or they joined while allocation was paused (copy A).
    variant: Variant | None


class Targets(BaseModel):
    items: list[Target]


def read_targets(conn, campaign: dict, limit: int, now: datetime) -> list[dict]:
    channel = holds.campaign_channel(campaign)
    params = {
        'campaign': campaign['id'],
        'owner': campaign['owner_id'],
        'project': campaign['project_id'],
        'channel': channel,
        'limit': limit,
        'held': sender_holds.held_accounts(conn, campaign['project_id'], now),
    }
    out = []
    for row in conn.execute(TARGETS_SQL, params).fetchall():
        sender = _email_sender(row.pop('email_hold'), channel == 'email', row.pop('has_email'))
        out.append({**row, 'email_sender': sender})
    return out


def _email_sender(email_hold: str | None, writes_email: bool, has_email: bool) -> EmailSender | None:
    if email_hold == 'stopped':
        return None
    if email_hold is not None:
        return 'instantly'
    return 'native' if writes_email and has_email else None
