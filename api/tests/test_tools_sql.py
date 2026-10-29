"""paid_call → vendor_calls and the LinkedIn connection row, against a real Postgres.

Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from app.billing import paid_call as pc
from app.billing.vendors import APOLLO
from app.core.config import settings
from app.core.errors import err, ok
from app.linkedin import api
from tests.conftest import act_in_own_org, migrated_database, pool_on, project, scoped_conn

DB_URL = os.environ.get('TEST_DATABASE_URL')

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: SQL tools test NOT run')


@pytest.fixture
def conn(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        fake = scoped_conn(c)
        monkeypatch.setattr(pc, 'get_conn', fake)
        monkeypatch.setattr(api, 'get_conn', fake)
        monkeypatch.setattr('app.integrations.project_senders.get_conn', fake)
        yield c


def _user(c) -> str:
    uid = uuid.uuid4()
    cols = {
        r['column_name']
        for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'user' AND table_schema = current_schema()"
        )
    }
    now = datetime.now(UTC)
    values = {
        'id': uid,
        'email': f'{uid}@x.test',
        'name': 'T',
        'emailVerified': False,
        'createdAt': now,
        'updatedAt': now,
    }
    use = {k: v for k, v in values.items() if k in cols}
    names = ', '.join(f'"{k}"' for k in use)
    c.execute(f'INSERT INTO "user" ({names}) VALUES ({", ".join(["%s"] * len(use))})', list(use.values()))
    return str(uid)


async def test_paid_call_writes_vendor_calls(conn):
    uid = _user(conn)
    act_in_own_org(conn, uid)  # a tool's vendor call runs in its turn's scope
    pc.current_user_id.set(uid)

    async def good():
        return ok([1, 2, 3])

    async def bad():
        return err('CONTACTS.RATE_LIMITED')

    await pc.paid_call('apollo.search_organizations', good)
    await pc.paid_call('apollo.match_person', bad)
    rows = conn.execute(
        'SELECT user_id::text, vendor, operation, cost_usd::float, success FROM vendor_calls ORDER BY created_at'
    ).fetchall()
    assert [tuple(r.values()) for r in rows] == [
        (uid, 'apollo', 'apollo.search_organizations', 0.02, True),
        (uid, 'apollo', 'apollo.match_person', 0.0, False),
    ]


def test_vendor_call_write_failure_never_raises(conn):
    act_in_own_org(conn, _user(conn))
    token = pc.current_user_id.set('not-a-uuid')
    try:
        pc.record_vendor_call(APOLLO, 'x', metered=1)  # logged, not raised
    finally:
        pc.current_user_id.reset(token)  # a sync test's context var outlives it on the worker


async def test_linkedin_connection_row(conn, monkeypatch):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    uid = _user(conn)
    pid = project(conn, uid)
    act_in_own_org(conn, uid)  # the connect request, then the reads of the user's own routes
    assert api._store(uid, pid, 'acc_1', name='Mario Rossi', premium=False).is_ok()
    assert api.connected_account_id(pid) == 'acc_1'
    row = api.get_account(pid)
    assert row['refresh_token_encrypted'] != 'acc_1'  # stored encrypted too, never plaintext only
    conn.execute("UPDATE user_oauth_connections SET status = 'error' WHERE user_id = %s", (uid,))
    assert api.connected_account_id(pid) is None
    api._store(uid, pid, 'acc_1', name=None, premium=None)  # the same account reconnected: its row, in place
    assert conn.execute('SELECT count(*) AS n FROM user_oauth_connections').fetchone()['n'] == 1
    assert api.connected_account_id(pid) == 'acc_1'
    api._store(uid, pid, 'acc_2', name=None, premium=None)  # another account: its own row, now the project's
    assert conn.execute('SELECT count(*) AS n FROM user_oauth_connections').fetchone()['n'] == 2
    assert api.connected_account_id(pid) == 'acc_2'
