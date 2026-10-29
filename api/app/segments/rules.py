"""Segment rules (#938): the tree a member saves, its bounds, and its check against one project.

A rule is AND / OR groups of conditions over one entity's fields. A canonical field means the same in every list of
the entity (``CANONICAL``); a column field names one list's column, because an AI column key means something only in
its list. There is no NOT group: a negative operator carries the missing-value rule itself, so a person whose field
is unknown never matches by negation.

``parse`` is the boundary: the evaluator trusts a parsed rule. A stored rule that later names a deleted list or
column has ``problems`` and is frozen until an edit fixes it."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.lists.rows import CANONICAL, VENDOR_KEYS, is_internal

Entity = Literal['person', 'company']
MAX_DEPTH = 3
MAX_LEAVES = 20
MAX_VALUES = 50
Number = Annotated[float, Field(allow_inf_nan=False)]
Fit = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class _Node(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class CanonicalField(_Node):
    kind: Literal['canonical'] = 'canonical'
    key: str = Field(min_length=1, max_length=100)


class ColumnField(_Node):
    kind: Literal['column'] = 'column'
    list_id: UUID
    key: str = Field(min_length=1, max_length=100)


RuleField = Annotated[CanonicalField | ColumnField, Field(discriminator='kind')]


class TextCondition(_Node):
    type: Literal['text'] = 'text'
    field: RuleField
    op: Literal['eq', 'neq', 'contains', 'not_contains', 'in', 'not_in']
    values: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(min_length=1, max_length=MAX_VALUES)

    @model_validator(mode='after')
    def _one_value(self) -> TextCondition:
        if self.op not in ('in', 'not_in') and len(self.values) != 1:
            raise ValueError(f'{self.op} takes one value')
        return self


_BOUNDS = {'gt': ('low',), 'gte': ('low',), 'lt': ('high',), 'lte': ('high',), 'between': ('low', 'high')}


def _bounds(op: str, low: float | None, high: float | None) -> None:
    if tuple(n for n, v in (('low', low), ('high', high)) if v is not None) != _BOUNDS[op]:
        raise ValueError(f'{op} takes {" and ".join(_BOUNDS[op])}')
    if low is not None and high is not None and low > high:
        raise ValueError('low is above high')


class NumberCondition(_Node):
    """A cell that is not a plain number ('51-200') is filled and never matches."""

    type: Literal['number'] = 'number'
    field: RuleField
    op: Literal['gt', 'gte', 'lt', 'lte', 'between']
    low: Number | None = None
    high: Number | None = None

    @model_validator(mode='after')
    def _bounded(self) -> NumberCondition:
        _bounds(self.op, self.low, self.high)
        return self


class FitCondition(_Node):
    """The best fit (``relevance_score``) over the person's rows."""

    type: Literal['fit'] = 'fit'
    op: Literal['gte', 'lte', 'between', 'is_missing', 'is_filled']
    low: Fit | None = None
    high: Fit | None = None

    @model_validator(mode='after')
    def _bounded(self) -> FitCondition:
        if self.op in ('is_missing', 'is_filled'):
            if self.low is not None or self.high is not None:
                raise ValueError(f'{self.op} takes no bound')
        else:
            _bounds(self.op, self.low, self.high)
        return self


class PresenceCondition(_Node):
    type: Literal['presence'] = 'presence'
    field: RuleField
    op: Literal['is_missing', 'is_filled']


class InListCondition(_Node):
    type: Literal['in_list'] = 'in_list'
    list_id: UUID
    op: Literal['in_list', 'not_in_list'] = 'in_list'


class RuleGroup(_Node):
    type: Literal['group'] = 'group'
    op: Literal['and', 'or']
    children: tuple[RuleNode, ...] = Field(min_length=1, max_length=MAX_LEAVES)


Condition = TextCondition | NumberCondition | FitCondition | PresenceCondition | InListCondition
RuleNode = Annotated[Condition | RuleGroup, Field(discriminator='type')]
RuleGroup.model_rebuild()
# The tags a discriminated union adds to an error location: not part of the submitted JSON.
_TAGS = frozenset({'text', 'number', 'fit', 'presence', 'in_list', 'group', 'canonical', 'column'})


@dataclass(frozen=True, slots=True)
class Rule:
    entity: Entity
    root: RuleGroup


class RuleError(Exception):
    """422 ``SEGMENT.INVALID_RULE``. ``path`` is a JSON pointer into the submitted rule, so the editor marks the
    condition."""

    def __init__(self, path: str, reason: str, detail: str | None = None) -> None:
        super().__init__(f'{detail or reason} at {path or "/"}')
        self.path = path
        self.reason = reason


def _pointer(loc: tuple[int | str, ...]) -> str:
    return ''.join(f'/{part}' for part in loc if part not in _TAGS)


def walk(node: RuleGroup, path: str = '', depth: int = 1) -> Iterator[tuple[str, int, RuleGroup | Condition]]:
    """Every node with its JSON pointer and group depth, the root group first."""
    yield path, depth, node
    for i, child in enumerate(node.children):
        here = f'{path}/children/{i}'
        if isinstance(child, RuleGroup):
            yield from walk(child, here, depth + 1)
        else:
            yield here, depth, child


def canonical_keys(entity: Entity) -> list[str]:
    """The canonical keys a rule may read: vendor plumbing is hidden from users, so it is no field either."""
    return [k for k in CANONICAL[entity] if k not in VENDOR_KEYS]


@dataclass(frozen=True, slots=True)
class Problem:
    path: str
    reason: Literal['list_not_found', 'column_not_found']
    list_id: UUID
    key: str | None = None


def _references(root: RuleGroup) -> Iterator[tuple[str, UUID, str | None]]:
    for path, _, node in walk(root):
        if isinstance(node, InListCondition):
            yield path, node.list_id, None
        elif not isinstance(node, (RuleGroup, FitCondition)) and isinstance(node.field, ColumnField):
            yield f'{path}/field', node.field.list_id, node.field.key


def problems_of(conn, project_id: UUID | str, rules: Sequence[tuple[Entity, RuleGroup]]) -> list[list[Problem]]:
    """For each rule, what it names that is not a live list of the project and its entity, or a live column a user
    sees. One read for all of them."""
    refs = [list(_references(root)) for _, root in rules]
    wanted = {list_id for found in refs for _, list_id, _ in found}
    if not wanted:
        return [[] for _ in rules]
    live = conn.execute(
        'SELECT l.id, l.entity, c.key FROM lists l'
        ' LEFT JOIN list_columns c ON c.list_id = l.id AND c.deleted_at IS NULL'
        ' WHERE l.id = ANY(%s) AND l.project_id = %s AND l.deleted_at IS NULL',
        (list(wanted), project_id),
    ).fetchall()
    lists = {(r['id'], r['entity']) for r in live}
    columns = {(r['id'], r['key']) for r in live if r['key'] and not is_internal(r['key'])}

    def problem(entity: Entity, path: str, list_id: UUID, key: str | None) -> Problem | None:
        if (list_id, entity) not in lists:
            return Problem(path, 'list_not_found', list_id, key)
        if key is not None and (key in VENDOR_KEYS or (list_id, key) not in columns):
            return Problem(path, 'column_not_found', list_id, key)
        return None

    return [
        [p for ref in found if (p := problem(entity, *ref))] for (entity, _), found in zip(rules, refs, strict=True)
    ]


def problems(conn, project_id: UUID | str, entity: Entity, root: RuleGroup) -> list[Problem]:
    return problems_of(conn, project_id, [(entity, root)])[0]


def parse(conn, project_id: UUID | str, entity: Entity, raw: Any) -> Rule:
    """The submitted rule as a ``Rule`` of this project, or ``RuleError`` at the first thing wrong with it."""
    try:
        root = RuleGroup.model_validate(raw)
    except ValidationError as e:
        first = e.errors(include_url=False, include_input=False)[0]
        raise RuleError(_pointer(first['loc']), first['type'], first['msg']) from None
    leaves = 0
    allowed = set(canonical_keys(entity))
    for path, depth, node in walk(root):
        if isinstance(node, RuleGroup):
            if depth > MAX_DEPTH:
                raise RuleError(path, 'too_deep')
            continue
        leaves += 1
        if leaves > MAX_LEAVES:
            raise RuleError(path, 'too_many_conditions')
        field = getattr(node, 'field', None)
        if isinstance(field, CanonicalField) and field.key not in allowed:
            raise RuleError(f'{path}/field/key', 'unknown_field')
    if found := problems(conn, project_id, entity, root):
        raise RuleError(found[0].path, found[0].reason)
    return Rule(entity, root)
