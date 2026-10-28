"""What a paid tool charges: one ``Price``, declared on its ``ToolDef`` (#418, #476, #668).

A price is per row delivered. ``keys`` are the cells a charge buys: a refund takes them back, and the scorer never
reads them. A cell that a found row gets outside ``keys`` is free (#476). ``found`` tells a delivered row from a miss
by the cells of its update, and a miss bills nothing (pay-if-found)."""

from collections.abc import Callable
from dataclasses import dataclass


def units(args: dict, schema: dict, batch: int) -> int:
    """Rows a call can deliver, before it runs: one per row it names, each row once whether it is in ``row_ids`` or a
    person's ``row_id`` (#418), else one per item, else ``batch``. Only the arguments the schema takes count: the
    handler ignores the others, so they must not lower the price."""
    takes = schema.get('properties') or {}

    def listed(key: str) -> list:
        value = args.get(key) if key in takes else None
        return value if isinstance(value, list) else []

    people = [p['row_id'] for p in listed('people') if isinstance(p, dict) and p.get('row_id')]
    rows = {str(r) for r in [*listed('row_ids'), *people]}
    return len(rows) or len(listed('items')) or batch


@dataclass(frozen=True)
class Quote:
    """What one call can deliver, fixed before it runs."""

    price: Price
    rows: int

    @property
    def credits(self) -> int:
        return self.rows * self.price.credits


@dataclass(frozen=True)
class Price:
    credits: int  # per row delivered
    keys: tuple[str, ...]
    found: Callable[[dict], bool]
    batch: int = 1  # rows a call that names no row can deliver

    def quote(self, args: dict, schema: dict) -> Quote:
        return Quote(self, units(args, schema, self.batch))
