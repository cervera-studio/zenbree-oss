"""EventStream's member re-check, with no database: the re-check is replaced by a switch. The real one, on every
stream route: tests/test_stream_member_sql.py."""

import asyncio
import gc
import logging
from contextlib import aclosing

import psycopg
import pytest
from structlog.testing import capture_logs

from app.core import event_stream
from app.core import scope as scopes
from app.core.event_stream import GONE_FRAME, EventStream

A, U = '00000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-0000000000cc'
INTERVAL = 0.1


@pytest.fixture
def membership(monkeypatch):
    """``left['now'] = True`` removes the member; ``checks`` counts the re-checks."""
    state = {'left': False, 'checks': 0}

    def still_member(member: scopes.Member) -> None:
        assert member == scopes.Member(scopes.OrgId(A), scopes.UserId(U))
        state['checks'] += 1
        if state['left']:
            raise scopes.ActorGone(member)

    monkeypatch.setattr(event_stream, '_still_member', still_member)
    monkeypatch.setattr(event_stream, 'MEMBER_RECHECK_SECONDS', INTERVAL)
    return state


def _as_member(frames, **kwargs) -> EventStream:
    with scopes.acting_as(A, U):
        return EventStream(frames, **kwargs)


async def _read(stream: EventStream, until=None, seconds: float = 2) -> list[str]:
    out: list[str] = []
    async with asyncio.timeout(seconds), aclosing(stream.body_iterator) as frames:
        async for frame in frames:
            out.append(str(frame))
            if until and until(out):
                break
    return out


async def test_an_idle_stream_ends_within_one_interval_of_the_removal(membership):
    closed = asyncio.Event()

    async def idle():
        try:
            yield 'event: a\ndata: {}\n\n'
            await asyncio.Event().wait()
        finally:
            closed.set()

    stream = _as_member(idle())
    frames = stream.body_iterator
    assert await anext(frames) == 'event: a\ndata: {}\n\n'
    membership['left'] = True
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await asyncio.wait_for(anext(frames), 2) == GONE_FRAME
    assert loop.time() - started < 2 * INTERVAL
    with pytest.raises(StopAsyncIteration):
        await anext(frames)
    assert closed.is_set(), 'the frames were not closed: their Redis read or LISTEN would stay open'


async def test_a_stream_that_keeps_sending_is_cut_too(membership):
    async def busy():
        n = 0
        while True:
            n += 1
            yield f'event: n\ndata: {n}\n\n'
            await asyncio.sleep(INTERVAL / 20)

    async def leave_soon():
        await asyncio.sleep(INTERVAL)
        membership['left'] = True

    leaving = asyncio.create_task(leave_soon())
    out = await _read(_as_member(busy()))
    await leaving
    assert out[-1] == GONE_FRAME
    assert len(out) < 3 * 20


async def test_a_member_who_stays_is_rechecked_and_never_cut(membership):
    async def slow():
        for n in range(4):
            await asyncio.sleep(INTERVAL)
            yield f'event: n\ndata: {n}\n\n'

    out = await _read(_as_member(slow()))
    assert len(out) == 4 and GONE_FRAME not in out
    assert membership['checks'] >= 2


async def test_actor_gone_from_the_frames_own_checkout_ends_the_same_way(membership):
    async def refused():
        yield 'event: a\ndata: {}\n\n'
        raise scopes.ActorGone(scopes.Member(scopes.OrgId(A), scopes.UserId(U)))

    done = 'event: done\ndata: {}\n\n'
    assert await _read(_as_member(refused(), closing_frames=(done,))) == ['event: a\ndata: {}\n\n', GONE_FRAME, done]


async def test_another_failure_of_the_frames_still_reaches_the_response(membership):
    async def broken():
        yield 'event: a\ndata: {}\n\n'
        raise RuntimeError('redis down')

    with pytest.raises(RuntimeError, match='redis down'):
        await _read(_as_member(broken()))


async def test_a_database_error_in_the_recheck_cuts_the_stream_as_before(monkeypatch):
    """Not a removal: no ``error`` frame, which the bell, nav and Home would answer with a rejoin and the other streams
    with a stop. The response is cut, as the chat's and the list fill's own re-checks did on main, so each web
    consumer reconnects as after a network cut (the chat's reattach resumes on a cut, but not on a clean end)."""
    closed = asyncio.Event()

    def db_down(member: scopes.Member) -> None:
        raise psycopg.OperationalError('connection refused')

    monkeypatch.setattr(event_stream, '_still_member', db_down)
    monkeypatch.setattr(event_stream, 'MEMBER_RECHECK_SECONDS', INTERVAL)

    async def idle():
        try:
            yield 'event: a\ndata: {}\n\n'
            await asyncio.Event().wait()
        finally:
            closed.set()

    frames = _as_member(idle()).body_iterator
    assert await anext(frames) == 'event: a\ndata: {}\n\n'
    with pytest.raises(psycopg.OperationalError):
        await asyncio.wait_for(anext(frames), 2)
    assert closed.is_set()


async def test_the_frames_run_in_the_scope_the_stream_was_built_in(membership):
    """A route builds the stream inside its request's scope; the server reads it later, maybe from another task."""

    async def scope_of_body():
        yield str(scopes.current_or_none())
        await asyncio.to_thread(lambda: None)
        yield str(scopes.current_or_none())

    stream = _as_member(scope_of_body())
    assert scopes.current_or_none() is None
    member = str(scopes.Member(scopes.OrgId(A), scopes.UserId(U)))
    assert await _read(stream) == [member, member]


def test_with_no_scope_the_frames_pass_through_unchecked():
    async def frames():
        yield ''

    body = frames()
    assert EventStream(body).body_iterator is body


def test_a_stream_for_no_person_is_refused():
    async def frames():
        yield ''

    with scopes.acting_for(A, 'test'), pytest.raises(TypeError, match='Member scope'):
        EventStream(frames())


def _counting_frames(cleaned: dict[str, int], idle: bool = False):
    """Frames that never end (busy, or idle after the first), with an async close like a Redis client's; ``cleaned``
    counts the closes."""

    async def frames():
        try:
            n = 0
            while True:
                n += 1
                yield f'event: n\ndata: {n}\n\n'
                await (asyncio.Event().wait() if idle else asyncio.sleep(0.001))
        finally:
            cleaned['started'] += 1
            await asyncio.sleep(0.005)  # takes a few loop turns, as network I/O does
            cleaned['done'] += 1

    return frames()


def _pumps_destroyed_pending(caplog) -> list[str]:
    # Only ours: a garbage collection here can also collect what an earlier test left behind.
    return [m for r in caplog.records if 'destroyed but it is pending' in (m := r.getMessage()) and '_pump' in m]


_HTTP_SCOPE = {
    'type': 'http',
    'asgi': {'version': '3.0', 'spec_version': '2.3'},
    'http_version': '1.1',
    'method': 'GET',
    'scheme': 'http',
    'path': '/s',
    'raw_path': b'/s',
    'query_string': b'',
    'root_path': '',
    'headers': [(b'host', b't')],
    'client': ('127.0.0.1', 1),
    'server': ('t', 80),
}


async def _leave_mid_stream(app, stops_reading: bool, spec: str = '2.3', bound: float = 2) -> asyncio.Task[None]:
    """One request whose client leaves after the first frame, having stopped reading or not. Returns the request,
    which the caller asserts done: it is not awaited, since a shielded close also holds off this cancel."""
    left, got, asked = asyncio.Event(), asyncio.Event(), []

    async def receive():
        if not asked:
            asked.append(True)
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await left.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        if message['type'] == 'http.response.body' and message.get('body'):
            got.set()
            if stops_reading:
                await asyncio.Event().wait()

    scope = {**_HTTP_SCOPE, 'asgi': {'version': '3.0', 'spec_version': spec}}
    request = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(got.wait(), 2)
    await asyncio.sleep(INTERVAL / 5)
    left.set()
    await asyncio.wait({request}, timeout=INTERVAL)
    request.cancel()  # a send that never returns holds the request: the server gives up on it
    await asyncio.wait({request}, timeout=bound)
    if request.done() and not request.cancelled():
        request.exception()  # retrieved, so asyncio does not log it at garbage collection
    return request


def _streaming_app(make_frames, member: bool = True):
    """The app's shape: a stream route behind one ``@app.middleware('http')``."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.middleware('http')
    async def passthrough(request, call_next):
        return await call_next(request)

    @app.get('/s')
    async def stream():
        return _as_member(make_frames()) if member else EventStream(make_frames())

    return app


async def _new_pumps(before: set) -> set:
    await asyncio.sleep(0)  # a done pump leaves ``_PUMPS`` in a callback, one loop turn later
    return event_stream._PUMPS - before


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
@pytest.mark.parametrize('client', ['stops reading', 'idle'])
async def test_a_client_that_leaves_ends_the_pump_with_the_response(membership, caplog, client):
    """Uvicorn's spec 2.3 behind the app's ``@app.middleware('http')``. A client that stops reading and then leaves
    cancels the response inside ``send``, and Starlette never closes the body; an idle one cancels it inside the
    body's wait. Either way the frames' async ``finally`` (``await r.aclose()`` in events.read_stream) completes and
    no pump is left when the request ends, with no garbage collection."""
    caplog.set_level(logging.ERROR, logger='asyncio')
    cleaned = {'started': 0, 'done': 0}
    app = _streaming_app(lambda: _counting_frames(cleaned, idle=client == 'idle'))
    before = set(event_stream._PUMPS)
    for run in range(1, 11):
        assert (await _leave_mid_stream(app, stops_reading=client == 'stops reading')).done()
        assert cleaned == {'started': run, 'done': run}
        assert not await _new_pumps(before), 'a pump outlived its stream'
    assert not _pumps_destroyed_pending(caplog)


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
async def test_an_abandoned_stream_still_completes_the_frames_cleanup(membership, caplog):
    """A body that nobody closes is finalized by the garbage collector. The pump must not be collected with it while
    pending: asyncio would destroy it and the frames' async ``finally`` would never complete."""
    caplog.set_level(logging.ERROR, logger='asyncio')
    cleaned = {'started': 0, 'done': 0}
    before = set(event_stream._PUMPS)
    runs = 10
    for _ in range(runs):
        body = _as_member(_counting_frames(cleaned)).body_iterator
        await anext(body)
        await asyncio.sleep(0.005)  # the pump waits to hand over the next frame
        # As in Starlette's cancelled response, only a reference cycle (a traceback's frames) still holds the body.
        cycle: list[object] = [body]
        cycle.append(cycle)
        del body, cycle
    for _ in range(3):
        gc.collect()
        await asyncio.sleep(0.05)
    assert not _pumps_destroyed_pending(caplog)
    assert cleaned == {'started': runs, 'done': runs}
    assert not event_stream._PUMPS - before


def _frames_closed_by(close):
    """Busy frames whose ``finally`` awaits ``close``, as events.read_stream awaits ``r.aclose()``."""

    async def frames():
        try:
            n = 0
            while True:
                n += 1
                yield f'event: n\ndata: {n}\n\n'
                await asyncio.sleep(0.001)
        finally:
            await close()

    return frames()


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
@pytest.mark.parametrize('spec', ['2.3', '2.4'])
async def test_a_close_that_fails_after_the_client_left_still_ends_the_request(
    membership, monkeypatch, app_logging, spec
):
    """A client that stops reading leaves the pump on a full queue. The frames' close then raises (a network close on
    a dead peer): the pump must end with the cancel, not wait to hand the failure to a response that left."""
    monkeypatch.setattr(event_stream, 'CLOSE_SECONDS', 5.0)  # far past the bound: the request must not need it
    closes = []

    async def failing_close():
        await asyncio.sleep(0)
        closes.append(True)
        raise ConnectionError('peer gone')

    app = _streaming_app(lambda: _frames_closed_by(failing_close))
    before = set(event_stream._PUMPS)
    for run in range(1, 4):
        with capture_logs() as logs:
            request = await _leave_mid_stream(app, stops_reading=True, spec=spec, bound=1)
        assert request.done(), 'the response waits on a pump that cannot end'
        assert len(closes) == run
        assert not await _new_pumps(before), 'a pump outlived its stream'
        # Nobody reads a cancelled pump's error: the log is the only place it shows.
        assert [e['event'] for e in logs if e['log_level'] == 'warning'] == ['event_stream.frames_close_failed']


@pytest.mark.parametrize('spec', ['2.3', '2.4'])
async def test_a_close_that_never_returns_ends_the_request_at_the_deadline(membership, monkeypatch, app_logging, spec):
    """The async Redis client has no socket timeout, so its ``aclose`` on a dead peer can wait for good. The response
    ends at ``CLOSE_SECONDS``; the pump stays held in ``_PUMPS`` until the close returns, so it is never destroyed
    while pending."""
    monkeypatch.setattr(event_stream, 'CLOSE_SECONDS', 0.2)
    release, closed = asyncio.Event(), []

    async def stuck_close():
        await release.wait()
        closed.append(True)

    app = _streaming_app(lambda: _frames_closed_by(stuck_close))
    before = set(event_stream._PUMPS)
    try:
        with capture_logs() as logs:
            request = await _leave_mid_stream(app, stops_reading=True, spec=spec, bound=0.2 + 1)
        assert request.done(), 'the response waits for good on a close that never returns'
        warned = [e['event'] for e in logs if e['log_level'] == 'warning']
        assert warned == ['event_stream.pump_close_timed_out']
        stuck = await _new_pumps(before)
        assert len(stuck) == 1, 'a pending pump left _PUMPS: the garbage collector can destroy it'
    finally:
        release.set()  # also unblocks a hung request, so a failure here does not hang the event loop's teardown
    await asyncio.wait(stuck, timeout=1)
    assert closed == [True]
    assert not await _new_pumps(before)


async def test_a_stream_with_no_scope_bounds_its_close_too(app_logging, monkeypatch):
    """With no scope the response closes the frames itself, with no pump in between."""
    monkeypatch.setattr(event_stream, 'CLOSE_SECONDS', 0.2)
    release = asyncio.Event()
    app = _streaming_app(lambda: _frames_closed_by(release.wait), member=False)
    try:
        with capture_logs() as logs:
            request = await _leave_mid_stream(app, stops_reading=True, bound=0.2 + 1)
        assert request.done(), 'the response waits for good on a close that never returns'
        assert [e['event'] for e in logs if e['log_level'] == 'warning'] == ['event_stream.close_timed_out']
    finally:
        release.set()


async def test_frames_that_catch_the_cancel_and_return_still_end_the_pump(membership, monkeypatch, app_logging):
    """The response stops reading after the first frame, and the second one waits in the queue. The frames then catch
    the stream's cancel and return: the pump must not wait to hand over an end that nobody reads."""
    monkeypatch.setattr(event_stream, 'CLOSE_SECONDS', 5.0)  # far past the bound: the close must not need it
    caught = []

    async def frames():
        yield 'event: n\ndata: 1\n\n'
        yield 'event: n\ndata: 2\n\n'
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            caught.append(True)
            return

    before = set(event_stream._PUMPS)
    body = _as_member(frames()).body_iterator
    assert await anext(body) == 'event: n\ndata: 1\n\n'
    await asyncio.sleep(0.01)  # the pump queues the second frame and waits in the frames
    with capture_logs() as logs:
        async with asyncio.timeout(1):
            await body.aclose()
    assert caught == [True]
    assert not await _new_pumps(before), 'a pump outlived its stream'
    assert not [e['event'] for e in logs if e['log_level'] == 'warning']
