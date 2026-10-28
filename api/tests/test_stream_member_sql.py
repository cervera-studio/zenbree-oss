"""Every SSE stream re-checks its member (organization-scoping PR 11): a member removed or banned while a stream is
open gets one ``error`` frame ``AUTH.NOT_A_MEMBER`` within one re-check interval, and the stream ends. And the stream
body runs in the request's scope: a database call inside it runs as ``zenbree_app`` for the request's member.

Each request goes through the real app and the real membership read (``sign_in`` replaces the token step only),
driven over ASGI by hand: TestClient and httpx's ASGITransport return the body only once the app has finished, and a
stream that never ends would hang them.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from app.agent import events
from app.agent import routes as agent_routes
from app.core import db as core_db
from app.core import event_stream
from app.home import live
from app.main import app
from app.onboarding import study_stream
from app.onboarding.study import Drafting, PageRead, Profile, Studied
from tests.conftest import member, project, sign_in
from tests.test_agent import _job, _user, needs_db
from tests.test_agent import db as db
from tests.test_ai_columns import _list_with_rows

pytestmark = needs_db

RECHECK = 0.3
# The re-check runs in a thread and the frame crosses the event loop: one interval plus this is "within one".
SLACK = 1.0
NOT_A_MEMBER = {'message': 'You are no longer a member of this organization.', 'code': 'AUTH.NOT_A_MEMBER'}


@dataclass
class Opened:
    """One request in flight: its status and its body chunks as the app sends them."""

    status: int | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    chunks: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    left: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    async def frame(self, seconds: float) -> tuple[str, dict] | None:
        """The next frame that is not a ``: ping``, or None when the stream ended."""
        async with asyncio.timeout(seconds):
            while (chunk := await self.chunks.get()) is not None:
                text = chunk.decode()
                if text.startswith(':'):
                    continue
                lines = dict(line.split(': ', 1) for line in text.strip().split('\n'))
                return lines['event'], json.loads(lines['data'])
        return None

    async def close(self) -> None:
        self.left.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def _open(method: str, path: str, body: dict | None = None, spec_version: str = '2.3') -> Opened:
    """``spec_version`` 2.3 is what uvicorn sends: Starlette then reads the body in a task of its own, next to a task
    that waits for the disconnect. With 2.4 it reads it in the request's task."""
    opened = Opened()
    payload = json.dumps(body).encode() if body is not None else b''
    sent_body = False

    async def receive() -> dict:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {'type': 'http.request', 'body': payload, 'more_body': False}
        await opened.left.wait()
        return {'type': 'http.disconnect'}

    async def send(message: dict) -> None:
        if message['type'] == 'http.response.start':
            opened.status = message['status']
            opened.started.set()
        elif message['type'] == 'http.response.body':
            if message.get('body'):
                await opened.chunks.put(message['body'])
            if not message.get('more_body'):
                await opened.chunks.put(None)

    path, _, query = path.partition('?')
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': spec_version},
        'http_version': '1.1',
        'method': method,
        'scheme': 'http',
        'path': path,
        'raw_path': path.encode(),
        'query_string': query.encode(),
        'root_path': '',
        'headers': [(b'host', b'test'), (b'content-type', b'application/json')],
        'client': ('127.0.0.1', 1),
        'server': ('test', 80),
    }
    opened.task = asyncio.create_task(app(scope, receive, send))
    return opened


# ── The streams: each one's route, opened as the signed-in member ──


def _chat(db, user) -> tuple[str, str, dict | None]:
    return 'POST', '/v1/chat', {'message': 'agenzie a Milano'}


def _chat_reattach(db, user) -> tuple[str, str, dict | None]:
    sid, _ = _job(db, user.id, 'agenzie a Milano')
    return 'GET', f'/v1/chat/sessions/{sid}/stream', None


def _fill(db, user) -> tuple[str, str, dict | None]:
    return 'GET', f'/v1/lists/{_list_with_rows(db, user.id, ["Acme"])}/stream', None


def _csv_import(db, user) -> tuple[str, str, dict | None]:
    import_id = db.execute(
        'INSERT INTO csv_imports (organization_id, user_id, project_id, entity, name, status)'
        " VALUES (%s, %s, %s, 'company', 'leads.csv', 'running') RETURNING id",
        (user.org_id, user.id, project(db, user.id)),
    ).fetchone()['id']
    return 'GET', f'/v1/lists/imports/{import_id}/stream', None


def _home(db, user) -> tuple[str, str, dict | None]:
    return 'GET', '/v1/home/stream', None


def _nav(db, user) -> tuple[str, str, dict | None]:
    return 'GET', '/v1/home/nav/stream', None


def _notifications(db, user) -> tuple[str, str, dict | None]:
    return 'GET', '/v1/notifications/stream', None


def _onboarding(db, user) -> tuple[str, str, dict | None]:
    return 'POST', '/v1/onboarding/profile', {'website': 'acme.it'}


STREAMS: dict[str, Callable] = {
    'chat': _chat,
    'chat reattach': _chat_reattach,
    'list fill': _fill,
    'csv import': _csv_import,
    'home': _home,
    'nav': _nav,
    'notifications': _notifications,
    'onboarding study': _onboarding,
}


async def _stalled_study(website, description, locale):
    """A study that reads one page and then waits on the LLM for good: no frame, no ping."""
    yield PageRead('https://acme.it')
    await asyncio.Event().wait()


@pytest.fixture
def signed_in(db, monkeypatch):
    monkeypatch.setattr(agent_routes, '_enqueue', lambda job_id: None)
    monkeypatch.setattr(study_stream, 'study_company', _stalled_study)
    user = member(db, _user(db))
    project(db, user.id)
    sign_in(app, user)
    try:
        yield user
    finally:
        app.dependency_overrides.clear()


def _leave(db, user, how: str) -> None:
    # Without its trigger the removal wakes no Home stream: only the timed re-check can end it.
    db.execute('ALTER TABLE member DISABLE TRIGGER home_touch')
    if how == 'removed':
        db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (user.id, user.org_id))
    else:
        db.execute('UPDATE "user" SET banned = true WHERE id = %s', (user.id,))


async def _open_for(opened: Opened, seconds: float) -> list[tuple[str, dict]]:
    """The frames the stream sends in ``seconds``; fails if it ends meanwhile."""
    seen = []
    try:
        async with asyncio.timeout(seconds):
            while True:
                frame = await opened.frame(seconds)
                assert frame is not None, f'the stream ended for a live member after {seen}'
                seen.append(frame)
    except TimeoutError:
        pass
    return seen


@pytest.mark.parametrize('how', ['removed', 'banned'])
@pytest.mark.parametrize('stream', STREAMS)
async def test_a_member_who_leaves_an_open_stream_gets_one_error_frame_and_the_end(
    db, signed_in, monkeypatch, stream, how
):
    """Catches: a stream that checks its member only when it starts (the notifications stream on main), or only
    when its own frames come (every 15 s ping). Idle on purpose: only EventStream's timer can see the removal."""
    monkeypatch.setattr(event_stream, 'MEMBER_RECHECK_SECONDS', RECHECK, raising=False)
    method, path, body = STREAMS[stream](db, signed_in)
    opened = await _open(method, path, body)
    try:
        # A loaded machine can take seconds to start the response; the re-checks are timed from there.
        async with asyncio.timeout(30):
            await opened.started.wait()
        # Two re-checks of a member who is still there: they keep the stream open.
        await _open_for(opened, 2 * RECHECK)
        assert opened.status == 200
        _leave(db, signed_in, how)
        assert await opened.frame(RECHECK + SLACK) == ('error', NOT_A_MEMBER)
        rest = []
        while (frame := await opened.frame(SLACK)) is not None:
            rest.append(frame)
        # The chat's web store ends a turn on `done` (docs/contracts-m1.md, Server-sent events).
        assert [e for e, _ in rest] == (['done'] if stream.startswith('chat') else [])
    finally:
        await opened.close()


async def test_a_study_whose_member_left_before_the_save_ends_with_the_error_frame(db, signed_in, monkeypatch):
    """The carry-over of PR 6: the save is the study's checkout, and ActorGone raised there ended the stream with no
    frame at all. It now reaches EventStream like any other checkout's, with no re-check needed."""
    monkeypatch.setattr(event_stream, 'MEMBER_RECHECK_SECONDS', 60, raising=False)

    async def study(website, description, locale):
        yield Drafting()
        _leave(db, signed_in, 'removed')
        yield Studied(Profile(company_name='Acme'), ['https://acme.it'])

    monkeypatch.setattr(study_stream, 'study_company', study)
    opened = await _open('POST', '/v1/onboarding/profile', {'website': 'acme.it'})
    try:
        assert await opened.frame(5) == ('drafting', {})
        assert await opened.frame(5) == ('error', NOT_A_MEMBER)
        assert await opened.frame(5) is None
    finally:
        await opened.close()
    assert db.execute('SELECT count(*) AS n FROM business_profiles').fetchone()['n'] == 0


# ── The scope of the stream body (decision 2): a database call inside each generator ──

PROBE = (
    "SELECT current_user AS role, current_setting('zb.actor_id', true) AS actor,"
    " current_setting('zb.org_id', true) AS org"
)


def _probe() -> dict:
    with core_db.get_conn() as conn:
        return conn.execute(PROBE).fetchone()


def _probing_read_stream(seen: list):
    """``events.read_stream`` (fill, CSV import, notifications, the chat's tail) with one checkout first."""

    async def read_stream(key: str, last: str):
        seen.append(await asyncio.to_thread(_probe))
        yield '1-0', {'event': 'probe', 'data': '{}'}
        await asyncio.Event().wait()

    return read_stream


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.parametrize('stream', STREAMS)
async def test_the_stream_body_runs_as_the_requests_member(db, signed_in, monkeypatch, stream, spec_version):
    """Catches: a body iterated outside the request's scope, which reads as the login role (it bypasses row-level
    security) or as nobody."""
    seen: list[dict] = []
    monkeypatch.setattr(events, 'read_stream', _probing_read_stream(seen))
    snapshot = live._snapshot  # Home and nav: the frames call it in a thread for each recompute

    def probing_snapshot(*args):
        seen.append(_probe())
        return snapshot(*args)

    monkeypatch.setattr(live, '_snapshot', probing_snapshot)

    async def probing_study(website, description, locale):
        seen.append(await asyncio.to_thread(_probe))
        yield PageRead('https://acme.it')
        await asyncio.Event().wait()

    monkeypatch.setattr(study_stream, 'study_company', probing_study)

    method, path, body = STREAMS[stream](db, signed_in)
    opened = await _open(method, path, body, spec_version)
    try:
        async with asyncio.timeout(5):
            while not seen:  # the chat sends `session` before it reads the job's stream
                await opened.frame(5)
        assert opened.status == 200
    finally:
        await opened.close()
    assert seen, 'the probe never ran inside the stream'
    assert {(r['role'], r['actor'], r['org']) for r in seen} == {('zenbree_app', signed_in.id, signed_in.org_id)}


def test_every_stream_route_is_listed_here():
    """A new stream route gets its removal and scope cases here."""
    from tests.test_scope_entry_points import _routes

    streams = {
        f'{",".join(sorted(r.methods))} {r.path}'
        for r in _routes(app.router.routes)
        if r.response_class is event_stream.EventStream
    }
    paths = {
        'POST /v1/chat',
        'GET /v1/chat/sessions/{session_id}/stream',
        'GET /v1/lists/{list_id}/stream',
        'GET /v1/lists/imports/{import_id}/stream',
        'GET /v1/home/stream',
        'GET /v1/home/nav/stream',
        'GET /v1/notifications/stream',
        'POST /v1/onboarding/profile',
    }
    assert len(STREAMS) == len(paths)
    assert streams == paths
