"""#305: a second organization never reads or writes the first one's rows, on every route family.

Organization A's data is made through A's own API calls (and a few direct inserts for what only the worker
writes); then B calls every route with A's ids and must get 404, or an empty answer, while A's rows stay put.
"""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agent import routes as agent_routes
from app.auth.middleware import CurrentUser
from app.main import app
from app.outreach.freeze import freezing
from tests.conftest import member, project, sign_in, uploaded
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db

PEOPLE_CSV = (
    'first_name,last_name,email,company_name\nAda,Lovelace,ada@acme.test,Acme\nAlan,Turing,alan@acme.test,Acme\n'
)


@pytest.fixture
def tc(db, monkeypatch, inline_worker, no_list_read_limit):
    monkeypatch.setattr(agent_routes, '_enqueue', lambda job_id: None)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user) -> None:
    sign_in(app, user)


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _org_a(tc, db, a) -> dict:
    """Projects, a chat with its run, a list from CSV, a campaign with a held message, an API key, and the
    per-user rows (exclusions, memory, notifications, inbox, saved search, webhook)."""
    _as(a)
    pid = _ok(tc.post('/v1/projects', json={'name': 'Client A'}))['project']['id']
    upload = uploaded(tc, f'/v1/lists/upload?entity=person&name=Leads&project_id={pid}', PEOPLE_CSV)
    lid = upload['list_id']
    row_id = _ok(tc.get(f'/v1/lists/{lid}/rows'))['rows'][0]['id']
    run = _ok(tc.post('/v1/runs', json={'message': 'Find agencies', 'project_id': pid}), 202)
    campaign = _ok(tc.post('/v1/campaigns', json={'name': 'Camp', 'list_id': lid}), 201)
    cid = campaign['id']
    with freezing(db):
        target = db.execute(
            "UPDATE outreach_campaign_targets SET status = 'active' WHERE campaign_id = %s RETURNING id, email",
            (cid,),
        ).fetchone()
    send_id = db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, subject,'
        " body, idempotency_key) VALUES (%s, %s, %s, 1, 'email', %s, 'Hi', 'Hello', %s) RETURNING id",
        (cid, a.id, target['id'], target['email'], uuid.uuid4().hex),
    ).fetchone()['id']
    key = _ok(tc.post('/v1/me/api-keys', json={'label': 'ci'}), 201)
    _ok(tc.post('/v1/exclusions', json={'items': [{'kind': 'domain', 'value': 'rival.test'}]}))
    _ok(tc.put('/v1/me/memory', json={'body': 'A sells to dentists', 'locked': False}))
    _ok(tc.post('/v1/saved-searches', json={'name': 'S', 'tool_name': 'search_people', 'list_id': lid}), 201)
    db.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, %s, 'agent_question', '/')",
        (a.id, a.org_id),
    )
    message_id = db.execute(
        'INSERT INTO outreach_inbound_messages (owner_id, project_id, channel, kind, provider_message_id, campaign_id,'
        " target_id, received_at) VALUES (%s, (SELECT project_id FROM outreach_campaigns WHERE id = %s), 'email',"
        " 'reply', %s, %s, %s, now()) RETURNING id",
        (a.id, cid, uuid.uuid4().hex, cid, target['id']),
    ).fetchone()['id']
    webhook_id = db.execute(
        'INSERT INTO user_webhooks (organization_id, user_id, url, secret_encrypted, event_names, label)'
        " VALUES (%s, %s, 'https://hooks.a.test', 'x', '{}', 'A') RETURNING id",
        (a.org_id, a.id),
    ).fetchone()['id']
    return {
        'project': pid,
        'list': lid,
        'row': row_id,
        'session': run['chat_session_id'],
        'job': run['id'],
        'campaign': cid,
        'send': str(send_id),
        'key': key,
        'message': str(message_id),
        'webhook': str(webhook_id),
    }


@needs_db
def test_a_second_org_never_reads_or_writes_the_first_orgs_rows(tc, db):
    a, b = member(db, _user(db)), member(db, _user(db))
    assert a.org_id != b.org_id
    ids = _org_a(tc, db, a)
    p, lst, s, c = ids['project'], ids['list'], ids['session'], ids['campaign']

    _as(b)
    missing = [
        # projects
        ('patch', f'/v1/projects/{p}', {'name': 'mine'}),
        ('delete', f'/v1/projects/{p}', None),
        # chats and runs
        ('post', '/v1/runs', {'message': 'x', 'project_id': p}),
        ('post', '/v1/runs', {'message': 'x', 'session_id': s}),
        ('get', f'/v1/chat/sessions/{s}', None),
        ('patch', f'/v1/chat/sessions/{s}', {'title': 'mine'}),
        ('delete', f'/v1/chat/sessions/{s}', None),
        ('get', f'/v1/chat/sessions/{s}/stream', None),
        ('get', f'/v1/runs/{ids["job"]}', None),
        ('post', f'/v1/runs/{ids["job"]}/cancel', None),
        # lists
        ('get', f'/v1/lists/{lst}', None),
        ('get', f'/v1/lists/{lst}/rows', None),
        ('get', f'/v1/lists/{lst}/stream', None),
        ('patch', f'/v1/lists/{lst}/rows/{ids["row"]}', {'excluded': True}),
        ('put', f'/v1/lists/{lst}/rows/{ids["row"]}/rating', {'rating': 'bad'}),
        ('post', f'/v1/lists/{lst}/columns', {'label': 'Fit', 'prompt': 'is it a fit?'}),
        ('post', f'/v1/lists/{lst}/columns/fit/fill', None),
        ('get', f'/v1/lists/{lst}/export.csv', None),
        ('get', f'/v1/lists/{lst}/export.xlsx', None),
        # campaigns
        ('post', '/v1/campaigns', {'name': 'mine', 'list_id': lst}),
        ('get', f'/v1/campaigns/{c}', None),
        ('patch', f'/v1/campaigns/{c}', {'name': 'mine'}),
        ('delete', f'/v1/campaigns/{c}', None),
        ('post', f'/v1/campaigns/{c}/activate', None),
        ('post', f'/v1/campaigns/{c}/pause', None),
        ('post', f'/v1/campaigns/{c}/resume', None),
        ('get', f'/v1/campaigns/{c}/preview', None),
        ('post', f'/v1/campaigns/{c}/preview', {'steps': []}),
        ('post', f'/v1/campaigns/{c}/test-email', {'step': 1}),
        ('get', f'/v1/campaigns/{c}/targets', None),
        ('get', f'/v1/campaigns/{c}/approvals', None),
        ('post', f'/v1/campaigns/{c}/approvals/approve', {'send_ids': [ids['send']]}),
        ('post', f'/v1/campaigns/{c}/approvals/reject', {'send_ids': [ids['send']]}),
        ('post', f'/v1/campaigns/{c}/approvals/undo-reject', {'send_ids': [ids['send']]}),
        ('patch', f'/v1/campaigns/{c}/approvals/{ids["send"]}', {'subject': 'x', 'body': 'y'}),
        # API keys (the organization's), and the per-user families
        ('delete', f'/v1/me/api-keys/{ids["key"]["id"]}', None),
        ('get', f'/v1/inbox/{ids["message"]}', None),
        ('post', f'/v1/inbox/{ids["message"]}/reply', {'body': 'hi'}),
        ('patch', f'/v1/inbox/{ids["message"]}', {'label': 'interested'}),
        ('delete', f'/v1/integrations/webhooks/{ids["webhook"]}', None),
    ]
    for method, url, body in missing:
        response = tc.request(method.upper(), url, json=body)
        assert response.status_code == 404, (method, url, response.status_code, response.text)
    upload = tc.post(f'/v1/lists/upload?entity=person&project_id={p}', content=PEOPLE_CSV)
    assert upload.status_code == 404
    refill = tc.post('/v1/saved-searches', json={'name': 'S', 'tool_name': 'search_people', 'list_id': lst})
    assert refill.status_code in (404, 422)

    # B's own views hold nothing of A's
    assert [x['id'] for x in _ok(tc.get('/v1/projects'))['projects']] != [p]
    assert p not in {x['id'] for x in _ok(tc.get('/v1/projects'))['projects']}
    assert _ok(tc.get('/v1/chat/sessions'))['sessions'] == []
    assert _ok(tc.get('/v1/lists'))['lists'] == []
    assert _ok(tc.get('/v1/campaigns'))['campaigns'] == []
    assert _ok(tc.get('/v1/home/todo'))['items'] == []
    assert _ok(tc.get('/v1/me/api-keys'))['keys'] == []
    assert _ok(tc.get('/v1/exclusions'))['exclusions'] == []
    assert _ok(tc.get('/v1/me/memory'))['body'] == ''
    assert _ok(tc.get('/v1/notifications'))['items'] == []
    assert _ok(tc.get('/v1/inbox'))['messages'] == []
    assert _ok(tc.get('/v1/saved-searches'))['saved_searches'] == []
    assert _ok(tc.get('/v1/integrations/webhooks'))['webhooks'] == []

    # and A's rows are as A left them
    _as(a)
    assert {'id': p, 'name': 'Client A'}.items() <= next(
        x for x in _ok(tc.get('/v1/projects'))['projects'] if x['id'] == p
    ).items()
    assert _ok(tc.get(f'/v1/chat/sessions/{s}'))['session']['title'] == 'Find agencies'
    assert _ok(tc.get(f'/v1/runs/{ids["job"]}'))['status'] == 'queued'
    rows = _ok(tc.get(f'/v1/lists/{lst}/rows'))['rows']
    assert len(rows) == 2 and not any(r['excluded'] or r['rating'] for r in rows)
    assert [col['key'] for col in _ok(tc.get(f'/v1/lists/{lst}'))['columns'] if col['kind'] == 'ai'] == []
    camp = _ok(tc.get(f'/v1/campaigns/{c}'))
    assert (camp['name'], camp['status'], camp['awaiting_approval']) == ('Camp', 'draft', 1)
    assert [k['revoked_at'] for k in _ok(tc.get('/v1/me/api-keys'))['keys']] == [None]
    assert len(_ok(tc.get('/v1/integrations/webhooks'))['webhooks']) == 1


@needs_db
def test_a_run_whose_chat_was_deleted_stays_visible_to_its_starter_only(tc, db):
    """docs/contracts-m1.md: the organization of the run is not enough once the chat is gone."""
    a, outsider = member(db, _user(db)), member(db, _user(db))
    teammate = CurrentUser(id=str(_user(db)), org_id=a.org_id, org_role='member')
    ids = _org_a(tc, db, a)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (a.org_id, teammate.id, 'member'),
    )
    db.execute('UPDATE jobs SET chat_session_id = NULL WHERE id = %s', (ids['job'],))
    seen = {}
    for name, user in (('starter', a), ('teammate', teammate), ('outsider', outsider)):
        _as(user)
        seen[name] = tc.get(f'/v1/runs/{ids["job"]}').status_code
    assert seen == {'starter': 200, 'teammate': 404, 'outsider': 404}


@needs_db
def test_an_api_key_acts_in_its_organization(tc, db):
    a, b = member(db, _user(db)), member(db, _user(db))
    ids = _org_a(tc, db, a)
    _as(b)
    b_list = uploaded(tc, '/v1/lists/upload?entity=person&name=Mine', PEOPLE_CSV)['list_id']
    app.dependency_overrides.clear()
    tc.headers['Authorization'] = f'Bearer {ids["key"]["key"]}'
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (ids['job'],))
    assert [x['id'] for x in _ok(tc.get('/v1/lists'))['lists']] == [ids['list']]
    assert tc.get(f'/v1/lists/{b_list}').status_code == 404
    run = _ok(tc.post('/v1/runs', json={'message': 'More', 'session_id': ids['session']}), 202)
    assert run['chat_session_id'] == ids['session']
    stored = db.execute('SELECT user_id::text AS user_id FROM jobs WHERE id = %s', (run['id'],)).fetchone()
    assert stored['user_id'] == a.id  # the key acts for whoever created it


@needs_db
def test_a_new_user_is_admin_of_one_organization_of_their_own(db):
    uid = _user(db)
    org = member(db, uid).org_id
    assert db.execute(
        'SELECT role FROM member WHERE "userId" = %s AND "organizationId" = %s', (uid, org)
    ).fetchone() == {'role': 'admin'}
    # a second call finds the same organization, never a new one
    assert member(db, uid).org_id == org
    assert db.execute('SELECT count(*) AS n FROM organization WHERE slug = %s', (uid,)).fetchone()['n'] == 1


@needs_db
@pytest.mark.parametrize(
    ('table', 'sql'),
    [
        ('projects', "INSERT INTO projects (user_id, name) VALUES (%s, 'p')"),
        # #307: a chat and a list need a project too; given one, the missing organization still refuses them
        ('chat_sessions', 'INSERT INTO chat_sessions (user_id, project_id) SELECT %s, id FROM projects'),
        ('lists', "INSERT INTO lists (user_id, project_id, name, entity) SELECT %s, id, 'l', 'person' FROM projects"),
        ('outreach_campaigns', "INSERT INTO outreach_campaigns (owner_id, name) VALUES (%s, 'c')"),
        ('user_api_keys', "INSERT INTO user_api_keys (user_id, label, key_hash) VALUES (%s, 'k', 'x')"),
    ],
)
def test_an_insert_that_names_no_organization_is_refused(db, table, sql):
    """#308 dropped the fill triggers: a user can be in several organizations, so none is guessed."""
    uid = _user(db)
    project(db, uid)
    with pytest.raises(psycopg.errors.NotNullViolation, match='organization_id'):
        db.execute(sql, (uid,))


def _join(db, user_id, org_id, role='member') -> None:
    """A second membership, as an accepted invitation (#308) makes it."""
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org_id, user_id, role),
    )


@needs_db
def test_members_of_one_org_share_its_rows_whoever_created_them(tc, db):
    """Scoping is by organization, not by creator: each member reads what the other one made."""
    a = member(db, _user(db))
    ids = _org_a(tc, db, a)
    m_id = _user(db)
    _join(db, m_id, a.org_id)
    m = CurrentUser(id=m_id, org_id=a.org_id)

    _as(m)
    assert ids['project'] in {x['id'] for x in _ok(tc.get('/v1/projects'))['projects']}
    assert _ok(tc.get(f'/v1/lists/{ids["list"]}'))['id'] == ids['list']
    assert len(_ok(tc.get(f'/v1/lists/{ids["list"]}/rows'))['rows']) == 2
    assert _ok(tc.get(f'/v1/chat/sessions/{ids["session"]}'))['session']['id'] == ids['session']
    assert _ok(tc.get(f'/v1/campaigns/{ids["campaign"]}'))['id'] == ids['campaign']
    assert len(_ok(tc.get(f'/v1/campaigns/{ids["campaign"]}/approvals'))['items']) == 1
    # other people than A's list: a row already in the project's lists is not imported again
    more = PEOPLE_CSV.replace('acme.test', 'beta.test')
    mine = uploaded(tc, f'/v1/lists/upload?entity=person&name=M&project_id={ids["project"]}', more)
    m_campaign = _ok(tc.post('/v1/campaigns', json={'name': 'M camp', 'list_id': mine['list_id']}), 201)['id']

    _as(a)
    assert _ok(tc.get(f'/v1/lists/{mine["list_id"]}'))['name'] == 'M'
    assert len(_ok(tc.get(f'/v1/lists/{mine["list_id"]}/rows'))['rows']) == 2
    assert _ok(tc.get(f'/v1/campaigns/{m_campaign}'))['name'] == 'M camp'
    assert {ids['list'], mine['list_id']} <= {x['id'] for x in _ok(tc.get('/v1/lists'))['lists']}
    stored = db.execute('SELECT user_id::text AS u FROM lists WHERE id = %s', (mine['list_id'],)).fetchone()
    assert stored['u'] == m_id  # user_id stays who created it


@needs_db
def test_the_same_user_in_another_org_sees_none_of_the_first(tc, db):
    """A user who belongs to both orgs, acting in B, gets 404 on the rows they made in A."""
    a, b = member(db, _user(db)), member(db, _user(db))
    ids = _org_a(tc, db, a)
    _join(db, a.id, b.org_id, 'admin')  # an admin there too: its keys are 404, not 403
    _as(CurrentUser(id=a.id, org_id=b.org_id))
    for url in (
        f'/v1/lists/{ids["list"]}',
        f'/v1/lists/{ids["list"]}/rows',
        f'/v1/chat/sessions/{ids["session"]}',
        f'/v1/campaigns/{ids["campaign"]}',
        f'/v1/campaigns/{ids["campaign"]}/approvals',
    ):
        assert tc.get(url).status_code == 404, url
    assert tc.patch(f'/v1/projects/{ids["project"]}', json={'name': 'x'}).status_code == 404
    assert tc.delete(f'/v1/me/api-keys/{ids["key"]["id"]}').status_code == 404
    assert ids['project'] not in {x['id'] for x in _ok(tc.get('/v1/projects'))['projects']}
    assert _ok(tc.get('/v1/lists'))['lists'] == []
    assert _ok(tc.get('/v1/chat/sessions'))['sessions'] == []
    assert _ok(tc.get('/v1/campaigns'))['campaigns'] == []
    assert _ok(tc.get('/v1/me/api-keys'))['keys'] == []
