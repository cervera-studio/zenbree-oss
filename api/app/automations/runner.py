"""One run of an automation (#939): claimed once with its credits held, then its steps one at a time.

The shape of ``app.watchlists.runner`` and ``app.scheduling.runner``. The claim moves a queued run to ``running`` and
holds the most it may spend on one ``Tab``. Each step calls its vendors, then stores its outcome, its charges and its
own record in one transaction (``run.guarded``). Only ``write_hubspot`` writes in its vendor half: when a cancel or a
pause stops the run after that write, the step still records who it wrote (``AFTER_STOP``). A step that wrote nothing
and failed is tried again, at most three times with a longer wait each time; one that wrote or charged is never
replayed by itself. A worker that
dies leaves the run ``running``: the reaper closes it ``unknown``, marks the step that was going ``uncertain`` and
settles the hold from the charges the steps recorded, so nothing is charged twice and nothing is half written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from psycopg.types.json import Jsonb

from app.auth.middleware import creator_not_banned
from app.automations import gate, hubspot, instantly_handoff, instantly_step, signal_trigger, steps, triggers
from app.automations.definition import problems
from app.automations.discard import discard_unapproved_drafts
from app.automations.models import Definition
from app.automations.run import Outcome, Run, StepFailed, Stopped, guarded
from app.billing import balance, credits
from app.billing.paid_call import current_user_id
from app.billing.tab import HoldGone, Tab, close_stored
from app.core.db import get_conn
from app.core.scope import ActorGone, acting_for

logger = structlog.get_logger()

STALE_AFTER = timedelta(minutes=17)  # the task's hard limit is 16; the credit reaper starts at 20
MAX_ATTEMPTS = 3
BACKOFF = (2.0, 6.0)  # seconds before the second and the third attempt
# Each step: its vendor calls, then its one transaction.
STEPS: dict[str, tuple[Callable[..., Awaitable], Callable]] = {
    'find_people': (steps.fetch_people, steps.apply_people),
    'qualify': (steps.fetch_qualify, steps.apply_qualify),
    'find_emails': (steps.fetch_emails, steps.apply_emails),
    'add_to_campaign': (steps.fetch_nothing, steps.apply_campaign),
    'add_to_instantly': (instantly_step.fetch_instantly, instantly_step.apply_instantly),
    'write_hubspot': (hubspot.fetch_hubspot, hubspot.apply_hubspot),
}
# A step that writes outside our database in its vendor half: what it did when the run stopped after that write.
AFTER_STOP: dict[str, Callable[[Run, Any], Outcome | None]] = {'write_hubspot': hubspot.stopped_hubspot}
# How a run ends when a step may not write any more.
_STOPPED = {
    'cancelled': ('cancelled', 'cancelled'),
    'automation_paused': ('cancelled', 'automation_paused'),
    'reservation_expired': ('unknown', 'reservation_expired'),
    'run_not_running': ('unknown', 'run_not_running'),
}


def _locked(conn, run_id) -> tuple[dict | None, dict | None]:
    row = conn.execute('SELECT automation_id FROM automation_runs WHERE id = %s', (run_id,)).fetchone()
    if not row:
        return None, None
    automation = conn.execute('SELECT * FROM automations WHERE id = %s FOR UPDATE', (row['automation_id'],)).fetchone()
    return automation, conn.execute('SELECT * FROM automation_runs WHERE id = %s FOR UPDATE', (run_id,)).fetchone()


def _stop(conn, run: dict, status: str, code: str) -> None:
    if run['reservation_id']:
        close_stored(conn, str(run['reservation_id']))
    conn.execute(
        'UPDATE automation_runs SET status = %s, error_code = %s, finished_at = now() WHERE id = %s',
        (status, code, run['id']),
    )
    _end_steps(conn, run['id'], 0, 'run_ended' if status in ('unknown', 'failed') else code)
    discard_unapproved_drafts(conn, run['automation_id'])
    signal_trigger.settle(conn, run['id'])


def _end_steps(conn, run_id, from_position: int, decision: str) -> None:
    """Steps that did not run are skipped; one that was going when the run ended is ``uncertain``: what it did outside
    our database is not known."""
    conn.execute(
        "UPDATE automation_run_steps SET status = CASE WHEN status = 'running' AND %s = 'run_ended' THEN 'uncertain'"
        " ELSE 'skipped' END, decision = %s, finished_at = now() WHERE run_id = %s AND position >= %s"
        " AND status IN ('pending', 'running')",
        (decision, decision, run_id, from_position),
    )


def _refusal(conn, automation: dict) -> tuple[str, str] | None:
    """Why this automation may not run now, as ``(status, code)``."""
    if not automation['enabled']:
        return 'cancelled', 'automation_paused'
    if not automation['user_id']:
        return 'cancelled', 'creator_deleted'
    if not conn.execute(f'SELECT {creator_not_banned("%s")} AS ok', (automation['user_id'],)).fetchone()['ok']:
        return 'cancelled', 'user_banned'
    if not conn.execute(
        'SELECT 1 FROM member WHERE "userId" = %s AND "organizationId" = %s',
        (automation['user_id'], automation['organization_id']),
    ).fetchone():
        return 'failed', 'creator_left'  # the run would spend an organization's credits as a stranger
    return None


def claim(run_id) -> Run | None:
    """Take a queued run, as the automation's creator, with the most it may spend held. ``None`` when the run is not
    to be done (its row says why). A creator who left or was banned since the run was queued gets ``ActorGone``: the
    organization then closes the run, so the beat does not queue it again."""
    try:
        return _claim(run_id)
    except ActorGone as gone:
        with acting_for(gone.scope.org, 'run of a creator who left'), get_conn() as conn:
            automation, run = _locked(conn, run_id)
            # Only the gone creator's own run: a forged header must not close a teammate's.
            if run and automation and run['status'] == 'queued' and str(automation['user_id']) == gone.scope.user:
                _stop(conn, run, *(_refusal(conn, automation) or ('failed', 'creator_left')))
        raise


def _claim(run_id) -> Run | None:
    refused_org = None
    with get_conn() as conn:
        automation, run = _locked(conn, run_id)
        if not run or not automation or run['status'] != 'queued':
            return None
        if refusal := _refusal(conn, automation):
            _stop(conn, run, *refusal)
            return None
        org, user, project = str(run['organization_id']), str(automation['user_id']), str(automation['project_id'])
        definition = Definition.model_validate(run['definition'])
        # The automation's trigger now: its watchlist can go away or pause after the run was queued.
        if problems(conn, org, project, user, definition, triggers.of_row(automation)):
            _stop(conn, run, 'failed', 'invalid_configuration')  # a list or a campaign went away, or HubSpot was cut
            return None
        tab: Tab | None = None
        if definition.spends:
            # Up to the cap, at least one credit: a wallet with less than the cap makes a smaller run.
            opened = Tab.open(
                conn,
                credits.Payer(org, user, project),
                run['credit_cap'],
                need=1,
                kind='automation',
                reference_id=str(run_id),
                description='Automation run',
            )
            if isinstance(opened, Tab):
                tab = opened
            else:
                _stop(conn, run, 'failed', 'project_cap_reached' if opened.capped else 'insufficient_credits')
                refused_org = org
        if refused_org is None:
            conn.execute(
                'INSERT INTO automation_run_steps (organization_id, run_id, position, kind)'
                " SELECT %s, %s, i - 1, s ->> 'type' FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY AS t(s, i)",
                (org, run_id, Jsonb(run['definition']['steps'])),
            )
            conn.execute(
                "UPDATE automation_runs SET status = 'running', reservation_id = %s, review = %s, started_at = now()"
                ' WHERE id = %s',
                (tab.hold.id if tab else None, automation['approved_version'] != run['version'], run_id),
            )
            return Run(
                id=str(run_id),
                org_id=org,
                user_id=user,
                project_id=project,
                automation_id=str(automation['id']),
                definition=definition,
                version=run['version'],
                review=automation['approved_version'] != run['version'],
                credit_cap=run['credit_cap'],
                hold_id=tab.hold.id if tab else None,
                budget=tab.hold.amount if tab else 0,
                target_row_id=str(run['target_row_id']) if run['target_row_id'] else None,
                target_account_id=str(run['target_account_id']) if run['target_account_id'] else None,
            )
    balance.changed(refused_org, refused=True)  # credits short: auto top-up may be due
    return None


def _step_row(run: Run, position: int, **columns) -> None:
    sets = ', '.join(f'{c} = %s' for c in columns)
    with get_conn() as conn:
        conn.execute(
            f'UPDATE automation_run_steps SET {sets} WHERE run_id = %s AND position = %s',
            (*columns.values(), run.id, position),
        )


def _begin(run: Run, position: int, attempt: int) -> None:
    """The step is going: checked as a write is, so a pause or a cancel stops it before its next try."""
    with guarded(run) as (conn, _):
        conn.execute(
            "UPDATE automation_run_steps SET status = 'running', attempts = %s, started_at = coalesce(started_at, now()),"
            ' input = %s WHERE run_id = %s AND position = %s',
            (attempt, Jsonb({'people': len(run.row_ids), 'credits_left': run.remaining}), run.id, position),
        )


def _failed(run: Run, position: int, failure: StepFailed) -> str:
    _step_row(
        run,
        position,
        status='failed',
        error_code=failure.code,
        output=Jsonb(failure.output),
        decision='failed' if failure.retry else 'failed, not tried again',
        finished_at=datetime.now(UTC),
    )
    with get_conn() as conn:
        _end_steps(conn, run.id, position + 1, 'previous_step_failed')
    return failure.code


async def _step(run: Run, position: int, step) -> str | None:
    """Run one step: its failure code, or ``None``. ``Stopped`` when the run may not go on."""
    fetch, apply = STEPS[step.type]
    fetched = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _begin(run, position, attempt)
        try:
            fetched = await fetch(run, step, position)
            break
        except StepFailed as failure:
            if not failure.retry or attempt == MAX_ATTEMPTS:
                return _failed(run, position, failure)
        except Stopped, ActorGone:
            raise
        except Exception as exc:  # a vendor client's own failure: nothing was stored, so it may be tried again
            logger.error('automation.step_crashed', run_id=run.id, kind=step.type, error_type=type(exc).__name__)
            if attempt == MAX_ATTEMPTS:
                return _failed(run, position, StepFailed('step_crashed'))
        await asyncio.sleep(BACKOFF[attempt - 1])
    try:
        # A database error here propagates: the step stays ``running`` and the reaper marks it uncertain.
        with guarded(run) as (conn, tab):
            outcome: Outcome = apply(conn, tab, run, step, position, fetched)
            conn.execute(
                "UPDATE automation_run_steps SET status = 'succeeded', decision = %s, output = %s, credits = %s,"
                ' finished_at = now() WHERE run_id = %s AND position = %s',
                (outcome.decision, Jsonb(outcome.output), outcome.credits, run.id, position),
            )
    except StepFailed as failure:
        return _failed(run, position, failure)
    except Stopped:
        if (after := AFTER_STOP.get(step.type)) and (wrote := after(run, fetched)):
            _wrote_before_stop(run, position, step.type, wrote)
        raise
    run.spent += outcome.credits
    run.unreached |= set(outcome.unreached)
    keep = run.row_ids if outcome.keep is None else outcome.keep
    run.row_ids = [i for i in keep if i not in run.unreached]
    run.summary[step.type] = outcome.output
    return None


def _wrote_before_stop(run: Run, position: int, kind: str, outcome: Outcome) -> None:
    """The step's record when the run stopped after it wrote outside our database. A run the reaper closed keeps the
    step ``uncertain``."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE automation_run_steps SET status = 'succeeded', decision = %s, output = %s, finished_at = now()"
            " WHERE run_id = %s AND position = %s AND status = 'running'",
            (outcome.decision, Jsonb(outcome.output), run.id, position),
        )
    run.summary[kind] = outcome.output


def _take(run: Run) -> bool:
    """The people this run works on, once the find-people step (if any) has added its own."""
    with guarded(run) as (conn, _):
        kept, refused, nobody = gate.take(conn, run)
    run.taken = run.row_ids = kept
    run.summary['taken'] = {'people': len(kept), 'refused_before_any_credit': dict(refused)}
    if run.target_account_id and not kept:
        # The run used its account's signals: say why they started nothing.
        run.summary['taken']['nobody'] = 'all_refused' if refused else nobody
    return bool(kept)


def _skip(run: Run, from_position: int, decision: str) -> None:
    with get_conn() as conn:
        _end_steps(conn, run.id, from_position, decision)


async def _execute(run: Run) -> tuple[str, str | None]:
    """Every step in order: ``(status, error code)`` of the run."""
    taken = False
    for position, step in enumerate(run.definition.steps):
        try:
            if step.type != 'find_people' and not taken:
                taken = True
                if not _take(run):
                    _skip(run, position, 'no_new_people')
                    return 'succeeded', None
            if step.type != 'find_people' and not run.row_ids:
                _skip(run, position, 'no_people_left')
                return 'succeeded', None
            if failure := await _step(run, position, step):
                return 'failed', failure
        except Stopped as stopped:
            _skip(run, position, stopped.code)
            return _STOPPED[stopped.code]
    return 'succeeded', None


def finish(run: Run, status: str, code: str | None) -> None:
    """Close the run in one transaction: the people it took in, the hold settled from its charges, its summary."""
    with get_conn() as conn:
        _, row = _locked(conn, run.id)
        if not row or row['status'] != 'running':
            return
        spent = 0
        if run.hold_id:
            tab = Tab.stored(conn, run.hold_id)
            try:
                tab.lock(conn)  # until this commit: nothing closes the hold under the charges
                spent = tab.close(conn)
            except HoldGone:
                _stop(conn, row, 'unknown', 'reservation_expired')
                return
        if status == 'succeeded':
            # Each person is worked on once: a later run takes the ones this run did not reach, and no other.
            done = [i for i in run.taken if i not in run.unreached]
            conn.execute(
                'INSERT INTO automation_rows (automation_id, row_id, organization_id, run_id)'
                ' SELECT %s, id, %s, %s FROM unnest(%s::uuid[]) id ON CONFLICT DO NOTHING',
                (run.automation_id, run.org_id, run.id, done),
            )
            instantly_handoff.record(conn, run)
            code = code or ('credit_cap_reached' if run.cap_hit else None)
        conn.execute(
            'UPDATE automation_runs SET status = %s, error_code = %s, credits_spent = %s, summary = %s,'
            ' finished_at = now() WHERE id = %s',
            (status, code, spent, Jsonb({**run.summary, 'credits_left': run.remaining}), run.id),
        )
        discard_unapproved_drafts(conn, run.automation_id)
        signal_trigger.settle(conn, run.id)


async def run_automation(run_id) -> None:
    run = claim(run_id)
    if not run:
        return
    if run.hold_id:
        balance.changed(run.org_id)  # the hold
    try:
        current_user_id.set(run.user_id)
        status, code = await _execute(run)
        finish(run, status, code)
        instantly_handoff.after_run(run)
    except ActorGone:  # the creator left mid-run: nothing more is written, and the organization gets its hold back
        with acting_for(run.org_id, 'run of a creator who left'), get_conn() as conn:
            _, row = _locked(conn, run.id)
            if row and row['status'] == 'running':
                _stop(conn, row, 'failed', 'creator_left')
        raise
    finally:
        if run.hold_id:
            balance.changed(run.org_id)  # settled or released


def maintain() -> None:
    """A run whose worker died: its outcome is unknown, its hold goes back. A queued run whose creator deleted their
    account has nobody to run as: ``creator_deleted``. Called in the beat's System scope, which only selects: each
    run is closed as its organization."""
    with get_conn() as conn:
        stale = conn.execute(
            'SELECT r.id, r.organization_id FROM automation_runs r JOIN automations a ON a.id = r.automation_id'
            " WHERE r.status IN ('queued', 'running')"
            " AND (r.started_at < %s OR (r.status = 'queued' AND a.user_id IS NULL))",
            (datetime.now(UTC) - STALE_AFTER,),
        ).fetchall()
    for row in stale:
        with acting_for(str(row['organization_id']), 'dead automation run'):
            _reap(row['id'])


def _reap(run_id) -> None:
    with get_conn() as conn:
        automation, run = _locked(conn, run_id)
        if run and automation and run['status'] == 'queued' and automation['user_id'] is None:
            _stop(conn, run, 'cancelled', 'creator_deleted')
            return
        if not (run and run['status'] == 'running' and run['started_at'] < datetime.now(UTC) - STALE_AFTER):
            return
        _stop(conn, run, 'unknown', 'worker_outcome_unknown')
    if automation:
        balance.changed(str(automation['organization_id']))
