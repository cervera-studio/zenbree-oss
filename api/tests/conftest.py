import contextlib
import functools
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

import fakeredis
import fakeredis.aioredis
import fastapi.routing
import httpx
import psycopg
import pytest
import structlog
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic_core import to_jsonable_python

from app.admin.settings import invalidate
from app.agent import events, loop
from app.agent.event_payloads import check_payload
from app.auth.middleware import CurrentUser
from app.core import db as core_db
from app.core import migrate as migrate_module
from app.core import scope as scopes
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.migrate import MIGRATIONS, migrate
from app.core.system_reasons import SystemReason
from tests import connect_retry

connect_retry.install()
# Before the guard below snapshots it: with structlog unconfigured the guard had nothing to compare, so a test
# that reconfigured it passed when its file ran alone.
configure_logging()


class FakeConn:
    """Stands in for app.core.db.get_conn: records every statement, returns nothing."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return None


@pytest.fixture(autouse=True)
def vendor_db(monkeypatch):
    """No test reaches a real DB through paid_call; the rows it would write land here."""
    fake = FakeConn()
    monkeypatch.setattr('app.billing.paid_call.get_conn', lambda timeout=None: contextlib.nullcontext(fake))
    return fake


COPY_PREFIX = 'zbtest_'


@functools.cache
def _template() -> str:
    """The test database server's copy of api/db fully migrated, built once per migration content and shared by every
    run. Migrating a new schema per test cost about 0.3 s; a copy of this database costs about 0.07 s."""
    url = os.environ['TEST_DATABASE_URL']
    # The runner and the file names are inputs too: the same SQL under another runner or order is another schema.
    inputs = [Path(migrate_module.__file__), *sorted(MIGRATIONS.glob('*.sql'))]
    digest = hashlib.sha256(b''.join(p.name.encode() + p.read_bytes() for p in inputs)).hexdigest()[:16]
    name = f'{COPY_PREFIX}template_{digest}'
    with psycopg.connect(url, autocommit=True) as admin:
        if admin.execute('SELECT 1 FROM pg_database WHERE datname = %s', (name,)).fetchone():
            return name
        # Built under a private name and renamed, so a parallel run never copies a half-migrated template.
        building = f'{COPY_PREFIX}building_{uuid.uuid4().hex[:12]}'
        admin.execute(f'CREATE DATABASE {building}')
        try:
            with psycopg.connect(make_conninfo(url, dbname=building), autocommit=True) as c:
                migrate(c)
            admin.execute(f'ALTER DATABASE {building} RENAME TO {name}')
        # Two renames at the same moment collide in the catalog's unique index instead of the name check.
        except psycopg.errors.DuplicateDatabase, psycopg.errors.UniqueViolation:
            admin.execute(f'DROP DATABASE {building}')
        except BaseException:
            admin.execute(f'DROP DATABASE {building} WITH (FORCE)')
            raise
    return name


# A drop costs about as much as a copy and grows with parallel workers, so it runs beside the next tests.
_drops = ThreadPoolExecutor(max_workers=1)


def _drop(name: str) -> None:
    with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
        admin.execute(f'DROP DATABASE {name} WITH (FORCE)')


def pytest_sessionfinish():
    _drops.shutdown(wait=True)


@contextlib.contextmanager
def migrated_database() -> Iterator[tuple[psycopg.Connection, str]]:
    """A new database copied from the migrated template: an autocommit connection to it, and its conninfo for any
    other connection the test opens. Dropped after exit."""
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL not set: SQL tests NOT run')
    name = f'{COPY_PREFIX}{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url, autocommit=True) as admin:
        # The default WAL_LOG strategy writes each block through shared buffers and the WAL, so parallel workers
        # wait on each other to extend files. FILE_COPY copies the files. Its two checkpoints per copy are cheap only
        # because the test server runs with fsync=off (harness/testdb.sh).
        admin.execute(f'CREATE DATABASE {name} TEMPLATE {_template()} STRATEGY FILE_COPY')
    conninfo = make_conninfo(url, dbname=name)
    try:
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as c:
            yield c, conninfo
    finally:
        _drops.submit(_drop, name)


@contextlib.contextmanager
def empty_database(row_factory=dict_row) -> Iterator[psycopg.Connection]:
    """A new empty database, for a test that runs the migrations itself: an autocommit connection to it. Dropped
    after exit. A schema of the shared database is not enough: a migration creates objects of the whole database
    (the zb schema, an event trigger), so a second schema could not run it again."""
    url = os.environ['TEST_DATABASE_URL']
    name = f'{COPY_PREFIX}{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE {name}')
    try:
        with psycopg.connect(make_conninfo(url, dbname=name), autocommit=True, row_factory=row_factory) as c:
            yield c
    finally:
        _drops.submit(_drop, name)


@contextlib.contextmanager
def pool_on(conninfo: str) -> Iterator[None]:
    """The app's pool on the test's database, for a test that patches each module's ``get_conn`` with its own
    connection: the membership read of every request still takes a pooled connection (app/core/scope.py)."""
    old = settings.database_url
    # A pooled checkout that waits on a row the request's scoped_conn transaction holds fails the test, never hangs it.
    settings.database_url = make_conninfo(conninfo, options='-c lock_timeout=5s')
    core_db._pool.cache_clear()
    try:
        yield
    finally:
        core_db._pool().close()
        core_db._pool.cache_clear()
        settings.database_url = old


def scoped_conn(conn: psycopg.Connection) -> Callable[..., contextlib.AbstractContextManager]:
    """``get_conn`` for a test that hands the app its own connection (a database copy, autocommit, as the login
    role). Inside a scope, each checkout is one transaction that enters it as a pooled checkout does
    (``core_db.entered``: ``SET LOCAL ROLE``, then ``zb.enter_*``), so the route's SQL runs under row-level security.
    With no scope it raises ``ScopeMissing``, as the pool does: the test's own setup uses ``conn`` itself.
    ``current_user`` reads the membership on a pooled connection: pair this with ``pool_on``."""

    @contextlib.contextmanager
    def get_conn(timeout: float | None = None) -> Iterator[psycopg.Connection]:
        scope = scopes.current()
        # SET LOCAL lasts until this transaction ends; on an autocommit connection outside one it does nothing.
        with conn.transaction(), core_db.entered(conn, scope):
            yield conn

    return get_conn


_ENTERED = contextlib.ExitStack()


def act_as(org_id, user_id) -> None:
    """The rest of this test runs as ``Member(org_id, user_id)``: what a request does once ``current_user`` entered
    the scope, or a member task once the worker read its header. A later call switches to another member; a block
    that the app runs in another scope (a beat, a webhook) enters it with ``acting_as``, ``acting_for`` or
    ``system``. The test leaves every scope it entered when it ends (``_scope_ends_with_the_test``)."""
    _ENTERED.enter_context(scopes.acting_as(str(org_id), str(user_id)))


def act_in_own_org(conn, user_id) -> None:
    """``act_as`` the user in their own organization (#305), as most tests' users are."""
    user = member(conn, user_id)
    act_as(user.org_id, user.id)


def start_turn(user: CurrentUser, request, *args):
    """``POST /v1/chat`` of ``user``, in the scope the request runs in."""
    from app.agent.routes import _start_turn

    return call_as(user, _start_turn, user, request, *args)


def as_request(user: CurrentUser) -> contextlib.AbstractContextManager:
    """The scope a request of ``user`` runs in once ``current_user`` entered it: for a test that calls a route
    function or a tool handler itself."""
    return scopes.acting_as(user.org_id, user.id)


def call_as[T](user: CurrentUser, fn: Callable[..., T], /, *args, **kwargs) -> T:
    """``fn(*args, **kwargs)`` in the scope of a request of ``user``."""
    with as_request(user):
        return fn(*args, **kwargs)


def call_for[T](org_id, fn: Callable[..., T], /, *args, **kwargs) -> T:
    """``fn(*args, **kwargs)`` as ``ForOrg(org_id)``: an organization task (app/core/tasks.py ``org_task``)."""
    with scopes.acting_for(str(org_id), 'test'):
        return fn(*args, **kwargs)


def call_system[T](reason: SystemReason, fn: Callable[..., T], /, *args, **kwargs) -> T:
    """``fn(*args, **kwargs)`` as ``System(reason)``: the entry point that has the reason (a webhook, a public
    link, a beat) runs it so."""
    with scopes.system(reason):
        return fn(*args, **kwargs)


def act_for(org_id, why: str = 'test') -> None:
    """``act_as`` for ``ForOrg(org_id)``: the organization's own work, as the campaign beat runs each organization
    or an organization task runs."""
    _ENTERED.enter_context(scopes.acting_for(str(org_id), why))


def campaign_orgs(conn) -> list[str]:
    rows = conn.execute('SELECT DISTINCT organization_id::text AS id FROM outreach_campaigns ORDER BY 1')
    return [r['id'] for r in rows]


def _summed(results: list):
    return sum(results[1:], results[0]) if results else Counter()


def each_org(conn, step: Callable, *args):
    """``step(*args)`` once for each organization with a campaign, as ``ForOrg`` of it, the results summed: the
    campaign beat runs every organization on its own (app/outreach/campaign_beat.py)."""
    results = []
    for org in campaign_orgs(conn):
        with scopes.acting_for(org, 'campaign beat'):
            results.append(step(*args))
    return _summed(results)


async def each_org_async(conn, step: Callable, *args):
    """``each_org`` for a coroutine function."""
    results = []
    for org in campaign_orgs(conn):
        with scopes.acting_for(org, 'campaign beat'):
            results.append(await step(*args))
    return _summed(results)


@pytest.fixture(autouse=True)
def _scope_ends_with_the_test():
    """Leaves what ``act_as`` entered, so no scope reaches the next test. Enters none."""
    yield
    _ENTERED.close()


def conninfo_of(conn: psycopg.Connection) -> str:
    """The conninfo of the database that conn is on, for a second connection to it."""
    return make_conninfo(os.environ['TEST_DATABASE_URL'], dbname=conn.info.dbname)


def wait_for_a_lock(conninfo: str, what: str, pid: int | None = None, unless: threading.Thread | None = None) -> None:
    """Until the backend ``pid``, or any backend of the database, waits on a lock, or the ``unless`` thread ended
    without a wait. A barrier for a race test: a fixed sleep lets a loaded host run the next step before the other
    side reaches the lock, and the test passes untested."""
    deadline = time.monotonic() + 10
    with psycopg.connect(conninfo) as watch:
        while (unless is None or unless.is_alive()) and not watch.execute(
            "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ' AND (%(pid)s::int IS NULL OR pid = %(pid)s)',
            {'pid': pid},
        ).fetchone():
            assert time.monotonic() < deadline, f'{what} never waited on a lock'
            time.sleep(0.01)


def _is_test_database(conninfo: str) -> bool:
    test = os.environ.get('TEST_DATABASE_URL')
    if not test:
        return False
    target, expected = conninfo_to_dict(conninfo), conninfo_to_dict(test)
    dbname = str(target.get('dbname'))
    return all(target.get(k) == expected.get(k) for k in ('host', 'port')) and (
        dbname == expected.get('dbname') or dbname.startswith(COPY_PREFIX)
    )


@pytest.fixture(autouse=True)
def stray_pool(monkeypatch):
    """A test reaches the process pool only after it points settings.database_url at the test database. Otherwise a
    get_conn the test forgot to patch waits 30 s for a pool at localhost:5432, and a caller that logs and goes on,
    such as notifications.service.deliver, passes the test with that path never run."""
    real = core_db._pool
    strays: list[str] = []

    def pool():
        if _is_test_database(settings.database_url):
            return real()
        frames = traceback.extract_stack()[:-1]
        caller = next((f for f in reversed(frames) if '/app/' in f.filename and 'core/db.py' not in f.filename), None)
        strays.append(f'{caller.filename}:{caller.lineno}' if caller else 'unknown caller')
        raise RuntimeError('the test did not patch get_conn for this caller')

    pool.cache_clear = real.cache_clear  # test_agent resets the real pool through it
    monkeypatch.setattr(core_db, '_pool', pool)
    yield
    assert not strays, f'reached the real database pool, patch get_conn in: {strays}'


@pytest.fixture(autouse=True)
def no_result_backend():
    """Sending and running a task touch the result backend, Redis: with none listening it retries for 20 s, then fails."""
    from celery.backends.base import DisabledBackend

    from app.worker import celery_app as app

    before = app._backend_cache, getattr(app._local, 'backend', None)
    app._backend = DisabledBackend(app)
    yield
    app._backend_cache = before[0]
    app._local.backend = before[1]


@pytest.fixture(autouse=True)
def event_redis(monkeypatch):
    """No test reaches a real Redis through the event bus: both clients share one in-memory server."""
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(events, 'redis_sync', lambda: sync)
    monkeypatch.setattr(
        events, 'redis_async', lambda: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    return sync


@pytest.fixture(autouse=True)
def chat_payloads_match_models(monkeypatch):
    """Every chat frame a test makes the agent send is checked against its model in api/app/agent/event_payloads.py,
    so a payload key the loop renames fails the test that sends it, not only the web."""
    turn_init = loop.Turn.__init__
    frame = events.frame

    def checked(emit):
        def emit_checked(event, data):
            check_payload(event, data)
            return emit(event, data)

        return emit_checked

    def turn_checked(self, job, provider, emit):
        turn_init(self, job, provider, checked(emit))

    def frame_checked(event, data, event_id=None):
        # only the chat routes' own frames: other streams (a list fill's `error`) share event names
        if sys._getframe(1).f_globals['__name__'] == 'app.agent.routes':
            check_payload(event, data)
        return frame(event, data, event_id)

    monkeypatch.setattr(loop.Turn, '__init__', turn_checked)
    monkeypatch.setattr(events, 'frame', frame_checked)


@pytest.fixture(autouse=True)
def balance_events(monkeypatch):
    """No test reaches the Celery broker through app.billing.balance: the queued (user id, refused) land here."""
    from app.worker import on_balance_changed

    queued: list[tuple[str, bool]] = []
    monkeypatch.setattr(on_balance_changed, 'apply_async', lambda args, **kw: queued.append(args))
    return queued


@pytest.fixture(autouse=True)
def webhook_events(monkeypatch):
    """No test reaches the Celery broker through app.integrations.webhooks.emit: every event has one endpoint
    subscribed to it, and the queued (organization id, payload) land here. Which organization an event resolves to
    is tested against SQL in test_webhooks."""
    from app.integrations import webhooks
    from app.worker import deliver_webhooks

    queued: list[tuple[str, dict]] = []
    monkeypatch.setattr(webhooks, '_subscribed', lambda event, data: [('org', 'endpoint')])
    monkeypatch.setattr(deliver_webhooks, 'delay', lambda org_id, _endpoint, payload: queued.append((org_id, payload)))
    return queued


@pytest.fixture(autouse=True)
def kill_switch(monkeypatch):
    """``settings`` stand-in for the per-path kill switch: add 'vendor_disabled:<x>' to block x."""
    disabled: set[str] = set()

    class Settings(FakeConn):
        def execute(self, sql, params=()):
            self.key = params[0] if params else None
            return self

        def fetchone(self):
            return {'value': True} if self.key in disabled else None

        def __iter__(self):
            return iter(())  # the cached full read (`is_disabled`) sees no row, so a switch never leaks across tests

    monkeypatch.setattr('app.admin.settings.get_conn', lambda: contextlib.nullcontext(Settings()))
    invalidate()
    yield disabled
    invalidate()


@pytest.fixture(autouse=True)
def lossless_response_models(monkeypatch):
    original = fastapi.routing.serialize_response

    async def serialize(**kwargs):
        content = await original(**kwargs)
        if kwargs.get('field') is not None:
            # Compared as text, since in Python 0 == 0.0 and 1 == True while the JSON differs.
            sent = json.dumps(json.loads(content) if isinstance(content, bytes) else content, sort_keys=True)
            raw = json.dumps(to_jsonable_python(kwargs['response_content'], by_alias=True), sort_keys=True)
            endpoint = (kwargs.get('endpoint_ctx') or {}).get('function')
            assert sent == raw, f'{endpoint}: the response model changed the JSON\nsent: {sent}\nraw:  {raw}'
        return content

    monkeypatch.setattr(fastapi.routing, 'serialize_response', serialize)


@pytest.fixture
def http(monkeypatch):
    """Route every httpx call (sync and async) to ``http.handler``; ``http.calls`` records requests."""

    class Router:
        calls: ClassVar[list[httpx.Request]] = []
        handler = staticmethod(lambda request: httpx.Response(404))

    def _handle(request: httpx.Request) -> httpx.Response:
        Router.calls.append(request)
        return Router.handler(request)

    transport = httpx.MockTransport(_handle)
    real_async, real_sync = httpx.AsyncClient, httpx.Client

    def async_client(*args, **kwargs):
        kwargs['transport'] = transport
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', async_client)
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: real_sync(transport=transport).post(url, **kw))
    Router.calls = []
    return Router


def body(request: httpx.Request) -> dict:
    return json.loads(request.content or b'{}')


def member(conn, user_id) -> CurrentUser:
    """The user as the API sees them: in their own organization (#305), created on first use, as its admin."""
    org = conn.execute('SELECT default_organization(%s) AS id', (user_id,)).fetchone()['id']
    role = conn.execute(
        'SELECT role FROM member WHERE "userId" = %s AND "organizationId" = %s', (user_id, org)
    ).fetchone()
    return CurrentUser(id=str(user_id), org_id=str(org), org_role=role['role'] if role else None)


def give_role(conn, user: CurrentUser) -> CurrentUser:
    """Write ``user.org_role`` as their role in ``user.org_id``, where ``current_user`` reads it: for ``sign_in``."""
    written = conn.execute(
        'UPDATE member SET role = %s WHERE "userId" = %s AND "organizationId" = %s',
        (user.org_role, user.id, user.org_id),
    ).rowcount
    assert written == 1, f'{user.id} is not a member of {user.org_id}: insert the member row first'
    return user


def sign_in(app, user: CurrentUser) -> None:
    """``user`` calls the next requests: the token step is replaced, and ``current_user`` still enters the scope
    and reads the membership in the database, as in production. Override this, never ``current_user``
    (tests/test_scope_entry_points.py). ``user.org_role`` is ignored: the database's ``member.role`` decides, so
    a test that needs another role writes it there. ``require_admin`` also needs ``"user".role`` to name admin."""
    from app.auth.middleware import Principal, authenticate, authenticate_or_api_key

    principal = Principal(user_id=user.id, org_id=user.org_id, role=user.role, email=user.email)
    app.dependency_overrides[authenticate] = lambda: principal
    app.dependency_overrides[authenticate_or_api_key] = lambda: principal


def sign_out(app) -> None:
    """The next requests carry no token again."""
    from app.auth.middleware import authenticate, authenticate_or_api_key

    app.dependency_overrides.pop(authenticate, None)
    app.dependency_overrides.pop(authenticate_or_api_key, None)


def no_membership_read(database_url: str) -> None:
    """For a fake of the membership read: a test with a database reads it there (``sign_in``), so the route's SQL
    runs under the scope. Called when the request enters, since that is when the test's pool is set."""
    has_database = _is_test_database(database_url)  # a name of its own, so a failure never prints the conninfo
    assert not has_database, (
        'this test has a database: use tests.conftest.sign_in, which reads the membership there, not a fake of it'
    )


@pytest.fixture
def sign_in_without_db(monkeypatch) -> Callable[[CurrentUser], None]:
    """``sign_in`` for a test with no database: ``current_user`` takes the role from ``user.org_role`` instead of
    reading the membership, and enters no scope. Safe only where the test fakes every ``get_conn`` the route reaches:
    with no scope and no test database, a call to the real pool still fails the test (``stray_pool``). The
    membership read itself is proven in tests/test_scope_request_sql.py."""
    from app.auth import middleware
    from app.main import app

    roles: dict[tuple[str, str], str | None] = {}

    async def enter_member(org: str, user: str) -> str | None:
        no_membership_read(settings.database_url)
        return roles[org, user]

    monkeypatch.setattr(middleware, 'enter_member', enter_member)

    def sign(user: CurrentUser) -> None:
        roles[user.org_id, user.id] = user.org_role
        sign_in(app, user)

    return sign


@pytest.fixture(autouse=True)
def list_limits_count_one(monkeypatch) -> None:
    """No test reaches a real Redis through the list limits: every hit counts 1. A test that counts patches
    ``app.core.rate_limit._redis`` itself, after this."""

    class _Counter:
        def eval(self, *args) -> int:
            return 1

        def set(self, *args, **kw) -> bool:
            return True

        def delete(self, *args) -> int:
            return 1

    monkeypatch.setattr('app.core.rate_limit._redis', _Counter)


@pytest.fixture
def no_list_read_limit(monkeypatch) -> None:
    """The list reads' rate limit always counts 1. Not an override of ``limit_list_reads``: the routes reach
    ``current_user_or_api_key`` through it too, and an override would take that entry out of their tree."""

    class _Counter:
        def eval(self, *args) -> int:
            return 1

    monkeypatch.setattr('app.core.rate_limit._redis', _Counter)


def project(conn, user_id) -> str:
    """The default project (client, #307) of the user's own organization, created on first use."""
    from app.projects.routes import default_project

    return default_project(conn, member(conn, user_id))


def hold(conn, user_id, job_id=None, amount=10) -> str:
    """A reserved credit hold on the user's organization; with ``job_id``, that job's hold, as a chat turn takes it."""
    rid = conn.execute(
        "SELECT reserve_credits(default_organization(%s), %s, %s, 'job', %s::text) AS r",
        (user_id, user_id, amount, job_id),
    ).fetchone()['r']
    if job_id:
        conn.execute('UPDATE jobs SET reservation_id = %s, credits_reserved = %s WHERE id = %s', (rid, amount, job_id))
    return str(rid)


def sender(conn, user_id, provider='google', *, projects=None, **cols) -> str:
    """A sender (#307): one physical row of the user's organization, linked to ``projects`` (default: the user's
    default project)."""
    org = member(conn, user_id).org_id
    row = {'organization_id': org, 'user_id': user_id, 'provider': provider, 'refresh_token_encrypted': 'x', **cols}
    sid = conn.execute(
        f'INSERT INTO user_oauth_connections ({", ".join(row)}) VALUES ({", ".join(["%s"] * len(row))}) RETURNING id',
        list(row.values()),
    ).fetchone()['id']
    for pid in projects or [project(conn, user_id)]:
        link(conn, pid, sid)
    return str(sid)


def link(conn, project_id, sender_id) -> None:
    """Link a sender to one more project of its organization."""
    conn.execute(
        'INSERT INTO project_senders (project_id, sender_id, provider, organization_id)'
        ' SELECT %s, id, provider, organization_id FROM user_oauth_connections WHERE id = %s',
        (project_id, sender_id),
    )


def pool(conn, campaign_id=None, providers=('google', 'microsoft')) -> None:
    conn.execute(
        """INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
           SELECT c.id, ps.sender_id, ps.project_id, ps.provider, ps.organization_id
           FROM outreach_campaigns c JOIN project_senders ps ON ps.project_id = c.project_id
           WHERE ps.provider = ANY(%(p)s) AND (%(c)s::uuid IS NULL OR c.id = %(c)s::uuid)
           ON CONFLICT DO NOTHING""",
        {'c': campaign_id, 'p': list(providers)},
    )


@pytest.fixture
def keys(monkeypatch):
    for name in ('apollo_api_key', 'serper_api_key', 'unipile_api_key'):
        monkeypatch.setattr(settings, name, 'test-key')
    monkeypatch.setattr(settings, 'unipile_dsn', 'https://unipile.test')
    monkeypatch.setattr(settings, 'treg_base_url', 'https://treg.test')
    for name in ('treg_api_key', 'aiark_api_key', 'millionverifier_api_key'):
        monkeypatch.setattr(settings, name, '')


@pytest.fixture
def inline_worker(monkeypatch):
    """The CSV import task runs where it is queued, so a test reads the finished import."""
    from app.lists.csv_run import run
    from app.worker import run_csv_import

    monkeypatch.setattr(run_csv_import, 'delay', run)


def uploaded(tc, url: str, content, headers=None) -> dict:
    """POST a CSV to ``/v1/lists/upload`` (needs ``inline_worker``) and return the finished import: ``list_id`` and counts."""
    res = tc.post(url, content=content, headers=headers)
    assert res.status_code == 202, res.text
    done = tc.get(f'/v1/lists/imports/{res.json()["id"]}', headers=headers).json()
    assert done['status'] == 'done', done
    return done


@pytest.fixture
def app_logging():
    """The app's structlog configuration, for a test that reads what the app logs.

    ``configure_logging`` sets it once for the process. Never call ``structlog.configure`` with new processors in a
    test: the app caches each logger on first use with the list of that moment, so ``capture_logs()`` would no
    longer see the loggers used before."""
    configure_logging()


@pytest.fixture(autouse=True)
def _one_logging_configuration():
    """Fails the test that replaces the processors of a configured structlog: the next test to capture the log
    of a module that logged before would get nothing, far from the cause."""
    before = structlog.get_config()['processors']
    yield
    if structlog.get_config()['processors'] is not before:
        pytest.fail('this test reconfigured structlog: ask for the app_logging fixture and capture with capture_logs()')


def italian_user(c, new_user) -> str:
    """A user who reads Italian, for a test that asserts on Italian copy: a new row is English (#699)."""
    uid = new_user(c)
    c.execute('UPDATE "user" SET locale = %s WHERE id = %s', ('it', uid))
    return uid
