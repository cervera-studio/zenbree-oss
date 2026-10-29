"""Instantly (#929): connect, preview, confirm and publish against real SQL; Instantly itself is a fake. Needs
TEST_DATABASE_URL."""

import asyncio
import json
import os
import sys
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.billing import row_charges
from app.core.config import settings
from app.integrations import instantly, instantly_publish
from app.main import app
from app.outreach import holds
from tests.conftest import as_request, hold, member, migrated_database, pool_on, project, scoped_conn, sign_in
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: Instantly SQL test NOT run')

KEY = 'ins-secret-key-1234567890'
CAMPAIGN = str(uuid.uuid4())
_DB: list = []


class FakeInstantly:
    """The slice of API v2 that Zenbree calls. ``leads`` is what the campaign holds (email to lead id)."""

    def __init__(self):
        self.campaign_status = 0
        self.workspace = 'ws-1'
        self.valid_key = KEY
        self.leads: dict[str, str] = {}
        self.add_calls: list[list[dict]] = []
        self.add_bodies: list[dict] = []
        self.fail_add: list = []  # one entry per call to fail: 'timeout' | 'applied-timeout' | 429 | 500 | 404
        self.refuse = 0  # a status every call answers (401, 402)
        self.blocked: set[str] = set()
        self.campaign_gone = False
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get('authorization') != f'Bearer {self.valid_key}':
            return httpx.Response(401, json={'statusCode': 401, 'error': 'Unauthorized', 'message': 'no'})
        if self.refuse:
            return httpx.Response(self.refuse, json={})
        path = request.url.path
        self.paths.append(path)
        if path == '/api/v2/workspaces/current':
            return httpx.Response(200, json={'id': self.workspace, 'name': 'Acme workspace'})
        if path == '/api/v2/campaigns':
            return httpx.Response(
                200, json={'items': [{'id': CAMPAIGN, 'name': 'Q4 outreach', 'status': self.campaign_status}]}
            )
        if path == f'/api/v2/campaigns/{CAMPAIGN}' and self.campaign_gone:
            return httpx.Response(404, json={})
        if path == f'/api/v2/campaigns/{CAMPAIGN}':
            return httpx.Response(200, json={'id': CAMPAIGN, 'name': 'Q4 outreach', 'status': self.campaign_status})
        if path == '/api/v2/leads/add':
            return self._add(request)
        if path == '/api/v2/leads/list':
            wanted = json.loads(request.content)['contacts']
            items = [{'id': self.leads[e], 'email': e} for e in wanted if e in self.leads]
            return httpx.Response(200, json={'items': items})
        return httpx.Response(404, json={})

    def _add(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.add_bodies.append(body)
        self.add_calls.append(body['leads'])
        mode = self.fail_add.pop(0) if self.fail_add else None
        created = []
        if mode in (None, 'applied-timeout'):
            for i, lead in enumerate(body['leads']):
                if lead['email'] in self.blocked or lead['email'] in self.leads:
                    continue
                self.leads[lead['email']] = f'lead-{len(self.leads) + 1}'
                created.append({'index': i, 'id': self.leads[lead['email']], 'email': lead['email']})
        if mode in ('timeout', 'applied-timeout'):
            raise httpx.ReadTimeout('too slow', request=request)
        if isinstance(mode, int):
            return httpx.Response(mode, json={}, headers={'Retry-After': '0'} if mode == 429 else {})
        return httpx.Response(
            200, json={'status': 'success', 'total_sent': len(body['leads']), 'created_leads': created}
        )


@pytest.fixture
def fake(http):
    state = FakeInstantly()
    http.handler = state.handler
    return state


@pytest.fixture
def db(monkeypatch, fake):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (instantly, instantly_publish):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
            monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
            monkeypatch.setattr('app.notifications.service.get_conn', scoped_conn(c))
            monkeypatch.setattr(instantly, 'is_disabled', lambda key: False)
            monkeypatch.setattr(instantly_publish, 'vendor_disabled_now', lambda provider: False)
            monkeypatch.setattr(instantly_publish, 'PAUSE_SECONDS', 0)
            monkeypatch.setattr(settings, 'oauth_token_encryption_key', Fernet.generate_key().decode())
            queued: list[str] = []
            monkeypatch.setattr('app.integrations.instantly_task.enqueue', queued.append)
            monkeypatch.setattr(sys.modules[__name__], '_DB', [c])
            c.queued = queued
            yield c
        finally:
            app.dependency_overrides.clear()


def _as(uid) -> TestClient:
    sign_in(app, member(_DB[0], uid))
    return TestClient(app)


def _list(c, uid, people) -> tuple[str, list[str]]:
    lid = c.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        " VALUES (default_organization(%s), %s, %s, 'L', 'person') RETURNING id",
        (uid, project(c, uid), uid),
    ).fetchone()['id']
    ids = []
    for cells in people:
        for key in cells:
            c.execute(
                "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, %s, %s, 'code', 0)"
                ' ON CONFLICT DO NOTHING',
                (lid, key, key),
            )
        ids.append(
            str(
                c.execute(
                    'INSERT INTO list_rows (list_id, cells) VALUES (%s, %s) RETURNING id', (lid, json.dumps(cells))
                ).fetchone()['id']
            )
        )
    return str(lid), ids


def _person(n: int, **extra) -> dict:
    return {
        'full_name': f'Pat Person{n}',
        'email': f'p{n}@acme{n}.test',
        'title': 'CEO',
        'company_name': 'Acme',
        **extra,
    }


def _connect(client, name='Main') -> dict:
    res = client.post('/v1/integrations/instantly/connect', json={'name': name, 'api_key': KEY})
    assert res.status_code == 200, res.text
    return res.json()


def _body(lid, ids, **extra) -> dict:
    return {'list_id': lid, 'row_ids': ids, 'campaign_id': CAMPAIGN, **extra}


def _confirm(client, lid, ids, **extra):
    return client.post(
        '/v1/integrations/instantly/enrollments', json=_body(lid, ids, confirm=True, acknowledge_sending=True, **extra)
    )


def _run(c, uid, eid):
    with as_request(member(c, uid)):
        asyncio.run(instantly_publish.run(eid))


def _outcomes(c, eid) -> dict[str, str]:
    return {
        r['email'] or str(r['id']): r['outcome'] + (f':{r["reason"]}' if r['reason'] else '')
        for r in c.execute('SELECT id, email, outcome, reason FROM instantly_leads WHERE enrollment_id = %s', (eid,))
    }


def _setup(db, n=3, **kw):
    uid = _user(db)
    client = _as(uid)
    _connect(client)
    lid, ids = _list(db, uid, [_person(i) for i in range(n)])
    return uid, client, lid, ids


# ── connection ─────────────────────────────────────────────────────────────


def test_connect_validates_encrypts_and_never_returns_the_key(db, fake, caplog):
    uid = _user(db)
    client = _as(uid)
    out = _connect(client)
    assert out['connected'] and out['workspace_name'] == 'Acme workspace' and out['can_manage']
    assert KEY not in json.dumps(out) and 'api_key' not in out
    stored = db.execute('SELECT api_key_encrypted, workspace_id FROM instantly_connections').fetchone()
    assert stored['workspace_id'] == 'ws-1' and KEY not in stored['api_key_encrypted']
    assert client.get('/v1/integrations/instantly').json() == out
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    ('status', 'code', 'http_status'),
    [
        (401, 'INSTANTLY.INVALID_KEY', 400),
        (402, 'INSTANTLY.PLAN_REQUIRED', 422),
        (403, 'INSTANTLY.MISSING_SCOPE', 422),
        (429, 'INSTANTLY.RATE_LIMITED', 503),
    ],
)
def test_connect_refusals_are_actionable_and_store_nothing(db, fake, status, code, http_status):
    fake.refuse = status
    res = _as(_user(db)).post('/v1/integrations/instantly/connect', json={'name': 'Main', 'api_key': KEY})
    assert res.status_code == http_status and res.json()['detail']['code'] == code
    assert db.execute('SELECT count(*) AS n FROM instantly_connections').fetchone()['n'] == 0


def test_only_who_connected_or_an_admin_replaces_or_disconnects(db, fake):
    uid = _user(db)
    _connect(_as(uid))
    org = member(db, uid).org_id
    mate = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, mate),
    )
    other = _as(mate)
    other_headers = {'X-Project-Id': project(db, uid)}
    assert other.get('/v1/integrations/instantly', headers=other_headers).json()['can_manage'] is False
    res = other.post('/v1/integrations/instantly/connect', json={'name': 'X', 'api_key': KEY}, headers=other_headers)
    assert res.status_code == 403 and res.json()['detail']['code'] == 'INSTANTLY.NOT_ALLOWED'
    assert other.delete('/v1/integrations/instantly', headers=other_headers).status_code == 403
    db.execute('UPDATE member SET role = \'admin\' WHERE "userId" = %s', (mate,))
    assert other.delete('/v1/integrations/instantly', headers=other_headers).status_code == 200


def test_disconnect_clears_the_key_and_keeps_the_leads(db, fake):
    uid, client, lid, ids = _setup(db)
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    out = client.delete('/v1/integrations/instantly').json()
    assert out['connected'] is False and out['name'] is None
    row = db.execute('SELECT api_key_encrypted, status FROM instantly_connections').fetchone()
    assert row == {'api_key_encrypted': '', 'status': 'disconnected'}
    assert db.execute("SELECT count(*) AS n FROM instantly_leads WHERE outcome = 'accepted'").fetchone()['n'] == 3
    assert client.get('/v1/integrations/instantly/campaigns').status_code == 409  # NOT_CONNECTED


def test_a_workspace_is_a_project_of_one_organization_only(db, fake):
    first, second = _user(db), _user(db)
    _connect(_as(first))
    other = _as(second)
    assert other.get('/v1/integrations/instantly').json()['connected'] is False
    assert other.get('/v1/integrations/instantly/campaigns').status_code == 409


# ── preview and confirm ────────────────────────────────────────────────────


def test_preview_names_what_would_go_and_why_not_and_changes_nothing(db, fake):
    uid = _user(db)
    client = _as(uid)
    _connect(client)
    people = [
        _person(1),
        _person(2, email=''),
        _person(3, email='p3@blocked.test'),
        _person(4, email='p1@acme1.test'),
        _person(5),
    ]
    lid, ids = _list(db, uid, people)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'blocked.test')",
        (project(db, uid), uid),
    )
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " VALUES (default_organization(%s), %s, %s, 'N', 'draft') RETURNING id",
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, replied_at)'
        " VALUES (%s, %s, 'p5@acme5.test', 'replied', now())",
        (cid, uid),
    )
    res = client.post('/v1/integrations/instantly/preview', json=_body(lid, ids))
    assert res.status_code == 200, res.text
    out = res.json()
    assert out['selected'] == 5 and out['eligible'] == 1
    assert out['skipped'] == {
        'no_email': 1,
        'excluded': 1,
        'duplicate_in_selection': 1,
        'replied_or_contacted': 1,
    }
    assert out['campaign']['may_send'] is False and out['campaign']['url'].endswith(CAMPAIGN)
    assert out['sample'][0]['lead']['email'] == 'p1@acme1.test'
    assert out['sample'][0]['lead']['first_name'] == 'Pat' and out['sample'][0]['lead']['job_title'] == 'CEO'
    assert fake.add_calls == []
    assert db.execute('SELECT count(*) AS n FROM instantly_leads').fetchone()['n'] == 0


def test_preview_only_reads_public_columns(db, fake):
    _, client, lid, ids = _setup(db, 1)
    bad = client.post('/v1/integrations/instantly/preview', json=_body(lid, ids, mapping={'custom': ['email_source']}))
    assert bad.status_code == 422 and bad.json()['detail']['code'] == 'INSTANTLY.INVALID_MAPPING'
    ok = client.post('/v1/integrations/instantly/preview', json=_body(lid, ids, mapping={'custom': ['company_name']}))
    assert ok.status_code == 200 and ok.json()['custom_variables'] == ['company_name']


def test_confirm_is_explicit_and_a_running_campaign_needs_its_own_acknowledgement(db, fake):
    _, client, lid, ids = _setup(db)
    no = client.post('/v1/integrations/instantly/enrollments', json=_body(lid, ids))
    assert no.status_code == 409 and no.json()['detail']['code'] == 'INSTANTLY.CONFIRM_REQUIRED'
    fake.campaign_status = 1  # the campaign was started in Instantly after the preview
    unacknowledged = client.post('/v1/integrations/instantly/enrollments', json=_body(lid, ids, confirm=True))
    assert unacknowledged.status_code == 409
    assert unacknowledged.json()['detail']['code'] == 'INSTANTLY.SENDING_CONFIRM_REQUIRED'
    assert db.execute('SELECT count(*) AS n FROM instantly_enrollments').fetchone()['n'] == 0
    assert fake.add_calls == []
    assert _confirm(client, lid, ids).status_code == 202


def test_a_list_of_another_project_or_org_is_not_found(db, fake):
    _, client, lid, ids = _setup(db)
    stranger = _user(db)
    other_lid, other_ids = _list(db, stranger, [_person(9)])
    res = client.post('/v1/integrations/instantly/preview', json=_body(other_lid, other_ids))
    assert res.status_code == 404 and res.json()['detail']['code'] == 'LIST.NOT_FOUND'
    mixed = client.post('/v1/integrations/instantly/preview', json=_body(lid, [*ids, *other_ids]))
    assert mixed.json()['selected'] == 3  # a row of another list is not in the selection


# ── publish ────────────────────────────────────────────────────────────────


def test_publish_records_every_outcome_and_ids_and_is_never_an_email_sent(db, fake):
    uid, client, lid, ids = _setup(db, 4)
    fake.blocked = {'p3@acme3.test'}
    before = db.execute(
        'SELECT (SELECT count(*) FROM outreach_campaign_sends) AS s, (SELECT count(*) FROM credit_transactions) AS c'
    ).fetchone()
    res = _confirm(client, lid, ids)
    assert res.status_code == 202 and res.json()['status'] == 'queued' and db.queued == [res.json()['id']]
    eid = res.json()['id']
    _run(db, uid, eid)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert view['status'] == 'completed' and view['counts']['accepted'] == 3 and view['counts']['skipped'] == 1
    assert {r['email']: r['outcome'] for r in view['rows']}['p3@acme3.test'] == 'skipped'
    stored = db.execute(
        "SELECT workspace_id, campaign_id, instantly_lead_id FROM instantly_leads WHERE outcome = 'accepted'"
    ).fetchall()
    assert {r['workspace_id'] for r in stored} == {'ws-1'} and {r['campaign_id'] for r in stored} == {CAMPAIGN}
    assert all(r['instantly_lead_id'] for r in stored)
    after = db.execute(
        'SELECT (SELECT count(*) FROM outreach_campaign_sends) AS s, (SELECT count(*) FROM credit_transactions) AS c'
    ).fetchone()
    assert after == before
    sent = fake.add_calls[0][0]
    assert sent['email'] == 'p0@acme0.test' and sent['first_name'] == 'Pat' and 'email_status' not in sent


def test_publish_is_bounded_in_chunks_of_100(db, fake):
    uid = _user(db)
    client = _as(uid)
    _connect(client)
    lid, ids = _list(db, uid, [_person(i) for i in range(250)])
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    assert [len(c) for c in fake.add_calls] == [100, 100, 50]
    assert db.execute("SELECT count(*) AS n FROM instantly_leads WHERE outcome = 'accepted'").fetchone()['n'] == 250


def test_people_are_judged_again_right_before_their_chunk_goes(db, fake):
    uid, client, lid, ids = _setup(db, 4)
    eid = _confirm(client, lid, ids).json()['id']
    pid = project(db, uid)
    # After the confirmation: an exclusion, a reply, a native campaign and another batch take three of them.
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'email', 'p0@acme0.test')",
        (pid, uid),
    )
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " VALUES (default_organization(%s), %s, %s, 'N', 'active') RETURNING id",
        (uid, pid, uid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, replied_at)'
        " VALUES (%s, %s, 'p1@acme1.test', 'replied', now()), (%s, %s, 'p2@acme2.test', 'active', NULL)",
        (cid, uid, cid, uid),
    )
    _run(db, uid, eid)
    assert _outcomes(db, eid) == {
        'p0@acme0.test': 'skipped:excluded',
        'p1@acme1.test': 'skipped:replied_or_contacted',
        'p2@acme2.test': 'skipped:in_native_campaign',
        'p3@acme3.test': 'accepted',
    }
    assert [lead['email'] for lead in fake.add_calls[0]] == ['p3@acme3.test']


def test_a_hubspot_hold_is_read_fresh_and_a_failed_read_fails_closed(db, fake, monkeypatch):
    uid, client, lid, ids = _setup(db, 3)
    db.execute(
        'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted, status)'
        " VALUES (default_organization(%s), %s, 'hubspot', 'x', 'connected')",
        (uid, uid),
    )
    eid = _confirm(client, lid, ids).json()['id']

    async def check(people, now, access_token, schedule_retry):
        ((owner, emails),) = people.items()
        for email in emails[:-1]:  # the last email's read "failed": no verdict is stored for it
            db.execute(
                'INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, %s, %s, %s)'
                ' ON CONFLICT (owner_id, email) DO UPDATE SET reason = EXCLUDED.reason, checked_at = EXCLUDED.checked_at',
                (owner, email, 'HUBSPOT_CUSTOMER' if email.startswith('p1') else None, now),
            )

    monkeypatch.setattr(instantly_publish.gate, 'check', check)
    _run(db, uid, eid)
    assert _outcomes(db, eid) == {
        'p0@acme0.test': 'accepted',
        'p1@acme1.test': 'skipped:crm_hold',
        'p2@acme2.test': 'skipped:crm_hold',
    }


def test_a_timeout_is_uncertain_and_never_retried_blindly(db, fake):
    uid, client, lid, ids = _setup(db, 3)
    fake.fail_add = ['applied-timeout']  # Instantly took them, and the answer never came back
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert view['status'] == 'failed' and view['error_code'] == 'INSTANTLY.UNKNOWN_OUTCOME'
    assert view['counts']['uncertain'] == 3 and len(fake.add_calls) == 1 and view['can_retry']
    # The retry is refused until Instantly can have indexed what it took...
    assert client.post(f'/v1/integrations/instantly/enrollments/{eid}/retry').status_code == 409
    db.execute("UPDATE instantly_leads SET updated_at = now() - interval '5 minutes'")
    assert client.post(f'/v1/integrations/instantly/enrollments/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    # ... then it looks the campaign up first: all three are there, nothing is added twice.
    assert len(fake.add_calls) == 1
    done = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert done['status'] == 'completed' and done['counts']['accepted'] == 3 and done['counts']['uncertain'] == 0


def test_an_uncertain_batch_that_never_arrived_goes_again_once_reconciled(db, fake):
    uid, client, lid, ids = _setup(db, 3)
    fake.fail_add = ['timeout']  # the request never took effect
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    db.execute("UPDATE instantly_leads SET updated_at = now() - interval '5 minutes'")
    assert client.post(f'/v1/integrations/instantly/enrollments/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    assert len(fake.add_calls) == 2 and len(fake.leads) == 3
    assert client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()['counts']['accepted'] == 3


def test_a_worker_killed_mid_request_leaves_uncertain_rows(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute("UPDATE instantly_leads SET outcome = 'publishing', updated_at = now() - interval '5 minutes'")
    with as_request(member(db, uid)):
        assert instantly_publish.claim(eid) is not None
    assert set(_outcomes(db, eid).values()) == {'uncertain'}


def test_a_stalled_batch_can_be_resumed_by_the_user(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    assert client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()['can_retry'] is False
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    assert client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()['can_retry'] is True
    assert client.post(f'/v1/integrations/instantly/enrollments/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    assert client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()['status'] == 'completed'


def test_throttling_waits_then_goes_on_and_gives_up_without_losing_people(db, fake, monkeypatch):
    slept = []

    async def nap(seconds):
        slept.append(seconds)

    monkeypatch.setattr(instantly_publish.asyncio, 'sleep', nap)
    uid, client, lid, ids = _setup(db, 2)
    fake.fail_add = [429]
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    assert client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()['status'] == 'completed'
    assert len(fake.add_calls) == 2 and len(fake.leads) == 2  # the refused request created nothing
    # Throttled for good: the people stay pending and Retry resumes them.
    uid2 = _user(db)
    client2 = _as(uid2)
    _connect(client2)
    lid2, ids2 = _list(db, uid2, [_person(i + 50) for i in range(2)])
    fake.fail_add = [429] * 10
    eid2 = _confirm(client2, lid2, ids2).json()['id']
    _run(db, uid2, eid2)
    view = client2.get(f'/v1/integrations/instantly/enrollments/{eid2}').json()
    assert view['status'] == 'failed' and view['error_code'] == 'INSTANTLY.RATE_LIMITED'
    assert view['counts']['pending'] == 2 and view['can_retry']


def test_a_refused_key_marks_the_connection_and_keeps_people_pending(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    fake.valid_key = 'rotated'  # the user revoked the key in Instantly
    _run(db, uid, eid)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert (
        view['status'] == 'failed' and view['error_code'] == 'INSTANTLY.INVALID_KEY' and view['counts']['pending'] == 2
    )
    assert client.get('/v1/integrations/instantly').json()['status'] == 'error'


def test_cancel_a_queued_batch_and_stop_a_running_one(db, fake):
    uid, client, lid, ids = _setup(db, 3)
    eid = _confirm(client, lid, ids).json()['id']
    out = client.post(f'/v1/integrations/instantly/enrollments/{eid}/cancel').json()
    assert out['status'] == 'cancelled' and out['counts']['skipped'] == 3
    _run(db, uid, eid)  # a run that starts after the cancel does nothing
    assert fake.add_calls == []
    lid2, ids2 = _list(db, uid, [_person(i + 20) for i in range(3)])
    eid2 = _confirm(client, lid2, ids2).json()['id']
    db.execute('UPDATE instantly_enrollments SET cancel_requested = true WHERE id = %s', (eid2,))
    _run(db, uid, eid2)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid2}').json()
    assert view['status'] == 'cancelled' and fake.add_calls == [] and view['counts']['skipped'] == 3


def test_one_live_batch_for_each_project(db, fake):
    _, client, lid, ids = _setup(db, 2)
    assert _confirm(client, lid, ids).status_code == 202
    again = _confirm(client, lid, ids)
    assert again.status_code == 409 and again.json()['detail']['code'] == 'INSTANTLY.BUSY'


def test_a_person_already_held_is_never_added_again(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    first = _confirm(client, lid, ids).json()['id']
    _run(db, uid, first)
    second = _confirm(client, lid, ids)
    assert second.status_code == 202
    assert set(_outcomes(db, second.json()['id']).values()) == {'skipped:already_in_instantly'}
    assert len(fake.add_calls) == 1


def test_another_organization_cannot_read_or_stop_a_batch(db, fake):
    _, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    stranger = _as(_user(db))
    assert stranger.get(f'/v1/integrations/instantly/enrollments/{eid}').status_code == 404
    assert stranger.post(f'/v1/integrations/instantly/enrollments/{eid}/cancel').status_code == 404
    assert stranger.post(f'/v1/integrations/instantly/enrollments/{eid}/retry').status_code == 404


def test_the_kill_switch_stops_a_running_batch_and_refuses_new_work(db, fake, monkeypatch):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    monkeypatch.setattr(instantly_publish, 'vendor_disabled_now', lambda provider: True)
    fake.paths.clear()  # the confirmation read the campaign; the run must read nothing
    _run(db, uid, eid)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert view['error_code'] == 'INSTANTLY.DISABLED' and view['counts']['pending'] == 2 and fake.add_calls == []
    assert fake.paths == []
    monkeypatch.setattr(instantly, 'is_disabled', lambda key: True)
    res = client.post('/v1/integrations/instantly/preview', json=_body(lid, ids))
    assert res.status_code == 503 and res.json()['detail']['code'] == 'INSTANTLY.DISABLED'


def test_a_removed_row_is_skipped_not_sent(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute('DELETE FROM list_rows WHERE id = %s', (ids[0],))
    _run(db, uid, eid)
    assert _outcomes(db, eid) == {'p0@acme0.test': 'skipped:row_removed', 'p1@acme1.test': 'accepted'}


# ── review round 1 ─────────────────────────────────────────────────────────


def test_an_email_the_verifier_rejected_never_goes_even_with_a_linkedin_url(db, fake):
    uid = _user(db)
    client = _as(uid)
    _connect(client)
    bad = _person(1, email_status='invalid', linkedin_url='https://www.linkedin.com/in/pat-one')
    lid, ids = _list(db, uid, [bad, _person(2)])
    preview = client.post('/v1/integrations/instantly/preview', json=_body(lid, ids)).json()
    assert preview['eligible'] == 1 and preview['skipped'] == {'no_email': 1}
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    assert [lead['email'] for call in fake.add_calls for lead in call] == ['p2@acme2.test']
    assert list(fake.leads) == ['p2@acme2.test']


def _native_target(db, uid, email):
    pid = project(db, uid)
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
        " VALUES (default_organization(%s), %s, %s, 'N', 'active') RETURNING id",
        (uid, pid, uid),
    ).fetchone()['id']
    target = db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status)'
        " VALUES (%s, %s, %s, 'active') RETURNING *",
        (cid, uid, email),
    ).fetchone()
    return {**target, 'project_id': pid}


def test_a_native_email_is_not_claimed_for_a_person_instantly_holds(db, fake):
    from app.outreach.claims import claim

    uid, client, lid, ids = _setup(db, 2)
    _run(db, uid, _confirm(client, lid, ids).json()['id'])
    held = _native_target(db, uid, 'p0@acme0.test')
    free = _native_target(db, uid, 'someone@else.test')
    assert claim(db, held, 1, 'approved', recipient='P0@acme0.test') is False
    assert (
        db.execute('SELECT count(*) AS n FROM outreach_campaign_sends WHERE target_id = %s', (held['id'],)).fetchone()[
            'n'
        ]
        == 0
    )
    assert (
        db.execute('SELECT status FROM outreach_campaign_targets WHERE id = %s', (held['id'],)).fetchone()['status']
        == 'active'
    )
    assert claim(db, free, 1, 'approved', recipient='someone@else.test') is True
    # A skip is not an email: only a send that would go out is held back.
    assert claim(db, held, 1, 'skipped', recipient='p0@acme0.test', code='NO_THREAD') is True


def test_a_native_email_goes_again_when_instantly_no_longer_holds_the_person(db, fake):
    from app.outreach.claims import claim

    uid, client, lid, ids = _setup(db, 1)
    _run(db, uid, _confirm(client, lid, ids).json()['id'])
    target = _native_target(db, uid, 'p0@acme0.test')
    db.execute("UPDATE instantly_leads SET outcome = 'failed' WHERE email = 'p0@acme0.test'")
    assert claim(db, target, 1, 'approved', recipient='p0@acme0.test') is True


def test_cancel_ends_a_batch_whose_worker_died_and_frees_the_project(db, fake):
    uid, client, lid, ids = _setup(db, 3)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    db.execute("UPDATE instantly_leads SET outcome = 'publishing' WHERE email = 'p0@acme0.test'")
    out = client.post(f'/v1/integrations/instantly/enrollments/{eid}/cancel').json()
    assert out['status'] == 'cancelled' and out['can_retry'] and not out['can_cancel']
    assert out['counts']['skipped'] == 2 and out['counts']['uncertain'] == 1
    assert fake.add_calls == []
    lid2, ids2 = _list(db, uid, [_person(i + 30) for i in range(2)])
    assert _confirm(client, lid2, ids2).status_code == 202


def test_a_live_batch_with_a_fresh_heartbeat_is_only_asked_to_stop(db, fake):
    _, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now()")
    out = client.post(f'/v1/integrations/instantly/enrollments/{eid}/cancel').json()
    assert out['status'] == 'running' and out['cancel_requested'] and out['counts']['pending'] == 2


def test_retry_skips_people_that_a_later_batch_took_instead_of_failing(db, fake):
    uid, client, lid, ids = _setup(db, 3)
    fake.fail_add = [400]
    first = _confirm(client, lid, ids).json()['id']
    _run(db, uid, first)
    assert set(_outcomes(db, first).values()) == {'failed:INSTANTLY.REJECTED'}
    second = _confirm(client, lid, ids[:2]).json()['id']
    _run(db, uid, second)
    assert set(_outcomes(db, second).values()) == {'accepted'}
    retried = client.post(f'/v1/integrations/instantly/enrollments/{first}/retry')
    assert retried.status_code == 202, retried.text
    _run(db, uid, first)
    assert _outcomes(db, first) == {
        'p0@acme0.test': 'skipped:already_in_instantly',
        'p1@acme1.test': 'skipped:already_in_instantly',
        'p2@acme2.test': 'accepted',
    }


def test_a_batch_without_its_creator_ends_cleanly_and_a_retry_adopts_it(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute('UPDATE instantly_enrollments SET created_by = NULL')
    _run(db, uid, eid)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert view['status'] == 'cancelled' and view['error_code'] is None and fake.add_calls == []
    lid2, ids2 = _list(db, uid, [_person(i + 40) for i in range(2)])
    eid2 = _confirm(client, lid2, ids2).json()['id']
    db.execute("UPDATE instantly_enrollments SET created_by = NULL, status = 'failed' WHERE id = %s", (eid2,))
    assert client.post(f'/v1/integrations/instantly/enrollments/{eid2}/retry').status_code == 202
    assert str(
        db.execute('SELECT created_by FROM instantly_enrollments WHERE id = %s', (eid2,)).fetchone()['created_by']
    ) == str(uid)
    _run(db, uid, eid2)
    assert set(_outcomes(db, eid2).values()) == {'accepted'}


def test_the_address_judged_is_the_address_sent(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute(
        "UPDATE list_rows SET cells = jsonb_set(cells, '{email}', '\"new@acme0.test\"') WHERE id = %s", (ids[0],)
    )
    _run(db, uid, eid)
    assert _outcomes(db, eid) == {'p0@acme0.test': 'skipped:email_changed', 'p1@acme1.test': 'accepted'}
    assert [lead['email'] for call in fake.add_calls for lead in call] == ['p1@acme1.test']


def _retitle(db, row_id, title):
    db.execute(
        "UPDATE list_rows SET cells = jsonb_set(cells, '{title}', to_jsonb(%s::text)) WHERE id = %s", (title, row_id)
    )


def test_a_cell_edit_after_confirm_does_not_change_the_published_lead(db, fake):
    uid, client, lid, ids = _setup(db, 1)
    eid = _confirm(client, lid, ids).json()['id']
    _retitle(db, ids[0], 'CFO')
    _run(db, uid, eid)
    assert [lead['job_title'] for call in fake.add_calls for lead in call] == ['CEO']
    assert _outcomes(db, eid) == {'p0@acme0.test': 'accepted'}


def test_a_lead_confirmed_without_a_payload_is_built_once_and_sent_the_same_again(db, fake):
    uid, client, lid, ids = _setup(db, 1)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute('UPDATE instantly_leads SET payload = NULL WHERE enrollment_id = %s', (eid,))
    _retitle(db, ids[0], 'CTO')
    fake.fail_add = ['timeout']
    _run(db, uid, eid)
    _retitle(db, ids[0], 'COO')
    db.execute("UPDATE instantly_leads SET updated_at = now() - interval '5 minutes'")
    assert client.post(f'/v1/integrations/instantly/enrollments/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    assert [lead['job_title'] for call in fake.add_calls for lead in call] == ['CTO', 'CTO']
    assert fake.add_calls[0] == fake.add_calls[1] and list(fake.leads) == ['p0@acme0.test']
    assert _outcomes(db, eid) == {'p0@acme0.test': 'accepted'}


def _hold_title_while_phone_missing(db, uid, lid, ids) -> str:
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        """ VALUES (%s, 'phone', 'phone', 'code', 0, '{"required": true}')""",
        (lid,),
    )
    db.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (default_organization(%s), %s, 10)',
        (uid, uid),
    )
    hold_id = hold(db, uid)
    row_charges.charge(db, hold_id, None, 'c1', lid, ids, ('title',), 1)
    return hold_id


def test_a_cell_an_unsettled_charge_holds_never_reaches_instantly(db, fake):
    uid, client, lid, ids = _setup(db, 1)
    hold_id = _hold_title_while_phone_missing(db, uid, lid, ids)
    sample = client.post('/v1/integrations/instantly/preview', json=_body(lid, ids)).json()['sample']
    assert 'job_title' not in sample[0]['lead']
    eid = _confirm(client, lid, ids).json()['id']
    assert row_charges.refund_unfinished(db, hold_id)
    _run(db, uid, eid)
    assert [lead.get('job_title') for call in fake.add_calls for lead in call] == [None]
    assert _outcomes(db, eid) == {'p0@acme0.test': 'accepted'}


def test_a_held_cell_is_not_built_into_a_lead_confirmed_without_a_payload(db, fake):
    uid, client, lid, ids = _setup(db, 1)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute('UPDATE instantly_leads SET payload = NULL WHERE enrollment_id = %s', (eid,))
    _hold_title_while_phone_missing(db, uid, lid, ids)
    _run(db, uid, eid)
    assert [lead.get('job_title') for call in fake.add_calls for lead in call] == [None]


def test_disconnect_cancels_a_queued_batch_instead_of_failing_it(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    assert client.delete('/v1/integrations/instantly').status_code == 200
    _run(db, uid, eid)
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert view['status'] == 'cancelled' and view['error_code'] is None and not view['can_retry']
    assert view['counts']['skipped'] == 2


# ── review round 2 ─────────────────────────────────────────────────────────

LATEST = '/v1/integrations/instantly/enrollments/latest'


def test_the_latest_batch_that_needs_a_decision_is_reachable_without_its_id(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    assert client.get(LATEST).json() is None
    eid = _confirm(client, lid, ids).json()['id']
    seen = client.get(LATEST).json()
    assert seen['id'] == eid and seen['status'] == 'queued' and seen['can_cancel']
    # The dialog is gone and the worker died: BUSY says to cancel it, and the latest batch is the one to cancel.
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    lid2, ids2 = _list(db, uid, [_person(i + 50) for i in range(2)])
    assert _confirm(client, lid2, ids2).json()['detail']['code'] == 'INSTANTLY.BUSY'
    assert (
        client.post(f'/v1/integrations/instantly/enrollments/{client.get(LATEST).json()["id"]}/cancel').status_code
        == 200
    )
    assert client.get(LATEST).json() is None
    assert _confirm(client, lid2, ids2).status_code == 202


def test_a_settled_batch_is_not_the_latest_but_a_failed_one_with_people_left_is(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    fake.fail_add = [400]
    eid = _confirm(client, lid, ids).json()['id']
    _run(db, uid, eid)
    assert client.get(LATEST).json()['id'] == eid
    db.execute("UPDATE instantly_leads SET outcome = 'skipped'")
    assert client.get(LATEST).json() is None


def test_a_new_batch_takes_over_the_pending_people_of_a_failed_one(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    fake.fail_add = [429, 429, 429, 429]
    first = _confirm(client, lid, ids).json()['id']
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(instantly_publish, 'MAX_RATE_WAIT_SECONDS', 0)
        _run(db, uid, first)
    assert set(_outcomes(db, first).values()) == {'pending'}
    assert client.get(f'/v1/integrations/instantly/enrollments/{first}').json()['status'] == 'failed'
    second = _confirm(client, lid, ids).json()['id']
    assert set(_outcomes(db, second).values()) == {'pending'}
    assert set(_outcomes(db, first).values()) == {'skipped:superseded'}
    _run(db, uid, second)
    assert set(_outcomes(db, second).values()) == {'accepted'}


def test_people_in_doubt_of_a_cancelled_batch_are_released_by_a_reconcile_only_retry(db, fake):
    uid, client, lid, ids = _setup(db, 3)
    eid = _confirm(client, lid, ids).json()['id']
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    db.execute(
        "UPDATE instantly_leads SET outcome = 'publishing', updated_at = now() - interval '5 minutes'"
        " WHERE email IN ('p0@acme0.test', 'p1@acme1.test')"
    )
    out = client.post(f'/v1/integrations/instantly/enrollments/{eid}/cancel').json()
    assert out['status'] == 'cancelled' and out['counts']['uncertain'] == 2 and out['can_retry']
    assert client.get(LATEST).json()['id'] == eid
    fake.leads['p0@acme0.test'] = 'lead-seen'  # it did reach Instantly; p1 did not
    retried = client.post(f'/v1/integrations/instantly/enrollments/{eid}/retry')
    assert retried.status_code == 202, retried.text
    _run(db, uid, eid)
    assert _outcomes(db, eid) == {
        'p0@acme0.test': 'accepted',
        'p1@acme1.test': 'skipped:cancelled_in_flight',
        'p2@acme2.test': 'skipped:cancelled',
    }
    view = client.get(f'/v1/integrations/instantly/enrollments/{eid}').json()
    assert view['status'] == 'cancelled' and not view['can_retry'] and fake.add_calls == []
    assert client.get(LATEST).json() is None
    campaign = _native_target(db, uid, 'p1@acme1.test')
    assert holds.hold(db, campaign['project_id'], 'email', 'p1@acme1.test', None) is None
    assert holds.hold(db, campaign['project_id'], 'email', 'p0@acme0.test', None) == 'held_by_instantly'


def test_a_worker_that_lost_its_batch_changes_nothing(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    with as_request(member(db, uid)):
        enrollment = instantly_publish.claim(eid)
        chunk = instantly_publish._next_chunk(eid)
        # The user cancelled and the batch ended while this worker held its chunk in memory.
        db.execute("UPDATE instantly_leads SET outcome = 'accepted', instantly_lead_id = 'kept'")
        db.execute("UPDATE instantly_enrollments SET status = 'cancelled' WHERE id = %s", (eid,))
        assert asyncio.run(instantly_publish._judge_chunk(enrollment, chunk)) == []
        instantly_publish._finish(eid, enrollment['run_token'], 'completed')
    assert set(_outcomes(db, eid).values()) == {'accepted'}
    assert (
        db.execute('SELECT status FROM instantly_enrollments WHERE id = %s', (eid,)).fetchone()['status'] == 'cancelled'
    )
    assert fake.add_calls == []


def test_a_second_add_that_instantly_skips_never_overwrites_an_accepted_person(db, fake):
    uid, client, lid, ids = _setup(db, 1)
    eid = _confirm(client, lid, ids).json()['id']
    with as_request(member(db, uid)):
        enrollment = instantly_publish.claim(eid)
        going = instantly_publish._next_chunk(eid)
        db.execute("UPDATE instantly_leads SET outcome = 'accepted', instantly_lead_id = 'kept'")
        fake.blocked.add('p0@acme0.test')
        asyncio.run(instantly_publish._send_chunk(KEY, enrollment, going))
    assert set(_outcomes(db, eid).values()) == {'accepted'}


def test_a_cancelled_batch_in_doubt_stays_reachable_after_a_newer_batch_completes(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    old = _confirm(client, lid, ids).json()['id']
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    db.execute("UPDATE instantly_leads SET outcome = 'publishing' WHERE email = 'p0@acme0.test'")
    client.post(f'/v1/integrations/instantly/enrollments/{old}/cancel')
    lid2, ids2 = _list(db, uid, [_person(i + 60) for i in range(2)])
    newer = _confirm(client, lid2, ids2).json()['id']
    _run(db, uid, newer)
    assert client.get(LATEST).json()['id'] == old


def test_retry_of_a_cancelled_batch_while_another_batch_is_live_is_busy(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    old = _confirm(client, lid, ids).json()['id']
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    db.execute("UPDATE instantly_leads SET outcome = 'publishing', updated_at = now() - interval '5 minutes'")
    client.post(f'/v1/integrations/instantly/enrollments/{old}/cancel')
    lid2, ids2 = _list(db, uid, [_person(i + 70) for i in range(2)])
    assert _confirm(client, lid2, ids2).status_code == 202
    res = client.post(f'/v1/integrations/instantly/enrollments/{old}/retry')
    assert res.status_code == 409 and res.json()['detail']['code'] == 'INSTANTLY.BUSY'


E = '/v1/integrations/instantly/enrollments'


def _stall_cancel(db, client, eid, email='p0@acme0.test'):
    db.execute("UPDATE instantly_enrollments SET status = 'running', touched_at = now() - interval '10 minutes'")
    db.execute(
        "UPDATE instantly_leads SET outcome = 'publishing', updated_at = now() - interval '5 minutes'"
        f" WHERE email = '{email}'"
    )
    return client.post(f'{E}/{eid}/cancel').json()


def test_a_failed_batch_can_be_dismissed_so_an_older_batch_in_doubt_is_reached(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    old = _confirm(client, lid, ids).json()['id']
    assert _stall_cancel(db, client, old)['counts']['uncertain'] == 1
    lid2, ids2 = _list(db, uid, [_person(i + 60) for i in range(2)])
    fake.fail_add = [400, 400]
    newer = _confirm(client, lid2, ids2).json()['id']
    _run(db, uid, newer)
    seen = client.get(LATEST).json()
    assert seen['id'] == newer and seen['can_cancel'] and seen['status'] == 'failed'
    dismissed = client.post(f'{E}/{newer}/cancel').json()
    assert dismissed['status'] == 'cancelled' and not dismissed['can_cancel']
    assert set(_outcomes(db, newer).values()) == {'skipped:cancelled'}
    assert client.get(LATEST).json()['id'] == old
    db.execute("UPDATE instantly_leads SET updated_at = now() - interval '2 minutes'")
    assert client.post(f'{E}/{old}/retry').status_code == 202
    _run(db, uid, old)
    assert client.get(LATEST).json() is None
    assert len(fake.add_calls) == 1  # only the failed batch called add; a reconcile never does


def test_a_cancel_survives_a_failed_reconcile_only_retry(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    fake.fail_add = ['timeout']
    _run(db, uid, eid)  # both uncertain:INSTANTLY.UNKNOWN_OUTCOME, batch failed
    db.execute("UPDATE instantly_leads SET updated_at = now() - interval '2 minutes'")
    assert client.post(f'{E}/{eid}/retry').status_code == 202
    cancelled = client.post(f'{E}/{eid}/cancel').json()  # cancelled while queued
    assert cancelled['status'] == 'cancelled' and cancelled['counts']['uncertain'] == 2
    assert set(_outcomes(db, eid).values()) == {'uncertain:cancelled_in_flight'}
    fake.refuse = 429
    assert client.post(f'{E}/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    assert client.get(f'{E}/{eid}').json()['status'] == 'failed'
    fake.refuse = 0
    assert client.post(f'{E}/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    assert len(fake.add_calls) == 1, 'the people of the cancelled batch were sent'
    assert set(_outcomes(db, eid).values()) == {'skipped:cancelled_in_flight'}
    assert client.get(f'{E}/{eid}').json()['status'] == 'cancelled'


def test_an_old_worker_cannot_end_or_keep_alive_the_run_a_retry_started(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    with as_request(member(db, uid)):
        old = instantly_publish.claim(eid)
    db.execute("UPDATE instantly_enrollments SET touched_at = now() - interval '10 minutes'")
    assert client.post(f'{E}/{eid}/retry').status_code == 202
    with as_request(member(db, uid)):
        new = instantly_publish.claim(eid)
        assert new['run_token'] != old['run_token']
        assert instantly_publish._must_stop(eid, old['run_token'])  # stale: it must stop
        assert not instantly_publish._must_stop(eid, new['run_token'])
        instantly_publish._finish(eid, old['run_token'], 'completed')  # the old worker wakes
    assert client.get(f'{E}/{eid}').json()['status'] == 'running'
    with as_request(member(db, uid)):
        instantly_publish._finish(eid, new['run_token'], 'completed')
    assert client.get(f'{E}/{eid}').json()['status'] == 'completed'


def test_people_in_doubt_are_released_when_their_campaign_was_deleted(db, fake):
    uid, client, lid, ids = _setup(db, 2)
    eid = _confirm(client, lid, ids).json()['id']
    assert _stall_cancel(db, client, eid)['counts']['uncertain'] == 1
    fake.campaign_gone = True
    db.execute("UPDATE instantly_leads SET updated_at = now() - interval '2 minutes'")
    assert client.post(f'{E}/{eid}/retry').status_code == 202
    _run(db, uid, eid)
    assert set(_outcomes(db, eid).values()) == {'skipped:cancelled_in_flight', 'skipped:cancelled'}
    assert client.get(LATEST).json() is None
    target = _native_target(db, uid, 'p0@acme0.test')
    assert holds.hold(db, target['project_id'], 'email', 'p0@acme0.test', None) is None
