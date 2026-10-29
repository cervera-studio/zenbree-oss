"""LinkedIn connect routes. Unipile mocked; DB reads patched."""

import contextlib

import fakeredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.core import scope as scopes
from app.core.config import settings
from app.core.errors import ok
from app.integrations import token_crypto
from app.linkedin import api
from app.main import app
from app.projects import routes as project_routes
from tests.conftest import body

UID = '00000000-0000-0000-0000-000000000001'
PID = '00000000-0000-0000-0000-0000000000f1'


@pytest.fixture
def client(monkeypatch, keys, sign_in_without_db):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test')
    monkeypatch.setattr(api, 'get_account', lambda project_id: None)
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(api, '_redis', lambda: fake)
    sign_in_without_db(CurrentUser(id=UID, org_id='org-1'))
    monkeypatch.setattr(project_routes, '_active', lambda user, header: PID)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_connect_hosted_link_carries_risk_notice(client, http):
    http.handler = lambda r: httpx.Response(
        200, json={'object': 'HostedAuthURL', 'url': 'https://account.unipile.com/x'}
    )
    res = client.post('/v1/integrations/linkedin/connect', json={'risk_accepted': True})
    assert res.status_code == 200
    assert res.json()['url'] == 'https://account.unipile.com/x'
    assert 'LinkedIn' in res.json()['risk_notice']
    sent = body(http.calls[0])
    assert sent['type'] == 'create' and sent['notify_url'].startswith(
        'https://api.zenbree.test/v1/integrations/linkedin/webhook?state='
    )
    state = sent['notify_url'].split('state=', 1)[1]
    assert token_crypto.state_project(state) == PID  # the account joins the project it was started in


def test_connect_credentials_checkpoint_stores_nothing(client, http, monkeypatch):
    stored = []
    monkeypatch.setattr(api, '_store', lambda *a, **k: stored.append(a))
    http.handler = lambda r: httpx.Response(
        201, json={'object': 'Checkpoint', 'account_id': 'acc_9', 'checkpoint': {'type': '2FA'}}
    )
    res = client.post(
        '/v1/integrations/linkedin/connect', json={'risk_accepted': True, 'username': 'me@x.it', 'password': 'hunter2'}
    )
    assert res.status_code == 200
    assert res.json() | {'risk_notice': None} == {
        'connected': False,
        'checkpoint': '2FA',
        'account_id': 'acc_9',
        'risk_notice': None,
    }
    assert stored == []


ROW = {
    'id': '00000000-0000-0000-0000-0000000000a1',
    'user_id': UID,
    'status': 'connected',
    'account_label': 'Anna Rossi',
    'linkedin_premium': True,
    'provider_account_id': 'acc_1',
}


def test_status_reports_the_projects_account(client, monkeypatch):
    monkeypatch.setattr(api, 'get_account', lambda project_id: ROW)
    res = client.get('/v1/integrations/linkedin')
    assert res.json() == {
        'connected': True,
        'status': 'connected',
        'account_label': 'Anna Rossi',
        'premium': True,
        'can_disconnect': True,
        'risk_notice': api.RISK_NOTICE,
    }
    monkeypatch.setattr(api, 'get_account', lambda project_id: None)
    assert client.get('/v1/integrations/linkedin').json()['status'] == 'disconnected'


def test_disconnect_deletes_the_account_at_unipile(client, http, monkeypatch):
    monkeypatch.setattr(api, 'get_account', lambda project_id: ROW)
    marked = []

    class Conn:
        def execute(self, sql, params):
            marked.append(params)

    monkeypatch.setattr(api, 'get_conn', lambda: contextlib.nullcontext(Conn()))
    http.handler = lambda r: httpx.Response(200, json={'object': 'AccountDeleted'})
    res = client.delete('/v1/integrations/linkedin')
    assert res.json() == {'connected': False}
    assert http.calls[0].method == 'DELETE' and http.calls[0].url.path.endswith('/accounts/acc_1')
    assert marked == [('disconnected', None, ROW['id'])]  # before the delete call, as Unipile's DELETED report races it


def test_checkpoint_answer_connects_the_account(client, http, monkeypatch):
    monkeypatch.setattr(api, '_store', lambda *a, **k: ok(None))
    api._redis().set(api._pending_key(UID, PID, 'acc_9'), '1')
    http.handler = lambda r: (
        httpx.Response(200, json={'object': 'AccountResolved', 'account_id': 'acc_9'})
        if r.method == 'POST'
        else httpx.Response(200, json={'object': 'Account', 'name': 'Anna Rossi', 'connection_params': {}})
    )
    res = client.post('/v1/integrations/linkedin/checkpoint', json={'account_id': 'acc_9', 'code': '123456'})
    assert res.json() == {'connected': True, 'checkpoint': None, 'account_id': 'acc_9'}
    assert body(http.calls[0])['code'] == '123456'


def test_webhook_hosted_auth_attaches_account_as_the_state_user_in_the_project_organization(client, http, monkeypatch):
    stored = []
    monkeypatch.setattr(api, '_project_organization', lambda project_id: 'org-2' if project_id == PID else None)
    monkeypatch.setattr(
        api,
        '_store',
        lambda user_id, project_id, account_id, **k: (
            stored.append((user_id, project_id, account_id, scopes.current_or_none())) or api.ok(None)
        ),
    )
    http.handler = lambda r: httpx.Response(
        200, json={'name': 'Mario Rossi', 'connection_params': {'im': {'premiumFeatures': []}}}
    )
    state = token_crypto.issue_state(user_id=UID, provider='unipile', project_id=PID).value
    res = client.post(
        f'/v1/integrations/linkedin/webhook?state={state}',
        json={'status': 'CREATION_SUCCESS', 'account_id': 'acc_1', 'name': UID},
    )
    assert res.status_code == 200 and stored == [(UID, PID, 'acc_1', scopes.Member('org-2', UID))]


def test_webhook_rejects_bad_state_and_missing_secret(client, monkeypatch):
    assert (
        client.post(
            '/v1/integrations/linkedin/webhook?state=forged-state-value-xxxxxxxx', json={'account_id': 'a'}
        ).status_code
        == 400
    )
    monkeypatch.setattr(settings, 'unipile_webhook_secret', '')
    assert (
        client.post(
            '/v1/integrations/linkedin/webhook', json={'AccountStatus': {}}, headers={'Unipile-Auth': ''}
        ).status_code
        == 401
    )
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 's3cret')
    assert (
        client.post(
            '/v1/integrations/linkedin/webhook', json={'AccountStatus': {}}, headers={'Unipile-Auth': 'wrong'}
        ).status_code
        == 401
    )


def test_webhook_routes_an_acceptance_only_with_the_secret(client, monkeypatch):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 's3cret')
    monkeypatch.setattr(
        api.webhook_events, 'accepted', lambda payload: {'handled': True, 'event': 'new_relation', 'targets': 1}
    )
    relation = {'event': 'new_relation', 'account_id': 'acc-1', 'user_provider_id': 'ACo1'}
    url = '/v1/integrations/linkedin/webhook'
    assert client.post(url, json=relation, headers={'Unipile-Auth': 'wrong'}).status_code == 401
    assert client.post(url, json=relation, headers={'Unipile-Auth': 's3cret'}).json() == {
        'handled': True,
        'event': 'new_relation',
        'targets': 1,
    }


def test_credentials_route_is_rate_limited(client, http):
    http.handler = lambda r: httpx.Response(401, json={'type': 'errors/invalid_credentials'})
    codes = [
        client.post(
            '/v1/integrations/linkedin/connect', json={'risk_accepted': True, 'username': 'me@x.it', 'password': 'x'}
        ).status_code
        for _ in range(6)
    ]
    assert codes[-1] == 429 and 429 not in codes[:5]


def test_connect_requires_risk_acceptance(client):
    res = client.post('/v1/integrations/linkedin/connect', json={})
    assert res.status_code == 400
    assert res.json()['detail']['code'] == 'LINKEDIN.RISK_NOT_ACCEPTED'


def test_rate_limit_is_per_login_across_accounts(monkeypatch):
    """A fresh Zenbree account can't keep guessing the same LinkedIn password."""
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(api, '_redis', lambda: fake)
    for i in range(5):
        api._rate_limit(f'user:u{i}', 'login:victim@x.it')
    with pytest.raises(HTTPException) as e:
        api._rate_limit('user:u9', 'login:victim@x.it')
    assert e.value.status_code == 429


COLLEAGUE_ROW = ROW | {'user_id': '00000000-0000-0000-0000-000000000002'}


@pytest.mark.parametrize('status', ['connected', 'error'])
@pytest.mark.parametrize(
    ('path', 'payload'),
    [
        ('connect', {'risk_accepted': True}),
        ('connect', {'risk_accepted': True, 'username': 'me@x.it', 'password': 'hunter2'}),
        ('checkpoint', {'account_id': 'acc_9', 'code': '123456'}),
    ],
)
def test_a_colleagues_sender_is_refused_before_any_unipile_call(client, http, monkeypatch, status, path, payload):
    monkeypatch.setattr(api, 'get_account', lambda project_id: COLLEAGUE_ROW | {'status': status})
    res = client.post(f'/v1/integrations/linkedin/{path}', json=payload)
    assert res.status_code == 403 and res.json()['detail']['code'] == 'INTEGRATIONS.REPLACE_FORBIDDEN'
    assert http.calls == []


def test_a_disconnected_colleagues_sender_or_an_admin_may_replace_it(client, http, monkeypatch, sign_in_without_db):
    http.handler = lambda r: httpx.Response(200, json={'object': 'HostedAuthURL', 'url': 'https://u/x'})
    monkeypatch.setattr(api, 'get_account', lambda project_id: COLLEAGUE_ROW | {'status': 'disconnected'})
    assert client.post('/v1/integrations/linkedin/connect', json={'risk_accepted': True}).status_code == 200
    monkeypatch.setattr(api, 'get_account', lambda project_id: COLLEAGUE_ROW)
    sign_in_without_db(CurrentUser(id=UID, org_id='org-1', org_role='admin'))
    assert client.post('/v1/integrations/linkedin/connect', json={'risk_accepted': True}).status_code == 200


def test_checkpoint_answers_only_for_the_login_this_user_started(client, http, monkeypatch):
    monkeypatch.setattr(api, '_store', lambda *a, **k: ok(None))
    http.handler = lambda r: httpx.Response(
        201, json={'object': 'Checkpoint', 'account_id': 'acc_9', 'checkpoint': {'type': '2FA'}}
    )
    login = {'risk_accepted': True, 'username': 'me@x.it', 'password': 'hunter2'}
    assert client.post('/v1/integrations/linkedin/connect', json=login).json()['checkpoint'] == '2FA'
    http.calls.clear()

    other = client.post('/v1/integrations/linkedin/checkpoint', json={'account_id': 'acc_colleague', 'code': '123456'})
    assert other.status_code == 422 and other.json()['detail']['code'] == 'INTEGRATIONS.INVALID_INPUT'
    assert http.calls == []  # a colleague's account id never reaches Unipile

    http.handler = lambda r: (
        httpx.Response(200, json={'object': 'AccountResolved', 'account_id': 'acc_9'})
        if r.method == 'POST'
        else httpx.Response(200, json={'object': 'Account', 'name': 'Anna Rossi', 'connection_params': {}})
    )
    own = client.post('/v1/integrations/linkedin/checkpoint', json={'account_id': 'acc_9', 'code': '123456'})
    assert own.json()['connected'] is True
    again = client.post('/v1/integrations/linkedin/checkpoint', json={'account_id': 'acc_9', 'code': '123456'})
    assert again.status_code == 422  # one solved login, one answer
