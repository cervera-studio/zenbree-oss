"""Every way into the app enters a scope. Structural, with no database, like test_error_shape.py: each failure names
the route or file and the fix.

E1: every route has ``current_user``, ``current_user_or_api_key``, ``require_admin`` or ``public(...)`` in its
dependency tree, and that dependency is a coroutine function: a sync dependency runs in a copied context, so the
scope it enters never reaches the endpoint.

E2: every Celery task declares whose work it is. E3: a task runs in the scope it was published in, and leaves none
behind.

E4: every SSE response is an ``EventStream`` (app/core/event_stream.py), the one class that re-checks the stream's
member while it is open.

E5: a connection comes from the pool. E6: only the checkout switches the role or the scope settings. E7: a
``SystemReason`` is entered only in the modules its ``ALLOWED`` entry lists. E8: the reasons are pinned by a hash.
E9: a hand-off to another thread carries the scope.

The reason scan and the override scan (tests/scope_scans.py) resolve each file's aliases first; their cases are in
tests/test_scope_scans.py.
"""

import ast
import hashlib
import inspect
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.auth import middleware
from app.core import scope as scopes
from app.main import app
from app.worker import celery_app
from tests import scope_scans

APP = Path(__file__).resolve().parents[1] / 'app'


def _routes(routes) -> list[APIRoute]:
    """Every APIRoute, also under an included router, which FastAPI keeps whole as ``original_router``."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif (inner := getattr(route, 'original_router', None)) is not None:
            found += _routes(inner.routes)
        elif hasattr(route, 'routes'):
            found += _routes(route.routes)
    return found


def _calls(dependant: Dependant) -> list:
    return [dependant.call, *(c for d in dependant.dependencies for c in _calls(d))]


def _entry(call) -> bool:
    # public(...) is found by its mark, not its class, so this test also runs on code that predates it.
    named = (middleware.current_user, middleware.current_user_or_api_key, middleware.require_admin)
    return call in named or hasattr(call, 'zb_public')


def _is_async(call) -> bool:
    # a dependency that is an instance, like public(...), is called through its class's __call__
    return inspect.iscoroutinefunction(call) or inspect.iscoroutinefunction(type(call).__call__)


def test_every_route_enters_a_scope():
    routes = _routes(app.router.routes)
    assert len(routes) > 100  # the walk reached the included routers
    missing = [
        f'{",".join(sorted(r.methods))} {r.path} ({r.endpoint.__module__})'
        for r in routes
        if not any(_entry(c) for c in _calls(r.dependant))
    ]
    assert not missing, (
        'routes with no scope: depend on current_user, current_user_or_api_key or require_admin, or, for a route with'
        ' no caller, add dependencies=[Depends(public(SystemReason.X))] (public(None) when it reads no tenant data):\n'
        + '\n'.join(missing)
    )
    sync = sorted({c.__qualname__ for r in routes for c in _calls(r.dependant) if _entry(c) and not _is_async(c)})
    assert not sync, f'scope-entering dependencies must be async, or the scope never reaches the endpoint: {sync}'


def _app_reasons() -> tuple[list[scope_scans.ReasonUse], list[str]]:
    uses, refused = [], []
    for path in sorted(APP.rglob('*.py')):
        module = path.relative_to(APP).as_posix()
        if module in {'core/scope.py', 'core/system_reasons.py'}:  # where scopes and reasons are defined
            continue
        found, wrong = scope_scans.reasons(ast.parse(path.read_text()), module)
        uses += found
        refused += wrong
    return uses, refused


def test_system_is_entered_only_where_its_reason_allows():
    """Every ``SystemReason.X`` in app/ counts as an entry by its module, however the reason gets to ``system()``.
    A reason the scan cannot read (a variable, ``SystemReason[name]``) and a scope built outside app/core/scope.py
    are refused: either would cross organizations where ``ALLOWED`` does not look."""
    from app.core.system_reasons import ALLOWED, SystemReason

    uses, refused = _app_reasons()
    assert not refused, 'the scan cannot tell which reason enters here:\n' + '\n'.join(refused)
    assert {u.reason for u in uses} == {r.name for r in SystemReason}, 'a reason nothing enters: delete it'
    wrong = [
        f'app/{u.module}:{u.line} SystemReason.{u.reason}'
        for u in uses
        if u.module not in ALLOWED[SystemReason[u.reason]]
    ]
    assert not wrong, (
        'a reason entered outside the modules SystemReason.ALLOWED lists for it. Scope the entry point instead'
        ' (acting_for its organization); a new module for a reason needs the user approval in the PR:\n'
        + '\n'.join(wrong)
    )


#: sha256 of app/core/system_reasons.py. Agents: never edit this hash or the check below yourself (the
#: web/lint/guard.mjs rule). A new reason, or a new module for one, crosses organizations, so only the user approves
#: it; then the hash changes in the same PR, and the PR records the approval.
SYSTEM_REASONS_SHA256 = '3a53d70645d9d67ebafbf512bdcf0211c7e748e5bdbbba0038403576fa0bb44e'


def test_the_system_reasons_are_the_ones_the_user_approved():
    """E8: the list of reasons and ``ALLOWED`` change only with the user's approval, recorded in the PR."""
    found = hashlib.sha256((APP / 'core' / 'system_reasons.py').read_bytes()).hexdigest()
    assert found == SYSTEM_REASONS_SHA256, (
        'app/core/system_reasons.py changed. A reason, or a module in ALLOWED, lets code cross organizations: stop'
        ' and ask the user. Do not update SYSTEM_REASONS_SHA256 yourself; when the user approves, the PR records it.'
    )


def test_the_allowed_modules_cannot_change_at_run_time():
    """E7 and E8 read ALLOWED: a module that widened it at run time would pass both (#767 review)."""
    from app.core.system_reasons import ALLOWED, SystemReason

    with pytest.raises(TypeError):
        ALLOWED[SystemReason.HEALTH] = frozenset({'outreach/routes.py'})
    assert all(isinstance(modules, frozenset) for modules in ALLOWED.values())


TESTS = Path(__file__).resolve().parent
#: The dependencies in middleware.py that enter a scope or read the membership. ``protected_dependencies`` adds every
#: dependency of a route that depends on one of them: overriding it removes the entry from that route's tree.
MIDDLEWARE_ENTRIES = (
    middleware.current_user,
    middleware.current_user_or_api_key,
    middleware.require_admin,
    middleware.require_org_admin,
    middleware.require_org_admin_or_api_key,
)


def protected_dependencies() -> frozenset[str]:
    entries = {middleware.current_user, middleware.current_user_or_api_key, middleware.require_admin}
    found = {f.__name__ for f in MIDDLEWARE_ENTRIES}

    def holds_entry(dependant: Dependant) -> bool:
        held = dependant.call in entries
        for sub in dependant.dependencies:
            held = holds_entry(sub) or held
        if held and dependant.call is not None:
            found.add(getattr(dependant.call, '__name__', type(dependant.call).__name__))
        return held

    for route in _routes(app.router.routes):
        for dependency in route.dependant.dependencies:  # not the endpoint: it is no dependency to override
            holds_entry(dependency)
    return frozenset(found)


def test_every_middleware_entry_is_protected():
    """Catches a new scope-entering dependency in middleware.py that ``MIDDLEWARE_ENTRIES`` does not list."""
    tree = ast.parse(Path(middleware.__file__).read_text())
    entering = {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            getattr(c.func, 'id', None) in {'_enter', 'enter_member', 'enter_system_for_request'}
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
        )
    }
    depending = {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(d, ast.Call)
            and getattr(d.func, 'id', None) == 'Depends'
            and d.args
            and getattr(d.args[0], 'id', None) in entering | {f.__name__ for f in MIDDLEWARE_ENTRIES}
            for d in node.args.defaults
        )
    }
    assert (entering | depending) - {'_enter'} <= {f.__name__ for f in MIDDLEWARE_ENTRIES}


def test_no_test_overrides_a_scope_entering_dependency():
    """An override of ``current_user``, or of a dependency through which a route reaches it, skips
    ``zb.enter_member``: the route's SQL runs as the login role, which bypasses row-level security, so the test
    proves nothing about the scope. So does a fake of the membership read that runs with a test database."""
    protected = protected_dependencies()
    found = []
    for path in sorted(TESTS.rglob('*.py')):
        tree = ast.parse(path.read_text())
        where = path.relative_to(TESTS.parent)
        found += [f'{where}:{line} {why}' for line, why in scope_scans.overrides(tree, protected)]
        found += [f'{where}:{line} {why}' for line, why in scope_scans.membership_fakes(tree)]
    assert not found, (
        'a test overrides a scope-entering dependency or fakes the membership read. Override the token step instead:'
        ' tests.conftest.sign_in(app, user) (the membership comes from the database: write member.role, and'
        ' "user".role for require_admin), or the sign_in_without_db fixture for a test with no database. For the'
        f' active project, patch app.projects.routes._active. The protected dependencies: {sorted(protected)}\n'
        + '\n'.join(found)
    )


def test_every_task_declares_its_scope():
    """E2: every task the worker runs says whose work it is (app/core/tasks.py). A plain ``@celery_app.task`` runs as
    the login role, which bypasses row-level security: its SQL reads and writes every organization."""
    names = sorted(n for n in celery_app.tasks if not n.startswith('celery.'))
    assert len(names) > 20  # the walk reached the worker's tasks
    undeclared = [n for n in names if not getattr(celery_app.tasks[n], 'zb_scope', None)]
    assert not undeclared, (
        'tasks with no declared scope: use member_task (person work, ends when the member leaves), org_task'
        ' (organization work that outlives the member) or system_task(SystemReason.X) (a beat that crosses'
        ' organizations) from app.core.tasks, not @celery_app.task:\n' + '\n'.join(undeclared)
    )


def _none_defaults(tree: ast.Module) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        a = node.args
        positional = a.posonlyargs + a.args
        defaulted = positional[len(positional) - len(a.defaults) :]
        pairs = [*zip(defaulted, a.defaults, strict=True), *zip(a.kwonlyargs, a.kw_defaults, strict=True)]
        found += [
            (arg.lineno, f'{getattr(node, "name", "lambda")}({arg.arg}=None)')
            for arg, default in pairs
            if arg.arg in {'org_id', 'organization_id'} and isinstance(default, ast.Constant) and default.value is None
        ]
    return found


def test_no_function_takes_every_organization_as_a_missing_one():
    """``org_id=None`` meaning "every organization" is how #410 and #701 happened: a caller that forgot the argument
    read every organization. Row-level security and ``zb.org()`` name the organization; a function that needs one
    takes it with no default."""
    found = [
        f'app/{path.relative_to(APP).as_posix()}:{line} {what}'
        for path in sorted(APP.rglob('*.py'))
        for line, what in _none_defaults(ast.parse(path.read_text()))
    ]
    assert not found, (
        'an org_id or organization_id parameter defaults to None: drop the default (or the parameter, where the'
        ' scope names the organization):\n' + '\n'.join(found)
    )


class _Producer:
    """Takes the message Celery would send to the broker."""

    class connection:  # the attribute Celery reads from a producer
        _reraise_as_library_errors = staticmethod(nullcontext)

    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    def publish(self, body, **options) -> None:
        self.sent.append((body, options['headers']))


def _published(task, *args) -> tuple[tuple, dict]:
    producer = _Producer()
    task.apply_async(args, producer=producer)
    return producer.sent[0]


def _delivered(task, message: tuple[tuple, dict]) -> None:
    """The prefork child's side: a Celery request built from the message, run in this process."""
    from celery.worker.request import Request
    from kombu.message import Message
    from kombu.serialization import dumps

    body, headers = message
    content_type, encoding, payload = dumps(body, serializer='json')
    raw = Message(
        body=payload,
        headers=headers,
        content_type=content_type,
        content_encoding=encoding,
        delivery_info={'exchange': '', 'routing_key': 'celery'},
        properties={},
    )
    Request(raw, app=task.app, task=task).execute()


@pytest.fixture
def probes():
    """One task of each kind on an app of its own, each recording the scope its body ran in."""
    from celery import Celery

    from app.core import tasks
    from app.core.system_reasons import SystemReason

    app = Celery('e3', set_as_current=False)
    seen: list = []

    def probe() -> None:
        seen.append(scopes.current_or_none())

    made = {
        'member': tasks.member_task(app, name='e3.member')(probe),
        'org': tasks.org_task(app, name='e3.org')(probe),
        'system': tasks.system_task(app, SystemReason.RETENTION, name='e3.system')(probe),
    }
    yield made, seen
    for name in ('e3.member', 'e3.org', 'e3.system'):
        tasks.KINDS.pop(name)


A, B, U = (
    '00000000-0000-0000-0000-00000000000a',
    '00000000-0000-0000-0000-00000000000b',
    '00000000-0000-0000-0000-0000000000cc',
)


def test_a_task_runs_in_the_scope_it_was_published_in(probes):
    """E3: the worker enters the publisher's scope from the header and leaves it when the body ends. One prefork
    child runs many tasks: a scope left set reaches the next one, which then acts in the wrong organization."""
    from app.core.system_reasons import SystemReason

    made, seen = probes
    with scopes.acting_for(A, 'test'):
        for_a = _published(made['org'])
    with scopes.acting_as(B, U):
        as_u = _published(made['member'])
        for_b = _published(made['org'])

    for task, message in ((made['org'], for_a), (made['member'], as_u), (made['org'], for_b)):
        _delivered(task, message)
        assert scopes.current_or_none() is None, f'{task.name} left its scope set for the next task'
    _delivered(made['system'], _published(made['system']))
    assert scopes.current_or_none() is None

    assert seen == [
        scopes.ForOrg(scopes.OrgId(A), 'task e3.org'),
        scopes.Member(scopes.OrgId(B), scopes.UserId(U)),
        scopes.ForOrg(scopes.OrgId(B), 'task e3.org'),
        scopes.System(SystemReason.RETENTION),
    ]


def test_a_task_is_published_only_from_its_scope(probes):
    """The header names the enqueue site: a person's task queued outside a ``Member``, or an organization's task queued
    with no organization, raises at ``.delay`` instead of running unscoped in the worker."""
    made, _ = probes
    with pytest.raises(scopes.ScopeMissing, match='Member scope'):
        _published(made['member'])
    with scopes.acting_for(A, 'test'), pytest.raises(scopes.ScopeMissing, match='Member scope'):
        _published(made['member'])
    with pytest.raises(scopes.ScopeMissing, match='Member or ForOrg scope'):
        _published(made['org'])
    assert _published(made['system'])[1].get('zb_scope') is None  # a beat task enters its reason itself


def test_a_message_with_no_header_does_not_run(probes):
    """A message queued by the previous image during a deploy has no header: it is dropped, never run unscoped."""
    made, seen = probes
    with scopes.acting_as(A, U):
        body, headers = _published(made['member'])
    _delivered(made['member'], (body, {k: v for k, v in headers.items() if k != 'zb_scope'}))
    assert seen == []


SSE_MEDIA_TYPE = 'text/event-stream'


def sse_outside_event_stream(tree: ast.Module) -> list[tuple[int, str]]:
    """What in ``tree`` can answer SSE without ``EventStream``: FastAPI's ``EventSourceResponse``, a subclass of
    ``StreamingResponse``, a ``StreamingResponse`` that is not plainly another media type, or the SSE media type
    itself (on a ``Response``, in headers)."""
    found = []
    for node in ast.walk(tree):
        name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
        if name == 'EventSourceResponse':
            found.append((node.lineno, 'EventSourceResponse'))
        elif isinstance(node, ast.alias) and node.name.rpartition('.')[2] == 'EventSourceResponse':
            found.append((node.lineno, 'import EventSourceResponse'))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SSE_MEDIA_TYPE in node.value:
            found.append((node.lineno, repr(node.value)))
        elif isinstance(node, ast.ClassDef) and any(
            getattr(b, 'id', getattr(b, 'attr', None)) == 'StreamingResponse' for b in node.bases
        ):
            found.append((node.lineno, f'class {node.name}(StreamingResponse)'))
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, 'id', getattr(node.func, 'attr', None)) == 'StreamingResponse'
        ):
            media = next((k.value for k in node.keywords if k.arg == 'media_type'), None)
            # A plain other type (a CSV download) is not a stream to re-check; a computed one could be SSE.
            if not (isinstance(media, ast.Constant) and isinstance(media.value, str)):
                found.append((node.lineno, 'StreamingResponse with no literal media_type'))
    return found


def test_every_stream_is_an_event_stream():
    """E4: a stream answers for minutes or hours on one request, and only ``EventStream`` re-checks that its member
    is still one (``MEMBER_RECHECK_SECONDS``): an SSE response built another way keeps sending a removed member
    their organization's events."""
    from app.core.event_stream import EventStream

    found = [
        f'app/{path.relative_to(APP).as_posix()}:{line} {what}'
        for path in sorted(APP.rglob('*.py'))
        if path.relative_to(APP).as_posix() != 'core/event_stream.py'
        for line, what in sse_outside_event_stream(ast.parse(path.read_text()))
    ]
    routes = _routes(app.router.routes)
    found += [
        f'{",".join(sorted(r.methods))} {r.path} ({r.endpoint.__module__}): response_class {r.response_class.__name__}'
        for r in routes
        if getattr(r.response_class, 'media_type', None) == SSE_MEDIA_TYPE and r.response_class is not EventStream
    ]
    found += [
        f'{",".join(sorted(r.methods))} {r.path} ({r.endpoint.__module__}): returns EventStream, response_class'
        f' {getattr(r.response_class, "__name__", r.response_class)}'
        for r in routes
        if inspect.signature(r.endpoint).return_annotation in (EventStream, 'EventStream')
        and r.response_class is not EventStream
    ]
    assert not found, (
        'an SSE response outside EventStream: return EventStream(frames) from app.core.event_stream, and pass'
        ' response_class=EventStream on the route:\n' + '\n'.join(found)
    )


@pytest.mark.parametrize(
    ('source', 'caught'),
    [
        ("StreamingResponse(frames(), media_type='text/event-stream')", True),
        ('StreamingResponse(frames(), media_type=MEDIA)', True),
        ('StreamingResponse(frames())', True),
        ("responses.StreamingResponse(frames(), media_type='text/csv')", False),
        ("Response(body, media_type='text/event-stream; charset=utf-8')", True),
        ('from fastapi.sse import EventSourceResponse', True),
        ('class Mine(StreamingResponse): pass', True),
        ('return EventStream(frames())', False),
    ],
)
def test_the_stream_scan_finds_each_way_around_event_stream(source, caught):
    tree = ast.parse(f'def route():\n    {source}' if source.startswith('return') else source)
    assert bool(sse_outside_event_stream(tree)) is caught


#: Where the role, the zb.* settings and the transaction are written: the checkout and the scope it enters.
SCOPE_OWNERS = frozenset({'core/db.py', 'core/scope.py'})


def _app_scan(scan, skipped: frozenset[str]) -> list[str]:
    return [
        f'app/{module}:{line} {what}'
        for path in sorted(APP.rglob('*.py'))
        if (module := path.relative_to(APP).as_posix()) not in skipped
        for line, what in scan(ast.parse(path.read_text()))
    ]


def test_only_the_checkout_switches_the_role_or_the_scope_settings():
    """E6: the scoped checkout's first statement sets the role and the ``zb.*`` settings for one transaction. SQL that
    switches either by hand runs as someone the scope did not enter, and a ``commit`` or ``rollback`` ends that
    transaction, so the next statement runs as the login role with no scope."""
    switched = _app_scan(scope_scans.role_switches, SCOPE_OWNERS)
    assert not switched, (
        'SQL that sets the role or a zb.* setting, or that resets the session (RESET ALL, DISCARD) and so clears the'
        ' scope, outside app/core/db.py and app/core/scope.py: enter a scope'
        ' instead (acting_for, acting_as, system):\n' + '\n'.join(switched)
    )
    ended_in_sql = _app_scan(scope_scans.statement_ends, SCOPE_OWNERS)
    assert not ended_in_sql, (
        'SQL that ends the transaction (COMMIT, ROLLBACK, END, ABORT) outside app/core/db.py and app/core/scope.py:'
        ' the pool commits when the with get_conn() block ends, and an exception out of that block rolls it back.'
        ' For a part that may fail alone, use with conn.transaction(): a savepoint keeps the scope:\n'
        + '\n'.join(ended_in_sql)
    )
    ended = _app_scan(scope_scans.transaction_ends, frozenset({'core/db.py'}))
    assert not ended, (
        'a commit or rollback outside app/core/db.py: the pool commits when the with get_conn() block ends. For a'
        ' part that may fail alone, use with conn.transaction(): a savepoint keeps the scope:\n' + '\n'.join(ended)
    )


#: Where a connection may skip the pool: the pool itself and migrations (they run as the login role before any scope
#: exists).
RAW_CONNECTION_OWNERS = frozenset({'core/db.py', 'core/migrate.py'})
#: The Home listener: one LISTEN connection that reads no table. Only that method of its file may skip the pool.
LISTENER = ('home/live.py', 'Hub._listen')


def listener_strays(tree: ast.AST, listener: str) -> list[str]:
    """Each raw connection in the listener's file other than the one the listener opens."""
    found = [(line, what, scope_scans.enclosing(tree, line)) for line, what in scope_scans.raw_connections(tree)]
    inside = [f for f in found if f[2] == listener]
    strays = [f for f in found if f[2] != listener] + inside[1:]
    return [f'{line} {what} (in {where or "the module"})' for line, what, where in strays]


def test_a_connection_comes_from_the_pool():
    """E5: a raw connection skips the checkout, so it runs as the login role, which bypasses row-level security."""
    module, listener = LISTENER
    raw = _app_scan(scope_scans.raw_connections, RAW_CONNECTION_OWNERS | {module})
    raw += [f'app/{module}:{s}' for s in listener_strays(ast.parse((APP / module).read_text()), listener)]
    assert not raw, (
        f'a connection outside the pool: use get_conn() from app.core.db (in app/{module}, only {listener} opens'
        ' its one LISTEN connection):\n' + '\n'.join(raw)
    )


_LISTENER_FILE = """
import psycopg
class Hub:
    async def _listen(self):
        async with await psycopg.AsyncConnection.connect(url) as conn:
            pass
{more}
"""


@pytest.mark.parametrize(
    ('more', 'caught'),
    [
        ('', False),
        ('def _project_exists():\n    psycopg.connect(url)', True),
        ('class Hub2:\n    async def _listen(self):\n        psycopg.connect(url)', True),
        ('conn = psycopg.connect(url)', True),
        (
            'class Hub(Hub):\n    async def _listen(self):\n        await psycopg.AsyncConnection.connect(url)\n'
            '        await psycopg.AsyncConnection.connect(other)',
            True,
        ),
    ],
)
def test_only_the_listener_opens_a_raw_connection_in_its_file(more, caught):
    assert bool(listener_strays(ast.parse(_LISTENER_FILE.format(more=more)), 'Hub._listen')) is caught


def test_a_hand_off_to_another_thread_carries_the_scope():
    """E9: a raw thread or an executor starts with an empty context, so the scope stays behind: the thread's checkout
    raises ScopeMissing, or runs in a scope the thread entered itself instead of the caller's."""
    dropped = _app_scan(scope_scans.scope_drops, frozenset())
    assert not dropped, (
        'a function sent to another thread without the scope: use asyncio.to_thread, or pass'
        ' contextvars.copy_context().run (or scope.for_another_thread().run) as the function:\n' + '\n'.join(dropped)
    )


@pytest.mark.parametrize(
    ('source', 'caught'),
    [
        ('pool.submit(work, 1)', True),
        ('pool.submit(contextvars.copy_context().run, work, 1)', False),
        ('pool.submit(scopes.for_another_thread().run, work)', False),
        ('pool.map(work, items)', True),
        ('await loop.run_in_executor(None, work, 1)', True),
        ('await loop.run_in_executor(None, copy_context().run, work, 1)', False),
        ('await asyncio.to_thread(work, 1)', False),
        ('import threading\nthreading.Thread(target=work).start()', True),
        ('import threading\nthreading.Thread(None, work).start()', True),
        ('import threading\nthreading.Thread(target=contextvars.copy_context().run, args=(work,))', False),
        ('from threading import Thread as T\nT(target=work)', True),
        ('import threading as th\nth.Timer(5, work).start()', True),
        ('class Thread(BaseModel):\n    id: str\nThread(id=x)', False),
        ('threading.Lock()', False),
    ],
)
def test_the_hand_off_scan_finds_each_thread_without_the_scope(source, caught):
    assert bool(scope_scans.scope_drops(ast.parse(source))) is caught


@pytest.mark.parametrize(
    ('source', 'caught'),
    [
        ("conn.execute('SELECT set_config(%s, %s, true)', ('zb.org_id', org))", True),
        ("conn.execute('SELECT SET_CONFIG (name, %s, false)', (v,))", True),
        ("conn.execute('SET LOCAL %s = %%s' % ('zb.org_id',), (org,))", True),
        ("conn.execute('SET ' + 'LOCAL %s' % ('ROLE x',))", True),
        ("conn.execute(f'SET LOCAL {name} = 1')", True),
        ("conn.execute('SET ROLE x')", True),
        ("conn.execute('select 1; SET ROLE x')", True),
        ("conn.execute('-- as admin\\nSET ROLE x')", True),
        ("conn.execute('DO $$ BEGIN SET ROLE x; END $$')", True),
        ('conn.execute(\'SET "role" TO x\')', True),
        ("conn.execute('UPDATE t SET role = %s WHERE id = %s', (r, i))", False),
        (
            "conn.execute('INSERT INTO t (id, role) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role')",
            False,
        ),
        ("conn.execute('UPDATE t SET role = %s, name = %s')", False),
        ("conn.execute('END')", True),
        ("conn.execute('select 1; ABORT')", True),
        ("conn.execute('SELECT 1 AS end')", False),
        ("conn.execute('DO $$ BEGIN UPDATE t SET a = 1; END; $$')", False),
        ('cur.execute(f"SET LOCAL zb.org_id = \'{org}\'")', True),
        ("setting = 'zb.org_id'\ncur.execute(f'SET LOCAL {setting} = {x}')", True),
        ("conn.execute('SET LOCAL ' + 'zb.org_id = %s', (org,))", True),
        ("conn.execute('SET ' + 'ROLE ' + role)", True),
        ("conn.execute(sql.SQL('SET LOCAL {} = {}').format(sql.Identifier('zb', 'org_id'), sql.Literal(org)))", True),
        ("conn.execute(sql.SQL(' ').join([sql.SQL('SET'), sql.SQL('SESSION'), sql.SQL('AUTHORIZATION x')]))", True),
        ("prefix = 'SET LOCAL'\nconn.execute(f'{prefix} zb.actor_id = 1')", True),
        ("conn.execute('RESET ' + 'ROLE')", True),
        ('conn.execute("SELECT set_config(\'zb.org_id\', %s, true)", (org,))', True),
        ("conn.execute('SELECT zb.enter_org(%s)', (org,))", True),
        ("conn.execute('SET row_security = off')", True),
        ("conn.execute('COMMIT')", True),
        ("conn.execute('UPDATE lists SET name = %s', (name,))", False),
        ("conn.execute('SELECT 1 AS commit')", False),
        ('conn.execute("DO $$ BEGIN EXECUTE \'SET ROLE x\'; END $$")', True),
        ('conn.execute("DO $$ BEGIN EXECUTE format(\'SET ROLE %I\', r); END $$")', True),
        ("conn.execute(f'{pre}SET ROLE x')", True),
        ("conn.execute('SET ROLE NONE')", True),
        ("conn.execute('SET role TO x')", True),
        ("conn.execute('SET SESSION ROLE x')", True),
        ("conn.execute('UPDATE t SET a = 1; SET role = x')", True),
        ("sets = 'SET role = %s'\nconn.execute('UPDATE t ' + sets)", True),
        ("conn.execute('SET session_authorization TO x')", True),
        ("conn.execute('SET SESSION zb.org_id = 1')", True),
        ("conn.execute('RESET ALL')", True),
        ("conn.execute('DISCARD ALL')", True),
        ("conn.execute(f'SET {name} = {v}')", True),
        ("conn.execute(f'SET SESSION {name} TO {v}')", True),
        ("conn.execute('SET %s = %%s' % (name,), (v,))", True),
        ("clause = f'SET {assignments}'\nconn.execute('UPDATE t ' + clause + ' WHERE id = 1')", False),
        ("conn.execute(f'UPDATE t SET {column} = %s WHERE id = %s', (v, i))", False),
        ("conn.execute(f'INSERT INTO t VALUES (%s) ON CONFLICT (id) DO UPDATE SET {sets}, at = now()', (v,))", False),
        ("conn.execute('UPDATE t SET (role, title) = (%s, %s)', (r, t))", False),
        ("conn.execute('SELECT name, discarded FROM csv_imports')", False),
        ("prompt = 'Local businesses: set local_query to the category'", False),
        ("conn.execute('select 1; COMMIT')", True),
        ("conn.execute('COMMIT; select 1')", True),
        ("conn.execute('END WORK; SELECT 1')", True),
        ("conn.execute('UPDATE t SET a = 1; END; SELECT 1')", True),
        ("conn.execute('ROLLBACK AND NO CHAIN')", True),
        ("q = 'COMMIT'\nconn.execute(q)", True),
        ("cur.executemany(sql.SQL('ABORT'), rows)", True),
        ("conn.execute('DO $$ BEGIN COMMIT; END $$')", True),
        ("conn.execute('DO $$ BEGIN UPDATE t SET a = 1; END $$; END TRANSACTION')", True),
        ("conn.execute('DO $$ BEGIN IF x THEN UPDATE t SET a = 1; END IF; END; $$')", False),
        ("conn.execute('DO $f$ BEGIN UPDATE t SET a = 1; END; $f$')", False),
        (
            "conn.execute('CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $body$ BEGIN SELECT 1; END; $body$')",
            False,
        ),
        ("conn.execute('DO $f$ BEGIN COMMIT; END $f$')", True),
        ("r.eval('local count = 0; if count > 0 then return count; end', 0)", False),
        (
            "r.eval(\"if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]); "
            'else return 0; end", 1, k, v)',
            False,
        ),
        ("status = 'COMMIT'", False),
        ("conn.execute('SELECT %s', ('end',))", False),
        ('"""A docstring may say SET ROLE: no code runs it."""', False),
        ("conn.execute(f'SET SESSION {x}')", True),
        ("conn.execute('SET SESSION %s' % (x,))", True),
        ("conn.execute('DISCARD PLANS')", True),
        ("conn.execute('select 1; DISCARD TEMP;')", True),
        ("conn.execute('select 1; RESET ALL')", True),
        ("conn.execute('DO $$ BEGIN RESET ALL; END $$')", True),
        ('conn.execute("DO $$ BEGIN EXECUTE \'RESET ALL\'; END $$")', True),
        ("conn.execute(f'{pre}RESET ALL')", True),
        ("conn.execute(f'RESET ALL{x}')", True),
        ("conn.execute('RESET ALL' + rest)", True),
        ("conn.execute('SET LOCAL ROLE x')", True),
        ("conn.execute('SET LOCAL zb.org_id = 1')", True),
        ("conn.execute('SET LOCAL search_path = x')", True),
        ("conn.execute('SET LOCAL row_security = off')", True),
        ("conn.execute('SET LOCAL SESSION AUTHORIZATION x')", True),
        ("conn.execute('SET SESSION AUTHORIZATION x')", True),
        ("conn.execute('RESET SESSION AUTHORIZATION')", True),
        ("for row in cur.stream('COMMIT'): pass", True),
        ("prompt = 'Discard companies that do not match the brief.'", False),
        ("prompt = 'Discard all companies that do not match the brief.'", False),
        ("prompt = 'Set local filters first.'", False),
        ("prompt = 'Set local roles aside.'", False),
        ("message = 'Reset all filters.'", False),
        ("prompt = f'Discard all {entity} that do not match the brief.'", False),
        ("message = f'Reset all {n} filters and try again.'", False),
        ("prompt = 'If none match, discard all'", False),
        ('prompt = "Use the \'Discard all\' button"', False),
        ("message = 'Reset all filters and try again.'", False),
        ("message = 'Session authorization failed.'", False),
        ("Action = Literal['keep', 'discard']", False),
        ('conn.execute("UPDATE replies SET action = \'discard\' WHERE id = %s", (i,))', False),
        ("conn.execute('SET LOCAL statement_timeout = 1000')", False),
        ('seen.discard(x)', False),
        ("with httpx.stream('GET', url) as r: pass", False),
    ],
)
def test_the_switch_scan_reads_each_way_to_spell_the_sql(source, caught):
    assert bool(scope_scans.scope_switches(ast.parse(source))) is caught


@pytest.mark.parametrize(
    ('source', 'role', 'end'),
    [
        ("conn.execute('SET ROLE x')", True, False),
        ("conn.execute('COMMIT')", False, True),
        ("conn.execute('select 1; ROLLBACK')", False, True),
        ("conn.execute('SET LOCAL zb.org_id = 1; COMMIT')", True, True),
    ],
)
def test_the_switch_scan_tells_a_role_switch_from_a_transaction_end(source, role, end):
    """E6 gives each its own advice: a scope for a switch, the end of the with block for a transaction end."""
    tree = ast.parse(source)
    assert (bool(scope_scans.role_switches(tree)), bool(scope_scans.statement_ends(tree))) == (role, end)


@pytest.mark.parametrize(
    ('source', 'caught'),
    [
        ('conn.commit()', True),
        ('with get_conn() as conn:\n    c = conn\n    c.rollback()', True),
        ('done = conn.commit\ndone()', True),
        ('await asyncio.to_thread(conn.rollback)', True),
        ("getattr(conn, 'commit')()", True),
        ('with conn.transaction():\n    conn.execute(q)', False),
    ],
)
def test_the_transaction_scan_finds_each_way_to_end_it(source, caught):
    assert bool(scope_scans.transaction_ends(ast.parse(source))) is caught


@pytest.mark.parametrize(
    ('source', 'caught'),
    [
        ('import psycopg\npsycopg.connect(url)', True),
        ('from app.core.db import _pool\nwith _pool().connection() as conn: pass', True),
        ('from app.core import db\nwith db._pool().connection() as conn: pass', True),
        ('import app.core.db as d\nd._pool().connection()', True),
        ('from app.core.db import get_conn\nwith get_conn() as conn: pass', False),
        ('from psycopg.connection import Connection\nConnection.connect(url)', True),
        ('import psycopg\npsycopg.Connection.connect(url)', True),
        ('import psycopg as pg\npg.connect(url)', True),
        ('from psycopg import connect\nconnect(url)', True),
        ('from psycopg import AsyncConnection as A\nawait A.connect(url)', True),
        ('import psycopg\nmake = psycopg.Connection.connect\nmake(url)', True),
        ('from psycopg_pool import ConnectionPool\nConnectionPool(url)', True),
        ('import psycopg_pool\npsycopg_pool.AsyncConnectionPool(url)', True),
        ('from psycopg.types.json import Jsonb\nJsonb({})', False),
        ('redis.connect(url)', False),
    ],
)
def test_the_raw_connection_scan_finds_each_way_around_the_pool(source, caught):
    assert bool(scope_scans.raw_connections(ast.parse(source))) is caught
