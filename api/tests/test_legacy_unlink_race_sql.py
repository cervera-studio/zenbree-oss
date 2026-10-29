"""Unlink and release wait before taking accounts when a campaign's pool is being edited (#1099)."""

import threading

import psycopg
import pytest
from psycopg.rows import dict_row

from app.integrations import project_senders
from app.outreach import sender_pool
from tests import test_dispatcher_sql
from tests.conftest import conninfo_of
from tests.test_dispatcher_sql import DB_URL
from tests.test_legacy_mailbox_pins_sql import _connect
from tests.test_legacy_unlink_sql import _leave, _world
from tests.test_sender_pool_race_sql import Side, _pool_of, _waiting

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: legacy unlink race SQL test NOT run')


@pytest.mark.parametrize(
    ('entry', 'linked'),
    [
        ('unlink', True),
        ('member', True),
        ('user', True),
        ('release', True),
        ('member', False),
        ('user', False),
        ('release', False),
    ],
)
def test_a_pool_edit_can_lock_accounts_while_unlink_or_release_waits_on_its_campaign(
    db, provider, monkeypatch, entry, linked
):
    user, bo, pid, cid, legacy, account = _world(db)
    if not linked:
        db.execute('DELETE FROM project_senders WHERE sender_id = %s', (legacy,))

    async def revoke(row):
        pass

    monkeypatch.setattr('app.integrations.member_release.revoke_at_provider', revoke)

    def leave():
        with psycopg.connect(conninfo_of(db), autocommit=True) as conn:
            _leave(conn, user, bo, pid, legacy, entry)

    leaving = Side(user, leave)
    try:
        with psycopg.connect(conninfo_of(db), row_factory=dict_row) as picking:
            picking.execute("SET LOCAL lock_timeout = '2s'")
            picking.execute('SELECT 1 FROM outreach_campaigns WHERE id = %s FOR UPDATE', (cid,))
            leaving.start()
            _waiting(db, leaving)
            assert leaving.is_alive(), 'release skipped the campaign lock'
            picking.execute(
                'SELECT 1 FROM user_oauth_connections WHERE id = ANY(%s::uuid[]) ORDER BY id FOR UPDATE',
                ([legacy, account],),
            )
            sender_pool.pick(picking, cid, [account])
    finally:
        leaving.join(15)
    assert not leaving.is_alive()
    assert leaving.error is None
    assert _pool_of(db, cid) == {account}


@pytest.mark.parametrize('leave_by', ['member', 'unlink'])
@pytest.mark.parametrize('account_first', [False, True])
@pytest.mark.parametrize('entry', ['link', 'connect'])
def test_a_connect_behind_a_release_waits_in_account_id_order(db, provider, entry, account_first, leave_by):
    user, bo, pid, cid, legacy, account = _world(db, account_first)

    def leave():
        with psycopg.connect(conninfo_of(db), autocommit=True) as conn:
            _leave(conn, user, bo, pid, legacy, leave_by)

    leaving = Side(user, leave)
    connecting = (
        Side(user, project_senders.link, user, account, pid)
        if entry == 'link'
        else Side(user, _connect, user, pid, 'sub-me')
    )
    try:
        with psycopg.connect(conninfo_of(db)) as held:
            held.execute('SELECT 1 FROM user_oauth_connections WHERE id = %s FOR UPDATE', (legacy,))
            leaving.start()
            _waiting(db, leaving)
            connecting.start()
            _waiting(db, connecting)
    finally:
        leaving.join(15)
        connecting.join(15)
    assert not leaving.is_alive() and not connecting.is_alive()
    assert (leaving.error, connecting.error) == (None, None)
    assert connecting.result.is_ok()
    assert _pool_of(db, cid) == {account}


def test_a_release_locks_legacy_and_identified_accounts_in_the_connects_order(db, provider, monkeypatch):
    user, bo, pid, _cid, legacy, _account_id = _world(db)
    locked, go = threading.Event(), threading.Event()
    real = project_senders._lock_in_id_order

    def lock_legacy_first(conn, *args, **kwargs):
        conn.execute('SELECT 1 FROM user_oauth_connections WHERE id = %s FOR UPDATE', (legacy,))
        locked.set()
        assert go.wait(10)
        real(conn, *args, **kwargs)

    monkeypatch.setattr(project_senders, '_lock_in_id_order', lock_legacy_first)
    connecting = Side(user, _connect, user, pid, 'sub-me')

    def leave():
        with psycopg.connect(conninfo_of(db), autocommit=True) as conn:
            _leave(conn, user, bo, pid, legacy, 'member')

    leaving = Side(user, leave)
    connecting.start()
    try:
        assert locked.wait(10)
        leaving.start()
        _waiting(db, leaving)
    finally:
        go.set()
        connecting.join(15)
        leaving.join(15)
    assert not connecting.is_alive() and not leaving.is_alive()
    assert (connecting.error, leaving.error) == (None, None)
    assert connecting.result.is_ok()
