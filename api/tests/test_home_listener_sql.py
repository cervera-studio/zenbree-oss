"""The Home listener is one task per process, started by whichever stream subscribes first. It must run in no scope:
a copy of that member's scope would outlive their stream, and their removal would reach every other subscriber."""

import contextvars

import psycopg
import pytest

from app.core import scope as scopes
from app.core.event_stream import NOT_A_MEMBER
from app.home import live
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db
from tests.test_home_overview import _campaign
from tests.test_home_stream import listener_attached
from tests.test_home_stream import open_stream as open_stream

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: home listener SQL tests NOT run')


def _scope_vars() -> dict[str, object]:
    """The scope ContextVars this context holds: the scope itself and the open-connection count."""
    return {var.name: value for var, value in contextvars.copy_context().items() if var.name.startswith('zb_')}


async def test_the_listener_runs_in_no_scope_and_outlives_its_first_subscribers_removal(db, open_stream, monkeypatch):
    # A hub of its own, so the first subscriber below is the one that starts the listener.
    monkeypatch.setattr(live, 'HUB', live.Hub())
    seen: list[tuple[scopes.Scope | None, dict[str, object]]] = []
    connect = psycopg.AsyncConnection.connect

    async def recording(*args, **kwargs):
        seen.append((scopes.current_or_none(), _scope_vars()))
        return await connect(*args, **kwargs)

    monkeypatch.setattr(psycopg.AsyncConnection, 'connect', recording)

    m, other = _user(db), _user(db)
    first = await open_stream(m)
    second = await open_stream(other)
    listener = live.HUB._task
    await first.next()
    await second.next()
    assert seen == [(None, {})], 'the listener inherited the scope of the stream that started it'

    db.execute('DELETE FROM member WHERE "userId" = %s', (m,))
    assert await first.next() == ('error', NOT_A_MEMBER)
    assert await first.next() is None

    killed = db.execute(
        'SELECT pg_terminate_backend(pid), pid FROM pg_stat_activity'
        ' WHERE application_name = %s AND datname = current_database()',
        (live.APPLICATION_NAME,),
    ).fetchall()
    await listener_attached(db, frozenset(r['pid'] for r in killed))
    _campaign(db, other, 'Live', waiting=1)
    event, body = await second.next()
    assert (event, body['approvals']['total']) == ('overview', 1)
    assert live.HUB._task is listener, 'the listener was replaced, so it no longer shows the first one survived'
    assert seen == [(None, {}), (None, {})], 'the reconnect after the removal ran in a scope'
