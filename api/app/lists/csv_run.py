"""The CSV import as a worker job (#344): ``routes`` stores the file in ``csv_imports``, ``run`` imports it in chunks
and publishes its progress to a Redis stream the dialog tails, so closing the dialog doesn't stop it."""

from __future__ import annotations

import structlog

from app.agent import events
from app.agent.language import user_locale
from app.core.db import get_conn
from app.lists.csv_import import CsvError, read_csv
from app.lists.csv_plan import discarded_csv, make_plan
from app.lists.rows import add_rows, create_list

logger = structlog.get_logger()

CHUNK = 100
STREAM_TTL_SECONDS = 3600
TERMINAL = ('done', 'failed')


def stream_key(import_id: str) -> str:
    return f'csv_import:{import_id}:events'


def _publish(import_id: str, event: str, data: dict) -> None:
    events.append(stream_key(import_id), event, data, maxlen=1000, ttl=STREAM_TTL_SECONDS)


def run(import_id: str) -> None:
    with get_conn() as conn:
        imp = conn.execute(
            "UPDATE csv_imports SET status = 'running', updated_at = now() WHERE id = %s AND status = 'queued'"
            ' RETURNING *',
            (import_id,),
        ).fetchone()
    if not imp:  # redelivered after it ran
        return
    # ponytail: a worker killed mid-run leaves the import `running` and its dialog open; add a beat reaper if it bites.
    try:
        _import(imp)
    except Exception as exc:
        code = exc.code if isinstance(exc, CsvError) else 'LIST.IMPORT_FAILED'
        logger.error('csv_import.failed', import_id=import_id, error_type=type(exc).__name__)
        with get_conn() as conn:
            conn.execute(
                "UPDATE csv_imports SET status = 'failed', error = %s, content = NULL, updated_at = now() WHERE id = %s",
                (code, import_id),
            )
        _publish(import_id, 'failed', {'code': code})


def _import(imp: dict) -> None:
    import_id, entity = str(imp['id']), imp['entity']
    records, rows, skipped = read_csv(imp['content'], entity, imp['mapping'])
    with get_conn() as conn:
        plan = make_plan(conn, str(imp['project_id']), entity, rows, skipped)
        sid = str(
            conn.execute(
                'INSERT INTO chat_sessions (organization_id, user_id, project_id, title)'
                ' VALUES (%s, %s, %s, %s) RETURNING id',
                (imp['organization_id'], imp['user_id'], imp['project_id'], imp['name'][:80]),
            ).fetchone()['id']
        )
        list_id = create_list(conn, user_id=str(imp['user_id']), session_id=sid, name=imp['name'], entity=entity)
        conn.execute("UPDATE lists SET mode = 'enrich' WHERE id = %s", (list_id,))  # #42: the user's own rows
        conn.execute(
            'UPDATE csv_imports SET total = %s, duplicates = %s, in_lists = %s, excluded = %s, skipped = %s,'
            ' list_id = %s, chat_session_id = %s, discarded = %s, updated_at = now() WHERE id = %s',
            (
                len(plan.keep),
                plan.count('in_file'),
                plan.count('in_lists'),
                plan.count('excluded'),
                plan.count('no_identity'),
                list_id,
                sid,
                discarded_csv(records, plan.discarded, user_locale(conn, str(imp['user_id'])))
                if plan.discarded
                else None,
                import_id,
            ),
        )
    total, added = len(plan.keep), 0
    _publish(import_id, 'progress', {'processed': 0, 'total': total})
    for start in range(0, total, CHUNK):
        chunk = plan.keep[start : start + CHUNK]
        with get_conn() as conn:
            new_ids = add_rows(conn, list_id, entity, chunk)
            # Uploaded values are the user's: marked manual, so a later vendor merge never overwrites them.
            conn.execute(
                'UPDATE list_rows SET cell_meta = (SELECT jsonb_object_agg(k,'
                " jsonb_build_object('status', 'manual', 'by', %s::text)) FROM jsonb_object_keys(cells) AS k)"
                ' WHERE id = ANY(%s)',  # #963: `by`, so the importer's own hosts name sites
                (str(imp['user_id']), new_ids),
            )
            added += len(new_ids)
            conn.execute(
                'UPDATE csv_imports SET processed = %s, rows_added = %s, updated_at = now() WHERE id = %s',
                (start + len(chunk), added, import_id),
            )
        _publish(import_id, 'progress', {'processed': start + len(chunk), 'total': total})
    with get_conn() as conn:
        conn.execute(
            "UPDATE csv_imports SET status = 'done', content = NULL, updated_at = now() WHERE id = %s", (import_id,)
        )
    _publish(import_id, 'done', {'list_id': list_id, 'chat_session_id': sid, 'rows_added': added})


STUCK_MINUTES = 15
# A queued import waits behind other jobs on the default queue; Redis redelivers it within the visibility timeout (2 h).
QUEUED_MINUTES = 120
KEEP_DAYS = 7


def reap() -> int:
    """Beat janitor. A run whose worker died stays running (or queued, if its message was lost): fail it and drop the file, which holds
    the user's contacts. Every chunk touches ``updated_at``, so a live run is never this old. The discarded rows
    (the do-not-contact list among them) are kept ``KEEP_DAYS`` after the run, no longer."""
    with get_conn() as conn:
        stuck = conn.execute(
            "UPDATE csv_imports SET status = 'failed', error = 'LIST.IMPORT_INTERRUPTED', content = NULL,"
            " updated_at = now() WHERE updated_at < now() - make_interval(mins => CASE status WHEN 'queued' THEN %s ELSE %s END)"
            " AND status IN ('queued', 'running') RETURNING id",
            (QUEUED_MINUTES, STUCK_MINUTES),
        ).fetchall()
        conn.execute(
            'UPDATE csv_imports SET discarded = NULL, content = NULL WHERE (discarded IS NOT NULL OR content IS NOT NULL)'
            " AND status IN ('done', 'failed') AND updated_at < now() - make_interval(days => %s)",
            (KEEP_DAYS,),
        )
    for row in stuck:
        _publish(str(row['id']), 'failed', {'code': 'LIST.IMPORT_INTERRUPTED'})
    return len(stuck)
