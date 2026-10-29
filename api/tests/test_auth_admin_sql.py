"""#431: the back office reads ``user.role`` from the database on every call, so a demotion applies to a token
that is still live."""

import pytest
from fastapi.testclient import TestClient

from app.auth import middleware
from app.main import app
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_auth import _FakeJWKS, _token

pytestmark = needs_db


@pytest.fixture
def tc(db, monkeypatch):
    monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())
    return TestClient(app)


def test_a_demoted_admin_is_refused_with_a_live_admin_token(tc, db):
    ada = _user(db)
    org = str(db.execute('SELECT default_organization(%s) AS id', (ada,)).fetchone()['id'])
    headers = {'Authorization': f'Bearer {_token(sub=ada, org=org, role="admin")}'}

    db.execute('UPDATE "user" SET role = %s WHERE id = %s', ('user,admin', ada))
    assert tc.get('/v1/admin/ping', headers=headers).status_code == 200
    assert tc.get('/v1/admin/settings', headers=headers).status_code == 200

    db.execute('UPDATE "user" SET role = %s WHERE id = %s', ('user', ada))
    r = tc.get('/v1/admin/ping', headers=headers)
    assert r.status_code == 403
    assert r.json()['detail']['code'] == 'AUTH.FORBIDDEN'
    assert tc.get('/v1/admin/settings', headers=headers).status_code == 403
