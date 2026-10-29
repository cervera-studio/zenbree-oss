import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.agent.tools import registry
from app.agent.tools.handlers import enrich_email  # noqa: F401  registers the tool the plan runs
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.price import Price
from app.lists.lookup_outcome import NothingToSearch, searched
from app.llm.base import ToolCall
from tests.conftest import call_as, member
from tests.test_agent import _balance, _job, _run, _user, needs_db
from tests.test_agent import db as db
from tests.test_plan import EMAILS, _list_row_ids, _plan_args

PROVIDER_UP = {'up': False}


async def _flaky_emails(args, ctx):
    if not PROVIDER_UP['up']:
        return ToolResult(ok=False, error='vendor said 503')
    ids = _list_row_ids(ctx.list_id)
    updates = [
        {'row_id': r, 'cells': {'email': f'{i}@acme.test', 'email_status': 'verified'}} for i, r in enumerate(ids)
    ]
    return ToolResult(
        ok=True,
        entity='person',
        row_updates=updates,
        outcome=searched('email', updates),
        data={'searched': len(ids)},
    )


if not registry.get('retry_emails'):
    registry.register(
        ToolDef(
            name='retry_emails',
            description='test',
            input_schema={'type': 'object'},
            handler=_flaky_emails,
            provider='emailco',
            price=EMAILS,
        )
    )


async def _nobody_left(args, ctx):
    return ToolResult(
        ok=True,
        outcome=NothingToSearch(kind='nothing_to_search', contact='email'),
        data={'searched': 0, 'failed': [], 'nothing_left': 'No row is left to search.'},
    )


if not registry.get('nobody_left_emails'):
    registry.register(
        ToolDef(
            name='nobody_left_emails',
            description='test',
            input_schema={'type': 'object'},
            handler=_nobody_left,
            found_noun='email',
            price=Price(2, (), lambda cells: False),
        )
    )


def _plan(rows=2, find='plan_people'):
    plan = _plan_args(rows=rows, email_units=rows)
    plan['stages'][0]['tool'] = find
    plan['stages'][1]['tool'] = 'retry_emails'
    return plan


def _start(db, uid, request):
    """``POST /v1/chat`` of ``uid``, in the scope the request runs in."""
    from app.agent.routes import _start_turn

    user = member(db, uid)
    return call_as(user, _start_turn, user, request)


def _request(sid, jid, stage=1):
    from app.agent.chat_request import ChatRequest, StageRetry

    return ChatRequest(message='Riprova', session_id=sid, retry_stage=StageRetry(job_id=jid, stage=stage))


async def _failed_first_run(db, find='plan_people', plan=None, paid_before=False):
    PROVIDER_UP['up'] = False
    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, '2 CTOs with emails')
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', plan or _plan(find=find))]),
            ('', [ToolCall('s1', find, {'tag': 'first'})]),
            *([('', [ToolCall('k1', 'plan_emails', {})])] if paid_before else []),
            ('', [ToolCall('e1', 'retry_emails', {})]),
            ('The email provider is down.', []),
        ],
    )
    return uid, sid, jid, events


@needs_db
async def test_a_failed_call_puts_its_reason_on_the_stage(db):
    uid, _, jid, events = await _failed_first_run(db)

    snapshots = [d for e, d in events if e == 'stages']
    assert snapshots[0]['stages'][1] == {
        'tool': 'retry_emails',
        'label': 'Emails',
        'units': 2,
        'done': 0,
        'per_row': True,
    }
    last = snapshots[-1]
    assert last['stages'][1]['error'] == {'code': 'provider'}
    assert last['stages'][0]['done'] == 1 and 'error' not in last['stages'][0]
    assert last['phase'] is None and last['credits_spent'] == 0
    assert [
        s['error']
        for s in db.execute('SELECT stages FROM jobs WHERE id = %s', (jid,)).fetchone()['stages']
        if 'error' in s
    ] == [{'code': 'provider'}]
    assert _balance(db, uid) == 100


@needs_db
async def test_retry_runs_the_failed_stage_under_the_same_plan_and_bills_only_what_it_delivers(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    PROVIDER_UP['up'] = True
    sid2, jid2 = _start(db, uid, _request(sid, jid))
    assert sid2 == sid

    row = db.execute('SELECT retry_of::text, retry_stage, stages FROM jobs WHERE id = %s', (jid2,)).fetchone()
    assert (row['retry_of'], row['retry_stage']) == (jid, 1)
    assert row['stages'][1]['error'] and row['stages'][0]['done'] == 1
    assert db.execute('SELECT approved_job_id::text FROM list_plans').fetchone()['approved_job_id'] == jid2

    # Taken on the clock the stage is stamped with (this process's), not the database's ``jobs.started_at``: a
    # database in a container VM runs tens of milliseconds apart from the host, and the two were compared.
    retry_began = datetime.now(UTC)
    events, provider = await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('Emails found.', [])])
    assert 'RETRY: this turn retries stage 2, "Emails"' in provider.systems[0]
    final = [d for e, d in events if e == 'stages'][-1]
    assert [(s['done'], s.get('error')) for s in final['stages']] == [(1, None), (2, None)]
    # #352: the retried stage's elapsed time is the retry's, not since the first run
    assert datetime.fromisoformat(final['stages'][1]['started_at']) >= retry_began
    assert db.execute('SELECT credits_spent, status FROM jobs WHERE id = %s', (jid2,)).fetchone() == {
        'credits_spent': 2,
        'status': 'succeeded',
    }
    assert _balance(db, uid) == 98


@needs_db
async def test_retry_is_refused_when_stale_or_repeated(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    other = _user(db)
    with pytest.raises(HTTPException) as e:
        _start(db, other, _request(sid, jid))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        _start(db, uid, _request(sid, jid, stage=0))
    assert e.value.detail['code'] == 'RETRY.STALE'

    _start(db, uid, _request(sid, jid))
    with pytest.raises(HTTPException) as e:
        _start(db, uid, _request(sid, jid))
    assert e.value.detail['code'] == 'CHAT.JOB_RUNNING'
    assert db.execute('SELECT count(*) AS n FROM jobs WHERE retry_of = %s', (jid,)).fetchone()['n'] == 1


@needs_db
async def test_an_old_failed_step_cannot_be_retried_once_a_newer_turn_exists(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])])
    with pytest.raises(HTTPException) as e:
        _start(db, uid, _request(sid, jid))
    assert e.value.detail['code'] == 'RETRY.STALE'


@needs_db
async def test_retry_spends_only_what_the_plan_still_allows(db):
    from app.agent import retry

    uid, sid, jid, _ = await _failed_first_run(db, paid_before=True)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])])
    assert retry.spent_before(db, jid2) == 2
    assert retry.spent_before(db, jid) == 0


def test_retry_needs_a_session_and_excludes_plan_approval():
    from pydantic import ValidationError

    from app.agent.chat_request import ChatRequest, StageRetry

    job = '00000000-0000-0000-0000-000000000001'
    with pytest.raises(ValidationError):
        ChatRequest(message='x', retry_stage=StageRetry(job_id=job, stage=0))


@needs_db
async def test_a_retry_turn_cannot_pay_for_a_stage_that_already_ran(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    events, _ = await _run(
        jid2, [('', [ToolCall('s2', 'plan_people', {})]), ('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])]
    )
    refused = next(d for e, d in events if e == 'tool_result' and d['tool_id'] == 's2')
    assert refused['status'] == 'failed' and 'already ran' in refused['result_preview']
    final = [d for e, d in events if e == 'stages'][-1]
    first = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid,)).fetchone()['stages'][0]
    # untouched by the refused call: its elapsed time (#352) stays the first run's
    assert final['stages'][0] == first and 'started_at' in first
    assert {k: first[k] for k in ('tool', 'label', 'units', 'done')} == {
        'tool': 'plan_people',
        'label': 'Find',
        'units': 3,
        'done': 1,
    }
    assert db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (jid2,)).fetchone()['credits_spent'] == 2


@needs_db
async def test_a_retry_turn_never_enqueues_the_plans_research_fills(db, monkeypatch):
    """#259: research is no stage, so a retry of one stage cannot pay for it."""
    from app import worker

    fills: list[tuple] = []
    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: fills.append(a))
    plan = _plan()
    plan['columns'] = [*plan['columns'], {'label': 'Hospitals', 'research': 'Does it sell to hospitals?'}]
    uid, sid, jid, _ = await _failed_first_run(db, plan=plan)
    assert fills  # the planned run itself researched its rows
    fills.clear()
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])])
    assert fills == []


@needs_db
async def test_a_reloaded_chat_gets_the_failed_stage_with_its_job(db):
    from uuid import UUID

    from app.agent.routes import get_session

    uid, sid, jid, _ = await _failed_first_run(db)
    job = call_as(member(db, uid), get_session, UUID(sid), member(db, uid))['active_job']
    assert str(job['id']) == jid and job['credits_spent'] == 0
    assert job['stages'][1]['error'] == {'code': 'provider'}
    assert job['retry_stage'] is None
    assert (
        str(job['list_id'])
        == db.execute('SELECT id::text FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['id']
    )


@needs_db
async def test_a_turn_that_crashes_mid_plan_leaves_its_open_stage_retryable(db):

    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, '2 CTOs with emails')
    await _run(jid, [('', [ToolCall('p1', 'propose_plan', _plan())]), ('', [ToolCall('s1', 'plan_people', {})])])
    job = db.execute('SELECT status, stages FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert job['status'] == 'failed'
    assert job['stages'][0]['error'] == {'code': 'failed'}  # people: 1 of 3 units, the first one still open

    PROVIDER_UP['up'] = True
    _start(db, uid, _request(sid, jid, stage=0))


def _counting_search(monkeypatch):
    from app.agent.tools.registry import ToolDef as Def

    calls: list[dict] = []

    async def search(args, ctx):
        calls.append(args)
        rows = [
            {'full_name': 'Ada Rossi', 'title': 'CTO', 'linkedin_url': 'https://linkedin.com/in/ada'},
            {'full_name': 'Bo Bianchi', 'title': 'CTO', 'linkedin_url': 'https://linkedin.com/in/bo'},
        ]
        return ToolResult(ok=True, rows=rows, entity='person')

    monkeypatch.setitem(
        registry._REGISTRY,
        'search_people',
        Def(
            name='search_people',
            description='test',
            input_schema={},
            handler=search,
            price=Price(1, (), lambda cells: False),
        ),
    )
    return calls


@needs_db
async def test_two_parallel_searches_in_a_retry_turn_pay_for_nothing(db, monkeypatch):

    calls = _counting_search(monkeypatch)
    uid, sid, jid, _ = await _failed_first_run(db, find='search_people')
    before, balance = len(calls), _balance(db, uid)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    events, _ = await _run(
        jid2,
        [
            ('', [ToolCall('a', 'search_people', {'tag': 'a'}), ToolCall('b', 'search_people', {'tag': 'b'})]),
            ('', [ToolCall('e2', 'retry_emails', {})]),
            ('ok', []),
        ],
    )
    assert len(calls) == before
    assert [d['status'] for e, d in events if e == 'tool_result' and d['tool_id'] in 'ab'] == ['failed', 'failed']
    final = [d for e, d in events if e == 'stages'][-1]
    assert final['stages'][0]['done'] == 1
    assert _balance(db, uid) == balance - 4  # only the emails the retried stage delivered


@needs_db
async def test_more_rows_in_a_retry_turn_does_not_rerun_the_search_stage(db, monkeypatch):

    calls = _counting_search(monkeypatch)
    uid, sid, jid, _ = await _failed_first_run(db, find='search_people')
    list_id = db.execute('SELECT id::text FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['id']
    before = len(calls)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    events, _ = await _run(
        jid2,
        [
            ('', [ToolCall('m', 'more_rows', {'list_id': list_id})]),
            ('', [ToolCall('e2', 'retry_emails', {})]),
            ('ok', []),
        ],
    )
    assert len(calls) == before
    refused = next(d for e, d in events if e == 'tool_result' and d['tool_id'] == 'm')
    assert refused['status'] == 'failed' and 'already ran' in refused['result_preview']


@needs_db
async def test_a_tool_shared_by_two_stages_advances_only_the_retried_stage(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    stages = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid,)).fetchone()['stages']
    stages[0] = {**stages[0], 'tool': 'retry_emails', 'label': 'First', 'units': 4, 'done': 2}
    stages[1] = {**stages[1], 'tool': 'retry_emails', 'label': 'Second', 'units': 2, 'done': 0}
    db.execute('UPDATE jobs SET stages = %s WHERE id = %s', (Jsonb(stages), jid))
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    events, _ = await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])])
    final = [d for e, d in events if e == 'stages'][-1]
    assert [s['done'] for s in final['stages']] == [2, 2]


@needs_db
async def test_a_crashed_retry_leaves_the_retried_stage_failed_not_another(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    _start(db, uid, _request(sid, jid))
    jid2 = db.execute('SELECT id::text FROM jobs WHERE retry_of = %s', (jid,)).fetchone()['id']
    await _run(jid2, [])
    stages = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid2,)).fetchone()['stages']
    assert 'error' not in stages[0] and 'error' in stages[1]
    _start(db, uid, _request(sid, jid2))


@needs_db
async def test_a_retry_that_never_starts_keeps_the_failed_step_retryable(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    _, jid2 = _start(db, uid, _request(sid, jid))
    assert db.execute('SELECT stages FROM jobs WHERE id = %s', (jid2,)).fetchone()['stages'][1]['error']
    db.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", (jid2,))
    _start(db, uid, _request(sid, jid2))


@needs_db
async def test_the_error_clears_when_the_retried_tool_starts_and_frames_say_which_stage_retries(db):

    uid, sid, jid, _ = await _failed_first_run(db)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    events, _ = await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])])
    frames = [d for e, d in events if e == 'stages']
    assert {f['retry_stage'] for f in frames} == {1}
    assert {f['list_id'] for f in frames} == {
        db.execute('SELECT id::text FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['id']
    }
    assert 'error' in frames[1]['stages'][1] and frames[1]['phase'] == 'retry_emails'
    assert 'error' not in frames[2]['stages'][1]


@needs_db
async def test_the_retry_prompt_states_the_reduced_budget_and_not_the_full_approval(db):

    uid, sid, jid, _ = await _failed_first_run(db, paid_before=True)
    PROVIDER_UP['up'] = True
    _, jid2 = _start(db, uid, _request(sid, jid))
    _, provider = await _run(jid2, [('', [ToolCall('e2', 'retry_emails', {})]), ('ok', [])])
    system, state = provider.systems[0], provider.histories[0][-1]['content']
    assert 'APPROVED for this run' not in state and 'Execute it' not in state
    assert 'spending at most 5 credits' in system


@needs_db
async def test_parallel_searches_report_their_phase_and_stages_at_tool_start(db, monkeypatch):
    _counting_search(monkeypatch)
    monkeypatch.setitem(
        registry._REGISTRY,
        'search_companies',
        ToolDef(
            name='search_companies',
            description='t',
            input_schema={},
            handler=registry.get('search_people').handler,
            price=Price(1, (), lambda cells: False),
        ),
    )
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, 'x')
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', _plan(find='search_people'))]),
            ('', [ToolCall('a', 'search_people', {'t': 'a'}), ToolCall('b', 'search_companies', {'t': 'b'})]),
            ('ok', []),
        ],
    )
    names = [(e, d.get('tool_id')) for e, d in events]
    start = names.index(('tool_start', 'a'))
    assert events[start + 1][0] == 'stages' and events[start + 1][1]['phase'] == 'search_people'


@needs_db
async def test_ids_that_match_no_row_leave_the_email_stage_open_and_never_read_as_a_search(db):
    """#550: the model passed an id of no row. Nobody was searched: the step is not failed (the corrected call
    follows), the chip reads as an error, never «0 email», and nothing is charged."""
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, '2 CTOs with emails')
    plan = _plan()
    plan['stages'][1]['tool'] = 'enrich_email'
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', plan)]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'enrich_email', {'row_ids': [str(uuid.uuid4())]})]),
            ('Wrong id.', []),
        ],
    )
    result = next(d for e, d in events if e == 'tool_result' and d['tool_id'] == 'e1')
    assert result['status'] == 'failed' and result['summary'] == {'kind': 'no_row_match', 'contact': 'email'}
    emails = [d for e, d in events if e == 'stages'][-1]['stages'][1]
    assert emails['done'] == 0 and 'error' not in emails
    assert db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()['credits_spent'] == 0


@needs_db
async def test_a_call_with_nobody_left_to_search_adds_nothing_to_its_stage(db):
    """#562: the call searched nobody, so the plan step stays open: the stage does not count the units it planned."""
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, '2 CTOs with emails')
    plan = _plan()
    plan['stages'][1]['tool'] = 'nobody_left_emails'
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', plan)]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'nobody_left_emails', {})]),
            ('All done already.', []),
        ],
    )
    result = next(d for e, d in events if e == 'tool_result' and d['tool_id'] == 'e1')
    assert result['status'] == 'success'
    assert result['summary'] == {'kind': 'nothing_to_search', 'contact': 'email', 'unmatched': 0}
    emails = [d for e, d in events if e == 'stages'][-1]['stages'][1]
    assert emails['done'] == 0 and 'error' not in emails
