"""JIT on against the pool's session (#1049), on the database that bench/jit_seed.sql filled.

Two connections, one per side, so a plan a side cached never takes the other side's JIT choice. Side ``on`` is the
pool's session with ``jit`` back on, as before #1049; side ``pool`` is the pool's session as ``app.core.db`` makes it.
The runs alternate, and each one is a transaction in the scope the code runs in (row-level security on).

    BENCH_DATABASE_URL=postgresql://... uv run python -m bench.jit [runs]

Prints, per surface and side, the median and range in ms, then EXPLAIN ANALYZE of ``project_campaigns``: plan cost,
planning, JIT and execution time. ``--survey`` instead prints every statement the surfaces run that JIT compiles,
read from auto_explain.
"""

import json
import os
import statistics
import sys
from collections.abc import Callable
from time import perf_counter
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core import db
from app.core import scope as scopes
from app.home.nav import load_nav
from app.home.overview import load_overview
from app.outreach import approval_queue
from app.outreach.campaign_sql import campaign_query
from app.outreach.routes import project_campaigns

URL = os.environ['BENCH_DATABASE_URL']


def _agency() -> tuple[str, str]:
    with psycopg.connect(URL, row_factory=dict_row) as conn:  # the login, before row-level security
        row = conn.execute(
            'SELECT o.id AS org, p.id AS project FROM organization o JOIN projects p ON p.organization_id = o.id'
            " WHERE o.slug = 'org-0' ORDER BY p.name LIMIT 1"
        ).fetchone()
    return str(row['org']), str(row['project'])


def _surfaces(org: str, project: str) -> dict[str, tuple[scopes.Scope, Callable[[Any], object]]]:
    member = scopes.Member(org=org, user=org)  # the seed's owner has the organization's id
    return {
        'project_campaigns': (member, lambda c: project_campaigns(c, org, project)),
        'GET /v1/campaigns': (
            member,
            lambda c: approval_queue.with_counts(project_campaigns(c, org, project), approval_queue.counts(c, project)),
        ),
        'load_overview': (member, lambda c: load_overview(c, org, org, project)),
        'load_nav': (member, lambda c: load_nav(c, org, org, project)),
    }


def _connect(side: str) -> psycopg.Connection:
    conn = psycopg.connect(URL, row_factory=dict_row)
    db._session_role(conn)
    if side == 'on':
        conn.autocommit = True
        conn.execute('SET jit = on')
        conn.autocommit = False
    return conn


def _timed(conn, scope, fn) -> tuple[float, object]:
    with db.entered(conn, scope):
        jit = conn.execute("SELECT current_setting('jit') AS jit").fetchone()['jit']
        started = perf_counter()
        out = fn(conn)
        elapsed = (perf_counter() - started) * 1000
    conn.rollback()
    return elapsed, (jit, len(out) if isinstance(out, list) else type(out).__name__)


def _explain(conn, org: str, project: str) -> dict:
    sql, params = campaign_query(org, where='AND c.project_id = %s', params=(project,))
    with db.entered(conn, scopes.Member(org=org, user=org)):
        [[top]] = (
            conn.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql, params, prepare=False).fetchone().values()
        )
    conn.rollback()
    jit = top.get('JIT', {}).get('Timing', {})
    return {
        'cost': top['Plan']['Total Cost'],
        'planning': top['Planning Time'],
        'jit': jit.get('Total', 0.0),
        'execution': top['Execution Time'],
        'jit_functions': top.get('JIT', {}).get('Functions', 0),
    }


def _summary(xs: list[float]) -> str:
    return f'median {statistics.median(xs):8.1f}  range {min(xs):7.1f}-{max(xs):7.1f}'


def bench(runs: int) -> None:
    conns = {side: _connect(side) for side in ('on', 'pool')}
    org, project = _agency()
    for name, (scope, fn) in _surfaces(org, project).items():
        times: dict[str, list[float]] = {'on': [], 'pool': []}
        seen: dict[str, object] = {}
        for _ in range(runs):
            for side, conn in conns.items():
                elapsed, seen[side] = _timed(conn, scope, fn)
                times[side].append(elapsed)
        for side in conns:
            print(f'{name:26} {side:4} jit,rows={seen[side]}  {_summary(times[side])}  n={runs}')
    explains: dict[str, list[dict]] = {'on': [], 'pool': []}
    for _ in range(runs):
        for side, conn in conns.items():
            explains[side].append(_explain(conn, org, project))
    for side, rows in explains.items():
        print(f'EXPLAIN project_campaigns {side:4} cost {rows[0]["cost"]:.0f} jit_functions {rows[0]["jit_functions"]}')
        for key in ('planning', 'jit', 'execution'):
            print(f'    {key:10} {_summary([r[key] for r in rows])}')


def survey() -> None:
    """Every statement the surfaces run that the planner JIT compiles, with its JIT and execution time."""
    conn = _connect('on')
    plans: list[dict] = []
    conn.add_notice_handler(lambda d: plans.append(json.loads(d.message_primary.split('\n', 1)[1])))
    conn.autocommit = True
    for setting in (
        'RESET ROLE',  # LOAD needs the superuser login
        "LOAD 'auto_explain'",
        'SET auto_explain.log_min_duration = 0',
        'SET auto_explain.log_analyze = on',
        'SET auto_explain.log_nested_statements = on',
        "SET auto_explain.log_format = 'json'",
        "SET auto_explain.log_level = 'notice'",
        'SET ROLE zenbree_app',
    ):
        conn.execute(setting)
    conn.autocommit = False
    org, project = _agency()
    for name, (scope, fn) in _surfaces(org, project).items():
        plans.clear()
        _timed(conn, scope, fn)
        jitted = [p for p in plans if p.get('JIT')]
        print(
            f'{name}: {len(plans)} statements, {len(jitted)} JIT compiled, top cost {max(p["Plan"]["Total Cost"] for p in plans):.0f}'
        )
        for p in jitted:
            t = p['JIT']['Timing']
            print(
                f'    cost {p["Plan"]["Total Cost"]:>10.0f}  jit {t["Total"]:7.1f} ms  execution'
                f' {p["Plan"]["Actual Total Time"]:7.1f} ms  {" ".join(p["Query Text"].split())[:90]}'
            )


if __name__ == '__main__':
    if '--survey' in sys.argv:
        survey()
    else:
        bench(int(next((a for a in sys.argv[1:] if a.isdigit()), '9')))
