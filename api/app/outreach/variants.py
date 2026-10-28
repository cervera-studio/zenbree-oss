"""A/B copy variants (#934): which copy each person gets.

A step may carry ``b``, its subject and body for the people in variant B. Channel, delays and threading are
shared, so every reader of the shape (``due``, holds, threading) stays the same. A campaign tests while some step
has ``b``; that fact lives only in ``steps``. A person's ``variant`` is set once, by the freeze that activates them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Literal, NamedTuple

Variant = Literal['a', 'b']


class Split(NamedTuple):
    a: int
    b: int


def has_test(steps: list[dict]) -> bool:
    return any(s.get('b') for s in steps)


def allocating(campaign: dict) -> bool:
    """New people join the test: it has a B copy and nobody paused the allocation."""
    return has_test(campaign['steps']) and campaign.get('allocation_paused_at') is None


def sequence(steps: list[dict], variant: Variant | None) -> list[dict]:
    """The steps one person gets: B's subject and body where a step has ``b``, the shared shape always."""
    out = []
    for s in steps:
        step = {k: v for k, v in s.items() if k != 'b'}
        if variant == 'b' and (b := s.get('b')):
            step.update(subject=b['subject'], body=b['body'])
            if step.get('personalised'):
                step['personalised'] = ''  # it marks a span of A's body, which B's body does not hold
        out.append(step)
    return out


def every_copy(steps: list[dict]) -> list[dict]:
    """A's steps and B's copies, for the tokens someone who may get either copy needs."""
    return [*sequence(steps, 'a'), *(s['b'] for s in steps if s.get('b'))]


def copy_to_fill(steps: list[dict], variant: Variant | None, *, joins_test: bool) -> list[dict]:
    """The copy whose tokens a person must have a value for. A person who joins while the test allocates may get
    either copy, so both count; anyone else gets the copy of their own variant."""
    return every_copy(steps) if variant is None and joins_test else sequence(steps, variant)


def assign(existing: Split, joining: Sequence[str], b_percent: int, campaign_id: str) -> dict[str, Variant]:
    """Each joiner once: B while B stays below ``b_percent`` of everyone assigned so far, else A.

    A running count, not a per-person hash threshold (29 people at 50% often split 10/19 that way): from (0, 0) the
    split stays within one person of ``b_percent`` across any batches. People go in hash order, so the order a caller
    lists them in does not choose their copy. Exact only while freezes of a campaign run one at a time (the project
    lock every freeze caller takes)."""
    a, b = existing
    out: dict[str, Variant] = {}
    for target_id in sorted(set(joining), key=lambda t: hashlib.sha256(f'{campaign_id}:{t}'.encode()).hexdigest()):
        if 100 * b < b_percent * (a + b + 1):
            out[target_id], b = 'b', b + 1
        else:
            out[target_id], a = 'a', a + 1
    return out
