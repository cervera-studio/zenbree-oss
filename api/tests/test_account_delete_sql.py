"""#341: the two rules checked before any account delete (an admin's or the user's own): the last admin of a team is
kept, and an organization left without members stops billing first. Stripe is mocked at ``stripe.Subscription``."""

import psycopg
import pytest
import stripe
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.main import app
from tests.conftest import sign_in
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_creator_deleted_sql import _mailbox, _org, _team
from tests.test_dispatcher_sql import provider as provider

pytestmark = needs_db


def _before_delete(uid, org):
    sign_in(app, CurrentUser(id=uid, org_id=org))
    try:
        return TestClient(app).post('/v1/me/before-delete', json={'user_id': uid})
    finally:
        app.dependency_overrides.clear()


def _billing(db, org, uid, sub_id):
    db.execute(
        'INSERT INTO subscriptions (stripe_subscription_id, organization_id, user_id, plan_id, status, event_created)'
        " VALUES (%s, %s, %s, 'pro', 'active', 1)",
        (sub_id, org, uid),
    )
    db.execute(
        'INSERT INTO auto_topups (organization_id, user_id, enabled, pack_id, threshold) VALUES (%s, %s, true,'
        " 'starter', 100)",
        (org, uid),
    )


def _state(db, sub_id):
    return db.execute(
        'SELECT s.status, a.enabled FROM subscriptions s JOIN auto_topups a USING (organization_id)'
        ' WHERE s.stripe_subscription_id = %s',
        (sub_id,),
    ).fetchone()


def test_the_last_admin_of_a_team_is_kept_until_another_member_is_admin(db, provider, monkeypatch):
    ada, bo, org, pid = _team(db)
    box = _mailbox(db, ada, org, pid, 'ada@agency.test')
    monkeypatch.setattr(stripe.Subscription, 'cancel', lambda *a, **kw: pytest.fail('Stripe must not be called'))

    res = _before_delete(ada, org)
    assert res.status_code == 409 and res.json()['detail']['code'] == 'ACCOUNT.LAST_ADMIN'
    status = db.execute('SELECT status FROM user_oauth_connections WHERE id = %s', (box,)).fetchone()['status']
    assert status == 'connected'

    db.execute('UPDATE member SET role = \'admin\' WHERE "userId" = %s AND "organizationId" = %s', (bo, org))
    assert _before_delete(ada, org).status_code == 200


def test_a_sole_member_cancels_their_organizations_billing_and_not_a_teams(db, monkeypatch):
    ada, bo, org, _ = _team(db)
    own = _org(db, bo)
    _billing(db, own, bo, 'sub_own')
    _billing(db, org, ada, 'sub_team')
    cancelled = []

    def cancel(sub_id, **kw):
        cancelled.append(sub_id)
        return stripe.Subscription.construct_from({'id': sub_id, 'status': 'active'}, 'k')  # stripe-mock's canned reply

    monkeypatch.setattr(stripe.Subscription, 'cancel', cancel)
    res = _before_delete(bo, org)
    assert res.status_code == 200 and res.json() == {'released': 0, 'cancelled_subscriptions': 1}
    assert cancelled == ['sub_own']
    assert _state(db, 'sub_own') == {'status': 'canceled', 'enabled': False}
    assert _state(db, 'sub_team') == {'status': 'active', 'enabled': True}


def test_a_failed_cancel_deletes_nothing(db, provider, monkeypatch):
    _, bo, org, pid = _team(db)
    own = _org(db, bo)
    _billing(db, own, bo, 'sub_own')
    box = _mailbox(db, bo, org, pid, 'bo@agency.test')

    def cancel(sub_id, **kw):
        raise stripe.APIConnectionError('down')

    monkeypatch.setattr(stripe.Subscription, 'cancel', cancel)
    res = _before_delete(bo, org)
    assert res.status_code == 502 and res.json()['detail']['code'] == 'ACCOUNT.BILLING_CANCEL_FAILED'
    assert _state(db, 'sub_own') == {'status': 'active', 'enabled': True}
    status = db.execute('SELECT status FROM user_oauth_connections WHERE id = %s', (box,)).fetchone()['status']
    assert status == 'connected'


@pytest.mark.parametrize(
    ('cancel_code', 'retrieved', 'deletes'),
    [
        ('resource_missing', None, True),  # gone at Stripe
        (None, 'canceled', True),  # already over: the webhook never reached us
        (None, 'incomplete_expired', True),
        (None, 'active', False),  # refused for another reason: still billing, nothing deleted
    ],
)
def test_a_subscription_already_over_at_stripe_does_not_lock_the_user_out(
    db, monkeypatch, cancel_code, retrieved, deletes
):
    _, bo, org, _ = _team(db)
    _billing(db, _org(db, bo), bo, 'sub_own')

    def cancel(sub_id, **kw):
        raise stripe.InvalidRequestError('No such subscription', 'id', code=cancel_code)

    monkeypatch.setattr(stripe.Subscription, 'cancel', cancel)
    monkeypatch.setattr(
        stripe.Subscription,
        'retrieve',
        lambda sub_id, **kw: stripe.Subscription.construct_from({'id': sub_id, 'status': retrieved}, 'k'),
    )
    res = _before_delete(bo, org)
    if deletes:
        assert res.status_code == 200
        assert _state(db, 'sub_own') == {'status': 'canceled', 'enabled': False}
    else:
        assert res.status_code == 502 and res.json()['detail']['code'] == 'ACCOUNT.BILLING_CANCEL_FAILED'
        assert _state(db, 'sub_own') == {'status': 'active', 'enabled': True}


def test_a_subscription_missing_at_retrieve_counts_as_over(db, monkeypatch):
    _, bo, org, _ = _team(db)
    _billing(db, _org(db, bo), bo, 'sub_own')

    def cancel(sub_id, **kw):
        raise stripe.InvalidRequestError('Cannot cancel', 'id')

    def retrieve(sub_id, **kw):
        raise stripe.InvalidRequestError('No such subscription', 'id', code='resource_missing')

    monkeypatch.setattr(stripe.Subscription, 'cancel', cancel)
    monkeypatch.setattr(stripe.Subscription, 'retrieve', retrieve)
    assert _before_delete(bo, org).status_code == 200
    assert _state(db, 'sub_own') == {'status': 'canceled', 'enabled': False}


def test_the_own_route_refuses_a_token_for_another_account(db):
    ada, bo, org, _ = _team(db)
    sign_in(app, CurrentUser(id=bo, org_id=org))
    try:
        res = TestClient(app).post('/v1/me/before-delete', json={'user_id': ada})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 403 and res.json()['detail']['code'] == 'ACCOUNT.NOT_YOURS'


def test_the_database_refuses_a_delete_that_skipped_the_rules(db):
    """The trigger: a last admin, and a sole member still billing, survive a plain DELETE."""
    ada, bo, _, _ = _team(db)
    _billing(db, _org(db, bo), bo, 'sub_own')
    for uid, rule in ((ada, 'ACCOUNT.LAST_ADMIN'), (bo, 'ACCOUNT.STILL_BILLING')):
        with pytest.raises(psycopg.errors.RaiseException, match=rule), db.transaction():
            db.execute('DELETE FROM "user" WHERE id = %s', (uid,))

    db.execute("UPDATE subscriptions SET status = 'canceled' WHERE stripe_subscription_id = 'sub_own'")
    db.execute('UPDATE auto_topups SET enabled = false')
    db.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    db.execute('DELETE FROM "user" WHERE id = %s', (ada,))  # alone in the team now
