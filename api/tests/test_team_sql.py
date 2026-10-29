"""#308: the role in the organization and the membership itself are read from `member` on every call.

Real tokens (signed like the web's, verified through the middleware) and real `sbk_` keys against a migrated
schema: a member gets 403 on the admin-only routes, a promotion or a removal takes effect on the very next call
with the same credential, never after the token's 15 minutes.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import middleware
from app.main import app
from tests.conftest import uploaded
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_auth import _FakeJWKS, _token

pytestmark = needs_db


@pytest.fixture
def tc(db, monkeypatch, inline_worker):
    monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())
    return TestClient(app)


def _org(db, user_id) -> str:
    return str(db.execute('SELECT default_organization(%s) AS id', (user_id,)).fetchone()['id'])


def _join(db, user_id, org, role='member') -> None:
    db.execute(
        'INSERT INTO member (id, "organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, %s, now())',
        (uuid.uuid4(), org, user_id, role),
    )


def _as(user_id, org) -> dict:
    return {'Authorization': f'Bearer {_token(sub=user_id, org=org)}'}


ADMIN_ONLY = ['/v1/me/api-keys', '/v1/integrations/webhooks']


def test_a_member_uses_the_organization_but_not_its_admin_routes(tc, db):
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, bo, org)

    assert tc.get('/v1/projects', headers=_as(bo, org)).status_code == 200
    for path in ADMIN_ONLY:
        r = tc.get(path, headers=_as(bo, org))
        assert r.status_code == 403, path
        assert r.json()['detail']['code'] == 'AUTH.ORG_ADMIN_ONLY'
        assert tc.get(path, headers=_as(ada, org)).status_code == 200, path
    assert tc.post('/v1/me/api-keys', headers=_as(bo, org), json={'label': 'mine'}).status_code == 403
    assert not db.execute('SELECT 1 FROM user_api_keys WHERE user_id = %s', (bo,)).fetchone()


def test_a_role_change_applies_to_the_same_token_at_once(tc, db):
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, bo, org)
    headers = _as(bo, org)
    assert tc.get('/v1/me/api-keys', headers=headers).status_code == 403
    db.execute('UPDATE member SET role = %s WHERE "userId" = %s AND "organizationId" = %s', ('admin', bo, org))
    assert tc.get('/v1/me/api-keys', headers=headers).status_code == 200
    db.execute('UPDATE member SET role = %s WHERE "userId" = %s AND "organizationId" = %s', ('member', bo, org))
    assert tc.get('/v1/me/api-keys', headers=headers).status_code == 403


def test_a_removed_member_is_refused_on_the_next_call(tc, db):
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, bo, org)
    headers = _as(bo, org)  # still well inside its lifetime below
    assert tc.get('/v1/lists', headers=headers).status_code == 200
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, org))
    r = tc.get('/v1/lists', headers=headers)
    assert r.status_code == 401
    assert r.json()['detail']['code'] == 'AUTH.NOT_A_MEMBER'
    # A token for an organization the user never joined is refused the same way.
    assert tc.get('/v1/lists', headers=_as(ada, _org(db, bo))).status_code == 401


def test_an_api_key_acts_with_its_creators_current_role(tc, db):
    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, bo, org, role='admin')
    created = tc.post('/v1/me/api-keys', headers=_as(bo, org), json={'label': 'ci'})
    assert created.status_code == 201, created.text
    key = {'Authorization': f'Bearer {created.json()["key"]}'}
    assert tc.get('/v1/integrations/webhooks', headers=key).status_code == 200

    db.execute('UPDATE member SET role = %s WHERE "userId" = %s AND "organizationId" = %s', ('member', bo, org))
    assert tc.get('/v1/integrations/webhooks', headers=key).status_code == 403
    assert tc.get('/v1/lists', headers=key).status_code == 200

    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, org))
    r = tc.get('/v1/lists', headers=key)
    assert r.status_code == 401
    assert r.json()['detail']['code'] == 'AUTH.API_KEY_INVALID'


@pytest.mark.parametrize('ban_expires', [None, "now() + interval '1 day'"])
def test_a_ban_refuses_the_users_key_and_token_until_it_is_lifted(tc, db, ban_expires):
    ada = _user(db)
    org = _org(db, ada)
    headers = _as(ada, org)
    created = tc.post('/v1/me/api-keys', headers=headers, json={'label': 'ci'})
    assert created.status_code == 201, created.text
    key = {'Authorization': f'Bearer {created.json()["key"]}'}

    db.execute(f'UPDATE "user" SET banned = true, "banExpires" = {ban_expires or "NULL"} WHERE id = %s', (ada,))
    r = tc.get('/v1/lists', headers=key)
    assert r.status_code == 401 and r.json()['detail']['code'] == 'AUTH.API_KEY_INVALID'
    r = tc.get('/v1/lists', headers=headers)
    assert r.status_code == 401 and r.json()['detail']['code'] == 'AUTH.NOT_A_MEMBER'

    if ban_expires:  # the ban runs out: Better Auth leaves banned = true until the next sign-in
        db.execute('UPDATE "user" SET "banExpires" = now() - interval \'1 second\' WHERE id = %s', (ada,))
    else:  # unbanUser
        db.execute('UPDATE "user" SET banned = false, "banExpires" = NULL WHERE id = %s', (ada,))
    assert tc.get('/v1/lists', headers=key).status_code == 200
    assert tc.get('/v1/lists', headers=_as(ada, org)).status_code == 200


PEOPLE_CSV = 'first_name,last_name,email,company_name\nAda,Lovelace,ada@acme.test,Acme\n'


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _team_campaign(tc, db):
    """Org of Ada (admin) with members Bo and Cy. Bo, active in Ada's org, uploads a list and drafts a campaign."""
    ada, bo, cy = _user(db), _user(db), _user(db)
    org = _org(db, ada)
    _org(db, bo)  # Bo has his own organization too: the team one is not his default
    for u in (bo, cy):
        _join(db, u, org)
    pid = _ok(tc.get('/v1/projects', headers=_as(bo, org)))['projects'][0]['id']
    lid = uploaded(tc, f'/v1/lists/upload?entity=person&name=Leads&project_id={pid}', PEOPLE_CSV, headers=_as(bo, org))[
        'list_id'
    ]
    cid = _ok(tc.post('/v1/campaigns', json={'name': 'Camp', 'list_id': lid}, headers=_as(bo, org)), 201)['id']
    return ada, bo, cy, org, lid, cid


def test_rows_made_in_a_team_org_belong_to_it_not_to_the_makers_own(tc, db):
    _, bo, _, org, lid, cid = _team_campaign(tc, db)
    assert _org(db, bo) != org  # Bo's default organization is his own, not the team's
    for table, row_id in (('lists', lid), ('outreach_campaigns', cid)):
        got = db.execute(f'SELECT organization_id::text AS o FROM {table} WHERE id = %s', (row_id,)).fetchone()
        assert got['o'] == org, table
    # The upload made the list through a chat of the team org: create_list takes the chat's organization.
    chat = db.execute('SELECT chat_session_id FROM lists WHERE id = %s', (lid,)).fetchone()['chat_session_id']
    got = db.execute('SELECT organization_id::text AS o FROM chat_sessions WHERE id = %s', (chat,)).fetchone()
    assert got['o'] == org


SEND_IDS = {'send_ids': [str(uuid.uuid4())]}
MUTATIONS = [
    ('patch', '', {'name': 'Renamed'}),
    ('post', '/activate', None),
    ('post', '/pause', None),
    ('post', '/resume', None),
    ('post', '/approvals/approve', SEND_IDS),
    ('post', '/approvals/reject', SEND_IDS),
    ('post', '/approvals/undo-reject', SEND_IDS),
    ('patch', f'/approvals/{uuid.uuid4()}', {'subject': 'Hi', 'body': 'Hello'}),
    ('post', '/test-email', {'step': 1}),
    ('delete', '', None),
]


def test_any_member_drives_a_campaign_of_the_project(tc, db):
    """#307: a campaign sends from its project's senders, which any member may use: no owner gate. A colleague
    gets past every route (whatever the answer, it is never a 403), and the campaign lands in the list's project."""
    _, _, cy, org, _, cid = _team_campaign(tc, db)
    row = db.execute(
        'SELECT c.project_id = l.project_id AS same FROM outreach_campaigns c JOIN lists l ON l.id = c.list_id'
        ' WHERE c.id = %s',
        (cid,),
    ).fetchone()
    assert row['same']

    def call(user, method, path, body):
        kwargs = {'headers': _as(user, org)} | ({'json': body} if body is not None else {})
        return getattr(tc, method)(f'/v1/campaigns/{cid}{path}', **kwargs)

    for method, path, body in MUTATIONS[:-1]:
        assert call(cy, method, path, body).status_code != 403, (method, path)
    assert call(cy, 'delete', '', None).status_code == 204


def test_webhooks_belong_to_the_organization_and_only_its_admins_manage_them(tc, db, monkeypatch):
    """#323: an endpoint one admin made is the team's: another admin lists and deletes it, a member gets 403 on
    every webhook route, and the team's endpoint is not in the member's own organization."""
    from cryptography.fernet import Fernet

    from app.core.config import settings
    from app.core.errors import ok
    from app.integrations import webhooks

    monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(webhooks, 'validate_url', ok)
    ada, bo, cy = _user(db), _user(db), _user(db)
    org = _org(db, ada)
    _join(db, bo, org, role='admin')
    _join(db, cy, org)
    body = {'url': 'https://hooks.test/team', 'label': 'Team', 'event_names': ['campaign.finished']}
    hook = _ok(tc.post('/v1/integrations/webhooks', json=body, headers=_as(ada, org)), 201)['id']

    assert [w['id'] for w in _ok(tc.get('/v1/integrations/webhooks', headers=_as(bo, org)))['webhooks']] == [hook]
    for method, path, payload in (
        ('get', '', None),
        ('post', '', body),
        ('delete', f'/{hook}', None),
        ('post', f'/{hook}/test', None),
    ):
        r = tc.request(method.upper(), f'/v1/integrations/webhooks{path}', json=payload, headers=_as(cy, org))
        assert r.status_code == 403, (method, path)
        assert r.json()['detail']['code'] == 'AUTH.ORG_ADMIN_ONLY'
    assert _ok(tc.get('/v1/integrations/webhooks', headers=_as(cy, _org(db, cy))))['webhooks'] == []
    assert tc.delete(f'/v1/integrations/webhooks/{hook}', headers=_as(bo, org)).status_code == 204
    assert _ok(tc.get('/v1/integrations/webhooks', headers=_as(ada, org)))['webhooks'] == []
    stored = db.execute('SELECT organization_id::text AS o, user_id::text AS u FROM user_webhooks').fetchone()
    assert stored == {'o': org, 'u': ada}  # the organization's, made by Ada


def test_runs_are_shared_and_only_their_starter_or_an_admin_stops_one(tc, db, monkeypatch):
    """#323: a run is its chat's, so every member reads it; stopping it is for who started it, or an admin."""
    from app.agent import routes as agent_routes

    monkeypatch.setattr(agent_routes, '_enqueue', lambda job_id: None)
    ada, bo, cy, org, _, _ = _team_campaign(tc, db)
    run = _ok(tc.post('/v1/runs', json={'message': 'Find agencies'}, headers=_as(bo, org)), 202)
    for user in (ada, bo, cy):
        got = _ok(tc.get(f'/v1/runs/{run["id"]}', headers=_as(user, org)))
        assert (got['id'], got['chat_session_id']) == (run['id'], run['chat_session_id'])
    r = tc.post(f'/v1/runs/{run["id"]}/cancel', headers=_as(cy, org))
    assert r.status_code == 403 and r.json()['detail']['code'] == 'JOB.NOT_OWNER'
    assert _ok(tc.post(f'/v1/runs/{run["id"]}/cancel', headers=_as(ada, org)))['status'] == 'cancelling'
    assert _ok(tc.post(f'/v1/jobs/{run["id"]}/cancel', headers=_as(bo, org)))['status'] == 'cancelling'
    # Bo acting in his own organization reaches none of it.
    assert tc.get(f'/v1/runs/{run["id"]}', headers=_as(bo, _org(db, bo))).status_code == 404


def test_saved_searches_are_shared_and_managed_by_their_creator_or_an_admin(tc, db):
    """#323: a saved search is its list's, so members see it and its runs; its creator or an admin pauses,
    edits, runs or deletes it, and an admin's edit or run keeps spending as the creator."""
    ada, bo, cy, org, lid, _ = _team_campaign(tc, db)
    body = {'name': 'Refill', 'tool_name': 'search_people', 'list_id': lid}
    search = _ok(tc.post('/v1/saved-searches', json=body, headers=_as(bo, org)), 201)
    url = f'/v1/saved-searches/{search["id"]}'

    for user in (ada, cy):
        listed = _ok(tc.get('/v1/saved-searches', headers=_as(user, org)))['saved_searches']
        assert [s['id'] for s in listed] == [search['id']]
        assert _ok(tc.get(f'{url}/runs', headers=_as(user, org)))['runs'] == []
    for method, path, payload in (
        ('patch', '', {'enabled': False}),
        ('put', '', {**body, 'name': 'Mine'}),
        ('post', '/run', None),
        ('delete', '', None),
    ):
        r = tc.request(method.upper(), f'{url}{path}', json=payload, headers=_as(cy, org))
        assert r.status_code == 403, (method, path)
    edited = _ok(tc.put(url, json={**body, 'name': 'Team refill'}, headers=_as(ada, org)))
    assert (edited['name'], str(edited['user_id'])) == ('Team refill', bo)
    run = _ok(tc.post(f'{url}/run', headers=_as(ada, org)), 202)
    assert str(run['user_id']) == bo  # spends as its creator
    assert [r['id'] for r in _ok(tc.get(f'{url}/runs', headers=_as(cy, org)))['runs']] == [run['id']]
    assert _ok(tc.get('/v1/saved-searches', headers=_as(bo, _org(db, bo))))['saved_searches'] == []
    assert tc.delete(url, headers=_as(bo, org)).status_code == 204
    assert _ok(tc.get('/v1/saved-searches', headers=_as(ada, org)))['saved_searches'] == []


def test_a_chat_stream_stops_for_a_member_removed_mid_turn(tc, db, monkeypatch):
    """#323: an open stream re-reads the membership (every MEMBER_RECHECK_SECONDS, 0 here: before each frame):
    after the removal it sends an auth error and `done`, and no further frame of the job. The job's frames run one
    frame ahead of the check, so `hi` may come before the error or not; `secret`, written after the removal, never."""
    import json

    from app.agent import events
    from app.agent import routes as agent_routes
    from app.core import event_stream

    ada, bo = _user(db), _user(db)
    org = _org(db, ada)
    _join(db, bo, org)
    monkeypatch.setattr(agent_routes, '_enqueue', lambda job_id: None)
    monkeypatch.setattr(event_stream, 'MEMBER_RECHECK_SECONDS', 0)

    async def tail(job_id, after=0):
        yield 1, 'text', json.dumps({'content': 'hi'})
        db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, org))
        yield 2, 'text', json.dumps({'content': 'secret'})
        yield 3, 'done', json.dumps({'sources': []})

    monkeypatch.setattr(events, 'tail', tail)
    r = tc.post('/v1/chat', json={'message': 'agencies'}, headers=_as(bo, org))
    assert r.status_code == 200
    frames = [f.split('\n') for f in r.text.split('\n\n') if f]
    heads = [f[0] for f in frames]
    assert heads in (
        ['event: session', 'event: text', 'event: error', 'event: done'],
        ['event: session', 'event: error', 'event: done'],
    )
    assert json.loads(frames[-2][-1].removeprefix('data: '))['code'] == 'AUTH.NOT_A_MEMBER'
    assert 'secret' not in r.text
