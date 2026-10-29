"""The one row of a person list that a webhook call names (#1031): looked up, never created.

A person row is keyed by its LinkedIn URL, else its email (``lists.rows.dedupe_key``), so the key finds a row that the
call names the way the row is keyed. A row keyed by LinkedIn that the call names by email only is found through the
audience identity that holds both (``audience_row_links``). LinkedIn comes first at each step: it names one person,
an email can be shared.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from app.audience.follow import find_identity
from app.automations.models import InboundPerson
from app.lists.rows import norm_url

MatchedBy = Literal['linkedin_url', 'email']


class Match(NamedTuple):
    row_id: str
    matched_by: MatchedBy


def identifiers(person: InboundPerson) -> list[tuple[MatchedBy, str]]:
    """What the call names, LinkedIn first, in the form a row's key has. Empty: it names nobody."""
    found: list[tuple[MatchedBy, str]] = []
    if linkedin := norm_url(person.linkedin_url):
        found.append(('linkedin_url', linkedin))
    if email := (person.email or '').strip().lower():
        found.append(('email', email))
    return found


def find(conn, project_id: str, list_id: str, named: list[tuple[MatchedBy, str]]) -> Match | None:
    for kind, value in named:
        row = conn.execute(
            'SELECT id::text FROM list_rows WHERE list_id = %s AND dedupe_key = %s', (list_id, value)
        ).fetchone()
        if row:
            return Match(row['id'], kind)
    for kind, value in named:
        identity = find_identity(conn, project_id, kind, value, entity='person')
        row = (
            identity
            and conn.execute(
                'SELECT r.id::text FROM audience_row_links l JOIN list_rows r ON r.id = l.list_row_id'
                ' WHERE l.identity_id = %s AND r.list_id = %s ORDER BY r.seq LIMIT 1',
                (identity, list_id),
            ).fetchone()
        )
        if row:
            return Match(row['id'], kind)
    return None
