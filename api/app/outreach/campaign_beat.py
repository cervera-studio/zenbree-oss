"""The campaign beat (#80): every phase of ``app.outreach.dispatcher``, one organization at a time.

The task runs as ``System(DUE_WORK)`` (app/worker.py). That scope picks the organizations with campaign work. The
stored HubSpot opt-outs of owners who left (``due.exclude_opted_out_of_departed_owners``), which no organization's
scope shows, are applied first in a ``System(OPT_OUT)`` scope of their own: an opt-out stops the person in that
owner's campaigns of every organization the owner left. Each organization then runs the whole beat as ``ForOrg`` of
itself, so no read or write of one organization's beat can reach another's rows, whatever id or join it gets wrong.
The organizations run at once, each in a context of its own (``tasks.each_org``), so one slow mailbox provider does
not hold the others back.

A campaign's sends are the organization's work: they go on after the owner leaves (#333). What acts with the owner's
HubSpot grant (the read gate here, the task step in ``app.outreach.crm_tasks``) runs as ``Member(org, owner)``, so it
stops with them: their grant is not read or written for that organization again. An opt-out the gate already stored
is the person's wish, not the owner's choice, so it still excludes the person.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime

import psycopg
import structlog

from app.core import tasks
from app.core.db import get_conn
from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.crm import gate
from app.outreach import away, crm_tasks, dispatcher, due, holds, replies

logger = structlog.get_logger()

# Every organization one phase has work in: an active campaign (gate, plan, sends, tasks, completion), a person
# whose pause ends (resume_paused), a reminder that comes due (surface_reminders).
_DUE_ORGANIZATIONS = """SELECT organization_id FROM outreach_campaigns WHERE status = 'active'
UNION SELECT organization_id FROM outreach_campaign_targets WHERE status = 'paused' AND paused_until <= %(now)s
UNION SELECT organization_id FROM outreach_reminders WHERE surfaced_at IS NULL AND remind_on <= %(today)s"""


def due_organizations(now: datetime) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(_DUE_ORGANIZATIONS, {'now': now, 'today': away.local_today(now)}).fetchall()
    return sorted(str(r['organization_id']) for r in rows)


def _schedule_gate_retry(org_id: str, owner_id: str, delay: int) -> None:
    from app.worker import retry_hubspot_read

    retry_hubspot_read.apply_async((owner_id, org_id), countdown=delay)


async def _gate(org_id: str, now: datetime) -> Counter:
    """The HubSpot read gate, owner by owner, each as ``Member(org, owner)``: the grant is the owner's."""
    checked: Counter = Counter()
    for owner, emails in dispatcher.gate_candidates(now).items():
        with tasks.as_member(org_id, owner):
            checked += await gate.check(
                {owner: emails},
                now,
                dispatcher._access_token,
                lambda who, delay: _schedule_gate_retry(org_id, who, delay),
            )
    return checked


async def _phases(org_id: str, now: datetime) -> dict:
    resumed = holds.resume_paused(now)
    surfaced = replies.surface_reminders(now)
    # Same ``now`` for both: the plan claims only for people the gate read clear in this beat.
    checked = await _gate(org_id, now)
    planned = dispatcher.plan(now)
    delivered = await dispatcher.send_due(now)
    # Fresh: the email phase may have taken most of the beat, and LinkedIn's window and caps are by the clock.
    delivered += await dispatcher.send_linkedin_due(datetime.now(UTC))
    delivered += await crm_tasks.deliver_due(datetime.now(UTC), dispatcher._access_token)
    completed = dispatcher.complete_finished()
    return {
        'gate': checked,
        'plan': planned,
        'send': delivered,
        'completed': completed,
        'resumed': resumed,
        'reminders': surfaced,
    }


def _failure(exc: Exception) -> dict:
    """The log fields of a failed write: its type, and the constraint it broke when the database names one. Never the
    message, which can quote a row's values (an email address in a unique-key error)."""
    constraint = exc.diag.constraint_name if isinstance(exc, psycopg.Error) else None
    return {'error_type': type(exc).__name__} | ({'constraint_name': constraint} if constraint else {})


async def _beat(org_id: str, now: datetime) -> dict:
    try:
        return await _phases(org_id, now)
    except Exception as exc:  # one organization must not stop the others
        logger.error('campaign.dispatch.organization_failed', org_id=org_id, **_failure(exc))
        return {'send': Counter(errors=1)}


def run() -> dict:
    now = datetime.now(UTC)
    # An opt-out, not due work: a departed owner's stored opt-outs are invisible to each organization's beat, and the
    # write reaches every organization the owner left. No connection is open here, so the scope may change.
    departed_opt_outs = 0
    try:
        with system(SystemReason.OPT_OUT), get_conn() as conn:
            departed_opt_outs = due.exclude_opted_out_of_departed_owners(conn)
    except Exception as exc:  # a failing cleanup must not stop every organization's sends
        logger.error('campaign.dispatch.departed_opt_outs_failed', **_failure(exc))
    beats = asyncio.run(tasks.each_org(due_organizations(now), 'campaign beat', lambda org: _beat(org, now)))
    total: dict = {
        'gate': Counter(),
        'plan': Counter(),
        'send': Counter(),
        'completed': 0,
        'resumed': 0,
        'reminders': 0,
    }
    if departed_opt_outs:
        total['plan']['excluded_crm'] += departed_opt_outs
    for beat in beats:
        for key, value in beat.items():
            total[key] += value
    if any(total.values()):
        logger.info(
            'campaign.dispatch.done',
            **{f'gate_{k}': v for k, v in total['gate'].items()},
            **{f'plan_{k}': v for k, v in total['plan'].items()},
            **total['send'],
            completed=total['completed'],
            resumed=total['resumed'],
            reminders=total['reminders'],
        )
    return {key: dict(value) if isinstance(value, Counter) else value for key, value in total.items()}
