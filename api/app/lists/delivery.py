"""What a tool call delivers and what it bills, in one transaction (#418, #668): the charge comes from the found
rows that landed, never from what the handler says it billed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.lists import rows

if TYPE_CHECKING:
    from app.billing.price import Price, Quote


class Recorder(Protocol):
    """Who pays a delivery's charges: the chat turn's ``TurnCredits``, which also caches the job's spend, or the
    ``Tab`` of a holder with no job."""

    def record(self, conn, call_id: str, list_id: str, row_ids: list[str], price: Price) -> int: ...


@dataclass(frozen=True)
class Delivery:
    landed: list[str]
    credits: int
    undelivered: dict[str, list[str]]  # row id -> the required columns it still misses (#539)

    @property
    def counts(self) -> dict:
        """What the model reads in the call's ``list``: the rows it updated and, only when there are some, the rows
        still short of a required column, which it cannot see in the list."""
        return {'rows_updated': len(self.landed), **({'undelivered': self.undelivered} if self.undelivered else {})}


def _row(update: dict) -> object:
    """One key per row, whatever uuid spelling names it: two spellings of one row would land twice and bill twice."""
    try:
        return UUID(str(update.get('row_id')))
    except ValueError:
        return update.get('row_id')


def deliver(
    conn, tab: Recorder, quote: Quote, call_id: str, updates: list[dict], list_id: str, entity: str
) -> Delivery:
    """Stores the call's row ``updates`` on ``list_id`` and bills each found row that landed at ``price.credits``,
    recorded under ``call_id``. Found rows past the quote are not stored: they stay untried for a later call, and the
    hold never pays past the quote."""
    price, found, misses, seen = quote.price, [], [], set()
    for update in updates:
        if not price.found(update.get('cells') or {}):
            misses.append(update)
        elif (row := _row(update)) not in seen:
            seen.add(row)
            found.append(update)
    within_quote = found[: quote.rows]
    missed = rows.apply_row_updates(conn, list_id, entity, misses)
    billed = rows.apply_row_updates(conn, list_id, entity, within_quote)  # what landed, not a miss on the same row
    credits = tab.record(conn, call_id, list_id, billed, price) if billed else 0
    landed = [*missed, *billed]
    return Delivery(landed, credits, rows.missing_required(conn, list_id, landed) if landed else {})
