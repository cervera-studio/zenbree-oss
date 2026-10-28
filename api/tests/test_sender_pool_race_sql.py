"""Needs TEST_DATABASE_URL."""

import contextlib
import threading
import time
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core import db as core_db
from app.integrations import project_senders, token_crypto
from app.outreach import drafts, pool_routes, routes, sender_pool
from app.outreach.pool_routes import PickIn
from tests import test_dispatcher_sql
from tests.conftest import as_request, conninfo_of, member, project
from tests.test_campaign_routes_sql import _list
from tests.test_dispatcher_sql import DB_URL, GMAIL_SEND, NOW, TWO_STEPS, _campaign, _mailbox
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: sender pool race SQL test NOT run')


class Side(threading.Thread):
    """One call on its own thread and connection, in a request of ``user``. Keeps what it returned or raised."""

    def __init__(self, user, fn, *args, **kwargs):
        super().__init__(daemon=True)
        self.user, self.call, self.result, self.error = user, (fn, args, kwargs), None, None

    def run(self):
        fn, args, kwargs = self.call
        try:
            with as_request(self.user):
                self.result = fn(*args, **kwargs)
        except Exception as e:
            self.error = e


def _waiting(c, side: Side | None = None) -> None:
    """Until a transaction waits on a row lock, or ``side`` finished without waiting."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if side is not None and not side.is_alive():
            return
        waits = c.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone()['n']
        if waits:
            return
        time.sleep(0.02)
    raise AssertionError('no transaction waits on a lock')


def _connect(entry: str, user, sender_id, account: str, pid):
    if entry == 'link':
        return lambda: project_senders.link(user, sender_id, pid)
    cols = {
        'refresh_token_encrypted': token_crypto.encrypt('refresh-1').value,
        'granted_scopes': ['openid', GMAIL_SEND],
        'account_label': f'{account}@acme.test',
        'provider_account_id': account,
    }
    return lambda: project_senders.attach(user.id, pid, 'google', account=account, cols=cols)


def _unlinked(c, uid, account: str) -> str:
    sid = _mailbox(c, uid, account_label=f'{account}@acme.test', provider_account_id=account)
    c.execute('DELETE FROM project_senders WHERE sender_id = %s', (sid,))
    return sid


def _pool_of(c, cid) -> set[str]:
    rows = c.execute('SELECT sender_id FROM outreach_campaign_senders WHERE campaign_id = %s', (cid,))
    return {str(r['sender_id']) for r in rows}


@pytest.fixture
def parked(monkeypatch):
    """``project_senders`` sets ``stopped`` once its work is done, then holds its locks and commits only after the
    test sets ``release``."""
    stopped, release = threading.Event(), threading.Event()
    real = core_db.get_conn

    @contextlib.contextmanager
    def parking(timeout=None):
        with real(timeout) as conn:
            yield conn
            stopped.set()
            assert release.wait(10), 'the test never let the link commit'

    monkeypatch.setattr(project_senders, 'get_conn', parking)
    return stopped, release


def _second_account(c):
    uid = _user(c)
    b = _unlinked(c, uid, 'sub-b')
    a = _mailbox(c, uid, account_label='sub-a@acme.test', provider_account_id='sub-a')
    cid = _campaign(c, uid, TWO_STEPS)
    c.execute('DELETE FROM outreach_campaign_senders')
    _heard_step_one(c, 'anna@client.test', b, NOW - timedelta(days=1))
    return member(c, uid), project(c, uid), cid, a, b


@pytest.mark.parametrize('entry', ['link', 'attach'])
def test_a_link_while_a_pick_holds_the_campaign_waits_and_pools_its_live_conversation(db, provider, entry):
    user, pid, cid, a, b = _second_account(db)
    link = Side(user, _connect(entry, user, b, 'sub-b', pid))
    picked = None
    with as_request(user):
        try:
            with core_db.get_conn() as conn:
                routes._own(conn, cid, user.org_id, lock=True)
                link.start()
                _waiting(db, link)
                sender_pool.pick(conn, cid, [a])
        except psycopg.errors.DeadlockDetected as e:
            picked = e
    link.join(10)

    assert (picked, link.error) == (None, None)
    assert link.result.is_ok(), link.result
    assert _pool_of(db, cid) == {a, b}


@pytest.mark.parametrize('entry', ['link', 'attach'])
def test_a_link_while_a_pick_holds_the_campaign_waits_and_joins_no_pool(db, provider, entry):
    uid = _user(db)
    microsoft = _mailbox(db, uid, provider='microsoft', granted_scopes=['User.Read', 'Mail.Send'])
    a = _unlinked(db, uid, 'sub-a')
    cid = _campaign(db, uid)
    user, pid = member(db, uid), project(db, uid)
    link = Side(user, _connect(entry, user, a, 'sub-a', pid))
    with as_request(user), core_db.get_conn() as conn:
        routes._own(conn, cid, user.org_id, lock=True)  # the PUT's lock order
        link.start()
        _waiting(db, link)
        sender_pool.pick(conn, cid, [microsoft])
    link.join(10)

    assert link.error is None
    assert link.result.is_ok(), link.result
    assert _pool_of(db, cid) == {microsoft}


@pytest.mark.parametrize('entry', ['link', 'attach'])
def test_a_pick_while_a_link_is_uncommitted_waits_and_sees_the_new_account(db, provider, parked, entry):
    stopped, release = parked
    user, pid, cid, _, b = _second_account(db)
    link = Side(user, _connect(entry, user, b, 'sub-b', pid))
    link.start()
    try:
        assert stopped.wait(10), 'the link never reached its commit'
        pick = Side(user, pool_routes.pick_senders, cid, PickIn(sender_ids=[b]), user)
        pick.start()
        _waiting(db, pick)
    finally:
        release.set()
    link.join(10)
    pick.join(10)

    assert (pick.error, link.error) == (None, None)
    assert _pool_of(db, cid) == {b}


@pytest.mark.parametrize('entry', ['link', 'attach'])
def test_a_new_campaign_while_a_link_is_uncommitted_starts_with_the_committed_accounts(db, provider, parked, entry):
    stopped, release = parked
    user, pid, _, a, b = _second_account(db)
    lid = _list(db, user.id, rows=[{'first_name': 'Dora', 'email': 'dora@client.test'}])

    def draft():
        with core_db.get_conn() as conn:
            return drafts.draft_from_list(conn, user.id, lid, 'New', reuse=False).id

    link = Side(user, _connect(entry, user, b, 'sub-b', pid))
    link.start()
    try:
        assert stopped.wait(10), 'the link never reached its commit'
        created = Side(user, draft)
        created.start()
        _waiting(db, created)
    finally:
        release.set()
    link.join(10)
    created.join(10)

    assert (created.error, link.error) == (None, None)
    assert _pool_of(db, created.result) == {a}


def test_a_link_waits_for_its_client_being_deleted(db, provider):
    uid = _user(db)
    a = _unlinked(db, uid, 'sub-a')
    _campaign(db, uid)
    user, pid = member(db, uid), project(db, uid)
    link = Side(user, project_senders.link, user, a, pid)
    with psycopg.connect(conninfo_of(db), row_factory=dict_row) as deleting:
        deleting.execute('SELECT 1 FROM projects WHERE id = %s FOR UPDATE', (pid,))
        link.start()
        _waiting(db, link)
        deleting.execute('DELETE FROM projects WHERE id = %s', (pid,))
    link.join(10)

    assert link.error is None
    assert link.result.error.code == 'INTEGRATIONS.PROJECT_NOT_FOUND'
