"""HubSpot connect routes (#120). Token and introspect endpoints mocked; storage patched."""

import contextlib
from urllib.parse import parse_qs, urlparse

import fakeredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.crm import gate, write_back
from app.integrations import hubspot, hubspot_timeline, mailboxes, token_crypto
from app.integrations.providers import HUBSPOT, HUBSPOT_READ_SCOPES, HUBSPOT_WRITE_SCOPES
from app.main import app

UID = '00000000-0000-0000-0000-000000000001'
ATTACKER = '00000000-0000-0000-0000-00000000000a'
ORG = '00000000-0000-0000-0000-0000000000c1'
GRANTED = ['oauth', *HUBSPOT_READ_SCOPES]


@pytest.fixture
def client(monkeypatch, sign_in_without_db):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test')
    monkeypatch.setattr(settings, 'web_origin', 'https://app.zenbree.test')
    monkeypatch.setattr(settings, 'hubspot_oauth_client_id', 'hs-id')
    monkeypatch.setattr(settings, 'hubspot_oauth_client_secret', 'hs-secret')
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(mailboxes, '_redis', lambda: fake)
    monkeypatch.setattr(gate, '_redis', lambda: fake)
    sign_in_without_db(CurrentUser(id=UID, org_id=ORG))
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


@pytest.fixture
def stored(monkeypatch):
    rows: list[tuple] = []
    monkeypatch.setattr(hubspot, '_store', lambda *a, **k: rows.append((a, k)))
    return rows


def _token_and_introspect(scopes=GRANTED, refresh: str | None = 'rt-1', introspect: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == hubspot.INTROSPECT_URL:
            return httpx.Response(
                introspect, json={'hub_id': 4242, 'hub_domain': 'acme.hubspot.com', 'user': 'me@acme.test'}
            )
        if request.url.path == '/crm/owners/2026-03':
            assert request.url.params['email'] == 'me@acme.test'
            return httpx.Response(200, json={'results': [{'id': '77', 'email': 'me@acme.test'}]})
        return httpx.Response(
            200, json={'access_token': 'at-1', 'refresh_token': refresh, 'scopes': scopes, 'hub_id': 4242}
        )

    return handler


def _state(user=UID):
    return token_crypto.issue_state(user_id=user, provider='hubspot').value


def _complete(client, state, code='c1'):
    return client.post('/v1/integrations/hubspot/complete', json={'code': code, 'state': state})


def test_required_scopes_are_read_only():
    """A portal that cannot give the write scopes still connects for the read gate: they are optional (#526)."""
    assert all(s == 'oauth' or s.endswith('.read') for s in HUBSPOT.scopes)
    assert all(s.endswith('.write') for s in HUBSPOT_WRITE_SCOPES)
    assert not set(HUBSPOT_WRITE_SCOPES) & set(HUBSPOT.scopes)


def test_connect_returns_consent_url_bound_to_user(client):
    res = client.post('/v1/integrations/hubspot/connect')
    assert res.status_code == 200
    url = urlparse(res.json()['authorization_url'])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == 'app.hubspot.com'
    assert q['scope'].split() == GRANTED
    assert q['optional_scope'].split() == list(HUBSPOT_WRITE_SCOPES)
    assert q['client_id'] == 'hs-id'
    assert q['redirect_uri'] == 'https://api.zenbree.test/v1/integrations/hubspot/callback'
    assert 'code_challenge' not in q  # HubSpot documents no PKCE
    assert token_crypto.verify_state(q['state'], provider='hubspot').value == UID
    # A HubSpot state does not complete a mailbox callback, and the other way round.
    assert token_crypto.verify_state(q['state'], provider='google').is_err()


def test_connect_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, 'hubspot_oauth_client_secret', '')
    res = client.post('/v1/integrations/hubspot/connect')
    assert res.status_code == 503 and res.json()['detail']['code'] == 'INTEGRATIONS.NOT_CONFIGURED'


def test_callback_only_bounces_to_the_web(client, http, stored):
    res = client.get('/v1/integrations/hubspot/callback', params={'code': 'c1', 'state': 's1'})
    assert res.status_code == 302
    assert res.headers['location'] == 'https://app.zenbree.test/settings/integrations/callback/hubspot?code=c1&state=s1'
    res = client.get('/v1/integrations/hubspot/callback', params={'error': 'access_denied'})
    assert res.headers['location'].endswith('/callback/hubspot?error=access_denied')
    assert stored == [] and http.calls == []


def test_complete_stores_encrypted_refresh_token_and_portal(client, http, stored):
    http.handler = _token_and_introspect()
    res = _complete(client, _state())
    assert res.status_code == 200 and res.json() == {'provider': 'hubspot', 'account_label': 'acme.hubspot.com'}
    ((args, kwargs),) = stored
    assert args == (UID, ORG) and kwargs['label'] == 'acme.hubspot.com' and kwargs['portal_id'] == '4242'
    assert kwargs['owner_id'] == '77'  # the connected user's own owner id, for the ownership rule
    assert kwargs['encrypted'] != 'rt-1' and token_crypto.decrypt(kwargs['encrypted']).value == 'rt-1'
    token = parse_qs(http.calls[0].content.decode())
    assert token['grant_type'] == ['authorization_code'] and 'code_verifier' not in token


def test_complete_refuses_a_portal_it_cannot_identify(client, http, stored):
    """Webhooks name the portal: a connection without its id could never be unblocked by an event."""
    http.handler = _token_and_introspect(introspect=500)
    assert _complete(client, _state()).json()['detail']['code'] == 'INTEGRATIONS.HTTP_ERROR'
    assert stored == []


def test_complete_refuses_another_users_state(client, http, stored):
    """Connect CSRF: an attacker's consent link, completed by the victim, must not attach anything."""
    res = _complete(client, _state(user=ATTACKER))
    assert res.status_code == 400 and res.json()['detail']['code'] == 'INTEGRATIONS.INVALID_STATE'
    assert stored == [] and http.calls == []


def test_complete_state_is_single_use(client, http, stored):
    http.handler = _token_and_introspect()
    state = _state()
    assert _complete(client, state).status_code == 200
    assert _complete(client, state).json()['detail']['code'] == 'INTEGRATIONS.INVALID_STATE'
    assert len(stored) == 1


@pytest.mark.parametrize(
    ('scopes', 'refresh', 'reason'),
    [
        (['oauth', 'crm.objects.contacts.read'], 'rt-1', 'INTEGRATIONS.SCOPE_DECLINED'),
        ([], 'rt-1', 'INTEGRATIONS.SCOPE_DECLINED'),  # no scope reported is not a grant
        (GRANTED, None, 'INTEGRATIONS.NO_REFRESH_TOKEN'),
    ],
)
def test_complete_refuses_unusable_grant(client, http, stored, scopes, refresh, reason):
    http.handler = _token_and_introspect(scopes, refresh)
    assert _complete(client, _state()).json()['detail']['code'] == reason
    assert stored == []


def _fake_conn(ciphertext, cleared):
    class Conn:
        def execute(self, sql, params=()):
            if sql.lstrip().startswith('UPDATE'):
                cleared.append(params)
            self.row = {'refresh_token_encrypted': ciphertext}
            return self

        def fetchone(self):
            return self.row

    return lambda timeout=None: contextlib.nullcontext(Conn())


def test_disconnect_revokes_with_client_auth_then_clears(client, http, monkeypatch):
    cleared: list = []
    monkeypatch.setattr(hubspot, 'get_conn', _fake_conn(token_crypto.encrypt('rt-1').value, cleared))
    http.handler = lambda r: httpx.Response(200)
    assert client.delete('/v1/integrations/hubspot').json() == {'connected': False}
    (revoke,) = http.calls
    assert str(revoke.url) == HUBSPOT.revoke_url
    assert parse_qs(revoke.content.decode()) == {
        'token': ['rt-1'],
        'client_id': ['hs-id'],
        'client_secret': ['hs-secret'],
    }
    assert cleared  # cleared even if the revoke had failed
    http.handler = lambda r: httpx.Response(500)
    cleared.clear()
    assert client.delete('/v1/integrations/hubspot').status_code == 200 and cleared


async def _timeline(user_id, project_id):
    return hubspot_timeline.TimelineStatus(logged=2, pending=1, issues=[])


def test_status(client, monkeypatch):
    monkeypatch.setattr('app.projects.routes._active', lambda user, header: 'p1')  # no database in this test
    monkeypatch.setattr(hubspot, 'connection', lambda uid: None)
    assert client.get('/v1/integrations/hubspot').json() == {
        'connected': False,
        'status': 'disconnected',
        'account_label': None,
        'configured': True,
        'can_write': False,
        'can_write_companies': False,
        'write_back': False,
        'timeline': {'logged': 0, 'pending': 0, 'issues': []},
    }
    monkeypatch.setattr(
        hubspot,
        'connection',
        lambda uid: {'status': 'connected', 'account_label': 'acme.hubspot.com', 'provider_account_id': '4242'},
    )
    monkeypatch.setattr(
        write_back, 'state', lambda uid: {'can_write': True, 'can_write_companies': True, 'write_back': False}
    )
    monkeypatch.setattr(hubspot_timeline, 'read', _timeline)
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', '')
    got = client.get('/v1/integrations/hubspot').json()
    assert got['connected'] is True and got['account_label'] == 'acme.hubspot.com' and got['configured'] is False
    assert (got['can_write'], got['write_back']) == (True, False)
    assert got['timeline'] == {'logged': 2, 'pending': 1, 'issues': []}


def test_the_write_back_switch_answers_the_new_state_or_says_why_not(client, monkeypatch):
    asked: list[tuple[str, bool]] = []
    outcome = {'value': 'ok'}
    monkeypatch.setattr('app.projects.routes._active', lambda user, header: 'p1')
    monkeypatch.setattr(hubspot_timeline, 'read', _timeline)

    def set_enabled(uid, enabled):
        asked.append((uid, enabled))
        return outcome['value']

    monkeypatch.setattr(write_back, 'set_enabled', set_enabled)
    monkeypatch.setattr(
        write_back, 'state', lambda uid: {'can_write': True, 'can_write_companies': True, 'write_back': True}
    )
    monkeypatch.setattr(hubspot, 'connection', lambda uid: {'status': 'connected', 'account_label': 'acme.hubspot.com'})
    on = client.put('/v1/integrations/hubspot/write-back', json={'enabled': True})
    assert on.status_code == 200 and on.json()['write_back'] is True and asked == [(UID, True)]

    for outcome['value'], code in (
        ('no_write_scope', 'INTEGRATIONS.HUBSPOT_WRITE_NOT_GRANTED'),
        ('not_connected', 'INTEGRATIONS.HUBSPOT_NOT_CONNECTED'),
    ):
        refused = client.put('/v1/integrations/hubspot/write-back', json={'enabled': True})
        assert refused.status_code == 409 and refused.json()['detail']['code'] == code
    assert client.put('/v1/integrations/hubspot/write-back', json={}).status_code == 422
