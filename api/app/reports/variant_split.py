"""The counts of a campaign that tests copy B, split by the copy each person got (#934).

Every count the report takes for the row is split here, so that each count summed over the groups is the row's
count. ``outside`` is a person in the campaign with no variant: they joined while the allocation was paused and got
copy A, outside the test. ``unknown`` is an event of no person in the campaign (a person deleted since, or a sender
who matched nobody)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

Group = Literal['a', 'b', 'outside', 'unknown']
GROUPS: tuple[Group, ...] = ('a', 'b', 'outside', 'unknown')
PEOPLE = ('enrolled', 'contacted', 'human_replies', 'positive_replies')
CHANNELS = ('email', 'linkedin_connect', 'linkedin_message')
_TESTED: dict[str | None, Group] = {'a': 'a', 'b': 'b'}


def group_of(has_target: bool, variant: str | None) -> Group:
    return _TESTED.get(variant, 'outside') if has_target else 'unknown'


def split(rows: Iterable[Mapping]) -> list[dict]:
    """Sums the counts of ``rows`` by group. Each row has ``has_target``, ``variant`` and any of the counts in
    ``PEOPLE`` and ``CHANNELS``. A and B are always listed; ``outside`` and ``unknown`` only when they count anyone."""
    totals: dict[Group, dict[str, int]] = {g: dict.fromkeys((*PEOPLE, *CHANNELS), 0) for g in GROUPS}
    for row in rows:
        group = totals[group_of(row['has_target'], row['variant'])]
        for key in group:
            group[key] += row.get(key) or 0
    return [
        {
            'group': g,
            **{m: totals[g][m] for m in PEOPLE},
            'sent': {k: totals[g][k] for k in CHANNELS},
            'bounced': None,  # native bounces are not stored yet (#747)
        }
        for g in GROUPS
        if g in ('a', 'b') or any(totals[g].values())
    ]
