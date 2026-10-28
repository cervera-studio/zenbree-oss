"""The insert of a queued run (#939, #1031, #1030): pinned to the automation's current version, at one slot of
``automation_runs_scheduled_key``. A manual run, a schedule, a webhook call and a signal each insert here, under the
automation's lock, so ``automation_runs_one_live`` never refuses one.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.types.json import Jsonb

from app.automations.triggers import TriggerKind

# What the API shows of a run: not its credit hold, its definition or the people it took.
RUN_COLUMNS = (
    'id, automation_id, version, trigger_kind, status, cancel_requested, credit_cap, credits_spent, review,'
    ' approved_at, summary, error_code, created_at, started_at, finished_at'
)


def insert_run(
    conn,
    automation: dict,
    when: datetime | None,
    kind: TriggerKind,
    target_row_id: str | None = None,
    target_account_id: str | None = None,
) -> dict | None:
    """A new queued run, or ``None`` when its slot is taken. ``when=None``: just after the automation's latest slot, so
    a webhook run never collides with another run while a schedule keeps its exact slot."""
    return conn.execute(
        'INSERT INTO automation_runs (organization_id, automation_id, version, definition, credit_cap, trigger_kind,'
        ' target_row_id, target_account_id, scheduled_for) VALUES (%(org)s, %(id)s, %(version)s, %(definition)s,'
        ' %(cap)s, %(kind)s, %(target)s, %(account)s, coalesce(%(when)s::timestamptz, greatest(clock_timestamp(),'
        " (SELECT max(scheduled_for) + interval '1 microsecond' FROM automation_runs WHERE automation_id = %(id)s))))"
        f' ON CONFLICT (automation_id, scheduled_for) DO NOTHING RETURNING {RUN_COLUMNS}',
        {
            'org': automation['organization_id'],
            'id': automation['id'],
            'version': automation['version'],
            'definition': Jsonb(automation['definition']),
            'cap': automation['credit_cap'],
            'kind': kind,
            'target': target_row_id,
            'account': target_account_id,
            'when': when,
        },
    ).fetchone()
