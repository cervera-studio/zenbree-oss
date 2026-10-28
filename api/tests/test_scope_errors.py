"""The two scope bugs answer the shared 500 body and reach error reporting, like any other exception. They are
BaseExceptions, so the app's ``except Exception`` blocks never hide them; Starlette's handlers and the server's
error page do not catch them either (review round 1, non-blocking 7)."""

import json

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.core import error_handlers
from app.core.scope import ScopeChangeWhileOpen, ScopeMissing
from app.main import app
from tests.test_error_reporting import Collect

SCOPE_BUGS = [ScopeMissing('no scope'), ScopeChangeWhileOpen('scope changed while a connection is open')]


@pytest.mark.parametrize('bug', SCOPE_BUGS, ids=lambda e: type(e).__name__)
def test_a_scope_bug_answers_the_shared_500(monkeypatch, sign_in_without_db, bug):
    """Through the real app: a route that hits the bug answers ``INTERNAL.UNEXPECTED`` JSON, not the server's
    plain-text 500."""
    from app.auth.middleware import CurrentUser

    def broken(org_id):
        raise bug

    monkeypatch.setattr(main, '_live_subscription', broken)
    sign_in_without_db(CurrentUser(id='u', org_id='o'))
    try:
        r = TestClient(app, raise_server_exceptions=False).get('/v1/me')
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 500
    assert r.json() == {'detail': {'code': 'INTERNAL.UNEXPECTED', 'message': 'internal error'}}


@pytest.mark.parametrize('bug', SCOPE_BUGS, ids=lambda e: type(e).__name__)
def test_a_scope_bug_reaches_error_reporting(bug):
    transport = Collect()
    main.init_error_reporting('https://public@errors.example.invalid/1', transport=transport)
    probe = FastAPI()
    error_handlers.install(probe)

    @probe.get('/boom')
    async def boom() -> None:
        raise bug

    try:
        r = TestClient(probe, raise_server_exceptions=False).get('/boom')
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_global_scope().set_client(None)
    assert r.json()['detail']['code'] == 'INTERNAL.UNEXPECTED'
    events = [item.payload.json for env in transport.envelopes for item in env.items if item.type == 'event']
    assert type(bug).__name__ in json.dumps(events), 'the scope bug produced no error event'
