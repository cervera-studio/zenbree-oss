"""Webhook idempotency, admin credits and costs math against a real Postgres.
Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import app.admin.chats
import app.admin.costs
import app.admin.jobs
import app.admin.routes
import app.admin.settings
import app.billing.auto_topup
import app.billing.credit_split
import app.billing.customer_email
import app.billing.ledger
import app.billing.plan_change
import app.billing.profile
import app.billing.routes
import app.billing.subscription
import app.lists.ai_columns
import app.main
import app.notifications.service
import app.worker
from app.auth import middleware
from app.billing.credits import Hold, Payer, Refused
from app.core import db as core_db
from app.core import scope as scopes
from app.core.config import CreditPack, Plan, settings
from app.main import app as api
from tests.conftest import act_in_own_org, call_as, call_for, migrated_database, pool_on, project
from tests.test_auth import _FakeJWKS, _token
from tests.test_billing_admin import SECRET, sign

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: billing/admin SQL tests NOT run')

client = TestClient(api)
ADMIN_ID = str(uuid.uuid4())
ADMIN_ORG: dict[str, str] = {}  # the admin's organization in the current test's database


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo):
        # The app's own checkout, on the pool that pool_on points at this database below: each one enters the
        # request's scope. Set on each module, since conftest fakes some of them (app.admin.settings).
        get_conn = core_db.get_conn
        for mod in (
            app.billing.routes,
            app.billing.subscription,
            app.billing.plan_change,
            app.billing.profile,
            app.billing.auto_topup,
            app.billing.credit_split,
            app.billing.customer_email,
            app.billing.ledger,
            app.admin.costs,
            app.admin.routes,
            app.admin.chats,
            app.admin.jobs,
            app.admin.settings,
            app.notifications.service,
            app.worker,
            app.lists.ai_columns,
            app.main,
        ):
            monkeypatch.setattr(mod, 'get_conn', get_conn)
        monkeypatch.setattr(settings, 'stripe_webhook_secret', SECRET)
        # Only the key: current_user reads the membership, and require_admin the role, in this database.
        monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())
        _user(c, ADMIN_ID)
        c.execute('UPDATE "user" SET role = %s WHERE id = %s', ('admin', ADMIN_ID))
        ADMIN_ORG['id'] = _org(c, ADMIN_ID)
        app.admin.settings.invalidate()
        with pool_on(conninfo):
            yield c


def _user(c, uid=None) -> str:
    uid = uid or str(uuid.uuid4())
    now = datetime.now(UTC)
    c.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt") VALUES (%s, %s, %s, false, %s, %s)',
        (uid, 'T', f'{uid}@x.test', now, now),
    )
    c.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (default_organization(%s), %s, 50)',
        (uid, uid),
    )
    return uid


def _balance(c, uid) -> int:
    """The balance of the wallet the user opened at signup: their own organization's (#306)."""
    return c.execute('SELECT credits_remaining FROM user_credits WHERE user_id = %s', (uid,)).fetchone()[
        'credits_remaining'
    ]


def _org(c, uid) -> str:
    return str(c.execute('SELECT default_organization(%s) AS id', (uid,)).fetchone()['id'])


def _wallet(c, org) -> int:
    return c.execute('SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (org,)).fetchone()[
        'credits_remaining'
    ]


def _auth(c, uid) -> dict:
    return {'Authorization': f'Bearer {_token(sub=uid, org=_org(c, uid))}'}


def _join(c, org, uid, role='member') -> None:
    """``uid`` leaves their own organization for ``org`` (what #308's invites will do)."""
    c.execute('DELETE FROM member WHERE "userId" = %s', (uid,))
    c.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, uid, role),
    )


def _post_event(event: dict):
    payload = json.dumps(event).encode()
    return client.post('/v1/billing/stripe-webhook', content=payload, headers={'stripe-signature': sign(payload)})


def _paid_event(uid, evt='evt_1', session='cs_1'):
    return {
        'id': evt,
        'object': 'event',
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'id': session,
                'payment_status': 'paid',
                'client_reference_id': uid,
                'payment_intent': 'pi_1',
                'amount_total': 1900,
                'currency': 'eur',
                'metadata': {'user_id': uid, 'pack_id': 'starter', 'credits': '1000'},
            }
        },
    }


def _admin():
    """The back office's admin, whom the ``db`` fixture creates, in their own organization."""
    return {'Authorization': f'Bearer {_token(role="admin", sub=ADMIN_ID, org=ADMIN_ORG["id"])}'}


def test_webhook_replay_credits_once(db):
    uid = _user(db)
    for _ in range(2):
        r = _post_event(_paid_event(uid))
        assert r.status_code == 200, r.text
    assert r.json()['result'] == 'duplicate'
    # a second event for the same session doesn't pay twice either
    ev = _paid_event(uid, evt='evt_2')
    ev['type'] = 'checkout.session.async_payment_succeeded'
    assert _post_event(ev).status_code == 200
    assert _balance(db, uid) == 1050
    assert db.execute("SELECT count(*) AS n FROM credit_transactions WHERE type = 'purchase'").fetchone()['n'] == 1
    assert db.execute("SELECT count(*) AS n FROM webhook_events WHERE status = 'processed'").fetchone()['n'] == 2


def test_webhook_handler_failure_is_500_and_logged(db):
    ev = _paid_event(_user(db))
    ev['data']['object']['metadata']['organization_id'] = str(uuid.uuid4())  # FK violation inside the handler
    r = _post_event(ev)
    assert r.status_code == 500
    row = db.execute("SELECT status, error_message FROM webhook_events WHERE stripe_event_id = 'evt_1'").fetchone()
    assert row['status'] == 'failed' and row['error_message']


def test_topup_revenue_excludes_vat(db):
    uid = _user(db)
    ev = _paid_event(uid)
    ev['data']['object'].update(amount_total=2318, total_details={'amount_tax': 418})  # €19 + 22% IVA
    assert _post_event(ev).status_code == 200
    assert db.execute('SELECT amount_cents FROM purchases').fetchone()['amount_cents'] == 1900


# ── Subscriptions ──────────────────────────────────────────────────────────


@pytest.fixture
def plans(monkeypatch):
    monkeypatch.setattr(
        settings,
        'billing_plans',
        [
            Plan(id='starter', credits=2000, price_eur_cents=2900, stripe_price_id='price_starter'),
            Plan(id='pro', credits=6000, price_eur_cents=7900, stripe_price_id='price_pro'),
        ],
    )


def _customer(c, uid, cus='cus_1'):
    c.execute(
        'INSERT INTO billing_customers (organization_id, user_id, stripe_customer_id)'
        ' VALUES (default_organization(%s), %s, %s)',
        (uid, uid, cus),
    )


def _line(price, amount=7900):
    # API 2026-09-30 shape: the price sits under lines.data[].pricing.price_details
    return {'amount': amount, 'pricing': {'type': 'price_details', 'price_details': {'price': price}}}


def _invoice_event(evt, inv, reason='subscription_cycle', price='price_pro', cus='cus_1', lines=None):
    return {
        'id': evt,
        'object': 'event',
        'type': 'invoice.paid',
        'created': 1,
        'data': {
            'object': {
                'id': inv,
                'object': 'invoice',
                'customer': cus,
                'billing_reason': reason,
                'currency': 'eur',
                'total': 9638,
                'total_excluding_tax': 7900,
                'lines': {'data': lines or [_line(price)]},
            }
        },
    }


def _upgrade_event(evt, inv, old='price_starter', new='price_pro', amounts=(-2900, 7900)):
    # Plan change prorated from the period start (plan_change): the whole old price back, the whole new one charged
    return _invoice_event(
        evt, inv, reason='subscription_update', lines=[_line(old, amounts[0]), _line(new, amounts[1])]
    )


def _spend(c, uid, n):
    sql = "SELECT deduct_credits(default_organization(%s), %s, %s, 'tool', 'x') AS b"
    assert c.execute(sql, (uid, uid, n)).fetchone()['b'] >= 0


def _subscription_event(evt, created, status='active', price='price_pro', cancel=False, typ='updated', cancel_at=None):
    return {
        'id': evt,
        'object': 'event',
        'type': f'customer.subscription.{typ}',
        'created': created,
        'data': {
            'object': {
                'id': 'sub_1',
                'object': 'subscription',
                'customer': 'cus_1',
                'status': status,
                'cancel_at_period_end': cancel,
                'cancel_at': cancel_at,
                'items': {'data': [{'price': {'id': price}, 'current_period_end': 1800000000}]},
            }
        },
    }


def test_invoice_paid_credits_plan_once(db, plans):
    uid = _user(db)
    _customer(db, uid)
    # signup: the subscription Checkout itself credits nothing, the first invoice does
    session = _paid_event(uid, evt='evt_cs')
    session['data']['object'].update(mode='subscription', metadata={'user_id': uid, 'plan_id': 'pro'})
    assert _post_event(session).status_code == 200
    assert _balance(db, uid) == 50
    assert _post_event(_invoice_event('evt_1', 'in_1', reason='subscription_create')).status_code == 200
    assert (
        _post_event(_invoice_event('evt_1b', 'in_1', reason='subscription_create')).status_code == 200
    )  # same invoice
    assert _balance(db, uid) == 6050
    # unknown prices grant nothing (and don't fail the webhook)
    assert _post_event(_invoice_event('evt_4', 'in_4', price='price_other')).status_code == 200
    assert _balance(db, uid) == 6050
    rows = db.execute('SELECT stripe_invoice_id, pack_id, amount_cents FROM purchases').fetchall()
    assert [(r['stripe_invoice_id'], r['pack_id'], r['amount_cents']) for r in rows] == [('in_1', 'plan:pro', 7900)]


def test_renewal_resets_plan_credits_and_keeps_purchased(db, plans):
    uid = _user(db)  # 50 signup credits: not plan credits, they survive
    _customer(db, uid)
    assert _post_event(_invoice_event('evt_1', 'in_1', reason='subscription_create')).status_code == 200
    _spend(db, uid, 1000)  # from the allowance
    assert _post_event(_paid_event(uid, evt='evt_tu')).status_code == 200  # +1000 top-up, never expires
    assert _balance(db, uid) == 6050
    assert _post_event(_invoice_event('evt_2', 'in_2')).status_code == 200
    # 5000 unused allowance expired, then the new 6000: 50 + 1000 + 6000
    assert _balance(db, uid) == 7050
    expiry = db.execute("SELECT amount FROM credit_transactions WHERE reference_type = 'plan_expiry'").fetchall()
    assert [e['amount'] for e in expiry] == [5000]
    # the admin report doesn't count the expiry as usage
    today = datetime.now(UTC).date().isoformat()
    r = client.get(f'/v1/admin/costs?from={today}&to={today}', headers=_admin())
    assert r.json()['totals']['credits_spent'] == 1000

    # allowance fully used and then some: nothing left to expire, purchased credits pay the rest
    _spend(db, uid, 6500)
    assert _post_event(_invoice_event('evt_3', 'in_3')).status_code == 200
    assert _balance(db, uid) == 550 + 6000


def test_subscription_deleted_expires_allowance_once(db, plans):
    uid = _user(db)
    _customer(db, uid)
    assert _post_event(_invoice_event('evt_1', 'in_1', reason='subscription_create')).status_code == 200
    _spend(db, uid, 100)
    assert _post_event(_subscription_event('evt_2', 200, status='canceled', typ='deleted')).status_code == 200
    assert _balance(db, uid) == 50
    assert _post_event(_subscription_event('evt_3', 300, status='canceled', typ='deleted')).status_code == 200
    assert _balance(db, uid) == 50


def test_refund_finds_subscription_invoice(db, plans):
    uid = _user(db)
    _customer(db, uid)
    assert _post_event(_invoice_event('evt_1', 'in_1', reason='subscription_create')).status_code == 200
    assert (
        _post_event(
            {
                'id': 'evt_ip',
                'object': 'event',
                'type': 'invoice_payment.paid',
                'created': 1,
                'data': {
                    'object': {
                        'id': 'inpay_1',
                        'object': 'invoice_payment',
                        'invoice': 'in_1',
                        'payment': {'type': 'payment_intent', 'payment_intent': 'pi_sub'},
                    }
                },
            }
        ).status_code
        == 200
    )
    assert (
        _post_event(
            {
                'id': 'evt_r',
                'object': 'event',
                'type': 'charge.refunded',
                'created': 2,
                'data': {'object': {'payment_intent': 'pi_sub', 'amount_refunded': 7900}},
            }
        ).status_code
        == 200
    )
    assert (
        db.execute("SELECT refunded_cents FROM purchases WHERE stripe_invoice_id = 'in_1'").fetchone()['refunded_cents']
        == 7900
    )


def test_subscription_mirror_ignores_stale_events(db, plans):
    uid = _user(db)
    _customer(db, uid)
    assert _post_event(_subscription_event('evt_1', 100, typ='created')).status_code == 200
    assert _post_event(_subscription_event('evt_3', 300, price='price_starter', cancel=True)).status_code == 200
    assert _post_event(_subscription_event('evt_2', 200, status='past_due')).status_code == 200  # late delivery
    row = db.execute('SELECT plan_id, status, cancel_at_period_end, current_period_end FROM subscriptions').fetchone()
    assert (row['plan_id'], row['status'], row['cancel_at_period_end']) == ('starter', 'active', True)
    assert row['current_period_end'].year == 2027

    r = client.get('/v1/billing/subscription', headers=_auth(db, uid))
    assert r.status_code == 200 and r.json()['subscription']['plan_id'] == 'starter'
    assert r.json()['subscription']['cancel_at'].startswith('2027-01-15')  # cancel_at_period_end alone: the period end
    # #538: flexible billing mode, the portal's cancellation sets cancel_at only
    assert (
        _post_event(_subscription_event('evt_5', 350, price='price_starter', cancel_at=1800000000)).status_code == 200
    )
    r = client.get('/v1/billing/subscription', headers=_auth(db, uid))
    assert r.json()['subscription']['cancel_at'].startswith('2027-01-15')
    assert _post_event(_subscription_event('evt_4', 400, status='canceled', typ='deleted')).status_code == 200
    r = client.get('/v1/billing/subscription', headers=_auth(db, uid))
    assert r.json() == {'subscription': None}


def test_subscription_for_unknown_customer_is_skipped(db, plans):
    assert _post_event(_subscription_event('evt_1', 100)).status_code == 200
    assert db.execute('SELECT count(*) AS n FROM subscriptions').fetchone()['n'] == 0


def test_admin_credits_record_who(db):
    uid = _user(db)
    r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': 25, 'reason': 'goodwill'}, headers=_admin())
    assert r.status_code == 200 and r.json()['balance'] == 75
    r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': -70, 'reason': 'abuse'}, headers=_admin())
    assert r.json()['balance'] == 5
    r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': -10, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 409
    rows = db.execute(
        'SELECT type, reference_type, reference_id, description FROM credit_transactions '
        'WHERE user_id = %s ORDER BY created_at',
        (uid,),
    ).fetchall()
    assert [(r['type'], r['reference_id'], r['description']) for r in rows] == [
        ('adjustment', ADMIN_ID, 'goodwill'),
        ('deduction', ADMIN_ID, 'abuse'),
    ]
    assert (
        client.post(
            f'/v1/admin/users/{uuid.uuid4()}/credits', json={'amount': 1, 'reason': 'x'}, headers=_admin()
        ).status_code
        == 404
    )


def test_admin_credits_open_a_wallet_the_signup_hook_never_made(db):
    """The back office runs as System(BACK_OFFICE): increment_credits inserts the missing wallet as that role."""
    uid = _user(db)
    org = _org(db, uid)
    db.execute('DELETE FROM user_credits WHERE organization_id = %s', (org,))
    r = client.post(f'/v1/admin/organizations/{org}/credits', json={'amount': 25, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 200 and r.json()['balance'] == 25 and _wallet(db, org) == 25


def test_admin_credits_amount_is_bounded(db):
    """#393: past ±1e9 is a 422 before it reaches the INT credit functions; the limit itself goes through."""
    uid = _user(db)
    for amount in (1_000_000_001, -1_000_000_001, 2_147_483_648):
        r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': amount, 'reason': 'x'}, headers=_admin())
        assert r.status_code == 422, amount
    r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': 1_000_000_000, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 200 and r.json()['balance'] == 1_000_000_050
    r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': -1_000_000_000, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 200 and r.json()['balance'] == 50


def test_admin_organizations_list_detail_and_credits(db):
    """#327: a team's wallet, members, ledger and clients, and an adjustment on it with the same audit trail."""
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, org, bo)
    db.execute("UPDATE organization SET name = 'Agenzia Nord' WHERE id = %s", (org,))
    pid = project(db, ada)
    db.execute("UPDATE projects SET credit_cap = 300, credit_cap_mode = 'hard' WHERE id = %s", (pid,))
    db.execute(
        'INSERT INTO subscriptions (stripe_subscription_id, organization_id, user_id, plan_id, status, event_created)'
        " VALUES ('sub_1', %s, %s, 'pro', 'active', 1)",
        (org, ada),
    )
    found = client.get(
        '/v1/admin/organizations', params={'q': f'{bo}@x.test'}, headers=_admin()
    ).json()  # a member's email
    assert [(o['id'], o['name'], o['members'], o['balance'], o['plan_id']) for o in found['items']] == [
        (org, 'Agenzia Nord', 2, 50, 'pro')
    ] and found['total'] == 1
    assert client.get('/v1/admin/organizations', params={'q': 'nord'}, headers=_admin()).json()['total'] == 1
    assert client.get('/v1/admin/organizations', params={'q': 'nobody'}, headers=_admin()).json()['items'] == []

    r = client.post(
        f'/v1/admin/organizations/{org}/credits', json={'amount': 25, 'reason': 'goodwill'}, headers=_admin()
    )
    assert r.status_code == 200 and r.json()['balance'] == 75 and _wallet(db, org) == 75
    r = client.post(f'/v1/admin/organizations/{org}/credits', json={'amount': -80, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ADMIN.INSUFFICIENT_CREDITS'
    assert (
        client.post(
            f'/v1/admin/organizations/{org}/credits', json={'amount': 0, 'reason': 'x'}, headers=_admin()
        ).status_code
        == 422
    )
    missing = client.post(
        f'/v1/admin/organizations/{uuid.uuid4()}/credits', json={'amount': 1, 'reason': 'x'}, headers=_admin()
    )
    assert missing.status_code == 404

    d = client.get(f'/v1/admin/organizations/{org}', headers=_admin()).json()
    assert d['balance'] == 75 and [(m['user_id'], m['role']) for m in d['members']] == [(ada, 'admin'), (bo, 'member')]
    assert d['subscription']['plan_id'] == 'pro'
    (tx,) = d['ledger']
    assert (tx['type'], tx['reference_type'], tx['reference_id'], tx['description'], tx['email']) == (
        'adjustment',
        'admin',
        ADMIN_ID,
        'goodwill',
        f'{ada}@x.test',
    ) and d['ledger_total'] == 1
    assert [(p['id'], p['credit_cap'], p['credit_cap_mode'], p['credits_this_month']) for p in d['projects']] == [
        (pid, 300, 'hard', 0)
    ]
    assert client.get(f'/v1/admin/organizations/{uuid.uuid4()}', headers=_admin()).status_code == 404
    usage = client.get(f'/v1/admin/users/{bo}/usage', headers=_admin()).json()
    assert [(o['id'], o['name'], o['role']) for o in usage['organizations']] == [(org, 'Agenzia Nord', 'member')]
    assert client.get('/v1/admin/organizations', headers=_auth(db, ada)).status_code == 403  # an org admin is not one


def test_costs_math(db):
    a, b = _user(db), _user(db)
    db.execute(
        'INSERT INTO llm_usage (user_id, model, input_tokens, output_tokens, cost_usd) '
        "VALUES (%s, 'unpriced-model', 1, 1, 1.0), (%s, 'unpriced-model', 1, 1, 0.25)",
        (a, b),
    )
    db.execute(
        'INSERT INTO vendor_calls (user_id, vendor, operation, cost_usd, units) '
        "VALUES (%s, 'apollo', 'search', 0.5, 2), (NULL, 'exa', 'search', 0.1, 1)",
        (a,),
    )
    assert _post_event(_paid_event(a)).status_code == 200  # +1000 credits, €19
    rid = db.execute('SELECT reserve_credits(%s, %s, 10) AS r', (_org(db, a), a)).fetchone()['r']
    db.execute('SELECT settle_reservation_with_slack(%s, 4)', (rid,))  # spent 6
    client.post(f'/v1/admin/users/{a}/credits', json={'amount': -3, 'reason': 'x'}, headers=_admin())  # not usage
    _post_event(
        {
            'id': 'evt_r',
            'object': 'event',
            'type': 'charge.refunded',
            'data': {'object': {'payment_intent': 'pi_1', 'amount_refunded': 400}},
        }
    )

    today = datetime.now(UTC).date().isoformat()
    r = client.get(f'/v1/admin/costs?from={today}&to={today}', headers=_admin())
    assert r.status_code == 200, r.text
    body = r.json()
    rate = settings.eur_per_usd
    t = body['totals']
    assert t['llm_cost_usd'] == pytest.approx(1.25)
    assert t['vendor_cost_usd'] == pytest.approx(0.6)
    assert t['credits_spent'] == 6 and t['credits_purchased'] == 1000
    assert t['revenue_eur'] == 15.0  # 19 − 4 refunded
    assert t['margin_eur'] == pytest.approx(15.0 - 1.85 * rate, abs=1e-4)
    ua = next(u for u in body['users'] if u['user_id'] == a)
    assert ua['cost_usd'] == pytest.approx(1.5) and ua['email'] == f'{a}@x.test'
    assert any(u['user_id'] is None and u['vendor_cost_usd'] == pytest.approx(0.1) for u in body['users'])
    assert len(body['daily']) == 1 and body['daily'][0]['revenue_eur_cents'] == 1500
    assert {v['vendor']: v['units'] for v in body['by_vendor']} == {'apollo': 2, 'exa': 1}

    u = client.get(f'/v1/admin/users/{a}/usage', headers=_admin()).json()
    assert u['balance'] == 1050 - 6 - 3
    assert u['eur_per_usd'] == rate
    assert u['costs']['totals']['vendor_cost_usd'] == pytest.approx(0.5)  # user filter drops NULL-user exa
    assert u['purchases'][0]['refunded_cents'] == 400


def test_costs_hit_rate_per_contact_provider(db):
    a = _user(db)
    db.execute(
        'INSERT INTO contact_lookups (user_id, provider, channel, found) VALUES '
        "(%s, 'apollo', 'email', true), (%s, 'apollo', 'email', false), (%s, 'pdl', 'phone', false)",
        (a, a, a),
    )
    today = datetime.now(UTC).date().isoformat()
    body = client.get(f'/v1/admin/costs?from={today}&to={today}', headers=_admin()).json()
    assert body['by_provider'] == [
        {'provider': 'apollo', 'channel': 'email', 'lookups': 2, 'hits': 1},
        {'provider': 'pdl', 'channel': 'phone', 'lookups': 1, 'hits': 0},
    ]


def test_settings_kill_switch(db):
    from app.admin.settings import is_disabled, vendor_disabled_now

    act_in_own_org(db, _user(db))  # a tool reads the switch in its turn's scope
    assert is_disabled('vendor_disabled:apollo') is False
    r = client.put('/v1/admin/settings', json={'key': 'vendor_disabled:apollo', 'value': True}, headers=_admin())
    by_key = {s['key']: s for s in r.json()['settings']}
    assert r.status_code == 200 and by_key['vendor_disabled:apollo']['updated_by'] == ADMIN_ID
    assert is_disabled('vendor_disabled:apollo') is True
    assert vendor_disabled_now('apollo') is True
    client.put('/v1/admin/settings', json={'key': 'vendor_disabled:apollo', 'value': None}, headers=_admin())
    assert is_disabled('vendor_disabled:apollo') is False


def test_get_settings_lists_every_switch_with_who_set_it(db):
    db.execute("INSERT INTO settings (key, value) VALUES ('channel_disabled:email', 'true'::jsonb)")
    client.put('/v1/admin/settings', json={'key': 'vendor_disabled:apollo', 'value': True}, headers=_admin())
    r = client.get('/v1/admin/settings', headers=_admin())
    assert r.status_code == 200
    by_key = {s['key']: s for s in r.json()['settings']}
    assert by_key['channel_disabled:email']['updated_by'] is None
    assert by_key['vendor_disabled:apollo']['updated_by'] == ADMIN_ID
    assert by_key['vendor_disabled:apollo']['updated_by_name'] == 'T'


def test_settings_name_vendors_that_failed_today(db):
    """#352: failed vendor calls of the last day, per vendor, next to the kill switches."""
    db.execute(
        'INSERT INTO vendor_calls (vendor, operation, success, created_at) VALUES '
        "('hunter', 'x', false, now()), ('hunter', 'x', false, now()), ('hunter', 'x', true, now()),"
        "('apollo', 'x', false, now() - interval '2 days'), ('aiark', 'x', false, now())"
    )
    errors = client.get('/v1/admin/settings', headers=_admin()).json()['vendor_errors']
    assert [(e['vendor'], e['errors']) for e in errors] == [('hunter', 2), ('aiark', 1)]


def test_add_setting_refuses_existing_and_malformed_keys(db):
    from app.admin.settings import is_disabled

    act_in_own_org(db, _user(db))  # a tool reads the switch in its turn's scope
    client.put('/v1/admin/settings', json={'key': 'vendor_disabled:aiark', 'value': True}, headers=_admin())
    r = client.post('/v1/admin/settings', json={'key': 'vendor_disabled:aiark'}, headers=_admin())
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ADMIN.SETTING_EXISTS'
    assert is_disabled('vendor_disabled:aiark') is True  # still blocked
    r = client.post('/v1/admin/settings', json={'key': 'vendor_disable:apollo'}, headers=_admin())
    assert r.status_code == 422 and r.json()['detail']['code'] == 'ADMIN.INVALID_SETTING_KEY'
    r = client.post('/v1/admin/settings', json={'key': 'channel_disabled:email'}, headers=_admin())
    assert r.status_code == 200
    assert {'key': 'channel_disabled:email', 'value': False} in [
        {'key': s['key'], 'value': s['value']} for s in r.json()['settings']
    ]


def test_chat_sessions_and_jobs(db):
    have = {
        r['t']
        for r in db.execute(
            'SELECT table_name AS t FROM information_schema.tables WHERE table_schema = current_schema()'
        )
    }
    if not {'chat_sessions', 'chat_messages', 'jobs'} <= have:
        pytest.skip('chat_sessions/chat_messages/jobs come from schema_agent.sql (feature/m1-agent): not here yet')
    uid = _user(db)
    sid = db.execute(
        "INSERT INTO chat_sessions (organization_id, project_id, user_id, title) VALUES (default_organization(%s), %s, %s, 'hi') RETURNING id",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO chat_messages (session_id, user_id, role, content) VALUES (%s, %s, 'user', 'ciao')", (sid, uid)
    )
    r = client.get(f'/v1/admin/chat-sessions?user_id={uid}', headers=_admin()).json()
    assert r['items'][0]['message_count'] == 1
    r = client.get(f'/v1/admin/chat-sessions/{sid}', headers=_admin()).json()
    assert r['messages'][0]['content'] == 'ciao'
    assert client.get('/v1/admin/jobs', headers=_admin()).status_code == 200


def test_chat_session_summary_counts_credits_and_our_cost(db):
    uid, other = _user(db), _user(db)
    t0 = datetime.now(UTC) - timedelta(minutes=10)
    sid = db.execute(
        "INSERT INTO chat_sessions (organization_id, project_id, user_id, title) VALUES (default_organization(%s), %s, %s, 'x') RETURNING id",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO chat_messages (session_id, user_id, role, content, question, created_at) VALUES '
        "(%s, %s, 'user', 'trova CEO', NULL, %s), (%s, %s, 'assistant', '', %s, %s)",
        (
            sid,
            uid,
            t0,
            sid,
            uid,
            Jsonb({'question': 'Solo indipendenti?', 'options': [{'label': 'Sì', 'description': None}]}),
            t0 + timedelta(seconds=1),
        ),
    )
    db.execute(
        'INSERT INTO jobs (organization_id, user_id, chat_session_id, status, credits_spent, started_at, finished_at,'
        " created_at, result) VALUES (default_organization(%s), %s, %s, 'failed', 100, %s, %s, %s, %s)",
        (uid, uid, sid, t0, t0 + timedelta(minutes=5), t0, Jsonb({'refunded': 14, 'undelivered': 3})),
    )
    # Inside the job: counted. Before it, or another user's: not.
    db.execute(
        'INSERT INTO vendor_calls (user_id, vendor, operation, cost_usd, created_at) VALUES '
        "(%s, 'apollo', 'x', 0.5, %s), (%s, 'apollo', 'x', 9, %s), (%s, 'apollo', 'x', 7, %s)",
        (uid, t0 + timedelta(minutes=1), uid, t0 - timedelta(minutes=1), other, t0 + timedelta(minutes=1)),
    )
    db.execute(
        'INSERT INTO purchases (organization_id, user_id, stripe_session_id, pack_id, credits, amount_cents, currency) '
        "VALUES (default_organization(%s), %s, 'cs_1', 'p', 1000, 1600, 'eur')",
        (uid, uid),
    )
    r = client.get(f'/v1/admin/chat-sessions/{sid}', headers=_admin()).json()
    assert r['messages'][1]['question']['question'] == 'Solo indipendenti?'
    s = r['summary']
    assert s['credits_spent'] == 100 and s['vendor_cost_usd'] == pytest.approx(0.5)
    assert s['credits_returned'] == 14
    assert s['eur_per_1000_credits'] == 16 and s['revenue_eur'] == pytest.approx(1.6)
    assert s['margin_eur'] == pytest.approx(1.6 - 0.5 * settings.eur_per_usd, abs=1e-4)

    jobs = client.get(f'/v1/admin/jobs?user_id={uid}', headers=_admin()).json()
    assert jobs['counts'] == {'failed': 1} and jobs['items'][0]['brief'] == 'trova CEO'
    # job_id finds one job whatever its age, and its counts are that job's alone.
    jid = jobs['items'][0]['id']
    db.execute(
        "INSERT INTO jobs (organization_id, user_id, status) VALUES (default_organization(%s), %s, 'succeeded')",
        (uid, uid),
    )
    one = client.get(f'/v1/admin/jobs?job_id={jid}', headers=_admin()).json()
    assert [j['id'] for j in one['items']] == [jid] and one['counts'] == {'failed': 1}
    # #352: the rail counts what needs a look: failed or waiting on input, in the last day
    db.execute(
        "INSERT INTO jobs (organization_id, user_id, status) VALUES (default_organization(%s), %s, 'needs_input'),"
        " (default_organization(%s), %s, 'running'), (default_organization(%s), %s, 'failed') RETURNING id",
        (uid, uid, other, other, other, other),
    )
    db.execute(
        "UPDATE jobs SET created_at = now() - interval '2 days' WHERE user_id = %s AND status = 'failed'", (other,)
    )
    assert client.get('/v1/admin/badges', headers=_admin()).json()['jobs_attention'] == 2


def test_chat_sessions_outcome_and_credits(db):
    """#352: each chat of a user says what it ended with and what it cost."""
    uid = _user(db)
    pid = project(db, uid)

    def chat(*statuses, rows=None):
        sid = db.execute(
            'INSERT INTO chat_sessions (organization_id, project_id, user_id) '
            'VALUES (default_organization(%s), %s, %s) RETURNING id',
            (uid, pid, uid),
        ).fetchone()['id']
        for i, st in enumerate(statuses):
            db.execute(
                'INSERT INTO jobs (organization_id, user_id, chat_session_id, status, credits_spent, created_at) '
                'VALUES (default_organization(%s), %s, %s, %s, 5, now() - make_interval(mins => %s))',
                (uid, uid, sid, st, len(statuses) - i),
            )
        if rows:
            lid = db.execute(
                'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity) '
                "VALUES (default_organization(%s), %s, %s, %s, 'l', 'person') RETURNING id",
                (uid, pid, uid, sid),
            ).fetchone()['id']
            for i, excluded in enumerate(rows):
                db.execute(
                    'INSERT INTO list_rows (list_id, dedupe_key, excluded) VALUES (%s, %s, %s)',
                    (lid, f'k{i}', excluded),
                )
        return str(sid)

    found = chat('failed', 'succeeded', rows=[False, False, True])
    stuck = chat('succeeded', 'needs_input')
    empty = chat('failed', 'cancelled')
    fresh = chat()
    # a deleted list's rows count for nothing (#341)
    gone = chat('succeeded', rows=[False])
    db.execute('UPDATE lists SET deleted_at = now() WHERE chat_session_id = %s', (gone,))
    items = {s['id']: s for s in client.get(f'/v1/admin/chat-sessions?user_id={uid}', headers=_admin()).json()['items']}
    pick = ('outcome', 'people', 'companies', 'credits_spent')
    assert [tuple(items[s][k] for k in pick) for s in (found, stuck, empty, fresh, gone)] == [
        ('results', 2, 0, 10),
        ('stuck', 0, 0, 10),
        ('no_results', 0, 0, 10),
        (None, 0, 0, 0),
        ('no_results', 0, 0, 5),
    ]


def test_users_overview(db):
    uid, bare = _user(db), _user(db)
    db.execute(
        'INSERT INTO business_profiles (project_id, user_id, profile) VALUES (%s, %s, %s)',
        (project(db, uid), uid, Jsonb({'company_name': 'Acme'})),
    )
    db.execute(
        'INSERT INTO purchases (organization_id, user_id, stripe_session_id, pack_id, credits, amount_cents, currency) '
        "VALUES (default_organization(%s), %s, 'cs_2', 'p', 10, 100, 'eur')",
        (uid, uid),
    )
    # An admin browsing as the user is not the user's activity.
    seen = datetime.now(UTC) - timedelta(days=2)
    db.execute(
        'INSERT INTO session (id, "expiresAt", token, "createdAt", "updatedAt", "userId", "impersonatedBy") VALUES '
        '(%s, %s, %s, %s, %s, %s, NULL), (%s, %s, %s, %s, %s, %s, %s)',
        (
            str(uuid.uuid4()),
            seen + timedelta(days=7),
            't-own',
            seen,
            seen,
            uid,
            str(uuid.uuid4()),
            seen + timedelta(days=7),
            't-imp',
            seen,
            datetime.now(UTC),
            uid,
            _user(db),
        ),
    )
    # A client project's profile (#307), edited later, is not the user's company; neither is another organization's.
    client_project = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'Cliente')"
        ' RETURNING id',
        (uid, uid),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO business_profiles (project_id, user_id, profile, updated_at) VALUES (%s, %s, %s, now() + interval '1 hour')",
        (client_project, uid, Jsonb({'company_name': 'Cliente Srl'})),
    )
    # #352: the user's header shows their company and their plan with its renewal date
    renews = datetime(2026, 11, 1, tzinfo=UTC)
    db.execute(
        'INSERT INTO subscriptions (organization_id, user_id, stripe_subscription_id, plan_id, status,'
        " current_period_end, event_created) VALUES (default_organization(%s), %s, 'sub_1', 'pro', 'active', %s, 1)",
        (uid, uid, renews),
    )
    usage = client.get(f'/v1/admin/users/{uid}/usage', headers=_admin()).json()
    assert usage['company'] == 'Acme' and usage['subscription']['plan_id'] == 'pro'
    assert datetime.fromisoformat(usage['subscription']['current_period_end']) == renews
    assert client.get(f'/v1/admin/users/{bare}/usage', headers=_admin()).json()['subscription'] is None

    r = client.get(f'/v1/admin/users/overview?ids={uid}&ids={bare}', headers=_admin()).json()
    assert datetime.fromisoformat(r['users'][0 if r['users'][0]['user_id'] == uid else 1]['last_active_at']) == seen
    by = {u['user_id']: u for u in r['users']}
    assert by[uid]['company'] == 'Acme' and by[uid]['has_purchases'] is True and by[uid]['plan_id'] == 'pro'
    assert by[bare]['company'] is None and by[bare]['has_purchases'] is False


def test_admin_balance_is_the_founded_organization_wallet(db):
    """#676, #682: a team the user created has its own wallet, plan and purchases with their user_id; the admin
    reads their own org's."""
    uid = _user(db)
    team = db.execute(
        'INSERT INTO organization (name, slug, "createdAt") VALUES (\'Agenzia\', %s, now()) RETURNING id',
        (f'team-{uuid.uuid4()}',),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'admin\', now())',
        (team, uid),
    )
    db.execute('INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (%s, %s, 0)', (team, uid))
    db.execute(
        'INSERT INTO subscriptions (organization_id, user_id, stripe_subscription_id, plan_id, status, event_created)'
        " VALUES (%s, %s, 'sub_team', 'pro', 'active', 1)",
        (team, uid),
    )
    db.execute(
        'INSERT INTO purchases (organization_id, user_id, stripe_session_id, pack_id, credits, amount_cents, currency) '
        "VALUES (%s, %s, 'cs_team', 'p', 10, 100, 'eur')",
        (team, uid),
    )
    assert client.get(f'/v1/admin/users/{uid}/usage', headers=_admin()).json()['balance'] == 50
    users = client.get(f'/v1/admin/users/overview?ids={uid}', headers=_admin()).json()['users']
    assert [(u['balance'], u['plan_id'], u['has_purchases']) for u in users] == [(50, None, False)]


# ── Auto top-up (#94) ──────────────────────────────────────────────────────


@pytest.fixture
def packs(monkeypatch):
    monkeypatch.setattr(
        settings, 'billing_packs', [CreditPack(id='starter', credits=1000, price_eur_cents=1900, stripe_price_id='p_1')]
    )


class FakeStripe:
    """The Stripe calls auto top-up makes, recorded; ``decline`` fails the payment."""

    def __init__(self, monkeypatch, *, card='pm_1', decline=False, status='draft'):
        self.calls: list[str] = []
        self.card, self.decline, self.status = card, decline, status
        mod = app.billing.auto_topup.stripe
        inv = {'id': 'in_1', 'status': 'draft', 'currency': 'eur', 'total_excluding_tax': 1900, 'metadata': {}}

        def obj(**extra):
            return mod.Invoice.construct_from({**inv, **extra}, 'k')

        def create(**kw):
            self.calls.append('create')
            inv['metadata'] = kw['metadata']
            return obj()

        def pay(_id, **kw):
            self.calls.append('pay')
            if self.decline is True:
                raise mod.CardError('declined', None, 'card_declined')
            if self.decline:
                raise self.decline
            return obj(status='paid')

        monkeypatch.setattr(mod.Customer, 'retrieve', lambda *a, **k: mod.Customer.construct_from({'id': 'cus_1'}, 'k'))
        monkeypatch.setattr(
            mod.Customer,
            'list_payment_methods',
            lambda *a, **k: type('L', (), {'data': [type('P', (), {'id': self.card})()] if self.card else []}),
        )
        monkeypatch.setattr(mod.Invoice, 'create', create)
        monkeypatch.setattr(mod.Invoice, 'retrieve', lambda *a, **k: obj(status=self.status))
        monkeypatch.setattr(mod.InvoiceItem, 'create', lambda **k: self.calls.append('item'))
        monkeypatch.setattr(mod.Invoice, 'finalize_invoice', lambda *a, **k: obj(status='open'))
        monkeypatch.setattr(mod.Invoice, 'pay', pay)
        monkeypatch.setattr(mod.Invoice, 'void_invoice', lambda *a, **k: self.calls.append('void'))


def _auto_topup(c, uid, *, threshold=100, balance=50, enabled=True):
    c.execute('UPDATE user_credits SET credits_remaining = %s WHERE user_id = %s', (balance, uid))
    c.execute(
        'INSERT INTO auto_topups (organization_id, user_id, enabled, pack_id, threshold)'
        ' VALUES (default_organization(%s), %s, %s, %s, %s)',
        (uid, uid, enabled, 'starter', threshold),
    )


def test_auto_topup_claims_only_users_under_threshold_once(db, packs):
    due, above, off = _user(db), _user(db), _user(db)
    _auto_topup(db, due)
    _auto_topup(db, above, balance=500)
    _auto_topup(db, off, enabled=False)

    def claim_due(org):
        return call_for(org, app.billing.auto_topup.claim_due, org)

    org = _org(db, due)
    assert [r['organization_id'] for r in claim_due(org)] == [org]
    assert claim_due(_org(db, above)) == claim_due(_org(db, off)) == []
    assert claim_due(org) == []  # the charge is running: no second one


def test_auto_topup_charge_credits_once_with_webhook_replay(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    fake = FakeStripe(monkeypatch)
    org = _org(db, uid)
    claim = call_for(org, app.billing.auto_topup.claim_due, org)[0]
    assert call_for(org, app.billing.auto_topup.charge, org, 'starter', claim['charging_since'].isoformat()) == 'paid'
    assert fake.calls == ['create', 'item', 'pay']
    # invoice.paid for the same invoice arrives afterwards: no second credit
    inv = {
        'id': 'in_1',
        'currency': 'eur',
        'total_excluding_tax': 1900,
        'metadata': {
            'kind': 'auto_topup',
            'organization_id': org,
            'user_id': uid,
            'pack_id': 'starter',
            'credits': '1000',
        },
    }
    ev = {'id': 'evt_at', 'object': 'event', 'type': 'invoice.paid', 'created': 1, 'data': {'object': inv}}
    assert _post_event(ev).status_code == 200
    assert _balance(db, uid) == 1050
    row = db.execute('SELECT charging_since, enabled FROM auto_topups WHERE user_id = %s', (uid,)).fetchone()
    assert row == {'charging_since': None, 'enabled': True}
    assert (
        db.execute('SELECT amount_cents FROM purchases WHERE user_id = %s', (uid,)).fetchone()['amount_cents'] == 1900
    )


def test_auto_topup_declined_card_turns_it_off(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    fake = FakeStripe(monkeypatch, decline=True)
    claim = _claim(db, uid)
    assert call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', claim) == 'failed'
    assert fake.calls[-1] == 'void'
    row = db.execute(
        'SELECT enabled, charging_since, last_error FROM auto_topups WHERE user_id = %s', (uid,)
    ).fetchone()
    assert row == {'enabled': False, 'charging_since': None, 'last_error': 'card_declined'}
    assert _balance(db, uid) == 50
    note = db.execute('SELECT kind, href, data FROM notifications WHERE user_id = %s', (uid,)).fetchone()
    # #173: cannot be turned off. #323: names the organization and opens its billing.
    assert note == {
        'kind': 'payment_failed',
        'href': f'/billing/switch?org={_org(db, uid)}',
        'data': {'organization': 'T'},
    }


def test_auto_topup_failure_stays_off_when_the_notification_fails(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    FakeStripe(monkeypatch, decline=True)

    def broken(*a, **k):
        raise RuntimeError('notifications down')

    monkeypatch.setattr(app.billing.auto_topup.notifications, 'notify', broken)
    assert call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', _claim(db, uid)) == 'failed'
    row = db.execute(
        'SELECT enabled, charging_since, last_error FROM auto_topups WHERE user_id = %s', (uid,)
    ).fetchone()
    assert row == {'enabled': False, 'charging_since': None, 'last_error': 'card_declined'}  # not the notify error


def test_auto_topup_without_card_saves_off_until_setup_completes(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    from tests.test_billing_profile import individual

    db.execute(
        "INSERT INTO billing_profiles (organization_id,profile,sync_status) VALUES (%s,%s,'local')",
        (_org(db, uid), Jsonb(individual())),
    )
    monkeypatch.setattr(app.billing.profile, '_sync_customer', lambda *a: None)
    FakeStripe(monkeypatch, card=None)
    monkeypatch.setattr(
        app.billing.routes.stripe.checkout.Session,
        'create',
        lambda **kw: type('S', (), {'url': f'https://checkout.test/{kw["mode"]}'})(),
    )
    auth = _auth(db, uid)
    body = {'enabled': True, 'pack_id': 'starter', 'threshold': 250}
    assert client.put('/v1/billing/auto-topup', json={**body, 'threshold': 7}, headers=auth).status_code == 422
    r = client.put('/v1/billing/auto-topup', json=body, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()['setup_url'] == 'https://checkout.test/setup' and r.json()['enabled'] is False
    ev = {
        'id': 'evt_setup',
        'object': 'event',
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'id': 'cs_setup',
                'mode': 'setup',
                'client_reference_id': uid,
                'metadata': {'kind': 'auto_topup'},
            }
        },
    }
    assert _post_event(ev).status_code == 200
    state = client.get('/v1/billing/auto-topup', headers=auth).json()
    assert state['enabled'] is True and state['threshold'] == 250 and state['thresholds'] == [100, 250, 500, 1000]


def _claim(db, uid):
    org = _org(db, uid)
    return call_for(org, app.billing.auto_topup.claim_due, org)[0]['charging_since'].isoformat()


def _paid_invoice_event(uid, evt, invoice):
    """An auto top-up invoice made before #306: its metadata names the user only."""
    inv = {
        'id': invoice,
        'currency': 'eur',
        'total_excluding_tax': 1900,
        'metadata': {'kind': 'auto_topup', 'user_id': uid, 'pack_id': 'starter', 'credits': '1000'},
    }
    return {'id': evt, 'object': 'event', 'type': 'invoice.paid', 'created': 1, 'data': {'object': inv}}


def test_auto_topup_late_webhook_keeps_a_newer_charge_claimed(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    FakeStripe(monkeypatch)
    assert (
        call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', _claim(db, uid)) == 'paid'
    )  # invoice in_1 credited
    db.execute('UPDATE user_credits SET credits_remaining = 10 WHERE user_id = %s', (uid,))
    _claim(db, uid)  # a second charge is running
    assert _post_event(_paid_invoice_event(uid, 'evt_late', 'in_1')).status_code == 200  # in_1's late webhook
    assert (
        call_for(_org(db, uid), app.billing.auto_topup.claim_due, _org(db, uid)) == []
    )  # still claimed: no third charge


def test_auto_topup_invoice_paid_before_the_charge_credits_it_and_ends_the_claim(db, packs):
    """The webhook runs as System(STRIPE_WEBHOOK): it credits the invoice and clears the charge claim as that role."""
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    _claim(db, uid)
    assert _post_event(_paid_invoice_event(uid, 'evt_first', 'in_first')).status_code == 200
    assert _balance(db, uid) == 1050
    row = db.execute('SELECT charging_since FROM auto_topups WHERE user_id = %s', (uid,)).fetchone()
    assert row == {'charging_since': None}


def test_auto_topup_redelivered_task_after_credit_is_skipped(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    fake = FakeStripe(monkeypatch)
    claim = _claim(db, uid)
    assert call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', claim) == 'paid'
    assert call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', claim) == 'skipped'
    assert fake.calls.count('pay') == 1
    assert db.execute('SELECT enabled FROM auto_topups WHERE user_id = %s', (uid,)).fetchone()['enabled'] is True


def test_auto_topup_redelivered_task_finds_paid_invoice_and_credits(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    fake = FakeStripe(monkeypatch, status='paid')  # the first run paid, then died before crediting
    assert call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', _claim(db, uid)) == 'paid'
    assert 'pay' not in fake.calls and _balance(db, uid) == 1050


def test_auto_topup_any_error_releases_the_claim(db, packs, monkeypatch):
    from billiard.exceptions import SoftTimeLimitExceeded

    uid = _user(db)
    _customer(db, uid)
    _auto_topup(db, uid)
    fake = FakeStripe(monkeypatch, decline=SoftTimeLimitExceeded())
    assert call_for(_org(db, uid), app.billing.auto_topup.charge, _org(db, uid), 'starter', _claim(db, uid)) == 'failed'
    assert fake.calls[-1] == 'void'
    row = db.execute(
        'SELECT enabled, charging_since, last_error FROM auto_topups WHERE user_id = %s', (uid,)
    ).fetchone()
    assert row == {'enabled': False, 'charging_since': None, 'last_error': 'SoftTimeLimitExceeded'}


def test_auto_topup_stale_card_setup_does_not_switch_it_back_on(db, packs, monkeypatch):
    uid = _user(db)
    _customer(db, uid)
    from tests.test_billing_profile import individual

    db.execute(
        "INSERT INTO billing_profiles (organization_id,profile,sync_status) VALUES (%s,%s,'local')",
        (_org(db, uid), Jsonb(individual())),
    )
    monkeypatch.setattr(app.billing.profile, '_sync_customer', lambda *a: None)
    FakeStripe(monkeypatch, card=None)
    monkeypatch.setattr(
        app.billing.routes.stripe.checkout.Session, 'create', lambda **kw: type('S', (), {'url': 'https://c.test/s'})()
    )
    auth = _auth(db, uid)
    body = {'enabled': True, 'pack_id': 'starter', 'threshold': 100}
    assert client.put('/v1/billing/auto-topup', json=body, headers=auth).json()['setup_url']
    assert client.put('/v1/billing/auto-topup', json={**body, 'enabled': False}, headers=auth).status_code == 200
    ev = {
        'id': 'evt_old_setup',
        'object': 'event',
        'type': 'checkout.session.completed',
        'data': {
            'object': {'id': 'cs_old', 'mode': 'setup', 'client_reference_id': uid, 'metadata': {'kind': 'auto_topup'}}
        },
    }
    assert _post_event(ev).status_code == 200
    assert client.get('/v1/billing/auto-topup', headers=auth).json()['enabled'] is False


# ── balance changes (#243) ─────────────────────────────────────────────────


def _rules(org_id: str, refused: bool = False) -> dict:
    """``on_balance_changed`` as the worker runs it: as ForOrg of the organization whose balance changed."""
    with scopes.acting_for(org_id, 'balance changed'):
        return app.worker.on_balance_changed(org_id, refused)


def _run_queued(queued: list[tuple[str, bool]]) -> list[dict]:
    """Run the on_balance_changed tasks the call sites queued, as the worker would."""
    results = [_rules(*args) for args in queued]
    queued.clear()
    return results


def test_admin_deduction_below_200_notifies_once_after_the_commit(db, balance_events, monkeypatch):
    uid = _user(db)
    seen: list[int] = []

    def enqueue(args, **kw):
        seen.append(_wallet(db, args[0]))  # another connection: only committed data is visible
        balance_events.append(args)

    monkeypatch.setattr(app.worker.on_balance_changed, 'apply_async', enqueue)

    def adjust(amount):
        r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': amount, 'reason': 'x'}, headers=_admin())
        assert r.status_code == 200, r.text
        return _run_queued(balance_events)

    assert adjust(250) == [{'charged': 0, 'notified': False}]  # 300: armed
    assert adjust(-150) == [{'charged': 0, 'notified': True}]  # 150: crossed below 200
    assert adjust(-10) == [{'charged': 0, 'notified': False}]  # still below: no second notice
    adjust(100)  # 240: re-armed
    assert adjust(-100) == [{'charged': 0, 'notified': True}]
    assert seen == [300, 150, 140, 240, 140]
    notes = db.execute("SELECT data FROM notifications WHERE kind = 'low_credits' ORDER BY created_at").fetchall()
    assert [n['data'] for n in notes] == [{'credits': 150, 'organization': 'T'}, {'credits': 140, 'organization': 'T'}]


def test_a_banned_admin_stays_armed_and_is_told_once_the_ban_ends(db, balance_events):
    """#487: nobody is told while the only admin is banned, so the run reports no notice and keeps the alert."""
    uid = _user(db)

    def adjust(amount):
        r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': amount, 'reason': 'x'}, headers=_admin())
        assert r.status_code == 200, r.text
        return _run_queued(balance_events)

    def state():
        armed = db.execute('SELECT armed FROM low_credit_alerts WHERE user_id = %s', (uid,)).fetchone()['armed']
        notes = db.execute("SELECT count(*) AS n FROM notifications WHERE kind = 'low_credits'").fetchone()['n']
        return armed, notes

    adjust(250)  # 300: armed
    db.execute('UPDATE "user" SET banned = true, "banExpires" = NULL WHERE id = %s', (uid,))
    assert adjust(-150) == [{'charged': 0, 'notified': False}]  # 150: below, but nobody to tell
    assert state() == (True, 0)
    db.execute('UPDATE "user" SET banned = false WHERE id = %s', (uid,))
    assert adjust(-10) == [{'charged': 0, 'notified': True}]  # the first change after the ban
    assert state() == (False, 1)
    assert adjust(-10) == [{'charged': 0, 'notified': False}]


def test_purchase_webhook_queues_the_rules_to_re_arm(db, balance_events):
    uid = _user(db)
    assert _post_event(_paid_event(uid)).status_code == 200
    assert balance_events == [(_org(db, uid), False)]
    _run_queued(balance_events)
    armed = db.execute('SELECT armed FROM low_credit_alerts WHERE user_id = %s', (uid,)).fetchone()
    assert armed == {'armed': True}


def test_enqueue_failure_never_breaks_the_charge(db, monkeypatch):
    uid = _user(db)

    def broker_down(*a, **k):
        raise ConnectionError('redis down')

    monkeypatch.setattr(app.worker.on_balance_changed, 'apply_async', broker_down)
    r = client.post(f'/v1/admin/users/{uid}/credits', json={'amount': -20, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 200 and r.json()['balance'] == 30
    assert _balance(db, uid) == 30


def test_balance_burst_charges_and_notifies_once(db, packs, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    auto, plain, above = _user(db), _user(db), _user(db)
    _auto_topup(db, auto)  # balance 50, threshold 100
    _auto_topup(db, above, balance=500)
    db.execute(
        'INSERT INTO low_credit_alerts (organization_id, user_id, armed) VALUES (%s, %s, true)',
        (_org(db, plain), plain),
    )
    db.execute('UPDATE user_credits SET credits_remaining = 150 WHERE user_id = %s', (plain,))
    charges: list[tuple] = []
    monkeypatch.setattr(app.worker.auto_topup_charge, 'delay', lambda *a: charges.append(a))
    with ThreadPoolExecutor(8) as pool:  # each task on its own connection, as on separate workers
        results = list(pool.map(_rules, [_org(db, u) for u in (auto, plain, above)] * 8))
    assert [c[0] for c in charges] == [_org(db, auto)]
    assert sum(r['charged'] for r in results) == 1
    notes = db.execute('SELECT user_id::text FROM notifications').fetchall()
    assert notes == [{'user_id': plain}]  # auto top-up on: never told; above both lines: nothing


def test_refunded_holds_never_notify_or_charge_but_spend_does(db, packs, monkeypatch):
    plain, auto = _user(db), _user(db)
    _auto_topup(db, auto, threshold=250, balance=250)
    db.execute('UPDATE user_credits SET credits_remaining = 250 WHERE user_id = %s', (plain,))
    charges: list[tuple] = []
    monkeypatch.setattr(app.worker.auto_topup_charge, 'delay', lambda *a: charges.append(a))

    def turn(spent):  # a chat turn: hold 100 (JOB_CREDIT_RESERVE), then settle what was spent
        results = []
        for uid in (plain, auto):
            org = _org(db, uid)
            rid = db.execute('SELECT reserve_credits(%s, %s, 100) AS r', (org, uid)).fetchone()['r']
            results.append(_rules(org))
            db.execute('SELECT settle_reservation_with_slack(%s, %s)', (rid, 100 - spent))
            results.append(_rules(org))
        return results

    for _ in range(2):
        assert turn(0) == [{'charged': 0, 'notified': False}] * 4  # 150 while held, 250 after
    assert turn(100) == [
        {'charged': 0, 'notified': False},  # held: still 250 net
        {'charged': 0, 'notified': True},  # 150 spent
        {'charged': 0, 'notified': False},
        {'charged': 1, 'notified': False},  # auto top-up on: charged, not told
    ]
    assert [c[0] for c in charges] == [_org(db, auto)]
    notes = db.execute('SELECT user_id::text, data FROM notifications').fetchall()
    assert notes == [{'user_id': plain, 'data': {'credits': 150, 'organization': 'T'}}]


def _ai_hold(org, uid):
    """An AI column fill's hold for 3 cells, paid by ``org`` with no project cap."""
    with scopes.acting_as(org, uid):  # a fill runs as the member who started it
        tab = app.lists.ai_columns._open(Payer(org, uid, None), str(uuid.uuid4()), 'ai_x', 3, None)
    return tab if isinstance(tab, Refused) else tab.hold


def test_refused_hold_queues_the_rules_so_a_lost_enqueue_heals(db, packs, monkeypatch, balance_events):
    uid = _user(db)
    _auto_topup(db, uid, balance=0)  # at 0, and the task of the change that got it there was lost
    charges: list[tuple] = []
    monkeypatch.setattr(app.worker.auto_topup_charge, 'delay', lambda *a: charges.append(a))
    org = _org(db, uid)
    assert _ai_hold(org, uid) == Refused(capped=False)
    assert balance_events == [(org, True)]
    assert _run_queued(balance_events) == [{'charged': 1, 'notified': False}]
    assert [c[0] for c in charges] == [org]


def test_refused_hold_tops_up_on_available_credits_while_holds_keep_net_high(db, packs, monkeypatch, balance_events):
    uid = _user(db)
    _auto_topup(db, uid, threshold=250, balance=300)
    charges: list[tuple] = []
    monkeypatch.setattr(app.worker.auto_topup_charge, 'delay', lambda *a: charges.append(a))
    org = _org(db, uid)
    for _ in range(3):  # a long job's growing holds: 0 available, still 300 net
        db.execute('SELECT reserve_credits(%s, %s, 100)', (org, uid))
    assert _run_queued([(org, False)]) == [{'charged': 0, 'notified': False}]  # net 300: not due
    assert _ai_hold(org, uid) == Refused(capped=False)  # the next hold
    assert _run_queued(balance_events * 3) == [  # however many refusals: one charge
        {'charged': 1, 'notified': False},
        {'charged': 0, 'notified': False},
        {'charged': 0, 'notified': False},
    ]
    assert [c[0] for c in charges] == [org]


# ── The organization's wallet (#306) ───────────────────────────────────────


def test_billing_is_for_the_organization_admins_only(db, packs, monkeypatch):
    admin, member = _user(db), _user(db)
    org = _org(db, admin)
    _join(db, org, member)
    _customer(db, admin)
    from tests.test_billing_profile import individual

    db.execute(
        "INSERT INTO billing_profiles (organization_id,profile,sync_status) VALUES (%s,%s,'local')",
        (org, Jsonb(individual())),
    )
    monkeypatch.setattr(app.billing.profile, '_sync_customer', lambda *a: None)
    monkeypatch.setattr(app.billing.routes, '_customer_id', lambda user: 'cus_1')
    monkeypatch.setattr(
        app.billing.routes.stripe.checkout.Session, 'create', lambda **kw: type('S', (), {'url': 'https://c.test/s'})()
    )
    calls = [
        ('get', '/v1/billing/subscription', None),
        ('get', '/v1/billing/auto-topup', None),
        ('get', '/v1/billing/transactions', None),
        ('post', '/v1/billing/checkout', {'pack_id': 'starter'}),
        ('post', '/v1/billing/subscribe', {'plan_id': 'starter'}),
        ('post', '/v1/billing/portal', None),
        ('put', '/v1/billing/auto-topup', {'enabled': False, 'pack_id': 'starter', 'threshold': 100}),
        ('post', '/v1/billing/plan', {'plan_id': 'starter'}),
    ]
    for method, path, body in calls:
        kw = {'json': body} if body else {}
        r = client.request(method, path, headers=_auth(db, member), **kw)
        assert r.status_code == 403 and r.json()['detail']['code'] == 'AUTH.ORG_ADMIN_ONLY', path
    assert client.get('/v1/billing/transactions', headers=_auth(db, admin)).status_code == 200
    assert client.post('/v1/billing/checkout', json={'pack_id': 'starter'}, headers=_auth(db, admin)).status_code == 200
    # what the web reads to show or hide the billing page
    me = client.get('/v1/me', headers=_auth(db, member)).json()
    assert (me['org_role'], me['credits_remaining']) == ('member', 50)
    assert client.get('/v1/me', headers=_auth(db, admin)).json()['org_role'] == 'admin'


def test_me_carries_the_live_plan_credits_for_every_member(db, plans):
    """#434: the sidebar's credits bar measures the balance against the plan's credits per period."""
    admin, member = _user(db), _user(db)
    org = _org(db, admin)
    _join(db, org, member)
    assert client.get('/v1/me', headers=_auth(db, member)).json()['plan_credits'] is None
    db.execute(
        'INSERT INTO subscriptions (stripe_subscription_id, organization_id, user_id, plan_id, status, event_created)'
        " VALUES ('sub_1', %s, %s, 'pro', 'active', 1)",
        (org, admin),
    )
    assert client.get('/v1/me', headers=_auth(db, member)).json()['plan_credits'] == 6000
    db.execute("UPDATE subscriptions SET status = 'canceled' WHERE stripe_subscription_id = 'sub_1'")
    assert client.get('/v1/me', headers=_auth(db, member)).json()['plan_credits'] is None


def test_purchases_and_plans_credit_the_organization_and_its_members_spend_them(db, plans):
    """Stripe webhooks map to the organization in the metadata, not to the buyer's own one; every member
    then draws on it, and the ledger shows the organization's activity whoever did it."""
    admin, member = _user(db), _user(db)
    org, own = _org(db, admin), _org(db, member)
    _join(db, org, member, role='admin')
    ev = _paid_event(member)
    ev['data']['object']['metadata']['organization_id'] = org
    assert _post_event(ev).status_code == 200
    assert (_wallet(db, org), _wallet(db, own)) == (1050, 50)
    row = db.execute('SELECT organization_id::text, user_id::text FROM purchases').fetchone()
    assert row == {'organization_id': org, 'user_id': member}

    _customer(db, admin)  # the organization's Stripe customer: renewals land on its wallet
    assert _post_event(_invoice_event('evt_2', 'in_1', reason='subscription_create')).status_code == 200
    assert _post_event(_subscription_event('evt_3', 100, typ='created')).status_code == 200
    assert _wallet(db, org) == 7050
    r = client.get('/v1/billing/subscription', headers=_auth(db, member))
    assert r.json()['subscription']['plan_id'] == 'pro'  # the organization's plan, seen by its other admin

    db.execute('SELECT reserve_credits(%s, %s, 50)', (org, member))
    items = client.get('/v1/billing/transactions', headers=_auth(db, admin)).json()['items']
    assert [i['type'] for i in items] == ['reservation', 'renewal', 'purchase']


def test_a_user_in_two_organizations_buys_spends_and_tops_up_in_each_apart(db, packs, monkeypatch):
    """#306 with #308: Bo is the admin of his own organization A and of Ada's team B. What he buys and spends
    acting in B moves B's wallet only; each organization keeps its own auto top-up; nothing guesses for him."""
    ada, bo = _user(db), _user(db)
    team, own = _org(db, ada), _org(db, bo)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (team, bo, 'admin'),
    )

    def as_bo(org):
        return {'Authorization': f'Bearer {_token(sub=bo, org=org)}'}

    ev = _paid_event(bo)
    ev['data']['object']['metadata']['organization_id'] = team  # the checkout Bo started in B
    assert _post_event(ev).status_code == 200
    assert (_wallet(db, team), _wallet(db, own)) == (1050, 50)
    hold = _ai_hold(team, bo)  # a fill on one of B's lists
    assert isinstance(hold, Hold) and hold.amount == 3 and (_wallet(db, team), _wallet(db, own)) == (1047, 50)
    rid = hold.id
    db.execute('SELECT settle_reservation_with_slack(%s, 1)', (rid,))
    assert (_wallet(db, team), _wallet(db, own)) == (1048, 50)
    spent = db.execute('SELECT organization_id::text, user_id::text FROM credit_transactions WHERE id = %s', (rid,))
    assert spent.fetchone() == {'organization_id': team, 'user_id': bo}

    for org, threshold in ((team, 100), (own, 500)):
        body = {'enabled': False, 'pack_id': 'starter', 'threshold': threshold}
        assert client.put('/v1/billing/auto-topup', json=body, headers=as_bo(org)).status_code == 200
        assert client.get('/v1/billing/auto-topup', headers=as_bo(org)).json()['threshold'] == threshold
    rows = db.execute('SELECT organization_id::text AS org, user_id::text AS uid, threshold FROM auto_topups')
    assert {r['org']: (r['uid'], r['threshold']) for r in rows} == {team: (bo, 100), own: (bo, 500)}
    db.execute('UPDATE auto_topups SET enabled = true')
    db.execute('UPDATE user_credits SET credits_remaining = 40 WHERE organization_id = ANY(%s::uuid[])', ([team, own],))
    charges: list[tuple] = []
    monkeypatch.setattr(app.worker.auto_topup_charge, 'delay', lambda *a: charges.append(a))
    assert _rules(team)['charged'] == 1
    assert [c[0] for c in charges] == [team]  # A's top-up waits for A's own balance change

    ledger = client.get('/v1/billing/transactions', headers=as_bo(own)).json()['items']
    assert ledger == []  # A's ledger: nothing Bo did in B
    # Nothing defaults Bo's credit rows to one of his organizations: no user-keyed call, no fill.
    with pytest.raises(psycopg.errors.UndefinedFunction):
        db.execute('SELECT reserve_credits(%s::uuid, 5)', (bo,))
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.execute(
            "INSERT INTO purchases (user_id, stripe_session_id, pack_id, credits, amount_cents) VALUES (%s, 'cs_x', 'p', 1, 1)",
            (bo,),
        )


def test_a_user_whose_oldest_membership_is_a_team_is_credited_on_their_own_organization(db):
    """Admin adjustments and pre-#306 Stripe objects (only ``user_id``) go to the organization the user founded
    (slug = their id), not to a team they happened to join first."""
    ada, bo = _user(db), _user(db)
    team, own = _org(db, ada), _org(db, bo)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, %s)',
        (team, bo, 'admin', datetime(2020, 1, 1, tzinfo=UTC)),
    )
    assert db.execute('SELECT default_organization(%s) AS id', (bo,)).fetchone()['id'] == uuid.UUID(team)
    r = client.post(f'/v1/admin/users/{bo}/credits', json={'amount': 25, 'reason': 'goodwill'}, headers=_admin())
    assert r.status_code == 200 and r.json()['balance'] == 75
    old = _paid_event(bo)  # no organization_id in its metadata
    assert _post_event(old).status_code == 200
    assert (_wallet(db, own), _wallet(db, team)) == (1075, 50)


def test_deleting_an_account_keeps_the_organizations_money(db):
    """#323 (F1 of the #328 review): the ledger, purchases, Stripe rows and the wallet are the organization's.
    Deleting the member who moved the money leaves them with user_id NULL: the balance, the history and the
    client's month spend (so its hard cap) are unchanged. Deleting the wallet's opener keeps the wallet."""
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, org, bo)
    pid = project(db, ada)
    rid = db.execute(
        "SELECT reserve_project_credits(%s, %s, %s, 30, 'chat_turn', 'j1', NULL, NULL, 'turn') AS r", (pid, org, bo)
    ).fetchone()['r']
    db.execute('SELECT settle_reservation_with_slack(%s, 10)', (rid,))
    db.execute(
        "SELECT increment_credits(%s, %s, 100, 'purchase', 'stripe_checkout', 'cs_9', NULL, NULL, 'Top-up')", (org, bo)
    )
    db.execute(
        'INSERT INTO purchases (organization_id, user_id, stripe_session_id, pack_id, credits, amount_cents)'
        " VALUES (%s, %s, 'cs_9', 'starter', 100, 1900)",
        (org, bo),
    )
    db.execute(
        "INSERT INTO billing_customers (organization_id, user_id, stripe_customer_id) VALUES (%s, %s, 'cus_9')",
        (org, bo),
    )
    before = {
        'wallet': _wallet(db, org),
        'spend': db.execute('SELECT project_month_spend(%s) AS s', (pid,)).fetchone()['s'],
        'ledger': db.execute(
            'SELECT count(*) AS n FROM credit_transactions WHERE organization_id = %s', (org,)
        ).fetchone()['n'],
    }
    assert before == {'wallet': 130, 'spend': 20, 'ledger': 3}  # 50 - 20 kept of the hold + 100 bought

    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    rows = db.execute('SELECT user_id FROM credit_transactions WHERE organization_id = %s', (org,)).fetchall()
    assert len(rows) == before['ledger'] and {r['user_id'] for r in rows} == {None}
    assert db.execute('SELECT project_month_spend(%s) AS s', (pid,)).fetchone()['s'] == before['spend']
    assert _wallet(db, org) == before['wallet']
    for table in ('purchases', 'billing_customers'):
        assert db.execute(f'SELECT user_id FROM {table} WHERE organization_id = %s', (org,)).fetchall() == [
            {'user_id': None}
        ], table
    d = client.get(f'/v1/admin/organizations/{org}', headers=_admin()).json()
    assert len(d['ledger']) == 3 and {tx['email'] for tx in d['ledger']} == {None} and len(d['purchases']) == 1

    db.execute('DELETE FROM "user" WHERE id = %s', (ada,))  # who opened the wallet
    assert _wallet(db, org) == before['wallet']
    r = client.post(f'/v1/admin/organizations/{org}/credits', json={'amount': 5, 'reason': 'x'}, headers=_admin())
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ADMIN.ORG_HAS_NO_MEMBERS'
    assert _wallet(db, org) == before['wallet']


def test_a_purchase_by_a_deleted_member_is_credited_with_no_user(db):
    """A Stripe checkout completed after its buyer deleted their account still credits the organization."""
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, org, bo)
    event = _paid_event(bo)
    event['data']['object']['metadata']['organization_id'] = org
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    assert _post_event(event).status_code == 200
    assert _wallet(db, org) == 1050
    assert db.execute('SELECT user_id FROM purchases').fetchall() == [{'user_id': None}]


def test_admin_organization_search_takes_wildcards_literally(db):
    a, b = _user(db), _user(db)
    for uid, name in ((a, '50% off'), (b, '500 off')):
        db.execute('UPDATE organization SET name = %s WHERE id = %s', (name, _org(db, uid)))

    def names(q):
        return [
            o['name'] for o in client.get('/v1/admin/organizations', params={'q': q}, headers=_admin()).json()['items']
        ]

    assert names('50%') == ['50% off']
    assert names('0_') == [] and names('0 off') == ['500 off']


def _renewed(c) -> list[dict]:
    return c.execute(
        "SELECT user_id::text, href, data, organization_id::text AS org FROM notifications WHERE kind = 'plan_renewed'"
    ).fetchall()


def test_a_renewal_tells_every_admin_once_and_creating_or_upgrading_a_plan_tells_nobody(db, plans):
    uid, co_admin, colleague = _user(db), _user(db), _user(db)
    _customer(db, uid)
    org = _org(db, uid)
    _join(db, org, co_admin, 'admin')
    _join(db, org, colleague)
    created = _invoice_event('evt_1', 'in_1', reason='subscription_create', price='price_starter')
    assert _post_event(created).status_code == 200
    assert _post_event(_upgrade_event('evt_2', 'in_2')).status_code == 200
    assert _renewed(db) == []  # the admin's own actions: the page's toast is enough
    _spend(db, uid, 1000)
    assert _post_event(_invoice_event('evt_3', 'in_3')).status_code == 200
    renewed = _renewed(db)
    assert sorted(r['user_id'] for r in renewed) == sorted([uid, co_admin])  # not the member
    assert {r['href'] for r in renewed} == {f'/billing/switch?org={org}'} and {r['org'] for r in renewed} == {org}
    name = db.execute('SELECT name FROM organization WHERE id = %s', (org,)).fetchone()['name']
    assert renewed[0]['data'] == {'credits': 6000, 'plan': 'pro', 'expired': 5000, 'organization': name}
    assert _post_event(_invoice_event('evt_3b', 'in_3')).status_code == 200  # the same invoice again
    assert len(_renewed(db)) == 2
    assert _post_event(_invoice_event('evt_4', 'in_4', price='price_other')).status_code == 200  # unmapped: no credit
    assert len(_renewed(db)) == 2


# ── the Stripe customer's email (#499) ───────────────────────────────────────


@pytest.fixture
def stripe_emails(monkeypatch):
    """Every ``stripe.Customer.modify`` as ``(customer id, email)``; ``refused`` names customers Stripe rejects."""

    class Calls(list):
        refused: set[str]

    calls = Calls()
    calls.refused = set()

    def modify(customer_id, **kw):
        assert kw['api_key'] == 'sk_test'
        if customer_id in calls.refused:
            raise app.billing.customer_email.stripe.APIConnectionError('down')
        calls.append((customer_id, kw['email']))

    monkeypatch.setattr(settings, 'stripe_secret_key', 'sk_test')
    monkeypatch.setattr(app.billing.customer_email.stripe.Customer, 'modify', modify)
    return calls


def _billed(db, uid, customer_id, email=None) -> None:
    """A Stripe customer of the user's organization, and the address Stripe was last given for it."""
    _customer(db, uid, customer_id)
    db.execute('UPDATE billing_customers SET email = %s WHERE stripe_customer_id = %s', (email, customer_id))


def test_a_changed_login_email_reaches_the_stripe_customer_once(db, stripe_emails):
    ann, bob = _user(db), _user(db)
    _billed(db, ann, 'cus_ann', f'{ann}@x.test')  # in step with Stripe
    _billed(db, bob, 'cus_bob', f'{bob}@x.test')
    assert app.worker.sync_billing_customer_emails() == {'updated': 0, 'failed': 0}

    db.execute('UPDATE "user" SET email = %s WHERE id = %s', ('ann@new.test', ann))  # Better Auth changeEmail
    assert app.worker.sync_billing_customer_emails() == {'updated': 1, 'failed': 0}
    assert stripe_emails == [('cus_ann', 'ann@new.test')]
    stored = db.execute('SELECT email FROM billing_customers WHERE user_id = %s', (ann,)).fetchone()['email']
    assert stored == 'ann@new.test'
    assert app.worker.sync_billing_customer_emails() == {'updated': 0, 'failed': 0}  # nothing left to send
    assert len(stripe_emails) == 1


def test_a_customer_from_before_the_column_is_sent_its_current_email(db, stripe_emails):
    uid = _user(db)
    _billed(db, uid, 'cus_old')  # email NULL: created before #499
    assert app.worker.sync_billing_customer_emails() == {'updated': 1, 'failed': 0}
    assert stripe_emails == [('cus_old', f'{uid}@x.test')]


def test_a_refused_update_is_retried_and_stops_nobody_else(db, stripe_emails):
    ann, bob = _user(db), _user(db)
    _billed(db, ann, 'cus_ann', 'old@x.test')
    _billed(db, bob, 'cus_bob', 'old@x.test')
    stripe_emails.refused.add('cus_ann')
    assert app.worker.sync_billing_customer_emails() == {'updated': 1, 'failed': 1}
    assert stripe_emails == [('cus_bob', f'{bob}@x.test')]
    kept = db.execute('SELECT email FROM billing_customers WHERE user_id = %s', (ann,)).fetchone()['email']
    assert kept == 'old@x.test'  # not marked sent
    stripe_emails.refused.clear()
    assert app.worker.sync_billing_customer_emails() == {'updated': 1, 'failed': 0}
    assert stripe_emails[-1] == ('cus_ann', f'{ann}@x.test')


def test_the_sweep_follows_only_a_live_admin_and_never_a_saved_profile(db, stripe_emails):
    cases = {}
    for name in ('removed', 'demoted', 'banned', 'profiled', 'admin'):
        org, creator = _org(db, _user(db)), _user(db)
        _join(db, org, creator, 'admin')
        _billed(db, creator, f'cus_{name}', 'old@x.test')
        db.execute(
            'UPDATE billing_customers SET organization_id = %s WHERE stripe_customer_id = %s', (org, f'cus_{name}')
        )
        db.execute('UPDATE "user" SET email = %s WHERE id = %s', (f'{name}@new.test', creator))
        cases[name] = (org, creator)
    db.execute('DELETE FROM member WHERE "userId" = %s', (cases['removed'][1],))
    db.execute('UPDATE member SET role = \'member\' WHERE "userId" = %s', (cases['demoted'][1],))
    db.execute('UPDATE "user" SET banned = true WHERE id = %s', (cases['banned'][1],))
    db.execute(
        "INSERT INTO billing_profiles (organization_id,profile,sync_status) VALUES (%s,'{}','local')",
        (cases['profiled'][0],),
    )
    assert app.worker.sync_billing_customer_emails() == {'updated': 1, 'failed': 0}
    assert stripe_emails == [('cus_admin', 'admin@new.test')]


def test_no_stripe_key_or_no_creator_sends_nothing(db, stripe_emails, monkeypatch):
    uid = _user(db)
    _billed(db, uid, 'cus_x', 'old@x.test')
    monkeypatch.setattr(settings, 'stripe_secret_key', '')
    assert app.worker.sync_billing_customer_emails() == {'updated': 0, 'failed': 0}
    monkeypatch.setattr(settings, 'stripe_secret_key', 'sk_test')
    db.execute('UPDATE billing_customers SET user_id = NULL WHERE user_id = %s', (uid,))  # the creator was deleted
    assert app.worker.sync_billing_customer_emails() == {'updated': 0, 'failed': 0}
    assert stripe_emails == []


def test_a_new_customer_records_the_email_stripe_was_given(db, stripe_emails, monkeypatch):
    uid = _user(db)
    created: list[dict] = []

    def create(**kw):
        created.append(kw)
        return type('Customer', (), {'id': 'cus_new'})

    monkeypatch.setattr(app.billing.routes.stripe.Customer, 'create', create)
    user = app.billing.routes.CurrentUser(id=uid, org_id=_org(db, uid), email=f'{uid}@x.test')
    assert call_as(user, app.billing.routes._customer_id, user) == 'cus_new'
    assert created[0]['email'] == f'{uid}@x.test'
    assert app.worker.sync_billing_customer_emails() == {'updated': 0, 'failed': 0}  # already what Stripe has
    assert stripe_emails == []
