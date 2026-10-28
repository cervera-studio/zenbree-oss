"""The signal trigger of automations (#1030): the beat starts one run for each account of the watchlist whose signals,
first seen since ``signals_since`` and not used by the automation yet, add up to its minimum score. The score is the
#648 campaign link's: ``signal_score``, which halves every 30 days.

``automation_signals`` is the ledger of the signals each automation holds or used, and this module is its only
writer. A run and the ledger rows of its signals are inserted in one transaction, under the automation's lock, so a
second beat or a crash never starts a second run for a signal. The run holds its signals until it takes its people
(``took``), and then they are used, whatever its end. A run that takes nobody because nobody is at the account used
them too, and its summary says why. A run that ends before it takes anyone settles them by how it ended (``settle``):
a failure gives them back and its account waits (``automation_signal_waits``), and the account's
``GIVE_UP_AFTER``-th failure in a row uses them. ``signals.enrolled_at`` is the campaign link's, never written here.

An account's sum only rises when a new signal arrives: decay and the ledger only lower it. So an evaluation that finds
no ready account stamps ``signals_checked_at``, and the beat evaluates the automation again only once a signal of its
watchlist is first seen after that stamp (less ``LATE_COMMIT``) or an account's wait ends after it, or once
``signals_since`` or ``min_score`` changes, which clear the stamp. Not handled: an account row that a rule stops
excluding is seen at the next new signal of the watchlist.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import structlog
from psycopg.types.json import Jsonb

from app.automations import definition as checks
from app.automations.discard import WAITING_FOR_APPROVAL
from app.automations.models import Definition
from app.automations.run_queue import insert_run
from app.automations.triggers import SignalTrigger, of_row
from app.billing import credits
from app.core.db import get_conn
from app.core.scope import acting_for
from app.lists.people_match import PeopleIndex
from app.lists.rows import norm_host

logger = structlog.get_logger()

AUTOMATIONS_PER_TICK = 100
PREVIEW_WINDOW = '30 days'
PREVIEW_ACCOUNTS = 5

_LIVE_SCORE = 'signal_score(s.score, s.occurred_at)'
# A run that failed before it took anyone gives its signals back, and the next one for its account would most likely
# fail the same way (a vendor that stays down, a list that went away): the account waits this long. After this many
# failed runs in a row the failure is taken as lasting, and the account's signals count as used, so they stop costing
# vendor calls; a new signal starts it again.
RETRY_AFTER = '15 minutes'
GIVE_UP_AFTER = 3
# A signal's first_seen_at is when its watchlist run inserted it, and the run commits a little later, so a signal that
# commits after an evaluation can be stamped before it. This is far longer than that insert-to-commit time.
LATE_COMMIT = '10 minutes'


def _signals(automation: str, watchlist: str, since: str) -> str:
    """The signals of the watchlist first seen since ``since`` that the automation has not used, at an account row of
    the watchlist's list that no rule excludes. Each argument is a SQL expression."""
    return (
        'signals s JOIN watchlists w ON w.id = s.watchlist_id'
        ' JOIN list_rows r ON r.id = s.row_id AND r.list_id = w.list_id AND NOT r.excluded'
        f' WHERE s.watchlist_id = {watchlist} AND s.first_seen_at >= {since} AND NOT EXISTS (SELECT 1'
        f' FROM automation_signals u WHERE u.automation_id = {automation} AND u.signal_id = s.id)'
    )


# May start a run now. Expects the automation aliased ``a`` and its watchlist ``w``.
_STARTABLE = (
    "a.enabled AND a.trigger_kind = 'signal' AND w.enabled AND zb.is_live_member(a.user_id, a.organization_id)"
    " AND NOT EXISTS (SELECT 1 FROM automation_runs l WHERE l.automation_id = a.id AND l.status IN ('queued', 'running'))"
    f' AND NOT ({WAITING_FOR_APPROVAL})'
)
_STARTABLE_AUTOMATIONS = (
    'SELECT a.id::text AS automation_id, a.organization_id::text AS organization_id, a.signals_tried_at,'
    f' a.signals_since FROM automations a JOIN watchlists w ON w.id = a.watchlist_id WHERE {_STARTABLE}'
)
# Automations that may start and have a signal, or an account whose wait ended, that their last evaluation did not see.
# A UNION, not an OR: an OR keeps Postgres from reading the signals through their index (40000 signals: 0.3 ms
# against 30 ms).
_DUE = (
    f'{_STARTABLE_AUTOMATIONS} AND EXISTS (SELECT 1 FROM signals s WHERE s.watchlist_id = a.watchlist_id'
    f" AND s.first_seen_at >= greatest(a.signals_since, a.signals_checked_at - interval '{LATE_COMMIT}'))"
    f' UNION {_STARTABLE_AUTOMATIONS} AND EXISTS (SELECT 1 FROM automation_signal_waits x'
    ' WHERE x.automation_id = a.id AND x.retry_at <= now() AND x.retry_at > a.signals_checked_at)'
    ' ORDER BY signals_tried_at NULLS FIRST, signals_since LIMIT %s'
)
_NEXT_ACCOUNT = (
    f'SELECT s.row_id FROM {_signals("%(automation)s", "%(watchlist)s", "%(since)s")}'
    ' AND NOT EXISTS (SELECT 1 FROM automation_signal_waits x WHERE x.automation_id = %(automation)s'
    ' AND x.account_id = s.row_id AND x.retry_at > now())'
    f' GROUP BY s.row_id HAVING sum({_LIVE_SCORE}) >= %(min)s ORDER BY min(s.first_seen_at), s.row_id LIMIT 1'
)
_USE = (
    'INSERT INTO automation_signals (automation_id, signal_id, organization_id, run_id, score)'
    f' SELECT %(automation)s, s.id, s.organization_id, %(run)s, {_LIVE_SCORE}'
    f' FROM {_signals("%(automation)s", "%(watchlist)s", "%(since)s")} AND s.row_id = %(account)s'
)


def pickup() -> int:
    """Start one run for each automation with a new signal and a ready account: how many started. Called in the
    beat's System scope, which only selects: each automation is worked on as its organization."""
    with get_conn() as conn:
        due = conn.execute(_DUE, (AUTOMATIONS_PER_TICK,)).fetchall()
    return sum(_as_its_organization(each, start_next) for each in due)


def _as_its_organization(head: dict, work: Callable[[str], bool]) -> bool:
    # One automation that fails must not stop the others, nor the schedules the beat queues after this.
    try:
        with acting_for(head['organization_id'], 'automation signal trigger'):
            return work(head['automation_id'])
    except Exception as exc:
        logger.error(
            'automation.signal_pickup_failed', automation_id=head['automation_id'], error_type=type(exc).__name__
        )
        return False


def start_next(automation_id: str) -> bool:
    """Queue a run for the automation's next ready account and use its signals, in one transaction, or stamp that
    none is ready: whether one started. An automation with a problem starts nothing and is not stamped, so its signals
    wait for the fix instead of failing runs."""
    with get_conn() as conn:
        # A watchlist delete locks the watchlist, its signals, then this automation (ON DELETE SET NULL). The pickup
        # takes the watchlist first too, so one waits for the other instead of a deadlock over the signals.
        named = conn.execute('SELECT watchlist_id FROM automations WHERE id = %s', (automation_id,)).fetchone()
        if named is None or named['watchlist_id'] is None:
            return False
        conn.execute('SELECT 1 FROM watchlists WHERE id = %s FOR KEY SHARE', (named['watchlist_id'],))
        automation = conn.execute(
            'SELECT * FROM automations WHERE id = %s FOR UPDATE SKIP LOCKED', (automation_id,)
        ).fetchone()
        # An edit or another beat has it, or its watchlist changed since the read above: the next tick.
        if automation is None or automation['watchlist_id'] != named['watchlist_id']:
            return False
        conn.execute('UPDATE automations SET signals_tried_at = now() WHERE id = %s', (automation_id,))
        startable = conn.execute(
            f'SELECT {_STARTABLE} AS ok FROM automations a JOIN watchlists w ON w.id = a.watchlist_id WHERE a.id = %s',
            (automation_id,),
        ).fetchone()
        if not (startable and startable['ok']):
            return False
        creator = str(automation['user_id']) if automation['user_id'] else None
        definition = Definition.model_validate(automation['definition'])
        trigger = of_row(automation)
        org, project = str(automation['organization_id']), str(automation['project_id'])
        if checks.problems(conn, org, project, creator, definition, trigger):
            return False
        # The claim would refuse the run for want of credits: the signals wait for a top-up, which starts no evaluation.
        if definition.spends and not credits.can_hold(conn, credits.Payer(org, str(creator), project), 1):
            return False
        params = {
            'automation': automation_id,
            'watchlist': automation['watchlist_id'],
            'since': automation['signals_since'],
            'min': automation['signal_min_score'],
        }
        account = conn.execute(_NEXT_ACCOUNT, params).fetchone()
        if account is None:
            conn.execute('UPDATE automations SET signals_checked_at = now() WHERE id = %s', (automation_id,))
            return False
        run = insert_run(conn, automation, None, 'signal', target_account_id=str(account['row_id']))
        if run is None:
            return False
        conn.execute(_USE, params | {'run': run['id'], 'account': account['row_id']})
        return True


_TOOK = """
WITH used AS (
    UPDATE automation_signals SET used_at = now() WHERE run_id = %(run)s AND used_at IS NULL
)
DELETE FROM automation_signal_waits x USING automation_runs r
WHERE r.id = %(run)s AND x.automation_id = r.automation_id AND x.account_id = r.target_account_id"""


def took(conn, run_id) -> None:
    """The run took its people, or its signals are used for another reason (``settle``): they count no more, and its
    account's failed runs are forgotten."""
    conn.execute(_TOOK, {'run': run_id})


_FAILED = f"""
INSERT INTO automation_signal_waits (automation_id, account_id, organization_id, failed_runs, retry_at)
SELECT automation_id, target_account_id, organization_id, 1, now() + interval '{RETRY_AFTER}'
FROM automation_runs WHERE id = %s
ON CONFLICT (automation_id, account_id) DO UPDATE
SET failed_runs = automation_signal_waits.failed_runs + 1, retry_at = excluded.retry_at
RETURNING failed_runs"""


_GIVE_BACK_AND_MARK = """
WITH back AS (DELETE FROM automation_signals WHERE run_id = %(run)s AND used_at IS NULL RETURNING 1)
UPDATE automation_runs SET summary = summary || %(given_back)s WHERE id = %(run)s AND EXISTS (SELECT 1 FROM back)"""


def settle(conn, run_id) -> None:
    """Settle the signals a run still holds once its end is written. Called from every place that ends a run; it does
    nothing to a run that holds none (it took its people, or is of another trigger), so a second call changes
    nothing.

    - Succeeded, or cancelled by the user: used. A cancel says "not for these", and a run started again for them would
      undo it.
    - Cancelled for another reason (an edit, a pause, the creator): given back, with no wait. Only a new version
      keeps ``signals_since``, so only then does the next beat count them again.
    - Failed or lost by its worker: given back, and the account waits ``RETRY_AFTER``. At the account's
      ``GIVE_UP_AFTER``-th such run in a row they are used instead, and the run's summary says so.

    A run that gives signals back says so in its summary too, since a deleted signal also leaves its run with none."""
    run = conn.execute(
        'SELECT r.status, r.error_code FROM automation_runs r WHERE r.id = %s AND EXISTS (SELECT 1'
        ' FROM automation_signals u WHERE u.run_id = r.id AND u.used_at IS NULL)',
        (run_id,),
    ).fetchone()
    if run is None:
        return
    if run['status'] == 'succeeded' or run['error_code'] == 'cancelled':
        took(conn, run_id)
        return
    if run['status'] in ('failed', 'unknown'):
        failed_runs = conn.execute(_FAILED, (run_id,)).fetchone()['failed_runs']
        if failed_runs >= GIVE_UP_AFTER:
            took(conn, run_id)
            given_up = Jsonb({'signals': {'given_up': True, 'failed_runs': failed_runs}})
            conn.execute('UPDATE automation_runs SET summary = summary || %s WHERE id = %s', (given_up, run_id))
            return
    conn.execute(_GIVE_BACK_AND_MARK, {'run': run_id, 'given_back': Jsonb({'signals': {'given_back': True}})})
    conn.execute(
        'UPDATE automations a SET signals_checked_at = NULL FROM automation_runs r WHERE r.id = %s'
        ' AND a.id = r.automation_id',
        (run_id,),
    )


def count_again(conn, automation_id) -> None:
    """``signals_since`` moved or the trigger left: the failed runs of the old count are forgotten with it."""
    conn.execute('DELETE FROM automation_signal_waits WHERE automation_id = %s', (automation_id,))


Nobody = Literal['account_gone', 'no_new_person']


def people_at_account(
    conn, account_id: str, near: Callable[[str | None, str | None], list[dict]]
) -> tuple[list[dict], Nobody | None]:
    """The untaken people at the run's account, best first, as the campaign link matches them (``PeopleIndex``: the
    person's ``company_domain``, else ``company_name``), and why nobody when nobody. ``near(host, name)`` reads the
    untaken people whose cells contain the account's host or name, best first: a superset the index then narrows, so
    only those rows of a large list reach Python."""
    account = conn.execute('SELECT cells FROM list_rows WHERE id = %s AND NOT excluded', (account_id,)).fetchone()
    if account is None:
        return [], 'account_gone'
    cells = account['cells']
    host = norm_host(cells.get('domain') or cells.get('website'))
    untaken = near(host or None, str(cells.get('name') or '').strip().lower() or None)
    index = PeopleIndex()
    for person in untaken:
        index.add(
            person, domain=person['cells'].get('company_domain'), company_name=person['cells'].get('company_name')
        )
    rank = {str(person['id']): i for i, person in enumerate(untaken)}
    found = sorted(index.at(cells), key=lambda person: rank[str(person['id'])])
    return found, None if found else 'no_new_person'


def of_run(conn, run_id) -> dict | None:
    """The account a signal run worked on and the signals that started it, as the run page shows them."""
    run = conn.execute('SELECT target_account_id FROM automation_runs WHERE id = %s', (run_id,)).fetchone()
    if not run or not run['target_account_id']:
        return None
    account = conn.execute(
        "SELECT cells ->> 'name' AS name FROM list_rows WHERE id = %s", (run['target_account_id'],)
    ).fetchone()
    signals = conn.execute(
        "SELECT s.type, s.data ->> 'title' AS title, u.score, s.occurred_at FROM automation_signals u"
        ' JOIN signals s ON s.id = u.signal_id WHERE u.run_id = %s ORDER BY u.score DESC, s.occurred_at DESC',
        (run_id,),
    ).fetchall()
    return {
        'account_id': run['target_account_id'],
        'account_name': account['name'] if account else None,
        'signals': signals,
    }


_RECENT = (
    f"SELECT s.row_id AS account_id, r.cells ->> 'name' AS name, sum({_LIVE_SCORE})::float8 AS score,"
    f" (array_agg(s.data ->> 'title' ORDER BY {_LIVE_SCORE} DESC))[1] AS title, max(s.first_seen_at) AS seen_at"
    f' FROM {_signals("%(automation)s", "%(watchlist)s", "now() - %(window)s::interval")}'
    f' GROUP BY s.row_id, r.id HAVING sum({_LIVE_SCORE}) >= %(min)s'
)


def preview(conn, trigger: SignalTrigger, automation_id) -> dict:
    """The accounts whose signals of the last ``PREVIEW_WINDOW`` would have started a run, whatever ``signals_since``
    says: how many, and the most recent. Without ``automation_id`` no signal counts as used."""
    params = {
        'automation': automation_id,
        'watchlist': trigger.watchlist_id,
        'window': PREVIEW_WINDOW,
        'min': trigger.min_score,
    }
    accounts = conn.execute(f'SELECT count(*) AS n FROM ({_RECENT}) ready', params).fetchone()['n']
    recent = conn.execute(f'{_RECENT} ORDER BY seen_at DESC LIMIT {PREVIEW_ACCOUNTS}', params).fetchall()
    return {
        'accounts': accounts,
        'recent': recent,
    }
