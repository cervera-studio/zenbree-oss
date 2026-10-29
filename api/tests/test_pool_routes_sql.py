"""A campaign's sending accounts, picked by id (#935): ``GET`` and ``PUT /v1/campaigns/{id}/senders``, activation, the
preview and the approval card that read them, and the accounts a new campaign starts with.

Needs TEST_DATABASE_URL.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.outreach import drafts, pool_routes, routes, sender_pool
from tests.conftest import member, migrated_database, pool_on, project, scoped_conn, sender, sign_in
from tests.test_campaign_routes_sql import DB_URL, STEPS, _list
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: pool routes SQL test NOT run')

GMAIL_SEND = 'https://www.googleapis.com/auth/gmail.send'


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (routes, pool_routes):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
            monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
            monkeypatch.setattr('app.notifications.service.get_conn', scoped_conn(c))
            yield c
        finally:
            app.dependency_overrides.clear()


def _as(c, uid) -> TestClient:
    sign_in(app, member(c, uid))
    return TestClient(app)


def _account(c, uid, provider='google', label='a@acme.test', **cols) -> str:
    return sender(
        c,
        uid,
        provider,
        granted_scopes=[GMAIL_SEND],
        account_label=label,
        created_at=datetime.now(UTC) - timedelta(days=30),
        **cols,
    )


def _campaign(c, uid, status='draft', people=('anna@client.test',)) -> str:
    cid = c.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps, activated_at)'
        " VALUES (default_organization(%s), %s, %s, 'C', %s, %s, CASE WHEN %s <> 'draft' THEN now() END) RETURNING id",
        (uid, project(c, uid), uid, status, json.dumps(STEPS), status),
    ).fetchone()['id']
    for addr in people:
        c.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status)'
            " VALUES (%s, %s, %s, 'Anna', 'Client', %s)",
            (cid, uid, addr, 'draft' if status == 'draft' else 'active'),
        )
    return str(cid)


def _pool(c, cid) -> set[str]:
    rows = c.execute('SELECT sender_id FROM outreach_campaign_senders WHERE campaign_id = %s', (cid,))
    return {str(r['sender_id']) for r in rows}


def _senders(client, cid) -> dict[str, dict]:
    got = client.get(f'/v1/campaigns/{cid}/senders')
    assert got.status_code == 200, got.text
    return {s['id']: s for s in got.json()['senders']}


def _pick(client, cid, ids):
    return client.put(f'/v1/campaigns/{cid}/senders', json={'sender_ids': list(ids)})


def test_the_pool_is_the_accounts_picked_by_id(db):
    uid = _user(db)
    a = _account(db, uid)
    m = _account(db, uid, 'microsoft', 'm@acme.test')
    cid = _campaign(db, uid)
    client = _as(db, uid)

    put = _pick(client, cid, [m])
    assert put.status_code == 200, put.text
    assert {s['id']: s['picked'] for s in put.json()['senders']} == {a: False, m: True}
    assert _pool(db, cid) == {m}
    assert {i: s['picked'] for i, s in _senders(client, cid).items()} == {a: False, m: True}

    assert _pick(client, cid, [a, m, a]).status_code == 200
    assert _pool(db, cid) == {a, m}
    assert _pick(client, cid, []).status_code == 200
    assert _pool(db, cid) == set()


def test_each_account_shows_its_state_cap_and_usage(db):
    uid = _user(db)
    a = _account(db, uid, daily_limit=30)
    young = _account(db, uid, 'microsoft', 'm@acme.test')
    db.execute("UPDATE user_oauth_connections SET created_at = now() - interval '2 days' WHERE id = %s", (young,))
    cid = _campaign(db, uid)
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, subject, body,'
        ' status, sender_id, attempted_at, delivered_at, idempotency_key)'
        " SELECT campaign_id, owner_id, id, 1, 'email', email, 's', 'b', 'sent', %s, now(), now(), id::text"
        ' FROM outreach_campaign_targets',
        (a,),
    )

    shown = _senders(_as(db, uid), cid)

    assert {k: shown[a][k] for k in ('provider', 'sent_today', 'daily_cap', 'warming_up', 'blocker')} == {
        'provider': 'google',
        'sent_today': 1,
        'daily_cap': 30,
        'warming_up': False,
        'blocker': None,
    }
    assert (shown[young]['daily_cap'], shown[young]['warming_up']) == (10, True)


def test_an_account_outside_the_client_is_refused(db):
    uid, stranger = _user(db), _user(db)
    a = _account(db, uid)
    org = member(db, uid).org_id
    other_client = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other') RETURNING id", (org, uid)
    ).fetchone()['id']
    elsewhere = _account(db, uid, label='b@acme.test', projects=[other_client])
    foreign = _account(db, stranger, label='c@acme.test')
    linkedin = sender(db, uid, 'unipile', account_label='Anna on LinkedIn')
    cid = _campaign(db, uid)
    client = _as(db, uid)

    for sid in (elsewhere, foreign, linkedin, str(uuid.uuid4())):
        refused = _pick(client, cid, [a, sid])
        assert refused.status_code == 422, (sid, refused.text)
        assert refused.json()['detail']['code'] == 'CAMPAIGN.SENDER_NOT_IN_PROJECT'
    assert _pool(db, cid) == set()
    assert set(_senders(client, cid)) == {a}


def test_a_pool_holds_at_most_twenty_accounts(db):
    uid = _user(db)
    a = _account(db, uid)
    cid = _campaign(db, uid)
    client = _as(db, uid)
    assert _pick(client, cid, [a] * 20).status_code == 200

    refused = _pick(client, cid, [a] * 21)

    assert refused.status_code == 422
    assert refused.json()['detail']['code'] == 'REQUEST.INVALID_INPUT'


@pytest.mark.parametrize('status', ['active', 'paused'])
def test_a_live_campaign_keeps_an_account_that_can_send(db, status):
    uid = _user(db)
    a = _account(db, uid)
    m = _account(db, uid, 'microsoft', 'm@acme.test', paused=True)
    cid = _campaign(db, uid, status)
    client = _as(db, uid)
    assert _pick(client, cid, [a, m]).status_code == 200

    refused = _pick(client, cid, [m])
    assert refused.status_code == 422
    assert refused.json()['detail']['code'] == 'CAMPAIGN.NO_SENDER'
    assert _pool(db, cid) == {a, m}
    assert _pick(client, cid, [a]).status_code == 200
    db.execute("UPDATE outreach_campaigns SET status = 'draft'")
    assert _pick(client, cid, [m]).status_code == 200


@pytest.mark.parametrize('status', ['stopped', 'completed'])
def test_a_finished_campaign_cannot_be_edited(db, status):
    uid = _user(db)
    a = _account(db, uid)
    cid = _campaign(db, uid, status)

    refused = _pick(_as(db, uid), cid, [a])

    assert refused.status_code == 409
    assert refused.json()['detail']['code'] == 'CAMPAIGN.INVALID_TRANSITION'
    assert _pool(db, cid) == set()


def test_activation_with_a_blocked_pool_fails_and_the_pool_shows_each_blocker(db):
    uid = _user(db)
    a = _account(db, uid, paused=True)
    m = _account(db, uid, 'microsoft', 'm@acme.test', status='error')
    cid = _campaign(db, uid)
    client = _as(db, uid)
    assert _pick(client, cid, [a, m]).status_code == 200

    refused = client.post(f'/v1/campaigns/{cid}/activate')

    assert refused.status_code == 422
    assert refused.json()['detail'] == {
        'code': 'CAMPAIGN.NO_SENDER',
        'message': 'pick a connected mailbox to send from',
    }
    assert {i: s['blocker'] for i, s in _senders(client, cid).items()} == {a: 'paused', m: 'needs_reconnect'}
    db.execute('UPDATE user_oauth_connections SET paused = false WHERE id = %s', (a,))
    assert client.post(f'/v1/campaigns/{cid}/activate').status_code == 200


def test_a_resting_or_capped_account_does_not_block_activation(db):
    uid = _user(db)
    a = _account(db, uid, last_used_at=datetime.now(UTC) + timedelta(minutes=40))
    cid = _campaign(db, uid)
    client = _as(db, uid)
    assert _pick(client, cid, [a]).status_code == 200

    shown = _senders(client, cid)[a]

    assert (shown['state'], shown['reason'], shown['blocker']) == ('held', 'throttled', None)
    assert client.post(f'/v1/campaigns/{cid}/activate').status_code == 200


def test_a_disconnected_account_shows_only_while_the_campaign_has_it_picked(db):
    uid = _user(db)
    a = _account(db, uid)
    held = _account(db, uid, label='held@acme.test', status='disconnected')
    gone = _account(db, uid, label='gone@acme.test', status='disconnected')
    cid = _campaign(db, uid)
    sender_pool.pick(db, cid, [a, held])
    client = _as(db, uid)

    shown = _senders(client, cid)
    assert set(shown) == {a, held}
    assert (shown[held]['picked'], shown[held]['blocker']) == (True, 'disconnected')

    refused = _pick(client, cid, [a, gone])
    assert refused.status_code == 422 and refused.json()['detail']['code'] == 'CAMPAIGN.SENDER_NOT_IN_PROJECT'
    assert _pick(client, cid, [a]).status_code == 200
    assert set(_senders(client, cid)) == {a}


def test_the_preview_and_the_approval_card_name_the_account_the_pool_gives(db):
    uid = _user(db)
    a = _account(db, uid, sign_off='Anna A')
    m = _account(db, uid, 'microsoft', 'm@acme.test', sign_off='Mario M')
    cid = _campaign(db, uid, 'active', people=('anna@client.test', 'bea@client.test'))
    db.execute(
        "UPDATE outreach_campaign_targets SET sender_id = %s, sender_pinned_at = now() WHERE email = 'bea@client.test'",
        (m,),
    )
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, subject, body,'
        " status, idempotency_key) SELECT campaign_id, owner_id, id, 1, 'email', email, 's', 'b', 'awaiting_approval', id::text"
        ' FROM outreach_campaign_targets',
    )
    client = _as(db, uid)

    def cards() -> dict[str, str | None]:
        items = client.get(f'/v1/campaigns/{cid}/approvals').json()['items']
        return {i['recipient']: (i['sender'] or {}).get('address') for i in items}

    def sign_offs() -> list[str]:
        items = client.get(f'/v1/campaigns/{cid}/preview').json()['items']
        return [i['steps'][0]['sign_off'] for i in items]

    assert _pick(client, cid, [a]).status_code == 200
    assert cards() == {'anna@client.test': 'a@acme.test', 'bea@client.test': 'm@acme.test'}
    assert sign_offs() == ['Anna A', 'Mario M']
    assert _pick(client, cid, [m]).status_code == 200
    assert cards() == {'anna@client.test': 'm@acme.test', 'bea@client.test': 'm@acme.test'}
    assert sign_offs() == ['Mario M', 'Mario M']


def test_a_new_campaign_starts_with_the_clients_connected_accounts(db):
    uid = _user(db)
    a = _account(db, uid)
    _account(db, uid, 'microsoft', 'm@acme.test', status='disconnected')
    lid = _list(db, uid, rows=[{'first_name': 'Anna', 'email': 'anna@client.test', 'company_name': 'Client'}])
    client = _as(db, uid)

    cid = client.post('/v1/campaigns', json={'name': 'Spring', 'list_id': lid}).json()['id']

    assert {i: s['picked'] for i, s in _senders(client, cid).items()} == {a: True}
    assert client.patch(f'/v1/campaigns/{cid}', json={'steps': STEPS}).status_code == 200
    assert _pick(client, cid, []).status_code == 200
    refused = client.post(f'/v1/campaigns/{cid}/activate')
    assert refused.json()['detail']['code'] == 'CAMPAIGN.NO_SENDER'
    with db.transaction():
        drafts.draft_from_list(db, uid, lid, 'Spring', reuse=True)
    assert _pool(db, cid) == set()
