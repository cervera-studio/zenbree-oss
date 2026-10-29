"""One run of a watchlist (E13.3, #647): read each account's postings, record what is new, settle the credits.

The shape of ``app.scheduling.runner``: the run is claimed once with its quote held on a ``Tab``, the vendor is
called with nothing written, and the outcome (signals, the rotation, a charge per account, the close, the run's
status) commits in one transaction. A worker that dies in between leaves no half result and no charge: the reaper
releases the hold and ``maintain`` marks the run ``unknown``.

Decisions on #122: a run spends at most :data:`CREDIT_CAP` credits, one per account that has postings, and the
accounts it does not reach are first in the next run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from psycopg.types.json import Jsonb

from app.admin.settings import vendor_disabled_now
from app.auth.middleware import creator_not_banned
from app.billing import balance, credits
from app.billing.paid_call import current_user_id
from app.billing.price import Price, Quote
from app.billing.tab import HoldGone, Tab, close_stored
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.scope import ActorGone, acting_for
from app.people.apollo_client import ApolloClient
from app.people.apollo_models import JobPosting
from app.watchlists import job_postings
from app.watchlists.enroll import enroll_signals

logger = structlog.get_logger()

CREDIT_CAP = 100
# Accounts read per run whatever they cost: most have no postings and are free, and the task has 15 minutes.
MAX_CHECKS = 300
CONCURRENCY = 5
# One credit per account read that has postings. It buys no cell (signals have their own table), and the runner, not
# the account's cells, knows which one had postings.
WATCHED_ACCOUNT = Price(1, (), lambda cells: True)
STALE_AFTER = timedelta(minutes=17)  # the task's hard limit is 16; the credit reaper starts at 20

# The accounts of the watchlist's list, the ones checked longest ago first.
_ACCOUNTS_SQL = """
SELECT r.id, r.cells, a.vendor_id
FROM list_rows r LEFT JOIN watchlist_accounts a ON a.watchlist_id = %s AND a.row_id = r.id
WHERE r.list_id = %s AND NOT r.excluded
ORDER BY a.checked_at NULLS FIRST, r.seq
LIMIT %s"""


@dataclass
class Checked:
    """What the vendor said about one account. ``skip``: why it could not be read at all."""

    row_id: str
    vendor_id: str | None = None
    has_postings: bool = False
    postings: list[JobPosting] = field(default_factory=list)
    skip: str | None = None
    failed: bool = False  # the vendor gave up on it: the run stops here


# An account the vendor fails on goes to the back like any skipped one, or an account it always fails on
# would be first in every run and nothing behind it would ever be read.
def _failed(row_id: str) -> Checked:
    return Checked(row_id, skip='vendor_failed', failed=True)


def _locked(conn, run_id) -> tuple[dict | None, dict | None]:
    run = conn.execute('SELECT * FROM watchlist_runs WHERE id = %s', (run_id,)).fetchone()
    if not run:
        return None, None
    watchlist = conn.execute(
        'SELECT w.*, l.organization_id, l.project_id, l.deleted_at AS list_deleted_at FROM watchlists w'
        ' JOIN lists l ON l.id = w.list_id WHERE w.id = %s FOR UPDATE OF w',
        (run['watchlist_id'],),
    ).fetchone()
    return watchlist, conn.execute('SELECT * FROM watchlist_runs WHERE id = %s FOR UPDATE', (run_id,)).fetchone()


def _stop(conn, run: dict, status: str, code: str) -> None:
    if run['reservation_id']:
        close_stored(conn, str(run['reservation_id']))
    conn.execute(
        'UPDATE watchlist_runs SET status = %s, error_code = %s, finished_at = now() WHERE id = %s',
        (status, code, run['id']),
    )


def _refusal(conn, watchlist: dict) -> tuple[str, str] | None:
    """Why this watchlist may not run now, as ``(status, code)``."""
    if not watchlist['enabled'] or watchlist['list_deleted_at']:
        return 'cancelled', 'watchlist_paused'
    if not watchlist['user_id']:
        return 'cancelled', 'creator_deleted'
    if not conn.execute(f'SELECT {creator_not_banned("%s")} AS ok', (watchlist['user_id'],)).fetchone()['ok']:
        return 'cancelled', 'user_banned'
    if not conn.execute(
        'SELECT 1 FROM member WHERE "userId" = %s AND "organizationId" = %s',
        (watchlist['user_id'], watchlist['organization_id']),
    ).fetchone():
        return 'failed', 'creator_left'  # the run would spend an organization's credits as a stranger
    return None


def claim(run_id) -> dict | None:
    """Take a queued run, as the watchlist's creator: its accounts for this round and the credits they can cost at
    most. ``None`` when the run is not to be done (and says why on its row). A creator who left or was banned since
    the run was queued gets ``ActorGone``: the organization then closes the run, so the beat does not queue it again."""
    try:
        return _claim(run_id)
    except ActorGone as gone:
        with acting_for(gone.scope.org, 'run of a creator who left'), get_conn() as conn:
            watchlist, run = _locked(conn, run_id)
            # Only the gone creator's own run: a forged header must not close a teammate's.
            if run and watchlist and run['status'] == 'queued' and str(watchlist['user_id']) == gone.scope.user:
                _stop(conn, run, *(_refusal(conn, watchlist) or ('failed', 'creator_left')))
        raise


def _claim(run_id) -> dict | None:
    refused_org = None
    with get_conn() as conn:
        watchlist, run = _locked(conn, run_id)
        if not run or not watchlist or run['status'] != 'queued':
            return None
        if refusal := _refusal(conn, watchlist):
            _stop(conn, run, *refusal)
            return None
        accounts = conn.execute(_ACCOUNTS_SQL, (watchlist['id'], watchlist['list_id'], MAX_CHECKS)).fetchall()
        if not accounts:
            conn.execute(
                "UPDATE watchlist_runs SET status = 'succeeded', started_at = now(), finished_at = now() WHERE id = %s",
                (run_id,),
            )
            return None
        quote = Quote(WATCHED_ACCOUNT, min(CREDIT_CAP, len(accounts)))
        tab = Tab.open(
            conn,
            credits.Payer(str(watchlist['organization_id']), str(watchlist['user_id']), str(watchlist['project_id'])),
            quote.credits,
            kind='watchlist',
            reference_id=str(run_id),
            tool_name='watchlist_job_postings',
            description='Watchlist run',
        )
        if isinstance(tab, Tab):
            conn.execute(
                "UPDATE watchlist_runs SET status = 'running', reservation_id = %s, started_at = now() WHERE id = %s",
                (tab.hold.id, run_id),
            )
            return {'watchlist': watchlist, 'accounts': accounts, 'quote': quote}
        _stop(conn, run, 'failed', 'project_cap_reached' if tab.capped else 'insufficient_credits')
        refused_org = str(watchlist['organization_id'])
    balance.changed(refused_org, refused=True)  # credits short: auto top-up may be due
    return None


async def _check(client: ApolloClient, account: dict) -> Checked:
    cells, row_id = account['cells'], str(account['id'])
    vendor_id = str(account['vendor_id'] or cells.get('apollo_id') or '').strip()
    if not vendor_id:
        domain = str(cells.get('domain') or '').strip()
        if not domain:
            return Checked(row_id, skip='no_domain')
        found = await client.enrich_organization(domain)
        if is_err(found):
            unknown = found.error.code in ('CONTACTS.ORG_NOT_FOUND', 'CONTACTS.INVALID_INPUT')
            return Checked(row_id, skip='not_found') if unknown else _failed(row_id)
        vendor_id = found.value.id
    read = await client.get_job_postings(org_id=vendor_id, org_name=str(cells.get('name') or ''))
    if is_err(read):
        # An id the vendor no longer knows is the account's own trouble, not the vendor's.
        return Checked(row_id, skip='not_found') if read.error.code == 'CONTACTS.ORG_NOT_FOUND' else _failed(row_id)
    postings = read.value.postings
    return Checked(row_id, vendor_id, bool(postings or read.value.total), postings)


async def check_accounts(accounts: list[dict], amount: int) -> tuple[list[Checked], str | None]:
    """Read accounts in order until they are done or ``amount`` of them had postings. ``(results, error)``:
    ``error`` when the vendor gave up, with the results up to there."""
    if vendor_disabled_now('apollo'):
        return [], 'vendor_disabled'
    client = ApolloClient()
    if not client.is_configured:
        return [], 'vendor_not_configured'
    done: list[Checked] = []
    spent, at = 0, 0
    while at < len(accounts) and spent < amount:
        # Never more in flight than credits left: every one of them may turn out to have postings.
        batch = accounts[at : at + min(CONCURRENCY, amount - spent)]
        at += len(batch)
        results = await asyncio.gather(*(_check(client, account) for account in batch))
        done += results
        spent += sum(1 for r in results if r.has_postings)
        if any(r.failed for r in results):
            return done, 'vendor_failed'
    return done, None


def within(done: list[Checked], quote: Quote) -> list[Checked]:
    """The accounts read up to the one that fills the quote: the run never delivers or bills past its hold, and the
    accounts after it stay unchecked, first in the next run."""
    found = [i for i, account in enumerate(done) if account.has_postings]
    return done if len(found) <= quote.rows else done[: found[quote.rows - 1] + 1]


def finish(run_id, job_titles: list[str], quote: Quote, done: list[Checked], error: str | None) -> None:
    """Record the run in one transaction: the signals, each account's place in the rotation, the charges, the close."""
    with get_conn() as conn:
        watchlist, run = _locked(conn, run_id)
        if not run or run['status'] != 'running':
            return
        tab = Tab.stored(conn, str(run['reservation_id']))
        try:
            tab.lock(conn)  # until this commit: nothing closes the hold under the charges below
        except HoldGone:
            _stop(conn, run, 'unknown', 'reservation_expired')
            return
        if not watchlist or not watchlist['enabled']:
            _stop(conn, run, 'cancelled', 'watchlist_paused')
            return
        now = datetime.now(UTC)
        new, billed = 0, []
        done = within(done, quote)
        for account in done:
            saved = conn.execute(
                'INSERT INTO watchlist_accounts (watchlist_id, row_id, checked_at, vendor_id, skip_reason)'
                ' SELECT %s, id, %s, %s, %s FROM list_rows WHERE id = %s'  # the row may be gone since the claim
                ' ON CONFLICT (watchlist_id, row_id) DO UPDATE SET checked_at = EXCLUDED.checked_at,'
                ' vendor_id = coalesce(EXCLUDED.vendor_id, watchlist_accounts.vendor_id),'
                ' skip_reason = EXCLUDED.skip_reason',
                (run['watchlist_id'], now, account.vendor_id, account.skip, account.row_id),
            ).rowcount
            if not saved:
                continue
            if account.has_postings:
                billed.append(account.row_id)
            for posting in account.postings:
                if not job_postings.matches(posting, job_titles):
                    continue
                # When the insert runs, not when the transaction began: a signal automation compares it with its last
                # evaluation (``signal_trigger.LATE_COMMIT``), and the waits for the locks above come before it.
                new += conn.execute(
                    'INSERT INTO signals (watchlist_id, row_id, run_id, type, key, data, score, occurred_at,'
                    ' first_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp()) ON CONFLICT DO NOTHING',
                    (
                        run['watchlist_id'],
                        account.row_id,
                        run_id,
                        job_postings.TYPE,
                        job_postings.key(posting),
                        Jsonb(posting.to_dict()),
                        job_postings.SCORE,
                        job_postings.occurred_at(posting, now),
                    ),
                ).rowcount
        tab.record(conn, str(run_id), str(watchlist['list_id']), billed, quote.price)
        spent = tab.close(conn)
        total = conn.execute(
            'SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND NOT excluded', (watchlist['list_id'],)
        ).fetchone()['n']
        conn.execute(
            'UPDATE watchlist_runs SET status = %s, error_code = %s, credits_spent = %s, accounts_checked = %s,'
            ' accounts_skipped = %s, accounts_left = %s, signals_new = %s, finished_at = now() WHERE id = %s',
            (
                'failed' if error else 'succeeded',
                error,
                spent,
                sum(1 for a in done if not a.skip),
                sum(1 for a in done if a.skip),
                max(total - len(done), 0),
                new,
                run_id,
            ),
        )


async def run_watchlist(run_id) -> None:
    claimed = claim(run_id)
    if not claimed:
        return
    watchlist = claimed['watchlist']
    org = str(watchlist['organization_id'])
    balance.changed(org)  # the hold
    try:
        current_user_id.set(str(watchlist['user_id']))
        done, error = await check_accounts(claimed['accounts'], claimed['quote'].rows)
        # A database error here propagates: the run stays ``running`` and nothing is charged twice.
        finish(run_id, list(watchlist['job_titles']), claimed['quote'], done, error)
        enroll_signals(run_id)
    except ActorGone:  # the creator left mid-run: nothing is recorded, and the organization gets its hold back
        with acting_for(org, 'run of a creator who left'), get_conn() as conn:
            _, run = _locked(conn, run_id)
            if run and run['status'] == 'running':
                _stop(conn, run, 'failed', 'creator_left')
        raise
    finally:
        balance.changed(org)  # settled or released


def maintain() -> None:
    """A run whose worker died: its outcome is unknown, its hold goes back. A queued run whose creator deleted their
    account has nobody to run as: ``creator_deleted``. Called in the beat's System scope, which only selects:
    each run is closed as its organization."""
    with get_conn() as conn:
        stale = conn.execute(
            'SELECT r.id, r.organization_id FROM watchlist_runs r JOIN watchlists w ON w.id = r.watchlist_id'
            " WHERE r.status IN ('queued', 'running')"
            " AND (r.started_at < %s OR (r.status = 'queued' AND w.user_id IS NULL))",
            (datetime.now(UTC) - STALE_AFTER,),
        ).fetchall()
    for row in stale:
        with acting_for(str(row['organization_id']), 'dead watchlist run'):
            _reap(row['id'])


def _reap(run_id) -> None:
    with get_conn() as conn:
        watchlist, run = _locked(conn, run_id)
        if run and watchlist and run['status'] == 'queued' and watchlist['user_id'] is None:
            _stop(conn, run, 'cancelled', 'creator_deleted')
            return
        if not (run and run['status'] == 'running' and run['started_at'] < datetime.now(UTC) - STALE_AFTER):
            return
        _stop(conn, run, 'unknown', 'worker_outcome_unknown')
    if watchlist:
        balance.changed(str(watchlist['organization_id']))
