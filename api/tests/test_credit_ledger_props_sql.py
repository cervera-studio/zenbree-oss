"""Properties of the credit ledger (#660), on generated sequences of what holders do to a hold, against the real SQL.

The machine plays a holder run on a ``Tab``: it opens or grows a hold, records charges on complete and unfinished
rows, refunds the unfinished ones, closes the hold (also twice) and lets the reaper close what is left. A small model
predicts every answer; after every step the ledger must show:

- L1: the wallet is never negative.
- L2: money in = money out: wallet = initial - reservations and deductions + refunds, row by row of the ledger.
- L3: pay-if-found: a closed hold keeps what its charges that were not refunded cost, at most its amount.
- L4: only a hold whose run is still going is reserved: the reaper closes every other one.
- L5: a closed hold stays closed: another close, a charge or a refund raises ``HoldGone`` and changes no row.
- L6: a project's spend this month never passes its hard cap.

A real holder, «Cerca il telefono» on a row (H1), clicked with generated vendor outcomes: only a found number is
charged, at its price; a miss, a vendor failure, a crash or a kill switch charges nothing, and no click leaves a hold.

Out of the model: the month boundary (``carried`` in ``credits.grow``) and concurrent holders."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from app.agent.tools.handlers import enrich_phone
from app.billing import credits
from app.billing.price import Price
from app.billing.tab import HoldGone, Tab
from app.lists.rows import add_rows, create_list
from tests.conftest import project
from tests.test_agent import _balance, _chat, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_charge_final import _require
from tests.test_row_phone import _apollo, _row
from tests.test_row_phone import vendors as vendors

pytestmark = needs_db

# Deterministic: the same programs on every run, and no example database left in the repo.
LEDGER = settings(
    max_examples=45, stateful_step_count=30, derandomize=True, database=None, deadline=None, print_blob=False
)
LIVE = 2  # holds open at once: one run keeps one hold, and a few runs share the wallet
# A click goes through the whole route, so fewer examples than the machine's.
CLICKS = settings(LEDGER, max_examples=20)
ROWS = 6  # the first two complete, the rest unfinished until a turn fills their required phone


@dataclass
class Charge:
    row: int
    credits: int
    state: Literal['held', 'charged', 'refunded']


@dataclass
class Model:
    amount: int
    charges: list[Charge] = field(default_factory=list)
    kept: int | None = None  # None while the hold is reserved

    @property
    def spent(self) -> int:
        return sum(c.credits for c in self.charges if c.state != 'refunded')


def _moved(db, org) -> int:
    """What the organization's ledger moved into the wallet: refunds in, reservations and deductions out."""
    return db.execute(
        "SELECT coalesce(sum(CASE WHEN type = 'refund' THEN amount ELSE -amount END), 0) AS n"
        " FROM credit_transactions WHERE organization_id = %s AND type IN ('reservation', 'deduction', 'refund')",
        (org,),
    ).fetchone()['n']


class Ledger(RuleBasedStateMachine):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.holds: dict[str, Model] = {}
        self.tabs: list[Tab] = []
        self.retried: set[str] = set()

    # an empty wallet stays possible, but most runs have credits to spend
    @initialize(initial=st.integers(0, 300) | st.integers(40, 300), cap=st.none() | st.integers(1, 200))
    def start(self, initial, cap):
        db = self.db
        self.initial, self.wallet, self.cap = initial, initial, cap
        uid = _user(db, credits=initial)
        self.org = str(db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o'])
        self.pid = project(db, uid)
        if cap is not None:
            db.execute("UPDATE projects SET credit_cap = %s, credit_cap_mode = 'hard' WHERE id = %s", (cap, self.pid))
        self.payer = credits.Payer(self.org, uid, self.pid)
        self.list_id = create_list(db, user_id=uid, session_id=_chat(db, uid), name='People', entity='person')
        self.rows = add_rows(
            db,
            self.list_id,
            'person',
            [{'full_name': f'P {i}', 'linkedin_url': f'https://linkedin.com/in/p{i}'} for i in range(ROWS)],
        )
        _require(db, self.list_id, 'phone')
        self.complete = [False] * ROWS
        for i in range(2):
            self._finish(i)

    @precondition(lambda self: len(self._live()) < LIVE)
    @rule(want=st.integers(1, 60), need=st.integers(1, 60))
    def open(self, want, need):
        need = min(need, want)
        amount = min(want, self._room())
        tab = Tab.open(
            self.db, self.payer, want, need=need, kind='job', reference_id=None, description='prop', job_id=None
        )
        if amount < need:
            assert tab == credits.Refused(capped=self._capped(need))
            return
        assert isinstance(tab, Tab) and tab.hold.amount == amount, (tab, amount)
        self.holds[tab.hold.id] = Model(amount)
        self.tabs.append(tab)
        self.wallet -= amount

    @precondition(lambda self: self._live())
    @rule(pick=st.integers(0, LIVE - 1), want=st.integers(1, 60), need=st.integers(1, 60))
    def cover(self, pick, want, need):
        tab, hold = self._pick(self._live(), pick)
        need = min(need, want)
        amount = min(want, self._room())
        grown = tab.cover(self.db, self.payer, want, need=need)
        if amount < need:
            assert grown == credits.Refused(capped=self._capped(need))
            return
        hold.amount += amount
        self.wallet -= amount
        assert grown is tab and tab.hold.amount == hold.amount

    @precondition(lambda self: self._live())
    @rule(pick=st.integers(0, LIVE - 1), row=st.integers(0, ROWS - 1), price=st.integers(1, 10), over=st.booleans())
    def record(self, pick, row, price, over):
        tab, hold = self._pick(self._live(), pick)
        # A holder records within its hold, but Tab.record does not check that: when a buggy one records past it,
        # settle_hold still keeps no more than the hold (L3), so the reaper can settle it.
        if not over:
            price = min(price, max(hold.amount - hold.spent, 0))
        if price:
            self._record(tab, row, price)
            hold.charges.append(Charge(row, price, 'charged' if self.complete[row] else 'held'))

    @precondition(lambda self: self._live())
    @rule(pick=st.integers(0, LIVE - 1), finished=st.sets(st.integers(0, ROWS - 1), max_size=1))
    def refund_unfinished(self, pick, finished):
        tab, hold = self._pick(self._live(), pick)
        for row in finished:  # the turn filled these rows before it ended
            self._finish(row)
        refunds = tab.refund_unfinished(self.db)
        expected = []
        for c in hold.charges:
            if c.state == 'held':
                c.state = 'charged' if self.complete[c.row] else 'refunded'
                if c.state == 'refunded':
                    expected.append((self.rows[c.row], c.credits))
        assert sorted((r.row_id, r.credits) for r in refunds) == sorted(expected)

    @precondition(lambda self: self._live())
    @rule(pick=st.integers(0, LIVE - 1))
    def close(self, pick):
        tab, _ = self._pick(self._live(), pick)
        assert tab.close(self.db) == self._settled(tab.hold.id)

    @precondition(lambda self: self._unretried())
    @rule(pick=st.integers(0, LIVE - 1))
    def after_close(self, pick):  # L5, once for each closed hold
        tab = self._unretried()[pick % len(self._unretried())]
        self.retried.add(tab.hold.id)
        before = self._snapshot()
        for act in (
            lambda: tab.close(self.db),
            lambda: self._record(tab, 0, 1),
            lambda: tab.refund_unfinished(self.db),
        ):
            with pytest.raises(HoldGone):
                act()
        assert tab.cover(self.db, self.payer, 1, need=1) == credits.Refused(capped=self._capped(1))
        assert self._snapshot() == before

    @precondition(lambda self: self._live())
    @rule()
    def reap(self):
        reaped = {r['r'] for r in self.db.execute('SELECT r::text FROM reap_stale_reservations_ids(0) AS r')}
        open_ids = {t.hold.id for t in self._live()}
        assert reaped == open_ids
        for hold_id in open_ids:
            self._settled(hold_id)

    def _finish(self, row: int) -> None:
        """Fills the row's required phone: the row is complete."""
        self.db.execute(
            """UPDATE list_rows SET cells = cells || '{"phone": "+390001"}' WHERE id = %s""", (self.rows[row],)
        )
        self.complete[row] = True

    @invariant()
    def wallet_is_the_model_and_never_negative(self):  # L1
        assert _balance(self.db, self.payer.user_id) == self.wallet >= 0

    @invariant()
    def money_in_equals_money_out(self):  # L2
        assert self.initial + _moved(self.db, self.org) == _balance(self.db, self.payer.user_id)

    @invariant()
    def month_spend_within_the_cap(self):  # L6
        spend = self.db.execute('SELECT project_month_spend(%s) AS s', (self.pid,)).fetchone()['s']
        assert spend == self._month_spend() <= (self.cap if self.cap is not None else spend)

    @invariant()
    def only_live_holds_are_reserved(self):  # L4
        reserved = self.db.execute(
            "SELECT id::text FROM credit_transactions WHERE organization_id = %s AND status = 'reserved'", (self.org,)
        ).fetchall()
        assert {r['id'] for r in reserved} == {t.hold.id for t in self._live()}

    def teardown(self):
        # Checks here would fail again after a failed step, at another line on each replay, and Hypothesis would
        # report a flaky test. The release only keeps a hold of this example from the next example's reaper.
        if hasattr(self, 'org'):
            self.db.execute(
                "UPDATE credit_transactions SET status = 'released' WHERE organization_id = %s AND status = 'reserved'",
                (self.org,),
            )

    def _live(self) -> list[Tab]:
        return [t for t in self.tabs if self.holds[t.hold.id].kept is None]

    def _closed(self) -> list[Tab]:
        return [t for t in self.tabs if self.holds[t.hold.id].kept is not None]

    def _unretried(self) -> list[Tab]:
        return [t for t in self._closed() if t.hold.id not in self.retried]

    def _pick(self, tabs: list[Tab], pick: int) -> tuple[Tab, Model]:
        tab = tabs[pick % len(tabs)]
        return tab, self.holds[tab.hold.id]

    def _record(self, tab: Tab, row: int, price: int) -> None:
        with self.db.transaction():
            # the delivery stores the bought cell in the transaction that records its charge
            self.db.execute(
                """UPDATE list_rows SET cells = cells || '{"email": "x@acme.test"}' WHERE id = %s""", (self.rows[row],)
            )
            tab.record(self.db, uuid.uuid4().hex, self.list_id, [self.rows[row]], Price(price, ('email',), bool))

    def _month_spend(self) -> int:
        return sum(h.amount if h.kept is None else h.kept for h in self.holds.values())

    def _room(self) -> int:
        return self.wallet if self.cap is None else min(self.wallet, max(self.cap - self._month_spend(), 0))

    def _capped(self, need: int) -> bool:
        return self.cap is not None and max(self.cap - self._month_spend(), 0) < need

    def _settled(self, hold_id: str) -> int:
        """The model's close of a reserved hold: held charges become final, it keeps their cost (L3)."""
        hold = self.holds[hold_id]
        for c in hold.charges:
            if c.state == 'held':
                c.state = 'charged'
        hold.kept = min(hold.amount, hold.spent)
        self.wallet += hold.amount - hold.kept
        assert credits.kept(self.db, hold_id) == hold.kept
        status = self.db.execute('SELECT status FROM credit_transactions WHERE id = %s', (hold_id,)).fetchone()
        assert status['status'] == ('committed' if hold.kept else 'released')
        return hold.kept

    def _snapshot(self) -> tuple:
        return (
            self.db.execute(
                'SELECT id, type, status, amount, settlement_net_credits FROM credit_transactions'
                ' WHERE organization_id = %s ORDER BY id',
                (self.org,),
            ).fetchall(),
            self.db.execute(
                'SELECT id, state, credits FROM row_charges WHERE organization_id = %s ORDER BY id', (self.org,)
            ).fetchall(),
            _balance(self.db, self.payer.user_id),
        )


def test_the_ledger_holds_its_invariants_under_any_run(db):
    run_state_machine_as_test(lambda: Ledger(db), settings=LEDGER)


# The status a click on a row with no number answers, per vendor outcome.
STATUS = {'found': 'found', 'missing': 'not_found', 'down': 'failed', 'crash': 'failed', 'off': 'unavailable'}


def test_a_click_is_charged_only_for_a_found_number(client, db, http, monkeypatch):  # H1
    c, uid = client
    db.execute('UPDATE user_credits SET credits_remaining = 10000 WHERE user_id = %s', (uid,))
    org = db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    now = {'outcome': 'found'}
    vendor = {
        'found': _apollo([{'sanitized_number': '+39333111222'}]),
        'missing': _apollo([]),
        'down': _apollo(None),  # Apollo answers 503: paid_call returns an Err
    }
    real = enrich_phone._phone_one

    async def phone_one(*args):
        if now['outcome'] == 'crash':
            raise RuntimeError('boom')
        return await real(*args)

    monkeypatch.setattr(enrich_phone, '_phone_one', phone_one)
    monkeypatch.setattr(
        enrich_phone, 'providers_off', lambda ps: frozenset(ps) if now['outcome'] == 'off' else frozenset()
    )
    http.handler = lambda request: vendor.get(now['outcome'], vendor['missing'])(request)

    def held() -> int:
        return db.execute(
            "SELECT count(*) AS n FROM credit_transactions WHERE organization_id = %s AND status = 'reserved'", (org,)
        ).fetchone()['n']

    @CLICKS
    @given(st.lists(st.tuples(st.integers(0, 1), st.sampled_from(sorted(STATUS))), min_size=1, max_size=5))
    def clicks(steps):
        # an earlier example that failed may have left a hold: only this example's clicks count
        wallet, moved, left = _balance(db, uid), _moved(db, org), held()
        rows, found = [_row(db, uid) for _ in range(2)], set()
        for pick, outcome in steps:
            now['outcome'] = outcome
            lid, rid = rows[pick]
            body = c.post(f'/v1/lists/{lid}/rows/{rid}/phone').json()
            # a row with a number is never searched again: a second click costs nothing
            status = 'already' if pick in found else STATUS[outcome]
            assert (body['status'], body['credits']) == (status, enrich_phone.PRICE.credits * (status == 'found'))
            if status == 'found':
                found.add(pick)
        assert held() == left
        assert _balance(db, uid) == wallet - enrich_phone.PRICE.credits * len(found)
        assert _balance(db, uid) - wallet == _moved(db, org) - moved  # L2

    clicks()
