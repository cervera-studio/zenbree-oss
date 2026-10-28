"""Link list rows and provider ids to an identity (#937). The rules live in SQL (``audience_link_row``,
``audience_attach_alias``), so every writer, including a raw one, resolves the same way.

An identity belongs to one project. A row with no exact alias gets none, and two identities that one row ties
together become a conflict (``app.audience.conflicts``), never a merge.

A writer of cells never links in its own transaction: linking takes alias locks, and a delivery transaction that waits
on them while it holds row and credit locks can deadlock with another one. The writer marks the rows
(``mark_rows``, one insert) and ``link_pending`` links them after its commit, one row to a transaction. Until a row
is linked, the checks that read its own cells (its email, LinkedIn URL and domain against the exclusion rules) are as
strict as before; only the following of its other aliases waits.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Literal

import psycopg
import structlog
from celery.exceptions import SoftTimeLimitExceeded

from app.core.db import get_conn

logger = structlog.get_logger()

# The cells that can hold an exact alias: a write that touches none of them cannot change an identity.
IDENTITY_KEYS = frozenset({'email', 'linkedin_url', 'domain', 'website'})
Attached = Literal['attached', 'known', 'conflict']
# Rows one beat links in an organization: the rest wait for the next beat, so a bulk import cannot hold the beat.
BATCH = 200
# A link that waits longer than this on another link's value gives up and stays marked: whatever order two links take
# locks in, a cycle ends one link's transaction and never a writer's.
LOCK_TIMEOUT = '5s'
# A row whose link failed this many times since its last write is left unlinked (and protected by its own cells) until
# a write marks it again, so one poison row cannot hold a beat for ever.
MAX_ATTEMPTS = 5
# Passes of one link in a row: each loses a race only to a turn that took the same value or merged in between.
PASSES = 3


def mark_rows(conn, row_ids: Iterable[str], source: str | None = None) -> None:
    """Mark rows for ``link_pending``, in the writer's transaction. ``source`` names who wrote the new aliases when
    the row's own ``source`` cell does not (a hand edit: ``manual``)."""
    ids = sorted({str(r) for r in row_ids})
    if ids:
        conn.execute('SELECT audience_mark_rows(%s::uuid[], %s)', (ids, source))


def link_pending(limit: int = BATCH, deadline: float | None = None) -> int:
    """Link the oldest marked rows of the scope's organization, each in a transaction of its own, and return how
    many were linked. A row that fails stays marked, behind the others, until ``MAX_ATTEMPTS``. Stops starting rows
    at ``deadline`` (a ``time.monotonic()`` value); the rest wait for the next beat."""
    with get_conn() as conn:
        marked = conn.execute(
            'SELECT list_row_id::text AS id, source, queued_at FROM audience_link_queue WHERE attempts < %s'
            ' ORDER BY queued_at, list_row_id LIMIT %s',
            (MAX_ATTEMPTS, limit),
        ).fetchall()
    linked = 0
    for mark in marked:
        if deadline is not None and time.monotonic() > deadline:
            break
        try:
            _link_once(mark)
            linked += 1
        except SoftTimeLimitExceeded:
            raise  # the task's own deadline: not this row's failure
        except Exception as exc:  # one row must not stop the others; never the message, which can quote a value
            logger.warning('audience.link_failed', row_id=mark['id'], error_type=type(exc).__name__)
            _failed(mark['id'])
    return linked


def _link_once(mark: dict) -> None:
    """Link one marked row. SQL raises ``serialization_failure`` when another turn changed what it read (a value taken,
    an identity merged): the transaction ends with every lock released, and the row runs again in a new one."""
    for attempt in range(PASSES):
        try:
            with get_conn() as conn:
                conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                conn.execute('SELECT audience_link_row(%s, %s)', (mark['id'], mark['source']))
                # Only the mark this link read: a write that marked the row again since stays marked.
                conn.execute(
                    'DELETE FROM audience_link_queue WHERE list_row_id = %s AND queued_at = %s',
                    (mark['id'], mark['queued_at']),
                )
            return
        except psycopg.errors.SerializationFailure:
            if attempt == PASSES - 1:
                raise


def _failed(row_id: str) -> None:
    """Count the failure and move the row behind the others; at the cap, say so."""
    try:
        with get_conn() as conn:
            attempts = conn.execute(
                'UPDATE audience_link_queue SET attempts = attempts + 1, queued_at = clock_timestamp()'
                ' WHERE list_row_id = %s RETURNING attempts',
                (row_id,),
            ).fetchone()
        if attempts and attempts['attempts'] >= MAX_ATTEMPTS:
            logger.warning('audience.link_gave_up', row_id=row_id, attempts=attempts['attempts'])
    except Exception as exc:
        logger.error('audience.requeue_failed', row_id=row_id, error_type=type(exc).__name__)


def attach_new_alias(
    conn, project_id: str, entity: str, kind: str, namespace: str, value: str, source: str
) -> tuple[str, Attached]:
    """Link a person or account that no list row holds (an external lead) in the project: the identity that
    already holds the alias (``known``), else a new one that takes it (``attached``). Returns its id and which."""
    row = conn.execute(
        'SELECT identity_id::text AS id, result FROM audience_attach_new(%s, %s, %s, %s, %s, %s)',
        (project_id, entity, kind, namespace, value, source),
    ).fetchone()
    return row['id'], row['result']


def attach_alias(conn, identity_id: str, kind: str, namespace: str, value: str, source: str) -> Attached:
    """Record a provider id (``crm_object``: namespace ``hubspot:<portal>:contact``; ``external_lead``: namespace
    ``<provider>:<workspace>``) or an exact alias on an identity. A value another identity holds becomes a conflict."""
    return conn.execute(
        'SELECT audience_attach_alias(%s, %s, %s, %s, %s) AS r', (identity_id, kind, namespace, value, source)
    ).fetchone()['r']
