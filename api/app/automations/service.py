"""Automations (#939): the definition, its versions and the queue of its runs.

An automation belongs to a project and is shared like a list: every member of the organization sees it, its creator or
an admin manages it. The runs are durable rows claimed by ``app.automations.runner``, as for watchlists. A definition
edit bumps ``version``; a run keeps the definition it was queued with.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

from psycopg.types.json import Jsonb

from app.auth.middleware import CurrentUser
from app.automations import definition as checks
from app.automations import deliveries, hook, instantly_handoff, signal_trigger, triggers
from app.automations.discard import discard_unapproved_drafts
from app.automations.models import AutomationIn, AutomationPatch, Definition
from app.automations.run_queue import RUN_COLUMNS, insert_run
from app.automations.triggers import Trigger, TriggerKind
from app.billing.tab import close_stored
from app.core import tasks
from app.core.db import get_conn
from app.core.errors_http import api_error

# Per project: each run can spend up to its credit cap, so the number of automations bounds a project's spend.
MAX_PER_PROJECT = 10

STEP_COLUMNS = 'position, kind, status, decision, input, output, credits, attempts, error_code, started_at, finished_at'
_COLUMNS = (
    'a.id, a.organization_id, a.project_id, a.user_id, a.name, a.trigger_kind, a.cadence, a.next_run_at, a.enabled,'
    ' a.version, a.approved_version, a.credit_cap, a.definition, a.created_at, a.updated_at, a.watchlist_id,'
    ' a.signal_min_score, a.signals_since, w.name AS watchlist_name'
)
_SIGNAL_COLUMNS = frozenset({'watchlist_id', 'watchlist_name', 'signal_min_score', 'signals_since'})
_VIEW = (
    f'SELECT {_COLUMNS} FROM automations a LEFT JOIN watchlists w ON w.id = a.watchlist_id'
    ' WHERE a.organization_id = %s AND a.project_id = %s'
)


def _shown(automation: dict) -> dict:
    """The automation as the API shows it: its signal columns as one ``signal``, or None for another trigger."""
    shown = {k: v for k, v in automation.items() if k not in _SIGNAL_COLUMNS}
    shown['signal'] = (
        {
            'watchlist_id': automation['watchlist_id'],
            'watchlist_name': automation['watchlist_name'],
            'min_score': automation['signal_min_score'],
            'since': automation['signals_since'],
        }
        if automation['trigger_kind'] == 'signal'
        else None
    )
    return shown


def _with_last_run(conn, automations: list[dict]) -> list[dict]:
    newest = conn.execute(
        f'SELECT DISTINCT ON (automation_id) {RUN_COLUMNS} FROM automation_runs WHERE automation_id = ANY(%s)'
        ' ORDER BY automation_id, created_at DESC',
        ([a['id'] for a in automations],),
    ).fetchall()
    by_automation = {r['automation_id']: r for r in newest}
    webhooks = hook.summaries(conn, [a['id'] for a in automations])
    return [
        {**_shown(a), 'last_run': by_automation.get(a['id']), 'webhook': webhooks.get(a['id'])} for a in automations
    ]


def owned(conn, user: CurrentUser, project_id: str, automation_id, *, manage: bool = True, lock: bool = False) -> dict:
    """The automation as the API shows it. ``manage``: only its creator or an admin of the organization."""
    if lock:
        conn.execute('SELECT id FROM automations WHERE id = %s FOR UPDATE', (automation_id,))
    row = conn.execute(f'{_VIEW} AND a.id = %s', (user.org_id, project_id, automation_id)).fetchone()
    if not row:
        raise api_error(404, 'AUTOMATION.NOT_FOUND', 'automation not found')
    if manage and str(row['user_id']) != user.id and user.org_role != 'admin':
        raise api_error(403, 'AUTOMATION.FORBIDDEN', 'only who made the automation, or an admin')
    return _with_last_run(conn, [row])[0]


def list_all(user: CurrentUser, project_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(f'{_VIEW} ORDER BY a.created_at DESC', (user.org_id, project_id)).fetchall()
        return _with_last_run(conn, rows)


def _refuse_unusable(
    conn, user: CurrentUser, project_id: str, definition: Definition, creator_id, trigger: Trigger
) -> None:
    """Runs spend and write as the creator, so their membership and HubSpot grant are checked, not the editor's."""
    creator = str(creator_id) if creator_id else None
    if found := checks.problems(conn, user.org_id, project_id, creator, definition, trigger):
        raise api_error(*found[0])


def _next_slot(kind: str) -> datetime | None:
    return datetime.now(UTC) if kind == 'schedule' else None


def create(user: CurrentUser, project_id: str, body: AutomationIn) -> dict:
    with get_conn() as conn:
        # Serialises the per-project limit against concurrent creates.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        _refuse_unusable(conn, user, project_id, body.definition, user.id, body.trigger)
        count = conn.execute('SELECT count(*) AS n FROM automations WHERE project_id = %s', (project_id,)).fetchone()
        if count['n'] >= MAX_PER_PROJECT:
            raise api_error(409, 'AUTOMATION.LIMIT', f'at most {MAX_PER_PROJECT} automations per project')
        trigger = triggers.columns(body.trigger)
        made = conn.execute(
            'INSERT INTO automations (organization_id, project_id, user_id, name, trigger_kind, cadence, watchlist_id,'
            ' signal_min_score, signals_since, next_run_at, enabled, credit_cap, definition) VALUES (%(org)s,'
            ' %(project)s, %(user)s, %(name)s, %(trigger_kind)s, %(cadence)s, %(watchlist_id)s, %(signal_min_score)s,'
            ' %(since)s, %(next)s, %(enabled)s, %(cap)s, %(definition)s) RETURNING id',
            {
                **trigger,
                'org': user.org_id,
                'project': project_id,
                'user': user.id,
                'name': body.name.strip(),
                'since': _database_now(conn) if body.trigger.kind == 'signal' else None,
                'next': _next_slot(body.trigger.kind),
                'enabled': body.enabled,
                'cap': body.credit_cap,
                'definition': Jsonb(body.definition.model_dump(mode='json')),
            },
        ).fetchone()
        return owned(conn, user, project_id, made['id'])


def _cancel_queued(conn, automation_id, code: str) -> None:
    cancelled = conn.execute(
        "UPDATE automation_runs SET status = 'cancelled', finished_at = now(), error_code = %s"
        " WHERE automation_id = %s AND status = 'queued' RETURNING id",
        (code, automation_id),
    ).fetchall()
    for run in cancelled:
        signal_trigger.settle(conn, run['id'])


def _accounts_list(definition: dict) -> str | None:
    return next((s['accounts_list_id'] for s in definition['steps'] if s['type'] == 'find_people'), None)


def _changes(current: dict, body: AutomationPatch, now: datetime) -> tuple[dict, bool]:
    """The columns to write, and whether the edit is a new version: the definition or the cap changed."""
    changes: dict = {}
    if body.name is not None:
        changes['name'] = body.name.strip()
    if body.enabled is not None:
        changes['enabled'] = body.enabled
    if body.trigger is not None:
        changes |= triggers.columns(body.trigger)
        if body.trigger.kind == 'schedule' and current['trigger_kind'] != 'schedule':
            changes['next_run_at'] = datetime.now(UTC)
        elif body.trigger.kind != 'schedule':
            changes['next_run_at'] = None
    changes |= _signals_since(current, {**current, **changes}, now)
    versioned = False
    if body.definition is not None:
        stored = body.definition.model_dump(mode='json')
        if stored != current['definition']:
            changes['definition'], versioned = Jsonb(stored), True
            if _accounts_list(stored) != _accounts_list(current['definition']):
                changes['accounts_seq'] = 0  # another list: the find-people step starts from its first account
    if body.credit_cap is not None and body.credit_cap != current['credit_cap']:
        changes['credit_cap'], versioned = body.credit_cap, True
    return changes, versioned


def _new_watchlist(before: dict, after: dict) -> bool:
    return after['trigger_kind'] == 'signal' and (
        before['trigger_kind'] != 'signal' or after['watchlist_id'] != before['watchlist_id']
    )


def _database_now(conn) -> datetime:
    """Signals are first seen on the database clock, so ``signals_since`` must be too: a host clock ahead of it would
    skip the signals seen right after a publish or a resume."""
    return conn.execute('SELECT now() AS now').fetchone()['now']


def _signals_since(before: dict, after: dict, now: datetime) -> dict:
    """Only signals first seen once the trigger names its watchlist start runs, and none that came while the automation
    was paused. A new count or a new minimum clears the last empty evaluation (``signal_trigger``): a lower minimum can
    make an account ready with no new signal."""
    if after['trigger_kind'] != 'signal':
        cleared = dict.fromkeys(('signals_since', 'signals_checked_at', 'signals_tried_at'))
        return cleared if before['signals_since'] else {}
    if _new_watchlist(before, after) or (after['enabled'] and not before['enabled']):
        return {'signals_since': now, 'signals_checked_at': None}
    if after['signal_min_score'] != before['signal_min_score']:
        return {'signals_checked_at': None}
    return {}


def _left_its_watchlist(before: dict, after: dict) -> bool:
    """A signal run queued for the old watchlist's account: an edit of the trigger makes no new version, so the
    edit cancels it."""
    return before['trigger_kind'] == 'signal' and (
        after['trigger_kind'] != 'signal' or after['watchlist_id'] != before['watchlist_id']
    )


def update(user: CurrentUser, project_id: str, automation_id, body: AutomationPatch) -> dict:
    with get_conn() as conn:
        owned(conn, user, project_id, automation_id, lock=True)
        current = conn.execute('SELECT * FROM automations WHERE id = %s', (automation_id,)).fetchone()
        changes, versioned = _changes(current, body, _database_now(conn))
        # What the automation would be after the edit: an enabled one is checked, so it never runs unchecked.
        after = body.definition or Definition.model_validate(current['definition'])
        trigger = body.trigger or triggers.of_row(current)
        named = body.trigger or body.definition
        if named and (found := checks.trigger_problems(conn, user.org_id, project_id, trigger, after)):
            raise api_error(*found[0])
        if changes.get('enabled', current['enabled']):
            _refuse_unusable(conn, user, project_id, after, current['user_id'], trigger)
        if changes:
            if versioned:
                changes['version'] = current['version'] + 1
            # Column names come from this module, never from the request.
            sets = ', '.join(f'{column} = %s' for column in changes)
            conn.execute(
                f'UPDATE automations SET {sets}, updated_at = now() WHERE id = %s', (*changes.values(), automation_id)
            )
            if 'signals_since' in changes:
                signal_trigger.count_again(conn, automation_id)
        if changes.get('enabled') is False:
            _cancel_queued(conn, automation_id, 'automation_paused')
        elif versioned or _left_its_watchlist(current, {**current, **changes}):
            _cancel_queued(conn, automation_id, 'automation_edited')
        if versioned:
            discard_unapproved_drafts(conn, automation_id)
        deliveries.automation_changed(conn, current, {**current, **changes})
        return owned(conn, user, project_id, automation_id)


def delete(user: CurrentUser, project_id: str, automation_id) -> None:
    with get_conn() as conn:
        owned(conn, user, project_id, automation_id, lock=True)
        # A run in flight loses its row with the automation: give its hold back now, not when the reaper finds it.
        for run in conn.execute(
            "SELECT reservation_id FROM automation_runs WHERE automation_id = %s AND status = 'running'"
            ' AND reservation_id IS NOT NULL',
            (automation_id,),
        ).fetchall():
            close_stored(conn, str(run['reservation_id']))
        conn.execute('DELETE FROM automations WHERE id = %s', (automation_id,))


def enqueue(conn, automation: dict, when: datetime, trigger_kind: TriggerKind) -> dict | None:
    """The automation's live run, or a new queued one pinned to its current version. The caller holds its lock."""
    live = conn.execute(
        f"SELECT {RUN_COLUMNS} FROM automation_runs WHERE automation_id = %s AND status IN ('queued', 'running')",
        (automation['id'],),
    ).fetchone()
    return live or insert_run(conn, automation, when, trigger_kind)


def run_now(user: CurrentUser, project_id: str, automation_id) -> dict:
    with get_conn() as conn:
        automation = owned(conn, user, project_id, automation_id, lock=True)
        if automation['trigger_kind'] == 'webhook':
            raise api_error(409, 'AUTOMATION.WEBHOOK_TRIGGERED', 'a webhook automation runs on its signed calls only')
        if automation['trigger_kind'] == 'signal':
            raise api_error(
                409, 'AUTOMATION.SIGNAL_TRIGGERED', "a signal automation runs on its watchlist's signals only"
            )
        if not automation['enabled']:
            raise api_error(409, 'AUTOMATION.PAUSED', 'resume the automation first')
        queued = enqueue(conn, automation, datetime.now(UTC), 'manual')
        if queued is None:  # a run of this very instant exists: it is the live one
            queued = conn.execute(
                f'SELECT {RUN_COLUMNS} FROM automation_runs WHERE automation_id = %s ORDER BY created_at DESC LIMIT 1',
                (automation_id,),
            ).fetchone()
        return queued


def runs(user: CurrentUser, project_id: str, automation_id) -> list[dict]:
    with get_conn() as conn:
        owned(conn, user, project_id, automation_id, manage=False)
        return conn.execute(
            f'SELECT {RUN_COLUMNS} FROM automation_runs WHERE automation_id = %s ORDER BY created_at DESC LIMIT 100',
            (automation_id,),
        ).fetchall()


def run_detail(user: CurrentUser, project_id: str, automation_id, run_id) -> dict:
    with get_conn() as conn:
        owned(conn, user, project_id, automation_id, manage=False)
        run = conn.execute(
            f'SELECT {RUN_COLUMNS} FROM automation_runs WHERE id = %s AND automation_id = %s', (run_id, automation_id)
        ).fetchone()
        if not run:
            raise api_error(404, 'AUTOMATION.RUN_NOT_FOUND', 'run not found')
        steps = conn.execute(
            f'SELECT {STEP_COLUMNS} FROM automation_run_steps WHERE run_id = %s ORDER BY position', (run_id,)
        ).fetchall()
        return {
            **run,
            'steps': steps,
            'delivery': deliveries.of_run(conn, run_id),
            'instantly': instantly_handoff.view(conn, run_id),
            'signal': signal_trigger.of_run(conn, run_id),
        }


def issue_secret(user: CurrentUser, project_id: str, automation_id) -> dict:
    """A new secret for the automation's hook (``hook.rotate``): only its creator or an admin."""
    with get_conn() as conn:
        return hook.rotate(conn, owned(conn, user, project_id, automation_id, lock=True))


def cancel_run(user: CurrentUser, project_id: str, automation_id, run_id) -> dict:
    """A queued run ends at once. A running one stops at its next store: the step that is going stores nothing and is
    skipped, and the steps after it do not run. A HubSpot step that wrote before the stop keeps its record
    (``runner.AFTER_STOP``)."""
    with get_conn() as conn:
        owned(conn, user, project_id, automation_id, lock=True)
        run = conn.execute(
            'SELECT id, status FROM automation_runs WHERE id = %s AND automation_id = %s FOR UPDATE',
            (run_id, automation_id),
        ).fetchone()
        if not run:
            raise api_error(404, 'AUTOMATION.RUN_NOT_FOUND', 'run not found')
        if run['status'] == 'queued':
            conn.execute(
                "UPDATE automation_runs SET status = 'cancelled', error_code = 'cancelled', finished_at = now()"
                ' WHERE id = %s',
                (run_id,),
            )
            signal_trigger.settle(conn, run_id)
        elif run['status'] == 'running':
            conn.execute('UPDATE automation_runs SET cancel_requested = true WHERE id = %s', (run_id,))
    return run_detail(user, project_id, automation_id, run_id)


# Due, with a creator who is a live member of the automation's organization: a deleted, removed or banned creator's
# automations wait, and a return or an unban resumes them.
_DUE_SQL = "a.enabled AND a.trigger_kind = 'schedule' AND a.next_run_at <= now()"


def schedule_due() -> list[tuple[str, str, str]]:
    """Queue a run for every scheduled automation that is due and move it to its next slot, as its creator.
    ``(organization, creator, run id)`` of every queued run. Called in the beat's System scope, which only selects."""
    with get_conn() as conn:
        due = conn.execute(
            f'SELECT a.id, a.organization_id, a.user_id FROM automations a WHERE {_DUE_SQL}'
            ' AND (a.user_id, a.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members)'
            ' ORDER BY a.next_run_at LIMIT 100'
        ).fetchall()
    for automation in due:
        tasks.for_member(
            str(automation['organization_id']), str(automation['user_id']), partial(_schedule, automation['id'])
        )
    with get_conn() as conn:
        queued = conn.execute(
            'SELECT r.id, r.organization_id, a.user_id FROM automation_runs r JOIN automations a ON a.id = r.automation_id'
            " WHERE r.status = 'queued' AND a.user_id IS NOT NULL ORDER BY r.created_at LIMIT 100"
        ).fetchall()
    return [(str(r['organization_id']), str(r['user_id']), str(r['id'])) for r in queued]


def _schedule(automation_id) -> None:
    with get_conn() as conn:
        automation = conn.execute(
            f'SELECT a.* FROM automations a WHERE a.id = %s AND {_DUE_SQL} FOR UPDATE SKIP LOCKED', (automation_id,)
        ).fetchone()
        if automation is None:  # another beat has it, or it changed since the selection
            return
        enqueue(conn, automation, automation['next_run_at'], 'schedule')
        step = timedelta(days=1 if automation['cadence'] == 'daily' else 7)
        # The next slot after now, on the same weekday and hour: a missed week is not run twice.
        missed = int((datetime.now(UTC) - automation['next_run_at']) / step) + 1
        conn.execute(
            'UPDATE automations SET next_run_at = %s WHERE id = %s',
            (automation['next_run_at'] + step * missed, automation['id']),
        )
