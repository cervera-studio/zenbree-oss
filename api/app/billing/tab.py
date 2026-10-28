"""One holder run's credits (#660): one hold, the per-row charges it pays for, and its close.

A charge names its hold (``row_charges.hold_id``) and needs that hold still reserved. The close and the reaper both
call ``settle_hold``, which settles the hold at what its charges cost, so a holder killed before its close is settled
by the rule that settles a live one."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass

from app.billing import credits, row_charges
from app.billing.price import Price


class HoldGone(Exception):
    """The hold is no longer reserved: the reaper closed it. A delivering transaction rolls back, so no cell lands
    that no hold pays for; a close finds the reaper's settle already made."""


@dataclass
class Tab:
    hold: credits.Hold
    job_id: str | None = None  # the chat turn its charges belong to

    @classmethod
    def open(
        cls,
        conn,
        payer: credits.Payer,
        want: int,
        *,
        need: int | None = None,
        kind: str,
        reference_id: str | None,
        description: str,
        tool_name: str | None = None,
        session_id: str | None = None,
        job_id: str | None = None,
    ) -> Tab | credits.Refused:
        hold = credits.reserve(
            conn,
            payer,
            want,
            need=need,
            reference_type=kind,
            reference_id=reference_id,
            tool_name=tool_name,
            session_id=session_id,
            description=description,
        )
        return hold if isinstance(hold, credits.Refused) else cls(hold, job_id)

    @classmethod
    def stored(cls, conn, hold_id: str) -> Tab:
        """The tab of a hold whose id a run's row keeps between its transactions (a scheduled search, a watchlist)."""
        return cls(credits.find_hold(conn, hold_id))

    def cover(self, conn, payer: credits.Payer, want: int, *, need: int) -> Tab | credits.Refused:
        """Grows the hold by up to ``want``, at least ``need``: a run keeps one hold. Returns this tab, or the
        refusal with the hold unchanged."""
        grown = credits.grow(conn, payer, self.hold, want, need=need)
        if isinstance(grown, credits.Refused):
            return grown
        self.hold = grown
        return self

    def lock(self, conn) -> None:
        """Locks the hold until the caller's commit, so nothing closes it under a charge; HoldGone when it no
        longer pays."""
        if not credits.live(conn, self.hold.id):
            raise HoldGone(self.hold.id)

    def record(self, conn, call_id: str, list_id: str, row_ids: Sequence[str], price: Price) -> int:
        """In the transaction that stores the rows: one charge of ``price.credits`` per row. Returns their credits."""
        self.lock(conn)
        row_charges.charge(conn, self.hold.id, self.job_id, call_id, list_id, row_ids, price.keys, price.credits)
        return len(row_ids) * price.credits

    def refund_unfinished(self, conn) -> list[row_charges.Refund]:
        """Locks the hold first, as a charge does; HoldGone when the reaper closed it, which made every held charge
        final."""
        self.lock(conn)
        return row_charges.refund_unfinished(conn, self.hold.id)

    def close(self, conn) -> int:
        """Settles the hold at what its charges cost (``settle_hold``) and returns that; HoldGone when it was not
        reserved."""
        spent = credits.close(conn, self.hold.id)
        if spent is None:
            raise HoldGone(self.hold.id)
        return spent


def close_stored(conn, hold_id: str) -> None:
    """Closes the hold a run's row names, from its charges: a release when it has none. A hold the reaper closed
    first stays as it settled."""
    with suppress(HoldGone):
        Tab.stored(conn, hold_id).close(conn)
