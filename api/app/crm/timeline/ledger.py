"""The only writer of ``crm_timeline_events`` and ``crm_timeline_cursors`` (#932), and the reads a user sees.

Every function runs in the scope its caller entered: a source records inside its own transaction, the writer settles
as the owner, and the user's own retry, resolve and status read as that member. ``due`` (queries.py) is the one read
that runs as System.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from app.admin.settings import is_disabled
from app.core import scope as scopes
from app.core.db import get_conn
from app.crm.timeline import queries
from app.crm.timeline.facts import (
    MAX_ATTEMPTS,
    RESOLVABLE,
    RETRYABLE,
    TERMINAL,
    Code,
    Frozen,
    State,
    TimelineFact,
)

_RECORD_SQL = f"""
WITH c AS (
  SELECT %(owner_id)s::uuid IS NULL AS ownerless,
         EXISTS (SELECT 1 FROM crm_timeline_events x
                 WHERE x.project_id = %(project_id)s AND x.dedupe_key = %(dedupe_key)s AND x.state <> 'skipped') AS dup)
INSERT INTO crm_timeline_events AS t (source, source_id, organization_id, project_id, owner_id, kind, email,
    occurred_at, dedupe_key, message_id, person, context, state, code, settled_at)
SELECT %(source)s, %(source_id)s, %(organization_id)s, %(project_id)s, %(owner_id)s, %(kind)s, %(email)s,
       %(occurred_at)s, %(dedupe_key)s, %(message_id)s, %(person)s::jsonb, %(context)s::jsonb,
       CASE WHEN ownerless OR dup THEN 'skipped' ELSE 'pending' END,
       CASE WHEN ownerless THEN '{Code.NO_OWNER}' WHEN dup THEN '{Code.DUPLICATE}' END,
       CASE WHEN ownerless OR dup THEN now() END
FROM c
ON CONFLICT (source, source_id) DO UPDATE SET occurred_at = EXCLUDED.occurred_at, message_id = EXCLUDED.message_id
WHERE t.state = 'pending' AND t.request IS NULL
RETURNING xmax = 0 AS inserted"""


def record(conn, facts: Sequence[TimelineFact]) -> int:
    """Put facts in the ledger, in the caller's transaction and scope. Idempotent on ``(source, source_id)``: a fact
    seen again refreshes its time and Message-ID while it is pending and unclaimed. A fact with no owner is recorded
    skipped, and so is one whose ``dedupe_key`` the project already holds. The advisory lock makes the duplicate check
    and the insert one step per key, so a caller inside its own transaction is never rolled back by the unique
    index. Returns the rows inserted."""
    inserted = 0
    # Sorted, so two callers that hold overlapping facts take the locks in one order and never wait on each other.
    for fact in sorted(facts, key=lambda f: (str(f.project_id), f.dedupe_key)):
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'{fact.project_id}:{fact.dedupe_key}',))
        row = conn.execute(
            _RECORD_SQL,
            {
                'source': fact.source.value,
                'source_id': fact.source_id,
                'organization_id': fact.organization_id,
                'project_id': fact.project_id,
                'owner_id': fact.owner_id,
                'kind': fact.kind.value,
                'email': fact.email,
                'occurred_at': fact.occurred_at,
                'dedupe_key': fact.dedupe_key,
                'message_id': fact.message_id,
                'person': json.dumps(_clean(asdict(fact.person))),
                'context': json.dumps(_clean(asdict(fact.context))),
            },
        ).fetchone()
        inserted += bool(row and row['inserted'])
    return inserted


def _clean(values: dict) -> dict:
    return {k: v for k, v in values.items() if v not in (None, '')}


def cursor(conn, project_id: str, org_id: str, source: str) -> int:
    """Where the feed of ``source`` was read up to in the project, locked until the caller's transaction ends: two
    sweeps never read the same page at once."""
    conn.execute(
        'INSERT INTO crm_timeline_cursors (project_id, source, organization_id) VALUES (%s, %s, %s)'
        ' ON CONFLICT DO NOTHING',
        (project_id, source, org_id),
    )
    row = conn.execute(
        'SELECT after FROM crm_timeline_cursors WHERE project_id = %s AND source = %s FOR UPDATE',
        (project_id, source),
    ).fetchone()
    return row['after']


def advance(conn, project_id: str, source: str, next_after: int) -> None:
    conn.execute(
        'UPDATE crm_timeline_cursors SET after = %s, updated_at = now() WHERE project_id = %s AND source = %s',
        (next_after, project_id, source),
    )


_ORPHAN_STATES = "('pending', 'claimed', 'failed', 'uncertain')"


def settle_orphans() -> int:
    """Rows still open, failed or uncertain whose owner was deleted since: nobody's grant can write or retry them, so
    none may show a Retry or Resolve nobody can use. The frozen request stays. Each organization on its own, since
    System only reads."""
    with get_conn() as conn:
        orgs = [
            r['organization_id']
            for r in conn.execute(
                'SELECT DISTINCT organization_id FROM crm_timeline_events WHERE owner_id IS NULL'
                f' AND state IN {_ORPHAN_STATES}'
            ).fetchall()
        ]
    done = 0
    for org in orgs:
        with scopes.acting_for(str(org), 'crm timeline orphans'), get_conn() as conn:
            done += conn.execute(
                "UPDATE crm_timeline_events SET state = 'skipped', code = %s, settled_at = now()"
                f' WHERE owner_id IS NULL AND state IN {_ORPHAN_STATES}',
                (Code.NO_OWNER.value,),
            ).rowcount
    return done


def _key(row: dict) -> tuple[str, str]:
    return row['source'], str(row['source_id'])


def claim(row: dict, frozen: Frozen, portal_id: str | None) -> dict | None:
    """Pending to claimed, freezing the request at the first claim; or a claim gone stale to a new one. A
    compare-and-set on the state and the time seen: ``None`` when another sweep took the row."""
    with get_conn() as conn:
        taken = conn.execute(
            "UPDATE crm_timeline_events SET state = 'claimed', claimed_at = now(),"
            ' request = coalesce(request, %s::jsonb), portal_id = %s'
            " WHERE source = %s AND source_id = %s AND (state = 'pending' OR (state = 'claimed'"
            " AND claimed_at = %s AND claimed_at < now() - interval '1 minute')) RETURNING request",
            (frozen.as_json(), portal_id, *_key(row), row['claimed_at']),
        ).fetchone()
    return taken['request'] if taken else None


def _settle(row: dict, sets: str, args: tuple = (), *, only: str = "state IN ('pending', 'claimed')") -> None:
    with get_conn() as conn:
        conn.execute(
            f'UPDATE crm_timeline_events SET {sets} WHERE source = %s AND source_id = %s AND {only}',
            (*args, *_key(row)),
        )


def settle_logged(row: dict, activity_id: str) -> None:
    _settle(row, "state = 'logged', code = NULL, activity_id = %s, settled_at = now()", (activity_id,))


def release(row: dict) -> None:
    """HubSpot proved it did not take the request: back to pending with the frozen request kept."""
    _settle(row, "state = 'pending'", only="state = 'claimed'")


def settle_refused(row: dict, code: Code) -> None:
    """One more refusal: pending again, or failed at ``MAX_ATTEMPTS``."""
    _settle(
        row,
        "attempts = attempts + 1, state = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END,"
        ' code = CASE WHEN attempts + 1 >= %s THEN %s END,'
        ' settled_at = CASE WHEN attempts + 1 >= %s THEN now() END',
        (MAX_ATTEMPTS, MAX_ATTEMPTS, code.value, MAX_ATTEMPTS),
    )


def settle_uncertain(row: dict) -> None:
    _settle(row, "state = 'uncertain', code = %s, settled_at = now()", (Code.ANSWER_LOST.value,))


def skip(row: dict, code: Code) -> None:
    if code not in TERMINAL:
        raise ValueError(f'{code} is not a terminal skip')
    _settle(row, "state = 'skipped', code = %s, settled_at = now()", (code.value,))


def retry(user_id: str, code: Code) -> int:
    """The user's failed rows with ``code`` go back to pending, and so does their contact write, so a contact
    deleted in HubSpot is read and repaired. As the member."""
    if code not in RETRYABLE:
        return 0
    with get_conn() as conn:
        rows = conn.execute(
            "UPDATE crm_timeline_events SET state = 'pending', code = NULL, attempts = 0, settled_at = NULL"
            " WHERE owner_id = %s AND state = 'failed' AND code = %s RETURNING email",
            (user_id, code.value),
        ).fetchall()
        if rows:
            conn.execute(
                'DELETE FROM crm_contact_writes WHERE owner_id = %s AND email = ANY(%s)',
                (user_id, [r['email'] for r in rows]),
            )
    return len(rows)


def resolve(user_id: str, code: Code, as_: Literal['log_again', 'logged']) -> int:
    """The user's uncertain rows with ``code``: sent again (the user accepts the duplicate) or taken as logged (the
    user accepts that the note may be missing). As the member."""
    if code not in RESOLVABLE:
        return 0
    sets = (
        "state = 'pending', code = NULL, settled_at = NULL"
        if as_ == 'log_again'
        else "state = 'logged', code = NULL, activity_id = '', settled_at = now()"
    )
    with get_conn() as conn:
        return conn.execute(
            f"UPDATE crm_timeline_events SET {sets} WHERE owner_id = %s AND state = 'uncertain' AND code = %s",
            (user_id, code.value),
        ).rowcount


@dataclass(frozen=True, slots=True)
class Issue:
    code: Code
    state: Literal['waiting', 'skipped', 'failed', 'uncertain']
    count: int
    action: Literal['retry', 'resolve'] | None
    latest_at: datetime


@dataclass(frozen=True, slots=True)
class TimelineStatus:
    logged: int
    pending: int
    issues: list[Issue]


_STATUS_SQL = f"""
SELECT CASE WHEN e.state IN ('pending', 'claimed') THEN 'waiting' ELSE e.state END AS state,
       coalesce(e.code, {queries.WAIT_SQL}) AS code, count(*) AS n,
       max(coalesce(e.settled_at, e.recorded_at)) AS latest_at
FROM crm_timeline_events e {queries.JOINS}
WHERE e.owner_id = %(user_id)s OR (e.owner_id IS NULL AND e.project_id = %(project_id)s)
GROUP BY 1, 2"""


def status(user_id: str, project_id: str | None) -> TimelineStatus:
    """The caller's rows, and the active project's ownerless ones, by state and code. In the caller's Member scope."""
    with get_conn() as conn:
        rows = conn.execute(
            _STATUS_SQL,
            {
                **queries.params(datetime.now(UTC), disabled=is_disabled('vendor_disabled:hubspot')),
                'user_id': user_id,
                'project_id': project_id,
            },
        ).fetchall()
    logged = sum(r['n'] for r in rows if r['state'] == State.LOGGED)
    pending = sum(r['n'] for r in rows if r['state'] == 'waiting')
    issues = [
        Issue(
            Code(r['code']),
            r['state'],
            r['n'],
            'retry' if Code(r['code']) in RETRYABLE else 'resolve' if Code(r['code']) in RESOLVABLE else None,
            r['latest_at'],
        )
        for r in rows
        if r['code'] and r['state'] != State.LOGGED
    ]
    return TimelineStatus(logged, pending, sorted(issues, key=lambda i: (i.state, i.code)))
