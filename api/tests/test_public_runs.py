"""Public runs use the real chat/job SQL path, without a streaming client."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent import routes
from app.auth.middleware import hash_api_key
from app.main import app
from tests import test_agent
from tests.conftest import member, sign_out, start_turn

# Reuse the existing isolated SQL fixtures.
db = test_agent.db
client = test_agent.client
needs_db = test_agent.needs_db


def test_public_runs_auth_and_openapi():
    tc = TestClient(app)
    assert tc.post('/v1/runs', json={'message': 'Find agencies'}).status_code == 401
    assert tc.get(f'/v1/runs/{uuid4()}').status_code == 401
    assert tc.get('/docs').status_code == 200
    schema = tc.get('/openapi.json').json()
    for path, method in (('/v1/runs', 'post'), ('/v1/runs/{job_id}', 'get')):
        operation = schema['paths'][path][method]
        assert operation['security'] == [{'HTTPBearer': []}]
        status = '202' if method == 'post' else '200'
        assert '$ref' in operation['responses'][status]['content']['application/json']['schema']


@needs_db
@pytest.mark.parametrize(
    'body', [{}, {'message': ''}, {'message': 'x' * 20_001}, {'message': 'x', 'session_id': 'invalid'}]
)
def test_public_runs_validation(client, db, body):
    tc, _ = client
    assert tc.post('/v1/runs', json=body).status_code == 422
    assert db.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 0


@needs_db
def test_public_run_api_key_poll_and_lists(client, db, monkeypatch):
    tc, uid = client
    key = 'sbk_test-public-run'
    db.execute(
        'INSERT INTO user_api_keys (organization_id, user_id, label, key_hash) VALUES (default_organization(%s), %s, %s, %s)',
        (uid, uid, 'test', hash_api_key(key)),
    )
    sign_out(app)
    tc.headers['Authorization'] = f'Bearer {key}'
    queued = []
    monkeypatch.setattr(routes, '_enqueue', queued.append)
    response = tc.post('/v1/runs', json={'message': 'Find agencies'})
    assert response.status_code == 202 and response.headers['content-type'] == 'application/json'
    accepted = response.json()
    assert queued == [accepted['id']]
    run_url = f'/v1/runs/{accepted["id"]}'
    run = tc.get(run_url).json()
    assert run['status'] == 'queued' and run['chat_session_id'] == accepted['chat_session_id']
    assert run == tc.get(f'/v1/jobs/{accepted["id"]}').json()
    assert run['credits'] == {'reserved': 0, 'spent': 0}
    assert tc.post('/v1/runs', json={'message': 'again'}).status_code == 409
    assert db.execute('SELECT count(*) AS n FROM chat_messages').fetchone()['n'] == 1
    assert db.execute('SELECT count(*) AS n FROM chat_sessions').fetchone()['n'] == 1
    from app.lists.rows import add_rows, create_list

    lid = create_list(db, user_id=uid, session_id=accepted['chat_session_id'], name='Agencies', entity='company')
    add_rows(db, lid, 'company', [{'name': 'Acme', 'domain': 'acme.test'}])
    db.execute(
        "UPDATE jobs SET status = 'succeeded', list_id = %s, finished_at = now() WHERE id = %s", (lid, accepted['id'])
    )
    assert tc.get(run_url).json()['list_id'] == lid
    assert tc.get(f'/v1/lists/{lid}/rows').json()['rows'][0]['cells']['name'] == 'Acme'
    followup = tc.post('/v1/runs', json={'message': 'Find emails', 'session_id': accepted['chat_session_id']})
    assert followup.status_code == 202 and followup.json()['chat_session_id'] == accepted['chat_session_id']
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (followup.json()['id'],))
    # Deleting a session preserves its jobs and lists, clearing the session reference.
    assert tc.delete(f'/v1/chat/sessions/{accepted["chat_session_id"]}').status_code == 200
    detached = tc.get(run_url)
    assert detached.status_code == 200
    assert detached.json()['chat_session_id'] is None and detached.json()['list_id'] == lid


@needs_db
def test_public_runs_foreign_resources(client, db, monkeypatch):
    tc, _ = client
    foreign = test_agent._user(db)
    sid, jid = start_turn(member(db, foreign), routes.ChatRequest(message='Other user'))
    pid = db.execute('SELECT project_id FROM chat_sessions WHERE id = %s', (sid,)).fetchone()['project_id']
    assert tc.get(f'/v1/runs/{jid}').status_code == 404
    assert tc.get(f'/v1/runs/{uuid4()}').status_code == 404
    for resource, value in (('session_id', sid), ('project_id', str(pid))):
        assert tc.post('/v1/runs', json={'message': 'x', resource: value}).status_code == 404
    assert db.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 1


@needs_db
def test_public_runs_queue_failure(client, db, monkeypatch):
    from app.worker import run_job

    tc, _ = client

    def unavailable(*args):
        raise ConnectionError('broker unavailable')

    monkeypatch.setattr(run_job, 'delay', unavailable)
    response = tc.post('/v1/runs', json={'message': 'Find agencies'})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'CHAT.QUEUE_UNAVAILABLE'
    job = db.execute('SELECT * FROM jobs').fetchone()
    assert job['status'] == 'failed' and job['credits_spent'] == 0
    monkeypatch.setattr(run_job, 'delay', lambda job_id: None)
    assert tc.post('/v1/runs', json={'message': 'Retry'}).status_code == 202
