from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.auth import api_key_routes
from app.auth.api_keys import MAX_ACTIVE_KEYS, IssuedApiKey, issue_api_key, list_api_keys, revoke_api_key
from app.auth.middleware import CurrentUser, hash_api_key
from app.core.errors import is_err, ok
from app.main import app
from tests.conftest import call_as, member, sign_in, sign_out
from tests.test_agent import DB_URL
from tests.test_agent import _user as _insert_user
from tests.test_agent import db as db

client = TestClient(app)
KEY_ID = '11111111-1111-4111-8111-111111111111'
needs_db = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: API key SQL tests NOT run')


def _auth_user():
    return CurrentUser(id='user-1', org_id='org-1', org_role='admin')


def test_api_key_routes_create_list_and_revoke(monkeypatch, sign_in_without_db):
    sign_in_without_db(_auth_user())
    calls = {}

    def issue(user, label):
        calls['issue'] = (user.id, label)
        return ok(
            IssuedApiKey(id=KEY_ID, label=label, key='sbk_secret', last4='cret', created_at='2026-10-01T10:00:00Z')
        )

    def listing(org_id):
        calls['list'] = org_id
        return ok(
            [{'id': KEY_ID, 'label': 'ci', 'last4': None, 'created_at': '2026-10-01T10:00:00Z', 'revoked_at': None}]
        )

    def revoke(org_id, key_id):
        calls['revoke'] = (org_id, key_id)
        return ok(
            {
                'id': key_id,
                'label': 'ci',
                'last4': 'cret',
                'created_at': '2026-10-01T10:00:00Z',
                'revoked_at': '2026-10-01T10:01:00Z',
            }
        )

    monkeypatch.setattr(api_key_routes, 'issue_api_key', issue)
    monkeypatch.setattr(api_key_routes, 'list_api_keys', listing)
    monkeypatch.setattr(api_key_routes, 'revoke_api_key', revoke)
    try:
        created = client.post('/v1/me/api-keys', json={'label': ' ci '})
        assert created.status_code == 201 and created.json()['key'] == 'sbk_secret'
        assert calls['issue'] == ('user-1', ' ci ')
        listed = client.get('/v1/me/api-keys')
        assert listed.status_code == 200
        assert calls['list'] == 'org-1'
        assert all('key' not in item and 'key_hash' not in item for item in listed.json()['keys'])
        revoked = client.delete(f'/v1/me/api-keys/{KEY_ID}')
        assert revoked.status_code == 200 and revoked.json()['id'] == KEY_ID
        assert calls['revoke'] == ('org-1', KEY_ID)
    finally:
        sign_out(app)


def test_api_key_routes_require_jwt_session():
    response = client.get('/v1/me/api-keys')
    assert response.status_code == 401


@pytest.mark.parametrize('label', ['', '   ', 'x' * 121])
def test_api_key_service_rejects_invalid_labels_before_database_access(label):
    class UnusedRepository:
        def count_active(self, user_id):
            pytest.fail('invalid labels must be rejected before database access')

    result = issue_api_key(_auth_user(), label, UnusedRepository())

    assert is_err(result) and result.error.code == 'API_KEY.INVALID_LABEL'


@needs_db
def test_concurrent_api_key_issuance_cannot_exceed_active_key_cap(db):
    owner_id = _insert_user(db)
    owner = member(db, owner_id)
    for index in range(MAX_ACTIVE_KEYS - 1):
        db.execute(
            'INSERT INTO user_api_keys (organization_id, user_id, label, key_hash) VALUES (default_organization(%s), %s, %s, %s)',
            (owner_id, owner_id, f'key {index}', f'{index:064x}'),
        )

    start = Barrier(2)

    def issue():
        start.wait()
        return call_as(owner, issue_api_key, owner, 'parallel request')  # each request in its own scope

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: issue(), range(2)))

    assert sum(result.is_ok() for result in results) == 1
    assert [result.error.code for result in results if result.is_err()] == ['API_KEY.CAP_REACHED']
    assert (
        db.execute(
            'SELECT count(*) AS n FROM user_api_keys WHERE user_id = %s AND revoked_at IS NULL', (owner_id,)
        ).fetchone()['n']
        == MAX_ACTIVE_KEYS
    )


@needs_db
def test_api_key_storage_and_revoke_are_owner_scoped(db):
    owner_id = _insert_user(db)
    other_id = _insert_user(db)
    owner, other = member(db, owner_id), member(db, other_id)
    issued = call_as(owner, issue_api_key, owner, 'integration test')

    assert issued.is_ok()
    plaintext = issued.value.key
    stored = db.execute(
        'SELECT user_id::text AS user_id, organization_id::text AS org_id, key_hash, last4, revoked_at FROM user_api_keys'
        ' WHERE id = %s',
        (issued.value.id,),
    ).fetchone()
    assert (stored['user_id'], stored['org_id']) == (owner_id, owner.org_id)
    assert stored['key_hash'] == hash_api_key(plaintext)
    assert stored['last4'] == plaintext[-4:] == issued.value.last4
    assert plaintext[:-4] not in str(dict(stored))
    assert plaintext not in stored['key_hash']

    owner_keys = call_as(owner, list_api_keys, owner.org_id)
    other_keys = call_as(other, list_api_keys, other.org_id)
    assert owner_keys.is_ok() and len(owner_keys.value) == 1
    assert other_keys.is_ok() and other_keys.value == []
    assert 'key_hash' not in owner_keys.value[0] and 'key' not in owner_keys.value[0]

    sign_in(app, other)
    try:
        foreign_response = client.delete(f'/v1/me/api-keys/{issued.value.id}')
    finally:
        sign_out(app)
    assert foreign_response.status_code == 404
    assert foreign_response.json()['detail']['code'] == 'API_KEY.NOT_FOUND'
    assert (
        db.execute('SELECT revoked_at FROM user_api_keys WHERE id = %s', (issued.value.id,)).fetchone()['revoked_at']
        is None
    )

    owned_revoke = call_as(owner, revoke_api_key, owner.org_id, issued.value.id)
    assert owned_revoke.is_ok()
    assert (
        db.execute('SELECT revoked_at FROM user_api_keys WHERE id = %s', (issued.value.id,)).fetchone()['revoked_at']
        is not None
    )
