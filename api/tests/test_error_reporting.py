import json

import httpx
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.main import init_error_reporting

# Built at runtime: the event's frames quote this file's source lines, so a literal marker would always match.
API_KEY = 'sbk_x' + 'SECRET' * 2
BODY_MARKER = 'BODY' + '_MARKER' * 2
LOCAL_MARKER = 'LOCAL' + '_MARKER' * 2
QUERY_MARKER = 'QUERY' + '_MARKER' * 2
OUTGOING_MARKER = 'OUTGOING' + '_MARKER' * 2


class Collect(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


def test_a_500_report_holds_no_api_key_body_locals_or_query_strings() -> None:
    transport = Collect()
    init_error_reporting('https://public@errors.example.invalid/1', transport=transport)
    app = FastAPI()

    @app.post('/boom')
    async def boom(request: Request) -> None:
        body = await request.json()
        local_marker = [LOCAL_MARKER + '@example.com', body]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as vendor:
            await vendor.get(f'https://x.test/?api={OUTGOING_MARKER}#{OUTGOING_MARKER}')
        raise RuntimeError(f'boom {len(local_marker)}')

    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            f'/boom?code={QUERY_MARKER}',
            headers={'Authorization': f'Bearer {API_KEY}'},
            json={'note': BODY_MARKER},
        )
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_global_scope().set_client(None)

    assert response.status_code == 500
    events = [item.payload.json for env in transport.envelopes for item in env.items if item.type == 'event']
    assert events, 'the 500 produced no error event'
    serialized = json.dumps(events)
    assert 'RuntimeError' in serialized
    assert 'https://x.test/' in serialized, 'the outgoing call left no breadcrumb'
    for secret in (API_KEY, BODY_MARKER, LOCAL_MARKER, QUERY_MARKER, OUTGOING_MARKER):
        assert secret not in serialized
