"""A pushed notification carries the bell's unread count, whatever scope delivered it (PR 9, review round 0).

The bell counts a person's unread notifications of every organization where they are a live member. The beat and the
reply labelling deliver under ``ForOrg`` of one organization, which sees only that organization's notifications, so
``deliver`` counts as the recipient. Each test gives the recipient one unread notification in another organization
and expects 2 pushed. Needs TEST_DATABASE_URL; nothing leaves the process.
"""

import pytest
from fastapi.testclient import TestClient

from app import worker
from app.core.scope import acting_for
from app.main import app
from app.notifications import service as notifications
from tests.test_agent import db as pool_db
from tests.test_agent import needs_db
from tests.test_creator_deleted_sql import _mailbox, _org, _team
from tests.test_dispatcher_sql import ONE_STEP
from tests.test_dispatcher_sql import provider as provider
from tests.test_dispatcher_sql import switches as switches
from tests.test_outreach_org_scope_sql import _campaign, _remove, beat
from tests.test_replies_orgs_sql import World
from tests.test_replies_sql import QUEUED, _post
from tests.test_replies_sql import db as reply_db

pytestmark = needs_db
fixtures = (pool_db, reply_db, provider, switches, beat)

REAL_DELIVER = notifications.deliver  # the beat fixture stubs it


def _unread_in(c, uid: str, org: str) -> None:
    c.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, %s, 'agent_question', '/')",
        (uid, org),
    )


@pytest.fixture
def pushed(monkeypatch) -> list:
    """The real ``deliver``, with each push to open tabs recorded as ``(user, unread)`` and each browser alert as
    ``(user, 'browser')``. No email: ``send_email`` answers as if it sent nothing."""
    out: list = []
    monkeypatch.setattr(notifications, 'deliver', REAL_DELIVER)
    monkeypatch.setattr(notifications, 'publish', lambda user, event, data: out.append((user, data['unread'])))
    monkeypatch.setattr(notifications.push, 'send', lambda user, payload: out.append((user, 'browser')))
    monkeypatch.setattr(notifications, 'send_email', lambda *a, **k: False)
    return out


@pytest.fixture
def team(pool_db, beat, pushed):
    """Ada's organization with Bo in it. Bo has one unread notification in his own organization."""
    ada, bo, org, pid = _team(pool_db)
    _unread_in(pool_db, bo, _org(pool_db, bo))
    return ada, bo, org, pid


def test_an_approval_notice_from_the_beat_pushes_the_bells_unread_count(pool_db, team, pushed):
    _, bo, org, pid = team
    _campaign(pool_db, bo, org, pid, ONE_STEP, ('anna@client.test',))
    pool_db.execute('UPDATE outreach_campaigns SET approve_before_sending = true')

    worker.dispatch_campaign_sends()

    assert pushed == [(bo, 2)]


def test_a_mailbox_error_from_the_beat_pushes_the_bells_unread_count(pool_db, team, pushed):
    _, bo, org, pid = team
    box = _mailbox(pool_db, bo, org, pid, 'bo@agency.test')
    pool_db.execute("UPDATE user_oauth_connections SET granted_scopes = '{openid}' WHERE id = %s", (box,))
    _campaign(pool_db, bo, org, pid, ONE_STEP, ('anna@client.test',))

    worker.dispatch_campaign_sends()

    assert pool_db.execute('SELECT status FROM user_oauth_connections WHERE id = %s', (box,)).fetchone() == {
        'status': 'error'
    }
    assert pushed == [(bo, 2)]


def test_a_bounce_notice_from_the_beat_pushes_the_bells_unread_count(pool_db, team, pushed):
    _, bo, org, pid = team
    valid = tuple(f'p{i}@client.test' for i in range(notifications.BOUNCE_MIN_ATTEMPTS - 2))
    _campaign(pool_db, bo, org, pid, ONE_STEP, (*valid, 'not an address', 'nor@this@one'))

    worker.dispatch_campaign_sends()

    kinds = pool_db.execute('SELECT kind FROM notifications WHERE organization_id = %s', (org,)).fetchall()
    assert kinds == [{'kind': 'bounces_high'}]
    assert pushed == [(bo, 2)]


def test_a_recipient_removed_before_the_push_gets_none_and_the_beat_goes_on(pool_db, team, pushed, monkeypatch):
    """Both owners hold messages for approval. The first one delivered leaves the organization between its notice
    and its push: no push for them, and the next owner still gets theirs."""
    ada, bo, org, pid = team
    for owner in (ada, bo):
        _campaign(pool_db, owner, org, pid, ONE_STEP, (f'{owner}@client.test',))
    pool_db.execute('UPDATE outreach_campaigns SET approve_before_sending = true')
    gone: list[str] = []

    def remove_first(notified):
        if not gone:
            gone.append(notified.user_id)
            _remove(pool_db, notified.user_id, org)
        REAL_DELIVER(notified)

    monkeypatch.setattr(notifications, 'deliver', remove_first)

    worker.dispatch_campaign_sends()

    assert len(gone) == 1
    assert [user for user, _ in pushed] == [({ada, bo} - set(gone)).pop()]


def test_a_labelled_reply_pushes_the_bells_unread_count(reply_db, webhook_events, http, pushed, monkeypatch):
    """The ``label_reply`` task runs as ``ForOrg`` of the reply's organization, a client's here. The reply is
    urgent, so its email goes out now, and ``emailed_at`` is written as the recipient."""
    w = World(reply_db)
    client_org, client_project = w.client()
    w.campaign(client_project)
    _unread_in(reply_db, w.uid, w.own)
    assert _post(TestClient(app), w.uid).json()['kind'] == 'reply'

    class Labeller:
        async def chat(self, **kw):
            return type('R', (), {'text': '{"label": "interested"}'})()

    monkeypatch.setattr('app.llm.router.get_chat_provider', lambda: Labeller())
    mailed: list[str] = []
    monkeypatch.setattr(notifications, 'send_email', lambda to, *a, **k: mailed.append(to) or True)
    with acting_for(client_org, 'test'):
        assert worker.label_reply.apply(args=[QUEUED[0]]).get() == 'interested'

    assert [p for p in pushed if p[1] != 'browser'] == [(w.uid, 2)]
    assert len(mailed) == 1
    emailed = reply_db.execute(
        "SELECT emailed_at IS NOT NULL AS done FROM notifications WHERE kind = 'reply_urgent' AND organization_id = %s",
        (client_org,),
    ).fetchall()
    assert emailed == [{'done': True}]
