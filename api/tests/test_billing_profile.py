"""Fiscal validation, native provider mapping and live organization guards."""

from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import middleware
from app.auth.middleware import CurrentUser
from app.billing import profile, routes
from app.core.config import CreditPack, Plan, settings
from app.main import app
from tests import test_billing_admin_sql as sql
from tests.test_auth import _FakeJWKS, _token

client = TestClient(app)


def individual():
    return {
        'customer_type': 'individual',
        'name': 'Synthetic Customer',
        'email': 'customer@example.com',
        'address': {'country': 'IT', 'state': 'MI', 'city': 'Milano', 'postal_code': '20100', 'line1': 'Via Test 1'},
        'fiscal_code': 'RSSMRA80A01F205X',
    }


def business(country='IT', kind='eu_vat', value='IT12345678901'):
    data = individual()
    data.update(customer_type='business', tax_id_type=kind, tax_id_value=value, fiscal_code='')
    data['address']['country'] = country
    return data


@pytest.mark.parametrize(
    'changes',
    [
        {'fiscal_code': 'AAAAAAAAAAAAAAAA'},
        {'fiscal_code': ''},
        {'name': ''},
        {'email': 'invalid'},
        {'recipient_code': 'too-long'},
        {'pec': 'invalid'},
    ],
)
def test_invalid_profile(changes):
    with pytest.raises(ValidationError):
        profile.BillingProfile.model_validate(individual() | changes)


def test_country_address_and_native_formats():
    assert len(profile.COUNTRIES) == 249
    for country in ['ZZ', 'XX']:
        data = individual()
        data['address']['country'] = country
        with pytest.raises(ValidationError):
            profile.BillingProfile.model_validate(data)
    data = business('CH', 'ch_vat', 'CHE-123.456.789 MWST')
    assert profile.BillingProfile.model_validate(data).tax_id_value == 'CHE-123.456.789 MWST'
    assert profile.BillingProfile.model_validate(business(value='12345678901')).tax_id_value == 'IT12345678901'
    for data in [business('FR', value='IT12345678901'), business('AQ'), business(kind='it_cf')]:
        with pytest.raises(ValidationError):
            profile.BillingProfile.model_validate(data)
    for country in ['AE', 'HK']:
        data = individual()
        data['address'].update(country=country, postal_code='', state='')
        data['fiscal_code'] = 'local-id-123'
        assert profile.BillingProfile.model_validate(data).address.postal_code == ''
    assert 'eu_vat' in profile.tax_types('GB')
    assert profile.BillingProfile.model_validate(business('GB', value='XI123456789')).tax_id_value == 'XI123456789'


def test_sync_replaces_tax_ids_and_preserves_non_eu_punctuation(monkeypatch):
    monkeypatch.setattr(settings, 'stripe_secret_key', 'sk_test_dummy')
    seen = []
    ids = [SimpleNamespace(id='old', type='eu_vat', value='IT00000000000')]
    monkeypatch.setattr(stripe.Customer, 'modify', lambda customer, **kw: seen.append(('modify', kw)))
    monkeypatch.setattr(
        stripe.Customer, 'list_tax_ids', lambda *a, **kw: SimpleNamespace(auto_paging_iter=lambda: iter(ids))
    )
    monkeypatch.setattr(stripe.Customer, 'delete_tax_id', lambda customer, tid, **kw: seen.append(('delete', tid)))
    monkeypatch.setattr(stripe.Customer, 'create_tax_id', lambda customer, **kw: seen.append(('create', kw)))
    profile._sync_customer('cus_test', profile.BillingProfile.model_validate(business()))
    assert [x[0] for x in seen] == ['modify', 'delete', 'create']
    assert seen[0][1]['metadata'] == {'fiscal_code': '', 'codice_destinatario': '0000000'}
    assert 'tax_exempt' not in seen[0][1]
    seen.clear()
    ids[:] = [SimpleNamespace(id='same', type='eu_vat', value='IT12345678901')]
    profile._sync_customer('cus_test', profile.BillingProfile.model_validate(business()))
    assert [x[0] for x in seen] == ['modify']
    seen.clear()
    profile._sync_customer(
        'cus_test', profile.BillingProfile.model_validate(individual() | {'pec': 'contact@example.com'})
    )
    assert [x[0] for x in seen] == ['modify', 'delete']
    assert seen[0][1]['metadata']['fiscal_code'] == individual()['fiscal_code']
    assert 'pec' not in seen[0][1]['metadata']


@pytest.fixture
def db(monkeypatch):
    if not sql.DB_URL:
        pytest.skip('TEST_DATABASE_URL required for fiscal-profile SQL tests')
    yield from sql.db.__wrapped__(monkeypatch)


@pytest.fixture
def user(db, monkeypatch):
    monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())
    monkeypatch.setattr(middleware, 'get_conn', routes.get_conn)
    monkeypatch.setattr(
        settings,
        'billing_packs',
        [CreditPack(id='starter', credits=1000, price_eur_cents=1000, stripe_price_id='price_test')],
    )
    monkeypatch.setattr(
        settings, 'billing_plans', [Plan(id='pro', credits=6000, price_eur_cents=2900, stripe_price_id='price_plan')]
    )
    uid = sql._user(db)
    org = sql._org(db, uid)
    return CurrentUser(id=uid, org_id=org)


def auth(user):
    return {'Authorization': f'Bearer {_token(sub=user.id, org=user.org_id)}'}


def test_profile_saved_without_provider_and_shared_admin(db, user):
    assert client.get('/v1/billing/profile', headers=auth(user)).json()['profile'] is None
    response = client.put('/v1/billing/profile', json=individual(), headers=auth(user))
    assert response.status_code == 200, response.text
    assert response.json()['sync_status'] == 'local'
    assert client.get('/v1/billing/profile', headers=auth(user)).json()['profile']['name'] == 'Synthetic Customer'
    uid = sql._user(db)
    db.execute(
        'INSERT INTO member ("userId","organizationId",role,"createdAt") VALUES (%s,%s,\'admin\',now())',
        (uid, user.org_id),
    )
    assert (
        client.get('/v1/billing/profile', headers=auth(CurrentUser(id=uid, org_id=user.org_id))).json()['profile']
        == response.json()['profile']
    )
    assert (
        client.get('/v1/billing/profile', headers=auth(CurrentUser(id=uid, org_id=sql._org(db, uid)))).json()['profile']
        is None
    )
    for role, status in [('member', 403), (None, 401)]:
        if role:
            db.execute(
                'UPDATE member SET role=%s WHERE "userId"=%s AND "organizationId"=%s', (role, user.id, user.org_id)
            )
        else:
            db.execute('DELETE FROM member WHERE "userId"=%s AND "organizationId"=%s', (user.id, user.org_id))
        assert client.put('/v1/billing/profile', json=individual(), headers=auth(user)).status_code == status


def test_invalid_response_redacts_input(user):
    data = individual() | {'email': 'private-invalid-PII', 'fiscal_code': 'secret-PII'}
    response = client.put('/v1/billing/profile', json=data, headers=auth(user))
    assert response.status_code == 422
    assert 'PII' not in response.text
    assert set(response.json()['detail']) == {'code', 'message'}


def test_missing_profile_blocks_payments_but_disable_allowed(user, monkeypatch):
    monkeypatch.setattr(stripe.checkout.Session, 'create', lambda **kw: pytest.fail('No provider call before profile'))
    monkeypatch.setattr(routes, '_live_subscription', lambda org: None)
    for path, data in [
        ('/checkout', {'pack_id': 'starter'}),
        ('/subscribe', {'plan_id': 'pro'}),
        ('/auto-topup', {'enabled': True, 'pack_id': 'starter', 'threshold': 100}),
    ]:
        method = client.put if path == '/auto-topup' else client.post
        response = method('/v1/billing' + path, json=data, headers=auth(user))
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'BILLING.PROFILE_REQUIRED'
    monkeypatch.setattr(routes.balance, 'changed', lambda org: None)
    assert (
        client.put(
            '/v1/billing/auto-topup',
            json={'enabled': False, 'pack_id': 'starter', 'threshold': 100},
            headers=auth(user),
        ).status_code
        == 200
    )


@pytest.mark.parametrize('caller', ['save', 'checkout', 'setup'])
def test_partial_sync_failure_persists_pending_and_disables_topup(db, user, monkeypatch, caller):
    client.put('/v1/billing/profile', json=individual(), headers=auth(user))
    db.execute(
        "INSERT INTO billing_customers (organization_id,user_id,stripe_customer_id) VALUES (%s,%s,'cus_test')",
        (user.org_id, user.id),
    )
    db.execute(
        "INSERT INTO auto_topups (organization_id,user_id,enabled,awaiting_card,pack_id,threshold) VALUES (%s,%s,true,true,'starter',100)",
        (user.org_id, user.id),
    )
    monkeypatch.setattr(settings, 'stripe_secret_key', 'sk_test_dummy')

    def failing(*args):
        raise stripe.APIConnectionError('private fiscal_code address must never appear in response')

    monkeypatch.setattr(profile, '_sync_customer', failing)
    monkeypatch.setattr(
        stripe.checkout.Session, 'create', lambda **kw: pytest.fail('Unsynced profile reached checkout')
    )
    if caller == 'save':
        response = client.put('/v1/billing/profile', json=individual(), headers=auth(user))
    elif caller == 'checkout':
        response = client.post('/v1/billing/checkout', json={'pack_id': 'starter'}, headers=auth(user))
    else:
        response = client.put(
            '/v1/billing/auto-topup', json={'enabled': True, 'pack_id': 'starter', 'threshold': 100}, headers=auth(user)
        )
    assert response.status_code == 502, response.text
    assert response.json()['detail']['code'] == 'BILLING.PROFILE_SYNC_FAILED'
    assert 'private' not in response.text
    assert (
        db.execute('SELECT sync_status FROM billing_profiles WHERE organization_id=%s', (user.org_id,)).fetchone()[
            'sync_status'
        ]
        == 'pending'
    )
    row = db.execute(
        'SELECT enabled,awaiting_card FROM auto_topups WHERE organization_id=%s', (user.org_id,)
    ).fetchone()
    assert row == {'enabled': False, 'awaiting_card': False}
    monkeypatch.setattr(profile, '_sync_customer', lambda *a: None)
    assert client.put('/v1/billing/profile', json=individual(), headers=auth(user)).json()['sync_status'] == 'synced'
    assert not db.execute('SELECT enabled FROM auto_topups WHERE organization_id=%s', (user.org_id,)).fetchone()[
        'enabled'
    ]
