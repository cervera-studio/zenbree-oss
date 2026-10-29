import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import app


def _untyped(schema: dict) -> bool:
    if '$ref' in schema or 'properties' in schema:
        return False
    if 'anyOf' in schema:
        return any(_untyped(s) for s in schema['anyOf'] if s.get('type') != 'null')
    if schema.get('type') == 'array':
        return _untyped(schema.get('items', {}))
    return schema.get('type') in (None, 'object')


def test_every_success_response_has_a_schema():
    untyped = []
    for path, operations in app.openapi()['paths'].items():
        for method, operation in operations.items():
            for status, response in operation['responses'].items():
                if not status.startswith('2') or status == '204':
                    continue
                content = response.get('content', {})
                json_schema = content.get('application/json', {}).get('schema')
                if not content or (json_schema is not None and _untyped(json_schema)):
                    untyped.append(f'{method.upper()} {path} {status}')
    assert not untyped, 'responses without a schema:\n' + '\n'.join(sorted(untyped))


def test_schema_names_are_unique():
    """Two models with one class name show in /docs as ``app__module__Name``: rename one."""
    prefixed = [name for name in app.openapi()['components']['schemas'] if '__' in name]
    assert not prefixed


def test_every_route_the_contract_doc_names_exists():
    """docs/contracts-m1.md holds meaning, not shapes (`/openapi.json` has those), so a field check would only pad it.
    What still goes stale is a route that the doc names after a rename or removal."""
    brace = re.compile(r'\{[^}]*\}')
    hidden = {  # include_in_schema=False: not in /openapi.json, so listed here
        '/v1/inbound/email',
        '/v1/unsubscribe/{token}',
        '/v1/integrations/mailboxes/{provider}/callback',
        '/v1/integrations/hubspot/callback',
        '/v1/webhooks/hubspot',
        '/v1/integrations/mailboxes/google/push',
        '/v1/integrations/mailboxes/microsoft/push',
        '/v1/integrations/instantly/webhooks/{subscription_id}',
        '/v1/automation-hooks/{hook_id}',
    }
    routes = {brace.sub('{}', path) for path in (*app.openapi()['paths'], *hidden)}
    text = (Path(__file__).parents[2] / 'docs/contracts-m1.md').read_text()
    named = {brace.sub('{}', match.rstrip('.').rstrip('/')) for match in re.findall(r'/v1/[\w/{}.\-]*', text)}
    route_groups = {'/v1/admin'}  # the doc names the whole group, not one route
    missing = sorted(path for path in named if path not in routes and path not in route_groups)
    assert not missing, 'docs/contracts-m1.md names routes that do not exist:\n' + '\n'.join(missing)


@pytest.mark.parametrize(
    'path',
    [
        '/v1/chat',
        '/v1/chat/sessions/{session_id}/stream',
        '/v1/lists/{list_id}/stream',
        '/v1/home/stream',
        '/v1/notifications/stream',
    ],
)
def test_streams_are_event_streams(path):
    (operation,) = app.openapi()['paths'][path].values()
    assert list(operation['responses']['200']['content']) == ['text/event-stream']


def test_a_lossy_response_model_fails_the_test_that_calls_it():
    class Narrow(BaseModel):
        id: int

    lossy = FastAPI()

    @lossy.get('/thing', response_model=Narrow)
    def thing() -> dict:
        return {'id': 1, 'name': 'dropped by Narrow'}

    class Total(BaseModel):
        total: float

    @lossy.get('/total', response_model=Total)
    def total() -> dict:
        return {'total': 0}

    for path in ('/thing', '/total'):
        with pytest.raises(AssertionError, match='the response model changed the JSON'):
            TestClient(lossy).get(path)


def test_every_422_documents_the_invalid_input_error():
    schema = app.openapi()
    refs = {
        op['responses']['422']['content']['application/json']['schema']['$ref']
        for ops in schema['paths'].values()
        for op in ops.values()
        if '422' in op['responses']
    }
    assert refs == {'#/components/schemas/InvalidInputError'}
    schemas = schema['components']['schemas']
    assert 'HTTPValidationError' not in schemas and 'ValidationError' not in schemas
    detail = schemas['InvalidInputDetail']
    assert detail['properties']['code']['type'] == 'string'
    assert detail['required'] == ['code', 'message']
    assert set(schemas['InvalidField']['properties']) == {'loc', 'msg', 'type'}


def test_the_notification_stream_takes_no_locale():
    # #795: opening the app must not name a language to save.
    operation = app.openapi()['paths']['/v1/notifications/stream']['get']
    assert 'locale' not in {p['name'] for p in operation.get('parameters', [])}
