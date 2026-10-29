from __future__ import annotations

from psycopg.types.json import Jsonb


class RetryNotFound(LookupError):
    pass


class RetryStale(ValueError):
    pass


def begin(conn, *, user_id: str, session_id: str, source_job_id: str, stage: int, job_id: str) -> None:
    source = conn.execute(
        'SELECT status, stages, progress_done, progress_total FROM jobs'
        ' WHERE id = %s AND user_id = %s AND chat_session_id = %s FOR UPDATE',
        (source_job_id, user_id, session_id),
    ).fetchone()
    if not source:
        raise RetryNotFound
    latest = conn.execute(
        'SELECT id::text FROM jobs WHERE chat_session_id = %s AND id <> %s ORDER BY created_at DESC LIMIT 1',
        (session_id, job_id),
    ).fetchone()
    stages = source['stages']
    if (
        not latest
        or latest['id'] != source_job_id
        or source['status'] in ('queued', 'running')
        or not isinstance(stages, list)
        or not 0 <= stage < len(stages)
    ):
        raise RetryStale
    if not stages[stage].get('error'):
        raise RetryStale
    moved = conn.execute(
        'UPDATE list_plans SET approved_job_id = %s WHERE approved_job_id = %s', (job_id, source_job_id)
    ).rowcount
    if not moved:
        raise RetryStale
    conn.execute(
        'UPDATE jobs SET retry_of = %s, retry_stage = %s, stages = %s, progress_done = %s, progress_total = %s'
        ' WHERE id = %s',
        (source_job_id, stage, Jsonb(stages), source['progress_done'], source['progress_total'], job_id),
    )


def spent_before(conn, job_id: str) -> int:
    return conn.execute(
        'WITH RECURSIVE chain AS ('
        ' SELECT id, retry_of, credits_spent FROM jobs WHERE id = %s'
        ' UNION ALL SELECT j.id, j.retry_of, j.credits_spent FROM jobs j JOIN chain c ON j.id = c.retry_of)'
        ' SELECT coalesce(sum(credits_spent), 0)::int AS n FROM chain WHERE id <> %s',
        (job_id, job_id),
    ).fetchone()['n']


def prompt(stages: list[dict], stage: int, credits_left: int) -> str:
    s = stages[stage]
    return (
        f'RETRY: this turn retries stage {stage + 1}, "{s["label"]}" (tool {s["tool"]}), which failed with '
        f'{s["done"]} of {s["units"]} done. The plan in <chat_state> is approved and its other stages already ran: '
        f'do not repeat them. Run only that tool, for the rows that still lack its result, spending at most '
        f'{credits_left} credits, then summarise. Do not ask for approval again.'
    )
