"""Follow an identity: every alias we know for a person or account, for the checks that must not miss one.

``excluded_aliases`` makes a project's exclusion rules reach the other aliases of the person they name, and
``alias_map`` lets enrollment tell that two of its values are one person. Both read only the aliases of one
project. Billing and delivery do not use them: a row stays a row, and the same email in two lists still counts twice.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.audience.aliases import RULE_KINDS, entity_of, normalize

AliasMap = dict[tuple[str, str], str]  # (kind, value) of every alias -> its identity id


def alias_map(conn, project_id, values: Iterable[tuple[str, str | None]]) -> AliasMap:
    """The identity of every alias held by an identity that holds any of ``values`` (``(kind, raw value)``), read
    in the entity the value names (``entity_of``). Values that are not exact aliases, or that no identity holds,
    are left out. Provider ids are not listed: only the kinds an exclusion or an enrollment compares."""
    wanted = {(k, v) for k, raw in values if k in RULE_KINDS and (v := normalize(k, raw))}
    if not wanted:
        return {}
    kinds, vals = zip(*sorted(wanted), strict=True)
    entities = [entity_of(k, v) for k, v in zip(kinds, vals, strict=True)]
    rows = conn.execute(
        'SELECT a.kind, a.value, a.identity_id::text AS identity_id FROM audience_aliases a WHERE a.project_id = %s'
        " AND a.kind = ANY(%s) AND a.namespace = '' AND a.identity_id IN (SELECT h.identity_id FROM audience_aliases h"
        ' JOIN unnest(%s::text[], %s::text[], %s::text[]) u(kind, value, entity)'
        " ON h.entity = u.entity AND h.kind = u.kind AND h.value = u.value AND h.namespace = ''"
        ' WHERE h.project_id = %s)',
        (project_id, list(RULE_KINDS), list(kinds), list(vals), entities, project_id),
    ).fetchall()
    return {(r['kind'], r['value']): r['identity_id'] for r in rows}


def held_values(targets: Iterable[dict], sent: Iterable[dict]) -> list[tuple[str, str | None]]:
    """The ``(kind, raw value)`` of every person a campaign already holds: its targets and the recipients it sent to."""
    return [(k, t[k]) for t in targets for k in ('email', 'linkedin_url')] + [
        ('email' if s['channel'] == 'email' else 'linkedin_url', s['recipient']) for s in sent
    ]


def held_pair(identity, email, linkedin) -> tuple[tuple[str, str], tuple[str, str]]:
    """One held person's ``(kind, value)`` pairs, each normalized on its own by the caller's ``identity``."""
    return (('email', identity(email, '')[0]), ('linkedin_url', identity('', linkedin)[1]))


def sent_pair(identity, sent: dict) -> tuple[tuple[str, str], tuple[str, str]]:
    """``held_pair`` of a send's recipient: an email for the email channel, a LinkedIn URL for the others."""
    email = sent['channel'] == 'email'
    return held_pair(identity, sent['recipient'] if email else '', '' if email else sent['recipient'])


def excluded_aliases(conn, project_id, rules: Iterable[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    """Exclusion keys for every other alias of the people and accounts that ``rules`` name."""
    return frozenset(alias_map(conn, project_id, rules))


def with_aliases(conn, project_id, rules: Iterable[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    """``rules`` and the other aliases of the people and accounts they name."""
    rules = frozenset(rules)
    return rules | excluded_aliases(conn, project_id, rules)


def same_person(ids: AliasMap, a: Iterable[tuple[str, str | None]], b: Iterable[tuple[str, str | None]]) -> bool:
    """True when two sets of ``(kind, value)`` share a value, or hold values of one identity."""

    def stored(pairs):
        return {(k, v) for k, raw in pairs if raw and (v := normalize(k, raw) or raw.strip().lower())}

    left, right = stored(a), stored(b)
    if not left.isdisjoint(right):
        return True
    return bool({ids[p] for p in left if p in ids} & {ids[p] for p in right if p in ids})


def find_identity(
    conn, project_id, kind: str, value: str, namespace: str = '', entity: str | None = None
) -> str | None:
    """The identity that holds exactly this alias in the project. An email, LinkedIn URL or domain is normalized
    first and read in the entity it names (``entity_of``); a provider id (``crm_object``, ``external_lead``) matches
    as written, in its namespace, in ``entity`` (either one when omitted)."""
    stored = normalize(kind, value) if kind in RULE_KINDS else (value or '').strip()
    if not stored:
        return None
    if entity is None and kind in RULE_KINDS:
        entity = entity_of(kind, stored)
    row = conn.execute(
        'SELECT identity_id::text AS id FROM audience_aliases'
        ' WHERE project_id = %s AND kind = %s AND namespace = %s AND value = %s AND (%s::text IS NULL OR entity = %s)'
        ' ORDER BY entity LIMIT 1',
        (project_id, kind, namespace, stored, entity, entity),
    ).fetchone()
    return row['id'] if row else None
