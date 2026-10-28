"""«Ferma» (#260): POST /v1/jobs/{id}/cancel and the worker stopping on it."""

import asyncio
import dataclasses
import uuid

import psycopg
import pytest
from fastapi import HTTPException

from app.agent import events as job_events
from app.agent.routes import cancel_job
from app.agent.tools import registry
from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.llm.base import LLMEvent, ToolCall
from tests.conftest import call_as, member
from tests.test_agent import FakeProvider, _balance, _job, _people, _run, _user, as_starter, needs_db, paid_emails
from tests.test_agent import db as db


def _press_stop(job_id: str, user_id: str) -> None:
    """«Ferma» as the web sends it: the real endpoint, over the in-memory Redis of ``event_redis``."""
    with psycopg.connect(settings.database_url) as conn:  # the request's own read, before its scope exists
        (org,) = conn.execute('SELECT organization_id FROM jobs WHERE id = %s', (job_id,)).fetchone()
    user = CurrentUser(id=user_id, org_id=str(org))
    assert call_as(user, cancel_job, uuid.UUID(job_id), user) == {'status': 'cancelling'}


_EMAILS = paid_emails('fake_stopping_emails', credits=2, batch=2)


async def _stopping_emails(args, ctx):
    _press_stop(ctx.job_id, ctx.user_id)
    await asyncio.sleep(0.5)  # the watcher's blocking read sees the request before this tool returns
    return await _EMAILS.handler(args, ctx)


if not registry.get(_EMAILS.name):
    registry.register(dataclasses.replace(_EMAILS, handler=_stopping_emails))


async def _run_with(jid, provider):
    from app.agent.loop import run_job

    events: list[tuple[str, dict]] = []
    with as_starter(jid):
        await run_job(jid, provider=provider, emit=lambda e, d: events.append((e, d)))
    return events


def _terminal(db, jid, sid):
    job = db.execute('SELECT status, finished_at, credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()
    msg = db.execute(
        "SELECT content, status, terminal_code, credits_used FROM chat_messages WHERE session_id = %s AND role = 'assistant'",
        (sid,),
    ).fetchone()
    held = db.execute(
        "SELECT count(*) AS n FROM credit_transactions WHERE status IN ('reserved', 'settling')"
    ).fetchone()['n']
    return job, msg, held


@needs_db
async def test_a_stop_cuts_the_model_off_keeps_its_text_and_releases_the_hold(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'agenzie a Milano')

    class Slow(FakeProvider):
        async def chat_stream(self, **kw):
            yield LLMEvent(type='text_delta', text='Cerco le agenzie')
            _press_stop(jid, uid)
            await asyncio.sleep(3600)  # cut off here

    events = await _run_with(jid, Slow([]))

    job, msg, held = _terminal(db, jid, sid)
    assert job['status'] == 'cancelled' and job['finished_at'] and job['credits_spent'] == 0
    assert (msg['content'], msg['status'], msg['terminal_code']) == ('Cerco le agenzie', 'partial', 'cancelled')
    assert held == 0 and _balance(db, uid) == 50
    names = [e for e, _ in events]
    assert names[-3:] == ['error', 'message_saved', 'done']
    assert dict(events)['error']['code'] == 'cancelled'


@needs_db
async def test_a_stop_lets_the_running_tool_finish_bills_it_and_runs_nothing_after(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'agenzie a Milano')
    _people(db, uid, sid, 2)
    calls = [ToolCall('t1', 'fake_stopping_emails', {}), ToolCall('t2', 'fake_companies', {})]

    events, provider = await _run(jid, [('', calls), ('unused', [])])

    job, msg, held = _terminal(db, jid, sid)
    assert [d['tool_id'] for e, d in events if e == 'tool_start'] == ['t1']
    assert len(provider.turns) == 1  # no step after the stop
    assert (job['status'], job['credits_spent'], msg['credits_used']) == ('cancelled', 4, 4)
    assert held == 0 and _balance(db, uid) == 46
    assert db.execute("SELECT count(*) AS n FROM list_rows WHERE cells ? 'email'").fetchone()['n'] == 2


@needs_db
async def test_a_stop_keeps_the_answer_the_model_wrote_with_the_running_tool(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'agenzie a Milano')

    # over the 200 characters a step holds back (#572), so it streams and the stop gives it back
    answer = (
        'Ecco le agenzie di Milano che lavorano con aziende B2B: le ordino per dimensione e per settore, '
        'poi cerco per ciascuna un referente commerciale e ti mostro prima quelle con un sito aggiornato di recente.'
    )
    events, _ = await _run(jid, [(answer, [ToolCall('t1', 'fake_stopping_emails', {})]), ('unused', [])])

    _, msg, _ = _terminal(db, jid, sid)
    assert (msg['content'], msg['status']) == (answer, 'partial')
    texts = [(e, d) for e, d in events if e in ('text', 'text_dropped', 'error')]
    assert [e for e, _ in texts] == ['text', 'text_dropped', 'text', 'error']
    assert texts[2][1] == {'content': answer}


@needs_db
async def test_a_job_stopped_while_queued_never_calls_the_model(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'agenzie a Milano')
    _press_stop(jid, uid)

    events, provider = await _run(jid, [('never', [])])

    job, msg, held = _terminal(db, jid, sid)
    assert provider.histories == [] and job['status'] == 'cancelled'
    assert (msg['status'], msg['terminal_code']) == ('partial', 'cancelled')
    assert held == 0 and _balance(db, uid) == 50
    assert [e for e, _ in events][-3:] == ['error', 'message_saved', 'done']


@needs_db
async def test_a_stop_during_an_empty_search_builds_no_funnel(db, http, keys, kill_switch):
    import httpx

    kill_switch.add('vendor_disabled:serper')
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'SaaS che assumono AE')

    def apollo(request):
        _press_stop(jid, uid)  # the user stops while the search is out
        return httpx.Response(200, json={'organizations': [], 'pagination': {'total_entries': 0}})

    http.handler = apollo
    search = {'industries': ['software'], 'job_titles': ['Account Executive']}
    events, provider = await _run(jid, [('', [ToolCall('s1', 'search_companies', search)]), ('unused', [])])

    job, msg, held = _terminal(db, jid, sid)
    assert len(http.calls) == 1  # the search itself, no funnel page
    assert (job['status'], msg['status'], msg['terminal_code']) == ('cancelled', 'partial', 'cancelled')
    assert 'question' not in dict(events)['message_saved'] and len(provider.turns) == 1
    assert held == 0 and _balance(db, uid) == 50


@needs_db
def test_cancel_is_owner_scoped_and_leaves_a_finished_job_alone(db, event_redis):
    uid, other = _user(db), _user(db)
    _, jid = _job(db, uid, 'x')

    with pytest.raises(HTTPException) as err:
        call_as(member(db, other), cancel_job, uuid.UUID(jid), member(db, other))
    assert err.value.status_code == 404 and not job_events.cancel_requested(jid)

    assert call_as(member(db, uid), cancel_job, uuid.UUID(jid), member(db, uid)) == {'status': 'cancelling'}
    assert job_events.cancel_requested(jid) and event_redis.ttl(job_events.cancel_key(jid)) > 0

    _, done = _job(db, other, 'y')
    db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (done,))
    assert call_as(member(db, other), cancel_job, uuid.UUID(done), member(db, other)) == {'status': 'succeeded'}
    assert not job_events.cancel_requested(done)


@needs_db
def test_cancel_says_so_when_the_request_cannot_be_queued(db, monkeypatch):
    monkeypatch.setattr(job_events, 'append', lambda *a, **kw: False)
    uid = _user(db)
    _, jid = _job(db, uid, 'x')
    with pytest.raises(HTTPException) as err:
        call_as(member(db, uid), cancel_job, uuid.UUID(jid), member(db, uid))
    assert err.value.status_code == 503
    assert db.execute('SELECT status FROM jobs WHERE id = %s', (jid,)).fetchone()['status'] == 'queued'
