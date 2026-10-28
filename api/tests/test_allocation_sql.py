"""Several mailbox accounts share a campaign's backlog, and an account's cap is its own (#935).

Needs TEST_DATABASE_URL. The mailbox and the token endpoint are faked.
"""

from datetime import timedelta

import pytest

from tests import test_dispatcher_sql
from tests.conftest import pool
from tests.pin_invariant import PIN_INVARIANT_SQL
from tests.test_dispatcher_sql import DB_URL, NOW, TWO_STEPS, _beat, _campaign, _mailbox
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: allocation SQL test NOT run')


def _sent(provider) -> list[tuple[str, str]]:
    return [(m['To'], m['From']) for m, _ in provider.sent]


async def test_three_accounts_on_one_backlog_send_three_in_one_beat(db, provider, switches):
    uid = _user(db)
    boxes = {_mailbox(db, uid, account_label=f'{n}@acme.test'): f'{n}@acme.test' for n in ('one', 'two', 'three')}
    _campaign(db, uid, emails=tuple(f'p{i}@client.test' for i in range(30)))

    counts = await _beat()

    assert counts['sent'] == 3
    assert sorted(sender for _, sender in _sent(provider)) == sorted(boxes.values())
    pinned = db.execute(
        'SELECT email, sender_id FROM outreach_campaign_targets WHERE sender_pinned_at IS NOT NULL'
    ).fetchall()
    assert {(r['email'], boxes[str(r['sender_id'])]) for r in pinned} == {
        (to, sender) for to, sender in _sent(provider)
    }
    assert db.execute(PIN_INVARIANT_SQL).fetchall() == []


async def test_a_capped_account_keeps_its_conversation_while_another_sends_new_ones(db, provider, switches):
    uid = _user(db)
    capped = _mailbox(db, uid, account_label='a@acme.test', daily_limit=1)
    _mailbox(db, uid, provider='microsoft', account_label='b@acme.test', granted_scopes=['User.Read', 'Mail.Send'])
    cid = _campaign(
        db, uid, TWO_STEPS, emails=('anna@client.test', 'zed@client.test', 'bea@client.test', 'carl@client.test')
    )
    pool(db, cid)
    _heard_step_one(db, 'anna@client.test', capped, NOW - timedelta(days=4))  # step 2 is due
    _heard_step_one(db, 'zed@client.test', capped, NOW - timedelta(minutes=30))  # a's one send today

    first = await _beat()
    await _beat(NOW + timedelta(minutes=20))

    assert first['capped'] == 1
    assert sorted(_sent(provider)) == [('bea@client.test', 'b@acme.test'), ('carl@client.test', 'b@acme.test')]
    anna = db.execute("SELECT sender_id FROM outreach_campaign_targets WHERE email = 'anna@client.test'").fetchone()
    assert str(anna['sender_id']) == capped
    assert db.execute(PIN_INVARIANT_SQL).fetchall() == []


async def test_two_campaigns_on_one_account_share_its_cap(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid, daily_limit=1)
    _campaign(db, uid, emails=('anna@client.test',))
    _campaign(db, uid, emails=('bea@client.test',))

    assert (await _beat())['sent'] == 1
    second = await _beat(NOW + timedelta(minutes=20))

    assert second['capped'] == 1 and second['sent'] == 0
    assert len(provider.sent) == 1
