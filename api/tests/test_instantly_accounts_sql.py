"""The Instantly mailboxes on the Senders page (#936), against real SQL; Instantly itself is a fake. Needs
TEST_DATABASE_URL."""

import os

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.integrations import instantly, instantly_accounts
from app.main import app
from tests.conftest import member, migrated_database, pool_on, scoped_conn
from tests.test_instantly_sql import FakeInstantly, _as, _connect
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: Instantly accounts SQL test NOT run')

URL = '/v1/integrations/instantly/accounts'
ACCOUNTS = [
    {'email': 'ann@acme.test', 'status': 1, 'warmup_status': 1, 'daily_limit': 40},
    {'email': 'bob@acme.test', 'status': -1, 'warmup_status': 7},
]


class FakeWithAccounts(FakeInstantly):
    def __init__(self):
        super().__init__()
        self.accounts_status = 200  # what GET /api/v2/accounts answers when the key is good

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != '/api/v2/accounts' or request.headers.get('authorization') != f'Bearer {self.valid_key}':
            return super().handler(request)
        self.paths.append(request.url.path)
        if self.accounts_status != 200:
            return httpx.Response(self.accounts_status, json={})
        return httpx.Response(200, json={'items': ACCOUNTS})


@pytest.fixture
def fake(http):
    state = FakeWithAccounts()
    http.handler = state.handler
    return state


@pytest.fixture
def db(monkeypatch, fake):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (instantly, instantly_accounts):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
                monkeypatch.setattr(module, 'is_disabled', lambda key: False)
            monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
            monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
            monkeypatch.setattr('tests.test_instantly_sql._DB', [c])
            yield c
        finally:
            app.dependency_overrides.clear()


def _connection(db) -> dict:
    return db.execute('SELECT status, status_detail FROM instantly_connections').fetchone()


def test_a_connected_workspace_lists_its_mailboxes_with_instantlys_own_labels(db, fake):
    client = _as(_user(db))
    _connect(client)
    res = client.get(URL)
    assert res.status_code == 200
    assert res.json() == {
        'state': 'ok',
        'accounts': [
            {'email': 'ann@acme.test', 'status': 'active', 'warmup': 'active', 'daily_limit': 40},
            {'email': 'bob@acme.test', 'status': 'connection_error', 'warmup': 'unknown', 'daily_limit': None},
        ],
    }


def test_no_connection_is_not_connected_and_calls_nobody(db, fake):
    assert _as(_user(db)).get(URL).json() == {'state': 'not_connected', 'accounts': []}
    assert fake.paths == []


def test_a_disconnected_workspace_is_not_connected(db, fake):
    client = _as(_user(db))
    _connect(client)
    client.delete('/v1/integrations/instantly')
    fake.paths.clear()
    assert client.get(URL).json()['state'] == 'not_connected' and fake.paths == []


@pytest.mark.parametrize('status', [401, 403])
def test_a_key_without_accounts_read_says_so_and_stays_connected(db, fake, status):
    client = _as(_user(db))
    _connect(client)
    before = _connection(db)
    fake.accounts_status = status
    assert client.get(URL).json() == {'state': 'no_permission', 'accounts': []}
    # instantly._refusal would mark this working key invalid and stop its enrollments and stops.
    assert _connection(db) == before == {'status': 'connected', 'status_detail': None}
    assert client.get('/v1/integrations/instantly').json()['connected'] is True


@pytest.mark.parametrize('status', [429, 500, 402])
def test_any_other_failure_is_unavailable(db, fake, status):
    client = _as(_user(db))
    _connect(client)
    fake.accounts_status = status
    assert client.get(URL).json() == {'state': 'unavailable', 'accounts': []}
    assert _connection(db)['status'] == 'connected'


def test_a_key_that_cannot_be_decrypted_is_unavailable(db, fake, monkeypatch):
    client = _as(_user(db))
    _connect(client)
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    fake.paths.clear()
    assert client.get(URL).json()['state'] == 'unavailable' and fake.paths == []


def test_the_kill_switch_answers_unavailable_without_calling_instantly(db, fake, monkeypatch):
    client = _as(_user(db))
    _connect(client)
    fake.paths.clear()
    monkeypatch.setattr(instantly_accounts, 'is_disabled', lambda key: key == 'vendor_disabled:instantly')
    assert client.get(URL).json() == {'state': 'unavailable', 'accounts': []}
    assert fake.paths == []


def test_the_kill_switch_leaves_a_project_without_instantly_not_connected(db, fake, monkeypatch):
    monkeypatch.setattr(instantly_accounts, 'is_disabled', lambda key: key == 'vendor_disabled:instantly')
    assert _as(_user(db)).get(URL).json() == {'state': 'not_connected', 'accounts': []}
    assert fake.paths == []


def test_another_projects_connection_is_never_read(db, fake):
    owner = _user(db)
    _connect(_as(owner))
    fake.paths.clear()
    assert _as(_user(db)).get(URL).json()['state'] == 'not_connected'
    org = member(db, owner).org_id
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Beta') RETURNING id", (org, owner)
    ).fetchone()['id']
    assert _as(owner).get(URL, headers={'X-Project-Id': str(other)}).json()['state'] == 'not_connected'
    assert fake.paths == []
