import ast
import re
from pathlib import Path

from app.agent.events import CHAT_EVENTS

ROOT = Path(__file__).parents[2]
TS_TYPES = ROOT / 'web/lib/chat/types.ts'


def _web_events() -> set[str]:
    block = re.search(r'SSE_EVENT_TYPES = \[(.*?)\] as const', TS_TYPES.read_text(), re.S)
    assert block, f'SSE_EVENT_TYPES not found in {TS_TYPES}: update this test if it moved'
    names = re.sub(r'//.*', '', block.group(1))  # a quoted word in a comment is not an event
    return set(re.findall(r"'([\w-]+)'", names))


def test_web_and_api_name_the_same_chat_events():
    web = _web_events()
    api = set(CHAT_EVENTS)
    assert len(api) == len(CHAT_EVENTS), 'CHAT_EVENTS in api/app/agent/events.py repeats a name'
    problems = [
        f'{name}: in the API only; add it to SSE_EVENT_TYPES in web/lib/chat/types.ts' for name in sorted(api - web)
    ]
    problems += [
        f'{name}: in the web only; add it to CHAT_EVENTS in api/app/agent/events.py or drop it from web/lib/chat/types.ts'
        for name in sorted(web - api)
    ]
    assert not problems, '\n'.join(problems)


def _emitted_names() -> dict[str, list[str]]:
    """Every literal event name passed to ``emit(...)`` or ``events.frame(...)`` in app/agent, with where.

    Not seen: a name held in a variable, a keyword argument (``emit(event=...)``) and a direct stream append.
    """
    found: dict[str, list[str]] = {}
    for path in sorted((ROOT / 'api/app/agent').rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and getattr(func.value, 'id', '') == 'webhooks':
                continue  # webhook events are another contract
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
            if callee in ('emit', 'frame') and isinstance(node.args[0].value, str):
                found.setdefault(node.args[0].value, []).append(f'{path.name}:{node.lineno}')
    return found


def test_the_agent_emits_only_listed_chat_events():
    unknown = {name: where for name, where in _emitted_names().items() if name not in CHAT_EVENTS}
    assert not unknown, (
        'add these to CHAT_EVENTS in api/app/agent/events.py and SSE_EVENT_TYPES in web/lib/chat/types.ts: '
        + ', '.join(f'{name} ({", ".join(where)})' for name, where in unknown.items())
    )


def test_every_listed_chat_event_is_emitted():
    emitted = _emitted_names()
    dead = [name for name in CHAT_EVENTS if name not in emitted]
    assert not dead, f'CHAT_EVENTS lists events nothing emits; remove from api/app/agent/events.py: {dead}'
