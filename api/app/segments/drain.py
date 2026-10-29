"""The segments beat (#938): judge again the people whose data changed, so members stay current.

Writers' triggers leave marks in ``segment_dirty`` (api/db/20261021141153_segment_dirty.sql). The task runs as
``System(DUE_WORK)`` (app/worker.py) only to pick the organizations with work, then works each one as ``ForOrg`` of
itself. In each project it tries the project's lock and comes back next beat when a publish, an edit or another beat
holds it. A transaction claims a batch of marks by deleting them, judges their people for every published segment
and commits both, so a crash loses no mark and a second run judges nothing twice.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from celery.exceptions import SoftTimeLimitExceeded

from app.admin.settings import is_disabled
from app.core.db import get_conn
from app.core.scope import acting_for
from app.segments.evaluate import Evaluation, apply, try_lock_project
from app.segments.people import People, stage
from app.segments.task import SOFT_LIMIT_S

logger = structlog.get_logger()

ORGANIZATIONS = 25
BATCH = 500
# Past this many marks, one full evaluation of each segment is cheaper than batches. Retry marks do not count: see
# segment_dirty.retry.
FULL_EVAL_AT = 5_000
# The task's soft limit is 50 s: stop starting work before it.
BUDGET_S = 35.0
# Each statement of an evaluation runs at most the time left until GRACE_S after the budget, counted when the
# evaluation starts. A longer one is cancelled, so one slow statement fails its segment alone instead of reaching the
# soft limit, which rolls the whole beat back. The beat commits the rest in the margin.
COMMIT_MARGIN_S = 5.0
GRACE_S = SOFT_LIMIT_S - BUDGET_S - COMMIT_MARGIN_S
# A held charge settles with no write to the row, so a person judged while a cell is pending is judged again later.
RETRY_PENDING_S = 60
PAUSED = 'segments_evaluation_paused'
# When a publish or a rule edit owes its catch-up: later than the longest write transaction can last, so a write that
# raced the evaluation has committed. No pool timeout ends a transaction; a task's does, and the longest is 960 s.
CATCH_UP = timedelta(minutes=20)
# A segment the drain cannot judge now, frozen or failing, owes a full evaluation this much later: its marks are gone.
RETRY_FULL = timedelta(minutes=5)

_LIVE = 'published_at IS NOT NULL AND deleted_at IS NULL'
# least() skips a NULL.
_OWED_AT = 'least(full_due_at, full_now_at)'
_OWED = f'{_OWED_AT} <= now() AND {_LIVE}'

DUE_SQL = f"""
SELECT organization_id FROM (
    SELECT organization_id, min(id) AS first_id FROM segment_dirty WHERE not_before <= now() GROUP BY organization_id
    UNION ALL
    SELECT DISTINCT organization_id, 0 FROM segments WHERE {_OWED}
) w GROUP BY organization_id ORDER BY min(first_id) LIMIT %(n)s
"""

_PROJECTS = f"""
SELECT w.project_id, p.id IS NOT NULL AS live FROM (
    SELECT project_id, min(id) AS first_id FROM segment_dirty WHERE not_before <= now() GROUP BY project_id
    UNION ALL
    SELECT DISTINCT project_id, 0 FROM segments WHERE {_OWED}
) w LEFT JOIN projects p ON p.id = w.project_id GROUP BY w.project_id, p.id ORDER BY min(w.first_id), w.project_id
"""

# = ANY(ARRAY(...)) deletes by key. With IN (...), a low row estimate made the planner compare each mark with
# every other one: 45 ms for 500 marks.
_CLAIM = """
DELETE FROM segment_dirty WHERE id = ANY(ARRAY(
    SELECT id FROM segment_dirty WHERE project_id = %s AND not_before <= now() ORDER BY id LIMIT %s
)) RETURNING list_row_id, identity_id
"""

# The marked identities of the project, with every identity a merge folded into one of them and the one it folded
# into: a merge's survivor is judged in the batch that carries the folded member over, never before it.
_IDENTITIES = """
WITH RECURSIVE marked AS (
    SELECT a.identity_id AS id FROM audience_row_links a WHERE a.list_row_id = ANY(%(rows)s::uuid[])
    UNION SELECT unnest(%(identities)s::uuid[])
),
closure AS (
    SELECT i.id, i.merged_into FROM audience_identities i JOIN marked m ON m.id = i.id
    WHERE i.project_id = %(project)s
    UNION
    SELECT i.id, i.merged_into FROM closure c JOIN audience_identities i ON i.merged_into = c.id OR i.id = c.merged_into
)
SELECT DISTINCT id FROM closure
"""


def due_organizations() -> list[str]:
    with get_conn() as conn:
        return [str(r['organization_id']) for r in conn.execute(DUE_SQL, {'n': ORGANIZATIONS}).fetchall()]


def _published(conn, project_id: UUID) -> list[dict]:
    return conn.execute(
        f'SELECT * FROM segments WHERE project_id = %s AND {_LIVE} ORDER BY id', (project_id,)
    ).fetchall()


def _identities(conn, project_id: UUID, marks: list[dict]) -> list[UUID]:
    rows = [m['list_row_id'] for m in marks if m['list_row_id'] is not None]
    named = [m['identity_id'] for m in marks if m['identity_id'] is not None]
    found = conn.execute(_IDENTITIES, {'rows': rows, 'identities': named, 'project': project_id}).fetchall()
    return [r['id'] for r in found]


def _remark(conn, project_id: UUID, pending: Sequence[UUID]) -> int:
    if not pending:
        return 0
    return conn.execute(
        'INSERT INTO segment_dirty (organization_id, project_id, identity_id, not_before, retry)'
        ' SELECT i.organization_id, i.project_id, i.id, now() + make_interval(secs => %s), true'
        ' FROM audience_identities i WHERE i.id = ANY(%s::uuid[]) AND NOT EXISTS ('
        '  SELECT 1 FROM segment_dirty d WHERE d.project_id = %s AND d.identity_id = i.id AND d.not_before > now())',
        (RETRY_PENDING_S, list(pending), project_id),
    ).rowcount


def _judge(conn, segment: dict, deadline: float, people: People | None = None) -> Evaluation | None:
    """None when the segment cannot be judged now. It must not stop the other segments, and the marks this step
    consumed come back as a full evaluation later. Each statement of the evaluation runs at most the time left until
    ``GRACE_S`` after ``deadline``, counted here. A statement that runs longer is cancelled, and the segment fails."""
    # At least 1 ms: 0 turns the timeout off.
    left_ms = max(1, round((deadline + GRACE_S - time.monotonic()) * 1000))
    try:
        with conn.transaction():
            conn.execute(f'SET LOCAL statement_timeout = {left_ms}')
            done = apply(conn, segment, 'enter', people=people)
            # A local setting outlives the release of its savepoint.
            conn.execute('SET LOCAL statement_timeout TO DEFAULT')
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.error(
            'segments.drain.segment_failed',
            project_id=str(segment['project_id']),
            segment_id=str(segment['id']),
            error_type=type(exc).__name__,
        )
        done = None
    if done is not None and not done.frozen:
        return done
    if people is None:
        # The full evaluation owed now moves on, or the next step picks this segment again.
        conn.execute(
            'UPDATE segments SET full_now_at = now() + %s,'
            ' full_due_at = CASE WHEN full_due_at <= now() THEN NULL ELSE full_due_at END WHERE id = %s',
            (RETRY_FULL, segment['id']),
        )
    else:
        # Every batch can fail it again: a later batch never moves the owed evaluation later.
        conn.execute(
            'UPDATE segments SET full_now_at = least(full_now_at, now() + %s) WHERE id = %s',
            (RETRY_FULL, segment['id']),
        )
    return None


def full_due(conn, project_id: UUID, deadline: float) -> bool:
    if not try_lock_project(conn, project_id):
        return False
    segment = conn.execute(
        f'SELECT * FROM segments WHERE project_id = %s AND {_OWED} ORDER BY {_OWED_AT}, id LIMIT 1', (project_id,)
    ).fetchone()
    if segment is None:
        return False
    started = time.monotonic()
    done = _judge(conn, segment, deadline)
    if done is None:
        return True
    conn.execute(
        'UPDATE segments SET full_now_at = NULL,'
        ' full_due_at = CASE WHEN full_due_at <= now() THEN NULL ELSE full_due_at END WHERE id = %s',
        (segment['id'],),
    )
    remarked = _remark(conn, project_id, done.deferred)
    logger.info(
        'segments.drain.full',
        project_id=str(project_id),
        segment_id=str(segment['id']),
        entered=done.applied.entered,
        exited=done.applied.exited,
        pending=remarked,
        ms=round((time.monotonic() - started) * 1000, 1),
    )
    return True


def batch(conn, project_id: UUID, deadline: float) -> int | None:
    """Claims up to ``BATCH`` marks of the project and judges their people for every published segment, in one
    transaction."""
    if not try_lock_project(conn, project_id):
        return None
    started = time.monotonic()
    due = conn.execute(
        'SELECT count(*) AS n FROM (SELECT 1 FROM segment_dirty WHERE project_id = %s AND not_before <= now()'
        ' AND NOT retry LIMIT %s) d',
        (project_id, FULL_EVAL_AT + 1),
    ).fetchone()['n']
    if due > FULL_EVAL_AT:
        claimed = conn.execute(
            'DELETE FROM segment_dirty WHERE project_id = %s AND not_before <= now()', (project_id,)
        ).rowcount
        owed = conn.execute(
            f'UPDATE segments SET full_now_at = now() WHERE project_id = %s AND {_LIVE}', (project_id,)
        ).rowcount
        logger.info('segments.drain.fallback', project_id=str(project_id), marks=claimed, segments=owed)
        return claimed
    marks = conn.execute(_CLAIM, (project_id, BATCH)).fetchall()
    if not marks:
        return 0
    segments = _published(conn, project_id)
    entered = exited = remarked = judged = 0
    if segments:
        people = stage(conn, project_id, _identities(conn, project_id, marks))
        for segment in segments:
            done = _judge(conn, segment, deadline, people)
            if done is not None:
                entered, exited = entered + done.applied.entered, exited + done.applied.exited
        remarked, judged = _remark(conn, project_id, people.deferred), len(people.identities)
    logger.info(
        'segments.drain.batch',
        project_id=str(project_id),
        marks=len(marks),
        identities=judged,
        segments=len(segments),
        entered=entered,
        exited=exited,
        pending=remarked,
        ms=round((time.monotonic() - started) * 1000, 1),
    )
    return len(marks)


def _committed[T](step: Callable[[Any, UUID, float], T], project_id: UUID, deadline: float) -> T:
    with get_conn() as conn:
        return step(conn, project_id, deadline)


def drain_project(project_id: UUID, deadline: float) -> None:
    """The project's owed full evaluations first, then its marks, each step in its own transaction, until none is
    left or the deadline passes."""
    while time.monotonic() < deadline:
        while time.monotonic() < deadline and _committed(full_due, project_id, deadline):
            pass
        if not _committed(batch, project_id, deadline):
            return


def drain_organization(deadline: float) -> int:
    """Every project of the current organization with work. Marks of a deleted project are dropped. Returns the
    projects worked."""
    with get_conn() as conn:
        projects = conn.execute(_PROJECTS).fetchall()
        gone = [p['project_id'] for p in projects if not p['live']]
        if gone:
            conn.execute('DELETE FROM segment_dirty WHERE project_id = ANY(%s::uuid[])', (gone,))
    worked = 0
    for p in projects:
        if not p['live'] or time.monotonic() >= deadline:
            continue
        try:
            drain_project(p['project_id'], deadline)
            worked += 1
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.error(
                'segments.drain.project_failed', project_id=str(p['project_id']), error_type=type(exc).__name__
            )
    return worked


def run() -> dict:
    """Beat task ``evaluate_segments``. While the admin switch ``segments_evaluation_paused`` is on it does nothing,
    and the marks and owed evaluations wait."""
    deadline = time.monotonic() + BUDGET_S
    if is_disabled(PAUSED):
        return {'paused': True, 'organizations': 0, 'projects': 0}
    organizations = projects = 0
    for org in due_organizations():
        if time.monotonic() > deadline:
            break
        try:
            with acting_for(org, 'segments evaluate'):
                projects += drain_organization(deadline)
            organizations += 1
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:  # one organization must not stop the others
            logger.error('segments.drain.organization_failed', org_id=org, error_type=type(exc).__name__)
    return {'paused': False, 'organizations': organizations, 'projects': projects}
