"""The bell's SSE stream (#242): scoped to the caller, replays after Last-Event-ID, marks Zenbree open."""

import asyncio
import json
from contextlib import aclosing

from structlog.testing import capture_logs

from app.agent import events
from app.auth.middleware import CurrentUser
from app.notifications import routes
from app.notifications import service as n


async def _frames(uid: str, count: int, last_event_id: str | None = None) -> list[str]:
    resp = await routes.stream(user=CurrentUser(id=uid, org_id='org-1'), last_event_id=last_event_id)

    async def read() -> list[str]:
        out: list[str] = []
        async with aclosing(resp.body_iterator) as it:
            async for chunk in it:
                out.append(str(chunk))
                if len(out) == count:
                    return out
        return out

    return await asyncio.wait_for(read(), 5)


def _event(frame: str) -> tuple[str, str, dict]:
    head, entry_id, data = frame.strip().split('\n')
    return head.removeprefix('event: '), entry_id.removeprefix('id: '), json.loads(data.removeprefix('data: '))


async def test_only_the_callers_stream_replayed_after_last_event_id():
    n.publish('other', 'read', {'ids': None, 'unread': 1})
    for i in range(3):
        n.publish('me', 'read', {'ids': [f'n{i}'], 'unread': i + 1})
    frames = [_event(f) for f in await _frames('me', 3, last_event_id='0-0')]
    assert [(e, d['ids']) for e, _, d in frames] == [('read', ['n0']), ('read', ['n1']), ('read', ['n2'])]
    replay = [_event(f) for f in await _frames('me', 2, last_event_id=frames[0][1])]
    assert [d['ids'] for _, _, d in replay] == [['n1'], ['n2']]


async def test_without_last_event_id_it_starts_now_and_keeps_zenbree_open(monkeypatch):
    monkeypatch.setattr(events, 'PING_EVERY_MS', 50)
    n.publish('me', 'read', {'ids': None, 'unread': 0})  # before the connection: not replayed
    assert not n.tab_open('me')
    assert await _frames('me', 1, last_event_id='not-an-id') == [': ping\n\n']
    assert n.tab_open('me')


async def test_an_out_of_range_last_event_id_starts_now_instead_of_failing_mid_stream(monkeypatch):
    monkeypatch.setattr(events, 'PING_EVERY_MS', 50)
    n.publish('me', 'read', {'ids': None, 'unread': 0})
    for bad in ('99999999999999999999999-0', f'{2**64}-0', '1-18446744073709551616'):
        assert events.entry_id(bad) is None  # real Redis rejects these in XREAD, after the 200 (fakeredis doesn't)
        assert await _frames('me', 1, last_event_id=bad) == [': ping\n\n']
    assert events.entry_id(f'{2**64 - 1}-{2**64 - 1}')


def test_redis_down_logs_and_never_raises(monkeypatch, app_logging):
    """Each helper alone, under the app's structlog config: a bad log call (``event=`` is structlog's
    positional) raises TypeError at the call site, and ``deliver``'s own try would hide it."""

    def down():
        raise ConnectionError('redis down')

    monkeypatch.setattr(events, 'redis_sync', down)
    with capture_logs() as logs:
        n.publish('me', 'read', {'ids': None, 'unread': 0})
        n.mark_open('me')
        assert n.stream_cursor('me') is None
        assert not n.tab_open('me')
    assert [entry['event'] for entry in logs] == [
        'stream.publish_failed',
        'notification.mark_open_failed',
        'stream.cursor_failed',
        'notification.tab_open_failed',
    ]
