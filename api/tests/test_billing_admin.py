"""Billing + admin routes without a DB: signature, checkout shape, admin gate."""

import hashlib
import hmac
import json
import time
from contextlib import contextmanager

import pytest
import stripe
from fastapi.testclient import TestClient

from app.core.config import CreditPack, Plan, settings
from app.main import app
from tests.test_auth import _token, fake_jwks  # noqa: F401 — autouse JWKS stub

client = TestClient(app)
SECRET = 'whsec_test'


def sign(payload: bytes, secret: str = SECRET) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f'{ts}.'.encode() + payload, hashlib.sha256).hexdigest()
    return f't={ts},v1={sig}'


@pytest.fixture(autouse=True)
def billing_settings(monkeypatch):
    monkeypatch.setattr(settings, 'stripe_webhook_secret', SECRET)
    monkeypatch.setattr(
        settings,
        'billing_packs',
        [
            CreditPack(id='starter', credits=1000, price_eur_cents=1900, stripe_price_id='price_1'),
            CreditPack(id='hidden', credits=5, price_eur_cents=100),
        ],
    )
    monkeypatch.setattr(
        settings,
        'billing_plans',
        [
            Plan(id='pro', credits=6000, price_eur_cents=7900, stripe_price_id='price_pro'),
            Plan(id='hidden', credits=5, price_eur_cents=100),
        ],
    )


def _auth(role='user'):
    return {'Authorization': f'Bearer {_token(role=role)}'}


@pytest.fixture
def session_create(monkeypatch):
    """Captures Checkout params; the user already has Stripe customer cus_1 and no plan."""
    import app.billing.routes as billing

    @contextmanager
    def locked(org):
        yield None

    monkeypatch.setattr(billing.profile, 'locked', locked)
    monkeypatch.setattr(billing.profile, 'required', lambda *a: None)
    monkeypatch.setattr(billing.profile, 'sync_customer', lambda *a: True)
    seen = {}

    def create(**kw):
        seen.update(kw)
        return stripe.checkout.Session.construct_from({'id': 'cs_1', 'url': 'https://stripe.test/cs_1'}, 'k')

    monkeypatch.setattr(stripe.checkout.Session, 'create', create)
    monkeypatch.setattr(billing, '_customer_id', lambda user: 'cus_1')
    monkeypatch.setattr(billing, '_live_subscription', lambda org_id: None)
    return seen


def test_packs_and_plans_hide_unpriced():
    assert client.get('/v1/billing/packs').json() == {
        'packs': [{'id': 'starter', 'credits': 1000, 'price_eur_cents': 1900}]
    }
    assert client.get('/v1/billing/plans').json() == {
        'plans': [{'id': 'pro', 'credits': 6000, 'price_eur_cents': 7900}]
    }


def _assert_taxed(seen):
    assert seen['customer'] == 'cus_1'
    assert seen['client_reference_id'] == 'u1'
    assert seen['automatic_tax'] == {'enabled': True}
    assert seen['tax_id_collection'] == {'enabled': False}
    assert 'customer_update' not in seen
    assert seen['success_url'].startswith(settings.web_origin)


def test_checkout_builds_taxed_payment_session(session_create):
    r = client.post('/v1/billing/checkout', json={'pack_id': 'starter'}, headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json() == {'url': 'https://stripe.test/cs_1'}
    seen = session_create
    assert seen['mode'] == 'payment'
    assert seen['line_items'] == [{'price': 'price_1', 'quantity': 1}]
    assert seen['metadata'] == {'organization_id': 'o1', 'user_id': 'u1', 'pack_id': 'starter', 'credits': '1000'}
    assert seen['invoice_creation'] == {'enabled': True}
    _assert_taxed(seen)


def test_subscribe_builds_taxed_subscription_session(session_create):
    r = client.post('/v1/billing/subscribe', json={'plan_id': 'pro'}, headers=_auth())
    assert r.status_code == 200, r.text
    seen = session_create
    assert seen['mode'] == 'subscription'
    assert seen['line_items'] == [{'price': 'price_pro', 'quantity': 1}]
    assert seen['subscription_data'] == {'metadata': {'organization_id': 'o1', 'user_id': 'u1'}}
    assert 'invoice_creation' not in seen  # subscriptions invoice natively
    _assert_taxed(seen)


def test_subscribe_refuses_second_plan(session_create, monkeypatch):
    import app.billing.routes as billing

    monkeypatch.setattr(billing, '_live_subscription', lambda org_id: {'plan_id': 'pro', 'status': 'active'})
    r = client.post('/v1/billing/subscribe', json={'plan_id': 'pro'}, headers=_auth())
    assert r.status_code == 409 and r.json()['detail']['code'] == 'BILLING.ALREADY_SUBSCRIBED'
    assert session_create == {}


def test_checkout_unknown_or_unpriced_404():
    for pack in ('nope', 'hidden'):
        assert client.post('/v1/billing/checkout', json={'pack_id': pack}, headers=_auth()).status_code == 404
        assert client.post('/v1/billing/subscribe', json={'plan_id': pack}, headers=_auth()).status_code == 404


def test_portal_needs_a_customer(monkeypatch):
    import app.billing.routes as billing

    monkeypatch.setattr(billing, '_stored_customer_id', lambda org_id: None)
    assert client.post('/v1/billing/portal', headers=_auth()).status_code == 404
    monkeypatch.setattr(billing, '_stored_customer_id', lambda org_id: 'cus_1')
    monkeypatch.setattr(
        stripe.billing_portal.Session,
        'create',
        lambda **kw: stripe.billing_portal.Session.construct_from(
            {'id': 'bps_1', 'url': f'https://stripe.test/{kw["customer"]}'}, 'k'
        ),
    )
    r = client.post('/v1/billing/portal', headers=_auth())
    assert r.status_code == 200 and r.json() == {'url': 'https://stripe.test/cus_1'}


def test_webhook_rejects_bad_signature(monkeypatch):
    import app.billing.routes as billing

    monkeypatch.setattr(billing, 'process_event', lambda e: pytest.fail('must not process'))
    payload = json.dumps({'id': 'evt_1', 'type': 'checkout.session.completed', 'data': {'object': {}}}).encode()
    for sig in ('', 't=1,v1=deadbeef', sign(payload, 'whsec_other')):
        r = client.post('/v1/billing/stripe-webhook', content=payload, headers={'stripe-signature': sig})
        assert r.status_code == 400
        assert r.json()['detail']['code'] == 'BILLING.WEBHOOK_INVALID_SIG'


def test_webhook_valid_signature_reaches_handler(monkeypatch):
    import app.billing.routes as billing

    got = []
    monkeypatch.setattr(billing, 'process_event', lambda e: got.append(e) or 'processed')
    payload = json.dumps({'id': 'evt_1', 'object': 'event', 'type': 'x', 'data': {'object': {}}}).encode()
    r = client.post('/v1/billing/stripe-webhook', content=payload, headers={'stripe-signature': sign(payload)})
    assert r.status_code == 200, r.text
    assert got[0]['id'] == 'evt_1'


ADMIN_ROUTES = [
    ('GET', '/v1/admin/costs'),
    ('POST', '/v1/admin/users/00000000-0000-0000-0000-000000000001/credits'),
    ('GET', '/v1/admin/users/00000000-0000-0000-0000-000000000001/usage'),
    ('GET', '/v1/admin/chat-sessions'),
    ('GET', '/v1/admin/chat-sessions/00000000-0000-0000-0000-000000000001'),
    ('GET', '/v1/admin/jobs'),
    ('GET', '/v1/admin/users/overview?ids=00000000-0000-0000-0000-000000000001'),
    ('GET', '/v1/admin/badges'),
    ('GET', '/v1/admin/settings'),
    ('PUT', '/v1/admin/settings'),
]


@pytest.mark.parametrize(('method', 'path'), ADMIN_ROUTES)
def test_admin_routes_forbid_non_admin(method, path):
    r = client.request(method, path, headers=_auth('user'), json={'amount': 1, 'reason': 'x', 'key': 'k', 'value': 1})
    assert r.status_code == 403
    assert client.request(method, path).status_code == 401


def test_balance_enqueue_never_touches_the_result_backend():
    # #243: apply_async on a result-recording task subscribes on the result backend first, whose socket has no
    # timeout: a hung Redis would block the charging path there.
    from app.worker import on_balance_changed

    assert on_balance_changed.ignore_result
