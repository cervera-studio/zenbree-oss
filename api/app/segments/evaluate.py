"""Segment membership (#938): preview a rule, keep a published segment's members, and explain one person's verdict.

``apply`` is the only writer of ``segment_members`` and ``segment_transitions``. It judges every identity of the
project with the compiled rule and writes only the difference, under ``lock_project``, so a second run on the same
data writes nothing. An identity with a cell under a held charge is neither joined nor exited until the charge
settles. A segment whose rule names a deleted list or column is frozen: ``apply`` writes nothing for it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal
from uuid import UUID

from psycopg import sql
from pydantic import BaseModel

from app.audience.link import MAX_ATTEMPTS
from app.lists.row_view import VISIBLE_CELLS_SQL
from app.lists.rows import public_cells
from app.segments import edits
from app.segments.compile import compile_rule, evidence, judged
from app.segments.people import People
from app.segments.rules import Condition, FitCondition, Rule, RuleGroup, problems

SAMPLE = 25

JoinKind = Literal['enter', 'baseline']


class MemberRow(BaseModel):
    """The row shown for a person: its visible cells, as the list shows them."""

    id: UUID
    list_id: UUID
    cells: dict
    relevance_score: float | None


class Applied(BaseModel):
    """What one evaluation changed. ``entered`` counts the joins recorded as entries; the rest joined silently.
    ``merged`` counts the members carried to the identity that a merge left standing. ``members`` counts the live
    members afterwards among the identities it judged, which a full evaluation judges all of."""

    joined: int
    entered: int
    exited: int
    merged: int
    members: int


class Evaluation(BaseModel):
    """``applied``, and of its changes the ones the caller's own action made: for a rule edit, ``joining`` and
    ``leaving`` leave out what the data changed. For a publish or a refresh they are every join and exit. ``frozen``:
    the rule names a deleted list or column, so nobody was judged. ``deferred``: the people it could not judge, a cell
    under a held charge, as the judgment read them."""

    applied: Applied
    joining: int
    leaving: int
    frozen: bool = False
    deferred: list[UUID] = []


_PROJECT_KEY = "hashtextextended('segments:' || %s::text, 0)"


def lock_project(conn, project_id: UUID | str) -> None:
    """One writer of membership per project until the transaction ends. Its own statement: under READ COMMITTED,
    the next statement then reads what the previous holder committed."""
    conn.execute(f'SELECT pg_advisory_xact_lock({_PROJECT_KEY})', (project_id,))


def try_lock_project(conn, project_id: UUID | str) -> bool:
    """``lock_project`` only when no other writer holds it now."""
    return conn.execute(f'SELECT pg_try_advisory_xact_lock({_PROJECT_KEY}) AS got', (project_id,)).fetchone()['got']


def stored_rule(segment: dict) -> Rule:
    """A saved rule: ``parse`` checked it when it was saved."""
    return Rule(segment['entity'], RuleGroup.model_validate(segment['rule']))


def member_rows(conn, row_ids: Iterable[UUID | None]) -> dict[UUID, MemberRow]:
    ids = [r for r in row_ids if r is not None]
    found = conn.execute(
        f'SELECT r.id, r.list_id, {VISIBLE_CELLS_SQL} AS cells, r.relevance_score FROM list_rows r'
        ' WHERE r.id = ANY(%s)',
        (ids,),
    ).fetchall()
    return {r['id']: MemberRow(**{**r, 'cells': public_cells(r['cells'])}) for r in found}


_MERGED = """
WITH RECURSIVE chain AS (
    SELECT m.identity_id AS folded, i.merged_into AS live
    FROM segment_members m JOIN audience_identities i ON i.id = m.identity_id
    WHERE m.segment_id = %(segment)s AND m.exited_at IS NULL AND i.merged_into IS NOT NULL{scope}
    UNION ALL
    SELECT c.folded, i.merged_into FROM chain c JOIN audience_identities i ON i.id = c.live
    WHERE i.merged_into IS NOT NULL
),
folded AS (
    SELECT c.folded, c.live, m.episode, m.entered_at, m.list_row_id
    FROM chain c JOIN segment_members m ON m.segment_id = %(segment)s AND m.identity_id = c.folded
    WHERE NOT EXISTS (SELECT 1 FROM audience_identities i WHERE i.id = c.live AND i.merged_into IS NOT NULL)
),
closed AS (
    UPDATE segment_members m SET exited_at = now() FROM folded f
    WHERE m.segment_id = %(segment)s AND m.identity_id = f.folded
    RETURNING m.identity_id, m.episode, m.list_row_id
),
carried AS (
    INSERT INTO segment_members AS m (segment_id, identity_id, organization_id, list_row_id, episode, entered_at)
    SELECT %(segment)s, f.live, %(org)s, (array_agg(f.list_row_id ORDER BY f.episode DESC))[1], max(f.episode),
           min(f.entered_at)
    FROM folded f GROUP BY f.live
    ON CONFLICT (segment_id, identity_id) DO UPDATE SET
        episode = greatest(m.episode, EXCLUDED.episode),
        entered_at = CASE WHEN m.exited_at IS NULL THEN m.entered_at ELSE EXCLUDED.entered_at END,
        list_row_id = CASE WHEN m.exited_at IS NULL THEN m.list_row_id ELSE EXCLUDED.list_row_id END,
        exited_at = NULL
    RETURNING m.identity_id, m.episode, m.list_row_id
),
changes AS (
    SELECT c.identity_id, 'exit' AS kind, c.episode, c.list_row_id FROM closed c
    UNION ALL
    SELECT c.identity_id, 'merged', c.episode, c.list_row_id FROM carried c
),
recorded AS (
    INSERT INTO segment_transitions
        (organization_id, segment_id, identity_id, kind, episode, list_row_id, segment_version)
    SELECT %(org)s, %(segment)s, c.identity_id, c.kind, c.episode, c.list_row_id, %(version)s
    FROM changes c ORDER BY c.kind, c.identity_id
    RETURNING kind
)
SELECT count(*) FILTER (WHERE kind = 'merged') AS merged FROM recorded
"""

_APPLY = """
WITH RECURSIVE judged AS MATERIALIZED ({judged}),
was AS MATERIALIZED ({was}),
before AS (SELECT identity_id, exited_at, list_row_id FROM segment_members m WHERE segment_id = %(segment)s{scope}),
-- A merge that committed after the carry above: its member waits for the next carry, never an exit and an entry.
-- Every hop of the chain, as the carry follows it, so the identity at its end does not join either.
folding AS (
    SELECT b.identity_id AS folded, i.merged_into AS into_id
    FROM before b JOIN audience_identities i ON i.id = b.identity_id
    WHERE b.exited_at IS NULL AND i.merged_into IS NOT NULL
    UNION ALL
    SELECT f.folded, i.merged_into FROM folding f JOIN audience_identities i ON i.id = f.into_id
    WHERE i.merged_into IS NOT NULL
),
joined AS (
    INSERT INTO segment_members AS m (segment_id, identity_id, organization_id, list_row_id, episode)
    SELECT %(segment)s, j.identity_id, %(org)s, j.list_row_id, 1 FROM judged j WHERE j.matched AND NOT j.deferred
      AND NOT EXISTS (SELECT 1 FROM before b WHERE b.identity_id = j.identity_id AND b.exited_at IS NULL
                      AND b.list_row_id IS NOT DISTINCT FROM j.list_row_id)
      AND NOT EXISTS (SELECT 1 FROM folding f WHERE f.into_id = j.identity_id)
    ON CONFLICT (segment_id, identity_id) DO UPDATE SET
        list_row_id = EXCLUDED.list_row_id,
        episode = m.episode + (m.exited_at IS NOT NULL)::int,
        entered_at = CASE WHEN m.exited_at IS NULL THEN m.entered_at ELSE now() END,
        exited_at = NULL
    WHERE m.exited_at IS NOT NULL OR m.list_row_id IS DISTINCT FROM EXCLUDED.list_row_id
    RETURNING m.identity_id, m.episode, m.list_row_id
),
exited AS (
    UPDATE segment_members m SET exited_at = now()
    WHERE m.segment_id = %(segment)s AND m.exited_at IS NULL{scope}
      AND NOT EXISTS (SELECT 1 FROM judged j WHERE j.identity_id = m.identity_id AND (j.matched OR j.deferred))
      AND NOT EXISTS (SELECT 1 FROM folding f WHERE f.folded = m.identity_id)
    RETURNING m.identity_id, m.episode, m.list_row_id
),
changes AS (
    SELECT j.identity_id, j.episode, j.list_row_id, false AS exit, {asked_join} AS asked
    FROM joined j LEFT JOIN before b USING (identity_id)
    WHERE b.identity_id IS NULL OR b.exited_at IS NOT NULL
    UNION ALL
    SELECT e.identity_id, e.episode, e.list_row_id, true, {asked_exit} FROM exited e
),
recorded AS (
    INSERT INTO segment_transitions
        (organization_id, segment_id, identity_id, kind, episode, list_row_id, segment_version)
    SELECT %(org)s, %(segment)s, c.identity_id,
           CASE WHEN c.exit THEN 'exit' WHEN c.asked THEN %(join_kind)s ELSE 'enter' END, c.episode, c.list_row_id,
           CASE WHEN c.asked THEN %(version)s ELSE %(was_version)s END
    FROM changes c ORDER BY c.exit, c.identity_id
)
SELECT
    count(*) FILTER (WHERE NOT exit) AS joined,
    count(*) FILTER (WHERE NOT exit AND (NOT asked OR %(join_kind)s = 'enter')) AS entered,
    count(*) FILTER (WHERE exit) AS exited,
    count(*) FILTER (WHERE NOT exit AND asked) AS joining,
    count(*) FILTER (WHERE exit AND asked) AS leaving,
    (SELECT count(*) FROM before WHERE exited_at IS NULL) AS live_before,
    ARRAY(SELECT identity_id FROM judged WHERE deferred) AS deferred
FROM changes
"""


def _was(conn, was: dict | None) -> tuple[sql.Composable, dict]:
    if was is None or problems(conn, was['project_id'], was['entity'], stored_rule(was).root):
        return edits.NOBODY, {'judged_was': False}
    query, params = judged(compile_rule(stored_rule(was), prefix='w'), was['project_id'])
    return query, {**params, 'judged_was': True}


def apply(
    conn, segment: dict, join_kind: JoinKind, was: dict | None = None, people: People | None = None
) -> Evaluation:
    """Converge the segment's members on who matches now, and record each change: every identity of the project, or
    only the staged ``people``, where an identity judged with no row left exits. ``join_kind`` is how a join is
    recorded. A join stores ``episode`` 1, or the last one + 1 after an exit; a stay keeps it and may only change the
    shown row. A member whose identity a merge folded into another moves to that one first: the folded identity's
    membership closes with an ``exit`` record, and the one left standing holds the higher episode with a ``merged``
    record, so a merge alone is never an entry and the ledger replays to the members.

    ``was`` is the segment before a rule edit. A join its rule makes too is the data's: an ``enter`` at its version,
    outside ``joining``. An exit its rule makes too is outside ``leaving``.

    Takes ``lock_project`` itself, so ``seq`` commits in order inside the project; a caller that read the segment
    under the lock keeps that read."""
    lock_project(conn, segment['project_id'])
    if problems(conn, segment['project_id'], segment['entity'], stored_rule(segment).root):
        n = conn.execute(
            'SELECT count(*) AS n FROM segment_members WHERE segment_id = %s AND exited_at IS NULL', (segment['id'],)
        ).fetchone()['n']
        return Evaluation(
            applied=Applied(joined=0, entered=0, exited=0, merged=0, members=n), joining=0, leaving=0, frozen=True
        )
    keys = {
        'segment': segment['id'],
        'org': segment['organization_id'],
        'version': segment['version'],
        'identities': None if people is None else people.identities,
    }
    scope = sql.SQL('' if people is None else ' AND m.identity_id = ANY(%(identities)s::uuid[])')
    merged = 0
    if people is None or people.folded:
        merged = conn.execute(sql.SQL(_MERGED).format(scope=scope), keys).fetchone()['merged']
    staged = None if people is None else people.identities
    query, params = judged(compile_rule(stored_rule(segment)), segment['project_id'], staged=staged)
    before, before_params = _was(conn, was)
    statement = sql.SQL(_APPLY).format(
        judged=query, was=before, scope=scope, **edits.asked('j.identity_id', 'e.identity_id')
    )
    values = {**params, **before_params, **keys, 'join_kind': join_kind, 'was_version': (was or segment)['version']}
    row = conn.execute(statement, values).fetchone()
    applied = Applied(
        joined=row['joined'],
        entered=row['entered'],
        exited=row['exited'],
        merged=merged,
        members=row['live_before'] + row['joined'] - row['exited'],
    )
    return Evaluation(applied=applied, joining=row['joining'], leaving=row['leaving'], deferred=row['deferred'])


_PREVIEW = """
WITH RECURSIVE judged AS MATERIALIZED ({judged}){ctes}
SELECT (SELECT count(*) FROM judged WHERE matched AND NOT deferred) AS count,
       (SELECT count(*) FROM judged WHERE deferred) AS pending,
       ARRAY(SELECT identity_id FROM judged WHERE matched AND NOT deferred ORDER BY identity_id LIMIT {sample})
           AS identities,
       ARRAY(SELECT list_row_id FROM judged WHERE matched AND NOT deferred ORDER BY identity_id LIMIT {sample}) AS rows
       {delta}
"""
_UNLINKED = """
SELECT count(*) FILTER (WHERE q.list_row_id IS NULL) AS unidentified_rows,
       count(*) FILTER (WHERE q.list_row_id IS NOT NULL) AS linking_rows
FROM lists l JOIN list_rows r ON r.list_id = l.id
LEFT JOIN audience_link_queue q ON q.list_row_id = r.id AND q.attempts < %(max)s
WHERE l.project_id = %(project)s AND l.entity = %(entity)s AND l.deleted_at IS NULL AND NOT r.excluded
  AND NOT EXISTS (SELECT 1 FROM audience_row_links a WHERE a.list_row_id = r.id)
"""


def preview(conn, project_id: UUID | str, rule: Rule, was: dict | None = None) -> dict:
    """Who matches now, as ``apply`` would join them: ``count`` leaves out ``pending`` people (a cell under a held
    charge), and ``sample`` is the first page of them by identity. With ``was``, a published segment, also
    ``joining`` and ``leaving``: what ``apply`` of ``rule`` with ``was`` would answer now."""
    query, values = judged(compile_rule(rule), project_id)
    ctes: sql.Composable = sql.SQL('')
    delta: sql.Composable = sql.SQL('')
    if was is not None:
        before, before_values = _was(conn, was)
        ctes = sql.SQL(edits.DELTA_CTES).format(was=before)
        delta = sql.SQL(edits.DELTA).format(**edits.asked('j.identity_id', 'l.id'))
        values = {**values, **before_values, 'segment': was['id']}
    statement = sql.SQL(_PREVIEW).format(judged=query, ctes=ctes, sample=SAMPLE, delta=delta)
    found = conn.execute(statement, values).fetchone()
    unlinked = conn.execute(_UNLINKED, {'max': MAX_ATTEMPTS, 'project': project_id, 'entity': rule.entity}).fetchone()
    rows = member_rows(conn, found['rows'])
    sample = [{'identity_id': i, 'row': rows.get(r)} for i, r in zip(found['identities'], found['rows'], strict=True)]
    changed = {'joining': found['joining'], 'leaving': found['leaving']} if was is not None else {}
    return {'count': found['count'], 'pending': found['pending'], **unlinked, 'sample': sample, **changed}


def _row_evidence(row: dict, i: int, condition: Condition, key: str | None) -> dict:
    seen = {
        'list_id': row['list_id'],
        'list_name': row['list_name'],
        'row_id': row['id'],
        'hit': row[f'h{i}'],
    }
    if isinstance(condition, FitCondition):
        return {**seen, 'relevance_score': row['relevance_score']}
    if key is None:
        return seen
    meta = (row['cell_meta'] or {}).get(key) or {}
    return {
        **seen,
        'value': public_cells({key: row['cells'].get(key)}).get(key),
        'at': meta.get('at'),
        'source': meta.get('source'),
        'status': meta.get('status'),
    }


def why(conn, segment: dict, identity_id: UUID) -> dict:
    """One person's verdict as SQL computes it: each node of the rule true or false, and for each condition the
    rows that have its field, which of them satisfy it, and how old each deciding cell is."""
    compiled = compile_rule(stored_rule(segment))
    query, params = judged(compiled, segment['project_id'], identity_id)
    verdict = conn.execute(query, params).fetchone()
    rows = conn.execute(*evidence(compiled, segment['project_id'], identity_id)).fetchall()
    index = {part.path: i for i, part in enumerate(compiled.parts)}

    def node(path: str, at: int) -> dict:
        value = bool(verdict and verdict[f'n{at}'])
        rule_node = compiled.nodes[at][1]
        if isinstance(rule_node, RuleGroup):
            return {'path': path, 'type': 'group', 'op': rule_node.op, 'value': value, 'children': []}
        i = index[path]
        part = compiled.parts[i]
        return {
            'path': path,
            'type': 'condition',
            'condition': rule_node.model_dump(mode='json'),
            'value': value,
            'rows': [_row_evidence(r, i, part.condition, part.cell_key) for r in rows if r[f'f{i}']],
        }

    by_path = {path: node(path, at) for at, (path, _) in enumerate(compiled.nodes)}
    for path, tree_node in by_path.items():
        if path:
            by_path[path.rsplit('/children/', 1)[0]]['children'].append(tree_node)
    member = conn.execute(
        'SELECT exited_at IS NULL AS live FROM segment_members WHERE segment_id = %s AND identity_id = %s',
        (segment['id'], identity_id),
    ).fetchone()
    return {
        'identity_id': identity_id,
        'matches': bool(verdict and verdict['matched']),
        'pending': bool(verdict and verdict['deferred']),
        'member': bool(member and member['live']),
        'rule': by_path[''],
    }


def members(conn, segment_id: UUID, after: UUID | None, limit: int) -> dict:
    """The live members by identity, from ``after``: a keyset page."""
    page = conn.execute(
        'SELECT identity_id, episode, entered_at, list_row_id FROM segment_members'
        ' WHERE segment_id = %s AND exited_at IS NULL AND (%s::uuid IS NULL OR identity_id > %s)'
        ' ORDER BY identity_id LIMIT %s',
        (segment_id, after, after, limit + 1),
    ).fetchall()
    rows = member_rows(conn, (m['list_row_id'] for m in page[:limit]))
    return {
        'members': [{**m, 'row': rows.get(m['list_row_id'])} for m in page[:limit]],
        'next_cursor': page[limit - 1]['identity_id'] if len(page) > limit else None,
    }
