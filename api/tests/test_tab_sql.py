"""Charges name their hold (``row_charges.hold_id``), and one SQL rule closes a hold from them (``settle_hold``):
the turn's own close and the reaper, for every kind of hold."""

import dataclasses
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from app.agent.tools import registry
from app.agent.turn_credits import TurnCredits
from app.billing import credits, row_charges
from app.billing.price import Price
from app.billing.tab import HoldGone
from app.core.config import settings
from app.core.db import get_conn
from app.core.migrate import MIGRATIONS
from app.core.scope import for_another_thread
from app.lists.lookup_outcome import searched
from app.lists.row_view import VISIBLE_CELLS_SQL
from app.lists.rows import apply_row_updates
from app.llm.base import ToolCall
from tests.conftest import act_in_own_org, hold
from tests.test_agent import _balance, _job, _people, _run, _user, needs_db, paid_emails
from tests.test_agent import db as db
from tests.test_charge_final import _require
from tests.test_credit_callers_sql import _ledger

pytestmark = needs_db

HOLD = '20261013183923_row_charges_hold.sql'
OLD_REAPER = '20261007163312_reap_job_holds_for_spend.sql'
EMAIL = Price(5, ('email', 'email_status'), lambda cells: bool(cells.get('email')))


def _turn(db, rows=2, credits=100) -> tuple[str, TurnCredits, str, list[str]]:
    """A running job whose turn holds credits, and a person list of ``rows`` rows. (user, turn, list, rows)."""
    uid = _user(db, credits=credits)
    sid, jid = _job(db, uid, 'email dei CEO')
    db.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (jid,))
    ids = _people(db, uid, sid, rows)
    lid = db.execute('SELECT list_id::text FROM list_rows WHERE id = %s', (ids[0],)).fetchone()['list_id']
    act_in_own_org(db, uid)  # the turn's worker runs as the job's member
    turn = TurnCredits(str(jid), uid, str(sid))
    assert turn.reserve()
    return uid, turn, lid, [str(r) for r in ids]


def _emails(row_ids) -> list[dict]:
    return [{'row_id': r, 'cells': {'email': f'{r}@acme.test', 'email_status': 'verified'}} for r in row_ids]


def _stale(db) -> None:
    db.execute("UPDATE credit_transactions SET created_at = created_at - interval '1 hour'")


def _reap(db) -> list[str]:
    _stale(db)
    return [r['r'] for r in db.execute('SELECT r::text FROM reap_stale_reservations_ids(20) AS r')]


def _settle_hold(conn, hold_id) -> int | None:
    return conn.execute('SELECT settle_hold(%s) AS s', (hold_id,)).fetchone()['s']


def _wait_for_a_lock(db, what: str) -> None:
    deadline = time.monotonic() + 10
    while not db.execute(
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone():
        assert time.monotonic() < deadline, f'{what} never waited on a lock'
        time.sleep(0.01)


def test_a_charge_after_the_reaper_closed_the_hold_raises_and_lands_no_cell(db):
    uid, turn, lid, (ada, _) = _turn(db)
    assert _reap(db) == [turn.tab.hold.id]

    def deliver() -> None:
        with db.transaction():
            turn.record(db, 'c1', lid, apply_row_updates(db, lid, 'person', _emails([ada])), EMAIL)

    with pytest.raises(HoldGone):
        deliver()

    assert db.execute("SELECT count(*) AS n FROM list_rows WHERE cells ? 'email'").fetchone()['n'] == 0
    assert db.execute('SELECT count(*) AS n FROM row_charges').fetchone()['n'] == 0
    assert _balance(db, uid) == 100


async def test_a_call_whose_hold_the_reaper_closed_stores_and_bills_nothing_and_the_turn_goes_on(db, monkeypatch):
    """The reaper closes the hold while the handler runs: the call fails like a crash and the model goes on."""
    paid = paid_emails('fake_reaped', credits=5)

    async def reaped_then_found(args, ctx):
        with get_conn() as conn:
            rows = [r['id'] for r in conn.execute('SELECT id::text FROM list_rows WHERE list_id = %s', (ctx.list_id,))]
        await ctx.claim('email', rows)
        with get_conn() as conn:
            conn.execute(
                "UPDATE credit_transactions SET created_at = created_at - interval '1 hour'"
                ' WHERE id = (SELECT reservation_id FROM jobs WHERE id = %s)',
                (ctx.job_id,),
            )
            conn.execute('SELECT reap_stale_reservations_ids(20)').fetchall()
        return await paid.handler(args, ctx)

    monkeypatch.setitem(registry._REGISTRY, 'fake_reaped', dataclasses.replace(paid, handler=reaped_then_found))
    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, 'email dei CEO')
    _people(db, uid, sid, 2)

    events, provider = await _run(jid, [('', [ToolCall('t1', 'fake_reaped', {})]), ('fatto', [])])

    assert dict(events)['tool_result']['status'] == 'failed'
    assert db.execute("SELECT count(*) AS n FROM list_rows WHERE cells ? 'email'").fetchone()['n'] == 0
    assert db.execute('SELECT count(*) AS n FROM row_charges').fetchone()['n'] == 0
    assert (
        db.execute('SELECT count(*) AS n FROM lookup_claims').fetchone()['n'] == 0
    )  # HoldGone rolled back their release
    assert _balance(db, uid) == 100
    assert db.execute('SELECT status FROM jobs WHERE id = %s', (jid,)).fetchone()['status'] == 'succeeded'
    assert provider.turns == []


class _Killed(BaseException):
    """The worker dies: the turn neither refunds nor settles."""


async def test_a_turn_killed_after_one_held_charge_is_settled_from_it_and_its_cells_show(db, monkeypatch):
    """#415: the turn charged one email on a row that misses a required title, then died. The reaper keeps that
    charge, and the list shows the email it bought."""
    from app.agent import loop

    store = loop.Turn._deliver

    def store_and_die(self, *args):
        store(self, *args)
        raise _Killed

    monkeypatch.setattr(loop.Turn, '_deliver', store_and_die)
    monkeypatch.setattr(TurnCredits, 'finish', lambda self: ({}, []))
    monkeypatch.setitem(registry._REGISTRY, 'fake_paid', paid_emails('fake_paid', credits=5))
    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, 'email dei CEO')
    (row,) = _people(db, uid, sid, 1)
    _require(db, db.execute('SELECT list_id FROM list_rows WHERE id = %s', (row,)).fetchone()['list_id'], 'title')

    with pytest.raises(_Killed):
        await _run(jid, [('', [ToolCall('t1', 'fake_paid', {})]), ('fatto', [])])
    visible = f'SELECT {VISIBLE_CELLS_SQL} AS cells FROM list_rows r WHERE r.id = %s'
    assert 'email' not in db.execute(visible, (row,)).fetchone()['cells']  # pending while the hold is reserved

    assert len(_reap(db)) == 1

    assert _balance(db, uid) == 95
    assert db.execute('SELECT state FROM row_charges').fetchall() == [{'state': 'charged'}]
    assert db.execute(visible, (row,)).fetchone()['cells']['email'] == f'{row}@acme.test'


def test_a_second_settle_of_a_hold_changes_nothing(db):
    uid, turn, lid, (ada, bo) = _turn(db)
    with db.transaction():
        landed = apply_row_updates(db, lid, 'person', _emails([ada, bo]))
        turn.record(db, 'c1', lid, landed, EMAIL)

    assert _settle_hold(db, turn.tab.hold.id) == 10
    after = (_ledger(db, uid), _balance(db, uid))
    assert _settle_hold(db, turn.tab.hold.id) is None
    assert (
        (_ledger(db, uid), _balance(db, uid))
        == after
        == (
            [('deduction', 'committed', 100, 'job'), ('refund', 'committed', 90, 'reservation.slack')],
            90,
        )
    )


def _seeded_dead_turn(db, case: str) -> str:
    """A turn that died after its charges: none, three final ones, or three of which the turn's own refund gave one
    back before the kill. Its job caches what the turn spent, as the loop writes it. Returns the user."""
    uid, turn, lid, rows = _turn(db, rows=3)
    if case == 'refunded':
        _require(db, lid, 'title')
        db.execute('UPDATE list_rows SET cells = cells || \'{"title": "CEO"}\' WHERE id = ANY(%s::uuid[])', (rows[:2],))
    if case != 'none':
        with db.transaction():
            landed = apply_row_updates(db, lid, 'person', _emails(rows))
            turn.spent = turn.record(db, 'c1', lid, landed, EMAIL)
    if case == 'refunded':  # a refund without its close: the old reaper's rule covered it
        with db.transaction():
            turn.spent -= sum(refund.credits for refund in row_charges.refund_unfinished(db, turn.tab.hold.id))
        db.execute('UPDATE jobs SET credits_spent = %s WHERE id = %s', (turn.spent, turn.job_id))
    _stale(db)
    return uid


@pytest.mark.parametrize(('case', 'spent'), [('none', 0), ('charged', 15), ('refunded', 10)])
def test_settle_hold_keeps_what_the_old_reaper_kept_of_a_dead_job(db, case, spent):
    """The old reaper kept ``jobs.credits_spent``; ``settle_hold`` keeps what the job's charges cost. On the same
    dead turn they move the wallet and write the ledger alike."""
    old = (
        (MIGRATIONS / OLD_REAPER).read_text().replace('reap_stale_reservations_ids', 'old_reap_stale_reservations_ids')
    )
    db.execute(old)
    first = _seeded_dead_turn(db, case)
    db.execute('SELECT old_reap_stale_reservations_ids(20)').fetchall()
    second = _seeded_dead_turn(db, case)
    db.execute('SELECT reap_stale_reservations_ids(20)').fetchall()

    assert _balance(db, first) == _balance(db, second) == 100 - spent
    assert _ledger(db, first) == _ledger(db, second)


def test_the_turns_own_close_settles_from_its_charges_not_its_count(db):
    uid, turn, lid, (ada, _) = _turn(db)
    with db.transaction():
        landed = apply_row_updates(db, lid, 'person', _emails([ada]))
        turn.record(db, 'c1', lid, landed, EMAIL)
    turn.spent = 40  # a count gone wrong

    turn.finish()

    assert (_balance(db, uid), turn.spent) == (95, 5)


@pytest.mark.parametrize('kind', ['enrich_phone', 'ai_column', 'saved_search', 'watchlist'])
def test_every_other_kind_of_stale_hold_with_no_charge_is_released_whole(db, kind):
    from app import worker
    from app.lists.row_phone import TOOL
    from tests.test_grow_reservation_sql import _payer

    assert TOOL == 'enrich_phone'
    uid = _user(db, credits=100)
    taken = credits.reserve(db, _payer(db, uid), 30, reference_type=kind, reference_id='r', description='t')
    assert isinstance(taken, credits.Hold)
    _stale(db)

    assert worker.reap_stale_reservations() == {'released': 1}

    assert _balance(db, uid) == 100
    assert _ledger(db, uid) == [('refund', 'committed', 30, kind), ('reservation', 'released', 30, kind)]


@pytest.mark.parametrize(
    ('kind', 'for_the_turn'),
    [
        ('job', False),
        ('enrich_phone', False),
        ('ai_column', False),
        ('ai_column', True),  # a research fill: its charges name the turn that asked (#408)
        ('saved_search', False),
        ('watchlist', False),
    ],
)
def test_the_reaper_settles_every_kind_of_hold_from_its_charges(db, kind, for_the_turn):
    from app import worker
    from app.billing.tab import Tab
    from tests.test_grow_reservation_sql import _payer

    uid, turn, lid, (ada, bo) = _turn(db, credits=200)
    job = turn.job_id if for_the_turn else None
    tab = Tab.open(db, _payer(db, uid), 30, kind=kind, reference_id='r', description='t', job_id=job)
    assert isinstance(tab, Tab)
    with db.transaction():
        tab.record(db, 'c1', lid, apply_row_updates(db, lid, 'person', _emails([ada, bo])), EMAIL)
    charged = db.execute('SELECT job_id::text AS j FROM row_charges WHERE hold_id = %s', (tab.hold.id,)).fetchall()
    assert [r['j'] for r in charged] == [job, job]
    _stale(db)

    assert worker.reap_stale_reservations() == {'released': 2}  # this hold and the turn's own, which bought nothing

    assert _balance(db, uid) == 190
    assert ('refund', 'committed', 20, 'reservation.slack') in _ledger(db, uid)


def test_the_migration_names_each_charge_by_its_jobs_hold_and_makes_final_the_held_ones_no_hold_pays(db):
    """A held charge whose hold is gone or closed would stay held for good: the migration makes it final. The workers
    are stopped, so the hold of a turn that was running is settled too."""
    uid, turn, _, rows = _turn(db, rows=5, credits=1000)
    stopped, closed = turn.tab.hold.id, hold(db, uid)
    _settle_hold(db, closed)
    db.execute('ALTER TABLE row_charges DROP COLUMN hold_id')
    _, closed_job = _job(db, _user(db), 'earlier')  # one active job per user
    db.execute('UPDATE jobs SET reservation_id = %s WHERE id = %s', (closed, closed_job))
    _, no_hold_job = _job(db, _user(db), 'free')
    db.execute(  # in the rows' organization: row_charges names it with the job
        'UPDATE jobs SET chat_session_id = NULL, organization_id = (SELECT organization_id FROM list_rows WHERE id = %s)'
        ' WHERE id = ANY(%s::uuid[])',
        (rows[0], [closed_job, no_hold_job]),
    )
    seeded = {
        'stopped turn, held': (turn.job_id, 'held'),
        'stopped turn, charged': (turn.job_id, 'charged'),
        'closed hold, held': (closed_job, 'held'),
        'no hold, held': (no_hold_job, 'held'),
        'no job, refunded': (None, 'refunded'),
    }
    for row, (job, state) in zip(rows, seeded.values(), strict=True):
        db.execute(
            "INSERT INTO row_charges (job_id, row_id, keys, credits, state) VALUES (%s, %s, '{email}', 1, %s)",
            (job, row, state),
        )

    db.execute((MIGRATIONS / HOLD).read_text())  # the fixture ran it on an empty table: run it on this data

    after = {
        r['row_id']: (r['hold_id'], r['state'])
        for r in db.execute('SELECT row_id::text, hold_id::text, state FROM row_charges')
    }
    assert dict(zip(seeded, (after[r] for r in rows), strict=True)) == {
        'stopped turn, held': (stopped, 'charged'),
        'stopped turn, charged': (stopped, 'charged'),
        'closed hold, held': (closed, 'charged'),
        'no hold, held': (None, 'charged'),
        'no job, refunded': (None, 'refunded'),
    }


def test_the_migration_settles_a_job_that_took_several_holds_by_what_it_spent(db):
    """Before one growing hold a job could take several holds, and its records named none: it keeps what
    ``jobs.credits_spent`` says, filled into its holds in the order it took them."""
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, 'earlier')
    first, second = hold(db, uid, jid, amount=3), hold(db, uid, jid, amount=10)
    db.execute("UPDATE credit_transactions SET created_at = now() - interval '2 minutes' WHERE id = %s", (first,))
    db.execute('UPDATE jobs SET reservation_id = %s, credits_spent = 5 WHERE id = %s', (first, jid))
    db.execute('ALTER TABLE row_charges DROP COLUMN hold_id')

    db.execute((MIGRATIONS / HOLD).read_text())
    _reap(db)

    assert _balance(db, uid) == 95
    held = "SELECT count(*) AS n FROM credit_transactions WHERE status = 'reserved' AND id IN (%s, %s)"
    assert db.execute(held, (first, second)).fetchone()['n'] == 0


def test_a_campaign_draft_during_the_turns_close_waits_and_neither_deadlocks(db):
    """``settle_hold`` locks the hold, then the held charges by id; the draft's ``finalize`` locks the same
    charges by id. Each waits for the other's commit, in both orders."""
    uid, turn, lid, (ada, bo) = _turn(db)
    _require(db, lid, 'title')  # every row unfinished: each charge is held
    with db.transaction():
        landed = apply_row_updates(db, lid, 'person', _emails([ada, bo]))
        turn.record(db, 'c1', lid, landed, EMAIL)

    draft = psycopg.connect(settings.database_url, row_factory=dict_row)
    row_charges.finalize(draft, [ada])
    out: dict = {}

    def close() -> None:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            _settle_hold(conn, turn.tab.hold.id)
        out['closed'] = True

    thread = threading.Thread(target=close)
    thread.start()
    _wait_for_a_lock(db, 'the close')
    draft.commit()
    thread.join()
    assert out == {'closed': True}

    closing = psycopg.connect(settings.database_url, row_factory=dict_row)
    _, other, other_lid, (cy,) = _turn(db, rows=1)
    _require(db, other_lid, 'title')
    with db.transaction():
        other.record(db, 'c1', other_lid, apply_row_updates(db, other_lid, 'person', _emails([cy])), EMAIL)
    assert _settle_hold(closing, other.tab.hold.id) == 5

    def enroll() -> None:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            row_charges.finalize(conn, [cy])
        out['enrolled'] = True

    thread = threading.Thread(target=enroll)
    thread.start()
    _wait_for_a_lock(db, 'the draft')
    closing.commit()
    closing.close()
    thread.join()
    draft.close()

    assert out == {'closed': True, 'enrolled': True}
    assert _balance(db, uid) == 90
    assert [r['state'] for r in db.execute('SELECT state FROM row_charges')] == ['charged'] * 3


def test_a_delivery_and_a_growth_of_one_turn_lock_its_hold_before_its_job(db, monkeypatch):
    """``_deliver`` writes the job (``jobs.list_id``) and charges on the hold; a growth locks the hold
    and writes the job (``jobs.reservation_id``). Both take the hold first, so the growth waits for the delivery's
    commit instead of a deadlock."""
    from app.agent import loop
    from app.agent.tools.registry import ToolResult
    from app.agent.units import PricedCall

    uid, credits_, lid, (ada, _) = _turn(db, credits=1000)
    job = db.execute(
        'SELECT id, organization_id, user_id, chat_session_id FROM jobs WHERE id = %s', (credits_.job_id,)
    ).fetchone()
    turn = loop.Turn(job, None, lambda event, data: None)  # type: ignore[arg-type]  # no model call
    turn.credits, turn.current_list = credits_, lid
    tool = paid_emails('fake_paid', credits=5)
    priced = PricedCall.of(ToolCall('c1', tool.name, {'row_ids': [ada]}), tool, credits_.session_id)
    stored, go = threading.Event(), threading.Event()
    store = loop.deliver

    def paused(*args):  # the job and the hold are locked; the charge comes next
        stored.set()
        go.wait(10)
        return store(*args)

    monkeypatch.setattr(loop, 'deliver', paused)
    growth = TurnCredits(credits_.job_id, uid, credits_.session_id)
    growth.tab = dataclasses.replace(credits_.tab)
    out: dict = {}

    def run(name, fn) -> None:
        try:
            out[name] = fn()
        except Exception as e:  # a deadlock aborts one of the two
            out[name] = e

    delivery = threading.Thread(
        target=for_another_thread().run,
        args=(
            run,
            'delivered',
            lambda: turn._deliver(
                priced,
                ToolResult(
                    ok=True,
                    entity='person',
                    row_updates=_emails([ada]),
                    outcome=searched('email', _emails([ada])),
                ),
            ),
        ),
    )
    delivery.start()
    assert stored.wait(10)
    growing = threading.Thread(target=for_another_thread().run, args=(run, 'grown', lambda: growth.reserve(150)))
    growing.start()
    _wait_for_a_lock(db, 'the growth')
    go.set()
    delivery.join()
    growing.join()

    assert (out['delivered'][1], out['grown']) == (5, True)
    job_row = db.execute('SELECT credits_spent, credits_reserved FROM jobs WHERE id = %s', (credits_.job_id,))
    assert job_row.fetchone() == {'credits_spent': 5, 'credits_reserved': 250}
    assert _balance(db, uid) == 750
