"""#962: a hold open at a renewal is not spent, and its refund in the next period is not new allowance, so
expiry removes exactly the unused allowance and never purchased credits. Each case runs beside a control
that has no hold: both must end with the same wallet."""

import threading

import psycopg
import pytest
from psycopg.rows import dict_row

from app.billing.plan_period import expire_plan_credits, period
from tests.conftest import conninfo_of, wait_for_a_lock
from tests.test_billing_admin_sql import _balance, _invoice_event, _org, _post_event, _spend
from tests.test_billing_admin_sql import db as db  # the throwaway database fixture
from tests.test_billing_plan_change_sql import _subscribed
from tests.test_billing_plan_change_sql import plans as plans  # the billing plans fixture


def _buy(c, uid, n):
    c.execute("SELECT increment_credits(%s, %s, %s, 'purchase', 'stripe_checkout', 'cs_x')", (_org(c, uid), uid, n))


def _hold(c, uid, n) -> str:
    return str(c.execute('SELECT reserve_credits(%s, %s, %s) AS id', (_org(c, uid), uid, n)).fetchone()['id'])


def _renew(n):
    assert _post_event(_invoice_event(f'evt_r{n}', f'in_r{n}', price='price_starter')).status_code == 200


def _close(c, uid, hold, how):
    """Release the hold, or keep 10 of it; with no hold (the control) the 10 are spent directly."""
    if how == 'slack':
        if hold:
            c.execute('SELECT settle_reservation_with_slack(%s, 90)', (hold,))
        else:
            _spend(c, uid, 10)
    elif hold:
        c.execute('SELECT release_reservation(%s)', (hold,))


@pytest.mark.parametrize('close', ['release', 'slack'])
@pytest.mark.parametrize('held', [0, 100], ids=['control', 'hold'])
def test_a_hold_across_a_renewal_leaves_the_wallet_a_no_hold_run_has(db, plans, held, close):
    uid = _subscribed(db)
    _buy(db, uid, 200)  # 500 allowance + 250 purchased
    hold = _hold(db, uid, held) if held else None
    _renew(1)  # the allowance expires in full and is granted again
    _close(db, uid, hold, close)
    # The 10 a hold spent came out of the allowance that expired; a spend after the renewal comes out of the new one.
    assert _balance(db, uid) == (740 if close == 'slack' and not held else 750)


@pytest.mark.parametrize('close', ['release', 'slack'])
@pytest.mark.parametrize('held', [0, 100], ids=['control', 'hold'])
def test_held_purchased_credits_survive_the_next_renewal(db, plans, held, close):
    uid = _subscribed(db)
    _spend(db, uid, 500)  # the allowance is gone: 50 signup credits remain
    _buy(db, uid, 200)
    hold = _hold(db, uid, held) if held else None
    _renew(1)
    _close(db, uid, hold, close)
    before = _balance(db, uid)
    assert period(db, _org(db, uid), lock=False)['unused'] == (490 if close == 'slack' else 500)
    _renew(2)  # expires the unused allowance and grants 500: the purchased credits are untouched
    assert before == (740 if close == 'slack' else 750)
    assert _balance(db, uid) == 750  # 250 purchased + the new allowance, whatever the 10 spent cost


@pytest.mark.parametrize(
    ('held', 'close'), [(0, 'release'), (100, 'release'), (100, 'slack')], ids=['control', 'release', 'slack']
)
def test_allowance_held_beyond_the_wallet_expires_when_the_hold_returns(db, plans, held, close):
    uid = _subscribed(db)  # 500 allowance + 50 purchased; a hold of 100 takes 50 of the allowance with it
    hold = _hold(db, uid, held) if held else None
    _renew(1)  # the wallet holds 450: the 50 left in the hold cannot expire yet
    _close(db, uid, hold, close)
    assert _balance(db, uid) == 550  # 50 purchased + the new 500, whatever came back
    assert period(db, _org(db, uid), lock=False)['unused'] == 500
    _renew(2)
    assert _balance(db, uid) == 550


@pytest.mark.parametrize('first', ['a', 'b'])
def test_two_holds_pay_their_debts_whatever_the_order_they_close_in(db, plans, first):
    uid = _subscribed(db)  # 500 allowance + 50 purchased, 50 held twice
    a, b = _hold(db, uid, 50), _hold(db, uid, 50)
    _renew(1)  # the 100 held expire first; the wallet gives up 400 and keeps the 50 purchased
    closers = {
        'a': lambda: db.execute('SELECT release_reservation(%s)', (a,)),
        'b': lambda: db.execute('SELECT settle_reservation_with_slack(%s, 0)', (b,)),
    }  # b spends all 50
    closers[first]()
    closers['b' if first == 'a' else 'a']()
    assert _balance(db, uid) == 550


def test_credits_a_hold_grows_after_the_renewal_are_not_old_allowance(db, plans):
    uid = _subscribed(db)
    hold = _hold(db, uid, 100)
    _renew(1)
    db.execute('SELECT grow_reservation(%s, 50)', (hold,))
    db.execute('SELECT release_reservation(%s)', (hold,))
    assert _balance(db, uid) == 550
    assert period(db, _org(db, uid), lock=False)['unused'] == 500


def test_a_renewal_waits_for_a_hold_that_is_closing_instead_of_deadlocking(db, plans):
    uid = _subscribed(db)
    hold = _hold(db, uid, 100)
    payer = {'organization_id': _org(db, uid), 'user_id': uid}
    closing = psycopg.connect(db.info.dsn, password=db.info.password, row_factory=dict_row)
    renewing = psycopg.connect(db.info.dsn, password=db.info.password, row_factory=dict_row)
    errors = []

    def renew():
        try:
            expire_plan_credits(renewing, payer, 'in_deadlock')
            renewing.commit()
        except psycopg.Error as e:
            errors.append(e)

    try:
        closing.execute('SELECT 1 FROM credit_transactions WHERE id = %s FOR UPDATE', (hold,))
        thread = threading.Thread(target=renew)
        thread.start()
        wait_for_a_lock(conninfo_of(db), 'the renewal', renewing.info.backend_pid)
        closing.execute('SELECT release_reservation(%s)', (hold,))
        closing.commit()
        thread.join(10)
    finally:
        closing.close()
        renewing.close()
    assert not errors


def test_a_hold_opened_during_the_expiry_fails_it_instead_of_expiring_nothing(db, plans, monkeypatch):
    uid = _subscribed(db)
    _hold(db, uid, 100)
    payer = {'organization_id': _org(db, uid), 'user_id': uid}
    real = period

    def period_after_a_new_hold(conn, org_id, **kw):
        _hold(db, uid, 100)  # another connection reserves between the hold locks and the wallet lock
        return real(conn, org_id, **kw)

    monkeypatch.setattr('app.billing.plan_period.period', period_after_a_new_hold)
    renewing = psycopg.connect(db.info.dsn, password=db.info.password, row_factory=dict_row)
    try:
        with pytest.raises(RuntimeError, match='hold opened'):
            expire_plan_credits(renewing, payer, 'in_race')
    finally:
        renewing.rollback()
        renewing.close()
