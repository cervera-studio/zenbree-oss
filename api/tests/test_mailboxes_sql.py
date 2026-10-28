"""Mailbox connection storage against real SQL. Needs TEST_DATABASE_URL."""

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.integrations import mailboxes, project_senders
from app.linkedin import api as linkedin_api
from app.main import app
from tests.conftest import (
    as_request,
    give_role,
    link,
    member,
    migrated_database,
    pool_on,
    project,
    scoped_conn,
    sender,
    sign_in,
)
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: mailbox SQL test NOT run')


@pytest.fixture
def conn(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        for module in (mailboxes, project_senders):
            monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
        monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
        monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
        monkeypatch.setattr(_CONN, 'c', c)
        yield c


class _CONN:
    c = None


def _as(uid):
    """The scope of ``uid``'s connect request (``complete`` runs as the signed-in member)."""
    return as_request(member(_CONN.c, uid))


def _team(conn):
    """Ada's organization with Bo (member) and Cy (admin); Ada's default project."""
    ada, bo, cy = _user(conn), _user(conn), _user(conn)
    pid = project(conn, ada)
    org = member(conn, ada).org_id
    for uid, role in ((bo, 'member'), (cy, 'admin')):
        conn.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (org, uid, role),
        )
    return ada, bo, cy, pid, org


def _rows(conn):
    return conn.execute(
        'SELECT u.user_id::text AS who, u.account_label, u.refresh_token_encrypted AS token,'
        ' ARRAY(SELECT ps.project_id::text FROM project_senders ps WHERE ps.sender_id = u.id) AS projects'
        ' FROM user_oauth_connections u ORDER BY u.created_at'
    ).fetchall()


def _store(uid, pid, label, token, subject=None):
    """``subject``: Google's ``sub`` for the account, by default one per address."""
    with _as(uid):
        return mailboxes._store(
            uid, pid, 'google', encrypted=token, scopes=['gmail.send'], label=label, subject=subject or f'sub-{label}'
        )


def _linkedin(uid, pid, account_id, **profile):
    with _as(uid):
        return linkedin_api._store(uid, pid, account_id, **profile)


def test_another_address_adds_a_mailbox_and_disconnect_drops_one_token(conn):
    uid = _user(conn)
    other = _user(conn)
    pid, opid = project(conn, uid), project(conn, other)
    assert _store(uid, pid, 'me@acme.test', 'ct-1').is_ok()
    assert _store(uid, pid, 'new@acme.test', 'ct-2').is_ok()
    assert _store(other, opid, 'x@acme.test', 'ct-x').is_ok()
    with _as(uid):
        listed = mailboxes.list_mailboxes(pid)
    assert [(r['account_label'], r['status'], str(r['user_id']), r['granted_scopes']) for r in listed] == [
        ('me@acme.test', 'connected', uid, ['gmail.send']),
        ('new@acme.test', 'connected', uid, ['gmail.send']),
    ]

    conn.execute("UPDATE user_oauth_connections SET inbound_subscription_id = 'sub-' || id, inbound_history_id = '9'")
    sign_in(app, member(conn, uid))
    try:
        assert TestClient(app).delete(f'/v1/integrations/mailboxes/{listed[0]["id"]}').json() == {'connected': False}
    finally:
        app.dependency_overrides.clear()
    rows = {
        r['account_label']: r
        for r in conn.execute(
            'SELECT account_label, status, refresh_token_encrypted, inbound_subscription_id, inbound_history_id'
            ' FROM user_oauth_connections'
        )
    }
    assert rows['me@acme.test']['status'] == 'disconnected' and rows['me@acme.test']['refresh_token_encrypted'] == ''
    # #747 review: a reconnect starts a fresh watch, never trusts the old one's expiry or cursor
    assert (
        rows['me@acme.test']['inbound_subscription_id'] is None and rows['me@acme.test']['inbound_history_id'] is None
    )
    assert rows['new@acme.test']['status'] == 'connected' and rows['new@acme.test']['refresh_token_encrypted'] == 'ct-2'
    assert rows['x@acme.test']['status'] == 'connected' and rows['x@acme.test']['refresh_token_encrypted'] == 'ct-x'
    with _as(uid):
        assert [r['account_label'] for r in mailboxes.list_mailboxes(pid)] == ['new@acme.test']


def test_a_projects_mailbox_is_shared_but_only_its_connector_or_an_admin_disconnects_it(conn):
    """#307: a member of the organization sees the project's mailbox and gets 403 on disconnect; an admin may.
    A second project of the same organization does not use it until linked."""
    ada, bo, cy, pid, org = _team(conn)
    second = conn.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'B') RETURNING id::text", (org, bo)
    ).fetchone()['id']
    _store(ada, pid, 'ada@acme.test', 'ct-1')
    sign_in(app, give_role(conn, CurrentUser(id=bo, org_id=org, org_role='member')))
    try:
        client = TestClient(app)
        listed = client.get('/v1/integrations/mailboxes', headers={'X-Project-Id': pid}).json()['mailboxes']
        (google,) = listed
        assert google['account_label'] == 'ada@acme.test' and google['can_disconnect'] is False
        assert client.get('/v1/integrations/mailboxes', headers={'X-Project-Id': second}).json()['mailboxes'] == []
        url = f'/v1/integrations/mailboxes/{google["id"]}'
        assert client.delete(url, headers={'X-Project-Id': second}).status_code == 200
        assert conn.execute('SELECT status FROM user_oauth_connections').fetchone()['status'] == 'connected'
        res = client.delete(url, headers={'X-Project-Id': pid})
        assert res.status_code == 403 and res.json()['detail']['code'] == 'INTEGRATIONS.DISCONNECT_FORBIDDEN'
        sign_in(app, give_role(conn, CurrentUser(id=cy, org_id=org, org_role='admin')))
        assert client.delete(url, headers={'X-Project-Id': pid}).status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert conn.execute('SELECT status FROM user_oauth_connections').fetchone()['status'] == 'disconnected'


def test_connecting_adds_a_mailbox_and_never_takes_over_a_colleagues(conn):
    """Security (#307 review), with several mailboxes per client (#935): a member connecting where a colleague's
    mailbox serves the project adds their own, and the colleague's row, token and link stay as they were.
    Re-authorising the colleague's account itself keeps the colleague its connector."""
    ada, bo, cy, pid, _ = _team(conn)
    _store(ada, pid, 'ada@acme.test', 'ct-ada')
    (adas,) = _rows(conn)

    assert _store(bo, pid, 'bo@other.test', 'ct-bo').is_ok()
    # The same label, another Google account: not Ada's mailbox, so a mailbox of Bo's.
    assert _store(bo, pid, 'ada@acme.test', 'ct-spoof', subject='sub-someone-else').is_ok()
    assert _store(cy, pid, 'cy@acme.test', 'ct-cy').is_ok()
    assert [(r['who'], r['token'], r['projects']) for r in _rows(conn)] == [
        (ada, 'ct-ada', [pid]),
        (bo, 'ct-bo', [pid]),
        (bo, 'ct-spoof', [pid]),
        (cy, 'ct-cy', [pid]),
    ]
    assert _rows(conn)[0] == adas

    assert _store(bo, pid, 'whatever', 'ct-reauth', subject='sub-ada@acme.test').is_ok()  # Ada's account itself
    row = _rows(conn)[0]
    assert (row['who'], row['token'], row['projects']) == (ada, 'ct-reauth', [pid])  # Ada stays its connector
    assert len(_rows(conn)) == 4


def test_linkedin_connect_never_takes_over_a_colleagues_account(conn):
    """The same rule for a LinkedIn account, keyed by its Unipile id."""
    ada, bo, cy, pid, _ = _team(conn)
    assert _linkedin(ada, pid, 'acc-ada', name='Ada', premium=False).is_ok()
    refused = _linkedin(bo, pid, 'acc-bo', name='Bo', premium=False)
    assert refused.is_err() and refused.error.code == 'INTEGRATIONS.REPLACE_FORBIDDEN'
    accounts = conn.execute(
        'SELECT user_id::text AS who, provider_account_id AS acc FROM user_oauth_connections'
    ).fetchall()
    assert accounts == [{'who': ada, 'acc': 'acc-ada'}]
    assert _linkedin(ada, pid, 'acc-ada', name='Ada', premium=True).is_ok()  # re-authorised in place by its connector
    assert _linkedin(cy, pid, 'acc-cy', name='Cy', premium=False).is_ok()  # an admin replaces it
    linked = conn.execute(
        'SELECT u.user_id::text AS who FROM project_senders ps JOIN user_oauth_connections u ON u.id = ps.sender_id'
    ).fetchall()
    assert linked == [{'who': cy}]


def test_a_member_cannot_delete_a_client_that_sends_from_a_colleagues_account(conn):
    """Deleting a project unlinks its senders, so it follows the unlink rule: 409 for a member over a colleague's
    live sender; its connector and an admin may, and the sender keeps its other links."""
    ada, bo, cy, pid, org = _team(conn)
    client_b, client_c = (
        conn.execute(
            'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id::text',
            (org, bo, name),
        ).fetchone()['id']
        for name in ('B', 'C')
    )
    _store(ada, pid, 'ada@acme.test', 'ct-1')
    for extra in (client_b, client_c):
        link(conn, extra, conn.execute('SELECT id FROM user_oauth_connections').fetchone()['id'])
    try:
        api = TestClient(app)
        for who, role, target, status in (
            (bo, 'member', client_b, 409),
            (ada, 'member', client_b, 200),
            (cy, 'admin', client_c, 200),
        ):
            sign_in(app, give_role(conn, CurrentUser(id=who, org_id=org, org_role=role)))
            res = api.delete(f'/v1/projects/{target}')
            assert res.status_code == status, (who, res.text)
            if status == 409:
                assert res.json()['detail']['code'] == 'PROJECT.HAS_SENDERS'
    finally:
        app.dependency_overrides.clear()
    (row,) = _rows(conn)
    assert row['who'] == ada and row['projects'] == [pid]


def test_reconnecting_one_account_leaves_the_members_other_account_alone(conn):
    """Owner decision (#307): one member, two Gmail accounts for two clients. Reconnecting A updates A's row in
    place; B's row, token and link do not move."""
    ada, _, _, pid, org = _team(conn)
    other = conn.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Client 2') RETURNING id::text",
        (org, ada),
    ).fetchone()['id']
    assert _store(ada, pid, 'a@acme.test', 'ct-a', subject='sub-a').is_ok()
    assert _store(ada, other, 'b@acme.test', 'ct-b', subject='sub-b').is_ok()
    before = {r['account_label']: r for r in _rows(conn)}
    assert _store(ada, pid, 'a@acme.test', 'ct-a2', subject='sub-a').is_ok()
    after = {r['account_label']: r for r in _rows(conn)}
    assert after['a@acme.test']['token'] == 'ct-a2' and after['a@acme.test']['projects'] == [pid]
    assert after['b@acme.test'] == before['b@acme.test']


def _legacy_mailbox(conn, uid, projects):
    """A mailbox connected before #307: no identity recorded, serving two clients."""
    return sender(conn, uid, projects=projects, account_label='a@acme.test', refresh_token_encrypted='ct-a')


def test_a_legacy_mailbox_reconnected_with_its_address_records_its_identity_in_place(conn):
    ada, _, _, pid, org = _team(conn)
    other = conn.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Client 2') RETURNING id::text",
        (org, ada),
    ).fetchone()['id']
    legacy = _legacy_mailbox(conn, ada, [pid, other])
    assert _store(ada, pid, 'A@acme.test', 'ct-a2', subject='sub-a').is_ok()
    (row,) = conn.execute(
        'SELECT id::text, provider_account_id, refresh_token_encrypted AS t FROM user_oauth_connections'
    ).fetchall()
    assert (row['id'], row['provider_account_id'], row['t']) == (legacy, 'sub-a', 'ct-a2')
    assert sorted(_rows(conn)[0]['projects']) == sorted([pid, other])  # still serves both clients


def test_a_new_account_in_one_client_joins_the_legacy_mailbox(conn):
    """N1: Ada's legacy a@ serves clients 1 and 2; connecting Gmail b@ in client 1 is a new row for client 1 only.
    Both clients keep sending from a@, whose token is untouched."""
    ada, _, _, pid, org = _team(conn)
    other = conn.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Client 2') RETURNING id::text",
        (org, ada),
    ).fetchone()['id']
    _legacy_mailbox(conn, ada, [pid, other])
    assert _store(ada, pid, 'b@acme.test', 'ct-b', subject='sub-b').is_ok()
    rows = {r['account_label']: r for r in _rows(conn)}
    assert rows['a@acme.test']['token'] == 'ct-a' and sorted(rows['a@acme.test']['projects']) == sorted([pid, other])
    assert rows['b@acme.test']['token'] == 'ct-b' and rows['b@acme.test']['projects'] == [pid]


def _client(conn, org, uid, name='Client 2') -> str:
    return conn.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id::text',
        (org, uid, name),
    ).fetchone()['id']


def _links(conn, pid) -> set[str]:
    return {
        r['s'] for r in conn.execute('SELECT sender_id::text AS s FROM project_senders WHERE project_id = %s', (pid,))
    }


def _account_id(conn, subject) -> str:
    return conn.execute(
        'SELECT id::text AS i FROM user_oauth_connections WHERE provider_account_id = %s', (subject,)
    ).fetchone()['i']


def _pooled(conn, pid, sender_id) -> str:
    cid = conn.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps)'
        " SELECT organization_id, id, user_id, 'C', 'draft', '[]' FROM projects WHERE id = %s"
        ' RETURNING id',
        (pid,),
    ).fetchone()['id']
    conn.execute(
        'INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)'
        ' SELECT %s, sender_id, project_id, provider, organization_id FROM project_senders'
        ' WHERE project_id = %s AND sender_id = %s',
        (cid, pid, sender_id),
    )
    return str(cid)


def test_reconnecting_a_legacy_address_in_a_client_that_links_it_moves_the_link_to_the_account(conn):
    ada, _, _, pid, org = _team(conn)
    other = _client(conn, org, ada)
    legacy = _legacy_mailbox(conn, ada, [pid])
    cid = _pooled(conn, pid, legacy)
    assert _store(ada, other, 'a@acme.test', 'ct-a', subject='sub-a').is_ok()
    account = _account_id(conn, 'sub-a')

    assert _store(ada, pid, 'a@acme.test', 'ct-a2', subject='sub-a').is_ok()

    assert _links(conn, pid) == {account} and _links(conn, other) == {account}
    assert [r['sender_id'] for r in conn.execute('SELECT sender_id::text FROM outreach_campaign_senders')] == [account]
    assert conn.execute('SELECT 1 FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()
    assert (
        conn.execute('SELECT provider_account_id FROM user_oauth_connections WHERE id = %s', (legacy,)).fetchone()[
            'provider_account_id'
        ]
        is None
    )


def test_reconnecting_a_legacy_address_whose_account_the_client_links_drops_the_legacy_link(conn):
    ada, _, _, pid, org = _team(conn)
    other = _client(conn, org, ada)
    legacy = _legacy_mailbox(conn, ada, [pid])
    assert _store(ada, other, 'a@acme.test', 'ct-a', subject='sub-a').is_ok()
    account = _account_id(conn, 'sub-a')
    link(conn, pid, account)
    assert _links(conn, pid) == {legacy, account}

    assert _store(ada, pid, 'a@acme.test', 'ct-a2', subject='sub-a').is_ok()

    assert _links(conn, pid) == {account}


def test_a_colleague_connecting_the_address_of_a_legacy_mailbox_is_refused(conn):
    ada, bo, _, pid, _ = _team(conn)
    _legacy_mailbox(conn, ada, [pid])
    before = _rows(conn)

    refused = _store(bo, pid, 'a@acme.test', 'ct-bo', subject='sub-bo')

    assert refused.is_err() and refused.error.code == 'INTEGRATIONS.REPLACE_FORBIDDEN'
    assert _rows(conn) == before


def test_a_colleague_connecting_beside_a_disconnected_legacy_mailbox_adds_their_own(conn):
    ada, bo, _, pid, _ = _team(conn)
    legacy = _legacy_mailbox(conn, ada, [pid])
    conn.execute("UPDATE user_oauth_connections SET status = 'disconnected' WHERE id = %s", (legacy,))

    assert _store(bo, pid, 'a@acme.test', 'ct-bo', subject='sub-bo').is_ok()

    assert len(_links(conn, pid)) == 2


def _link_as(conn, uid, sender_id, pid):
    with _as(uid):
        return project_senders.link(member(conn, uid), sender_id, pid)


def test_linking_an_account_into_a_client_that_links_its_legacy_row_moves_the_link(conn):
    ada, _, _, pid, org = _team(conn)
    other = _client(conn, org, ada)
    _legacy_mailbox(conn, ada, [pid])
    assert _store(ada, other, 'a@acme.test', 'ct-a', subject='sub-a').is_ok()
    account = _account_id(conn, 'sub-a')

    assert _link_as(conn, ada, account, pid).is_ok()

    assert _links(conn, pid) == {account}


def test_linking_a_legacy_row_into_a_client_that_links_its_account_changes_nothing(conn):
    ada, _, _, pid, org = _team(conn)
    other = _client(conn, org, ada)
    legacy = _legacy_mailbox(conn, ada, [other])
    assert _store(ada, pid, 'a@acme.test', 'ct-a', subject='sub-a').is_ok()
    account = _account_id(conn, 'sub-a')
    _pooled(conn, pid, account)
    pool_rows = 'SELECT campaign_id, sender_id FROM outreach_campaign_senders ORDER BY 1, 2'
    before = conn.execute(pool_rows).fetchall()

    assert _link_as(conn, ada, legacy, pid).is_ok()

    assert _links(conn, pid) == {account}
    assert conn.execute(pool_rows).fetchall() == before


def test_a_colleague_cannot_link_their_account_beside_a_legacy_row_of_the_same_address(conn):
    ada, bo, _, pid, org = _team(conn)
    legacy = _legacy_mailbox(conn, ada, [pid])
    assert _store(bo, _client(conn, org, bo), 'a@acme.test', 'ct-bo', subject='sub-bo').is_ok()

    refused = _link_as(conn, bo, _account_id(conn, 'sub-bo'), pid)

    assert refused.is_err() and refused.error.code == 'INTEGRATIONS.REPLACE_FORBIDDEN'
    assert _links(conn, pid) == {legacy}


def test_a_colleague_connecting_another_address_beside_a_legacy_mailbox_adds_their_own(conn):
    ada, bo, _, pid, _ = _team(conn)
    _legacy_mailbox(conn, ada, [pid])
    (legacy,) = _rows(conn)
    assert _store(bo, pid, 'bo@acme.test', 'ct-bo', subject='sub-bo').is_ok()
    rows = _rows(conn)
    assert rows[0] == legacy
    assert (rows[1]['who'], rows[1]['token'], rows[1]['projects']) == (bo, 'ct-bo', [pid])


def test_a_connect_without_the_providers_account_id_stores_nothing(conn):
    ada, _, _, pid, _ = _team(conn)
    _legacy_mailbox(conn, ada, [pid])
    before = _rows(conn)
    with _as(ada):
        res = mailboxes._store(
            ada, pid, 'google', encrypted='ct-x', scopes=['gmail.send'], label='a@acme.test', subject=None
        )
    assert res.is_err() and res.error.code == 'INTEGRATIONS.PROFILE_UNAVAILABLE'
    assert _rows(conn) == before


def test_a_member_cannot_reauthorise_or_link_a_colleagues_linkedin_account(conn):
    ada, bo, cy, pid, _ = _team(conn)
    bos_project = project(conn, bo)
    assert _linkedin(ada, pid, 'acc-ada', name='Ada', premium=None).is_ok()
    before = _rows(conn)

    for target in (pid, bos_project):  # the project's own account re-authorised, and linked into another project
        refused = _linkedin(bo, target, 'acc-ada', name='Bo', premium=None)
        assert refused.is_err() and refused.error.code == 'INTEGRATIONS.REPLACE_FORBIDDEN'
    assert _rows(conn) == before

    assert _linkedin(ada, pid, 'acc-ada', name='Ada again', premium=None).is_ok()  # the connector
    assert _linkedin(cy, pid, 'acc-ada', name='Ada admin', premium=None).is_ok()  # an admin
