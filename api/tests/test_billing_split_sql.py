"""The balance split into plan and purchased credits, and the ledger's links to chats and lists (#348)."""

from datetime import UTC, datetime

from tests.conftest import project
from tests.test_billing_admin_sql import (
    _auth,
    _customer,
    _invoice_event,
    _org,
    _paid_event,
    _post_event,
    _spend,
    _subscription_event,
    _user,
    client,
)
from tests.test_billing_admin_sql import (
    db as db,
)
from tests.test_billing_admin_sql import (
    plans as plans,
)


def test_me_splits_plan_credits_from_purchased_ones(db, plans):  # pytest fixtures, requested by name
    uid = _user(db)
    me = lambda: client.get('/v1/me', headers=_auth(db, uid)).json()  # noqa: E731
    assert (me()['plan_credits_left'], me()['purchased_credits']) == (None, 50)

    _customer(db, uid)
    assert _post_event(_invoice_event('evt_1', 'in_1', reason='subscription_create')).status_code == 200
    assert _post_event(_subscription_event('evt_2', 100, typ='created')).status_code == 200
    got = me()
    assert (got['plan_credits_left'], got['purchased_credits']) == (6000, 50)
    assert datetime.fromisoformat(got['plan_credits_reset_at']) == datetime.fromtimestamp(1800000000, UTC)

    _spend(db, uid, 100)  # the allowance goes first
    _post_event(_paid_event(uid, evt='evt_9', session='cs_9'))  # 1000 bought
    got = me()
    assert (got['plan_credits_left'], got['purchased_credits']) == (5900, 1050)


def test_transactions_link_to_the_organizations_chat_and_list_only(db):  # pytest fixture, requested by name
    uid, other = _user(db), _user(db)
    org, pid = _org(db, uid), project(db, uid)
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (%s, %s, %s) RETURNING id::text',
        (org, pid, uid),
    ).fetchone()['id']
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'L', 'company')"
        ' RETURNING id::text',
        (org, pid, uid),
    ).fetchone()['id']
    foreign = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (%s, %s, %s, 'F', 'company')"
        ' RETURNING id::text',
        (_org(db, other), project(db, other), other),
    ).fetchone()['id']
    trashed = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity, deleted_at)'
        " VALUES (%s, %s, %s, 'T', 'company', now()) RETURNING id::text",
        (org, pid, uid),
    ).fetchone()['id']
    sql = "SELECT deduct_credits(%s, %s, 1, %s, %s, NULL, %s, 'x')"
    db.execute(sql, (org, uid, 'chat', 'r1', sid))
    db.execute(sql, (org, uid, 'ai_column', f'{lid}:col', None))
    db.execute(sql, (org, uid, 'ai_column', f'{foreign}:col', None))
    db.execute(sql, (org, uid, 'ai_column', f'{trashed}:col', None))
    items = client.get('/v1/billing/transactions', headers=_auth(db, uid)).json()['items']
    assert [(i['session_id'], i['list_id']) for i in reversed(items)] == [
        (sid, None),
        (None, lid),
        (None, None),
        (None, None),
    ]
