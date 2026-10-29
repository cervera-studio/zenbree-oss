"""Watchlists (E13.3, #647): the accounts of a company list, watched on a cadence for a kind of change.

A watchlist belongs to its list's project and is shared like the list: every member of the organization sees
it, its creator or an admin manages it. Runs are durable rows claimed by ``app.watchlists.runner``; this module
owns the watchlist itself and the queue of its runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

from app.auth.middleware import CurrentUser
from app.billing.tab import close_stored
from app.core import tasks
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.watchlists.models import WatchlistIn, WatchlistPatch

# Per project: each run can spend up to the credit cap, so the number of watchlists bounds a project's spend.
MAX_PER_PROJECT = 10

# What the API shows of a run: not its credit hold.
RUN_COLUMNS = (
    'id, watchlist_id, scheduled_for, status, credits_spent, accounts_checked, accounts_skipped, accounts_left,'
    ' signals_new, people_enrolled, accounts_without_people, people_left_out, error_code, created_at, started_at, finished_at'
)
_VIEW = """
SELECT w.*, l.name AS list_name,
       (SELECT count(*) FROM list_rows r WHERE r.list_id = w.list_id AND NOT r.excluded) AS accounts,
       (SELECT count(*) FROM signals s WHERE s.watchlist_id = w.id) AS signals
FROM watchlists w JOIN lists l ON l.id = w.list_id
WHERE l.organization_id = %s AND l.project_id = %s AND l.deleted_at IS NULL"""


def _with_last_run(conn, watchlists: list[dict]) -> list[dict]:
    newest = conn.execute(
        f'SELECT DISTINCT ON (watchlist_id) {RUN_COLUMNS} FROM watchlist_runs WHERE watchlist_id = ANY(%s)'
        ' ORDER BY watchlist_id, created_at DESC',
        ([w['id'] for w in watchlists],),
    ).fetchall()
    by_watchlist = {r['watchlist_id']: r for r in newest}
    return [{**w, 'last_run': by_watchlist.get(w['id'])} for w in watchlists]


def owned(conn, user: CurrentUser, project_id: str, watchlist_id, *, manage: bool = True, lock: bool = False) -> dict:
    """The watchlist as the API shows it. ``manage``: only its creator or an admin of the organization."""
    if lock:
        conn.execute('SELECT id FROM watchlists WHERE id = %s FOR UPDATE', (watchlist_id,))
    row = conn.execute(f'{_VIEW} AND w.id = %s', (user.org_id, project_id, watchlist_id)).fetchone()
    if not row:
        raise api_error(404, 'WATCHLIST.NOT_FOUND', 'watchlist not found')
    if manage and str(row['user_id']) != user.id and user.org_role != 'admin':
        raise api_error(403, 'WATCHLIST.FORBIDDEN', 'only who made the watchlist, or an admin')
    return _with_last_run(conn, [row])[0]


def list_all(user: CurrentUser, project_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(f'{_VIEW} ORDER BY w.created_at DESC', (user.org_id, project_id)).fetchall()
        return _with_last_run(conn, rows)


def _check_campaign(conn, user: CurrentUser, project_id: str, campaign_id) -> None:
    if (
        campaign_id
        and not conn.execute(
            'SELECT 1 FROM outreach_campaigns WHERE id = %s AND organization_id = %s AND project_id = %s'
            " AND status NOT IN ('stopped', 'completed')",
            (campaign_id, user.org_id, project_id),
        ).fetchone()
    ):
        raise api_error(404, 'WATCHLIST.CAMPAIGN_NOT_FOUND', 'pick a campaign of this project that has not ended')


def create(user: CurrentUser, project_id: str, body: WatchlistIn) -> dict:
    with get_conn() as conn:
        # Serialises the per-project limit against concurrent creates.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (project_id,))
        if not conn.execute(
            "SELECT 1 FROM lists WHERE id = %s AND organization_id = %s AND project_id = %s AND entity = 'company'"
            ' AND deleted_at IS NULL FOR SHARE',
            (body.list_id, user.org_id, project_id),
        ).fetchone():
            raise api_error(404, 'WATCHLIST.LIST_NOT_FOUND', 'pick a company list of this project')
        _check_campaign(conn, user, project_id, body.campaign_id)
        count = conn.execute(
            'SELECT count(*) AS n FROM watchlists w JOIN lists l ON l.id = w.list_id WHERE l.project_id = %s',
            (project_id,),
        ).fetchone()['n']
        if count >= MAX_PER_PROJECT:
            raise api_error(409, 'WATCHLIST.LIMIT', f'at most {MAX_PER_PROJECT} watchlists per project')
        made = conn.execute(
            'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, enabled, next_run_at, campaign_id,'
            ' campaign_threshold) VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s) RETURNING id',
            (
                user.id,
                body.list_id,
                body.name.strip(),
                body.job_titles,
                body.cadence,
                body.enabled,
                body.campaign_id,
                body.campaign_threshold,
            ),
        ).fetchone()
        return owned(conn, user, project_id, made['id'])


def _cancel_queued(conn, watchlist_id) -> None:
    conn.execute(
        "UPDATE watchlist_runs SET status = 'cancelled', finished_at = now(), error_code = 'watchlist_paused'"
        " WHERE watchlist_id = %s AND status = 'queued'",
        (watchlist_id,),
    )


def update(user: CurrentUser, project_id: str, watchlist_id, body: WatchlistPatch) -> dict:
    changes = body.model_dump(exclude_none=True)
    pair = {'campaign_id', 'campaign_threshold'}
    if pair & body.model_fields_set:
        # An explicit null clears; the two go together, or the watchlist would name one without the other.
        if not pair <= body.model_fields_set or (body.campaign_id is None) != (body.campaign_threshold is None):
            raise api_error(422, 'WATCHLIST.CAMPAIGN_PAIR', 'name a campaign and a score threshold together')
        changes |= {'campaign_id': body.campaign_id, 'campaign_threshold': body.campaign_threshold}
    with get_conn() as conn:
        if changes.get('campaign_id'):
            # Campaign before watchlist, as enrollment (app.watchlists.enroll) and the foreign key's SET NULL take
            # them: the UPDATE below locks the new campaign anyway, and taking it after the watchlist would let a
            # run that holds that campaign and waits for the watchlist deadlock with this update.
            conn.execute('SELECT 1 FROM outreach_campaigns WHERE id = %s FOR KEY SHARE', (changes['campaign_id'],))
        owned(conn, user, project_id, watchlist_id, lock=True)
        _check_campaign(conn, user, project_id, changes.get('campaign_id'))
        if changes:
            # One statement: the campaign and its threshold are checked together. Column names come from the model's
            # own fields, never from the request.
            sets = ', '.join(f'{column} = %s' for column in changes)
            conn.execute(
                f'UPDATE watchlists SET {sets}, updated_at = now() WHERE id = %s', (*changes.values(), watchlist_id)
            )
        if changes.get('enabled') is False:
            _cancel_queued(conn, watchlist_id)
        return owned(conn, user, project_id, watchlist_id)


def delete(user: CurrentUser, project_id: str, watchlist_id) -> None:
    with get_conn() as conn:
        owned(conn, user, project_id, watchlist_id, lock=True)
        # A run in flight loses its row with the watchlist: give its hold back now, not when the reaper finds it.
        for run in conn.execute(
            "SELECT reservation_id FROM watchlist_runs WHERE watchlist_id = %s AND status = 'running'"
            ' AND reservation_id IS NOT NULL',
            (watchlist_id,),
        ).fetchall():
            close_stored(conn, str(run['reservation_id']))
        conn.execute('DELETE FROM watchlists WHERE id = %s', (watchlist_id,))


def enqueue(conn, watchlist_id, when: datetime) -> dict | None:
    """The watchlist's live run, or a new queued one. The caller holds the watchlist's lock."""
    live = conn.execute(
        f"SELECT {RUN_COLUMNS} FROM watchlist_runs WHERE watchlist_id = %s AND status IN ('queued', 'running')",
        (watchlist_id,),
    ).fetchone()
    if live:
        return live
    return conn.execute(
        'INSERT INTO watchlist_runs (watchlist_id, scheduled_for) VALUES (%s, %s)'
        f' ON CONFLICT (watchlist_id, scheduled_for) DO NOTHING RETURNING {RUN_COLUMNS}',
        (watchlist_id, when),
    ).fetchone()


def run_now(user: CurrentUser, project_id: str, watchlist_id) -> dict | None:
    with get_conn() as conn:
        watchlist = owned(conn, user, project_id, watchlist_id, lock=True)
        if not watchlist['enabled']:
            raise api_error(409, 'WATCHLIST.PAUSED', 'resume the watchlist first')
        return enqueue(conn, watchlist_id, datetime.now(UTC))


def runs(user: CurrentUser, project_id: str, watchlist_id) -> list[dict]:
    with get_conn() as conn:
        owned(conn, user, project_id, watchlist_id, manage=False)
        return conn.execute(
            f'SELECT {RUN_COLUMNS} FROM watchlist_runs WHERE watchlist_id = %s ORDER BY created_at DESC LIMIT 100',
            (watchlist_id,),
        ).fetchall()


def signals(user: CurrentUser, project_id: str, watchlist_id) -> list[dict]:
    """The watchlist's signals with their score as of now, strongest first."""
    with get_conn() as conn:
        owned(conn, user, project_id, watchlist_id, manage=False)
        return conn.execute(
            """SELECT s.id, s.type, s.row_id, s.occurred_at, s.first_seen_at,
                      signal_score(s.score, s.occurred_at)::float8 AS score,
                      coalesce(r.cells ->> 'name', '') AS account_name,
                      coalesce(r.cells ->> 'domain', '') AS account_domain,
                      coalesce(s.data ->> 'title', '') AS title, s.data ->> 'city' AS city,
                      s.data ->> 'country' AS country, s.data ->> 'url' AS url
               FROM signals s JOIN list_rows r ON r.id = s.row_id
               WHERE s.watchlist_id = %s ORDER BY score DESC, s.occurred_at DESC LIMIT 500""",
            (watchlist_id,),
        ).fetchall()


# Due, on a live list. The selection also wants a creator who is a live member of the watchlist's organization: a
# deleted, removed or banned (#389) creator's watchlists wait, and a return or an unban resumes them.
_DUE_SQL = (
    'w.enabled AND w.next_run_at <= now()'
    ' AND NOT EXISTS (SELECT 1 FROM lists l WHERE l.id = w.list_id AND l.deleted_at IS NOT NULL)'
)


def schedule_due() -> list[tuple[str, str, str]]:
    """Queue a run for every watchlist that is due and move it to its next slot, as its creator.
    ``(organization, creator, run id)`` of every queued run. Called in the beat's System scope, which only selects."""
    with get_conn() as conn:
        due = conn.execute(
            f'SELECT w.id, w.organization_id, w.user_id FROM watchlists w WHERE {_DUE_SQL}'
            ' AND (w.user_id, w.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members)'
            ' ORDER BY w.next_run_at LIMIT 100'
        ).fetchall()
    for watchlist in due:
        tasks.for_member(
            str(watchlist['organization_id']), str(watchlist['user_id']), partial(_schedule, watchlist['id'])
        )
    with get_conn() as conn:
        queued = conn.execute(
            'SELECT r.id, r.organization_id, w.user_id FROM watchlist_runs r JOIN watchlists w ON w.id = r.watchlist_id'
            " WHERE r.status = 'queued' AND w.user_id IS NOT NULL ORDER BY r.created_at LIMIT 100"
        ).fetchall()
    return [(str(r['organization_id']), str(r['user_id']), str(r['id'])) for r in queued]


def _schedule(watchlist_id) -> None:
    with get_conn() as conn:
        watchlist = conn.execute(
            f'SELECT w.id, w.cadence, w.next_run_at FROM watchlists w WHERE w.id = %s AND {_DUE_SQL}'
            ' FOR UPDATE SKIP LOCKED',
            (watchlist_id,),
        ).fetchone()
        if watchlist is None:  # another beat has it, or it changed since the selection
            return
        enqueue(conn, watchlist['id'], watchlist['next_run_at'])
        step = timedelta(days=1 if watchlist['cadence'] == 'daily' else 7)
        # The next slot after now, on the same weekday and hour: a missed week is not run twice.
        missed = int((datetime.now(UTC) - watchlist['next_run_at']) / step) + 1
        conn.execute(
            'UPDATE watchlists SET next_run_at = %s WHERE id = %s',
            (watchlist['next_run_at'] + step * missed, watchlist['id']),
        )
