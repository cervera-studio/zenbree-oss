"""A request acts in its organization (organization-scoping PR 6): the request's scope reaches the database on every
checkout, a removed or banned member's next call is 401, and the paths that cross organizations on purpose still do.

Everything goes through the real app and the real membership read: the JWT is signed by the test's key
(tests/test_auth.py) or the token step is replaced (``sign_in``), never ``current_user``.
"""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import main
from app.agent import routes as agent_routes
from app.auth import middleware
from app.core import db as core_db
from app.main import app
from tests.conftest import member, project, scoped_conn, sender, sign_in
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db
from tests.test_auth import _FakeJWKS, _token

pytestmark = needs_db
_LIVE_SUBSCRIPTION = main._live_subscription


@pytest.fixture
def tc(db, monkeypatch):
    monkeypatch.setattr(middleware, '_jwks_client', _FakeJWKS())
    monkeypatch.setattr(agent_routes, '_enqueue', lambda job_id: None)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _bearer(user) -> dict:
    return {'Authorization': f'Bearer {_token(sub=user.id, org=user.org_id)}'}


def _probe(monkeypatch, seen: list, *statements: str) -> None:
    """``GET /v1/me`` runs ``statements`` on one more checkout of the request, and keeps the last one's row: the
    route's own reads stay as they are."""

    def live_subscription(org_id):
        with core_db.get_conn() as conn:
            for sql in statements:
                cursor = conn.execute(sql)
            seen.append(cursor.fetchone())
        return _LIVE_SUBSCRIPTION(org_id)

    monkeypatch.setattr(main, '_live_subscription', live_subscription)


# ── The removed member (#308, #389): the next call is 401, whatever the token says ──


@pytest.mark.parametrize('how', ['removed', 'banned'])
def test_a_member_removed_or_banned_since_sign_in_gets_401_on_the_next_call(tc, db, how):
    """Catches: a membership read that accepts everyone (zb.enter_member is the only one a request makes)."""
    ada = member(db, _user(db))
    assert tc.get('/v1/me', headers=_bearer(ada)).status_code == 200

    if how == 'removed':
        db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (ada.id, ada.org_id))
    else:
        db.execute('UPDATE "user" SET banned = true WHERE id = %s', (ada.id,))

    r = tc.get('/v1/me', headers=_bearer(ada))
    assert r.status_code == 401, r.text
    assert r.json()['detail']['code'] == 'AUTH.NOT_A_MEMBER'
    assert r.headers['WWW-Authenticate'] == 'Bearer'


def test_a_member_removed_during_the_request_gets_401_from_the_next_checkout(tc, db, monkeypatch):
    """ActorGone is a BaseException, which no Starlette exception handler sees: the middleware answers instead of
    the server's 500. TestClient re-raises what escapes the app, so a miss fails here."""
    ada = member(db, _user(db))
    real = main._live_subscription

    def removed_then_read(org_id):
        db.execute('DELETE FROM member WHERE "userId" = %s', (ada.id,))
        return real(org_id)

    monkeypatch.setattr(main, '_live_subscription', removed_then_read)
    r = tc.get('/v1/me', headers=_bearer(ada))
    assert r.status_code == 401, r.text
    assert r.json() == {'detail': {'code': 'AUTH.NOT_A_MEMBER', 'message': 'Not a member of this organization'}}


# ── The scope of each checkout (interrogate, Act on 2) ──


@pytest.fixture
def one_connection(db, monkeypatch):
    """The app's pool at size 1, so the next checkout gets the same server session back."""
    monkeypatch.setattr(core_db, 'POOL_SIZE', 1)
    core_db._pool.cache_clear()
    yield
    core_db._pool().close()
    core_db._pool.cache_clear()


def test_a_session_role_or_setting_left_by_one_checkout_never_reaches_the_next(tc, db, monkeypatch, one_connection):
    """Catches: a checkout that skips SET LOCAL ROLE zenbree_app, so a session-level RESET ROLE or set_config left
    on the pooled connection decides who the next request is."""
    ada, bo = member(db, _user(db)), member(db, _user(db))
    project(db, bo.id)
    seen: list = []
    _probe(
        monkeypatch,
        seen,
        'RESET ROLE',
        f"SELECT set_config('zb.org_id', '{bo.org_id}', false), pg_backend_pid() AS pid",
    )
    assert tc.get('/v1/me', headers=_bearer(ada)).status_code == 200

    _probe(
        monkeypatch,
        seen,
        'SELECT pg_backend_pid() AS pid, current_user AS role, zb.org()::text AS org,'
        f" (SELECT count(*) FROM projects WHERE organization_id = '{bo.org_id}') AS foreign_projects",
    )
    assert tc.get('/v1/me', headers=_bearer(ada)).status_code == 200

    left, now = seen[0], seen[-1]
    assert now['pid'] == left['pid']  # the same server session came back
    assert (now['role'], now['org'], now['foreign_projects']) == ('zenbree_app', ada.org_id, 0)


def test_a_scope_cannot_change_while_a_connection_is_open(db):
    from app.core.scope import ScopeChangeWhileOpen, acting_as, system
    from app.core.system_reasons import SystemReason

    ada = member(db, _user(db))
    with acting_as(ada.org_id, ada.id):
        with core_db.get_conn(), core_db.get_conn():  # nested, as helpers do
            pass
        with core_db.get_conn(), pytest.raises(ScopeChangeWhileOpen), system(SystemReason.BACK_OFFICE):
            pytest.fail('entered System inside a Member checkout')
        with system(SystemReason.BACK_OFFICE), core_db.get_conn() as conn:  # closed first: fine
            assert conn.execute('SELECT current_user AS r').fetchone()['r'] == 'zenbree_system'


def test_a_test_connection_runs_the_requests_sql_under_its_scope(db):
    """``scoped_conn``, which the route tests that hand the app their own connection use: inside a scope its
    checkout runs as the pooled one does; outside, it raises as the pool does."""
    from app.core.scope import ScopeChangeWhileOpen, ScopeMissing, acting_as, system
    from app.core.system_reasons import SystemReason

    ada, bo = member(db, _user(db)), member(db, _user(db))
    get_conn = scoped_conn(db)
    read = 'SELECT current_user AS role, zb.org()::text AS org, (SELECT count(*) FROM projects) AS projects'
    for who in (ada, bo):
        project(db, who.id)
    with acting_as(ada.org_id, ada.id), get_conn() as conn:
        assert conn.execute(read).fetchone() == {'role': 'zenbree_app', 'org': ada.org_id, 'projects': 1}
        with pytest.raises(ScopeChangeWhileOpen), system(SystemReason.BACK_OFFICE):
            pytest.fail('entered System inside a Member checkout')
    with pytest.raises(ScopeMissing), get_conn():
        pytest.fail('a checkout with no scope')


# ── Stale jobs (interrogate, Act on 6; review round 1, B1) ──


def _job_in(db, org: str, user: str, status: str, idle: str) -> str:
    """A job of ``user`` in ``org`` on a chat of its own, last updated ``idle`` ago."""
    chat = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (%s,'
        ' (SELECT id FROM projects WHERE organization_id = %s LIMIT 1), %s) RETURNING id',
        (org, org, user),
    ).fetchone()['id']
    return str(
        db.execute(
            'INSERT INTO jobs (organization_id, user_id, chat_session_id, status, updated_at)'
            ' VALUES (%s, %s, %s, %s, now() - %s::interval) RETURNING id',
            (org, user, chat, status, idle),
        ).fetchone()['id']
    )


def _status(db, job: str) -> tuple[str, str | None]:
    row = db.execute('SELECT status, error FROM jobs WHERE id = %s', (job,)).fetchone()
    return row['status'], row['error']


def test_a_turn_fails_its_users_dead_job_in_another_organization_and_no_one_elses(tc, db):
    """``uq_jobs_one_active_per_user`` spans organizations, and a turn in A cannot see B's jobs, so its own sweep
    leaves Ada's dead job in B in place: the 409 sweeps her jobs in every organization, as System, and the turn
    starts. Catches: a sweep with no retry (409), and one that is not limited to the caller (Bo's job fails)."""
    ada, bo = _two_orgs(db)
    project(db, bo.id)
    hers = _job_in(db, bo.org_id, ada.id, 'running', '1 hour')
    his = _job_in(db, bo.org_id, bo.id, 'queued', '1 hour')
    sign_in(app, ada)
    r = tc.post('/v1/runs', json={'message': 'Find agencies'})
    assert r.status_code == 202, r.text
    assert _status(db, hers) == ('failed', 'stale')
    assert _status(db, his) == ('queued', None)


def test_a_young_job_in_another_organization_still_refuses_the_turn(tc, db):
    ada, bo = _two_orgs(db)
    project(db, bo.id)
    hers = _job_in(db, bo.org_id, ada.id, 'queued', '5 minutes')
    sign_in(app, ada)
    r = tc.post('/v1/runs', json={'message': 'Find agencies'})
    assert r.status_code == 409, r.text
    assert r.json()['detail']['code'] == 'CHAT.JOB_RUNNING'
    assert _status(db, hers) == ('queued', None)


async def test_a_turn_that_waits_in_the_queue_past_the_stale_bound_still_runs(db):
    """No timer fails a queued job, as on main: a turn that waits in a worker backlog for longer than
    STALE_JOB_MINUTES runs when a worker takes it (review round 1, B1)."""
    from tests.test_agent import _job, _run

    uid = _user(db)
    _, jid = _job(db, uid, 'Find agencies')
    db.execute("UPDATE jobs SET updated_at = now() - interval '25 minutes' WHERE id = %s", (jid,))
    await _run(jid, [('Done.', [])])
    assert _status(db, jid) == ('succeeded', None)


def test_only_a_new_turn_runs_the_stale_job_sweep():
    """Catches a beat task or any other caller of the sweep: run on a timer, it fails a turn still in the queue."""
    app_dir = Path(core_db.__file__).resolve().parents[1]
    sweepers = sorted(
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob('*.py')
        if 'FAIL_STALE_JOBS_SQL' in path.read_text() and path.name != 'jobs.py'
    )
    assert sweepers == ['agent/routes.py', 'agent/turn_start.py'], f'the sweep runs outside a new turn: {sweepers}'


# ── The routes that cross organizations on purpose ──


def _two_orgs(db):
    """Ada, an admin of her own organization A and a member of Bo's B."""
    ada, bo = member(db, _user(db)), member(db, _user(db))
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (bo.org_id, ada.id),
    )
    return ada, bo


def _a_reply_in_each(db, ada, bo) -> dict[str, str]:
    """A reply to Ada in A and one in B, each with its notification: the notification ids by organization."""
    ids = {}
    for org, owner in ((ada.org_id, ada.id), (bo.org_id, bo.id)):
        message = db.execute(
            'INSERT INTO outreach_inbound_messages (organization_id, owner_id, project_id, channel, kind,'
            " provider_message_id, body) VALUES (%s, %s, %s, 'email', 'reply', %s, %s) RETURNING id::text",
            (org, ada.id, project(db, owner), uuid.uuid4().hex, f'Reply in {org}'),
        ).fetchone()['id']
        ids[org] = db.execute(
            "INSERT INTO notifications (user_id, organization_id, kind, href, data) VALUES (%s, %s, 'reply', '/', %s)"
            ' RETURNING id::text',
            (ada.id, org, Jsonb({'message_id': message})),
        ).fetchone()['id']
    return ids


def test_the_bell_lists_and_marks_read_the_users_notifications_of_every_organization_they_belong_to(tc, db):
    """Each reply keeps its quote: row-level security shows a message only in its own organization."""
    ada, bo = _two_orgs(db)
    ids = _a_reply_in_each(db, ada, bo)
    sign_in(app, ada)
    listed = tc.get('/v1/notifications').json()
    assert {i['id']: i.get('quote') for i in listed['items']} == {
        ids[org]: f'Reply in {org}' for org in (ada.org_id, bo.org_id)
    }
    assert listed['unread'] == 2
    assert tc.post('/v1/notifications/read', json={'ids': [ids[bo.org_id]]}).json()['updated'] == 1
    assert tc.get('/v1/notifications').json()['unread'] == 1


def test_a_member_who_leaves_another_organization_while_the_bell_loads_gets_its_items_with_no_quote(
    tc, db, monkeypatch
):
    """The list is read in A; B's quote is read afterwards as Ada in B, which she has just left: that item comes
    back with no quote, and the request still answers."""
    from app.notifications import routes as bell

    ada, bo = _two_orgs(db)
    ids = _a_reply_in_each(db, ada, bo)
    own_quotes = bell.with_quotes

    def then_leave_b(conn, user_id, items):
        own_quotes(conn, user_id, items)
        db.execute('DELETE FROM member WHERE "organizationId" = %s AND "userId" = %s', (bo.org_id, ada.id))

    monkeypatch.setattr(bell, 'with_quotes', then_leave_b)
    sign_in(app, ada)
    r = tc.get('/v1/notifications')
    assert r.status_code == 200, r.text
    assert {i['id']: i.get('quote') for i in r.json()['items']} == {
        ids[ada.org_id]: f'Reply in {ada.org_id}',
        ids[bo.org_id]: None,
    }


def test_a_browser_subscribed_by_someone_in_another_organization_is_taken_over_with_its_secret(tc, db, monkeypatch):
    from app.notifications import push

    monkeypatch.setattr(push, 'enabled', lambda: True)
    ada, bo = member(db, _user(db)), member(db, _user(db))
    endpoint = 'https://fcm.googleapis.com/fcm/send/' + uuid.uuid4().hex
    db.execute(
        "INSERT INTO push_subscriptions (endpoint, user_id, p256dh, auth) VALUES (%s, %s, 'key', 'secret')",
        (endpoint, bo.id),
    )
    sign_in(app, ada)
    body = {'endpoint': endpoint, 'p256dh': 'key2', 'auth': 'other'}
    assert tc.put('/v1/notifications/push', json=body).json()['detail']['code'] == 'NOTIFICATIONS.PUSH_TAKEN'
    assert tc.put('/v1/notifications/push', json={**body, 'auth': 'secret'}).json() == {'ok': True}
    owner = db.execute('SELECT user_id::text AS u FROM push_subscriptions WHERE endpoint = %s', (endpoint,)).fetchone()
    assert owner['u'] == ada.id


def test_an_account_deleted_by_its_owner_releases_its_senders_in_every_organization(tc, db):
    ada, bo = _two_orgs(db)
    own = sender(db, ada.id)
    db.execute('INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s)', (bo.org_id, bo.id, 'B'))
    theirs = db.execute(
        'INSERT INTO user_oauth_connections (organization_id, user_id, provider, refresh_token_encrypted)'
        " VALUES (%s, %s, 'google', 'x') RETURNING id::text",
        (bo.org_id, ada.id),
    ).fetchone()['id']
    sign_in(app, ada)
    r = tc.post('/v1/me/before-delete', json={'user_id': ada.id})
    assert r.status_code == 200, r.text
    assert r.json()['released'] == 2
    rows = db.execute(
        'SELECT id::text, status FROM user_oauth_connections WHERE id = ANY(%s::uuid[])', ([own, theirs],)
    ).fetchall()
    assert {r['id']: r['status'] for r in rows} == {own: 'disconnected', theirs: 'disconnected'}
