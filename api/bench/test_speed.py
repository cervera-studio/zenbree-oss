"""Offline speed regression gate; see docs/speed-benchmark.md."""

import json
from time import perf_counter

import httpx
import pytest

from app.agent.loop import run_job
from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.price import Price
from app.llm.base import ToolCall
from tests.test_agent import DB_URL, FakeProvider, _job, _user, as_starter, db  # noqa: F401

FIRST_ROW_SECONDS = 1.0
FINISHED_LIST_SECONDS = 3.0
BRIEFS = [
    ('Find 10 marketing agencies in Milan.', 'company', 10),
    ('Find 50 SaaS companies in Italy.', 'company', 50),
    ('Find 100 engineering leaders in European SaaS companies.', 'person', 100),
]


@pytest.mark.parametrize(('brief', 'entity', 'count'), BRIEFS)
@pytest.mark.parametrize('repeat', range(3))
async def test_brief_speed(brief, entity, count, repeat, request, monkeypatch):
    # Fail instead of silently skipping the CI gate when its DB is missing.
    assert DB_URL, 'Set TEST_DATABASE_URL to a disposable PostgreSQL database'
    conn = request.getfixturevalue('db')

    def no_http(*args, **kwargs):
        raise AssertionError('Speed benchmark must never call external HTTP services')

    monkeypatch.setattr(httpx.Client, 'send', no_http)
    monkeypatch.setattr(httpx.AsyncClient, 'send', no_http)

    async def search(args, ctx):
        rows = [
            {'name': f'Company {i}', 'domain': f'company-{i}.test'}
            if entity == 'company'
            else {'first_name': f'Person {i}', 'last_name': 'Test', 'email': f'person-{i}@example.test'}
            for i in range(count)
        ]
        return ToolResult(ok=True, rows=rows, entity=entity)

    tool = ToolDef(
        'speed_search', 'Offline search fixture', {'type': 'object'}, search, price=Price(1, (), lambda cells: False)
    )
    monkeypatch.setitem(registry._REGISTRY, tool.name, tool)
    uid = _user(conn, credits=500)
    _, jid = _job(conn, uid, brief)
    provider = FakeProvider([('', [ToolCall('search', tool.name, {})]), ('List ready.', [])])
    timings = {}
    events = []

    def emit(event, data):
        elapsed = perf_counter() - started
        events.append((event, data))
        if event == 'list_updated' and data.get('rows_added', 0) > 0:
            timings.setdefault('first_row_seconds', elapsed)
        if event == 'done':
            timings['finished_list_seconds'] = elapsed

    with as_starter(jid):  # the scope the worker runs a turn in
        started = perf_counter()
        await run_job(jid, provider=provider, emit=emit)
    print(json.dumps({'brief': brief, 'repeat': repeat, 'rows': count, **timings}), flush=True)

    # A fast failed/empty/partial job must not pass the timing gate.
    assert not [data for event, data in events if event == 'error']
    assert dict(events)['message_saved']['status'] == 'success'
    job = conn.execute('SELECT * FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert job['status'] == 'succeeded' and job['finished_at'] is not None
    rows = conn.execute('SELECT relevance_score FROM list_rows WHERE list_id = %s', (job['list_id'],)).fetchall()
    assert len(rows) == count and all(row['relevance_score'] == 80 for row in rows)
    balance = conn.execute('SELECT credits_remaining FROM user_credits WHERE user_id = %s', (uid,)).fetchone()
    assert balance['credits_remaining'] == 500  # a search bills only found row_updates (#727): new rows are free
    assert timings['first_row_seconds'] <= FIRST_ROW_SECONDS, timings
    assert timings['finished_list_seconds'] <= FINISHED_LIST_SECONDS, timings
