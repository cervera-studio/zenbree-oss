"""#343: what a LinkedIn acceptance and an unhealthy account tell the user, against real SQL.

Needs TEST_DATABASE_URL; the schema and the connect flow are test_linkedin_sql's.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.system_reasons import SystemReason
from app.linkedin import api as linkedin_api
from app.main import app
from app.notifications import service as notifications
from tests.conftest import call_system, scoped_conn
from tests.test_dispatcher_sql import DB_URL, _campaign
from tests.test_linkedin_sql import connect, db
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: LinkedIn notification test NOT run')
fixtures = (connect, db)

EVENT = {'event': 'new_relation', 'account_id': 'acc-1', 'user_provider_id': 'ACo1'}


@pytest.fixture
def told(db, monkeypatch):
    monkeypatch.setattr(notifications, 'get_conn', scoped_conn(db))

    def kinds(kind: str) -> list[dict]:
        return db.execute(
            'SELECT user_id::text, href, data, organization_id IS NOT NULL AS scoped FROM notifications WHERE kind = %s',
            (kind,),
        ).fetchall()

    return kinds


def _invited(db, uid: str, first='Giulia', last='Ferri', company='') -> str:
    cid = _campaign(db, uid, emails=(f'{uid}@client.test',))
    db.execute(
        'UPDATE outreach_campaign_targets SET linkedin_provider_id = %s, first_name = %s, last_name = %s,'
        ' company_name = %s WHERE campaign_id = %s',
        ('ACo1', first, last, company, cid),
    )
    return cid


def test_an_acceptance_tells_the_campaign_owner_once_and_names_the_campaign(db, connect, told):
    first, second = _user(db), _user(db)
    connect(first)
    connect(second)
    cid = _invited(db, first)
    _invited(db, second)  # the same profile invited from another tenant's campaign: not the first account's
    assert call_system(SystemReason.LINKEDIN_WEBHOOK, linkedin_api.webhook_events.accepted, EVENT)['targets'] == 1
    (note,) = told('linkedin_accepted')
    assert note['user_id'] == first and note['href'] == f'/campaigns/{cid}' and note['scoped']
    assert note['data'] == {'name': 'Giulia Ferri', 'campaign': 'C', 'channel': 'linkedin'}
    assert (
        call_system(SystemReason.LINKEDIN_WEBHOOK, linkedin_api.webhook_events.accepted, EVENT)['targets'] == 0
    )  # Unipile retries: already dated, nobody told twice
    assert len(told('linkedin_accepted')) == 1


def test_a_nameless_target_shows_its_company_and_an_unknown_profile_tells_nobody(db, connect, told):
    uid = _user(db)
    connect(uid)
    _invited(db, uid, first='', last='', company='Officina Nove')
    call_system(
        SystemReason.LINKEDIN_WEBHOOK, linkedin_api.webhook_events.accepted, {**EVENT, 'user_provider_id': 'ACo-other'}
    )
    assert told('linkedin_accepted') == []
    call_system(SystemReason.LINKEDIN_WEBHOOK, linkedin_api.webhook_events.accepted, EVENT)
    assert [r['data']['name'] for r in told('linkedin_accepted')] == ['Officina Nove']


def _status(message: str, account='acc-1'):
    return TestClient(app).post(
        '/v1/integrations/linkedin/webhook',
        json={'AccountStatus': {'account_id': account, 'message': message}},
        headers={'Unipile-Auth': 'uni-secret'},
    )


def test_an_unhealthy_account_tells_its_user_once_and_a_healthy_or_own_disconnect_nobody(
    db, connect, told, monkeypatch
):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 'uni-secret')
    uid = _user(db)
    connect(uid)
    assert _status('OK').json()['handled'] is True  # healthy while connected: no news
    assert told('mailbox_error') == []
    assert _status('CREDENTIALS').json()['handled'] is True
    (note,) = told('mailbox_error')
    assert note['user_id'] == uid and note['href'] == '/settings/integrations' and note['scoped']
    assert note['data']['provider'] == 'unipile'
    assert _status('CREDENTIALS').json()['handled'] is True  # reported again while still in error
    assert len(told('mailbox_error')) == 1
    _status('OK')  # recovered, then broken again: told again
    _status('CREDENTIALS')
    assert len(told('mailbox_error')) == 2


def test_the_users_own_disconnect_is_not_news_when_the_provider_reports_afterwards(db, connect, told, monkeypatch):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 'uni-secret')
    uid = _user(db)
    connect(uid)
    assert TestClient(app).delete('/v1/integrations/linkedin').json() == {'connected': False}
    assert _status('CREDENTIALS').json()['handled'] is False  # a disconnected row stays disconnected
    assert told('mailbox_error') == []


@pytest.mark.parametrize('message', ['CONNECTING', 'DELETED', 'SOMETHING_NEW'])
def test_a_transient_or_unknown_account_status_changes_nothing_and_tells_nobody(
    db, connect, told, monkeypatch, message
):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 'uni-secret')
    uid = _user(db)
    connect(uid)
    assert _status(message).json()['handled'] is False
    assert db.execute("SELECT status FROM user_oauth_connections WHERE provider = 'unipile'").fetchone() == {
        'status': 'connected'
    }
    assert told('mailbox_error') == []


@pytest.mark.parametrize('message', ['ERROR', 'STOPPED', 'CREDENTIALS'])
def test_an_explicitly_unhealthy_account_status_tells_its_user(db, connect, told, monkeypatch, message):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 'uni-secret')
    uid = _user(db)
    connect(uid)
    assert _status(message).json()['handled'] is True
    assert [r['user_id'] for r in told('mailbox_error')] == [uid]


def test_the_row_is_disconnected_before_unipile_is_asked_to_delete_the_account(db, connect, http, told):
    uid = _user(db)
    connect(uid)
    seen = []

    def handler(request):
        seen.append(db.execute("SELECT status FROM user_oauth_connections WHERE provider = 'unipile'").fetchone())
        return httpx.Response(200, json={})

    http.handler = handler
    assert TestClient(app).delete('/v1/integrations/linkedin').json() == {'connected': False}
    assert seen == [{'status': 'disconnected'}]


def test_a_failed_unipile_delete_leaves_the_account_as_it_was(db, connect, http, told):
    uid = _user(db)
    connect(uid)
    http.handler = lambda request: httpx.Response(500, json={})
    assert TestClient(app).delete('/v1/integrations/linkedin').status_code >= 400
    assert db.execute("SELECT status FROM user_oauth_connections WHERE provider = 'unipile'").fetchone() == {
        'status': 'connected'
    }
