"""The dispatcher's due query for the heavy client of an agency (#1127), on a migrated database that bench/jit_seed.sql
and then bench/due_seed.sql filled.

Each run is a transaction in the scope the campaign beat runs in (``ForOrg``, row-level security on), cut by
``statement_timeout`` (``--timeout`` seconds, 180 by default). ``--campaigns k`` leaves only the first k campaigns of
the heavy client active for the run and pauses the agency's other campaigns (rolled back), to show how the time grows
with the campaigns.

    BENCH_DATABASE_URL=postgresql://... uv run python -m bench.due [runs] [--campaigns k] [--timeout s]
        [--explain] [--fingerprint] [--candidates]

Prints the median and range in ms of ``due.due`` as the plan phase calls it (``PLAN_SELECT``, ``GATE_PLAN``, a batch of
500). ``--fingerprint`` prints, for every due person with no batch limit, their count, how many more are due when no
campaign holds duplicates, and a sha256 of their ids: two versions of the query that block the same people print the
same line. ``--candidates`` times the HubSpot gate's candidate query (``GATE_CANDIDATES``, a batch of 500 as
``dispatcher.gate_candidates`` reads it each beat), then the same query with no batch limit.
``--explain`` prints EXPLAIN ANALYZE of one run.
"""

import argparse
import hashlib
import os
import statistics
from datetime import UTC, datetime, timedelta
from time import perf_counter

import psycopg
from psycopg.rows import dict_row

from app.core import db
from app.core import scope as scopes
from app.outreach import due

URL = os.environ['BENCH_DATABASE_URL']
NOW = datetime.now(UTC) + timedelta(days=4)  # step 3 of the seed is due 3 days after step 2's send, a day ago


def _agency() -> tuple[str, str]:
    with psycopg.connect(URL, row_factory=dict_row) as conn:  # the login, before row-level security
        row = conn.execute(
            'SELECT o.id AS org, p.id AS project FROM organization o JOIN projects p ON p.organization_id = o.id'
            " WHERE o.slug = 'org-0' AND p.name = 'Client 1'"
        ).fetchone()
    return str(row['org']), str(row['project'])


def _run(conn, org: str, project: str, campaigns: int | None, timeout: int, fn):
    with db.entered(conn, scopes.ForOrg(org=org, reason='bench')):
        if campaigns is not None:
            conn.execute(
                "UPDATE outreach_campaigns SET status = 'paused'"
                " WHERE project_id <> %s OR split_part(name, ' ', 2)::int > %s",
                (project, campaigns),
            )
        conn.execute(f"SET LOCAL statement_timeout = '{timeout}s'")
        started = perf_counter()
        try:
            out = fn(conn)
        except psycopg.errors.QueryCanceled:
            out = None
        elapsed = (perf_counter() - started) * 1000
    conn.rollback()
    return elapsed, out


def _batch(conn):
    return due.due(conn, NOW, select=due.PLAN_SELECT, gate_sql=due.GATE_PLAN)


class _Explained:
    """A connection whose statements run under EXPLAIN ANALYZE, so the plan is the one of the code as it is."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params):
        return self.conn.execute('EXPLAIN (ANALYZE, BUFFERS) ' + sql, params, prepare=False)


def _every(conn):
    """Every due person, no batch limit, and how many more are due when no campaign holds duplicates."""
    batch, due.PLAN_BATCH = due.PLAN_BATCH, 10**9
    try:
        blocking = [r['id'] for r in due.due(conn, NOW, select='t.id', gate_sql=due.GATE_PLAN)]
        conn.execute('UPDATE outreach_campaigns SET block_active_duplicates = false')
        free = due.due(conn, NOW, select='t.id', gate_sql=due.GATE_PLAN)
    finally:  # a timed-out fingerprint must not leave the explained run without the plan phase's batch limit
        due.PLAN_BATCH = batch
    digest = hashlib.sha256(''.join(sorted(str(i) for i in blocking)).encode()).hexdigest()
    return len(blocking), len(free) - len(blocking), digest


def _candidates(conn):
    return due.due(conn, NOW, select='t.owner_id, t.email_key', gate_sql=due.GATE_CANDIDATES)


def _every_candidate(conn):
    batch, due.PLAN_BATCH = due.PLAN_BATCH, 10**9
    try:
        return _candidates(conn)
    finally:
        due.PLAN_BATCH = batch


def _summary(xs: list[float]) -> str:
    return f'median {statistics.median(xs):9.1f}  range {min(xs):9.1f}-{max(xs):9.1f}'


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('runs', nargs='?', type=int, default=7)
    p.add_argument('--campaigns', type=int)
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--explain', action='store_true')
    p.add_argument('--fingerprint', action='store_true')
    p.add_argument('--candidates', action='store_true')
    a = p.parse_args()
    org, project = _agency()
    conn = psycopg.connect(URL, row_factory=dict_row)
    db._session_role(conn)

    times, rows = [], None
    for _ in range(a.runs):
        elapsed, out = _run(conn, org, project, a.campaigns, a.timeout, _batch)
        times.append(elapsed)
        rows = 'timeout' if out is None else len(out)
    shape = 'the whole agency' if a.campaigns is None else f'only {a.campaigns} campaigns of the heavy client'
    print(f'due.due, {shape}: rows={rows}  {_summary(times)}  n={a.runs}', flush=True)
    if a.candidates:
        for label, fn in (
            ('gate candidates, batch of 500', _candidates),
            ('gate candidates, no limit', _every_candidate),
        ):
            times, rows = [], None
            for _ in range(a.runs):
                elapsed, out = _run(conn, org, project, a.campaigns, a.timeout, fn)
                times.append(elapsed)
                rows = 'timeout' if out is None else len(out)
            print(f'{label}: rows={rows}  {_summary(times)}  n={a.runs}', flush=True)
    if a.fingerprint:
        _, fingerprint = _run(conn, org, project, a.campaigns, a.timeout, _every)
        print(f'every due person (count, held duplicates, sha256): {fingerprint or "timeout"}', flush=True)
    if a.explain:
        _, plan = _run(conn, org, project, a.campaigns, a.timeout, lambda c: _batch(_Explained(c)))
        print('\n'.join(r['QUERY PLAN'] for r in plan) if plan else 'EXPLAIN: timeout')


if __name__ == '__main__':
    main()
