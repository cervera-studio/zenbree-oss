"""Token endpoint errors (#680): only a dead grant asks the user to reconnect. Bodies recorded from the live
endpoints on 2026-10-13, except where marked as Microsoft's documented shape."""

import httpx
import pytest
from structlog.testing import capture_logs

from app.core.config import settings
from app.integrations.oauth_client import OAuthClient
from app.integrations.providers import GOOGLE, HUBSPOT, MICROSOFT

SECRET = 'SECRET-680'
REFRESH = 'RT-680'

GOOGLE_INVALID_CLIENT = {'error': 'invalid_client', 'error_description': 'The OAuth client was not found.'}
# Google deleted_client (#697): the body of a forum report, not recorded from our client.
GOOGLE_DELETED_CLIENT = {'error': 'deleted_client', 'error_description': 'The OAuth client was deleted.'}
MICROSOFT_INVALID_GRANT = {
    'error': 'invalid_grant',
    'error_description': 'AADSTS9002313: Invalid request. Request is malformed or invalid.',
    'error_codes': [9002313],
}
HUBSPOT_BAD_REFRESH_TOKEN = {
    'status': 'BAD_REFRESH_TOKEN',
    'message': 'missing or invalid refresh token',
    'correlationId': 'c-1',
    'error': 'invalid_request',
    'error_description': 'missing or invalid refresh token',
}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for name in ('google', 'microsoft', 'hubspot'):
        monkeypatch.setattr(settings, f'{name}_oauth_client_id', f'{name}-id')
        monkeypatch.setattr(settings, f'{name}_oauth_client_secret', SECRET)


def _answer(http, status: int, body: dict | str) -> None:
    http.handler = lambda request: (
        httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    )


@pytest.mark.parametrize(
    ('provider', 'status', 'body', 'code'),
    [
        (GOOGLE, 401, GOOGLE_INVALID_CLIENT, 'INTEGRATIONS.CLIENT_REJECTED'),
        # Microsoft's documented shapes: an expired secret, an app the user's tenant removed, consent withdrawn,
        # an outage, MFA.
        (
            MICROSOFT,
            401,
            {'error': 'invalid_client', 'error_description': 'AADSTS7000215: Invalid client secret provided.'},
            'INTEGRATIONS.CLIENT_REJECTED',
        ),
        (
            MICROSOFT,
            400,
            {
                'error': 'unauthorized_client',
                'error_description': "AADSTS700016: Application with identifier 'microsoft-id' was not found in the "
                "directory 'Contoso'.",
            },
            'INTEGRATIONS.AUTH',
        ),
        (MICROSOFT, 400, {'error': 'consent_required'}, 'INTEGRATIONS.AUTH'),
        (MICROSOFT, 400, {'error': 'temporarily_unavailable'}, 'INTEGRATIONS.UNAVAILABLE'),
        (MICROSOFT, 400, {'error': 'interaction_required'}, 'INTEGRATIONS.AUTH'),
        (GOOGLE, 401, GOOGLE_DELETED_CLIENT, 'INTEGRATIONS.CLIENT_REJECTED'),
        (MICROSOFT, 400, MICROSOFT_INVALID_GRANT, 'INTEGRATIONS.AUTH'),
        (HUBSPOT, 400, HUBSPOT_BAD_REFRESH_TOKEN, 'INTEGRATIONS.AUTH'),
        (
            HUBSPOT,
            400,
            {'status': 'MISSING_PARAMETERS', 'message': 'missing one or more parameters', 'error': 'invalid_request'},
            'INTEGRATIONS.HTTP_ERROR',
        ),
        (GOOGLE, 400, '<html>Bad Request</html>', 'INTEGRATIONS.HTTP_ERROR'),
        (GOOGLE, 400, {'error': ['invalid_grant']}, 'INTEGRATIONS.HTTP_ERROR'),
    ],
    ids=[
        'google-invalid_client',
        'microsoft-invalid_client',
        'microsoft-unauthorized_client',
        'microsoft-consent_required',
        'microsoft-temporarily_unavailable',
        'microsoft-interaction_required',
        'google-deleted_client',
        'microsoft-invalid_grant',
        'hubspot-BAD_REFRESH_TOKEN',
        'hubspot-MISSING_PARAMETERS',
        'not-json',
        'error-not-a-string',
    ],
)
async def test_refresh_maps_each_token_endpoint_body_to_one_code(http, provider, status, body, code):
    _answer(http, status, body)
    grant = await OAuthClient(provider).refresh(REFRESH)
    assert grant.error.code == code
    assert len(http.calls) == 1


async def test_hubspot_bad_auth_code_asks_for_a_new_sign_in(http):
    _answer(
        http, 400, {'status': 'BAD_AUTH_CODE', 'message': 'missing or unknown auth code', 'error': 'invalid_request'}
    )
    grant = await OAuthClient(HUBSPOT).exchange_code(
        code='c1', redirect_uri='https://api.zenbree.test/cb', code_verifier=''
    )
    assert grant.error.code == 'INTEGRATIONS.AUTH'


async def test_rejected_client_is_logged_as_an_error_without_secrets(http):
    _answer(http, 401, GOOGLE_INVALID_CLIENT)
    with capture_logs() as logs:
        await OAuthClient(GOOGLE).refresh(REFRESH)
    assert [(e['event'], e['log_level'], e['provider'], e['status'], e['error']) for e in logs] == [
        ('integrations.token.client_rejected', 'error', 'google', 401, 'invalid_client')
    ]
    assert SECRET in http.calls[0].content.decode()  # the form carried it, so the absence below is meaningful
    assert SECRET not in repr(logs) and REFRESH not in repr(logs)
