"""The event names of every SSE stream except chat (api/tests/test_sse_events.py), API against web.

The API side is read from the literal names at its emit sites (and the constants of the home streams); the web side
from the file that handles the frames. A name on one side only fails with the file to edit. Payload shapes are
checked for chat only: these streams are small and their payloads are typed by hand on both sides.
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.event_stream import GONE_EVENT
from app.home import live

ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class Emit:
    """Calls of ``callee`` in ``file`` (relative to api/app) whose argument number ``arg`` is the event name."""

    file: str
    callee: str
    arg: int = 0


@dataclass(frozen=True)
class Stream:
    name: str
    api: tuple[Emit, ...]
    web_file: str
    web_names: str  # a regex whose groups are the names the web file handles
    extra_api: frozenset[str] = frozenset()
    web_ignores: frozenset[str] = field(default_factory=frozenset)  # sent by the API, dropped by the web on purpose


STREAMS = (
    Stream(
        'GET /v1/lists/{id}/stream',
        (Emit('lists/ai_columns.py', 'publish'), Emit('lists/fill_stream.py', 'frame')),
        'web/lib/lists/fill-stream.ts',
        r"FILL_EVENT_TYPES = \[(.*?)\] as const|event === '(\w+)'",
    ),
    Stream(
        'GET /v1/lists/imports/{id}/stream',
        (Emit('lists/csv_run.py', '_publish', 1),),
        'web/lib/lists/csv-import.ts',
        r"event === '(\w+)'",
    ),
    Stream(
        'GET /v1/home/stream',
        (),
        'web/lib/home/store.ts',
        r"event [!=]== '(\w+)'",
        extra_api=frozenset({live.SNAPSHOT_EVENT, live.OVERVIEW_EVENT}),
        # followStream (lib/api/event-stream.ts) takes the `error` frame itself: Home passes `rejoin`, which refreshes
        # the page and reconnects into the user's remaining organization
        web_ignores=frozenset({GONE_EVENT}),
    ),
    Stream(
        'GET /v1/home/nav/stream',
        (),
        'web/lib/home/nav.ts',
        r"event [!=]== '(\w+)'",
        extra_api=frozenset({live.SNAPSHOT_EVENT, live.NAV_EVENT}),
    ),
    Stream(
        'GET /v1/notifications/stream',
        (Emit('notifications/service.py', 'publish', 1), Emit('notifications/routes.py', 'publish', 1)),
        'web/lib/notifications/feed.ts',
        r'NOTIFICATION_EVENT_TYPES = \[(.*?)\] as const',
        # followStream takes the `error` frame itself: the bell passes `rejoin`, as Home does
        web_ignores=frozenset({GONE_EVENT}),
    ),
    Stream(
        'POST /v1/onboarding/profile',
        (Emit('onboarding/study_stream.py', '_frame'),),
        'web/lib/onboarding/study.ts',
        r"event === '(\w+)'|^  (\w+): z\b",
    ),
)


def _api_names(stream: Stream) -> set[str]:
    names = {GONE_EVENT, *stream.extra_api}  # EventStream ends any stream with it when the member left
    for emit in stream.api:
        path = ROOT / 'api/app' / emit.file
        found = 0
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and len(node.args) > emit.arg):
                continue
            func = node.func
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
            arg = node.args[emit.arg]
            if callee == emit.callee and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
                found += 1
        assert found, f'{emit.callee}(...) with a literal event name not found in api/app/{emit.file}: update STREAMS'
    return names


def _web_names(stream: Stream) -> set[str]:
    path = ROOT / stream.web_file
    text = re.sub(r'//.*', '', path.read_text())  # a quoted word in a comment is not an event
    found: set[str] = set()
    for match in re.finditer(stream.web_names, text, re.S | re.M):
        for group in filter(None, match.groups()):
            found |= set(re.findall(r"'(\w+)'", group)) if "'" in group else {group}
    assert found, f'no event names found in {stream.web_file}: update STREAMS in api/tests/test_stream_events.py'
    return found


def test_every_stream_has_the_same_event_names_on_both_sides():
    problems = []
    for stream in STREAMS:
        api, web = _api_names(stream), _web_names(stream)
        files = ', '.join(sorted({f'api/app/{e.file}' for e in stream.api}) or ['api/app/home/live.py'])
        problems += [
            f'{stream.name}: {name} is sent by the API only; handle it in {stream.web_file}, or add it to web_ignores'
            for name in sorted(api - web - stream.web_ignores)
        ]
        problems += [
            f'{stream.name}: {name} is handled in {stream.web_file} but the API never sends it; drop it there or '
            f'send it from {files}'
            for name in sorted(web - api)
        ]
        problems += [
            f'{stream.name}: {name} is in web_ignores but the API does not send it; drop it from STREAMS'
            for name in sorted(stream.web_ignores - api)
        ]
    assert not problems, '\n'.join(problems)
