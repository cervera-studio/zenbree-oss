import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.main import app

APP = Path(__file__).resolve().parents[1] / 'app'
# The two modules that build the shape: everything else raises through `api_error`.
ALLOWED = {APP / 'core' / 'errors_http.py', APP / 'core' / 'error_handlers.py'}


def test_http_exception_is_built_only_by_api_error():
    found = []
    for path in APP.rglob('*.py'):
        if path in ALLOWED:
            continue
        tree = ast.parse(path.read_text())
        # `from fastapi import HTTPException as E` would otherwise slip past a check on the name.
        names = {'HTTPException'} | {
            a.asname
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
            for a in n.names
            if a.name == 'HTTPException' and a.asname
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
                if name in names:
                    found.append(f'{path.relative_to(APP.parent)}:{node.lineno}')
    assert not found, f'raise api_error(status, code, message) instead: {found}'


@pytest.fixture
def client(sign_in_without_db):
    sign_in_without_db(CurrentUser(id='u', org_id='o'))
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _detail(response) -> dict:
    detail = response.json()['detail']
    assert isinstance(detail['code'], str) and isinstance(detail['message'], str)
    return detail


def test_an_unknown_route_is_request_not_found(client):
    r = client.get('/v1/nope')
    assert (r.status_code, _detail(r)['code']) == (404, 'REQUEST.NOT_FOUND')


def test_a_wrong_method_is_request_method_not_allowed_and_keeps_allow(client):
    r = client.delete('/health')
    assert (r.status_code, _detail(r)['code']) == (405, 'REQUEST.METHOD_NOT_ALLOWED')
    assert r.headers['allow'] == 'GET'


def test_a_bad_body_names_its_fields_without_echoing_the_input(client):
    r = client.post('/v1/projects', json={'name': 42, 'secret': 'jane@example.com'})
    detail = _detail(r)
    assert (r.status_code, detail['code']) == (422, 'REQUEST.INVALID_INPUT')
    assert detail['fields'] and all(set(f) == {'loc', 'msg', 'type'} for f in detail['fields'])
    assert 'jane@example.com' not in r.text


def test_an_unhandled_error_is_internal_and_readable_cross_origin(client, monkeypatch):
    def boom(_org_id):
        raise RuntimeError('db exploded')

    monkeypatch.setattr(main, '_live_subscription', boom)
    r = client.get('/v1/me', headers={'Origin': settings.web_origin})
    assert (r.status_code, _detail(r)['code']) == (500, 'INTERNAL.UNEXPECTED')
    assert 'exploded' not in r.text
    assert r.headers['access-control-allow-origin'] == settings.web_origin


def test_an_auth_error_keeps_its_code_and_header():
    r = TestClient(app).get('/v1/me')
    assert (r.status_code, _detail(r)['code']) == (401, 'AUTH.TOKEN_MISSING')
    assert r.headers['www-authenticate'] == 'Bearer'
