"""The no-results funnel: where an Apollo company or people search lost every row, and which relaxations of it
find some.

Built from the filters the empty search applied, never from model input. At most ``MAX_PAGES`` Apollo pages per
company funnel: one per filter group but the last (that one is the empty search itself), plus ``MAX_ALTERNATIVES``.
People search pages are free.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypedDict

# Broad to narrow: the order the funnel applies them.
GROUPS: dict[str, tuple[str, ...]] = {
    'where': ('locations',),
    'what': ('industries', 'keywords'),
    'size': ('employee_ranges',),
    'money': ('latest_funding_min', 'total_funding_min', 'revenue_min', 'revenue_max'),
    'signal': ('job_titles', 'min_active_jobs'),
}
# search_people's own filter names: the person's role and location, then the employer's.
PEOPLE_GROUPS: dict[str, tuple[str, ...]] = {
    'where': ('locations', 'company_locations'),
    'what': ('industries', 'keywords', 'keyword_tags'),
    'size': ('employee_ranges',),
    'money': ('revenue_min', 'revenue_max'),
    'signal': ('job_titles', 'min_active_jobs'),
    'who': ('titles', 'seniorities', 'functions'),
}
_GROUPS = {'company': GROUPS, 'person': PEOPLE_GROUPS}
MAX_ALTERNATIVES = 3
MAX_PAGES = len(GROUPS) - 1 + MAX_ALTERNATIVES

Count = Callable[[dict], Awaitable[int | None]]


class Step(TypedDict):
    group: str
    values: list[str]
    count: int


class Option(TypedDict):
    label: str
    description: None
    estimate: dict
    filters: dict
    drop: str
    tool: str


def applied_filters(args: dict, entity: str = 'company') -> dict:
    keys = {k for keys in _GROUPS[entity].values() for k in keys}
    return {k: args[k] for k in keys if args.get(k) not in (None, [], '')}


def _compact(n: int) -> str:
    return (
        f'{n // 1_000_000}M' if n >= 1_000_000 and n % 1_000_000 == 0 else f'{n // 1000}k' if n % 1000 == 0 else str(n)
    )


def _values(key: str, value: object) -> list[str]:
    if key == 'employee_ranges':
        return [str(v).replace(',', '–') for v in value] if isinstance(value, list) else []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, int):
        return [f'{"≤" if key == "revenue_max" else "≥"} {_compact(value)}']
    return [str(value)]


def _group_values(group: str, filters: dict, groups: dict = GROUPS) -> list[str]:
    return [v for key in groups[group] if key in filters for v in _values(key, filters[key])]


def _only(filters: dict, group: str, groups: dict = GROUPS) -> dict:
    return {k: v for k, v in filters.items() if k in groups[group]}


def _without(filters: dict, group: str, groups: dict = GROUPS) -> dict:
    return {k: v for k, v in filters.items() if k not in groups[group]}


async def build(count: Count, applied: dict, entity: str = 'company') -> dict | None:
    """The question payload for an empty search with these filters, or None when it cannot be explained."""
    groups = _GROUPS[entity]
    present = [g for g in groups if _only(applied, g, groups)]
    if not present:
        return None
    steps: list[Step] = []
    cumulative: dict = {}
    for i, group in enumerate(present):
        cumulative = {**cumulative, **_only(applied, group, groups)}
        total = 0 if i == len(present) - 1 else await count(cumulative)
        if total is None:
            return None
        steps.append({'group': group, 'values': _group_values(group, applied, groups), 'count': total})
        if total == 0:
            break
    excluded = len(steps) - 1
    candidates = list(dict.fromkeys([present[excluded], *reversed(present)]))
    candidates = [g for g in candidates if _without(applied, g, groups)][:MAX_ALTERNATIVES]
    counts = await asyncio.gather(*(count(_without(applied, g, groups)) for g in candidates))
    options: list[Option] = [
        {
            'label': f'Without {group}',
            'description': None,
            'estimate': {'count': total, 'noun': entity},
            'filters': _without(applied, group, groups),
            'drop': group,
            'tool': 'search_people' if entity == 'person' else 'search_companies',
        }
        for group, total in zip(candidates, counts, strict=True)
        if total
    ]
    return {
        'question': f'No {entity} matches every filter',
        'options': options,
        'funnel': {'steps': steps, 'excluded_index': excluded, 'entity': entity},
    }


def with_question(content: str, question: dict | None) -> str:
    """The question lives beside the message, not in it: the model still reads it as the turn's last words.
    Rows saved before #211 already end with it."""
    asked = question.get('question', '') if question else ''
    if not asked or content.rstrip().endswith(asked):
        return content
    return f'{content}\n\n{asked}' if content else asked
