"""GET /v1/chat/sessions/{id}/stream re-attaches to the running job and replays its Redis stream after Last-Event-ID."""

import asyncio
import contextlib
import json
import uuid
from contextlib import aclosing

import pytest
from fastapi import HTTPException

from app.agent import events, routes
from app.auth.middleware import CurrentUser

SESSION = uuid.uuid4()
JOB = str(uuid.uuid4())
USER = CurrentUser(id='u-1', org_id='org-1')


class _Rows:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=()):
        return self

    def fetchone(self):
        return self.row


@pytest.fixture
def running_job(monkeypatch):
    """The live-job lookup finds JOB, so the route re-attaches; the status it reports to the idle check is settable."""
    state = {'status': 'running'}
    monkeypatch.setattr(routes, 'get_conn', lambda: contextlib.nullcontext(_Rows({'id': JOB})))
    monkeypatch.setattr(routes, '_job_status', lambda job_id: state['status'])
    monkeypatch.setattr(events, 'PING_EVERY_MS', 50)
    return state


def _emit(*names: str) -> None:
    publish = events.Publisher(JOB)
    for name in names:
        publish(name, {'n': name})


async def _read(last_event_id: str | None) -> list[str]:
    resp = routes.reattach(session_id=SESSION, user=USER, last_event_id=last_event_id)
    try:
        # A stream that should have ended keeps pinging: fail the test instead of hanging the run.
        async with asyncio.timeout(5), aclosing(resp.body_iterator) as it:
            return [str(chunk) async for chunk in it]
    except TimeoutError:
        pytest.fail('the stream did not end: it should stop after `done` or when the job is terminal')


def _events(chunks: list[str]) -> list[tuple[str, str | None, dict]]:
    """(event, id, data) of each frame after the leading `session` frame."""
    parsed = []
    for chunk in chunks[1:]:
        lines = dict(line.split(': ', 1) for line in chunk.strip().split('\n'))
        parsed.append((lines['event'], lines.get('id'), json.loads(lines['data'])))
    return parsed


async def test_reconnect_with_last_event_id_gets_exactly_the_later_frames_in_order(running_job):
    _emit('step', 'tool_call', 'tool_result', 'message_saved', 'done')
    chunks = await _read('2')
    assert chunks[0].startswith('event: session\n')
    assert [(e, i) for e, i, _ in _events(chunks)] == [
        ('tool_result', '3'),
        ('message_saved', '4'),
        ('done', '5'),
    ]


async def test_without_last_event_id_the_whole_stream_replays(running_job):
    _emit('step', 'tool_call', 'done')
    assert [(e, i) for e, i, _ in _events(await _read(None))] == [('step', '1'), ('tool_call', '2'), ('done', '3')]


async def test_a_non_numeric_last_event_id_replays_everything(running_job):
    _emit('step', 'done')
    assert [e for e, _, _ in _events(await _read('not-an-id'))] == ['step', 'done']


async def test_a_stream_already_at_done_ends_after_it_without_waiting(running_job):
    _emit('step', 'done')
    assert [e for e, _, _ in _events(await _read('1'))] == ['done']
    running_job['status'] = 'succeeded'  # past `done` nothing more comes: the idle check ends the stream
    assert _events(await _read('2')) == []


async def test_a_job_that_finished_without_done_ends_on_the_next_idle_ping(running_job):
    _emit('step')
    running_job['status'] = 'failed'  # the worker died before `done`
    assert [e for e, _, _ in _events(await _read('1'))] == []


async def test_a_finished_job_is_not_found(monkeypatch):
    monkeypatch.setattr(routes, 'get_conn', lambda: contextlib.nullcontext(_Rows(None)))
    with pytest.raises(HTTPException) as err:
        routes.reattach(session_id=SESSION, user=USER, last_event_id='1')
    assert err.value.status_code == 404
