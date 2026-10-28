"""Invoice ownership and live organization permissions against disposable PostgreSQL."""

import pytest
import stripe
from fastapi.testclient import TestClient

import app.billing.invoices as invoices
import app.billing.routes as billing
from app.auth import middleware
from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.main import app
from tests import test_billing_admin_sql as sql_tests
from tests.test_auth import _FakeJWKS, _token
from tests.test_billing_admin_sql import _user

client = TestClient(app)
pytestmark = sql_tests.pytestmark


@pytest.fixture(autouse=True)
def jwks(monkeypatch):
    monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())


@pytest.fixture
def db(monkeypatch):
    yield from sql_tests.db.__wrapped__(monkeypatch)


@pytest.fixture
def billing_user(db, monkeypatch):
    monkeypatch.setattr(middleware, 'get_conn', billing.get_conn)
    monkeypatch.setattr(settings, 'stripe_secret_key', 'sk_test_dummy')
    uid = _user(db)
    org = str(db.execute('SELECT default_organization(%s) AS id', (uid,)).fetchone()['id'])
    user = CurrentUser(id=uid, org_id=org)
    return user


def _get(user, path='/v1/billing/invoices'):
    return client.get(path, headers={'Authorization': f'Bearer {_token(sub=user.id, org=user.org_id, role=user.role)}'})


def _customer(db, user):
    db.execute(
        'INSERT INTO billing_customers (organization_id, user_id, stripe_customer_id) VALUES (%s, %s, %s)',
        (user.org_id, user.id, 'cus_own'),
    )


def _invoice(**changes):
    return {
        'id': 'in_1',
        'customer': 'cus_own',
        'created': 1791288000,
        'number': '2026-001',
        'status': 'paid',
        'currency': 'eur',
        'total': 1220,
        'total_excluding_tax': 1000,
        'total_taxes': [{'amount': 220}],
        'invoice_pdf': 'https://invoice.stripe.com/pdf',
        'hosted_invoice_url': 'https://invoice.stripe.com/view',
        **changes,
    }


def test_no_customer_never_calls_stripe(billing_user, monkeypatch):
    monkeypatch.setattr(stripe.Invoice, 'list', lambda **kw: pytest.fail('Stripe must not be called'))
    assert _get(billing_user, '/v1/billing/invoices').json() == {
        'items': [],
        'next_cursor': None,
        'has_customer': False,
        'portal_available': False,
    }


@pytest.mark.parametrize('role', ['member', None])
def test_live_membership_required(db, billing_user, monkeypatch, role):
    _customer(db, billing_user)
    if role:
        db.execute('UPDATE member SET role = %s WHERE "userId" = %s', (role, billing_user.id))
    else:
        db.execute('DELETE FROM member WHERE "userId" = %s', (billing_user.id,))
    monkeypatch.setattr(stripe.Invoice, 'list', lambda **kw: pytest.fail('Forbidden caller reached Stripe'))
    response = _get(billing_user, '/v1/billing/invoices')
    assert response.status_code == (403 if role else 401)
    assert response.json()['detail']['code'] == ('AUTH.ORG_ADMIN_ONLY' if role else 'AUTH.NOT_A_MEMBER')


def test_foreign_active_org_denied(db, billing_user):
    foreign_uid = _user(db)
    foreign_org = str(db.execute('SELECT default_organization(%s) AS id', (foreign_uid,)).fetchone()['id'])
    foreign_user = CurrentUser(id=billing_user.id, org_id=foreign_org, role='admin')
    assert _get(foreign_user).status_code == 401
    db.execute(
        'INSERT INTO member ("userId", "organizationId", role, "createdAt") VALUES (%s,%s,\'member\',now())',
        (billing_user.id, foreign_org),
    )
    assert _get(foreign_user).status_code == 403


def test_two_admins_share_documents_and_pack_portal(db, billing_user, monkeypatch):
    _customer(db, billing_user)
    other = _user(db)
    db.execute(
        'INSERT INTO billing_customers (organization_id, user_id, stripe_customer_id) VALUES (default_organization(%s), %s, %s)',
        (other, other, 'cus_foreign'),
    )
    db.execute(
        'INSERT INTO member ("userId", "organizationId", role, "createdAt") VALUES (%s,%s,\'admin\',now())',
        (other, billing_user.org_id),
    )
    viewer = CurrentUser(id=other, org_id=billing_user.org_id)
    seen = {}

    def listing(**kw):
        seen.update(kw)
        return stripe.ListObject.construct_from({'data': [_invoice()], 'has_more': True}, 'k')

    monkeypatch.setattr(stripe.Invoice, 'list', listing)
    response = _get(viewer, '/v1/billing/invoices?limit=1').json()
    assert seen['customer'] == 'cus_own'
    assert seen['limit'] == 1
    assert response['portal_available'] is True
    assert response['next_cursor'] == 'in_1'
    assert response['items'][0]['net_amount'] == 1000
    assert response['items'][0]['tax_amount'] == 220
    assert response['items'][0]['total_amount'] == 1220

    db.execute('UPDATE organization SET slug = %s WHERE id = %s', ('billing-squad', billing_user.org_id))
    assert _get(billing_user).status_code == 200
    assert seen['customer'] == 'cus_own'

    def portal(**kw):
        seen.update(kw)
        return stripe.billing_portal.Session.construct_from({'url': 'https://billing.stripe.com/test'}, 'k')

    monkeypatch.setattr(stripe.billing_portal.Session, 'create', portal)
    result = client.post(
        '/v1/billing/portal',
        headers={
            'Authorization': f'Bearer {_token(sub=viewer.id, org=viewer.org_id)}',
        },
    )
    assert result.status_code == 200
    assert seen['customer'] == 'cus_own'
    foreign_org = str(db.execute('SELECT default_organization(%s) AS id', (other,)).fetchone()['id'])
    assert _get(CurrentUser(id=other, org_id=foreign_org)).status_code == 200
    assert seen['customer'] == 'cus_foreign'


def test_cursor_must_belong_to_customer(db, billing_user, monkeypatch):
    _customer(db, billing_user)
    monkeypatch.setattr(
        stripe.Invoice,
        'retrieve',
        lambda *a, **kw: stripe.Invoice.construct_from(_invoice(customer='cus_foreign'), 'k'),
    )
    monkeypatch.setattr(stripe.Invoice, 'list', lambda **kw: pytest.fail('Foreign cursor reached list'))
    assert _get(billing_user, '/v1/billing/invoices?starting_after=in_foreign').status_code == 400
    assert _get(billing_user, '/v1/billing/invoices?starting_after=invalid').status_code == 422
    assert _get(billing_user, '/v1/billing/invoices?limit=101').status_code == 422


def test_missing_key_and_vendor_failure(db, billing_user, monkeypatch):
    _customer(db, billing_user)
    monkeypatch.setattr(settings, 'stripe_secret_key', '')
    assert _get(billing_user, '/v1/billing/invoices').status_code == 503
    monkeypatch.setattr(settings, 'stripe_secret_key', 'sk_test_dummy')

    def failing(**kw):
        raise stripe.APIConnectionError('Provider down')

    monkeypatch.setattr(stripe.Invoice, 'list', failing)
    assert _get(billing_user, '/v1/billing/invoices').status_code == 502


def test_draft_and_unknown_net_preserved():
    document = invoices._document(_invoice(status='draft', total_excluding_tax=None, total_taxes=[]))
    assert document['net_amount'] is None
    assert document['tax_amount'] is None
    assert document['pdf_url'] is None
    assert document['hosted_url'] is None


def test_owner_portal_and_customer_cursor(db, billing_user, monkeypatch):
    _customer(db, billing_user)
    monkeypatch.setattr(stripe.Invoice, 'retrieve', lambda *a, **kw: stripe.Invoice.construct_from(_invoice(), 'k'))
    seen = {}

    def listing(**kw):
        seen.update(kw)
        return stripe.ListObject.construct_from({'data': [_invoice(id='in_2')], 'has_more': False}, 'k')

    monkeypatch.setattr(stripe.Invoice, 'list', listing)
    response = _get(billing_user, '/v1/billing/invoices?starting_after=in_1').json()
    assert seen['starting_after'] == 'in_1'
    assert seen['customer'] == 'cus_own'
    assert response['portal_available'] is True
    assert response['next_cursor'] is None
