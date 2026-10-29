from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.segments.compile import STAGE, STAGED

_FACTS = f"""
SELECT EXISTS (SELECT 1 FROM audience_identities WHERE id = ANY(%s::uuid[]) AND merged_into IS NOT NULL) AS folded,
       ARRAY(SELECT DISTINCT identity_id FROM {STAGED} WHERE pending) AS deferred
"""


@dataclass(frozen=True, slots=True)
class People:
    identities: list[UUID]
    folded: bool
    deferred: list[UUID]


def stage(conn, project_id: UUID | str, identities: Sequence[UUID]) -> People:
    ids = list(identities)
    conn.execute(f'DROP TABLE IF EXISTS pg_temp.{STAGED}')
    conn.execute(STAGE, {'project': project_id, 'identities': ids})
    facts = conn.execute(_FACTS, (ids,)).fetchone()
    return People(identities=ids, folded=facts['folded'], deferred=list(facts['deferred']))
