"""Request body cap (#419): over the cap is 413 before auth, without buffering the body."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.auth.middleware import CurrentUser
from app.core.body_limit import MAX_BODY_BYTES, BodyLimit
from app.core.config import settings
from app.lists import csv_routes
from app.lists.csv_import import MAX_BYTES as CSV_MAX_BYTES
from app.lists.csv_import import CsvError
from app.main import app
from app.projects import routes as project_routes

client = TestClient(app)
CHUNK = 64 * 1024


def _chunked(total: int, sent: list[int]):
    """A body with no Content-Length; ``sent`` records how much of it the server pulled."""
    while sum(sent) < total:
        n = min(CHUNK, total - sum(sent))
        sent.append(n)
        yield b'x' * n


def test_content_length_over_the_cap_is_413_without_reading_the_body():
    async def receive():
        raise AssertionError('the body was read')

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/v1/chat',
        'headers': [(b'content-length', str(MAX_BODY_BYTES + 1).encode())],
    }
    asyncio.run(BodyLimit(app)(scope, receive, send))
    assert sent[0]['status'] == 413
    assert b'REQUEST.TOO_LARGE' in sent[1]['body']


@pytest.mark.parametrize(
    ('url', 'headers'),
    [
        ('/v1/chat', {}),
        ('/v1/billing/stripe-webhook', {'stripe-signature': 't=1,v1=x'}),
        ('/v1/integrations/linkedin/webhook', {'Unipile-Auth': 's3cret'}),
    ],
)
def test_a_chunked_body_over_the_cap_stops_at_the_cap(url, headers, monkeypatch):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 's3cret')
    sent: list[int] = []
    res = client.post(url, content=_chunked(MAX_BODY_BYTES + 1, sent), headers=headers)
    assert res.status_code == 413, res.text
    assert res.json()['detail']['code'] == 'REQUEST.TOO_LARGE'
    assert sum(sent) <= MAX_BODY_BYTES + CHUNK


def test_under_the_cap_reaches_the_route():
    assert client.post('/v1/chat', content=b'x' * MAX_BODY_BYTES).status_code == 401


def test_the_csv_upload_takes_its_own_cap(monkeypatch, sign_in_without_db):
    def parsed(*_):
        raise CsvError('LIST.CSV_EMPTY', 'reached the parser')

    monkeypatch.setattr(csv_routes, 'headers', parsed)
    monkeypatch.setattr(csv_routes, 'read_csv', parsed)
    sign_in_without_db(CurrentUser(id='u', org_id='o'))
    monkeypatch.setattr(project_routes, '_active', lambda user, header: 'p')
    try:
        for url in ('/v1/lists/upload?entity=person', '/v1/lists/upload/preview?entity=person'):
            assert client.post(url, content=_chunked(CSV_MAX_BYTES, [])).status_code == 422
            assert client.post(url, content=_chunked(CSV_MAX_BYTES + 1, [])).status_code == 413
            assert client.post(url, content=b'x' * (CSV_MAX_BYTES + 1)).status_code == 413
    finally:
        app.dependency_overrides.clear()


def test_a_streaming_response_ends_when_its_request_body_passes_the_cap():
    async def events():
        while True:
            yield b'data: x\n\n'
            await asyncio.sleep(0)

    sse = BodyLimit(StreamingResponse(events(), media_type='text/event-stream'))

    async def receive():
        await asyncio.sleep(0)
        return {'type': 'http.request', 'body': b'x' * CHUNK, 'more_body': True}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'asgi': {'spec_version': '2.3'}, 'method': 'GET', 'path': '/sse', 'headers': []}
    asyncio.run(asyncio.wait_for(sse(scope, receive, send), 5))
    assert sent[0] == {'type': 'http.response.start', 'status': 200, 'headers': sent[0]['headers']}


@pytest.mark.parametrize('header', [{}, {'Unipile-Auth': 'wrong'}])
def test_the_linkedin_webhook_authenticates_before_it_parses(header, monkeypatch):
    monkeypatch.setattr(settings, 'unipile_webhook_secret', 's3cret')
    res = client.post('/v1/integrations/linkedin/webhook', content=b'not json', headers=header)
    assert res.status_code == 401, res.text
