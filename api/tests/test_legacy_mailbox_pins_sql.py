"""A mailbox connected before #307 (a legacy row, no identity) and the identified account of the same address are one
mailbox (#1069). When a client moves from one to the other, the legacy row's conversations go on in their thread from
the account. A different mailbox gets none of them.

Needs TEST_DATABASE_URL. The mailbox and the token endpoint are faked.
"""

from datetime import timedelta

import psycopg
import pytest

from app.core.scope import acting_for
from app.integrations import project_senders, token_crypto
from app.outreach import dispatcher, sender_holds, sender_pool
from tests import test_dispatcher_sql, test_sender_pool_sql
from tests.conftest import call_as, link, member, project, scoped_conn
from tests.pin_invariant import PIN_INVARIANT_SQL
from tests.test_dispatcher_sql import DB_URL, GMAIL_SEND, NOW, TWO_STEPS, _beat, _campaign, _each_org, _mailbox
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches
linking = test_sender_pool_sql.linking

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: legacy mailbox pins SQL test NOT run')

ADDRESS = 'me@acme.test'


def _other_client(c, uid) -> str:
    return str(
        c.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'Client 2')"
            ' RETURNING id',
            (uid, uid),
        ).fetchone()['id']
    )


def _target(c, email: str) -> dict:
    return c.execute('SELECT * FROM outreach_campaign_targets WHERE email = %s', (email,)).fetchone()


def _reply_hold(c, pid: str, email: str):
    target = _target(c, email)
    org = c.execute('SELECT organization_id FROM projects WHERE id = %s', (pid,)).fetchone()['organization_id']
    with acting_for(str(org), 'test'), scoped_conn(c)() as conn:
        return sender_holds.reply_hold(conn, pid, target['id'], NOW)


def _connect(user, pid, subject: str, label: str = ADDRESS):
    return call_as(
        user,
        project_senders.attach,
        user.id,
        pid,
        'google',
        account=subject,
        cols={
            'refresh_token_encrypted': token_crypto.encrypt('refresh-1').value,
            'granted_scopes': ['openid', GMAIL_SEND],
            'account_label': label,
            'provider_account_id': subject,
        },
    )


@pytest.mark.parametrize(
    'relink', ['connect', 'connect_when_linked', 'connect_renamed', 'link', 'connect_after_unlink', 'link_after_unlink']
)
async def test_moving_to_the_identified_account_of_the_same_mailbox_keeps_its_conversations_sending(
    db, provider, switches, linking, relink
):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    legacy = _mailbox(db, uid, account_label=ADDRESS)
    _campaign(db, uid, TWO_STEPS, emails=('anna@client.test', 'cleo@client.test'))
    _heard_step_one(db, 'anna@client.test', legacy, NOW - timedelta(days=4))
    _heard_step_one(db, 'cleo@client.test', legacy, NOW - timedelta(days=4))
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE email = 'cleo@client.test'")
    pinned_at = _target(db, 'anna@client.test')['sender_pinned_at']
    # 'connect_renamed': the account's address changed at the provider since it was stored.
    stored = 'me@acme.onmicrosoft.test' if relink == 'connect_renamed' else ADDRESS
    account = _mailbox(db, uid, projects=[_other_client(db, uid)], account_label=stored, provider_account_id='sub-me')
    if relink == 'connect_when_linked':
        link(db, pid, account)
    if relink.endswith('_after_unlink'):
        assert call_as(user, project_senders.unlink, user, legacy, pid).is_ok()
    _each_org(dispatcher.plan, NOW)  # Anna's step 2 waits for her account

    if relink.startswith('link'):
        assert call_as(user, project_senders.link, user, account, pid).is_ok()
    else:
        assert _connect(user, pid, 'sub-me').is_ok()
    await _beat()
    await _beat(NOW + timedelta(minutes=20))

    ((sent, _),) = provider.sent
    assert (sent['To'], sent['From'], sent['In-Reply-To']) == ('anna@client.test', ADDRESS, '<step1@acme.test>')
    anna = _target(db, 'anna@client.test')
    assert (str(anna['sender_id']), anna['sender_pinned_at'], anna['next_step']) == (account, pinned_at, 3)
    assert _reply_hold(db, pid, 'cleo@client.test') is None
    assert db.execute(PIN_INVARIANT_SQL).fetchall() == []
    assert {
        str(r['sender_id']) for r in db.execute('SELECT sender_id FROM project_senders WHERE project_id = %s', (pid,))
    } == {account}


async def test_a_different_mailbox_gets_none_of_the_conversations(db, provider, switches, linking):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    legacy = _mailbox(db, uid, account_label=ADDRESS)
    cid = _campaign(db, uid, TWO_STEPS, emails=('anna@client.test', 'bea@client.test'))
    _heard_step_one(db, 'anna@client.test', legacy, NOW - timedelta(days=4))
    assert _connect(user, pid, 'sub-b', label='b@acme.test').is_ok()
    other = str(
        db.execute("SELECT id FROM user_oauth_connections WHERE provider_account_id = 'sub-b'").fetchone()['id']
    )
    sender_pool.pick(db, cid, [other])
    assert call_as(user, project_senders.unlink, user, legacy, pid).is_ok()

    await _beat()
    await _beat(NOW + timedelta(minutes=20))

    assert [(m['To'], m['From']) for m, _ in provider.sent] == [('bea@client.test', 'b@acme.test')]
    assert str(_target(db, 'anna@client.test')['sender_id']) == legacy
    assert _reply_hold(db, pid, 'anna@client.test') == 'unlinked'


async def test_another_link_keeps_the_account_that_inherited_the_legacy_conversations(db, provider, switches, linking):
    uid = _user(db)
    user, pid = member(db, uid), project(db, uid)
    legacy = _mailbox(db, uid, account_label=ADDRESS)
    _campaign(db, uid, TWO_STEPS)
    _heard_step_one(db, 'anna@client.test', legacy, NOW - timedelta(days=4))
    _mailbox(db, uid, account_label=ADDRESS, provider_account_id='sub-me')  # linked with the legacy row, as before
    assert call_as(user, project_senders.unlink, user, legacy, pid).is_ok()

    assert _connect(user, pid, 'sub-b', label='b@acme.test').is_ok()
    await _beat()
    await _beat(NOW + timedelta(minutes=20))

    assert [m['From'] for m, _ in provider.sent] == [ADDRESS]
    account = db.execute("SELECT id FROM user_oauth_connections WHERE provider_account_id = 'sub-me'").fetchone()['id']
    assert _target(db, 'anna@client.test')['sender_id'] == account


def _move_pin(c, target, account, pinned_at: str = 'sender_pinned_at') -> None:
    c.execute(
        f'UPDATE outreach_campaign_targets SET sender_id = %s, sender_pinned_at = {pinned_at} WHERE id = %s',
        (account, target),
    )


@pytest.mark.parametrize(
    ('start', 'to', 'pinned_at'),
    [
        pytest.param('legacy', 'other_address', 'sender_pinned_at', id='legacy_to_another_address'),
        pytest.param('account', 'second_account', 'sender_pinned_at', id='between_two_accounts_of_one_address'),
        pytest.param('account', 'legacy', 'sender_pinned_at', id='account_back_to_legacy'),
        pytest.param('legacy', 'account', 'now()', id='pin_time_moved'),
    ],
)
def test_a_pin_moves_only_from_a_legacy_row_to_its_account(db, provider, start, to, pinned_at):
    uid = _user(db)
    rows = {
        'legacy': _mailbox(db, uid, account_label=ADDRESS),
        'account': _mailbox(db, uid, projects=[], account_label=ADDRESS.upper(), provider_account_id='sub-me'),
        'second_account': _mailbox(db, uid, projects=[], account_label=ADDRESS, provider_account_id='sub-me-2'),
        'other_address': _mailbox(db, uid, projects=[], account_label='b@acme.test', provider_account_id='sub-b'),
    }
    _campaign(db, uid, TWO_STEPS)
    target = _heard_step_one(db, 'anna@client.test', rows[start], NOW - timedelta(days=4))

    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match='is final'):
        _move_pin(db, target, rows[to], pinned_at)
