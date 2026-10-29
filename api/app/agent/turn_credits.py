"""The credits of one chat turn (#660): its hold on the chat's organization, what its tool calls spent, and the
per-row charges it gives back at the end (#45, #416).

One turn keeps one ``Tab``: it takes a hold when it starts and grows it (``grow_reservation``) when a call needs
more, so the hold id names the turn. Each charge names that hold. The end of the turn is one transaction: it gives
back the charges on rows still unfinished, closes the hold at what its other charges cost (``settle_hold``) and
caches that spend on the job, so a kill or a failure leaves all three undone, for the reaper to settle from the
charges. The loop sends the frames and writes the job and the message; this object keeps the counts they read."""

from __future__ import annotations

import structlog

from app.agent.tools.registry import ToolResult
from app.billing import balance, credits, row_charges
from app.billing.price import Price
from app.billing.tab import HoldGone, Tab
from app.core.db import get_conn

logger = structlog.get_logger()

# First hold per job; grown in chunks of at least this when a call needs more.
JOB_CREDIT_RESERVE = 100


class TurnCredits:
    def __init__(self, job_id: str, user_id: str, session_id: str) -> None:
        self.job_id, self.user_id, self.session_id = job_id, user_id, session_id
        self.tab: Tab | None = None
        self.spent = 0
        self.balance = 0  # the wallet after the hold, as last read
        self.org_id = ''  # whose wallet the hold is on, read before the first hold
        self.capped = False  # #307: the project's hard monthly cap refused a hold or a growth
        self.refunded = 0  # #45 per-row credits given back at the end, for the bell (#215)
        self.undelivered = 0  # the rows they were given back for (#351)

    @property
    def unspent(self) -> int:
        return (self.tab.hold.amount if self.tab else 0) - self.spent

    @property
    def remaining(self) -> int:
        """The wallet as the user sees it: the balance plus what the hold keeps unspent."""
        return self.balance + self.unspent

    def reserve(self, need: int = 0) -> bool:
        """Take or grow the hold by max(need, JOB_CREDIT_RESERVE), capped by the balance and the project's cap
        room. False when they cannot cover ``need``."""
        ok = self._hold(need)
        # A hold, or credits short: either way the balance rules run (a short balance is when auto top-up
        # matters most, and it re-queues them if an earlier enqueue was lost).
        if self.org_id:
            balance.changed(self.org_id, refused=not ok)
        return ok

    def _hold(self, need: int) -> bool:
        with get_conn() as conn:
            # The chat's organization pays (#306); the user is who spent. A chat deleted mid-turn holds nothing. The
            # turn runs as its member, so one removed (#308) or banned (#389) mid-turn gets ActorGone at the checkout.
            row = conn.execute(
                'SELECT s.organization_id, s.project_id, c.credits_remaining FROM chat_sessions s'
                ' LEFT JOIN user_credits c ON c.organization_id = s.organization_id WHERE s.id = %s',
                (self.session_id,),
            ).fetchone()
            if row is None:
                self.balance = 0
                return need <= 0
            self.org_id = str(row['organization_id'])
            self.balance = row['credits_remaining'] or 0
            # #307: the chat's project may have a hard monthly cap; a hold never goes past it.
            payer = credits.Payer(self.org_id, self.user_id, str(row['project_id']))
            want = max(need, JOB_CREDIT_RESERVE)
            held = self.tab.hold.amount if self.tab else 0
            tab = (
                self.tab.cover(conn, payer, want, need=need)
                if self.tab
                else Tab.open(
                    conn,
                    payer,
                    want,
                    need=need,
                    kind='job',
                    reference_id=self.job_id,
                    session_id=self.session_id,
                    description='Job credit hold',
                    job_id=self.job_id,
                )
            )
            if isinstance(tab, credits.Refused):
                if need <= 0:  # nothing to hold and nothing needed: the turn still runs its free tools
                    return True
                self.capped = tab.capped
                return False
            self.tab, hold = tab, tab.hold
            self.balance -= hold.amount - held
            conn.execute(
                'UPDATE jobs SET reservation_id = %s, credits_reserved = %s WHERE id = %s',
                (hold.id, hold.amount, self.job_id),
            )
        return True

    def cover(self, amount: int) -> bool:
        """Make the unspent hold cover ``amount``, growing it if needed."""
        short = amount - self.unspent
        return short <= 0 or self.reserve(short)

    def refusal(self) -> ToolResult:
        if self.capped:
            error = (
                "This client's monthly credit cap is reached: no paid step runs until next month or until an admin"
                ' raises the cap.'
            )
        else:
            error = 'Not enough credits for this step. Top up credits to continue.'
        return ToolResult(ok=False, error=error, error_code='no_credits')

    def lock(self, conn, credits: int) -> None:
        """First in a delivery that may bill ``credits``: locks the hold before the job, the order ``_hold`` takes
        them in, and raises HoldGone before anything of the call is stored."""
        if credits:
            self._tab().lock(conn)

    def record(self, conn, call_id: str, list_id: str, row_ids: list[str], price: Price) -> int:
        """In the transaction that stores the rows: their charges on the turn's hold, and the job's spend, a cache
        of those charges that the job readers read. ``spent`` grows only once that transaction commits."""
        credits = self._tab().record(conn, call_id, list_id, row_ids, price)
        conn.execute('UPDATE jobs SET credits_spent = %s WHERE id = %s', (self.spent + credits, self.job_id))
        return credits

    def _tab(self) -> Tab:
        if self.tab is None:  # a paid call covers its quote first: not a gone hold, so the turn fails loud
            raise RuntimeError(f'job {self.job_id}: a paid delivery with no hold')
        return self.tab

    def finish(self) -> tuple[dict[str, tuple[int, int]], list[str]]:
        """#45, #416: a row still missing a required column gives back its held per-row credits and the cells they
        bought, each to the call that charged them (#659), in the transaction that closes the hold and writes the
        job's spend. A hold the reaper closed first gives nothing back: the reaper made its held charges final, and
        the turn spent what they cost. Returns (credits, rows) given back per call id, and the list id of every row
        that had a held charge. The counts change only once that transaction commits."""
        if self.tab is None:
            return {}, []
        tab = self.tab
        with get_conn() as conn:
            try:
                refunds = tab.refund_unfinished(conn)
            except HoldGone:
                logger.warning('agent.hold_reaped', job_id=self.job_id, hold_id=tab.hold.id)
                refunds, spent = [], credits.kept(conn, tab.hold.id)
            else:
                spent = tab.close(conn)  # a raise here rolls the refund back with it
            lists = row_charges.settled_lists(conn, tab.hold.id)
            conn.execute('UPDATE jobs SET credits_spent = %s WHERE id = %s', (spent, self.job_id))
        given = row_charges.refunds_by_call(refunds, self.spent)
        self.refunded = sum(back for back, _ in given.values())
        self.undelivered = len({refund.row_id for refund in refunds})
        self.spent = spent  # the ledger, not the count, says what the turn keeps
        balance.changed(self.org_id)
        return given, lists
