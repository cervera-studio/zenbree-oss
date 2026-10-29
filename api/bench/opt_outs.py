"""The opt-out checks of each beat for the agency (#1121), on a database that bench/jit_seed.sql, bench/due_seed.sql
and then bench/opt_out_seed.sql filled. Every run is rolled back.

    BENCH_DATABASE_URL=postgresql://... uv run python -m bench.opt_outs [runs] [--timeout s]

Prints the median and range in ms, and the rows, of:
- ``exclude_opted_out`` as the agency's beat runs it (``ForOrg``): the first beat after the opt-outs land, which writes
  every target they reach, and a later beat, which finds them all excluded already;
- ``exclude_opted_out_of_departed_owners`` as ``System``, after the agency's owner leaves it, the same two ways;
- ``GATE_SEND`` on every open target of the agency, a claim each: a mailbox reads a page of these per beat;
- ``due.due`` for the whole agency, as bench/due.py runs it, now with HubSpot connected.
"""

import argparse
from collections.abc import Callable
from time import perf_counter

import psycopg
from psycopg.rows import dict_row

from app.core import db
from app.core import scope as scopes
from app.core.system_reasons import SystemReason
from app.outreach import due
from bench.due import NOW, URL, _agency, _batch, _summary

_SEND_GATE = (
    'SELECT count(*) AS n FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
    " CROSS JOIN (VALUES ('email')) s(channel)"
    " WHERE c.status = 'active' AND t.status = 'active' {gate}"
)


def _depart(conn, org: str) -> None:
    conn.execute('RESET ROLE')  # the login: the app roles cannot delete a membership; the rollback restores the role
    conn.execute(
        'DELETE FROM member m USING outreach_campaigns c'
        ' WHERE c.organization_id = %s AND m."userId" = c.owner_id AND m."organizationId" = c.organization_id',
        (org,),
    )


def _time(conn, scope: scopes.Scope, timeout: int, fn: Callable, before: Callable | None, later: bool):
    if before:
        before(conn)
    with db.entered(conn, scope):
        conn.execute(f"SET LOCAL statement_timeout = '{timeout}s'")
        if later:
            fn(conn)
        started = perf_counter()
        try:
            out = fn(conn)
        except psycopg.errors.QueryCanceled:
            out = 'timeout'
        elapsed = (perf_counter() - started) * 1000
    conn.rollback()
    return elapsed, out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('runs', nargs='?', type=int, default=7)
    p.add_argument('--timeout', type=int, default=180)
    a = p.parse_args()
    org, _ = _agency()
    conn = psycopg.connect(URL, row_factory=dict_row)
    db._session_role(conn)
    in_org = scopes.ForOrg(org=org, reason='bench')
    checks = {
        'exclude_opted_out': (in_org, due.exclude_opted_out, None),
        'exclude_opted_out_of_departed_owners': (
            scopes.System(reason=SystemReason.OPT_OUT),
            due.exclude_opted_out_of_departed_owners,
            lambda c: _depart(c, org),
        ),
        'GATE_SEND, every open target': (
            in_org,
            lambda c: c.execute(_SEND_GATE.format(gate=due.GATE_SEND), (NOW,)).fetchone()['n'],
            None,
        ),
        'due.due, the whole agency': (in_org, lambda c: len(_batch(c)), None),
    }
    for name, (scope, fn, before) in checks.items():
        for later in (False, True) if name.startswith('exclude') else (False,):
            runs = [_time(conn, scope, a.timeout, fn, before, later) for _ in range(a.runs)]
            beat = ', a later beat' if later else ''
            print(f'{name}{beat}: rows={runs[-1][1]}  {_summary([t for t, _ in runs])}  n={a.runs}', flush=True)


if __name__ == '__main__':
    main()
