"""A client's already-linked mailbox inherits its legacy row's conversations when that row leaves (#1099)."""

import asyncio
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from app.auth.middleware import CurrentUser
from app.core.system_reasons import SystemReason
from app.integrations import project_senders, senders
from app.outreach import dispatcher
from tests import test_dispatcher_sql, test_sender_pool_sql
from tests.conftest import call_as, call_system, member, project
from tests.test_dispatcher_sql import DB_URL, NOW, TWO_STEPS, _beat, _campaign, _each_org, _mailbox
from tests.test_legacy_mailbox_pins_sql import _reply_hold
from tests.test_release_lock_order_sql import _account
from tests.test_sender_pool_race_sql import _pool_of
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches
linking = test_sender_pool_sql.linking
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: legacy unlink SQL test NOT run')


def _world(c, account_first=False):
    ada, bo = _user(c), _user(c)
    member(c, bo)
    user, pid = member(c, ada), project(c, ada)
    c.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (user.org_id, bo),
    )
    low, high = '00000000-0000-4000-8000-000000000001', '80000000-0000-4000-8000-000000000001'
    legacy = _account(c, bo, user.org_id, pid, None, high if account_first else low)
    account = _account(c, ada, user.org_id, pid, 'sub-me', low if account_first else high)
    cid = _campaign(c, ada, TWO_STEPS, emails=('anna@client.test', 'cleo@client.test', 'unknown@client.test'))
    c.execute('DELETE FROM outreach_campaign_senders WHERE sender_id = %s', (account,))
    for email in ('anna@client.test', 'cleo@client.test', 'unknown@client.test'):
        _heard_step_one(c, email, legacy, NOW - timedelta(days=4))
    c.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE email = 'cleo@client.test'")
    _each_org(dispatcher.plan, NOW)
    c.execute(
        "UPDATE outreach_campaign_sends SET status = 'sending', sender_id = %s, sender_provider = 'google', attempts = 1"
        " WHERE recipient = 'unknown@client.test' AND step = 2",
        (legacy,),
    )
    return user, bo, pid, cid, legacy, account


def _leave(c, user, bo, pid, legacy, entry):
    if entry == 'unlink':
        assert call_as(user, project_senders.unlink, user, legacy, pid).is_ok()
    elif entry == 'member':
        c.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, user.org_id))
    elif entry == 'user':
        c.execute('DELETE FROM "user" WHERE id = %s', (bo,))
    else:

        async def release():
            return await senders.release_senders_of(bo, user.org_id, 'owner_removed')

        result = (
            call_system(SystemReason.ACCOUNT_DELETE, asyncio.run, release())
            if entry == 'system_release'
            else call_as(user, asyncio.run, release())
        )
        assert result == 1


@pytest.mark.parametrize('entry', ['unlink', 'member', 'user', 'release', 'system_release'])
async def test_an_already_linked_account_resumes_legacy_threads_without_touching_sends(
    db, provider, switches, linking, monkeypatch, entry
):
    user, bo, pid, cid, legacy, account = _world(db)

    async def revoke(row):
        pass

    monkeypatch.setattr('app.integrations.member_release.revoke_at_provider', revoke)
    pins = {r['id']: r['sender_pinned_at'] for r in db.execute('SELECT * FROM outreach_campaign_targets')}
    sends = db.execute('SELECT * FROM outreach_campaign_sends ORDER BY id').fetchall()
    if entry in ('release', 'system_release'):
        await asyncio.to_thread(_leave, db, user, bo, pid, legacy, entry)
    else:
        _leave(db, user, bo, pid, legacy, entry)

    assert db.execute('SELECT * FROM outreach_campaign_sends ORDER BY id').fetchall() == sends
    assert {r['id']: r['sender_pinned_at'] for r in db.execute('SELECT * FROM outreach_campaign_targets')} == pins
    assert {str(r['sender_id']) for r in db.execute('SELECT * FROM outreach_campaign_targets')} == {account}
    assert _pool_of(db, cid) == {account}
    assert _reply_hold(db, pid, 'cleo@client.test') is None
    await _beat()
    await _beat(NOW + timedelta(minutes=20))
    assert [m['To'] for m, _ in provider.sent] == ['anna@client.test']
    unknown = db.execute(
        "SELECT status, sender_id::text FROM outreach_campaign_sends WHERE recipient = 'unknown@client.test' AND step = 2"
    ).fetchone()
    assert unknown == {'status': 'sending', 'sender_id': legacy}


@pytest.fixture
def pinned_world(db, provider, linking):
    return _world(db)


@settings(max_examples=4, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(repetitions=st.integers(min_value=1, max_value=4))
@example(repetitions=1)
def test_legacy_reconciliation_is_idempotent(db, pinned_world, repetitions):
    with db.transaction(force_rollback=True):
        user, bo, pid, _cid, legacy, _account_id = pinned_world
        _leave(db, user, bo, pid, legacy, 'unlink')
        before = [
            db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
            for table in ('outreach_campaign_targets', 'outreach_campaign_sends')
        ]
        pool = db.execute('SELECT * FROM outreach_campaign_senders ORDER BY campaign_id, sender_id').fetchall()
        for _ in range(repetitions):
            db.execute('SELECT reconcile_legacy_pins(%s)', (pid,))
        assert [
            db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
            for table in ('outreach_campaign_targets', 'outreach_campaign_sends')
        ] == before
        assert db.execute('SELECT * FROM outreach_campaign_senders ORDER BY campaign_id, sender_id').fetchall() == pool


def test_unlink_preserves_other_organizations_and_a_colleagues_account(db, provider, linking):
    user, bo, pid, _cid, legacy, account = _world(db)
    foreign = member(db, bo)
    before = db.execute('SELECT * FROM project_senders ORDER BY project_id, sender_id').fetchall()
    refused = call_as(foreign, project_senders.unlink, foreign, legacy, pid)
    assert refused.is_err()
    assert refused.error.code == 'INTEGRATIONS.SENDER_NOT_FOUND'
    colleague = CurrentUser(id=bo, org_id=user.org_id, org_role='member')
    refused = call_as(colleague, project_senders.unlink, colleague, account, pid)
    assert refused.is_err()
    assert refused.error.code == 'INTEGRATIONS.LINK_FORBIDDEN'
    assert db.execute('SELECT * FROM project_senders ORDER BY project_id, sender_id').fetchall() == before


def test_a_members_release_does_not_move_their_legacy_pins_in_another_organization(db, provider, linking):
    user, bo, pid, _cid, legacy, _account_id = _world(db)
    other = member(db, bo)
    other_pid = project(db, bo)
    other_legacy = _mailbox(db, bo)
    _mailbox(db, bo, provider_account_id='sub-elsewhere')
    other_cid = _campaign(db, bo, TWO_STEPS, emails=('elsewhere@client.test',))
    _heard_step_one(db, 'elsewhere@client.test', other_legacy, NOW - timedelta(days=4))
    before = db.execute('SELECT * FROM outreach_campaign_targets WHERE campaign_id = %s', (other_cid,)).fetchall()
    _leave(db, user, bo, pid, legacy, 'member')
    assert (
        db.execute('SELECT * FROM outreach_campaign_targets WHERE campaign_id = %s', (other_cid,)).fetchall() == before
    )
    assert _pool_of(db, other_cid) == {
        other_legacy,
        str(
            db.execute(
                'SELECT id FROM user_oauth_connections WHERE organization_id = %s AND provider_account_id IS NOT NULL',
                (other.org_id,),
            ).fetchone()['id']
        ),
    }
    assert (
        db.execute('SELECT count(*) AS n FROM project_senders WHERE project_id = %s', (other_pid,)).fetchone()['n'] == 2
    )
