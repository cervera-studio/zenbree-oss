"""Segment rules (#938) judged in Python, for tests only: the semantics written a second time, so a property test can
compare the SQL compiler (app.segments.compile) with it. The app has one judge, the SQL.

A row is ``{'id', 'identity': key or None, 'list_id', 'cells', 'cell_meta', 'relevance_score', 'excluded'}``, its cells
already as a reader sees them; cell values are strings."""

from __future__ import annotations

import re
from decimal import Decimal

from app.segments.rules import (
    ColumnField,
    Condition,
    FitCondition,
    InListCondition,
    NumberCondition,
    PresenceCondition,
    RuleField,
    RuleGroup,
    TextCondition,
)

NUMBER = re.compile(r'-?[0-9]+(\.[0-9]+)?')


def _value(row: dict, field: RuleField) -> str | None:
    if isinstance(field, ColumnField) and row['list_id'] != field.list_id:
        return None
    if (row['cell_meta'].get(field.key) or {}).get('status') == 'failed':
        return None
    return row['cells'].get(field.key) or None


def _norm(text: str) -> str:
    return text.strip(' ').lower()


def _between(value: Decimal, op: str, low: float | None, high: float | None) -> bool:
    lo = None if low is None else Decimal(str(low))
    hi = None if high is None else Decimal(str(high))
    return {
        'gt': lambda: value > lo,
        'gte': lambda: value >= lo,
        'lt': lambda: value < hi,
        'lte': lambda: value <= hi,
        'between': lambda: lo <= value <= hi,
    }[op]()


def _text_hit(c: TextCondition, value: str) -> bool:
    wanted = [_norm(v) for v in c.values]
    if c.op in ('eq', 'neq'):
        return _norm(value) == wanted[0]
    if c.op in ('contains', 'not_contains'):
        return wanted[0] in _norm(value)
    return _norm(value) in wanted


def _condition(c: Condition, rows: list[dict]) -> bool:
    if isinstance(c, InListCondition):
        found = any(r['list_id'] == c.list_id for r in rows)
        return found if c.op == 'in_list' else not found
    if isinstance(c, FitCondition):
        scores = [Decimal(str(r['relevance_score'])) for r in rows if r['relevance_score'] is not None]
        if c.op in ('is_missing', 'is_filled'):
            return bool(scores) == (c.op == 'is_filled')
        return bool(scores) and _between(max(scores), c.op, c.low, c.high)
    values = [v for r in rows if (v := _value(r, c.field)) is not None]
    if isinstance(c, PresenceCondition):
        return bool(values) == (c.op == 'is_filled')
    if isinstance(c, NumberCondition):
        numbers = [Decimal(v.strip(' ')) for v in values if NUMBER.fullmatch(v.strip(' '))]
        return any(_between(n, c.op, c.low, c.high) for n in numbers)
    hits = any(_text_hit(c, v) for v in values)
    if c.op in ('neq', 'not_contains', 'not_in'):
        return bool(values) and not hits
    return hits


def judge(group: RuleGroup, rows: list[dict]) -> bool:
    results = (
        judge(child, rows) if isinstance(child, RuleGroup) else _condition(child, rows) for child in group.children
    )
    return all(results) if group.op == 'and' else any(results)


def members(group: RuleGroup, rows: list[dict]) -> dict:
    """The identities that match, each with its shown row: their linked rows that are not excluded, judged
    together. The shown row is the best fit, then the lowest id (the rows of a test share ``created_at``)."""
    people: dict = {}
    for row in rows:
        if row['identity'] is not None and not row['excluded']:
            people.setdefault(row['identity'], []).append(row)
    return {
        who: min(theirs, key=lambda r: (r['relevance_score'] is None, -(r['relevance_score'] or 0), r['id']))['id']
        for who, theirs in people.items()
        if judge(group, theirs)
    }
