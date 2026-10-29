"""What a rule edit of a published segment changes (#938), as SQL that ``evaluate.apply`` and ``evaluate.preview``
share, so the answer before an edit is the edit's by construction.

The rule the segment had before the edit is the CTE ``was``, judged in the same statement as the new rule. Each
statement reads what committed before it, and cell writers do not take the project's lock: judged in one snapshot, a
change that the old rule also makes is the data's, never the edit's. With no rule before, or a frozen one that judges
nobody, ``was`` is empty, ``%(judged_was)s`` is false, and every change is the caller's."""

from __future__ import annotations

from psycopg import sql

NOBODY = sql.SQL('SELECT NULL::uuid AS identity_id, false AS matched, false AS deferred WHERE false')

_ASKED_JOIN = 'NOT EXISTS (SELECT 1 FROM was w WHERE w.identity_id = {id} AND w.matched AND NOT w.deferred)'
_ASKED_EXIT = (
    '(NOT %(judged_was)s OR EXISTS (SELECT 1 FROM was w WHERE w.identity_id = {id} AND (w.matched OR w.deferred)))'
)

# The members as the merge carry would leave them, with no write.
DELTA_CTES = """,
was AS MATERIALIZED ({was}),
hop AS (
    SELECT m.identity_id AS id FROM segment_members m WHERE m.segment_id = %(segment)s AND m.exited_at IS NULL
    UNION
    SELECT i.merged_into FROM hop h JOIN audience_identities i ON i.id = h.id WHERE i.merged_into IS NOT NULL
),
live AS (SELECT h.id FROM hop h JOIN audience_identities i ON i.id = h.id WHERE i.merged_into IS NULL)"""

# The joins and the exits that ``apply`` makes from ``live``, counted when the edit asked for them.
DELTA = """,
       (SELECT count(*) FROM judged j WHERE j.matched AND NOT j.deferred
          AND NOT EXISTS (SELECT 1 FROM live l WHERE l.id = j.identity_id) AND {asked_join}) AS joining,
       (SELECT count(*) FROM live l
        WHERE NOT EXISTS (SELECT 1 FROM judged j WHERE j.identity_id = l.id AND (j.matched OR j.deferred))
          AND {asked_exit}) AS leaving"""


def asked(join_id: str, exit_id: str) -> dict[str, sql.Composable]:
    """Whether the edit made the join of ``join_id`` and the exit of ``exit_id``, given what the old rule makes."""
    return {
        'asked_join': sql.SQL(_ASKED_JOIN.format(id=join_id)),
        'asked_exit': sql.SQL(_ASKED_EXIT.format(id=exit_id)),
    }
