"""#471: removing a member revokes their senders of that organization at the provider, and only those."""

import pytest
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.integrations import oauth_client, token_crypto
from app.linkedin import unipile_client
from app.main import app
from tests.conftest import sign_in
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_creator_deleted_sql import _campaign, _mailbox, _org, _team
from tests.test_dispatcher_sql import provider as provider

pytestmark = needs_db


@pytest.fixture
def calls(monkeypatch):
    seen = {'revoked': [], 'deleted': []}

    async def revoke(self, token):
        seen['revoked'].append((self._provider.name, token))
        return True

    async def delete_account(self, *, account_id):
        seen['deleted'].append(account_id)

    monkeypatch.setattr(oauth_client.OAuthClient, 'revoke', revoke)
    monkeypatch.setattr(unipile_client.UnipileClient, 'delete_account', delete_account)
    monkeypatch.setattr(unipile_client.UnipileClient, 'is_configured', lambda self: True)
    return seen


def _post(caller, target, org):
    sign_in(app, CurrentUser(id=caller, org_id=caller_org(caller)))
    try:
        return TestClient(app).post(f'/v1/integrations/senders/members/{target}/release', json={'organization_id': org})
    finally:
        app.dependency_overrides.clear()


_ORGS: dict[str, str] = {}


def caller_org(uid) -> str:
    return _ORGS[uid]


def _connection(db, uid, org, provider_name, account) -> str:
    return str(
        db.execute(
            'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted,'
            ' provider_account_id) VALUES (%s, %s, %s, %s, %s) RETURNING id',
            (org, uid, provider_name, token_crypto.encrypt('tok-' + provider_name).value, account),
        ).fetchone()['id']
    )


def test_an_admin_releases_a_members_senders_in_their_organization_only(db, provider, calls):
    ada, bo, org, pid = _team(db)
    _ORGS[ada] = org
    bo_own = _org(db, bo)
    box = _mailbox(db, bo, org, pid, 'bo@agency.test')
    li = _connection(db, bo, org, 'unipile', 'acc-1')
    hub = _connection(db, bo, org, 'hubspot', 'portal-1')
    elsewhere = _connection(db, bo, bo_own, 'unipile', 'acc-2')
    camp = _campaign(db, bo, org, pid)

    res = _post(ada, bo, org)

    assert res.status_code == 200 and res.json() == {'released': 3}
    assert sorted(calls['revoked']) == [('google', 'refresh-1'), ('hubspot', 'tok-hubspot')]
    assert calls['deleted'] == ['acc-1']
    rows = {
        r['id']: r
        for r in db.execute(
            'SELECT id::text, status, status_detail FROM user_oauth_connections WHERE id = ANY(%s::uuid[])',
            ([box, li, hub, elsewhere],),
        )
    }
    for sid in (box, li, hub):
        assert (rows[sid]['status'], rows[sid]['status_detail']) == ('disconnected', 'owner_removed')
    assert rows[elsewhere]['status'] == 'connected'
    assert db.execute('SELECT count(*) AS n FROM project_senders WHERE sender_id = %s', (box,)).fetchone()['n'] == 0
    assert db.execute('SELECT former_owner_id FROM outreach_campaigns WHERE id = %s', (camp,)).fetchone() == {
        'former_owner_id': None
    }


def test_only_an_admin_of_the_named_organization_releases(db, provider, calls):
    ada, bo, org, pid = _team(db)
    other = _org(db, bo)
    _ORGS.update({ada: org, bo: other})
    _mailbox(db, bo, org, pid, 'bo@agency.test')
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (other, ada),
    )

    assert _post(bo, bo, org).status_code == 403  # admin of their own organization, naming Ada's
    assert _post(ada, bo, other).status_code == 403  # Ada's token names her organization
    sign_in(app, CurrentUser(id=bo, org_id=org))
    try:  # a plain member of Ada's organization
        res = TestClient(app).post(f'/v1/integrations/senders/members/{ada}/release', json={'organization_id': org})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 403
    assert calls == {'revoked': [], 'deleted': []}
    assert (
        db.execute("SELECT count(*) AS n FROM user_oauth_connections WHERE status = 'connected'").fetchone()['n'] == 1
    )


def test_a_user_who_is_not_a_member_is_404(db, provider, calls):
    ada, _bo, org, _pid = _team(db)
    _ORGS[ada] = org
    stranger = _team(db)[1]
    assert _post(ada, stranger, org).status_code == 404


def _project(db, uid, org) -> str:
    return str(
        db.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Altro') RETURNING id", (org, uid)
        ).fetchone()['id']
    )


def test_a_google_account_that_is_also_a_sender_elsewhere_is_not_revoked(db, provider, calls):
    ada, bo, org, pid = _team(db)
    _ORGS[ada] = org
    bo_own = _org(db, bo)
    a_row = _mailbox(db, bo, org, pid, 'bo@agency.test')
    b_row = _mailbox(db, bo, bo_own, _project(db, bo, bo_own), 'bo@agency.test')

    assert _post(ada, bo, org).status_code == 200

    assert calls['revoked'] == []
    status = {
        r['id']: r['status']
        for r in db.execute(
            'SELECT id::text, status FROM user_oauth_connections WHERE id = ANY(%s::uuid[])', ([a_row, b_row],)
        )
    }
    assert status == {a_row: 'disconnected', b_row: 'connected'}


@pytest.mark.parametrize('shared', [True, False])
def test_disconnect_revokes_only_a_grant_no_other_organization_uses(db, provider, calls, shared):
    _ada, bo, org, pid = _team(db)
    box = _mailbox(db, bo, org, pid, 'bo@agency.test')
    if shared:
        bo_own = _org(db, bo)
        _mailbox(db, bo, bo_own, _project(db, bo, bo_own), 'bo@agency.test')
    db.execute('UPDATE member SET role = \'admin\' WHERE "userId" = %s AND "organizationId" = %s', (bo, org))
    sign_in(app, CurrentUser(id=bo, org_id=org))
    try:
        res = TestClient(app).delete(f'/v1/integrations/mailboxes/{box}', headers={'X-Project-Id': pid})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    assert calls['revoked'] == ([] if shared else [('google', 'refresh-1')])


def test_account_deletion_revokes_hubspot_too(db, provider, calls):
    ada, bo, org, _pid = _team(db)
    hub = _connection(db, bo, org, 'hubspot', 'portal-1')
    db.execute('UPDATE "user" SET role = %s WHERE id = %s', ('admin', ada))  # require_admin reads it too
    sign_in(app, CurrentUser(id=ada, org_id=org, role='admin'))
    try:
        res = TestClient(app).post(f'/v1/admin/users/{bo}/before-delete')
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    assert calls['revoked'] == [('hubspot', 'tok-hubspot')]
    assert db.execute('SELECT status FROM user_oauth_connections WHERE id = %s', (hub,)).fetchone() == {
        'status': 'disconnected'
    }


def test_a_hubspot_grant_stored_in_another_organization_is_left_alone(db, provider, calls):
    """One HubSpot grant per person (a unique user_id), kept in the organization of their latest connect."""
    ada, bo, org, _pid = _team(db)
    _ORGS[ada] = org
    hub = _connection(db, bo, _org(db, bo), 'hubspot', 'portal-1')

    assert _post(ada, bo, org).json() == {'released': 0}
    assert calls['revoked'] == []
    assert db.execute('SELECT status FROM user_oauth_connections WHERE id = %s', (hub,)).fetchone() == {
        'status': 'connected'
    }
