"""A member's release and a connect or link of the same mailbox in one client, on two connections (#1085): neither
deadlocks. The connect holds both rows of the mailbox (the legacy row and its account) while it moves the client's
link, pins and pool rows to the account (#1069). It locks the two in id order, and the release locks the account
rows in id order before it deletes their links.

Needs TEST_DATABASE_URL.
"""

import asyncio
import threading
from datetime import timedelta

import psycopg
import pytest

from app.integrations import oauth_client, project_senders, senders, token_crypto
from tests import test_dispatcher_sql
from tests.conftest import conninfo_of, member, project
from tests.test_dispatcher_sql import DB_URL, GMAIL_SEND, NOW, TWO_STEPS, _campaign
from tests.test_sender_pool_race_sql import Side, _waiting
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: release lock order SQL test NOT run')

ADDRESS = 'me@acme.test'


def _account(c, uid, org, pid, subject: str | None, sid: str) -> str:
    c.execute(
        """INSERT INTO user_oauth_connections (id, organization_id, user_id, provider, provider_account_id,
               account_label, refresh_token_encrypted, granted_scopes, status)
           VALUES (%s, %s, %s, 'google', %s, %s, %s, %s, 'connected')""",
        (sid, org, uid, subject, ADDRESS, token_crypto.encrypt('refresh-1').value, ['openid', GMAIL_SEND]),
    )
    c.execute(
        'INSERT INTO project_senders (project_id, sender_id, provider, organization_id) VALUES (%s, %s, %s, %s)',
        (pid, sid, 'google', org),
    )
    return sid


def _remove_member(c, uid, org):
    def remove():
        with psycopg.connect(conninfo_of(c), autocommit=True) as conn:
            conn.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (uid, org))

    return remove


@pytest.mark.parametrize('release', ['member_removed', 'release_senders_of'])
@pytest.mark.parametrize(
    ('released', 'entry', 'pause'),
    [
        # #1069 made the connect write the pool rows of the account it keeps while it holds both rows.
        pytest.param('account', 'connect', '_is_linked', id='account_whose_link_the_connect_keeps'),
        pytest.param('legacy', 'connect', '_is_linked', id='legacy_row_whose_link_the_connect_drops'),
        # Both rows are the member's, the legacy one first by id: a lock by role took the account first.
        pytest.param('both', 'connect', '_legacy_row', id='both_rows_while_the_connect_takes_them'),
        pytest.param('both', 'link', '_legacy_row', id='both_rows_while_the_link_takes_them'),
    ],
)
def test_a_release_and_a_connect_of_the_same_mailbox_both_finish(
    db, provider, monkeypatch, release, released, entry, pause
):
    ada, bo, cy = _user(db), _user(db), _user(db)
    pid, org = project(db, ada), member(db, ada).org_id
    for uid, role in ((bo, 'member'), (cy, 'admin')):
        db.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (org, uid, role),
        )
    legacy_owner, account_owner = {'account': (ada, bo), 'legacy': (bo, ada), 'both': (bo, bo)}[released]
    legacy = _account(db, legacy_owner, org, pid, None, '00000000-0000-4000-8000-000000000001')
    account = _account(db, account_owner, org, pid, 'sub-me', '80000000-0000-4000-8000-000000000001')
    cid = _campaign(db, ada, TWO_STEPS)
    db.execute('DELETE FROM outreach_campaign_senders WHERE campaign_id = %s AND sender_id = %s', (cid, account))
    _heard_step_one(db, 'anna@client.test', legacy, NOW - timedelta(days=4))

    async def revoke(self, token):
        return True

    monkeypatch.setattr(oauth_client.OAuthClient, 'revoke', revoke)
    paused, go = threading.Event(), threading.Event()
    paused_at = getattr(project_senders, pause)

    def held(*args):
        """The connect stops here: at ``_legacy_row`` it holds what it locks first, at ``_is_linked`` both rows."""
        paused.set()
        assert go.wait(10)
        return paused_at(*args)

    monkeypatch.setattr(project_senders, pause, held)
    admin = member(db, cy)
    if entry == 'link':
        connect = Side(admin, project_senders.link, admin, account, pid)
    else:
        connect = Side(
            admin,
            project_senders.attach,
            cy,
            pid,
            'google',
            account='sub-me',
            cols={
                'refresh_token_encrypted': token_crypto.encrypt('refresh-1').value,
                'granted_scopes': ['openid', GMAIL_SEND],
                'account_label': ADDRESS,
                'provider_account_id': 'sub-me',
            },
        )
    if release == 'member_removed':
        releasing = Side(admin, _remove_member(db, bo, org))
    else:
        releasing = Side(admin, lambda: asyncio.run(senders.release_senders_of(bo, org, 'owner_removed')))

    connect.start()
    assert paused.wait(10)
    releasing.start()
    _waiting(db, releasing)
    go.set()
    connect.join(15)
    releasing.join(15)

    assert not connect.is_alive() and not releasing.is_alive()
    assert (connect.error, releasing.error) == (None, None)
    assert connect.result.is_ok(), connect.result
    released_ids = {'account': [account], 'legacy': [legacy], 'both': [legacy, account]}[released]
    rows = db.execute('SELECT status FROM user_oauth_connections WHERE id = ANY(%s::uuid[])', (released_ids,))
    assert [r['status'] for r in rows] == ['disconnected'] * len(released_ids)
    assert (
        db.execute('SELECT 1 FROM project_senders WHERE sender_id = ANY(%s::uuid[])', (released_ids,)).fetchall() == []
    )
