"""The one SSE response class, and the one place a stream re-checks its member (#323, #413).

A stream answers for minutes or hours on one request, but ``current_user`` reads the membership once, when it
starts. ``EventStream`` re-reads it every ``MEMBER_RECHECK_SECONDS`` on its own timer, whatever the stream sends, and
ends a stream whose member left or was banned with one ``error`` frame. tests/test_scope_entry_points.py (E4) keeps
every SSE route on this class.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from contextvars import Context, copy_context
from dataclasses import dataclass

import anyio
import structlog
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.core import scope as scopes

logger = structlog.get_logger()

# How often an open stream re-reads its member's membership: a member removed or banned stops receiving frames
# within this. ponytail: a timed re-check, not a push on removal (the web's afterRemoveMember could publish one if
# this must be instant).
MEMBER_RECHECK_SECONDS = 30.0

# How long a response waits for its frames' close (a Redis client's ``aclose``, a LISTEN's end) once it ends. A close
# on a live peer takes milliseconds; the async Redis client has no socket timeout, so a dead one could hold it for the
# operating system's TCP limits. 5 s is over twice the 2 s socket timeout of the app's other Redis clients.
CLOSE_SECONDS = 5.0

GONE_EVENT = 'error'  # every stream's web consumer handles it (tests/test_stream_events.py)
NOT_A_MEMBER = {'message': 'You are no longer a member of this organization.', 'code': 'AUTH.NOT_A_MEMBER'}
# No ``id:``: it comes from the stream's check, not from the stream's own entries, so a resume never asks for it.
GONE_FRAME = f'event: {GONE_EVENT}\ndata: {json.dumps(NOT_A_MEMBER)}\n\n'


class EventStream(StreamingResponse):
    """Pass it as the route's ``response_class`` too, so /openapi.json names ``text/event-stream`` instead of an
    empty JSON body (#369). Not FastAPI's ``EventSourceResponse``: that one makes the handler itself the
    generator.

    Build it inside the request's scope, as a route does: ``frames`` then runs in that scope for its whole life, and
    the stream re-checks that scope's ``Member``. ``closing_frames`` follow the ``error`` frame when the member left
    (the chat's ``done``, which its web store waits for)."""

    media_type = 'text/event-stream'

    def __init__(self, frames: AsyncGenerator[str], status_code: int = 200, closing_frames: Sequence[str] = ()) -> None:
        scope = scopes.current_or_none()
        if scope is not None and not isinstance(scope, scopes.Member):
            # A stream acts for the person who opened it: none acts for an organization or the system today. Such a
            # stream needs its own re-check rule, written here, before a route can build it.
            raise TypeError(f'a stream needs a Member scope to re-check, not {scope}')
        body = frames if scope is None else _guarded(frames, copy_context(), scope, tuple(closing_frames))
        self._body: AsyncGenerator[str] = body
        super().__init__(body, status_code, headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # A client that leaves while ``send`` waits (spec 2.3) cancels the response and Starlette never closes the
            # body: close it here, so the pump stops with the response and not at the next garbage collection.
            # Shielded: a cancel scope around the response (``@app.middleware('http')``) would cut the close too.
            with anyio.move_on_after(CLOSE_SECONDS, shield=True) as close:
                await self._body.aclose()
            if close.cancelled_caught:
                logger.warning('event_stream.close_timed_out', seconds=CLOSE_SECONDS)


# With no scope (a unit test that calls a route function directly, signed in without a database) the frames pass
# through unchecked: there is no member to re-check, and a checkout the frames make raises ScopeMissing.


# Every running pump. The event loop holds a task only weakly: when a client stops reading and then leaves, Starlette
# (spec 2.3, behind ``@app.middleware('http')``) never closes the body, and the garbage collector would destroy a pump
# nobody else holds while it is pending, cutting the frames' async ``finally`` (a Redis client's ``aclose``).
_PUMPS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class _Ended:
    error: BaseException | None = None


async def _pump(frames: AsyncGenerator[str], queue: asyncio.Queue[str | _Ended]) -> None:
    """Reads ``frames`` in one task, so a ContextVar it sets holds across its yields, and closes it here: a cancel
    while it waits on ``put`` still runs its ``finally`` (a Redis connection, a LISTEN subscription)."""
    ended = _Ended()
    try:
        async with aclosing(frames):
            async for frame in frames:
                await queue.put(frame)
    except (Exception, scopes.ActorGone) as e:  # ActorGone from the frames' own checkout, or a failure to re-raise
        if _stream_ended():
            # The frames' close failed after the stream ended. Nobody reads the queue any more: a ``put`` on a full one
            # would hold this task, and the response that waits for it, for good.
            logger.warning('event_stream.frames_close_failed', exc_info=e)
            raise asyncio.CancelledError from e
        ended = _Ended(e)
    if _stream_ended():  # the frames caught the stream's cancel and returned: the same full queue, nobody to read it
        return
    await queue.put(ended)


def _stream_ended() -> bool:
    """The stream cancelled this pump: it reads no more frames."""
    return (task := asyncio.current_task()) is not None and task.cancelling() > 0


def _still_member(member: scopes.Member) -> None:
    # The ids, not the ambient scope: the re-check runs in the response's task, whatever context the server gave it.
    with scopes.acting_as(member.org, member.user):
        scopes.still_live()


async def _guarded(
    frames: AsyncGenerator[str], context: Context, member: scopes.Member, closing_frames: tuple[str, ...]
) -> AsyncGenerator[str]:
    queue: asyncio.Queue[str | _Ended] = asyncio.Queue(maxsize=1)  # the frames run at most one frame ahead
    pump = asyncio.create_task(_pump(frames, queue), context=context)
    _PUMPS.add(pump)
    pump.add_done_callback(_PUMPS.discard)
    loop = asyncio.get_running_loop()
    # From the start, not from the last frame: a stream that keeps sending is cut within one interval too.
    due = loop.time() + MEMBER_RECHECK_SECONDS
    try:
        while True:
            if loop.time() >= due:
                await asyncio.to_thread(_still_member, member)
                due = loop.time() + MEMBER_RECHECK_SECONDS
            try:
                async with asyncio.timeout_at(due):
                    item = await queue.get()
            except TimeoutError:
                continue
            if isinstance(item, _Ended):
                if item.error is not None:
                    raise item.error
                return
            yield item
    except scopes.ActorGone:
        yield GONE_FRAME
        for frame in closing_frames:
            yield frame
    finally:
        pump.cancel()
        # Shielded: a client that leaves cancels the response's scope while this body waits for a frame, and that
        # scope cancels every await in it again, this one too; the pump would then outlive its stream.
        with anyio.CancelScope(shield=True):
            await asyncio.wait({pump}, timeout=CLOSE_SECONDS)
        if not pump.done():
            # The response ends anyway. ``_PUMPS`` holds the pump until its close returns, so it is not destroyed.
            logger.warning('event_stream.pump_close_timed_out', seconds=CLOSE_SECONDS)
