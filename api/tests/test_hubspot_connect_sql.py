"""HubSpot connection storage against real SQL (#120). Needs TEST_DATABASE_URL."""

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.migrate import MIGRATIONS, migrate
from app.core.scope import acting_as
from app.crm import write_back
from app.integrations import hubspot
from app.integrations.providers import HUBSPOT_CONTACTS_WRITE, HUBSPOT_READ_SCOPES, HUBSPOT_WRITE_SCOPES
from app.main import app
from tests.conftest import call_as, conninfo_of, empty_database, member, pool_on, scoped_conn, sign_in
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: HubSpot SQL test NOT run')


@pytest.fixture
def conn(monkeypatch):
    with empty_database() as c:
        migrate(c)
        for module in (hubspot, write_back):
            monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
        monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
        with pool_on(conninfo_of(c)):
            monkeypatch.setattr(_CONN, 'c', c)
            yield c


class _CONN:
    c = None


def _as(uid, fn, *args, **kwargs):
    """``fn`` in the scope of a request of ``uid``: connect, the switch and its state are the member's own."""
    return call_as(member(_CONN.c, uid), fn, *args, **kwargs)


def test_migration_is_rerunnable(conn):
    conn.execute(next(MIGRATIONS.glob('*_hubspot_gate.sql')).read_text())


def test_reconnect_upserts_and_disconnect_drops_the_token(conn):
    uid = _user(conn)
    other = _user(conn)
    org = member(conn, uid).org_id
    _as(uid, hubspot._store, uid, org, encrypted='ct-1', scopes=['oauth'], label='old.hubspot.com', portal_id='1')
    conn.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason) VALUES (%s, 'a@b.test', 'HUBSPOT_CUSTOMER'),"
        " (%s, 'a@b.test', 'HUBSPOT_CUSTOMER')",
        (uid, other),
    )
    # Reconnecting another portal replaces it, never keeps the old one.
    _as(uid, hubspot._store, uid, org, encrypted='ct-2', scopes=['oauth'], label='acme.hubspot.com', portal_id='4242')
    # Reconnecting re-evaluates this user's people from scratch; nobody else's verdicts are touched.
    assert [str(r['owner_id']) for r in conn.execute('SELECT owner_id FROM crm_gate_checks')] == [other]
    # A colleague connects the same portal of the same organization: their own row, not a sender-style merge.
    conn.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, other),
    )
    with acting_as(org, other):
        hubspot._store(other, org, encrypted='ct-x', scopes=['oauth'], label='acme.hubspot.com', portal_id='4242')
    assert _as(uid, hubspot.connection, uid) == {
        'status': 'connected',
        'account_label': 'acme.hubspot.com',
        'provider_account_id': '4242',
    }

    sign_in(app, member(conn, uid))
    try:
        assert TestClient(app).delete('/v1/integrations/hubspot').json() == {'connected': False}
    finally:
        app.dependency_overrides.clear()
    rows = {
        r['user_id']: r
        for r in conn.execute(
            "SELECT user_id::text, status, refresh_token_encrypted FROM user_oauth_connections WHERE provider = 'hubspot'"
        )
    }
    assert rows[uid]['status'] == 'disconnected' and rows[uid]['refresh_token_encrypted'] == ''
    assert rows[other]['status'] == 'connected' and rows[other]['refresh_token_encrypted'] == 'ct-x'


def test_a_portal_change_drops_the_old_portals_write_history_and_a_reconnect_keeps_it(conn):
    uid = _user(conn)
    org = member(conn, uid).org_id
    connect = {'encrypted': 'ct', 'scopes': ['oauth'], 'label': 'acme.hubspot.com'}
    _as(uid, hubspot._store, uid, org, portal_id='1', **connect)
    conn.execute(
        "INSERT INTO crm_contact_writes (owner_id, email, contact_id, created) VALUES (%s, 'a@b.test', 'c1', true)",
        (uid,),
    )
    _as(uid, hubspot._store, uid, org, portal_id='1', **connect)
    assert conn.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 1
    _as(uid, hubspot._store, uid, org, portal_id='2', **connect)
    assert conn.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 0


def test_a_portal_change_restarts_write_back_from_now_and_a_same_portal_reconnect_keeps_since(conn):
    uid = _user(conn)
    org = member(conn, uid).org_id
    connect = {'encrypted': 'ct', 'scopes': [*WRITE], 'label': 'acme.hubspot.com'}
    since = lambda: conn.execute('SELECT crm_write_back_since AS s FROM user_oauth_connections').fetchone()['s']  # noqa: E731
    _as(uid, hubspot._store, uid, org, portal_id='1', **connect)
    assert _as(uid, write_back.set_enabled, uid, True) == 'ok'
    conn.execute("UPDATE user_oauth_connections SET crm_write_back_since = now() - interval '2 days'")
    old = since()
    _as(uid, hubspot._store, uid, org, portal_id='1', **connect)
    assert since() == old
    _as(uid, hubspot._store, uid, org, portal_id='2', **connect)
    assert since() > old


READ = ['oauth', *HUBSPOT_READ_SCOPES]
WRITE = [*READ, *HUBSPOT_WRITE_SCOPES]


def _connect(conn, uid, scopes):
    _as(
        uid,
        hubspot._store,
        uid,
        member(conn, uid).org_id,
        encrypted='ct',
        scopes=scopes,
        label='acme.hubspot.com',
        portal_id='1',
    )


def test_write_back_needs_the_write_scopes_and_is_off_until_turned_on(conn):
    uid = _user(conn)
    assert _as(uid, write_back.state, uid) == {'can_write': False, 'can_write_companies': False, 'write_back': False}
    assert _as(uid, write_back.set_enabled, uid, True) == 'not_connected'

    _connect(conn, uid, READ)  # a connection from before #526, or a portal that cannot give the write scopes
    assert _as(uid, write_back.state, uid) == {'can_write': False, 'can_write_companies': False, 'write_back': False}
    assert _as(uid, write_back.set_enabled, uid, True) == 'no_write_scope'
    assert _as(uid, write_back.set_enabled, uid, False) == 'ok'  # stopping never needs a permission

    _connect(conn, uid, WRITE)  # reconnected: the grant can write, and nothing is on by itself
    assert _as(uid, write_back.state, uid) == {'can_write': True, 'can_write_companies': True, 'write_back': False}
    assert _as(uid, write_back.set_enabled, uid, True) == 'ok'
    assert _as(uid, write_back.state, uid) == {'can_write': True, 'can_write_companies': True, 'write_back': True}


def test_write_back_survives_a_reconnection_and_ends_with_the_grant(conn):
    uid, colleague = _user(conn), _user(conn)
    _connect(conn, uid, WRITE)
    _connect(conn, colleague, WRITE)
    _as(uid, write_back.set_enabled, uid, True)
    assert _as(colleague, write_back.state, colleague) == {
        'can_write': True,
        'can_write_companies': True,
        'write_back': False,
    }  # each user's own switch

    _connect(conn, uid, WRITE)
    assert _as(uid, write_back.state, uid) == {
        'can_write': True,
        'can_write_companies': True,
        'write_back': True,
    }  # the choice is kept
    _connect(conn, uid, READ)  # the write scopes were taken away: nothing writes, whatever the switch says
    assert _as(uid, write_back.state, uid) == {'can_write': False, 'can_write_companies': False, 'write_back': False}
    _connect(conn, uid, WRITE)
    assert _as(uid, write_back.state, uid)['write_back'] is True

    conn.execute("UPDATE user_oauth_connections SET status = 'error' WHERE user_id = %s", (uid,))
    assert _as(uid, write_back.state, uid) == {
        'can_write': False,
        'can_write_companies': False,
        'write_back': False,
    }  # a grant that cannot be read
    conn.execute("UPDATE user_oauth_connections SET status = 'connected' WHERE user_id = %s", (uid,))

    sign_in(app, member(conn, uid))
    try:
        TestClient(app).delete('/v1/integrations/hubspot')
    finally:
        app.dependency_overrides.clear()
    _connect(conn, uid, WRITE)  # connecting again after a disconnect starts from off
    assert _as(uid, write_back.state, uid) == {'can_write': True, 'can_write_companies': True, 'write_back': False}


def test_write_back_remembers_since_when_it_is_on(conn):
    """#527: only people written to after the switch went on get a contact."""
    uid = _user(conn)
    _connect(conn, uid, WRITE)
    since = 'SELECT crm_write_back_since AS at FROM user_oauth_connections WHERE user_id = %s'
    assert conn.execute(since, (uid,)).fetchone()['at'] is None
    _as(uid, write_back.set_enabled, uid, True)
    first = conn.execute(since, (uid,)).fetchone()['at']
    assert first is not None
    conn.execute("UPDATE user_oauth_connections SET crm_write_back_since = now() - interval '3 days'")
    _as(uid, write_back.set_enabled, uid, True)  # already on: the moment does not move
    _connect(conn, uid, WRITE)  # nor does a reconnection move it
    assert conn.execute(since, (uid,)).fetchone()['at'] < first
    _as(uid, write_back.set_enabled, uid, False)
    assert conn.execute(since, (uid,)).fetchone()['at'] is None
    _as(uid, write_back.set_enabled, uid, True)
    assert conn.execute(since, (uid,)).fetchone()['at'] >= first  # on again: from now, not from the first time


def test_the_contacts_permission_alone_is_enough_to_write(conn):
    """HubSpot's consent lists the optional permissions one by one: a user may tick contacts and not companies
    (seen on a real portal). Write-back then works, without the companies."""
    uid = _user(conn)
    _connect(conn, uid, [*READ, HUBSPOT_CONTACTS_WRITE])
    assert _as(uid, write_back.state, uid) == {'can_write': True, 'can_write_companies': False, 'write_back': False}
    assert _as(uid, write_back.set_enabled, uid, True) == 'ok'
    assert _as(uid, write_back.state, uid) == {'can_write': True, 'can_write_companies': False, 'write_back': True}

    other = _user(conn)
    _connect(conn, other, [*READ, 'crm.objects.companies.write'])  # companies without contacts writes nothing
    assert _as(other, write_back.state, other) == {
        'can_write': False,
        'can_write_companies': False,
        'write_back': False,
    }
    assert _as(other, write_back.set_enabled, other, True) == 'no_write_scope'
