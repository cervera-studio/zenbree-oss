"""#343: what the dispatcher tells the owner about a campaign's addresses and a sender that stopped working.

Needs TEST_DATABASE_URL; the throwaway database and the fakes are test_dispatcher_sql's.
"""

import uuid
from datetime import timedelta

import httpx
import pytest

from app.core.config import settings
from app.notifications import service as n
from app.outreach import dispatcher
from tests import test_dispatcher_sql
from tests.conftest import call_for, each_org, each_org_async, scoped_conn
from tests.test_dispatcher_sql import DB_URL, NOW, _campaign, _mailbox
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: notification trigger tests NOT run')
db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches


@pytest.fixture(autouse=True)
def delivery(db, http, monkeypatch):
    monkeypatch.setattr(n, 'get_conn', scoped_conn(db))
    monkeypatch.setattr(settings, 'resend_api_key', 're_test')
    monkeypatch.setattr(settings, 'email_from', 'Zenbree <noreply@zenbree.test>')
    http.handler = lambda r: httpx.Response(200, json={'id': 'mail'})


def _kinds(c, kind: str) -> list[dict]:
    return c.execute(
        'SELECT user_id::text, href, data, emailed_at IS NOT NULL AS emailed, organization_id IS NOT NULL AS scoped'
        ' FROM notifications WHERE kind = %s',
        (kind,),
    ).fetchall()


def _seed(c, cid: str, uid: str, *, sent: int, invalid: int) -> None:
    """``sent`` people the campaign wrote to and ``invalid`` whose address could not be written to."""
    for i in range(sent + invalid):
        bad = i >= sent
        tid = c.execute(
            "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, %s, 'active')"
            ' RETURNING id',
            (cid, uid, f'{uuid.uuid4().hex[:8]}@seed.test'),
        ).fetchone()['id']
        c.execute(
            """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status,
                   recipient, error_code, idempotency_key, delivered_at)
               VALUES (%s, %s, %s, 1, 'email', %s, 'x@seed.test', %s, %s, %s)""",
            (
                cid,
                uid,
                tid,
                'skipped' if bad else 'sent',
                'INVALID_EMAIL' if bad else None,
                f'{cid}:{tid}:1',
                None if bad else NOW,
            ),
        )


def _bounces(c, cid: str) -> int:
    notified = n.notify_bounces(c, cid)
    n.deliver(notified)
    return len(_kinds(c, 'bounces_high'))


def test_bounces_need_twenty_attempts_and_more_than_five_percent_and_fire_once_per_campaign(db, http):
    uid = _user(db)
    cid = _campaign(db, uid, emails=())
    _seed(db, cid, uid, sent=14, invalid=5)  # 26%, but only 19 tried
    assert _bounces(db, cid) == 0
    other = _campaign(db, uid, emails=())
    _seed(db, other, uid, sent=19, invalid=1)  # 20 tried, exactly 5%: not above it
    assert _bounces(db, other) == 0
    _seed(db, other, uid, sent=1, invalid=0)  # 21 tried, 1 refused: 4.8%
    assert _bounces(db, other) == 0
    _seed(db, other, uid, sent=0, invalid=1)  # 22 tried, 2 refused: 9%
    assert _bounces(db, other) == 1
    (row,) = _kinds(db, 'bounces_high')
    assert row['user_id'] == uid and row['href'] == f'/campaigns/{other}' and row['scoped']
    assert row['data'] == {'campaign': 'C', 'invalid': 2, 'attempted': 22}
    assert row['emailed'] and len(http.calls) == 1  # always emails: no toggle
    _seed(db, other, uid, sent=0, invalid=5)  # a second crossing, far worse
    assert _bounces(db, other) == 1
    _seed(db, cid, uid, sent=1, invalid=0)  # the first campaign reaches 20 tried: its own notice
    assert _bounces(db, cid) == 2


def test_a_campaign_with_no_owner_left_is_not_notified(db):
    uid = _user(db)
    cid = _campaign(db, uid, emails=())
    _seed(db, cid, uid, sent=18, invalid=4)
    db.execute('UPDATE outreach_campaigns SET owner_id = NULL WHERE id = %s', (cid,))
    assert n.notify_bounces(db, cid) is None


async def test_the_planner_and_the_sender_ask_for_the_bounce_check(db, provider, switches):
    uid = _user(db)
    _mailbox(db, uid)
    cid = _campaign(db, uid, emails=('a@x.com, b@y.com', 'c@x.com, d@y.com'))  # two addresses in a cell: never sent
    _seed(db, cid, uid, sent=19, invalid=0)
    each_org(db, dispatcher.plan, NOW)
    assert [r['href'] for r in _kinds(db, 'bounces_high')] == [f'/campaigns/{cid}']
    each_org(db, dispatcher.plan, NOW + timedelta(minutes=1))
    assert len(_kinds(db, 'bounces_high')) == 1

    refused = _campaign(db, uid, emails=('one@client.test', 'two@client.test'))
    _seed(db, refused, uid, sent=19, invalid=0)
    each_org(db, dispatcher.plan, NOW)
    db.execute("UPDATE outreach_campaign_sends SET recipient = 'a@x.com, b@y.com' WHERE status = 'approved'")
    assert (await each_org_async(db, dispatcher.send_due, NOW))['refused_recipient'] == 1  # 20 tried, 1 refused: 5%
    assert len(_kinds(db, 'bounces_high')) == 1
    assert (await each_org_async(db, dispatcher.send_due, NOW))['refused_recipient'] == 1  # 21 tried, 2 refused
    assert {r['href'] for r in _kinds(db, 'bounces_high')} == {f'/campaigns/{cid}', f'/campaigns/{refused}'}


async def test_a_fresh_campaign_with_three_invalid_of_thirty_addresses_raises_one_notice_at_the_first_plan(
    db, provider, switches
):
    uid = _user(db)
    _mailbox(db, uid)
    valid = tuple(f'p{i}@client.test' for i in range(27))
    invalid = ('a@x.com, b@y.com', 'c@x.com, d@y.com', 'e@x.com, f@y.com')
    cid = _campaign(db, uid, emails=valid + invalid)
    each_org(db, dispatcher.plan, NOW)
    (row,) = _kinds(db, 'bounces_high')
    assert row['href'] == f'/campaigns/{cid}' and row['data'] == {'campaign': 'C', 'invalid': 3, 'attempted': 30}
    each_org(db, dispatcher.plan, NOW + timedelta(minutes=1))
    await each_org_async(db, dispatcher.send_due, NOW + timedelta(minutes=1))
    assert len(_kinds(db, 'bounces_high')) == 1


def _mark_error(mailbox: dict, detail: str) -> None:
    """As the sender marks it, in the beat's run of the mailbox's organization."""
    call_for(mailbox['organization_id'], dispatcher._mark_mailbox_error, mailbox, detail)


def test_a_mailbox_that_goes_from_connected_to_error_tells_its_user_once(db, http, provider):
    uid, other = _user(db), _user(db)
    mb = _mailbox(db, uid)
    row = db.execute('SELECT * FROM user_oauth_connections WHERE id = %s', (mb,)).fetchone()
    _mark_error(row, 'send permission no longer granted')
    _mark_error(row, 'invalid_grant')  # the next turn finds it already in error
    (note,) = _kinds(db, 'mailbox_error')
    assert note['user_id'] == uid and note['href'] == '/settings/integrations' and note['emailed']
    assert note['data'] == {'mailbox': 'me@acme.test', 'provider': 'google'}
    assert len(http.calls) == 1
    gone = db.execute('SELECT * FROM user_oauth_connections WHERE id = %s', (_mailbox(db, other),)).fetchone()
    db.execute("UPDATE user_oauth_connections SET status = 'disconnected' WHERE id = %s", (gone['id'],))
    _mark_error(gone, 'invalid_grant')  # the user's own disconnect: nothing moves, nobody told
    assert len(_kinds(db, 'mailbox_error')) == 1
