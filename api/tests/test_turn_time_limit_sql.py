"""#415: a turn cut off by its time limit still pays for what it delivered."""

import asyncio
import signal
import threading
import time

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.llm.base import LLMEvent, ToolCall
from tests.conftest import hold
from tests.test_agent import (
    FakeProvider,
    _balance,
    _job,
    _org,
    _people,
    _run,
    _user,
    as_starter,
    needs_db,
    paid_emails,
)
from tests.test_agent import db as db


async def _slow(args, ctx):
    if args.get('soft_limit'):
        signal.setitimer(signal.ITIMER_REAL, 0.2)
    await asyncio.sleep(3600)
    return ToolResult(ok=True, data={})


for _tool in (
    paid_emails('fake_paid', credits=5),
    paid_emails('fake_paid_too', credits=5),
    ToolDef(name='fake_slow', description='test', input_schema={'type': 'object'}, handler=_slow),
):
    if not registry.get(_tool.name):
        registry.register(_tool)


def _paying_job(db, uid) -> str:
    sid, jid = _job(db, uid, 'email dei CEO')
    _people(db, uid, sid, 2)
    return jid


def _ended(db, jid) -> dict:
    return db.execute(
        'SELECT j.status, j.credits_spent,'
        " (SELECT count(*) FROM credit_transactions WHERE status IN ('reserved', 'settling')) AS held"
        ' FROM jobs j WHERE j.id = %s',
        (jid,),
    ).fetchone()


@pytest.fixture
def soft_limit():
    """Celery's prefork pool raises SoftTimeLimitExceeded from a signal handler, here SIGALRM."""

    def fire(signum, frame):
        raise SoftTimeLimitExceeded()

    old = signal.signal(signal.SIGALRM, fire)
    yield
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, old)


@needs_db
def test_a_turn_past_the_soft_time_limit_settles_what_it_spent(db, soft_limit, monkeypatch):
    from app import worker
    from app.agent import events
    from app.llm import router

    uid = _user(db, credits=100)
    jid = _paying_job(db, uid)
    provider = FakeProvider(
        [('', [ToolCall('t1', 'fake_paid', {})]), ('', [ToolCall('t2', 'fake_slow', {'soft_limit': True})])]
    )
    monkeypatch.setattr(router, 'get_chat_provider', lambda: provider)
    monkeypatch.setattr(events, 'Publisher', lambda job_id: lambda e, d: None)

    with as_starter(jid), pytest.raises(SoftTimeLimitExceeded):  # the scope its message's header names
        worker.run_job(jid)

    assert _ended(db, jid) == {'status': 'failed', 'credits_spent': 5, 'held': 0}
    assert _balance(db, uid) == 95


@needs_db
async def test_a_turn_past_its_deadline_ends_failed_and_settles(db, monkeypatch):
    from app.agent import loop

    monkeypatch.setattr(loop, 'TURN_DEADLINE_SECONDS', 0.5)
    uid = _user(db, credits=100)
    jid = _paying_job(db, uid)
    sid = db.execute('SELECT chat_session_id FROM jobs WHERE id = %s', (jid,)).fetchone()['chat_session_id']

    events, _ = await _run(jid, [('', [ToolCall('t1', 'fake_paid', {})]), ('', [ToolCall('t2', 'fake_slow', {})])])

    assert _ended(db, jid) == {'status': 'failed', 'credits_spent': 5, 'held': 0}
    assert _balance(db, uid) == 95
    assert [e for e, _ in events][-3:] == ['error', 'message_saved', 'done']
    msg = db.execute("SELECT status FROM chat_messages WHERE session_id = %s AND role = 'assistant'", (sid,))
    assert msg.fetchone()['status'] == 'failed'


def test_the_turn_deadline_lands_well_before_celerys_soft_limit():
    from app import worker
    from app.agent import loop

    assert worker.run_job.soft_time_limit >= loop.TURN_DEADLINE_SECONDS + 30


def test_the_reaper_never_closes_the_hold_of_a_turn_still_inside_its_deadline():
    """A charge whose hold the reaper closed fails its call (HoldGone): that can happen only to a turn past its
    deadline."""
    from app import worker
    from app.agent import loop

    assert loop.TURN_DEADLINE_SECONDS < worker.RESERVATION_STALE_AFTER_MINUTES * 60


@needs_db
async def test_the_deadline_waits_for_a_threaded_charge_and_bills_it(db, monkeypatch):
    from app.agent import loop

    monkeypatch.setattr(loop, 'TURN_DEADLINE_SECONDS', 0.5)
    monkeypatch.setattr(loop, 'PARALLEL_TOOL_ALLOWLIST', frozenset({'fake_paid', 'fake_paid_too'}))
    finish = loop.Turn._finish_tool

    def slow_finish(self, *args):
        if threading.current_thread() is not threading.main_thread():
            time.sleep(1)
        return finish(self, *args)

    monkeypatch.setattr(loop.Turn, '_finish_tool', slow_finish)
    uid = _user(db, credits=100)
    jid = _paying_job(db, uid)

    events, _ = await _run(jid, [('', [ToolCall('a', 'fake_paid', {}), ToolCall('b', 'fake_paid_too', {})])])

    assert _ended(db, jid) == {'status': 'failed', 'credits_spent': 5, 'held': 0}
    assert _balance(db, uid) == 95
    assert [e for e, _ in events][-1] == 'done'


@needs_db
def test_the_soft_limit_waits_for_a_threaded_charge_and_bills_it(db, soft_limit, monkeypatch):
    from app import worker
    from app.agent import events, loop
    from app.llm import router

    monkeypatch.setattr(loop, 'PARALLEL_TOOL_ALLOWLIST', frozenset({'fake_paid', 'fake_paid_too'}))
    finish = loop.Turn._finish_tool

    def slow_finish(self, *args):
        if threading.current_thread() is not threading.main_thread():
            signal.setitimer(signal.ITIMER_REAL, 0.2)
            time.sleep(1)
        return finish(self, *args)

    monkeypatch.setattr(loop.Turn, '_finish_tool', slow_finish)
    uid = _user(db, credits=100)
    jid = _paying_job(db, uid)
    provider = FakeProvider([('', [ToolCall('a', 'fake_paid', {}), ToolCall('b', 'fake_paid_too', {})])])
    monkeypatch.setattr(router, 'get_chat_provider', lambda: provider)
    monkeypatch.setattr(events, 'Publisher', lambda job_id: lambda e, d: None)

    with as_starter(jid), pytest.raises(SoftTimeLimitExceeded):  # the scope its message's header names
        worker.run_job(jid)

    assert _ended(db, jid) == {'status': 'failed', 'credits_spent': 5, 'held': 0}
    assert _balance(db, uid) == 95


@needs_db
async def test_the_deadline_keeps_the_text_the_model_streamed(db, monkeypatch):
    from app.agent import loop

    class Hanging(FakeProvider):
        async def chat_stream(self, **kw):
            yield LLMEvent(type='text_delta', text='Analisi parziale')
            await asyncio.sleep(3600)

    monkeypatch.setattr(loop, 'TURN_DEADLINE_SECONDS', 0.5)
    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, 'email dei CEO')

    with as_starter(jid):
        await loop.run_job(jid, provider=Hanging([]), emit=lambda e, d: None)

    msg = db.execute("SELECT content, status FROM chat_messages WHERE session_id = %s AND role = 'assistant'", (sid,))
    assert msg.fetchone() == {'content': 'Analisi parziale', 'status': 'failed'}


@needs_db
async def test_the_deadline_gives_back_the_free_rows_a_cut_search_held(db, monkeypatch):
    from app.agent import free_rows, loop

    monkeypatch.setattr(loop, 'TURN_DEADLINE_SECONDS', 0.5)
    monkeypatch.setattr(free_rows, 'SEARCHES', frozenset({'fake_slow'}))
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, 'agenzie a Milano')

    await _run(jid, [('', [ToolCall('t1', 'fake_slow', {})])])

    assert _ended(db, jid)['status'] == 'failed'
    assert free_rows.today(_org(db, uid)) == 0


class _Killed(BaseException):
    """The worker dies: the turn neither refunds nor settles."""


@needs_db
async def test_a_turn_killed_after_its_store_keeps_what_it_delivered(db, monkeypatch):
    from app.agent import loop
    from app.agent.turn_credits import TurnCredits

    recorded = []
    store = loop.Turn._deliver

    def store_and_die(self, *args):
        store(self, *args)
        job = db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (self.job_id,)).fetchone()
        recorded.append(job['credits_spent'])
        raise _Killed

    monkeypatch.setattr(loop.Turn, '_deliver', store_and_die)
    monkeypatch.setattr(TurnCredits, 'finish', lambda self: ({}, []))
    uid = _user(db, credits=100)
    jid = _paying_job(db, uid)

    with pytest.raises(_Killed):
        await _run(jid, [('', [ToolCall('t1', 'fake_paid', {})]), ('fatto', [])])
    db.execute("UPDATE credit_transactions SET created_at = created_at - interval '1 hour'")
    db.execute('SELECT reap_stale_reservations_ids(20)').fetchall()

    assert recorded == [5]
    assert _balance(db, uid) == 95
    assert not db.execute("SELECT 1 FROM credit_transactions WHERE status IN ('reserved', 'settling')").fetchone()


@needs_db
def test_the_reaper_keeps_a_dead_jobs_recorded_spend(db):
    """The dead job's hold keeps what its charges cost; a hold with no charge is released whole."""
    from app.billing import row_charges

    uid, other = _user(db, credits=100), _user(db, credits=100)
    sid, jid = _job(db, uid, 'email dei CEO')
    _, other_jid = _job(db, other, 'telefoni')
    (row,) = _people(db, uid, sid, 1)
    lid = db.execute('SELECT list_id::text FROM list_rows WHERE id = %s', (row,)).fetchone()['list_id']
    holds = [hold(db, uid, jid, 13), hold(db, other, other_jid, 20)]
    row_charges.charge(db, holds[0], jid, 't1', lid, [row], (), 5)
    db.execute("UPDATE credit_transactions SET created_at = created_at - interval '1 hour'")

    reaped = db.execute('SELECT r::text FROM reap_stale_reservations_ids(20) AS r').fetchall()

    assert {r['r'] for r in reaped} == set(holds)
    assert _balance(db, uid) == 95
    assert _balance(db, other) == 100
    assert not db.execute("SELECT 1 FROM credit_transactions WHERE status IN ('reserved', 'settling')").fetchone()
