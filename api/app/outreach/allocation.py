"""Which ready mailbox account sends which queued email this beat (#935). Pure.

Each account brings its own queue: its conversations' next steps first, then new conversations of the campaigns
that pool it. An unpinned row can sit in several queues, so the accounts take turns: the one with the fewest rows to
choose from picks first, a tie goes to the one with the most of today's cap left. Every account with a row nobody
took yet sends one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Hashable, Mapping, Sequence


def assign[K: Hashable](queues: Mapping[K, Sequence[dict]], headroom: Mapping[K, int]) -> dict[K, dict]:
    """At most one row for each account, never one row (by its ``id``) for two accounts."""
    taken: set = set()
    picked: dict[K, dict] = {}
    for account in sorted(queues, key=lambda a: (len(queues[a]), -headroom[a], str(a))):
        row = next((r for r in queues[account] if r['id'] not in taken), None)
        if row is not None:
            taken.add(row['id'])
            picked[account] = row
    return picked


def predict[K: Hashable](target: Mapping, ready: Sequence[K]) -> K | None:
    """The account a preview names, for display only: ``assign`` decides at send time. A conversation shows its own
    account, even one that cannot send now (``None`` when that account is unknown). A new one shows one of the
    ``ready`` accounts, the same one on every call."""
    if target['sender_pinned_at'] is not None:
        return target['sender_id']
    if not ready:
        return None
    ordered = sorted(ready, key=str)
    return ordered[int.from_bytes(hashlib.sha256(str(target['id']).encode()).digest()[:8], 'big') % len(ordered)]
