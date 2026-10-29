"""One growing hold per chat turn: a call that needs more than the turn holds grows the turn's hold
(``grow_reservation``) under the project's hard monthly cap (#307), instead of taking a second hold."""

import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from app.agent.tools import registry
from app.agent.turn_credits import TurnCredits
from app.billing import credits
from app.billing.balance import NET_CREDITS_SQL
from app.billing.ledger import transactions
from app.billing.price import Price
from app.core.config import settings
from app.llm.base import ToolCall
from tests.conftest import act_in_own_org, call_as, member, project
from tests.test_agent import _balance, _job, _people, _run, _user, needs_db, paid_emails
from tests.test_agent import db as db
from tests.test_credit_callers_sql import _cap, _ledger

pytestmark = needs_db


def _payer(db, uid) -> credits.Payer:
    org = db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    return credits.Payer(str(org), uid, project(db, uid))


def _hold(db, uid, amount) -> credits.Hold:
    hold = credits.reserve(db, _payer(db, uid), amount, reference_type='job', reference_id=None, description='t')
    assert isinstance(hold, credits.Hold)
    return hold


def test_a_turn_grows_its_one_hold_and_settles_what_it_spent(db):
    uid = _user(db, credits=500)
    sid, jid = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn's worker runs as the job's member
    turn = TurnCredits(jid, uid, sid)
    assert turn.reserve() and turn.cover(240)
    job = db.execute('SELECT reservation_id::text, credits_reserved FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert job == {'reservation_id': turn.tab.hold.id, 'credits_reserved': 240}
    assert _ledger(db, uid) == [('reservation', 'reserved', 240, 'job')]
    assert (_balance(db, uid), turn.balance, turn.remaining) == (260, 260, 500)

    (row,) = _people(db, uid, sid, 1)
    lid = db.execute('SELECT list_id::text FROM list_rows WHERE id = %s', (row,)).fetchone()['list_id']
    with db.transaction():
        turn.spent = turn.record(db, 'c1', lid, [row], Price(30, (), bool))
    turn.finish()
    assert (_balance(db, uid), turn.spent) == (470, 30)
    assert _ledger(db, uid) == [
        ('deduction', 'committed', 240, 'job'),
        ('refund', 'committed', 210, 'reservation.slack'),
    ]


def test_the_project_cap_refuses_the_growth_and_changes_nothing(db):
    uid = _user(db, credits=1000)
    _cap(db, uid, 100)
    sid, jid = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn's worker runs as the job's member
    turn = TurnCredits(jid, uid, sid)
    assert turn.reserve() and turn.tab.hold.amount == 100
    before = (_ledger(db, uid), _balance(db, uid))

    assert turn.cover(240) is False and turn.capped
    assert (_ledger(db, uid), _balance(db, uid)) == before == ([('reservation', 'reserved', 100, 'job')], 900)
    assert db.execute('SELECT credits_reserved FROM jobs WHERE id = %s', (jid,)).fetchone()['credits_reserved'] == 100


async def test_a_paid_call_past_the_cap_room_fails_with_the_cap_message(db, monkeypatch):
    from app.agent import plan as plans

    monkeypatch.setattr(plans, 'LARGE_RUN_CREDITS', 1000)  # hold growth, not the plan gate (test_plan.py)
    monkeypatch.setitem(registry._REGISTRY, 'fake_bulk', paid_emails('fake_bulk', credits=3))
    uid = _user(db, credits=1000)
    _cap(db, uid, 100)
    sid, jid = _job(db, uid, 'x')
    rows = _people(db, uid, sid, 80)
    events, _ = await _run(jid, [('', [ToolCall('t1', 'fake_bulk', {'row_ids': rows})]), ('Sorry.', [])])
    result = dict(events)['tool_result']
    assert result['status'] == 'failed' and 'monthly credit cap' in result['result_preview']
    assert _balance(db, uid) == 1000
    assert _ledger(db, uid) == [('refund', 'committed', 100, 'job'), ('reservation', 'released', 100, 'job')]


@pytest.mark.parametrize('close', ['settle', 'release'])
def test_a_closed_hold_never_grows(db, close):
    uid = _user(db, credits=500)
    hold = _hold(db, uid, 100)
    if close == 'settle':
        db.execute('SELECT settle_reservation_with_slack(%s, %s)', (hold.id, hold.amount - 30))
    else:
        db.execute('SELECT release_reservation(%s)', (hold.id,))
    before = (_ledger(db, uid), _balance(db, uid))
    assert db.execute('SELECT grow_reservation(%s, 50) AS a', (hold.id,)).fetchone()['a'] is None
    assert credits.grow(db, _payer(db, uid), hold, 50, need=50) == credits.Refused(capped=False)
    assert (_ledger(db, uid), _balance(db, uid)) == before


def _last_month(db, uid, cap: int) -> tuple[str, credits.Hold]:
    pid = project(db, uid)
    db.execute("UPDATE projects SET credit_cap = %s, credit_cap_mode = 'hard' WHERE id = %s", (cap, pid))
    hold = _hold(db, uid, 100)
    db.execute(
        "UPDATE credit_transactions SET created_at = date_trunc('month', now()) - interval '1 minute' WHERE id = %s",
        (hold.id,),
    )
    return pid, hold


def _month_spend(db, pid) -> int:
    return db.execute('SELECT project_month_spend(%s) AS n', (pid,)).fetchone()['n']


def test_a_hold_taken_last_month_grows_into_this_months_cap_whole(db):
    uid = _user(db, credits=500)
    pid, hold = _last_month(db, uid, cap=150)
    assert credits.grow(db, _payer(db, uid), hold, 50, need=50) == credits.Hold(hold.id, 150)
    assert (_month_spend(db, pid), _balance(db, uid)) == (150, 350)
    taken = db.execute(
        "SELECT created_at = date_trunc('month', now()) AS moved FROM credit_transactions WHERE id = %s", (hold.id,)
    ).fetchone()
    assert taken['moved']


def test_a_hold_taken_last_month_asks_only_for_the_room_its_move_leaves(db):
    uid = _user(db, credits=500)
    pid, hold = _last_month(db, uid, cap=180)
    assert credits.grow(db, _payer(db, uid), hold, 100, need=30) == credits.Hold(hold.id, 180)
    assert _month_spend(db, pid) == 180


def test_a_hold_taken_last_month_never_grows_past_this_months_cap(db):
    uid = _user(db, credits=500)
    pid, hold = _last_month(db, uid, cap=140)
    assert db.execute('SELECT grow_reservation(%s, 50) AS a', (hold.id,)).fetchone()['a'] is None
    assert credits.grow(db, _payer(db, uid), hold, 50, need=50) == credits.Refused(capped=True)
    assert (_month_spend(db, pid), _balance(db, uid)) == (0, 400)


def test_a_grown_hold_shows_the_wallet_after_its_growth(db):
    uid = _user(db, credits=1000)
    other = _hold(db, uid, 900)
    hold = _hold(db, uid, 100)
    db.execute('SELECT release_reservation(%s)', (other.id,))
    assert credits.grow(db, _payer(db, uid), hold, 140, need=140) == credits.Hold(hold.id, 240)
    row = db.execute('SELECT amount, balance_after FROM credit_transactions WHERE id = %s', (hold.id,)).fetchone()
    assert (row['amount'], row['balance_after'], _balance(db, uid)) == (240, 760, 760)


def test_the_ledger_and_the_net_balance_read_a_grown_hold(db):
    uid = _user(db, credits=500)
    net = f'SELECT {NET_CREDITS_SQL.format(org="default_organization(%s)")} AS n'
    hold = _hold(db, uid, 100)
    assert db.execute(net, (uid,)).fetchone()['n'] == 500
    grown = credits.grow(db, _payer(db, uid), hold, 140, need=140)
    assert grown == credits.Hold(hold.id, 240)
    assert db.execute(net, (uid,)).fetchone()['n'] == 500
    (item,) = call_as(member(db, uid), transactions, limit=50, offset=0, user=member(db, uid))['items']
    assert (str(item['id']), item['type'], item['status'], item['amount'], item['balance_after']) == (
        hold.id,
        'reservation',
        'reserved',
        240,
        260,
    )


def test_two_holds_of_one_project_never_grow_past_its_cap_together(db):
    """Hold A grows by 50 and keeps the project lock until its commit; hold B asks for 50 meanwhile, rechecks
    under the lock and grows by the 20 the cap leaves."""
    uid = _user(db, credits=1000)
    pid = project(db, uid)
    db.execute("UPDATE projects SET credit_cap = 170, credit_cap_mode = 'hard' WHERE id = %s", (pid,))
    payer = _payer(db, uid)
    a, b = _hold(db, uid, 50), _hold(db, uid, 50)
    other = psycopg.connect(settings.database_url, row_factory=dict_row)
    assert credits.grow(other, payer, a, 50, need=50) == credits.Hold(a.id, 100)
    out = {}

    def step():
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            out['b'] = credits.grow(conn, payer, b, 50, need=5)

    thread = threading.Thread(target=step)
    thread.start()
    deadline = time.monotonic() + 10
    while not db.execute(  # B waits on the project's lock that A's growth keeps until its commit
        "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone():
        assert time.monotonic() < deadline, 'the growth of B never waited on the project lock'
        time.sleep(0.01)
    other.commit()
    other.close()
    thread.join()
    assert out['b'] == credits.Hold(b.id, 70)
    assert db.execute('SELECT project_month_spend(%s) AS n', (pid,)).fetchone()['n'] == 170
    assert _balance(db, uid) == 830
