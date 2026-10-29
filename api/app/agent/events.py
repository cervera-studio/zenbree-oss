"""Job event bus: the worker XADDs SSE frames to a Redis stream, the HTTP
endpoint tails it. A stream (not pub/sub + list) because XREAD from an id is
replay-on-reconnect and live tail in one call, with no subscribe/replay race.

Entry ids are ``<seq>-0`` so the SSE ``id:`` is a plain integer, which is what
the FE sends back as ``Last-Event-ID``.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from datetime import timedelta
from functools import cache
from typing import cast

import redis
import redis.asyncio as aredis
import structlog

from app.agent.event_payloads import ChatEventPayloads
from app.core.config import settings

logger = structlog.get_logger()

# The chat stream's event names: the fields of ``ChatEventPayloads`` (api/app/agent/event_payloads.py), which also
# holds their data. web/lib/chat/types.ts `SSE_EVENT_TYPES` must hold the same names (api/tests/test_sse_events.py),
# and an `emit(...)` in this package may name only these.
CHAT_EVENTS = tuple(ChatEventPayloads.model_fields)

STREAM_TTL_SECONDS = 3600
PING_EVERY_MS = 15_000
_ENTRY_ID = re.compile(r'(\d{1,20})-(\d{1,20})')


def stream_key(job_id: str) -> str:
    return f'job:{job_id}:events'


def cancel_key(job_id: str) -> str:
    """The user's stop request for a job: one entry, which the worker reads with a blocking XREAD."""
    return f'job:{job_id}:cancel'


def request_cancel(job_id: str) -> bool:
    """Ask a job to stop at its next safe point; the worker then settles its credits. False when Redis is down."""
    return append(cancel_key(job_id), 'cancel', {}, maxlen=1, ttl=STREAM_TTL_SECONDS)


def cancel_requested(job_id: str) -> bool:
    """Whether the stop request is already there (a job stopped while queued). False when Redis is down."""
    try:
        return bool(redis_sync().exists(cancel_key(job_id)))
    except Exception as exc:
        logger.error('stream.cancel_check_failed', error_type=type(exc).__name__)
        return False


def frame(event: str, data: dict, event_id: int | None = None) -> str:
    head = f'event: {event}\n' + (f'id: {event_id}\n' if event_id else '')
    return f'{head}data: {json.dumps(data, default=str)}\n\n'


@cache
def redis_sync() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def redis_async() -> aredis.Redis:
    return aredis.Redis.from_url(settings.redis_url, decode_responses=True)


class Publisher:
    """What the loop calls as ``emit(event, data)``. One per job."""

    def __init__(self, job_id: str) -> None:
        self.key = stream_key(job_id)
        self.seq = 0

    def __call__(self, event: str, data: dict) -> None:
        self.seq += 1
        r = redis_sync()
        r.xadd(self.key, {'event': event, 'data': json.dumps(data, default=str)}, id=f'{self.seq}-0')
        r.expire(self.key, STREAM_TTL_SECONDS)


def _kind(key: str) -> str:
    """``user:<id>:notifications`` → ``notifications``: what the stream carries, without the id."""
    return key.rsplit(':', 1)[-1]


def append(key: str, event: str, data: dict, *, maxlen: int, ttl: int | timedelta) -> bool:
    """XADD one frame to a stream capped at ~``maxlen`` entries, expiring ``ttl`` after its last frame.
    Call it after the commit. Never raises: False (logged) when Redis is down, and a reader misses the
    frame until its next reload."""
    try:
        r = redis_sync()
        r.xadd(key, {'event': event, 'data': json.dumps(data, default=str)}, maxlen=maxlen, approximate=True)
        r.expire(key, ttl)
    except Exception as exc:
        logger.error('stream.publish_failed', stream=_kind(key), frame=event, error_type=type(exc).__name__)
        return False
    return True


def last_id(key: str) -> str | None:
    """The stream's newest entry id ('0-0' when there is none), the cursor a reader tails from after a load
    read later; None when Redis is unreachable."""
    try:
        last = cast(list, redis_sync().xrevrange(key, count=1))  # sync client
    except Exception as exc:
        logger.error('stream.cursor_failed', stream=_kind(key), error_type=type(exc).__name__)
        return None
    return last[0][0].decode() if last else '0-0'


def entry_id(value: str | None) -> str | None:
    """A Redis stream id XREAD accepts (two unsigned 64-bit parts), else None: checked before the 200."""
    m = _ENTRY_ID.fullmatch(value or '')
    return value if m and all(int(part) < 2**64 for part in m.groups()) else None


async def read_stream(key: str, last: str) -> AsyncGenerator[tuple[str, dict[str, str]] | None]:
    """Yield ``(entry_id, fields)`` after entry ``last``, forever; ``None`` on each idle ping interval.
    ``last`` must be a concrete id, not ``$``: an idle loop on ``$`` would skip entries added between reads."""
    r = redis_async()
    try:
        while True:
            res = await r.xread({key: last}, block=PING_EVERY_MS, count=100)
            if not res:
                yield None
                continue
            for entry_id, fields in res[0][1]:
                last = entry_id
                yield entry_id, fields
    finally:
        await r.aclose()


async def tail(job_id: str, after: int = 0) -> AsyncIterator[tuple[int, str, str] | None]:
    """Yield ``(seq, event, data_json)`` from ``after`` on; ``None`` on each idle
    ping interval. Stops after ``done``. The caller decides when idling means dead."""
    async with aclosing(read_stream(stream_key(job_id), f'{after}-0')) as entries:
        async for item in entries:
            if item is None:
                yield None
                continue
            entry_id, fields = item
            yield int(entry_id.split('-')[0]), fields['event'], fields['data']
            if fields['event'] == 'done':
                return
