"""Divergence points 9 and 11: an AI column fill bills from one ``row_charges`` record per filled cell, written with the
cell, so a fill whose worker dies still pays for the cells it wrote; a plan's research records under its turn."""

import asyncio
import contextlib
import json
import threading

import pytest

from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.lists import ai_columns
from app.lists.ai_columns import fill_column, queue_fill
from app.lists.row_edit import write_edit
from app.llm.base import LLMResponse
from tests.conftest import member
from tests.test_agent import _balance, _job, _user, needs_db
from tests.test_agent import db as db
from tests.test_ai_columns import FakeProvider, _ai_list, _fake_search, _fill
from tests.test_ai_columns import fake_search as fake_search
from tests.test_credit_callers_sql import _ledger
from tests.test_plan import _as_lists_member, _research_args, _run
from tests.test_row_phone_credits_sql import _reap

pytestmark = needs_db


def _charges(db) -> list[dict]:
    return db.execute(
        'SELECT t.reference_type, t.user_id::text AS spender, c.job_id::text AS job, c.keys, c.credits, c.state'
        ' FROM row_charges c JOIN credit_transactions t ON t.id = c.hold_id ORDER BY c.created_at'
    ).fetchall()


async def test_a_fill_whose_worker_dies_after_three_cells_bills_three(db, monkeypatch):
    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta', 'Gamma', 'Delta'])
    real = ai_columns._write_cell
    lock, calls, written = threading.Lock(), [], []
    stored, dead = threading.Event(), threading.Event()

    def write(run, row_id, cell):
        with lock:
            calls.append(row_id)
            last = len(calls) == 4
        if last:  # the fourth cell: this worker runs no further line
            dead.wait(30)
            raise RuntimeError('the worker died')
        ok = real(run, row_id, cell)
        with lock:
            written.append(ok)
            if len(written) == 3:
                stored.set()
        return ok

    monkeypatch.setattr(ai_columns, '_write_cell', write)
    fill = asyncio.create_task(_fill(db, lid, 'ai_h', provider=FakeProvider()))
    try:
        assert await asyncio.to_thread(stored.wait, 30), 'the fill never wrote three cells'
        _reap(db)

        filled = db.execute("SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND cells ? 'ai_h'", (lid,))
        assert filled.fetchone()['n'] == 3
        assert _balance(db, uid) == 47
        assert _ledger(db, uid) == [
            ('deduction', 'committed', 4, 'ai_column'),
            ('refund', 'committed', 1, 'reservation.slack'),
        ]
        assert [(c['reference_type'], c['keys'], c['credits'], c['state']) for c in _charges(db)] == [
            ('ai_column', ['ai_h'], 1, 'charged')
        ] * 3
    finally:
        dead.set()
        with contextlib.suppress(Exception):
            await fill


async def test_a_fill_that_ends_keeps_the_source_of_each_cell_it_bills(db):
    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta'])

    await _fill(db, lid, 'ai_h', provider=FakeProvider())

    meta = db.execute("SELECT cell_meta->'ai_h' AS m FROM list_rows WHERE list_id = %s", (lid,)).fetchall()
    assert [(m['m']['status'], m['m']['source']['url']) for m in meta] == [('filled', 'https://acme.com/about')] * 2
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_h'").fetchone()['f']
    assert (fill['status'], fill['filled'], fill['credits'], _balance(db, uid)) == ('done', 2, 2, 48)
    assert len(_charges(db)) == 2


async def test_a_hand_edit_never_frees_room_under_a_plans_cap_for_its_retry(db):
    """#1095: a charged cell edited by hand still counts against the cap the plan approved."""
    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta', 'Gamma', 'Delta'], cap=3)
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    charged = db.execute(
        "SELECT id FROM list_rows WHERE list_id = %s AND cell_meta->'ai_h'->>'status' = 'filled' LIMIT 1", (lid,)
    ).fetchone()['id']
    assert _balance(db, uid) == 47

    write_edit(db, lid, 'company', charged, {'ai_h': 'My own answer'}, None, by=uid)
    assert queue_fill(db, lid, 'ai_h', 3)
    await _fill(db, lid, 'ai_h', provider=FakeProvider())

    filled = db.execute("SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND cells ? 'ai_h'", (lid,))
    assert (filled.fetchone()['n'], _balance(db, uid), len(_charges(db))) == (3, 47, 3)


async def test_a_plans_research_records_under_its_turn_as_its_author(db, monkeypatch):
    """#408: the research fill keeps its own hold, but its author is the turn's and its records name the turn."""
    from app import worker
    from app.llm.base import ToolCall

    page = {'url': 'https://acme.test/about', 'title': 'About', 'snippet': 'Sells to hospitals.'}

    async def search(args, ctx):
        return ToolResult(ok=True, data={'results': [page]})

    class Research:
        async def chat(self, **kw):
            return LLMResponse(text=json.dumps({'value': 'Yes', 'source_url': page['url']}))

    monkeypatch.setitem(registry._REGISTRY, 'web_search', ToolDef('web_search', 'test', {}, search))
    plan_fills: list[tuple] = []
    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: plan_fills.append(a))
    monkeypatch.setattr(worker.fill_ai_column, 'delay', lambda *a: None)
    uid, maker = _user(db, credits=100), _user(db)
    org = member(db, uid).org_id
    member(db, maker)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (org, maker),
    )
    _, jid = _job(db, uid, '2 CTOs with research')
    args = _research_args()
    args['columns'] = [{'key': 'full_name', 'required': True}, {'label': 'Hospitals', 'research': 'Hospitals?'}]
    args['stages'] = [{'tool': 'plan_people', 'units': 1}]
    await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', args)]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('Done.', []),
        ],
    )
    *_, (list_id, *fill_args) = plan_fills
    db.execute('UPDATE lists SET user_id = %s WHERE id = %s', (maker, list_id))  # the author is not the list's maker
    with _as_lists_member(db, list_id, uid):
        worker.fill_plan_columns.run(list_id, *fill_args)
        await fill_column(list_id, 'ai_hospitals', provider=Research())

    research = [c for c in _charges(db) if c['reference_type'] == 'ai_column']
    assert [(c['spender'], c['job'], c['credits']) for c in research] == [(uid, jid, 1)] * 2


async def test_a_cell_whose_hold_the_reaper_closed_lands_nothing_and_bills_nothing(db, monkeypatch):
    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme'])
    real = ai_columns._write_cell

    def write(run, row_id, cell):
        _reap(db)
        return real(run, row_id, cell)

    monkeypatch.setattr(ai_columns, '_write_cell', write)
    await _fill(db, lid, 'ai_h', provider=FakeProvider())

    row = db.execute('SELECT cells, cell_meta FROM list_rows WHERE list_id = %s', (lid,)).fetchone()
    assert ('ai_h' in row['cells'], 'ai_h' in row['cell_meta']) == (False, False)
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_h'").fetchone()['f']
    assert (fill['status'], fill['error'], fill['credits'], _balance(db, uid)) == ('failed', 'fill_crashed', 0, 50)
    assert _charges(db) == []


async def test_a_fill_whose_hold_the_reaper_closed_searches_no_further_cell(db, monkeypatch):
    """One cell at a time: the reaper closes the hold before the second cell's write, which stops the third and the
    fourth cells before their search."""
    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta', 'Gamma', 'Delta'])
    searched: list[str] = []
    writes = 0
    real = ai_columns._write_cell

    async def search(args, ctx) -> ToolResult:
        if len(searched) >= 2:
            await asyncio.sleep(10)  # reached only by a fill that goes on after the stop: the count below fails
        searched.append(args['query'])
        return await _fake_search(args, ctx)

    def write(run, row_id, cell):
        nonlocal writes
        writes += 1
        if writes == 2:
            _reap(db)
        return real(run, row_id, cell)

    monkeypatch.setitem(registry._REGISTRY, 'web_search', ToolDef('web_search', 'test', {}, search))
    monkeypatch.setattr(ai_columns, 'CONCURRENCY', 1)
    monkeypatch.setattr(ai_columns, '_write_cell', write)
    await _fill(db, lid, 'ai_h', provider=FakeProvider())

    assert len(searched) == 2
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_h'").fetchone()['f']
    assert (fill['status'], fill['error'], fill['filled'], fill['credits']) == ('failed', 'fill_crashed', 1, 1)
    assert _balance(db, uid) == 49
    assert [(c['keys'], c['credits'], c['state']) for c in _charges(db)] == [(['ai_h'], 1, 'charged')]


@pytest.mark.parametrize('waits', ['before_write', 'in_get_conn'])
async def test_a_cell_still_writing_when_the_fill_stops_lands_nothing_and_bills_nothing(db, monkeypatch, waits):
    """The other cell's HoldGone cancels this cell's task, but its write thread runs on past ``run.finish``: either
    it starts after the finish, or it read the tab before and waits for a connection until then."""
    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta'])
    real_write, real_conn, real_finish = ai_columns._write_cell, ai_columns.get_conn, ai_columns._Run.finish
    lock, calls, late = threading.Lock(), [], threading.local()
    entered, finished, landed = threading.Event(), threading.Event(), threading.Event()

    def get_conn():
        if getattr(late, 'cell', False):
            late.cell = False
            entered.set()
            finished.wait(30)
        return real_conn()

    def write(run, row_id, cell):
        with lock:
            calls.append(row_id)
            first = len(calls) == 1
        if not first:
            assert entered.wait(30), 'the late cell never reached its write'
            _reap(db)
            return real_write(run, row_id, cell)
        try:
            if waits == 'before_write':
                entered.set()
                finished.wait(30)
            else:
                late.cell = True
            return real_write(run, row_id, cell)
        finally:
            landed.set()

    def finish(run, status, error):
        real_finish(run, status, error)
        finished.set()

    monkeypatch.setattr(ai_columns, 'get_conn', get_conn)
    monkeypatch.setattr(ai_columns, '_write_cell', write)
    monkeypatch.setattr(ai_columns._Run, 'finish', finish)
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    assert await asyncio.to_thread(landed.wait, 30), 'the late write never ended'

    rows = db.execute('SELECT cells, cell_meta FROM list_rows WHERE list_id = %s', (lid,)).fetchall()
    assert [('ai_h' in r['cells'], 'ai_h' in r['cell_meta']) for r in rows] == [(False, False)] * 2
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_h'").fetchone()['f']
    assert (fill['status'], fill['error'], fill['credits'], _balance(db, uid)) == ('failed', 'fill_crashed', 0, 50)
    assert _charges(db) == []
