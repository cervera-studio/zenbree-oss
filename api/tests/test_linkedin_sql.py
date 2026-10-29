from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.migrate import MIGRATIONS
from app.core.system_reasons import SystemReason
from app.integrations import token_crypto
from app.linkedin import api as linkedin_api
from app.main import app
from tests import test_dispatcher_sql
from tests.conftest import call_system, member, scoped_conn, sign_in
from tests.test_dispatcher_sql import DB_URL, _campaign
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: LinkedIn SQL test NOT run')
db = test_dispatcher_sql.db
UNIQUE = '20261006123127_unipile_account_unique.sql'


@pytest.fixture
def connect(db, http, keys, monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(linkedin_api, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(linkedin_api.webhook_events, 'get_conn', scoped_conn(db))
    monkeypatch.setattr('app.integrations.project_senders.get_conn', scoped_conn(db))
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(linkedin_api, '_redis', lambda: redis)
    http.handler = lambda r: (
        httpx.Response(201, json={'object': 'AccountCreated', 'account_id': 'acc-1'})
        if r.method == 'POST'
        else httpx.Response(200, json={'object': 'Account', 'name': 'Anna Rossi', 'connection_params': {}})
    )
    client = TestClient(app)

    def as_user(uid):
        sign_in(app, member(db, uid))
        return client.post(
            '/v1/integrations/linkedin/connect',
            json={'risk_accepted': True, 'username': 'anna@client.test', 'password': 'hunter2'},
        )

    yield as_user
    app.dependency_overrides.clear()


def _connections(c):
    return c.execute(
        "SELECT user_id::text, status FROM user_oauth_connections WHERE provider = 'unipile' ORDER BY created_at"
    ).fetchall()


def test_a_second_tenant_cannot_connect_the_same_account(db, connect):
    first, second = _user(db), _user(db)
    assert connect(first).json()['connected'] is True
    assert connect(first).json()['connected'] is True

    res = connect(second)
    assert res.status_code == 409
    assert res.json()['detail']['code'] == 'INTEGRATIONS.ALREADY_CONNECTED_ELSEWHERE'
    assert _connections(db) == [{'user_id': first, 'status': 'connected'}]


def test_a_hosted_link_of_a_user_removed_since_attaches_nothing(db, connect):
    """The hosted-auth notification attaches as the user the state names, in the project's organization: one removed
    from it since the link was made is refused, and no account is stored."""
    ada, bo = _user(db), _user(db)
    org = member(db, ada).org_id
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, bo),
    )
    pid = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Cliente') RETURNING id::text", (org, bo)
    ).fetchone()['id']
    state = token_crypto.issue_state(user_id=bo, provider='unipile', project_id=pid).value
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, org))

    res = TestClient(app).post(f'/v1/integrations/linkedin/webhook?state={state}', json={'account_id': 'acc-1'})

    assert res.status_code == 401 and res.json()['detail']['code'] == 'AUTH.NOT_A_MEMBER'
    assert _connections(db) == []


def test_an_acceptance_stamps_only_the_tenant_holding_the_account(db, connect):
    first, second = _user(db), _user(db)
    connect(first)
    connect(second)
    for uid in (first, second):
        cid = _campaign(db, uid, emails=(f'{uid}@client.test',))
        db.execute("UPDATE outreach_campaign_targets SET linkedin_provider_id = 'ACo1' WHERE campaign_id = %s", (cid,))

    accepted = call_system(
        SystemReason.LINKEDIN_WEBHOOK,
        linkedin_api.webhook_events.accepted,
        {'event': 'new_relation', 'account_id': 'acc-1', 'user_provider_id': 'ACo1'},
    )
    assert accepted['targets'] == 1
    stamped = db.execute(
        'SELECT owner_id::text FROM outreach_campaign_targets WHERE linkedin_accepted_at IS NOT NULL'
    ).fetchall()
    assert stamped == [{'owner_id': first}]


def test_a_status_event_changes_only_the_connection_holding_the_account(db, connect, monkeypatch):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 'uni-secret')
    first, second = _user(db), _user(db)
    connect(first)
    db.execute(
        """INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,
               provider_account_id) VALUES (default_organization(%s), %s, 'unipile', 'sealed', 'acc-2')""",
        (second, second),
    )

    res = TestClient(app).post(
        '/v1/integrations/linkedin/webhook',
        json={'AccountStatus': {'account_id': 'acc-1', 'message': 'CREDENTIALS'}},
        headers={'Unipile-Auth': 'uni-secret'},
    )
    assert res.json()['handled'] is True
    assert _connections(db) == [
        {'user_id': first, 'status': 'error'},
        {'user_id': second, 'status': 'connected'},
    ]


def test_the_migration_keeps_a_shared_account_on_its_first_live_connection(db):
    db.execute('DROP INDEX idx_user_oauth_connections_provider_account')
    db.execute(
        'CREATE INDEX idx_user_oauth_connections_provider_account ON user_oauth_connections(provider, provider_account_id)'
    )
    start = datetime(2026, 9, 1, tzinfo=UTC)
    rows = {
        'gone': ('acc-1', 'disconnected', start),
        'first': ('acc-1', 'connected', start + timedelta(days=1)),
        'second': ('acc-1', 'connected', start + timedelta(days=2)),
        'alone': ('acc-2', 'connected', start),
        'broken': ('acc-3', 'error', start),
        'working': ('acc-3', 'connected', start + timedelta(days=1)),
    }
    users = {}
    for name, (account, status, connected_at) in rows.items():
        users[name] = _user(db)
        db.execute(
            """INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,
                   provider_account_id, status, connected_at)
               VALUES (default_organization(%s), %s, 'unipile', 'sealed', %s, %s, %s)""",
            (users[name], users[name], account, status, connected_at),
        )

    db.execute((MIGRATIONS / UNIQUE).read_text())  # the fixture ran it on an empty schema: run it on this data
    after = {
        r['user_id']: (r['provider_account_id'], r['status'], r['status_detail'])
        for r in db.execute(
            'SELECT user_id::text, provider_account_id, status, status_detail FROM user_oauth_connections'
        ).fetchall()
    }
    assert after == {
        users['gone']: (None, 'disconnected', None),
        users['first']: ('acc-1', 'connected', None),
        users['second']: (None, 'error', 'ACCOUNT_IN_USE_ELSEWHERE'),
        users['alone']: ('acc-2', 'connected', None),
        users['broken']: (None, 'error', 'ACCOUNT_IN_USE_ELSEWHERE'),
        users['working']: ('acc-3', 'connected', None),
    }
