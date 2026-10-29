from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from app.integrations.sender_state import account_hold

SenderHold = Literal[
    'send_uncertain',
    'mailbox_unknown',
    'disconnected',
    'needs_reconnect',
    'unlinked',
    'not_in_pool',
    'paused',
    'throttled',
]
ReplyHold = Literal['mailbox_unknown', 'disconnected', 'needs_reconnect', 'unlinked']

_ACCOUNTS = """
SELECT * FROM user_oauth_connections
WHERE organization_id = (SELECT organization_id FROM projects WHERE id = %s) AND provider IN ('google', 'microsoft')"""


def held_accounts(conn, project_id, now: datetime) -> Jsonb:
    rows = conn.execute(_ACCOUNTS, (project_id,)).fetchall()
    return Jsonb({str(r['id']): hold.value for r in rows if (hold := account_hold(r, now))})


def _account(t: str, held: str) -> str:
    return f'({held} ->> {t}.sender_id::text)'


def link_sql(t: str, project: str, held: str) -> str:
    return f"""
    WHEN {t}.sender_id IS NULL THEN 'mailbox_unknown'
    WHEN {_account(t, held)} IN ('disconnected', 'needs_reconnect') THEN {_account(t, held)}
    WHEN NOT EXISTS (SELECT 1 FROM project_senders ps WHERE ps.project_id = {project} AND ps.sender_id = {t}.sender_id)
        THEN 'unlinked'"""


def reason_sql(t: str, c: str, held: str) -> str:
    return f"""CASE
    WHEN ({t}.status = 'active' AND {c}.status IN ('active', 'paused')
          AND {c}.steps -> ({t}.next_step - 1) ->> 'channel' = 'email') IS NOT TRUE THEN NULL
    WHEN EXISTS (SELECT 1 FROM outreach_campaign_sends s
                  WHERE s.campaign_id = {t}.campaign_id AND s.target_id = {t}.id
                    AND s.owner_id IS NOT DISTINCT FROM {t}.owner_id AND s.step = {t}.next_step
                    AND s.channel = 'email' AND s.status = 'sending') THEN 'send_uncertain'
    WHEN {t}.sender_pinned_at IS NULL THEN NULL
    {link_sql(t, f'{c}.project_id', held)}
    WHEN NOT EXISTS (SELECT 1 FROM outreach_campaign_senders p
                      WHERE p.campaign_id = {t}.campaign_id AND p.sender_id = {t}.sender_id) THEN 'not_in_pool'
    WHEN {_account(t, held)} IN ('paused', 'throttled') THEN {_account(t, held)}
END"""


_COUNTS = f"""
SELECT t.sender_id, count(*) AS held
FROM outreach_campaign_targets t
JOIN outreach_campaigns c ON c.id = t.campaign_id AND c.owner_id IS NOT DISTINCT FROM t.owner_id
WHERE t.sender_id = ANY(%(accounts)s) AND t.status = 'active' AND c.project_id = %(project)s
  AND {reason_sql('t', 'c', '%(held)s')} IS NOT NULL
GROUP BY t.sender_id"""


def held_counts(conn, project_id, accounts: Sequence[UUID], now: datetime) -> dict[UUID, int]:
    params = {'accounts': list(accounts), 'project': project_id, 'held': held_accounts(conn, project_id, now)}
    return {r['sender_id']: r['held'] for r in conn.execute(_COUNTS, params).fetchall()}


_REPLY = f"""
SELECT CASE WHEN t.sender_pinned_at IS NULL THEN NULL {link_sql('t', '%(project)s', '%(held)s')} END AS hold
FROM outreach_campaign_targets t WHERE t.id = %(target)s"""


def reply_hold(conn, project_id, target_id, now: datetime) -> ReplyHold | None:
    params = {'target': target_id, 'project': project_id, 'held': held_accounts(conn, project_id, now)}
    row = conn.execute(_REPLY, params).fetchone()
    return cast(ReplyHold | None, row and row['hold'])
