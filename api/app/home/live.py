"""LISTEN needs a session that stays on one server connection: behind a transaction-mode pooler (PgBouncer) no
notification would arrive. Compose connects to Postgres directly.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import aclosing, contextmanager
from datetime import UTC, datetime
from typing import Literal, NewType

import psycopg
import structlog
from pydantic import BaseModel

from app.agent import events
from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.core.db import get_conn
from app.core.event_stream import MEMBER_RECHECK_SECONDS
from app.home.overview import load_overview

logger = structlog.get_logger()

CHANNEL = 'home'
APPLICATION_NAME = 'zenbree-home-live'
QUIET_SECONDS = 0.4
MAX_WAIT_SECONDS = 1.5
PROBE_SECONDS = 30
RECONNECT_MIN_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30
MIN_REFRESH_SECONDS = 1.0
# The frame names of the two streams: web/lib/home/store.ts and nav.ts handle them (api/tests/test_stream_events.py).
SNAPSHOT_EVENT = 'snapshot'
OVERVIEW_EVENT = 'overview'
NAV_EVENT = 'nav'

Topic = NewType('Topic', str)
# (conn, user_id, org_id, project_id) -> what the stream sends, and when to recompute with no write.
Loader = Callable[[psycopg.Connection, str, str, str], tuple[BaseModel, datetime | None]]


def project_topic(project_id: str) -> Topic:
    return Topic(f'p:{project_id}')


def user_topic(user_id: str) -> Topic:
    return Topic(f'u:{user_id}')


class Hub:
    def __init__(self) -> None:
        self._subs: dict[Topic, set[asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None

    @contextmanager
    def subscribe(self, topics: set[Topic]) -> Iterator[asyncio.Event]:
        wake = asyncio.Event()
        for topic in topics:
            self._subs.setdefault(topic, set()).add(wake)
        if self._task is None or self._task.done():
            # One listener serves every subscriber in the process, so it must not inherit the scope of the request
            # that started it: a scoped call from it would act as that member, and raise ActorGone for everyone once
            # they leave. Its raw connection runs only LISTEN and SELECT 1 and reads no table, so it needs no scope.
            self._task = asyncio.create_task(self._listen(), context=contextvars.Context())
        try:
            yield wake
        finally:
            for topic in topics:
                self._subs[topic].discard(wake)
                if not self._subs[topic]:
                    del self._subs[topic]
            if not self._subs and self._task is not None:
                self._task.cancel()
                self._task = None

    def _wake_all(self) -> None:
        for wakes in self._subs.values():
            for wake in wakes:
                wake.set()

    async def _listen(self) -> None:
        delay = RECONNECT_MIN_SECONDS
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    settings.database_url, autocommit=True, application_name=APPLICATION_NAME
                ) as conn:
                    await conn.execute(f'LISTEN {CHANNEL}')
                    delay = RECONNECT_MIN_SECONDS
                    # NOTIFY has no replay: whatever committed before this LISTEN, every stream recomputes once.
                    self._wake_all()
                    while True:
                        async for n in conn.notifies(timeout=PROBE_SECONDS):
                            for wake in self._subs.get(Topic(n.payload), ()):
                                wake.set()
                        await conn.execute('SELECT 1')
            except psycopg.Error as e:
                logger.warning('home.listen_lost', error=str(e), retry_in=delay)
            except Exception:
                logger.exception('home.listen_crashed', retry_in=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)


HUB = Hub()


# The membership is EventStream's re-check (a removed or banned member's checkout raises ActorGone, which it turns
# into the stream's `error` frame); this one is the project, deleted while its Home streams.
def _has_project(conn, user: CurrentUser, project_id: str) -> bool:
    return conn.execute(
        'SELECT EXISTS (SELECT 1 FROM projects WHERE id = %s AND organization_id = %s) AS ok', (project_id, user.org_id)
    ).fetchone()['ok']


def _project_exists(user: CurrentUser, project_id: str) -> bool:
    with get_conn() as conn:
        return _has_project(conn, user, project_id)


def _snapshot(load: Loader, user: CurrentUser, project_id: str) -> tuple[BaseModel, datetime | None] | None:
    with get_conn() as conn:
        return load(conn, user.id, user.org_id, project_id) if _has_project(conn, user, project_id) else None


async def _next(wake: asyncio.Event, refresh_at: datetime | None) -> Literal['recompute', 'ping']:
    ping = events.PING_EVERY_MS / 1000
    refresh_in = _refresh_in(refresh_at)
    timeout = ping if refresh_in is None else min(ping, refresh_in)
    if await _woken(wake, timeout):
        await _settle(wake)
        return 'recompute'
    return 'recompute' if timeout == refresh_in else 'ping'


async def _woken(wake: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(wake.wait(), seconds)
    except TimeoutError:
        return False
    return True


async def _settle(wake: asyncio.Event) -> None:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    wake.clear()
    while (left := deadline - time.monotonic()) > 0 and await _woken(wake, min(QUIET_SECONDS, left)):
        wake.clear()


def _refresh_in(refresh_at: datetime | None) -> float | None:
    if refresh_at is None:
        return None
    return max(MIN_REFRESH_SECONDS, (refresh_at - datetime.now(UTC)).total_seconds())


async def frames(
    user: CurrentUser, project_id: str, load: Loader = load_overview, event: str = OVERVIEW_EVENT
) -> AsyncGenerator[str]:
    """``snapshot`` first, then ``event`` each time what ``load`` returns changes."""
    with HUB.subscribe({project_topic(project_id), user_topic(user.id)}) as wake:
        try:
            async with aclosing(_frames(wake, user, project_id, load, event)) as frames_:
                async for frame in frames_:
                    yield frame
        except psycopg.OperationalError as e:
            logger.warning('home.stream_db_down', error=str(e))


async def _frames(
    wake: asyncio.Event, user: CurrentUser, project_id: str, load: Loader, event: str
) -> AsyncGenerator[str]:
    sent: str | None = None
    while (snap := await asyncio.to_thread(_snapshot, load, user, project_id)) is not None:
        checked = time.monotonic()
        body = snap[0].model_dump_json()
        if body != sent:
            yield f'event: {SNAPSHOT_EVENT if sent is None else event}\ndata: {body}\n\n'
            sent = body
        while await _next(wake, snap[1]) == 'ping':
            if time.monotonic() - checked >= MEMBER_RECHECK_SECONDS:
                if not await asyncio.to_thread(_project_exists, user, project_id):
                    return
                checked = time.monotonic()
            yield ': ping\n\n'
