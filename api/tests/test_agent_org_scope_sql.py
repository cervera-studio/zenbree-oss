"""The turn acts as the member who started it, in the job's organization (PR 7 of the organization-scoping run).

The API queues ``run_job`` with the request's ``Member`` in a Celery header (app/core/tasks.py); the worker enters it
before its first database call, so row-level security holds every tool to the chat's organization, and a member
removed or banned mid-turn gets ``ActorGone`` at the next one. The end of the turn runs as ``ForOrg`` of the job's
organization (app/agent/turn_scope.py). The #701 cases go green from scoping alone: nothing in
app/agent/tools/handlers/scheduler.py or app/scheduling/ changed.

The seed: U is a member of A, where the chat runs, and of B. Each organization has a list and a saved search of U.
"""

import asyncio
import contextlib
import contextvars
import dataclasses
import json
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from celery.worker.request import Request
from kombu.message import Message
from kombu.serialization import dumps
from structlog.testing import capture_logs

from app import worker
from app.agent import events, loop
from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.paid_call import current_user_id, paid_call
from app.core import db as core_db
from app.core import scope as scopes
from app.core.config import settings
from app.core.errors import ok
from app.llm import metering, router
from app.llm.base import LLMEvent, TokenUsage, ToolCall
from tests.test_agent import FakeProvider, _job, _org, _run, _user, needs_db
from tests.test_agent import db as db
from tests.test_scope_sql import _search

pytestmark = needs_db

_PROBE_SQL = 'SELECT current_user AS who, zb.org()::text AS org, zb.actor()::text AS actor'
# The contract (docs/contracts-m1.md, Server-sent events), written out rather than imported from
# app/agent/turn_scope.py: the file then also runs over main's code, where the tests fail on what the turn did.
NOT_A_MEMBER = {'message': 'You are no longer a member of this organization.', 'code': 'AUTH.NOT_A_MEMBER'}
GONE_ERROR = 'member removed or banned'


# ── the world ──────────────────────────────────────────────────────────────


@pytest.fixture
def world(db):
    """U in A (their first organization: the chat's) and in B, with a saved search on a list in each."""
    u = _user(db, credits=50)
    a = _org(db, u)
    b = db.execute(
        "INSERT INTO organization (name, slug, \"createdAt\") VALUES ('org-b', 'org-b', now()) RETURNING id::text"
    ).fetchone()['id']
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'admin\', now())', (b, u)
    )
    (list_a, search_a), (list_b, search_b) = _search(db, a, u), _search(db, b, u)
    sid, jid = _job(db, u, 'agenzie a Milano')
    return {
        'u': u,
        'a': a,
        'b': b,
        'list_a': str(list_a),
        'list_b': str(list_b),
        'search_a': str(search_a),
        'search_b': str(search_b),
        'sid': sid,
        'jid': jid,
    }


def _superuser(sql: str, params: tuple) -> None:
    """What the web's Better Auth writes as the login role: a member row removed, a user banned."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(sql, params)


def _leave(world: dict, how: str) -> None:
    if how == 'removed':
        _superuser('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (world['u'], world['a']))
    else:
        _superuser('UPDATE "user" SET banned = true WHERE id = %s', (world['u'],))


# ── the worker path: the message the API queues, run as the worker runs it ─


class _Producer:
    """Takes the message Celery would send to the broker."""

    class connection:  # the attribute Celery reads from a producer
        _reraise_as_library_errors = staticmethod(contextlib.nullcontext)

    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    def publish(self, body, **options) -> None:
        self.sent.append((body, options['headers']))


def _queued(world: dict) -> tuple[tuple, dict]:
    """``run_job`` as ``routes._enqueue`` queues it, inside the request's scope."""
    producer = _Producer()
    with scopes.acting_as(world['a'], world['u']):
        worker.run_job.apply_async((world['jid'],), producer=producer)
    return producer.sent[0]


def _delivered(message: tuple[tuple, dict]) -> None:
    """The worker's side: a Celery request built from the message, which hands the task its headers."""
    body, headers = message
    content_type, encoding, payload = dumps(body, serializer='json')
    raw = Message(
        body=payload,
        headers=headers,
        content_type=content_type,
        content_encoding=encoding,
        delivery_info={'exchange': '', 'routing_key': 'celery'},
        properties={},
    )
    Request(raw, app=worker.celery_app, task=worker.run_job).execute()


@pytest.fixture
def turn(monkeypatch):
    """The provider and the frames of the next ``run_job`` the worker runs."""
    state: dict = {'frames': []}
    monkeypatch.setattr(router, 'get_chat_provider', lambda: state['provider'])
    monkeypatch.setattr(events, 'Publisher', lambda job_id: lambda e, d: state['frames'].append((e, d)))
    return state


@pytest.fixture
def checkouts(monkeypatch):
    """Every checkout: its scope and what the database says right after it entered (role, zb.org(), zb.actor()).
    ``'end'`` marks where the turn's end begins."""
    seen: list = []
    entered = core_db.entered

    @contextlib.contextmanager
    def spy(conn, scope):
        with entered(conn, scope) as role:
            seen.append((scope, *conn.execute(_PROBE_SQL).fetchone().values()))
            yield role

    end = loop.Turn._end

    def marked(self, *args):
        seen.append('end')
        return end(self, *args)

    monkeypatch.setattr(core_db, 'entered', spy)
    monkeypatch.setattr(loop.Turn, '_end', marked)
    return seen


def _tool(monkeypatch, name: str, handler) -> None:
    monkeypatch.setitem(registry._REGISTRY, name, ToolDef(name, 'test', {'type': 'object', 'properties': {}}, handler))


async def _probe(args, ctx) -> ToolResult:
    """What a tool's database call runs as, from a thread like the real handlers."""

    def read() -> dict:
        with core_db.get_conn() as conn:
            return conn.execute(_PROBE_SQL).fetchone()

    return ToolResult(ok=True, data=await asyncio.to_thread(read))


def _results(frames) -> dict[str, dict]:
    return {d['tool_name']: d for e, d in frames if e == 'tool_result'}


# ── #701: the scheduler tools ──────────────────────────────────────────────


def test_the_schedule_tools_stay_in_the_chats_organization(db, world, turn):
    """#701. From a chat in A: list_schedules returns only A's search; cancel_schedule on B's id answers "Saved
    search not found" and B's search stays enabled; schedule_check with B's list fails. On main all three cross into
    B: the worker ran the turn as the login role, and the tools filter by the user only."""
    schedule = {'name': 'Daily', 'tool_name': 'search_companies', 'tool_input': {}, 'list_id': world['list_b']}
    calls = [
        ToolCall('l1', 'list_schedules', {}),
        ToolCall('c1', 'cancel_schedule', {'schedule_id': world['search_b']}),
        ToolCall('s1', 'schedule_check', {**schedule, 'cadence': 'daily'}),
    ]
    turn['provider'] = FakeProvider([('', calls), ('Done.', [])])

    message = _queued(world)
    _delivered(message)

    results = _results(turn['frames'])
    listed = json.loads(results['list_schedules']['result_preview'])['schedules']
    assert [s['id'] for s in listed] == [world['search_a']]
    assert results['cancel_schedule']['status'] == 'failed'
    assert json.loads(results['cancel_schedule']['result_preview']) == {'error': 'Saved search not found'}
    assert results['schedule_check']['status'] == 'failed'
    searches = db.execute('SELECT id::text, enabled FROM saved_searches WHERE list_id = %s', (world['list_b'],))
    assert searches.fetchall() == [{'id': world['search_b'], 'enabled': True}]
    assert message[1]['zb_scope'] == {'org': world['a'], 'user': world['u']}


# ── the scope of the body and of the end ───────────────────────────────────


def test_the_turn_acts_as_its_member_and_ends_as_its_organization(world, turn, checkouts, monkeypatch):
    """Every checkout of the body, a tool's in its thread too, runs as zenbree_app under Member(A, U); every one of
    the end under ForOrg(A)."""
    _tool(monkeypatch, 'probe', _probe)
    turn['provider'] = FakeProvider([('', [ToolCall('p1', 'probe', {})]), ('Done.', [])])

    _delivered(_queued(world))

    assert json.loads(_results(turn['frames'])['probe']['result_preview']) == {
        'who': 'zenbree_app',
        'org': world['a'],
        'actor': world['u'],
    }
    body, end = checkouts[: checkouts.index('end')], checkouts[checkouts.index('end') + 1 :]
    member = scopes.Member(scopes.OrgId(world['a']), scopes.UserId(world['u']))
    assert body and set(body) == {(member, 'zenbree_app', world['a'], world['u'])}
    assert end and set(end) == {(scopes.ForOrg(scopes.OrgId(world['a']), 'turn end'), 'zenbree_app', world['a'], None)}
    assert [e for e, _ in turn['frames']][-2:] == ['message_saved', 'done']


def test_a_message_with_no_scope_header_runs_nothing(db, world, turn, checkouts, app_logging):
    """A ``run_job`` with no scope header (queued by an image from before the header) is dropped with an error line:
    the worker never reads the job to guess whose it is. The job waits for the stale-job sweep."""
    turn['provider'] = FakeProvider([('unused', [])])
    body, headers = _queued(world)

    with capture_logs() as logs:
        _delivered((body, {k: v for k, v in headers.items() if k != 'zb_scope'}))

    assert (turn['frames'], checkouts) == ([], [])
    assert [e['event'] for e in logs if e['log_level'] == 'error'] == ['task.no_scope_header']
    assert db.execute('SELECT status FROM jobs WHERE id = %s', (world['jid'],)).fetchone()['status'] == 'queued'


def test_a_message_naming_a_teammate_runs_nothing(db, world, turn):
    """A forged ``run_job`` header names V, another member of A. The job is U's, so V's scope claims nothing: the turn
    never runs, and the job stays queued until the stale-job sweep fails it."""
    v = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (world['a'], v),
    )
    turn['provider'] = FakeProvider([('unused', [])])
    body, headers = _queued(world)

    _delivered((body, {**headers, 'zb_scope': {'org': world['a'], 'user': v}}))

    assert turn['frames'] == []
    assert db.execute('SELECT status FROM jobs WHERE id = %s', (world['jid'],)).fetchone()['status'] == 'queued'


def test_run_job_is_queued_only_as_a_member(world):
    """The header names the enqueue site: a ``run_job`` queued outside a ``Member`` raises at ``.delay``."""
    with pytest.raises(scopes.ScopeMissing):
        worker.run_job.apply_async((world['jid'],), producer=_Producer())
    with scopes.acting_for(world['a'], 'test'), pytest.raises(scopes.ScopeMissing, match='Member scope'):
        worker.run_job.apply_async((world['jid'],), producer=_Producer())


# ── a member who leaves ────────────────────────────────────────────────────


def _ended(db, world) -> dict:
    """The job, the assistant message and the credits after the turn."""
    return db.execute(
        'SELECT j.status, j.error, m.status AS message, m.terminal_code,'
        " (SELECT count(*) FROM credit_transactions WHERE status IN ('reserved', 'settling')) AS held,"
        ' (SELECT credits_remaining FROM user_credits WHERE organization_id = j.organization_id) AS balance'
        " FROM jobs j LEFT JOIN chat_messages m ON m.job_id = j.id AND m.role = 'assistant' WHERE j.id = %s",
        (world['jid'],),
    ).fetchone()


GONE = {
    'status': 'failed',
    'error': GONE_ERROR,
    'message': 'failed',
    'terminal_code': None,
    'held': 0,
    'balance': 50,
}


@pytest.mark.parametrize('how', ['removed', 'banned'])
def test_a_member_who_leaves_between_two_tool_calls_ends_the_turn(db, world, turn, monkeypatch, how):
    """U leaves A while the first tool runs. The next checkout ends the turn: ``remember`` never runs, the hold
    settles, the job ends failed and the stream gets AUTH.NOT_A_MEMBER. No tool result reports a crash. On main the
    worker runs the turn as the login role, and ``remember`` writes after the removal."""

    async def leave(args, ctx) -> ToolResult:
        _leave(world, how)
        return ToolResult(ok=True, data={'left': True})

    _tool(monkeypatch, 'leave', leave)
    calls = [ToolCall('l1', 'leave', {}), ToolCall('r1', 'remember', {'content': 'Sells to agencies'})]
    turn['provider'] = FakeProvider([('', calls), ('unused', [])])

    _delivered(_queued(world))

    events_ = turn['frames']
    assert [d['tool_id'] for e, d in events_ if e == 'tool_start'] == ['l1']
    assert not [d for e, d in events_ if e == 'tool_result']
    assert [d for e, d in events_ if e == 'error'] == [NOT_A_MEMBER]
    assert [e for e, _ in events_][-3:] == ['error', 'message_saved', 'done']
    assert db.execute('SELECT body FROM user_memory_doc WHERE user_id = %s', (world['u'],)).fetchone() is None
    assert _ended(db, world) == GONE


async def test_a_member_removed_during_parallel_searches_stops_the_other_one(db, world, monkeypatch):
    """Two searches run at once; U is removed during one, whose next checkout raises ActorGone. The other search is
    cancelled before its vendor call: a turn whose member left calls no vendor. On main, and with the gather of
    ``return_exceptions=True`` the loop had, it runs to its end."""
    vendor_calls: list[str] = []
    cancelled: list[str] = []

    async def removed(args, ctx) -> ToolResult:
        _leave(world, 'removed')
        await asyncio.to_thread(scopes.still_live)
        return ToolResult(ok=True, data={})

    async def slow(args, ctx) -> ToolResult:
        try:
            await asyncio.sleep(10)  # cut off here, when the other search finds the member gone
        except asyncio.CancelledError:
            cancelled.append('companies')
            raise
        vendor_calls.append('companies')
        return ToolResult(ok=True, data={})

    for name, handler in (('search_people', removed), ('search_companies', slow)):
        monkeypatch.setitem(registry._REGISTRY, name, dataclasses.replace(registry._REGISTRY[name], handler=handler))
    calls = [ToolCall('p1', 'search_people', {'titles': ['CEO']}), ToolCall('c1', 'search_companies', {})]

    events_, _ = await _run(world['jid'], [('', calls), ('unused', [])])

    assert (vendor_calls, cancelled) == ([], ['companies'])
    assert [d for e, d in events_ if e == 'error'] == [NOT_A_MEMBER]
    assert _ended(db, world) == GONE


def test_a_turn_queued_before_its_member_left_does_nothing(db, world, turn):
    """U posts in A and is removed before a worker takes the turn. The claim is its first checkout: no step runs, no
    message is written, no credit is held, and the job ends failed."""
    turn['provider'] = FakeProvider([('', [ToolCall('r1', 'remember', {'content': 'x'})]), ('Done.', [])])
    message = _queued(world)
    _leave(world, 'removed')

    _delivered(message)

    assert turn['frames'] == []
    assert _ended(db, world) == {**GONE, 'message': None}
    assert db.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0


# ── cost rows ──────────────────────────────────────────────────────────────


async def test_a_paid_calls_cost_is_recorded_after_its_member_left(db, world, checkouts, monkeypatch):
    """The vendor answered and charged while U was removed: its ``vendor_calls`` row is still written, as ForOrg(A)
    (the table has no organization column, design question 6). Then the turn ends."""
    monkeypatch.setattr('app.billing.paid_call.get_conn', core_db.get_conn)  # the conftest default is a fake

    async def vendor():
        _leave(world, 'removed')
        return ok({'id': 'p1'})

    async def matched(args, ctx) -> ToolResult:
        current_user_id.set(ctx.user_id)
        await paid_call('apollo.match_person', vendor)
        return ToolResult(ok=True, data={})

    _tool(monkeypatch, 'matched', matched)

    events_, _ = await _run(world['jid'], [('', [ToolCall('m1', 'matched', {})]), ('unused', [])])

    rows = db.execute('SELECT user_id::text, operation FROM vendor_calls').fetchall()
    assert rows == [{'user_id': world['u'], 'operation': 'apollo.match_person'}]
    cost_record = scopes.ForOrg(scopes.OrgId(world['a']), 'cost record')
    assert (cost_record, 'zenbree_app', world['a'], None) in checkouts
    assert [d for e, d in events_ if e == 'error'] == [NOT_A_MEMBER]


def _metered_in(context: contextvars.Context, user: str) -> None:
    """One metered model call of ``user``, in ``context``: a metering context set here would reach later tests."""
    usage = TokenUsage(input_tokens=10, output_tokens=5)

    def call() -> None:
        metering.set_metering_context(metering.MeteringContext(user_id=user, endpoint='test'))
        metering.record_usage(provider='deepseek', model='m', usage=usage, cost_usd=0.01, latency_ms=3)

    context.run(call)


def test_a_usage_metered_while_the_caller_holds_a_connection_is_stored(db, world, monkeypatch):
    """A model call metered inside the caller's ``with get_conn()``. The write runs on the metering pool in a copy of
    the caller's context, and enters the cost scope there: that copy must not count the caller's connection as its
    own, or the entry raises ``ScopeChangeWhileOpen`` and the row is lost."""
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(metering, '_write_pool', pool)

    def holding() -> None:
        with scopes.acting_as(world['a'], world['u']), core_db.get_conn():
            _metered_in(contextvars.copy_context(), world['u'])

    contextvars.copy_context().run(holding)
    pool.shutdown(wait=True)

    rows = db.execute('SELECT user_id::text, model FROM llm_usage').fetchall()
    assert rows == [{'user_id': world['u'], 'model': 'm'}]


def test_a_usage_row_lost_to_a_scope_error_is_logged_as_an_error(world, monkeypatch):
    """A scope error derives from ``BaseException``. Nobody reads the metering pool's future, so a write that
    lets one through loses the row with no line in the log."""
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(metering, '_write_pool', pool)

    def missing(*_a, **_k):
        raise scopes.ScopeMissing('no scope')

    monkeypatch.setattr(core_db, 'get_conn', missing)
    with capture_logs() as logs:
        _metered_in(contextvars.copy_context(), world['u'])
        pool.shutdown(wait=True)

    lost = [(e['event'], e['error_type']) for e in logs if e['log_level'] == 'error']
    assert lost == [('llm_metering.write_failed', 'ScopeMissing')]


async def test_a_model_calls_usage_is_recorded_after_its_member_left(db, world, checkouts, monkeypatch):
    """The model answered while U was removed: its ``llm_usage`` row, written on the metering pool, still lands, as
    ForOrg(A). The pool runs it in the turn's context, not the thread's empty one."""
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(metering, '_write_pool', pool)

    class Metered(FakeProvider):
        async def chat_stream(self, **kwargs):
            _leave(world, 'removed')
            usage = TokenUsage(input_tokens=10, output_tokens=5)
            metering.record_usage(provider='deepseek', model='m', usage=usage, cost_usd=0.01, latency_ms=3)
            yield LLMEvent(type='text_delta', text='Fatto.')
            yield LLMEvent(type='done')

    from app.agent.loop import run_job
    from tests.test_agent import as_starter

    frames: list = []
    with as_starter(world['jid']):
        await run_job(world['jid'], provider=Metered([]), emit=lambda e, d: frames.append((e, d)))
    pool.shutdown(wait=True)

    rows = db.execute('SELECT user_id::text, session_id::text, model FROM llm_usage').fetchall()
    assert rows == [{'user_id': world['u'], 'session_id': world['sid'], 'model': 'm'}]
    cost_record = scopes.ForOrg(scopes.OrgId(world['a']), 'cost record')
    assert (cost_record, 'zenbree_app', world['a'], None) in checkouts
    assert [d for e, d in frames if e == 'error'] == [NOT_A_MEMBER]
