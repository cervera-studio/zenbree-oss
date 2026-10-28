from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.errors import is_err
from app.core.scope import acting_for
from app.integrations import project_senders, senders
from app.main import app
from app.outreach import dispatcher, routes, send_queue
from app.outreach.target_rows import read_targets
from tests import test_dispatcher_sql
from tests.conftest import call_as, member, scoped_conn, sign_in
from tests.test_dispatcher_sql import DB_URL, NOW, TWO_STEPS, _campaign, _each_org, _mailbox
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
switches = test_dispatcher_sql.switches
provider = test_dispatcher_sql.provider

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: sender holds SQL test NOT run')


@pytest.fixture(autouse=True)
def faked(provider, switches): ...


ALWAYS_OPEN = {'send_window_start_hour': 0, 'send_window_end_hour': 24, 'send_window_days': [0, 1, 2, 3, 4, 5, 6]}


def _account(c, uid, name: str, **cols) -> str:
    return _mailbox(c, uid, account_label=f'{name}@acme.test', provider_account_id=f'sub-{name}', **ALWAYS_OPEN, **cols)


def _always_open(c, campaign_id: str) -> None:
    c.execute(
        'UPDATE outreach_campaigns SET send_window_start_hour = 0, send_window_end_hour = 24,'
        ' send_window_days = %s WHERE id = %s',
        (ALWAYS_OPEN['send_window_days'], campaign_id),
    )


def _unpool(c, campaign_id: str, account: str) -> None:
    c.execute('DELETE FROM outreach_campaign_senders WHERE campaign_id = %s AND sender_id = %s', (campaign_id, account))


def _unlink(c, account: str) -> None:
    c.execute('DELETE FROM project_senders WHERE sender_id = %s', (account,))


PEOPLE = {
    'uncertain': 'ready',
    'unknown': 'ready',
    'gone': 'gone',
    'revoked': 'revoked',
    'unlinked': 'unlinked',
    'outside': 'outside',
    'paused': 'paused',
    'throttled': 'throttled',
    'capped': 'capped',
    'free': 'ready',
}
HELD = {
    'uncertain': 'send_uncertain',
    'unknown': 'mailbox_unknown',
    'gone': 'disconnected',
    'revoked': 'needs_reconnect',
    'unlinked': 'unlinked',
    'outside': 'not_in_pool',
    'paused': 'paused',
    'throttled': 'throttled',
}


def _conversations(c) -> tuple[str, dict[str, str], dict[str, str]]:
    uid = _user(c)
    accounts = {
        name: _account(c, uid, name, **cols)
        for name, cols in {
            'ready': {},
            'gone': {'status': 'disconnected'},
            'revoked': {'status': 'error'},
            'unlinked': {},
            'outside': {},
            'paused': {'paused': True},
            'throttled': {'last_used_at': NOW + timedelta(hours=1)},
            'capped': {'daily_limit': 1},
        }.items()
    }
    emails = [f'{p}@client.test' for p in (*PEOPLE, 'new', 'today')]
    cid = _campaign(c, uid, TWO_STEPS, emails=tuple(emails))
    _always_open(c, cid)
    targets = {
        p: _heard_step_one(c, f'{p}@client.test', accounts[a], NOW - timedelta(days=4)) for p, a in PEOPLE.items()
    }
    _heard_step_one(c, 'today@client.test', accounts['capped'], NOW - timedelta(minutes=30))
    c.execute('UPDATE outreach_campaign_targets SET sender_id = NULL WHERE id = %s', (targets['unknown'],))
    _unlink(c, accounts['unlinked'])
    _unpool(c, cid, accounts['outside'])
    _unpool(c, cid, accounts['gone'])
    _each_org(dispatcher.plan, NOW)
    c.execute(
        "UPDATE outreach_campaign_sends SET status = 'sending', attempts = 1, attempted_at = %s"
        " WHERE target_id = %s AND step = 2 AND status = 'approved'",
        (NOW - timedelta(minutes=10), targets['uncertain']),
    )
    targets['new'] = str(
        c.execute("SELECT id FROM outreach_campaign_targets WHERE email = 'new@client.test'").fetchone()['id']
    )
    return cid, targets, accounts


def _people(c, cid: str, now: datetime = NOW) -> dict[str, dict]:
    campaign = c.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()
    with acting_for(str(campaign['organization_id']), 'test'), scoped_conn(c)() as conn:
        return {str(r['id']): r for r in read_targets(conn, campaign, 1000, now)}


def _queues(c) -> list[send_queue.Mailbox]:
    org = c.execute('SELECT organization_id FROM outreach_campaigns').fetchone()['organization_id']
    with acting_for(str(org), 'campaign beat'), scoped_conn(c)() as conn:
        return send_queue.mailboxes(conn, NOW, 100)


def test_each_held_conversation_says_why_and_names_its_account(db):
    cid, targets, _ = _conversations(db)

    people = _people(db, cid)

    shown = {p: (people[targets[p]]['sender_hold'], people[targets[p]]['sender']) for p in HELD}
    assert shown == {
        'uncertain': ('send_uncertain', 'ready@acme.test'),
        'unknown': ('mailbox_unknown', None),
        'gone': ('disconnected', 'gone@acme.test'),
        'revoked': ('needs_reconnect', 'revoked@acme.test'),
        'unlinked': ('unlinked', 'unlinked@acme.test'),
        'outside': ('not_in_pool', 'outside@acme.test'),
        'paused': ('paused', 'paused@acme.test'),
        'throttled': ('throttled', 'throttled@acme.test'),
    }
    assert (people[targets['free']]['sender_hold'], people[targets['free']]['sender']) == (None, 'ready@acme.test')
    assert (people[targets['new']]['sender_hold'], people[targets['new']]['sender']) == (None, None)


def test_the_queue_gives_a_held_conversation_to_no_other_account_and_a_free_one_to_its_own(db):
    cid, targets, accounts = _conversations(db)
    people = _people(db, cid)

    boxes = _queues(db)

    queued = {str(b.row['id']): {str(s['target_id']) for s in b.queue} for b in boxes}
    ready = {str(b.row['id']) for b in boxes if b.state.state == 'ready'}
    assert ready == {accounts['ready']}
    for name, target in targets.items():
        own = accounts.get(PEOPLE.get(name, ''))
        holders = {account for account, rows in queued.items() if target in rows}
        if people[target]['sender_hold']:
            assert holders <= {own} and not holders & ready, f'{name} is held but queued for {holders}'
        else:
            assert holders & ({own} if own else ready), f'{name} is free but in no queue it may send from'


def test_a_paused_account_holds_its_conversation_and_a_capped_one_makes_it_wait(db):
    cid, targets, accounts = _conversations(db)

    people, boxes = _people(db, cid), {str(b.row['id']): b for b in _queues(db)}

    assert people[targets['paused']]['sender_hold'] == 'paused'
    assert people[targets['capped']]['sender_hold'] is None
    capped = boxes[accounts['capped']]
    assert (capped.state.state, capped.state.reason) == ('waiting', 'capped')
    assert targets['capped'] in {str(s['target_id']) for s in capped.queue}


def test_reconnecting_the_same_account_resumes_the_conversation_without_touching_it(db, monkeypatch):
    monkeypatch.setattr(project_senders, 'get_conn', scoped_conn(db))
    cid, targets, accounts = _conversations(db)
    before = db.execute('SELECT xmin::text AS v FROM outreach_campaign_targets WHERE id = %s', (targets['revoked'],))
    version = before.fetchone()['v']
    assert _people(db, cid)[targets['revoked']]['sender_hold'] == 'needs_reconnect'
    uid = str(
        db.execute('SELECT user_id FROM user_oauth_connections WHERE id = %s', (accounts['revoked'],)).fetchone()[
            'user_id'
        ]
    )
    project_id = str(
        db.execute('SELECT project_id FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()['project_id']
    )

    attached = call_as(
        member(db, uid),
        project_senders.attach,
        uid,
        project_id,
        'google',
        account='sub-revoked',
        cols={'account_label': 'revoked@acme.test', 'refresh_token_encrypted': 'x', 'granted_scopes': ['openid']},
    )

    assert not is_err(attached)
    assert _people(db, cid)[targets['revoked']]['sender_hold'] is None
    after = db.execute('SELECT xmin::text AS v FROM outreach_campaign_targets WHERE id = %s', (targets['revoked'],))
    assert after.fetchone()['v'] == version
    assert targets['revoked'] in {str(s['target_id']) for b in _queues(db) for s in b.queue}


@pytest.fixture
def client(db, monkeypatch):
    for module in (routes, senders):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    yield
    app.dependency_overrides.clear()


def test_each_accounts_held_count_equals_the_held_people_on_it(db, client):
    uid = _user(db)
    now = datetime.now(UTC)
    paused = _account(db, uid, 'paused', paused=True)
    throttled = _account(db, uid, 'throttled', last_used_at=now + timedelta(hours=1))
    ready = _account(db, uid, 'ready')
    first = _campaign(
        db, uid, TWO_STEPS, emails=('a1@client.test', 'a2@client.test', 'a3@client.test', 'a4@client.test')
    )
    second = _campaign(db, uid, TWO_STEPS, emails=('b1@client.test', 'b2@client.test', 'b3@client.test'))
    for email, account in {
        'a1': paused, 'a2': throttled, 'a3': paused, 'a4': ready, 'b1': throttled, 'b2': paused, 'b3': paused,
    }.items():  # fmt: skip
        _heard_step_one(db, f'{email}@client.test', account, now - timedelta(days=4))
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE email = 'b3@client.test'")
    sign_in(app, member(db, uid))
    api = TestClient(app)

    listed = api.get('/v1/integrations/senders?dns=false').json()['senders']
    rows = [p for cid in (first, second) for p in api.get(f'/v1/campaigns/{cid}/targets').json()['items']]

    by_account = Counter(p['sender'] for p in rows if p['sender_hold'])
    assert {s['account_label']: s['held'] for s in listed} == {
        'paused@acme.test': by_account['paused@acme.test'],
        'throttled@acme.test': by_account['throttled@acme.test'],
        'ready@acme.test': by_account['ready@acme.test'],
    }
    assert by_account == {'paused@acme.test': 3, 'throttled@acme.test': 2}


def test_an_approval_names_the_conversations_account_and_why_it_is_held(db, client):
    uid = _user(db)
    ready = _account(db, uid, 'ready')
    revoked = _account(db, uid, 'revoked', status='error')
    unlinked = _account(db, uid, 'unlinked')
    paused = _account(db, uid, 'paused', paused=True)
    people = {'anna': revoked, 'bea': unlinked, 'carla': paused, 'dora': ready}
    cid = _campaign(db, uid, TWO_STEPS, approve=True, emails=tuple(f'{p}@client.test' for p in people))
    _always_open(db, cid)
    now = datetime.now(UTC)
    for person, account in people.items():
        _heard_step_one(db, f'{person}@client.test', account, now - timedelta(days=4))
    _unlink(db, unlinked)
    _each_org(dispatcher.plan, now)
    sign_in(app, member(db, uid))

    items = TestClient(app).get(f'/v1/campaigns/{cid}/approvals').json()['items']

    shown = {i['recipient']: (i['sender_hold'], i['sender'] and i['sender']['address']) for i in items}
    assert shown == {
        'anna@client.test': ('needs_reconnect', 'revoked@acme.test'),
        'bea@client.test': ('unlinked', 'unlinked@acme.test'),
        'carla@client.test': ('paused', 'paused@acme.test'),
        'dora@client.test': (None, 'ready@acme.test'),
    }
    # A held conversation waits for a person or the provider, so no window can say when it goes.
    timed = {i['recipient'] for i in items if i['send_at']}
    assert timed == {'dora@client.test'}
