import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from jwt import PyJWK
from starlette.requests import Request

from app.auth import middleware
from app.core.config import settings
from app.core.scope import ActorGone, Member, OrgId, UserId
from app.main import app
from tests.conftest import no_membership_read

_KEY = Ed25519PrivateKey.generate()


class _FakeJWKS:
    """Stands in for PyJWKClient: serves the public half of _KEY as kid k1."""

    def get_signing_key_from_jwt(self, token):
        if jwt.get_unverified_header(token).get('kid') != 'k1':
            raise jwt.PyJWKClientError('Unable to find a signing key that matches')
        return PyJWK.from_dict(jwt.algorithms.OKPAlgorithm.to_jwk(_KEY.public_key(), as_dict=True))


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    """The JWKS, and a ``member`` stand-in: (user, org) -> role, where anyone not listed is an admin of the
    token's organization (#308) and None is no longer a member. Other test modules import this fixture for both.
    It replaces the membership read, which needs a database: tests/test_scope_request_sql.py runs the real one."""
    monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())
    roles: dict[tuple[str, str], str | None] = {}

    async def enter_member(org_id: str, user_id: str) -> str:
        no_membership_read(settings.database_url)
        if (role := roles.get((user_id, org_id), 'admin')) is None:
            raise ActorGone(Member(OrgId(org_id), UserId(user_id)))
        return role

    monkeypatch.setattr(middleware, 'enter_member', enter_member)
    monkeypatch.setattr(middleware, '_user_role', lambda user_id: 'admin')  # the token's role decides here
    return roles


def _token(key=_KEY, kid='k1', **claims):
    payload = {
        'sub': 'u1',
        'org': 'o1',
        'email': 'a@b.c',
        'iss': settings.auth_issuer,
        'aud': settings.auth_audience,
        'exp': int(time.time()) + 60,
        **claims,
    }
    return jwt.encode(payload, key, algorithm='EdDSA', headers={'kid': kid})


client = TestClient(app)


def _ping(token=None):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    return client.get('/v1/admin/ping', headers=headers)


def test_missing_token_401():
    r = _ping()
    assert r.status_code == 401
    assert r.json()['detail']['code'] == 'AUTH.TOKEN_MISSING'


@pytest.mark.parametrize(
    'token',
    [
        'not-a-jwt',
        _token(key=Ed25519PrivateKey.generate(), role='admin'),  # wrong signer
        _token(kid='nope', role='admin'),  # unknown kid
        _token(role='admin', aud='someone-else'),
        _token(role='admin', exp=int(time.time()) - 10),
    ],
    # Each process signs with new keys, so the tokens cannot be the ids: pytest-xdist workers must collect the same.
    ids=['malformed', 'wrong-signer', 'unknown-kid', 'wrong-audience', 'expired'],
)
def test_invalid_token_401(token):
    assert _ping(token).status_code == 401


def _current_user(token, authenticate=middleware.authenticate, current_user=middleware.current_user):
    async def resolve():
        principal = await authenticate(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token))
        return await current_user(Request({'type': 'http'}), principal)

    return asyncio.run(resolve())


def test_the_org_claim_scopes_the_caller(monkeypatch):
    monkeypatch.setattr(middleware, '_default_org', lambda user_id: pytest.fail('the claim is enough'))
    assert _current_user(_token()).org_id == 'o1'


def test_a_token_from_before_organizations_gets_the_users_own(monkeypatch):
    monkeypatch.setattr(middleware, '_default_org', lambda user_id: f'org-of-{user_id}')
    assert _current_user(_token(org=None)).org_id == 'org-of-u1'
    monkeypatch.setattr(middleware, '_default_org', lambda user_id: None)
    with pytest.raises(HTTPException) as e:
        _current_user(_token(org=None))
    assert e.value.status_code == 401


def test_the_role_in_the_organization_comes_from_member_not_the_token(fake_jwks):
    assert _current_user(_token(org_role='member')).org_role == 'admin'
    fake_jwks['u1', 'o1'] = 'member'
    assert _current_user(_token()).org_role == 'member'


def test_api_key_resolves_owner_and_org_without_role(monkeypatch):
    seen = {}

    def lookup(key_hash):
        seen['hash'] = key_hash
        return middleware.Principal(user_id='u2', org_id='o2')

    monkeypatch.setattr(middleware, '_lookup_api_key', lookup)
    user = _current_user('sbk_secret', middleware.authenticate_or_api_key, middleware.current_user_or_api_key)
    assert (user.id, user.org_id, user.role) == ('u2', 'o2', None)
    assert seen['hash'] == middleware.hash_api_key('sbk_secret')
    # admin routes stay JWT-only
    assert _ping('sbk_secret').status_code == 401
