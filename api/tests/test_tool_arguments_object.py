"""#718: tool arguments that parse to a string, list or number fail that call only (the provider boundary)."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent.loop import Turn, _parallelizable
from app.agent.tools.registry import ToolDef, ToolResult
from app.llm.base import ToolCall
from app.llm.openai_compat import OpenAICompatibleProvider

BAD = ['"acme"', '["acme"]', '7']


def _provider(handler) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(base_url='http://llm', headers={}, default_model='m')
    provider._active_client = lambda: httpx.AsyncClient(base_url='http://llm', transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return provider


def _sse(*chunks: dict) -> bytes:
    return ''.join(f'data: {json.dumps(c)}\n\n' for c in chunks).encode() + b'data: [DONE]\n\n'


@pytest.mark.parametrize('arguments', BAD)
async def test_non_stream_arguments_that_are_not_an_object_mark_the_call(arguments):
    call = {'id': 't1', 'function': {'name': 'search_companies', 'arguments': arguments}}
    body = {'choices': [{'message': {'tool_calls': [call]}, 'finish_reason': 'tool_calls'}], 'usage': {}}
    provider = _provider(lambda request: httpx.Response(200, json=body))
    response = await provider._chat(system='s', messages=[{'role': 'user', 'content': 'x'}])
    assert response.tool_calls == [
        ToolCall('t1', 'search_companies', {}, error='Tool arguments must be a JSON object.')
    ]


@pytest.mark.parametrize('arguments', BAD)
async def test_stream_arguments_that_are_not_an_object_mark_the_call(arguments):
    delta = {'tool_calls': [{'index': 0, 'id': 't1', 'function': {'name': 'search_companies', 'arguments': arguments}}]}
    content = _sse({'choices': [{'delta': delta}]})
    provider = _provider(lambda request: httpx.Response(200, content=content))
    events = [e async for e in provider._chat_stream(system='s', messages=[{'role': 'user', 'content': 'x'}])]
    ended = [e.tool_call for e in events if e.type == 'tool_end']
    assert ended == [ToolCall('t1', 'search_companies', {}, error='Tool arguments must be a JSON object.')]


async def test_a_bad_call_beside_a_good_parallel_search_fails_alone(monkeypatch):
    bad = ToolCall('b', 'search_people', {}, error='Tool arguments must be a JSON object.')
    good = ToolCall('g', 'search_companies', {'local_query': 'cafes'})
    assert not _parallelizable([good, bad])

    ran: list[str] = []

    async def handler(args, ctx):
        ran.append('handler')
        return ToolResult(ok=True)

    tool = ToolDef(name='search_people', description='', input_schema={}, handler=handler)
    monkeypatch.setattr('app.agent.loop.registry.get', lambda name: tool)
    monkeypatch.setattr(
        'app.agent.loop.PricedCall.of', lambda call, tool, session_id: SimpleNamespace(tool=tool, call=call)
    )
    events: list[tuple[str, dict]] = []
    finished: list[ToolResult] = []
    turn = Turn.__new__(Turn)  # a bare Turn: the seams _run_tool touches are stubbed, the guard under test is not
    turn.session_id = 's'
    turn.emit = lambda e, d: events.append((e, d))  # type: ignore[method-assign]
    turn._begin_tool = lambda call: events.append(('tool_start', {'tool_id': call.id}))  # type: ignore[method-assign]
    turn._preflight = lambda priced: None  # type: ignore[method-assign]
    turn._start_stage = lambda priced: None  # type: ignore[method-assign]
    turn._context = lambda priced: None  # type: ignore[method-assign]
    turn._finish_tool = lambda call, tool, result, called, priced: finished.append(result) or 'done'  # type: ignore[method-assign]

    assert await turn._run_tool(bad) == 'done'
    assert ran == []
    assert [(r.ok, r.error) for r in finished] == [(False, 'Tool arguments must be a JSON object.')]
    assert events == [('tool_start', {'tool_id': 'b'})]
