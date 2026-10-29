"""Mailbox connect routes (#79). Token endpoint mocked; storage patched."""

from urllib.parse import parse_qs, urlparse

import fakeredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.integrations import mailboxes, token_crypto
from app.integrations.oauth_client import pkce_challenge
from app.integrations.providers import GOOGLE, MICROSOFT
from app.main import app
from app.projects import routes as project_routes

UID = '00000000-0000-0000-0000-000000000001'
PID = '00000000-0000-0000-0000-0000000000p1'
ATTACKER = '00000000-0000-0000-0000-00000000000a'
MB = '00000000-0000-0000-0000-0000000000b1'
GMAIL_SEND = 'https://www.googleapis.com/auth/gmail.send'


@pytest.fixture
def client(monkeypatch, sign_in_without_db):
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.zenbree.test')
    monkeypatch.setattr(settings, 'web_origin', 'https://app.zenbree.test')
    for name in ('google', 'microsoft'):
        monkeypatch.setattr(settings, f'{name}_oauth_client_id', f'{name}-id')
        monkeypatch.setattr(settings, f'{name}_oauth_client_secret', f'{name}-secret')
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(mailboxes, '_redis', lambda: fake)
    sign_in_without_db(CurrentUser(id=UID, org_id='org-1'))
    monkeypatch.setattr(project_routes, '_active', lambda user, header: PID)
    monkeypatch.setattr(mailboxes, 'in_org', lambda project_id, org_id: project_id == PID and org_id == 'org-1')
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


@pytest.fixture
def stored(monkeypatch):
    rows: list[tuple] = []
    monkeypatch.setattr(mailboxes, '_store', lambda *a, **k: rows.append((a, k)))
    return rows


def _token_and_profile(scope: str, refresh: str | None = 'rt-1'):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'POST':
            return httpx.Response(200, json={'access_token': 'at-1', 'refresh_token': refresh, 'scope': scope})
        return httpx.Response(200, json={'email': 'me@acme.test', 'sub': 'g-123'})

    return handler


def test_scopes_send_and_read_the_bounces():
    """#747: the read scope is asked for the DSNs; a grant without it still sends."""
    for provider in (GOOGLE, MICROSOFT):
        assert provider.send_scope in provider.scopes and provider.read_scope in provider.scopes
    assert MICROSOFT.grants_send(['Mail.Send', 'User.Read']) and not MICROSOFT.grants_send(['User.Read'])
    assert MICROSOFT.grants_read(['Mail.Send', 'Mail.Read']) and not MICROSOFT.grants_read(['Mail.Send'])
    assert not GOOGLE.grants_read([GMAIL_SEND])


def _state(user=UID, verifier='v' * 50, project_id=PID):
    return token_crypto.issue_state(
        user_id=user, provider='google', pkce_verifier=verifier, project_id=project_id
    ).value


def _complete(client, state, code='c1'):
    return client.post('/v1/integrations/mailboxes/google/complete', json={'code': code, 'state': state})


def test_connect_returns_consent_url_bound_to_user_with_pkce(client):
    res = client.post('/v1/integrations/mailboxes/google/connect')
    assert res.status_code == 200
    url = urlparse(res.json()['authorization_url'])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == 'accounts.google.com'
    assert q['scope'].split() == ['openid', 'email', GMAIL_SEND, GOOGLE.read_scope]
    assert q['redirect_uri'] == 'https://api.zenbree.test/v1/integrations/mailboxes/google/callback'
    assert q['access_type'] == 'offline' and q['prompt'] == 'consent'
    assert token_crypto.verify_state(q['state'], provider='google').value == UID
    assert token_crypto.state_project(q['state']) == PID  # the mailbox joins the project it was started in
    assert q['code_challenge_method'] == 'S256'
    assert q['code_challenge'] == pkce_challenge(token_crypto.pkce_verifier(q['state']))
    assert token_crypto.pkce_verifier(q['state']) not in res.text  # the verifier only travels encrypted
    # A state minted for one provider does not complete another's callback.
    assert token_crypto.verify_state(q['state'], provider='microsoft').is_err()


def test_connect_unknown_provider_and_unconfigured(client, monkeypatch):
    assert client.post('/v1/integrations/mailboxes/yahoo/connect').status_code == 422
    monkeypatch.setattr(settings, 'microsoft_oauth_client_id', '')
    res = client.post('/v1/integrations/mailboxes/microsoft/connect')
    assert res.status_code == 503 and res.json()['detail']['code'] == 'INTEGRATIONS.NOT_CONFIGURED'


def test_callback_only_bounces_to_the_web(client, http, stored):
    res = client.get('/v1/integrations/mailboxes/google/callback', params={'code': 'c1', 'state': 's1'})
    assert res.status_code == 302
    assert res.headers['location'] == 'https://app.zenbree.test/settings/integrations/callback/google?code=c1&state=s1'
    res = client.get('/v1/integrations/mailboxes/google/callback', params={'error': 'access_denied'})
    assert res.headers['location'].endswith('/callback/google?error=access_denied')
    assert stored == [] and http.calls == []


def test_complete_stores_encrypted_refresh_token_with_pkce(client, http, stored):
    http.handler = _token_and_profile(f'openid email {GMAIL_SEND}')
    res = _complete(client, _state())
    assert res.status_code == 200 and res.json() == {'provider': 'google', 'account_label': 'me@acme.test'}
    ((args, kwargs),) = stored
    assert args == (UID, PID, 'google') and kwargs['label'] == 'me@acme.test' and kwargs['subject'] == 'g-123'
    assert kwargs['encrypted'] != 'rt-1' and token_crypto.decrypt(kwargs['encrypted']).value == 'rt-1'
    assert 'code_verifier=' + 'v' * 50 in http.calls[0].content.decode()


def test_complete_refuses_another_users_state(client, http, stored):
    """Connect CSRF: an attacker's consent link, completed by the victim, must not attach anything."""
    res = _complete(client, _state(user=ATTACKER))
    assert res.status_code == 400 and res.json()['detail']['code'] == 'INTEGRATIONS.INVALID_STATE'
    assert stored == [] and http.calls == []


def test_complete_refuses_a_state_from_another_team(client, http, stored):
    """The state's project must be in the caller's active organization (#307)."""
    res = _complete(client, _state(project_id='00000000-0000-0000-0000-0000000000p2'))
    assert res.status_code == 400 and res.json()['detail']['code'] == 'INTEGRATIONS.INVALID_STATE'
    assert stored == [] and http.calls == []


def test_complete_state_is_single_use(client, http, stored):
    http.handler = _token_and_profile(GMAIL_SEND)
    state = _state()
    assert _complete(client, state).status_code == 200
    res = _complete(client, state)
    assert res.status_code == 400 and res.json()['detail']['code'] == 'INTEGRATIONS.INVALID_STATE'
    assert len(stored) == 1


@pytest.mark.parametrize(
    ('scope', 'refresh', 'reason'),
    [
        ('openid email', 'rt-1', 'INTEGRATIONS.SCOPE_DECLINED'),
        ('', 'rt-1', 'INTEGRATIONS.SCOPE_DECLINED'),  # no scope reported is not a grant
        (GMAIL_SEND, None, 'INTEGRATIONS.NO_REFRESH_TOKEN'),
    ],
)
def test_complete_refuses_unusable_grant(client, http, stored, scope, refresh, reason):
    http.handler = _token_and_profile(scope, refresh)
    assert _complete(client, _state()).json()['detail']['code'] == reason
    assert stored == []


def test_complete_with_our_client_rejected_is_503_not_a_reconnect(client, http, stored):
    """#680: Google's recorded answer to a client it does not know; the user cannot fix it by reconnecting."""
    http.handler = lambda r: httpx.Response(
        401, json={'error': 'invalid_client', 'error_description': 'The OAuth client was not found.'}
    )
    res = _complete(client, _state())
    assert (res.status_code, res.json()['detail']['code']) == (503, 'INTEGRATIONS.CLIENT_REJECTED')
    assert stored == []


def test_complete_rejects_forged_state(client, http, stored):
    assert _complete(client, 'forged').json()['detail']['code'] == 'INTEGRATIONS.INVALID_STATE'
    assert stored == [] and http.calls == []


def test_disconnect_revokes_google_then_clears(client, http, monkeypatch):
    cleared = []
    ciphertext = token_crypto.encrypt('rt-1').value
    monkeypatch.setattr(mailboxes, 'get_conn', _fake_conn(ciphertext, cleared))
    http.handler = lambda r: httpx.Response(200)
    assert client.delete(f'/v1/integrations/mailboxes/{MB}').json() == {'connected': False}
    (revoke,) = http.calls
    assert str(revoke.url) == 'https://oauth2.googleapis.com/revoke' and revoke.content == b'token=rt-1'
    assert cleared  # cleared even if the revoke had failed
    http.handler = lambda r: httpx.Response(500)
    cleared.clear()
    assert client.delete(f'/v1/integrations/mailboxes/{MB}').status_code == 200 and cleared
    assert client.delete('/v1/integrations/mailboxes/google').status_code == 422


def _fake_conn(ciphertext, cleared):
    import contextlib

    class Conn:
        def execute(self, sql, params=()):
            if sql.lstrip().startswith('UPDATE'):
                cleared.append(params)
            self.row = {
                'id': MB,
                'provider': 'google',
                'refresh_token_encrypted': ciphertext,
                'user_id': UID,
                'provider_account_id': None,
                'inbound_subscription_id': None,
                'inbound_subscription_expires_at': None,  # never watched: the disconnect stops nothing
            }
            return self

        def fetchone(self):
            return self.row

        def fetchall(self):
            return [self.row]

    return lambda timeout=None: contextlib.nullcontext(Conn())


def _box(n: int, provider: str, status: str, user_id: str, scopes: tuple[str, ...] = ()) -> dict:
    return {
        'id': f'00000000-0000-0000-0000-00000000000{n}',
        'provider': provider,
        'status': status,
        'account_label': f'{n}@acme.test',
        'user_id': user_id,
        'granted_scopes': list(scopes),
    }


def test_list_gives_each_account_and_each_provider_to_connect(client, monkeypatch):
    boxes = [
        _box(1, 'google', 'connected', UID, ('https://www.googleapis.com/auth/gmail.readonly',)),
        _box(2, 'google', 'error', UID),
        # #747: connected before the read scope, so the card asks for a reconnect
        _box(3, 'microsoft', 'connected', ATTACKER, ('Mail.Send',)),
    ]
    monkeypatch.setattr(mailboxes, 'list_mailboxes', lambda pid: boxes)
    monkeypatch.setattr(settings, 'google_oauth_client_id', '')
    shown = [{k: v for k, v in box.items() if k not in ('user_id', 'granted_scopes')} for box in boxes]
    got = client.get('/v1/integrations/mailboxes').json()
    assert got['mailboxes'] == [
        {**shown[0], 'connected': True, 'can_disconnect': True, 'can_read_bounces': True, 'configured': False},
        {**shown[1], 'connected': False, 'can_disconnect': True, 'can_read_bounces': False, 'configured': False},
        # another member connected it, and the caller is no admin
        {**shown[2], 'connected': True, 'can_disconnect': False, 'can_read_bounces': False, 'configured': True},
    ]
    assert got['providers'] == [
        {'provider': 'google', 'configured': False},
        {'provider': 'microsoft', 'configured': True},
    ]


def test_not_configured_without_encryption_key(client, monkeypatch):
    monkeypatch.setattr(mailboxes, 'list_mailboxes', lambda pid: [])
    monkeypatch.setattr(settings, 'oauth_token_encryption_key', '')
    got = client.get('/v1/integrations/mailboxes').json()
    assert got['mailboxes'] == [] and not any(p['configured'] for p in got['providers'])


def test_only_the_connector_or_an_admin_disconnects(client, http, monkeypatch, sign_in_without_db):
    """Senders are the project's (#307): another member may use the mailbox but not disconnect it."""
    cleared = []
    monkeypatch.setattr(mailboxes, 'get_conn', _fake_conn(token_crypto.encrypt('rt-1').value, cleared))
    sign_in_without_db(CurrentUser(id=ATTACKER, org_id='org-1', org_role='member'))
    res = client.delete(f'/v1/integrations/mailboxes/{MB}')
    assert res.status_code == 403 and res.json()['detail']['code'] == 'INTEGRATIONS.DISCONNECT_FORBIDDEN'
    assert cleared == [] and http.calls == []
    sign_in_without_db(CurrentUser(id=ATTACKER, org_id='org-1', org_role='admin'))
    http.handler = lambda r: httpx.Response(200)
    assert client.delete(f'/v1/integrations/mailboxes/{MB}').status_code == 200 and cleared


async def test_the_account_identity_is_the_providers_subject_not_the_address(http):
    """#307: Google's userinfo ``sub`` and Graph's ``id`` tell mailboxes apart; the address is only the label
    (Microsoft's ``mail`` is tenant-editable and null on personal accounts)."""
    from app.integrations.oauth_client import OAuthClient

    http.handler = lambda r: httpx.Response(200, json={'email': 'a@x.test', 'name': 'Ada', 'sub': '1078'})
    assert await OAuthClient(GOOGLE).fetch_account('t') == ('a@x.test', '1078')
    http.handler = lambda r: httpx.Response(
        200, json={'mail': None, 'userPrincipalName': 'ada@contoso.test', 'displayName': 'Ada', 'id': 'oid-9'}
    )
    assert await OAuthClient(MICROSOFT).fetch_account('t') == ('ada@contoso.test', 'oid-9')
    http.handler = lambda r: httpx.Response(200, json={'email': 'a@x.test'})
    assert await OAuthClient(GOOGLE).fetch_account('t') == ('a@x.test', None)  # no subject: no in-place re-auth
