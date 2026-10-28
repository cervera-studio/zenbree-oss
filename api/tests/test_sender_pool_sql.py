"""Needs TEST_DATABASE_URL."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.integrations import project_senders, token_crypto
from app.main import app
from app.outreach import sender_pool
from tests import test_dispatcher_sql
from tests.conftest import call_as, member, project, scoped_conn, sign_in
from tests.test_dispatcher_sql import DB_URL, GMAIL_SEND, NOW, TWO_STEPS, _beat, _campaign, _mailbox
from tests.test_sender_slot_sql import _heard_step_one
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
switches = test_dispatcher_sql.switches

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: sender pool SQL test NOT run')


@pytest.fixture
def linking(db, monkeypatch):
    monkeypatch.setattr(project_senders, 'get_conn', scoped_conn(db))
    monkeypatch.setattr('app.outreach.routes.get_conn', scoped_conn(db))


def _attach(user, pid, account: str):
    return call_as(
        user,
        project_senders.attach,
        user.id,
        pid,
        'google',
        account=account,
        cols={
            'refresh_token_encrypted': token_crypto.encrypt('refresh-1').value,
            'granted_scopes': ['openid', GMAIL_SEND],
            'account_label': 'me@acme.test',
            'provider_account_id': account,
        },
    )


async def test_a_mailbox_connected_after_its_draft_sends_once_someone_picks_it(db, provider, switches, linking):
    uid = _user(db)
    cid = _campaign(db, uid)
    db.execute("UPDATE outreach_campaigns SET status = 'draft', activated_at = NULL")
    db.execute("UPDATE outreach_campaign_targets SET status = 'draft'")
    user = member(db, uid)
    assert _attach(user, project(db, uid), 'sub-1').is_ok()
    sign_in(app, user)
    try:
        client = TestClient(app)
        refused = client.post(f'/v1/campaigns/{cid}/activate')
        assert refused.json()['detail']['code'] == 'CAMPAIGN.NO_SENDER'
        (account,) = client.get(f'/v1/campaigns/{cid}/senders').json()['senders']
        assert client.put(f'/v1/campaigns/{cid}/senders', json={'sender_ids': [account['id']]}).status_code == 200
        assert client.post(f'/v1/campaigns/{cid}/activate').status_code == 200
    finally:
        app.dependency_overrides.clear()
    db.execute('UPDATE outreach_campaigns SET activated_at = %s', (NOW - timedelta(hours=1),))

    await _beat()

    assert [(m['To'], m['From']) for m, _ in provider.sent] == [('anna@client.test', 'me@acme.test')]


async def test_linking_the_account_back_resumes_its_conversation_on_it(db, provider, switches, linking):
    uid = _user(db)
    google = _mailbox(db, uid)
    microsoft = _mailbox(
        db, uid, provider='microsoft', granted_scopes=['User.Read', 'Mail.Send'], account_label='ms@acme.test'
    )
    cid = _campaign(db, uid, TWO_STEPS)
    _heard_step_one(db, 'anna@client.test', google, NOW - timedelta(days=4))
    sender_pool.pick(db, cid, [google, microsoft])
    stopped = _campaign(db, uid, TWO_STEPS, emails=('dora@client.test',))
    _heard_step_one(db, 'dora@client.test', google, NOW - timedelta(days=4))
    db.execute("UPDATE outreach_campaigns SET status = 'stopped' WHERE id = %s", (stopped,))
    user, pid = member(db, uid), project(db, uid)
    assert call_as(user, project_senders.unlink, user, google, pid).is_ok()
    await _beat()
    assert provider.sent == []

    assert call_as(user, project_senders.link, user, google, pid).is_ok()
    await _beat(NOW + timedelta(minutes=20))

    assert [(m['To'], m['From']) for m, _ in provider.sent] == [('anna@client.test', 'me@acme.test')]
    assert db.execute('SELECT 1 FROM outreach_campaign_senders WHERE campaign_id = %s', (stopped,)).fetchall() == []
