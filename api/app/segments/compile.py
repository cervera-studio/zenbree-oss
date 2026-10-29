"""Compile a parsed segment rule to SQL (#938). This is the one judge of who matches: preview, apply and why all run
the same compiled conditions.

A person is an identity (#937). The identity view is every row linked to one, in a live list of the project with the
rule's entity, not excluded, with its cells as a reader sees them (``VISIBLE_CELLS_SQL``). Each condition compiles to
two row-level parts, ``filled`` (the row has the field) and ``hit`` (the row satisfies the positive form), and to a
``truth`` over the identity's rows:

- a positive operator is true when some row hits;
- a negative operator (``neq``, ``not_contains``, ``not_in``, ``not_in_list``) is true when some row has the field
  and no row hits, so an unknown value never matches by negation;
- ``is_missing`` is true when no row has the field. An absent key, ``''`` and a cell whose fill ``failed`` are missing;
- fit compares the best ``relevance_score`` of the rows.

A row with a cell still under a held charge makes its identity ``deferred``: nobody can judge it until the charge
settles. User values are bound parameters, never SQL text."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import sql

from app.lists.row_view import VISIBLE_CELLS_SQL
from app.segments.rules import (
    CanonicalField,
    Condition,
    FitCondition,
    InListCondition,
    NumberCondition,
    PresenceCondition,
    Rule,
    RuleField,
    RuleGroup,
    TextCondition,
    walk,
)

NEGATIVE = frozenset({'neq', 'not_contains', 'not_in', 'not_in_list'})
NUMBER_PATTERN = r'^-?[0-9]+(\.[0-9]+)?$'
_CMP = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}

_VISIBLE_ROW = 'l.project_id = %(project)s AND l.deleted_at IS NULL AND NOT r.excluded'
# The lateral computes the visible cells once a row: each read of them probes row_charges.
_ROWS = (
    'SELECT l.entity, a.identity_id, r.id, r.list_id, r.relevance_score, r.created_at, r.cell_meta, vis.cells,'
    ' vis.cells <> r.cells AS pending'
    ' FROM lists l JOIN list_rows r ON r.list_id = l.id JOIN {links} a ON a.list_row_id = r.id'
    f' CROSS JOIN LATERAL (SELECT {VISIBLE_CELLS_SQL} AS cells OFFSET 0) vis'
    f' WHERE {_VISIBLE_ROW}'
)
_IDENTITY_VIEW = sql.SQL(_ROWS + ' AND l.entity = %(entity)s').format(links=sql.SQL('audience_row_links'))

STAGED = 'segment_people_rows'
# OFFSET 0 stops Postgres from flattening the links subquery, so the links of ``identities`` drive the read and it
# never scans the project's rows.
STAGE = sql.SQL(f'CREATE TEMP TABLE {STAGED} ON COMMIT DROP AS ' + _ROWS).format(
    links=sql.SQL(
        '(SELECT list_row_id, identity_id FROM audience_row_links WHERE identity_id = ANY(%(identities)s::uuid[])'
        ' OFFSET 0)'
    )
)
_STAGED_VIEW = sql.SQL(f'SELECT * FROM {STAGED} WHERE entity = %(entity)s')
_CHANGED_SINCE_STAGE = f"""
folded_since_stage AS (
    SELECT i.id FROM audience_identities i WHERE i.id = ANY(%(identities)s::uuid[]) AND i.merged_into IS NOT NULL
),
relinked_since_stage AS (
    SELECT coalesce(s.identity_id, a.identity_id) AS id FROM {STAGED} s
    FULL JOIN (SELECT list_row_id, identity_id FROM audience_row_links WHERE identity_id = ANY(%(identities)s::uuid[])
               OFFSET 0) a ON a.list_row_id = s.id AND a.identity_id = s.identity_id
    WHERE a.list_row_id IS NULL OR (s.id IS NULL AND EXISTS (
        SELECT 1 FROM list_rows r JOIN lists l ON l.id = r.list_id WHERE r.id = a.list_row_id AND {_VISIBLE_ROW}))
),
changed AS MATERIALIZED (SELECT id FROM folded_since_stage UNION SELECT id FROM relinked_since_stage)
"""


@dataclass(frozen=True, slots=True)
class Part:
    """One condition, compiled. ``filled`` and ``hit`` read one row of the view ``v``; ``truth`` aggregates them
    over an identity. None of them is ever NULL."""

    path: str
    condition: Condition
    filled: sql.Composable
    hit: sql.Composable
    truth: sql.Composable
    cell_key: str | None


@dataclass(slots=True)
class Compiled:
    """Every node of the rule in tree order (the root group first), each condition's ``Part``, and the truth of
    every node by its JSON pointer."""

    entity: str
    nodes: list[tuple[str, RuleGroup | Condition]]
    parts: list[Part]
    truths: dict[str, sql.Composable]
    params: dict[str, Any] = field(default_factory=dict)


class _Params:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.values: dict[str, Any] = {}

    def bind(self, value: Any, cast: str) -> sql.Composable:
        name = f'{self.prefix}{len(self.values)}'
        self.values[name] = value
        return sql.SQL('{}::{}').format(sql.Placeholder(name), sql.SQL(cast))


def _cell(field: RuleField, p: _Params) -> sql.Composable:
    """The row's value of ``field`` as text, NULL when missing."""
    key = p.bind(field.key, 'text')
    template = sql.SQL("CASE WHEN v.cell_meta->{k}->>'status' = 'failed' THEN NULL ELSE nullif(v.cells->>{k}, '') END")
    value = template.format(k=key)
    if isinstance(field, CanonicalField):
        return value
    return sql.SQL('CASE WHEN v.list_id = {} THEN {} END').format(p.bind(field.list_id, 'uuid'), value)


def _norm(value: sql.Composable) -> sql.Composable:
    return sql.SQL('lower(btrim({}))').format(value)


def _false_if_null(expr: sql.Composable) -> sql.Composable:
    return sql.SQL('coalesce({}, false)').format(expr)


def _any(expr: sql.Composable) -> sql.Composable:
    return sql.SQL('coalesce(bool_or({}), false)').format(expr)


def _text(c: TextCondition, value: sql.Composable, p: _Params) -> sql.Composable:
    norm = _norm(value)
    wanted = [_norm(p.bind(v, 'text')) for v in c.values]
    if c.op in ('eq', 'neq'):
        return sql.SQL('{} = {}').format(norm, wanted[0])
    if c.op in ('contains', 'not_contains'):
        return sql.SQL('strpos({}, {}) > 0').format(norm, wanted[0])
    return sql.SQL('{} IN ({})').format(norm, sql.SQL(', ').join(wanted))


def _compare(value: sql.Composable, op: str, low: float | None, high: float | None, p: _Params) -> sql.Composable:
    if op == 'between':
        return sql.SQL('{} BETWEEN {} AND {}').format(
            value, p.bind(Decimal(str(low)), 'numeric'), p.bind(Decimal(str(high)), 'numeric')
        )
    bound = low if op in ('gt', 'gte') else high
    return sql.SQL('{} {} {}').format(value, sql.SQL(_CMP[op]), p.bind(Decimal(str(bound)), 'numeric'))


def _number(value: sql.Composable) -> sql.Composable:
    return sql.SQL("CASE WHEN btrim({v}) ~ '{pattern}' THEN btrim({v})::numeric END").format(
        v=value, pattern=sql.SQL(NUMBER_PATTERN)
    )


def _part(path: str, c: Condition, p: _Params) -> Part:
    filled: sql.Composable
    hit: sql.Composable
    if isinstance(c, InListCondition):
        hit = sql.SQL('v.list_id = {}').format(p.bind(c.list_id, 'uuid'))
        truth = _any(hit)
        return Part(path, c, hit, hit, sql.SQL('NOT {}').format(truth) if c.op in NEGATIVE else truth, None)
    if isinstance(c, FitCondition):
        filled = sql.SQL('v.relevance_score IS NOT NULL')
        if c.op in ('is_missing', 'is_filled'):
            truth = _any(filled)
            return Part(
                path, c, filled, filled, sql.SQL('NOT {}').format(truth) if c.op == 'is_missing' else truth, None
            )
        hit = _false_if_null(_compare(sql.SQL('v.relevance_score'), c.op, c.low, c.high, p))
        best = _false_if_null(_compare(sql.SQL('max(v.relevance_score)'), c.op, c.low, c.high, p))
        return Part(path, c, filled, hit, best, None)
    value = _cell(c.field, p)
    filled = sql.SQL('{} IS NOT NULL').format(value)
    key = c.field.key
    if isinstance(c, PresenceCondition):
        truth = _any(filled)
        return Part(path, c, filled, filled, sql.SQL('NOT {}').format(truth) if c.op == 'is_missing' else truth, key)
    if isinstance(c, NumberCondition):
        hit = _false_if_null(_compare(_number(value), c.op, c.low, c.high, p))
        return Part(path, c, filled, hit, _any(hit), key)
    hit = _false_if_null(_text(c, value, p))
    if c.op in NEGATIVE:
        return Part(path, c, filled, hit, sql.SQL('({} AND NOT {})').format(_any(filled), _any(hit)), key)
    return Part(path, c, filled, hit, _any(hit), key)


def compile_rule(rule: Rule, prefix: str = 'p') -> Compiled:
    """``prefix`` names the bound values, so two compiled rules can share one statement."""
    p = _Params(prefix)
    nodes = [(path, node) for path, _, node in walk(rule.root)]
    parts: dict[str, Part] = {}
    truths: dict[str, sql.Composable] = {}
    for path, node in reversed(nodes):  # each group after its children
        if isinstance(node, RuleGroup):
            joiner = sql.SQL(' AND ' if node.op == 'and' else ' OR ')
            children = [truths[f'{path}/children/{i}'] for i in range(len(node.children))]
            truths[path] = sql.SQL('({})').format(joiner.join(children))
        else:
            parts[path] = _part(path, node, p)
            truths[path] = parts[path].truth
    ordered = [parts[path] for path, _ in nodes if path in parts]
    return Compiled(rule.entity, nodes, ordered, truths, p.values)


def judged(
    compiled: Compiled,
    project_id: UUID | str,
    identity_id: UUID | str | None = None,
    staged: Sequence[UUID] | None = None,
) -> tuple[sql.Composed, dict]:
    """One row per identity of the project in the view: ``identity_id``, ``matched``, ``deferred`` and the
    representative ``list_row_id`` (best fit, then oldest, then lowest id). With ``identity_id``, that identity only,
    and the truth of each node as ``n<i>``, in tree order. With ``staged``, the people ``people.stage`` staged, never
    with ``identity_id``."""
    if identity_id is not None and staged is not None:
        raise ValueError('judged takes identity_id or staged, not both: the staged rows have no links alias')
    view: sql.Composable = _IDENTITY_VIEW if staged is None else _STAGED_VIEW
    truths: list[sql.Composable] = []
    if identity_id is not None:
        view = sql.SQL('{} AND a.identity_id = %(identity)s').format(view)
        truths = [
            sql.SQL(', {} AS {}').format(compiled.truths[path], sql.Identifier(f'n{i}'))
            for i, (path, _) in enumerate(compiled.nodes)
        ]
    query = sql.SQL(
        'SELECT v.identity_id, {matched} AS matched, bool_or(v.pending) AS deferred,'
        ' (array_agg(v.id ORDER BY v.relevance_score DESC NULLS LAST, v.created_at, v.id))[1] AS list_row_id{truths}'
        ' FROM ({view}) v GROUP BY v.identity_id'
    ).format(matched=compiled.truths[''], truths=sql.Composed(truths), view=view)
    params = {**compiled.params, 'project': project_id, 'entity': compiled.entity, 'identity': identity_id}
    if staged is None:
        return query, params
    query = sql.SQL(
        'WITH ' + _CHANGED_SINCE_STAGE + ', verdicts AS MATERIALIZED ({query})'
        ' SELECT f.identity_id, f.matched, f.deferred OR c.id IS NOT NULL AS deferred, f.list_row_id'
        ' FROM verdicts f LEFT JOIN changed c ON c.id = f.identity_id'
        ' UNION ALL SELECT c.id, false, true, NULL::uuid FROM changed c'
        ' WHERE NOT EXISTS (SELECT 1 FROM verdicts f WHERE f.identity_id = c.id)'
    ).format(query=query)
    return query, {**params, 'identities': list(staged)}


def evidence(compiled: Compiled, project_id: UUID | str, identity_id: UUID | str) -> tuple[sql.Composed, dict]:
    """The rows of one identity, oldest first, each with every condition's ``f<i>`` (filled) and ``h<i>`` (hit)
    as the judge reads them, in the order of ``compiled.parts``."""
    cols = [
        sql.SQL(', {} AS {}, {} AS {}').format(part.filled, sql.Identifier(f'f{i}'), part.hit, sql.Identifier(f'h{i}'))
        for i, part in enumerate(compiled.parts)
    ]
    query = sql.SQL(
        'SELECT v.id, v.list_id, l.name AS list_name, v.relevance_score, v.cells, v.cell_meta{cols}'
        ' FROM ({view} AND a.identity_id = %(identity)s) v JOIN lists l ON l.id = v.list_id ORDER BY v.created_at, v.id'
    ).format(cols=sql.Composed(cols), view=_IDENTITY_VIEW)
    return query, {**compiled.params, 'project': project_id, 'entity': compiled.entity, 'identity': identity_id}
