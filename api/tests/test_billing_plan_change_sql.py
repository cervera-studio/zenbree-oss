"""#417: an upgrade charges the full price difference on any day of the period and keeps the renewal date;
a downgrade waits for the renewal. Stripe is stubbed; the credits go through the real webhook."""

import pytest
import stripe

import app.billing.plan_change
from app.billing.plan_period import period
from app.core.config import Plan, settings
from tests.test_billing_admin_sql import (
    _auth,
    _balance,
    _customer,
    _invoice_event,
    _join,
    _org,
    _post_event,
    _spend,
    _subscription_event,
    _upgrade_event,
    _user,
    client,
)
from tests.test_billing_admin_sql import db as db  # the throwaway database fixture

PERIOD_START = 1_790_000_000


@pytest.fixture
def plans(monkeypatch):
    monkeypatch.setattr(
        settings,
        'billing_plans',
        [
            Plan(id='starter', credits=500, price_eur_cents=2900, stripe_price_id='price_starter'),
            Plan(id='pro', credits=1500, price_eur_cents=7900, stripe_price_id='price_pro'),
        ],
    )


def _plan_left(c, uid) -> int:
    current = period(c, _org(c, uid), lock=False)
    assert current is not None
    return current['unused']


def _subscribed(c, plan='starter', used=0) -> str:
    """An organization in the first period of ``plan`` that spent ``used`` plan credits (50 signup credits stay)."""
    uid = _user(c)
    _customer(c, uid)
    created = _invoice_event('evt_0', 'in_0', reason='subscription_create', price=f'price_{plan}')
    assert _post_event(created).status_code == 200
    c.execute(
        'INSERT INTO subscriptions (stripe_subscription_id, organization_id, user_id, plan_id, status,'
        " current_period_end, event_created) VALUES ('sub_1', %s, %s, %s, 'active', now() + interval '15 days', 1)",
        (_org(c, uid), uid, plan),
    )
    if used:
        _spend(c, uid, used)
    return uid


# ── Credits: the three examples of the issue ───────────────────────────────


@pytest.mark.parametrize('used', [0, 200, 500])
def test_a_renewal_without_upgrade_grants_the_plan_allowance_whatever_was_used(db, plans, used):
    uid = _subscribed(db, used=used)
    assert _post_event(_invoice_event('evt_r', 'in_r', price='price_starter')).status_code == 200
    assert (_plan_left(db, uid), _balance(db, uid)) == (500, 550)


def test_an_upgrade_tops_the_period_up_to_pro_minus_what_was_used_and_grants_once(db, plans):
    uid = _subscribed(db, used=200)
    assert _post_event(_upgrade_event('evt_u', 'in_u')).status_code == 200
    assert _post_event(_upgrade_event('evt_u2', 'in_u')).status_code == 200  # the same invoice delivered again
    assert (_plan_left(db, uid), _balance(db, uid)) == (1300, 1350)  # purchased 50 unchanged
    row = db.execute("SELECT pack_id, credits, amount_cents FROM purchases WHERE stripe_invoice_id = 'in_u'").fetchone()
    assert (row['pack_id'], row['credits']) == ('plan:pro', 1000)


@pytest.mark.parametrize('spent_after_upgrade', [0, 1300])
def test_the_renewal_after_an_upgrade_expires_the_leftover_and_grants_pro(db, plans, spent_after_upgrade):
    uid = _subscribed(db, used=200)
    assert _post_event(_upgrade_event('evt_u', 'in_u')).status_code == 200
    if spent_after_upgrade:
        _spend(db, uid, spent_after_upgrade)
    assert _post_event(_invoice_event('evt_r', 'in_r', price='price_pro')).status_code == 200
    assert (_plan_left(db, uid), _balance(db, uid)) == (1500, 1550)


def test_a_prorated_upgrade_grants_nothing(db, plans):
    """The exploit: the portal's prorated switch on the last day charged 1.66 € and granted the difference."""
    uid = _subscribed(db, used=200)
    assert _post_event(_upgrade_event('evt_u', 'in_u', amounts=(-97, 263))).status_code == 200
    assert (_plan_left(db, uid), _balance(db, uid)) == (300, 350)
    assert db.execute("SELECT 1 FROM purchases WHERE stripe_invoice_id = 'in_u'").fetchone() is None


# ── POST /v1/billing/plan ──────────────────────────────────────────────────


class FakeStripe:
    """The Stripe subscription ``sub_1`` on ``price``, and the calls the plan change makes."""

    def __init__(self, monkeypatch, price='price_starter', schedule=None, pending=False, fail=False, **sub):
        self.calls: list[tuple[str, dict]] = []
        self.price, self.schedule, self.pending, self.fail = price, schedule, pending, fail
        self.sub = {
            'status': 'active',
            'cancel_at_period_end': False,
            'cancel_at': None,
            'invoice': 'paid',
            'invoice_id': 'in_0',  # the period's invoice, credited by _subscribed
            'invoice_reason': 'subscription_create',
        } | sub
        sub_api, schedule_api = app.billing.plan_change.stripe.Subscription, stripe.SubscriptionSchedule
        monkeypatch.setattr(sub_api, 'retrieve', self.retrieve)
        monkeypatch.setattr(sub_api, 'modify', self.record('modify', self._subscription))
        monkeypatch.setattr(schedule_api, 'create', self.record('schedule_create', self._schedule))
        monkeypatch.setattr(schedule_api, 'modify', self.record('schedule_modify', self._schedule))
        monkeypatch.setattr(schedule_api, 'release', self.record('release', self._schedule))

    def record(self, name, result):
        def call(*args, **kw):
            if self.fail:
                raise stripe.CardError('declined', None, 'card_declined')
            self.calls.append((name, {k: v for k, v in kw.items() if k != 'api_key'}))
            return result()

        return call

    def retrieve(self, sub_id, **kw):
        return self._subscription()

    def _subscription(self):
        item = {'id': 'si_1', 'price': {'id': self.price}, 'current_period_start': PERIOD_START}
        invoice_id = 'in_u' if self.pending else self.sub['invoice_id']
        amounts = (-2900, 7900) if invoice_id == 'in_u' else (2900,)  # an upgrade credits the old plan back
        invoice = {
            'id': invoice_id,
            'object': 'invoice',
            'lines': {'object': 'list', 'data': [{'object': 'line_item', 'amount': a} for a in amounts]},
            'billing_reason': 'subscription_update' if self.pending else self.sub['invoice_reason'],
            'status': 'open' if self.pending else self.sub['invoice'],
            'hosted_invoice_url': 'https://pay.test/in_u',
        }
        values = {
            'id': 'sub_1',
            'object': 'subscription',
            'status': self.sub['status'],
            'cancel_at_period_end': self.sub['cancel_at_period_end'],
            'cancel_at': self.sub['cancel_at'],
            'items': {'object': 'list', 'data': [item]},
            'schedule': self.schedule,
            'pending_update': {'expires_at': 1} if self.pending else None,
            'latest_invoice': invoice,
        }
        return stripe.Subscription.construct_from(values, 'sk_test')

    def _schedule(self):
        phase = {'start_date': PERIOD_START, 'end_date': PERIOD_START + 30 * 86400}
        return stripe.SubscriptionSchedule.construct_from({'id': 'sub_sched_1', 'phases': [phase]}, 'sk_test')


def _change(c, uid, plan_id):
    return client.post('/v1/billing/plan', json={'plan_id': plan_id}, headers=_auth(c, uid))


def _scheduled(c) -> str | None:
    return c.execute("SELECT scheduled_plan_id FROM subscriptions WHERE stripe_subscription_id = 'sub_1'").fetchone()[
        'scheduled_plan_id'
    ]


def test_an_upgrade_prorates_from_the_period_start_and_applies_once_paid(db, plans, monkeypatch):
    uid = _subscribed(db)
    fake = FakeStripe(monkeypatch)
    r = _change(db, uid, 'pro')
    assert r.status_code == 200 and r.json() == {'result': 'upgraded', 'payment_url': None}
    assert fake.calls == [
        (
            'modify',
            {
                'items': [{'id': 'si_1', 'price': 'price_pro'}],
                'proration_behavior': 'always_invoice',
                'proration_date': PERIOD_START,
                'payment_behavior': 'pending_if_incomplete',
                'expand': ['latest_invoice'],
            },
        )
    ]
    assert _balance(db, uid) == 550  # the credits come with the paid invoice, not with the call


def test_an_upgrade_whose_payment_needs_the_user_returns_stripes_page(db, plans, monkeypatch):
    uid = _subscribed(db)
    FakeStripe(monkeypatch, pending=True)
    assert _change(db, uid, 'pro').json() == {'result': 'payment_required', 'payment_url': 'https://pay.test/in_u'}


def test_a_downgrade_waits_for_the_renewal_and_grants_nothing_now(db, plans, monkeypatch):
    uid = _subscribed(db, plan='pro', used=300)
    fake = FakeStripe(monkeypatch, price='price_pro')
    assert _change(db, uid, 'starter').json() == {'result': 'downgrade_scheduled', 'payment_url': None}
    assert [name for name, _ in fake.calls] == ['schedule_create', 'schedule_modify']
    phases = fake.calls[1][1]['phases']
    assert [p['items'][0]['price'] for p in phases] == ['price_pro', 'price_starter']
    assert phases[0]['end_date'] == PERIOD_START + 30 * 86400  # the renewal date
    assert fake.calls[1][1]['end_behavior'] == 'release'
    assert (_plan_left(db, uid), _balance(db, uid)) == (1200, 1250)  # the period keeps what it paid for
    sub = client.get('/v1/billing/subscription', headers=_auth(db, uid)).json()['subscription']
    assert (sub['plan_id'], sub['scheduled_plan_id']) == ('pro', 'starter')
    # Stripe confirms the schedule (the plan is still pro): the downgrade stays shown
    assert _post_event(_scheduled_event('evt_s1', 2, price='price_pro')).status_code == 200
    assert _scheduled(db) == 'starter'
    # the renewal: starter's allowance, the leftover gone, and the downgrade done
    assert _post_event(_invoice_event('evt_r', 'in_r', price='price_starter')).status_code == 200
    assert _post_event(_scheduled_event('evt_s2', 3, price='price_starter')).status_code == 200
    assert (_plan_left(db, uid), _balance(db, uid)) == (500, 550)
    assert _scheduled(db) is None


def _scheduled_event(evt, created, price):
    event = _subscription_event(evt, created, price=price)
    event['data']['object']['schedule'] = 'sub_sched_1'
    return event


def test_a_schedule_released_outside_the_app_clears_the_shown_downgrade(db, plans):
    _subscribed(db, plan='pro')
    db.execute("UPDATE subscriptions SET scheduled_plan_id = 'starter'")
    assert _post_event(_subscription_event('evt_s1', 2, price='price_pro')).status_code == 200  # no schedule
    assert _scheduled(db) is None


def test_choosing_the_current_plan_cancels_a_scheduled_downgrade(db, plans, monkeypatch):
    uid = _subscribed(db, plan='pro')
    db.execute("UPDATE subscriptions SET scheduled_plan_id = 'starter'")
    fake = FakeStripe(monkeypatch, price='price_pro', schedule='sub_sched_1')
    assert _change(db, uid, 'pro').json() == {'result': 'downgrade_canceled', 'payment_url': None}
    assert [name for name, _ in fake.calls] == ['release']
    assert _scheduled(db) is None


def test_an_upgrade_replaces_a_scheduled_downgrade(db, plans, monkeypatch):
    """A starter organization whose earlier pro → starter downgrade is still attached."""
    uid = _subscribed(db)
    fake = FakeStripe(monkeypatch, schedule='sub_sched_1')
    assert _change(db, uid, 'pro').json()['result'] == 'upgraded'
    assert [name for name, _ in fake.calls] == ['release', 'modify']


@pytest.mark.parametrize(
    ('stripe_state', 'plan_id', 'status', 'code'),
    [
        ({'status': 'past_due'}, 'pro', 409, 'BILLING.PLAN_CHANGE_UNAVAILABLE'),
        ({'cancel_at_period_end': True}, 'pro', 409, 'BILLING.PLAN_CHANGE_UNAVAILABLE'),
        ({'cancel_at': PERIOD_START + 30 * 86400}, 'starter', 409, 'BILLING.PLAN_CHANGE_UNAVAILABLE'),  # flexible
        ({'invoice': 'draft'}, 'pro', 409, 'BILLING.PLAN_CHANGE_UNAVAILABLE'),  # the renewal's first hour
        ({'invoice': 'open'}, 'pro', 409, 'BILLING.PLAN_CHANGE_UNAVAILABLE'),
        # a renewal Stripe was paid for, whose webhook is late here
        ({'invoice_id': 'in_r', 'invoice_reason': 'subscription_cycle'}, 'pro', 409, 'BILLING.PLAN_CHANGE_UNAVAILABLE'),
        # the same, when Stripe bills a scheduled downgrade's renewal as an update
        (
            {'invoice_id': 'in_r', 'invoice_reason': 'subscription_update'},
            'pro',
            409,
            'BILLING.PLAN_CHANGE_UNAVAILABLE',
        ),
        ({}, 'starter', 409, 'BILLING.SAME_PLAN'),
        ({}, 'gold', 404, 'BILLING.UNKNOWN_PLAN'),
    ],
)
def test_a_change_that_cannot_happen_now_is_refused_and_changes_nothing(
    db, plans, monkeypatch, stripe_state, plan_id, status, code
):
    uid = _subscribed(db, plan='pro' if 'cancel_at' in stripe_state else 'starter')
    fake = FakeStripe(
        monkeypatch, price='price_pro' if 'cancel_at' in stripe_state else 'price_starter', **stripe_state
    )
    r = _change(db, uid, plan_id)
    assert (r.status_code, r.json()['detail']['code']) == (status, code)
    assert fake.calls == []


def test_an_organization_without_a_live_subscription_is_told_to_subscribe(db, plans, monkeypatch):
    uid = _subscribed(db)
    db.execute("UPDATE subscriptions SET status = 'canceled'")
    fake = FakeStripe(monkeypatch)
    r = _change(db, uid, 'pro')
    assert (r.status_code, r.json()['detail']['code']) == (409, 'BILLING.NO_SUBSCRIPTION')
    assert fake.calls == []


def test_an_upgrade_after_an_expired_payment_starts_again(db, plans, monkeypatch):
    """Stripe voids the invoice of a pending upgrade that expired: a new upgrade is allowed."""
    uid = _subscribed(db)
    FakeStripe(monkeypatch, invoice='void', invoice_id='in_u', invoice_reason='subscription_update')
    assert _change(db, uid, 'pro').json()['result'] == 'upgraded'


def test_a_stripe_failure_is_a_502_and_changes_nothing(db, plans, monkeypatch):
    uid = _subscribed(db, plan='pro')
    FakeStripe(monkeypatch, price='price_pro', fail=True)
    r = _change(db, uid, 'starter')
    assert (r.status_code, r.json()['detail']['code']) == (502, 'BILLING.PLAN_CHANGE_FAILED')
    assert _scheduled(db) is None


def test_a_member_cannot_change_the_plan(db, plans, monkeypatch):
    admin, member = _subscribed(db), _user(db)
    org = _org(db, admin)
    _join(db, org, member)
    fake = FakeStripe(monkeypatch)
    assert _change(db, member, 'pro').status_code == 403
    assert fake.calls == []


def test_a_second_upgrade_invoice_in_the_same_period_grants_nothing(db, plans):
    uid = _subscribed(db, used=200)
    assert _post_event(_upgrade_event('evt_u', 'in_u')).status_code == 200
    assert _post_event(_upgrade_event('evt_u2', 'in_u2')).status_code == 200  # a second paid upgrade invoice
    assert (_plan_left(db, uid), _balance(db, uid)) == (1300, 1350)
    assert db.execute("SELECT 1 FROM purchases WHERE stripe_invoice_id = 'in_u2'").fetchone() is None


def test_a_renewal_into_a_scheduled_downgrade_opens_the_period_whatever_stripe_calls_it(db, plans):
    """The schedule's phase change may bill as ``subscription_update``: one starter line, no credit for pro."""
    uid = _subscribed(db, plan='pro', used=300)
    renewal = _invoice_event('evt_r', 'in_r', reason='subscription_update', price='price_starter')
    renewal['data']['object']['lines']['data'][0]['amount'] = 2900
    assert _post_event(renewal).status_code == 200
    assert (_plan_left(db, uid), _balance(db, uid)) == (500, 550)


def test_a_click_while_the_upgrade_waits_for_payment_returns_the_same_invoice(db, plans, monkeypatch):
    uid = _subscribed(db)
    fake = FakeStripe(monkeypatch, pending=True)
    assert _change(db, uid, 'pro').json() == {'result': 'payment_required', 'payment_url': 'https://pay.test/in_u'}
    assert fake.calls == []  # no second invoice
