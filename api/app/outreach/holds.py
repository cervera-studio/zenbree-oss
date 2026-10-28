"""May native outreach write to this person now (#931). The rule is the SQL function ``outreach_hold``, so every
native boundary answers alike; this module is its only Python reader."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, get_args

from app.core.db import get_conn
from app.integrations import webhooks
from app.outreach import open_sends
from app.outreach.reply_targets import LINKEDIN_KEY

HoldReason = Literal['stop_failed', 'stop_pending', 'stopped', 'held_by_instantly']
HOLD_REASONS: tuple[HoldReason, ...] = get_args(HoldReason)
STOP_REASONS: tuple[HoldReason, ...] = ('stop_failed', 'stop_pending', 'stopped')

SKIP_REASON: dict[HoldReason, str] = {
    'stop_failed': 'stopped',
    'stop_pending': 'stopped',
    'stopped': 'stopped',
    'held_by_instantly': 'held_by_instantly',
}


def held(project: str, channel: str, email: str, linkedin: str, reasons: tuple[HoldReason, ...] = HOLD_REASONS) -> str:
    listed = ', '.join(f"'{r}'" for r in reasons)
    return f'EXISTS (SELECT 1 FROM outreach_hold({project}, {channel}, {email}, {linkedin}) h WHERE h.reason IN ({listed}))'


def reason_sql(project: str, channel: str, email: str, linkedin: str) -> str:
    return f'(SELECT h.reason FROM outreach_hold({project}, {channel}, {email}, {linkedin}) h)'


def not_held(project: str, channel: str, email: str, linkedin: str) -> str:
    return f'NOT EXISTS (SELECT 1 FROM outreach_hold({project}, {channel}, {email}, {linkedin}))'


def in_keys(keys: str, channel: str, email: str, linkedin_alias: str) -> str:
    # The same answer as outreach_hold, as hashed lookups against keys read once. A NULL key matches nothing.
    every = f'(SELECT kind, value FROM {keys} WHERE channel IS NULL)'
    on_channel = f'(SELECT channel, kind, value FROM {keys} WHERE channel IS NOT NULL)'
    email_key = f'lower(btrim({email}))'
    return f"""coalesce(('email', {email_key}) IN {every} OR ('linkedin_url', {linkedin_alias}) IN {every}
        OR ({channel}, 'email', {email_key}) IN {on_channel}
        OR ({channel}, 'linkedin_url', {linkedin_alias}) IN {on_channel}, false)"""


def lock_out_enrollments(conn, project_ids: Sequence, *, wait: bool) -> bool:
    """Every enrollment of a project, native and Instantly (``instantly_publish._judge_chunk``), holds the project
    row ``FOR NO KEY UPDATE`` while it judges and writes, so a hold read after this lock sees what they committed.
    ``wait=False`` answers False when one is in flight: not now, never a refusal."""
    ids = sorted({str(p) for p in project_ids})
    lock = 'FOR SHARE' if wait else 'FOR SHARE SKIP LOCKED'
    locked = conn.execute(f'SELECT 1 FROM projects WHERE id = ANY(%s::uuid[]) ORDER BY id {lock}', (ids,)).fetchall()
    return len(locked) == len(ids)


def stopped(conn, project_id, email: str | None, linkedin: str | None) -> bool:
    return hold(conn, project_id, None, email, linkedin) is not None


def hold(conn, project_id, channel: str | None, email: str | None, linkedin: str | None) -> HoldReason | None:
    row = conn.execute(
        'SELECT reason FROM outreach_hold(%s, %s, %s, %s)', (project_id, channel, email or '', linkedin or '')
    ).fetchone()
    return _checked(row['reason'] if row else None)


def _checked(reason: str | None) -> HoldReason | None:
    if reason is not None and reason not in HOLD_REASONS:
        raise ValueError(f'outreach_hold answered {reason!r}, which HoldReason does not list')
    return reason


def campaign_channel(campaign: dict) -> str | None:
    return 'email' if any(step.get('channel') == 'email' for step in campaign.get('steps') or []) else None


def for_campaign(conn, campaign: dict, email: str | None, linkedin: str | None) -> HoldReason | None:
    return hold(conn, campaign['project_id'], campaign_channel(campaign), email, linkedin)


def resume_paused(now: datetime) -> int:
    """Ends the out-of-office pauses due by ``now``. A person who meanwhile replied or was excluded in another campaign
    of the project, on any alias, ends ``replied`` or ``excluded``; one a stop holds stays paused. Returns the people
    who go on."""
    sibling = f"""(EXISTS (SELECT 1 FROM outreach_campaigns oc JOIN outreach_campaign_targets o ON o.campaign_id = oc.id
                    WHERE oc.project_id = d.project_id AND o.id <> d.id AND o.status = '{{status}}'
                      AND coalesce(btrim(o.email), '') <> '' AND lower(btrim(o.email)) IN (
                          SELECT e.email FROM outreach_person_emails(d.project_id, d.email, d.linkedin_url) e))
                   OR (btrim(d.linkedin_url) <> '' AND EXISTS (
                       SELECT 1 FROM outreach_campaigns oc JOIN outreach_campaign_targets o ON o.campaign_id = oc.id
                        WHERE oc.project_id = d.project_id AND o.id <> d.id AND o.status = '{{status}}'
                          AND btrim(o.linkedin_url) <> ''
                          AND {LINKEDIN_KEY.format('o.linkedin_url')} = {LINKEDIN_KEY.format('d.linkedin_url')})))"""
    stop = held('d.project_id', 'd.channel', 'd.email', 'd.linkedin_url', STOP_REASONS)
    with get_conn() as conn:
        rows = conn.execute(
            f"""WITH due AS (
                    SELECT t.id, c.project_id, t.email, t.linkedin_url,
                           c.steps -> (t.next_step - 1) ->> 'channel' AS channel
                      FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id
                     WHERE t.status = 'paused' AND t.paused_until <= %s
                     ORDER BY t.id FOR UPDATE OF t SKIP LOCKED),
                verdict AS (
                    SELECT d.id, CASE WHEN {sibling.format(status='replied')} THEN 'replied'
                                      WHEN {sibling.format(status='excluded')} THEN 'excluded'
                                      WHEN {stop} THEN NULL ELSE 'active' END AS status
                      FROM due d)
                UPDATE outreach_campaign_targets t
                   SET status = v.status, paused_until = NULL,
                       replied_at = CASE WHEN v.status = 'replied' THEN now() ELSE t.replied_at END
                  FROM verdict v
                 WHERE t.id = v.id AND v.status IS NOT NULL AND t.status = 'paused'
             RETURNING t.id, t.campaign_id, t.email, t.linkedin_url, t.status""",
            (now,),
        ).fetchall()
        ended = {status: [r for r in rows if r['status'] == status] for status in ('replied', 'excluded')}
        for status, code in (('replied', 'REPLIED'), ('excluded', 'EXCLUDED')):
            open_sends.skip(conn, [r['id'] for r in ended[status]], code)
    for status, targets in ended.items():
        webhooks.sequence_stopped(targets, status)
    return sum(r['status'] == 'active' for r in rows)
