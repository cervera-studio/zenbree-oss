"""A conversation keeps its mailbox account (#935): the send claim pins it, and the queue never moves it.

Needs TEST_DATABASE_URL. The mailbox and the token endpoint are faked.
"""

from datetime import timedelta

import pytest

from app.core.errors import err, ok
from app.core.mail_threading import ProviderReceipt
from app.core.scope import acting_for
from app.outreach import dispatcher
from tests import test_dispatcher_sql
from tests.conftest import pool
from tests.pin_invariant import PIN_INVARIANT_SQL
from tests.test_dispatcher_sql import (
    DB_URL,
    NOW,
    TWO_STEPS,
    _beat,
    _campaign,
    _each_org,
    _each_org_async,
    _li_campaign,
    _linkedin,
    _mailbox,
)
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches
unipile = test_dispatcher_sql.unipile

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: sender slot SQL test NOT run')


def _heard_step_one(c, email: str, account: str, at) -> str:
    """The person got step 1 from ``account`` at ``at``: their conversation is on it."""
    target = c.execute('SELECT * FROM outreach_campaign_targets WHERE email = %s', (email,)).fetchone()
    c.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               subject, body, idempotency_key, attempts, attempted_at, delivered_at, sender_id, sender_provider,
               message_id)
           VALUES (%s, %s, %s, 1, 'email', 'sent', %s, 'Ciao', 'Ciao', gen_random_uuid()::text, 1, %s, %s, %s,
                   'google', '<step1@acme.test>')""",
        (target['campaign_id'], target['owner_id'], target['id'], email, at, at, account),
    )
    c.execute(
        'UPDATE outreach_campaign_targets SET next_step = 2, sender_id = %s, sender_pinned_at = %s WHERE id = %s',
        (account, at, target['id']),
    )
    return str(target['id'])


async def test_replacing_the_clients_account_does_not_move_its_conversations(db, provider, switches):
    uid = _user(db)
    second = _mailbox(db, uid, account_label='second@acme.test')
    db.execute('DELETE FROM project_senders WHERE sender_id = %s', (second,))
    first = _mailbox(db, uid, account_label='first@acme.test')
    cid = _campaign(db, uid, TWO_STEPS, emails=('anna@client.test', 'bea@client.test'))
    pool(db, cid, ('google',))
    anna = _heard_step_one(db, 'anna@client.test', first, NOW - timedelta(days=4))
    db.execute('UPDATE project_senders SET sender_id = %s WHERE sender_id = %s', (second, first))  # as attach does

    await _beat()
    await _beat(NOW + timedelta(minutes=20))

    assert [(m['To'], m['From']) for m, _ in provider.sent] == [('bea@client.test', 'second@acme.test')]
    pinned = db.execute('SELECT sender_id FROM outreach_campaign_targets WHERE id = %s', (anna,)).fetchone()
    assert str(pinned['sender_id']) == first
    assert db.execute(PIN_INVARIANT_SQL).fetchall() == []


def _approved(c, email: str) -> dict:
    """The person's planned email, approved, as the beat's queue reads it."""
    return c.execute(
        """SELECT s.* FROM outreach_campaign_sends s JOIN outreach_campaign_targets t ON t.id = s.target_id
           WHERE t.email = %s AND s.status = 'approved'""",
        (email,),
    ).fetchone()


def _pin(c, email: str) -> tuple:
    t = c.execute('SELECT sender_id, sender_pinned_at FROM outreach_campaign_targets WHERE email = %s', (email,))
    row = t.fetchone()
    return (row['sender_id'] and str(row['sender_id']), row['sender_pinned_at'])


def _account(c, sender_id: str) -> dict:
    return c.execute('SELECT * FROM user_oauth_connections WHERE id = %s', (sender_id,)).fetchone()


def test_a_lost_slot_or_a_full_cap_leaves_no_pin(db, provider):
    uid = _user(db)
    box = _mailbox(db, uid)
    _campaign(db, uid, emails=('anna@client.test', 'other@client.test'))
    _heard_step_one(db, 'other@client.test', box, NOW - timedelta(minutes=30))  # today's one send
    _each_org(dispatcher.plan, NOW)
    send, read = _approved(db, 'anna@client.test'), _account(db, box)
    db.execute('UPDATE user_oauth_connections SET last_used_at = %s', (NOW - timedelta(hours=1),))  # another worker
    org = str(send['organization_id'])
    with acting_for(org, 'campaign beat'):
        assert dispatcher._take_slot(send, read, 'google', NOW) is False
    assert _pin(db, 'anna@client.test') == (None, None) and _approved(db, 'anna@client.test')

    db.execute('UPDATE user_oauth_connections SET daily_limit = 1')
    with acting_for(org, 'campaign beat'):
        assert dispatcher._take_slot(send, _account(db, box), 'google', NOW) is False
    assert _pin(db, 'anna@client.test') == (None, None) and _approved(db, 'anna@client.test')

    db.execute('UPDATE user_oauth_connections SET daily_limit = 5')
    with acting_for(org, 'campaign beat'):
        assert dispatcher._take_slot(send, _account(db, box), 'google', NOW) is True
    assert _pin(db, 'anna@client.test') == (box, NOW)
    assert db.execute(PIN_INVARIANT_SQL).fetchall() == []


def test_a_person_who_stopped_or_an_account_out_of_the_pool_is_not_pinned(db, provider):
    uid = _user(db)
    box = _mailbox(db, uid)
    cid = _campaign(db, uid)
    _each_org(dispatcher.plan, NOW)
    send = _approved(db, 'anna@client.test')
    org = str(send['organization_id'])
    pool_row = db.execute('DELETE FROM outreach_campaign_senders WHERE campaign_id = %s RETURNING *', (cid,))
    removed = pool_row.fetchone()
    with acting_for(org, 'campaign beat'):
        assert dispatcher._take_slot(send, _account(db, box), 'google', NOW) is False
    db.execute(
        'INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)'
        ' VALUES (%(campaign_id)s, %(sender_id)s, %(project_id)s, %(provider)s, %(organization_id)s)',
        removed,
    )
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied'")  # replies.record, after the queue read
    with acting_for(org, 'campaign beat'):
        assert dispatcher._take_slot(send, _account(db, box), 'google', NOW) is False
    assert _pin(db, 'anna@client.test') == (None, None) and _approved(db, 'anna@client.test')


def test_a_conversation_on_one_account_is_not_claimed_by_another(db, provider):
    uid = _user(db)
    google = _mailbox(db, uid)
    microsoft = _mailbox(db, uid, provider='microsoft', granted_scopes=['User.Read', 'Mail.Send'])
    cid = _campaign(db, uid, TWO_STEPS)
    pool(db, cid)
    at = NOW - timedelta(days=4)
    _heard_step_one(db, 'anna@client.test', google, at)
    _each_org(dispatcher.plan, NOW)
    send = _approved(db, 'anna@client.test')
    before = _account(db, microsoft)
    with acting_for(str(send['organization_id']), 'campaign beat'):
        assert dispatcher._take_slot(send, before, 'microsoft', NOW) is False
    assert _pin(db, 'anna@client.test') == (google, at) and _approved(db, 'anna@client.test')
    assert _account(db, microsoft)['last_used_at'] == before['last_used_at']

    with acting_for(str(send['organization_id']), 'campaign beat'):
        assert dispatcher._take_slot(send, _account(db, google), 'google', NOW) is True
    assert _pin(db, 'anna@client.test') == (google, at)


async def test_a_follow_up_goes_before_a_new_conversation(db, provider, switches):
    uid = _user(db)
    box = _mailbox(db, uid)
    _campaign(db, uid, TWO_STEPS, emails=('anna@client.test', 'bea@client.test'))
    _heard_step_one(db, 'anna@client.test', box, NOW - timedelta(days=4))
    _each_org(dispatcher.plan, NOW)
    db.execute(
        "UPDATE outreach_campaign_sends SET approved_at = %s WHERE recipient = 'bea@client.test' AND step = 1",
        (NOW - timedelta(hours=2),),
    )
    db.execute(
        "UPDATE outreach_campaign_sends SET approved_at = %s WHERE recipient = 'anna@client.test' AND step = 2",
        (NOW - timedelta(hours=1),),
    )

    assert (await _each_org_async(dispatcher.send_due, NOW))['sent'] == 1

    assert [m['To'] for m, _ in provider.sent] == ['anna@client.test']


async def test_a_conversation_with_no_known_account_waits_for_a_person(db, provider, switches):
    uid = _user(db)
    box = _mailbox(db, uid)
    _campaign(db, uid, TWO_STEPS, emails=('anna@client.test', 'bea@client.test'))
    _heard_step_one(db, 'anna@client.test', box, NOW - timedelta(days=4))
    db.execute(
        "UPDATE outreach_campaign_sends SET sender_id = NULL WHERE recipient = 'anna@client.test';"
        "UPDATE outreach_campaign_targets SET sender_id = NULL WHERE email = 'anna@client.test'"
    )  # a thread from before #307: pinned, account unknown

    for minutes in (0, 20, 40):
        await _beat(NOW + timedelta(minutes=minutes))

    assert [m['To'] for m, _ in provider.sent] == ['bea@client.test']


async def test_an_uncertain_send_is_never_resent_and_the_pin_rule_holds(db, provider, switches):
    uid = _user(db)
    box = _mailbox(db, uid)
    _campaign(db, uid, emails=('anna@client.test', 'bea@client.test'))
    provider.result = err('INTEGRATIONS.TRANSPORT', detail='timeout', delivery_ambiguous=True)
    assert (await _beat())['unknown'] == 1
    provider.result = ok(ProviderReceipt(message_id='g2', thread_id='th2'))
    for minutes in (20, 40, 60):
        await _beat(NOW + timedelta(minutes=minutes))

    assert sorted(m['To'] for m, _ in provider.sent) == ['anna@client.test', 'bea@client.test']
    statuses = {
        r['recipient']: r['status'] for r in db.execute('SELECT recipient, status FROM outreach_campaign_sends')
    }
    assert sorted(statuses.values()) == ['sending', 'sent']
    assert {_pin(db, e)[0] for e in ('anna@client.test', 'bea@client.test')} == {box}
    assert db.execute(PIN_INVARIANT_SQL).fetchall() == []


async def test_a_linkedin_step_claims_through_the_link_and_writes_no_pin(db, unipile, switches):
    uid = _user(db)
    _linkedin(db, uid)
    _li_campaign(db, uid)
    _each_org(dispatcher.plan, NOW)
    send = db.execute('SELECT * FROM outreach_campaign_sends').fetchone()
    acct = db.execute("SELECT * FROM user_oauth_connections WHERE provider = 'unipile'").fetchone()
    link = db.execute('DELETE FROM project_senders WHERE sender_id = %s RETURNING *', (acct['id'],)).fetchone()
    with acting_for(str(send['organization_id']), 'campaign beat'):
        assert dispatcher._take_slot(send, acct, 'unipile', NOW) is False
    db.execute(
        'INSERT INTO project_senders (project_id, sender_id, provider, organization_id)'
        ' VALUES (%(project_id)s, %(sender_id)s, %(provider)s, %(organization_id)s)',
        link,
    )
    assert (await _each_org_async(dispatcher.send_linkedin_due, NOW))['linkedin_sent'] == 1
    assert unipile.calls == [('invite', 'ACo1', 'Ciao Anna')]
    target = db.execute('SELECT sender_id, sender_pinned_at FROM outreach_campaign_targets').fetchone()
    assert target == {'sender_id': None, 'sender_pinned_at': None}
