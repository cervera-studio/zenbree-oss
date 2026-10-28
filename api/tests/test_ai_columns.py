"""AI columns (E3): key naming, grounding, the fill task's credits and per-cell sources."""

import asyncio
import json
import uuid

import pytest
from structlog.testing import capture_logs

from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.core.scope import acting_as
from app.lists.ai_columns import column_key, fill_column
from app.lists.ai_research import research_cell, ungrounded_numbers
from app.llm.base import LLMResponse
from tests.conftest import as_request, member
from tests.test_agent import _balance, _chat, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db

# What the fake search returns per company name; None = the vendor call fails.
SEARCH: dict[str, list[dict] | None] = {}
# What the fake model answers per company name.
ANSWERS: dict[str, dict] = {}


class FakeProvider:
    async def chat(self, *, system, messages, **kw):
        name = json.loads(messages[0]['content'])['company']['name']
        return LLMResponse(text=json.dumps(ANSWERS[name]))


async def _fake_search(args, ctx):
    name = next((n for n in SEARCH if args['query'].startswith(n)), None)
    if name is None or SEARCH[name] is None:
        return ToolResult(ok=False, error='down')
    return ToolResult(ok=True, data={'query': args['query'], 'results': SEARCH[name]})


class CountingRedis:
    """The fill quota's counter, in memory: ``counts`` is what the routes and the plan path have used."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def eval(self, script, numkeys, key, seconds):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


@pytest.fixture(autouse=True)
def fill_counter(monkeypatch):
    counter = CountingRedis()
    monkeypatch.setattr('app.core.rate_limit._redis', lambda: counter)
    return counter


@pytest.fixture(autouse=True)
def fake_search(monkeypatch):
    tool = ToolDef('web_search', 'test', {'type': 'object'}, _fake_search, provider='firecrawl')
    monkeypatch.setitem(registry._REGISTRY, 'web_search', tool)
    SEARCH.clear()
    ANSWERS.clear()


ACME_PAGE = {'url': 'https://acme.com/about', 'title': 'About Acme', 'snippet': 'Acme serves 120 hospitals in Italy.'}


async def _fill(db, list_id: str, key: str, provider=None) -> None:
    """``fill_column`` as the worker runs it: as the member the message's header names, here the list's creator."""
    row = db.execute('SELECT organization_id::text AS org, user_id::text AS uid FROM lists WHERE id = %s', (list_id,))
    row = row.fetchone()
    with acting_as(row['org'], row['uid']):
        await fill_column(list_id, key, provider=provider)


def test_column_key_is_unique_and_never_internal():
    assert column_key('Sells to hospitals?', set()) == 'ai_sells_to_hospitals'
    assert column_key('Sells to hospitals', {'ai_sells_to_hospitals'}) == 'ai_sells_to_hospitals_2'
    assert column_key('Customer ID', set()) == 'ai_customer_id_'
    assert column_key('???', set()) == 'ai_column'


def test_ungrounded_numbers_fails_open_on_separators():
    assert ungrounded_numbers('+2,7%', 'growth was 2.7% last year') == set()
    assert ungrounded_numbers('about 1.200 staff', 'employs 1,200 people') == set()
    assert ungrounded_numbers('yes, 35 sites', 'Acme serves 120 hospitals') == {'35'}
    assert ungrounded_numbers('Yes', '') == set()


@pytest.mark.parametrize(
    ('answer', 'status', 'error'),
    [
        ({'value': 'Yes, 120 hospitals', 'source_url': ACME_PAGE['url']}, 'filled', None),
        ({'value': 'Yes, 300 hospitals', 'source_url': ACME_PAGE['url']}, 'empty', 'ungrounded'),
        ({'value': 'Yes', 'source_url': 'https://elsewhere.test'}, 'empty', 'no_source'),
        ({'value': None, 'source_url': None}, 'empty', None),
    ],
)
async def test_research_cell_keeps_only_grounded_sourced_answers(answer, status, error):
    SEARCH['Acme'] = [ACME_PAGE]
    ANSWERS['Acme'] = answer
    cell = await research_cell(FakeProvider(), 'u', 'company', {'name': 'Acme'}, 'Does it sell to hospitals?')
    assert (cell['status'], cell['error']) == (status, error)
    if status == 'filled':
        assert cell['value'] == 'Yes, 120 hospitals'
        assert cell['source'] == {'url': ACME_PAGE['url'], 'title': 'About Acme'}
    else:
        assert cell['value'] is None and cell['source'] is None


async def test_research_cell_rejects_non_http_sources():
    bad = {'url': 'javascript:alert(1)', 'title': 'x', 'snippet': 'Yes'}
    SEARCH['Acme'] = [bad]
    ANSWERS['Acme'] = {'value': 'Yes', 'source_url': bad['url']}
    cell = await research_cell(FakeProvider(), 'u', 'company', {'name': 'Acme'}, 'q?')
    assert (cell['status'], cell['error']) == ('empty', 'no_source')


async def test_research_cell_vendor_failure_is_a_failed_cell():
    SEARCH['Acme'] = None
    cell = await research_cell(FakeProvider(), 'u', 'company', {'name': 'Acme'}, 'q?')
    assert cell['status'] == 'failed'


# ── db ─────────────────────────────────────────────────────────────────────


def _list_with_rows(db, uid, names):
    from app.lists.rows import add_rows, create_list

    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='Hospitals', entity='company')
    add_rows(db, lid, 'company', [{'name': n, 'domain': f'{n.lower()}.test'} for n in names])
    return lid


@pytest.fixture
def queued(monkeypatch):
    from app.lists import routes

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(routes, '_enqueue_fill', lambda lid, key: calls.append((lid, key)))
    return calls


@needs_db
async def test_add_column_fill_bills_filled_cells_and_retries_failed(client, db, queued, balance_events):
    tc, uid = client
    lid = _list_with_rows(db, uid, ['Acme', 'Beta', 'Gamma'])
    SEARCH.update({'Acme': [ACME_PAGE], 'Beta': [{'url': 'https://beta.test', 'title': 'Beta'}], 'Gamma': None})
    ANSWERS.update(
        {
            'Acme': {'value': 'Yes, 120 hospitals', 'source_url': ACME_PAGE['url']},
            'Beta': {'value': None, 'source_url': None},
            'Gamma': {'value': 'Yes', 'source_url': 'https://gamma.test'},
        }
    )

    r = tc.post(f'/v1/lists/{lid}/columns', json={'label': 'Hospitals', 'prompt': 'Does it sell to hospitals?'})
    assert r.status_code == 201
    col = r.json()
    assert (col['key'], col['kind'], col['config']['fill']['status']) == ('ai_hospitals', 'ai', 'queued')
    assert queued == [(lid, 'ai_hospitals')]
    assert tc.post(f'/v1/lists/{lid}/columns/ai_hospitals/fill').status_code == 409  # already queued

    await _fill(db, lid, 'ai_hospitals', provider=FakeProvider())
    await _fill(db, lid, 'ai_hospitals', provider=FakeProvider())  # redelivery: not queued, no-op

    assert _balance(db, uid) == 49  # only Acme was filled
    org = db.execute('SELECT organization_id::text FROM lists WHERE id = %s', (lid,)).fetchone()['organization_id']
    assert balance_events == [(org, False)] * 2  # #243: the hold and the settle; the redelivery holds nothing
    rows = {r['cells']['name']: r for r in tc.get(f'/v1/lists/{lid}/rows').json()['rows']}
    assert rows['Acme']['cells']['ai_hospitals'] == 'Yes, 120 hospitals'
    assert rows['Acme']['cell_meta']['ai_hospitals']['source'] == {'url': ACME_PAGE['url'], 'title': 'About Acme'}
    assert rows['Beta']['cell_meta']['ai_hospitals']['status'] == 'empty'
    assert 'ai_hospitals' not in rows['Beta']['cells']
    assert rows['Gamma']['cell_meta']['ai_hospitals']['status'] == 'failed'
    fill = next(c for c in tc.get(f'/v1/lists/{lid}').json()['columns'] if c['key'] == 'ai_hospitals')['config']
    assert fill['prompt'] == 'Does it sell to hospitals?'
    assert {k: fill['fill'][k] for k in ('status', 'filled', 'empty', 'failed', 'credits')} == {
        'status': 'done',
        'filled': 1,
        'empty': 1,
        'failed': 1,
        'credits': 1,
    }
    tx = db.execute(
        "SELECT reference_type, status FROM credit_transactions WHERE user_id = %s AND type = 'deduction'", (uid,)
    ).fetchall()
    assert [(t['reference_type'], t['status']) for t in tx] == [('ai_column', 'committed')]

    # Rerun: only the failed cell is researched again.
    SEARCH['Gamma'] = [{'url': 'https://gamma.test', 'title': 'Gamma'}]
    assert tc.post(f'/v1/lists/{lid}/columns/ai_hospitals/fill').status_code == 202
    await _fill(db, lid, 'ai_hospitals', provider=FakeProvider())
    rows = {r['cells']['name']: r for r in tc.get(f'/v1/lists/{lid}/rows').json()['rows']}
    assert rows['Gamma']['cells']['ai_hospitals'] == 'Yes'
    assert rows['Beta']['cell_meta']['ai_hospitals']['status'] == 'empty'
    assert _balance(db, uid) == 48

    # A hand edit marks the cell manual (AI or not): no source, and a refill leaves it alone.
    acme = rows['Acme']['id']
    patched = tc.patch(f'/v1/lists/{lid}/rows/{acme}', json={'cells': {'ai_hospitals': 'No', 'notes': 'x'}}).json()
    assert patched['cell_meta']['ai_hospitals'] == patched['cell_meta']['notes'] == {'status': 'manual', 'by': str(uid)}
    assert tc.post(f'/v1/lists/{lid}/columns/ai_hospitals/fill').status_code == 202
    await _fill(db, lid, 'ai_hospitals', provider=FakeProvider())
    acme_row = next(r for r in tc.get(f'/v1/lists/{lid}/rows').json()['rows'] if r['id'] == acme)
    assert acme_row['cells']['ai_hospitals'] == 'No' and _balance(db, uid) == 48

    # CSV carries the value, never the provenance.
    assert 'Hospitals' in tc.get(f'/v1/lists/{lid}/export.csv').text.splitlines()[0]
    assert 'acme.com/about' not in tc.get(f'/v1/lists/{lid}/export.csv').text


@needs_db
def test_ai_column_routes_are_owner_scoped(client, db, queued):
    tc, _ = client
    other = _list_with_rows(db, _user(db), ['Acme'])
    assert tc.post(f'/v1/lists/{other}/columns', json={'label': 'X', 'prompt': 'what?'}).status_code == 404
    assert tc.post(f'/v1/lists/{other}/columns/name/fill').status_code == 404
    mine = _list_with_rows(db, client[1], ['Acme'])
    assert tc.post(f'/v1/lists/{mine}/columns/name/fill').status_code == 404  # static column
    assert tc.post(f'/v1/lists/{uuid.uuid4()}/columns', json={'label': 'X', 'prompt': 'what?'}).status_code == 404
    assert tc.post(f'/v1/lists/{mine}/columns', json={'label': '   ', 'prompt': 'what?'}).status_code == 422
    assert queued == []


@needs_db
@pytest.mark.parametrize(
    ('credits', 'disabled', 'error'), [(0, False, 'insufficient_credits'), (50, True, 'vendor_disabled')]
)
async def test_fill_refused_without_credits_or_with_kill_switch(
    client, db, queued, kill_switch, credits, disabled, error
):
    tc, uid = client
    db.execute('UPDATE user_credits SET credits_remaining = %s WHERE user_id = %s', (credits, uid))
    if disabled:
        kill_switch.add('vendor_disabled:firecrawl')
    lid = _list_with_rows(db, uid, ['Acme'])
    SEARCH['Acme'] = [ACME_PAGE]
    ANSWERS['Acme'] = {'value': 'Yes, 120 hospitals', 'source_url': ACME_PAGE['url']}
    tc.post(f'/v1/lists/{lid}/columns', json={'label': 'H', 'prompt': 'hospitals?'})

    await _fill(db, lid, 'ai_h', provider=FakeProvider())

    fill = next(c for c in tc.get(f'/v1/lists/{lid}').json()['columns'] if c['key'] == 'ai_h')['config']['fill']
    assert (fill['status'], fill['error']) == ('failed', error)
    assert tc.get(f'/v1/lists/{lid}/rows').json()['rows'][0]['cell_meta'] == {}
    assert _balance(db, uid) == credits


def _fill_state(tc, lid, key):
    return next(c for c in tc.get(f'/v1/lists/{lid}').json()['columns'] if c['key'] == key)['config']['fill']


@needs_db
def test_dead_fill_reads_stale_and_can_be_refilled(client, db, queued):
    tc, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    tc.post(f'/v1/lists/{lid}/columns', json={'label': 'H', 'prompt': 'hospitals?'})

    def set_fill(fill):
        db.execute(
            "UPDATE list_columns SET config = jsonb_set(config, '{fill}', %s) WHERE list_id = %s AND key = 'ai_h'",
            (json.dumps(fill), lid),
        )

    # Queued long ago (broker backlog) but claimed a minute ago: live, no second run.
    set_fill({'status': 'running', 'queued_at': '2020-01-01T00:00:00+00:00', 'started_at': _ago(1)})
    assert _fill_state(tc, lid, 'ai_h')['status'] == 'running'
    assert tc.post(f'/v1/lists/{lid}/columns/ai_h/fill').status_code == 409

    # Worker killed: running since 30 minutes reads failed and can be refilled.
    set_fill({'status': 'running', 'queued_at': _ago(31), 'started_at': _ago(30)})
    assert (_fill_state(tc, lid, 'ai_h')['status'], _fill_state(tc, lid, 'ai_h')['error']) == ('failed', 'fill_stale')
    assert tc.post(f'/v1/lists/{lid}/columns/ai_h/fill').status_code == 202
    assert _fill_state(tc, lid, 'ai_h')['status'] == 'queued'


def _ago(minutes):
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


@needs_db
async def test_crash_after_reserving_releases_hold_and_marks_failed(client, db, queued, monkeypatch):
    tc, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    tc.post(f'/v1/lists/{lid}/columns', json={'label': 'H', 'prompt': 'hospitals?'})

    class SoftTimeLimit(BaseException):
        pass

    def boom():
        raise SoftTimeLimit

    monkeypatch.setattr('app.llm.router.get_provider', boom)
    with pytest.raises(SoftTimeLimit):
        await _fill(db, lid, 'ai_h')

    fill = _fill_state(tc, lid, 'ai_h')
    assert (fill['status'], fill['error'], fill['credits']) == ('failed', 'fill_crashed', 0)
    assert _balance(db, uid) == 50
    holds = db.execute("SELECT status FROM credit_transactions WHERE user_id = %s AND type = 'reservation'", (uid,))
    assert [h['status'] for h in holds.fetchall()] == ['released']


@needs_db
async def test_plan_cap_bounds_filled_cells_across_runs(db):
    """#59: a plan approved for 2 rows researches at most 2 cells, however many rows the list has."""
    from psycopg.types.json import Jsonb

    from app.lists.ai_columns import queue_fill

    uid = _user(db)
    names = ['Acme', 'Beta', 'Gamma', 'Delta', 'Eps']
    lid = _list_with_rows(db, uid, names)
    for n in names:
        SEARCH[n] = [ACME_PAGE]
        ANSWERS[n] = {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    for _ in range(2):  # a second plan run (end of turn, retry) finds the cap already used
        assert queue_fill(db, lid, 'ai_h', cap=2)
        await _fill(db, lid, 'ai_h', provider=FakeProvider())
    filled = db.execute(
        "SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND cell_meta->'ai_h'->>'status' = 'filled'", (lid,)
    ).fetchone()['n']
    assert filled == 2 and _balance(db, uid) == 48


@needs_db
async def test_fill_skips_rows_missing_a_required_column(db):
    from psycopg.types.json import Jsonb

    from app.lists.ai_columns import queue_fill
    from app.lists.rows import add_rows, create_list

    uid = _user(db)
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='Hospitals', entity='company')
    add_rows(db, lid, 'company', [{'name': 'Acme', 'city': 'Milano'}, {'name': 'Beta'}])
    required = db.execute(
        "UPDATE list_columns SET config = config || '{\"required\": true}' WHERE list_id = %s AND key = 'city'", (lid,)
    )
    assert required.rowcount == 1
    for n in ('Acme', 'Beta'):
        SEARCH[n] = [ACME_PAGE]
        ANSWERS[n] = {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h')
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    cells = {
        r['cells']['name']: (r['cells'].get('ai_h'), r['cell_meta'].get('ai_h', {}).get('status'))
        for r in db.execute('SELECT cells, cell_meta FROM list_rows WHERE list_id = %s', (lid,))
    }
    assert cells == {'Acme': ('Yes', 'filled'), 'Beta': (None, None)}
    assert _balance(db, uid) == 49


@needs_db
async def test_row_excluded_mid_fill_is_not_written_or_billed(db):
    from psycopg.types.json import Jsonb

    from app.lists.ai_columns import queue_fill

    uid = _user(db)
    lid = _list_with_rows(db, uid, ['Acme', 'Beta'])
    for n in ('Acme', 'Beta'):
        SEARCH[n] = [ACME_PAGE]
        ANSWERS[n] = {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )

    class ExcludesBeta(FakeProvider):
        async def chat(self, *, system, messages, **kw):
            if json.loads(messages[0]['content'])['company']['name'] == 'Beta':
                db.execute("UPDATE list_rows SET excluded = true WHERE cells->>'name' = 'Beta'")
            return await super().chat(system=system, messages=messages, **kw)

    assert queue_fill(db, lid, 'ai_h')
    await _fill(db, lid, 'ai_h', provider=ExcludesBeta())
    cells = {
        r['cells']['name']: (r['cells'].get('ai_h'), 'ai_h' in r['cell_meta'])
        for r in db.execute('SELECT cells, cell_meta FROM list_rows WHERE list_id = %s', (lid,))
    }
    assert cells == {'Acme': ('Yes', True), 'Beta': (None, False)}
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_h'").fetchone()['f']
    assert (fill['filled'], fill['credits'], _balance(db, uid)) == (1, 1, 49)


# ── live progress (#252) ───────────────────────────────────────────────────


def _ai_list(db, uid, names, cap=None):
    from psycopg.types.json import Jsonb

    from app.lists.ai_columns import queue_fill

    lid = _list_with_rows(db, uid, names)
    for n in names:
        SEARCH[n] = [ACME_PAGE]
        ANSWERS[n] = {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h', cap)
    return lid


async def _frames(db, lid: str, uid: str, count: int, last_event_id: str | None) -> list[tuple[str, str, dict]]:
    from contextlib import aclosing

    from app.lists import routes

    user = member(db, uid)
    with as_request(user):  # the request's scope, which the response's iteration keeps
        resp = await routes.fill_stream(uuid.UUID(lid), user=user, last_event_id=last_event_id)

    async def read() -> list[str]:
        out: list[str] = []
        async with aclosing(resp.body_iterator) as it:
            async for chunk in it:
                out.append(str(chunk))
                if len(out) == count:
                    break
        return out

    frames = []
    for f in await asyncio.wait_for(read(), 5):
        if f.startswith(':'):
            frames.append(('ping', '', {}))
            continue
        head, entry_id, data = f.strip().split('\n')
        frames.append((head.removeprefix('event: '), entry_id.removeprefix('id: '), json.loads(data[6:])))
    return frames


@needs_db
async def test_fill_publishes_each_change_after_its_commit(db, monkeypatch):
    from app.lists import ai_columns

    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta'])
    seen: list[tuple[str, dict]] = []
    real = ai_columns._publish

    def publish(list_id, event, data):
        # `db` is its own autocommit connection: it sees only what the run has committed.
        if event == 'row':
            meta = db.execute('SELECT cell_meta FROM list_rows WHERE id = %s', (data['row_id'],)).fetchone()
            assert meta['cell_meta']['ai_h'] == data['cell_meta']['ai_h']
        else:
            fill = db.execute(
                "SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)
            )
            assert fill.fetchone()['f'] == data['fill']
        seen.append((event, data))
        return real(list_id, event, data)

    monkeypatch.setattr(ai_columns, '_publish', publish)
    await _fill(db, lid, 'ai_h', provider=FakeProvider())

    assert [e for e, _ in seen] == ['fill', 'row', 'row', 'fill']
    assert (seen[0][1]['fill']['status'], seen[-1][1]['fill']['status'], seen[-1][1]['fill']['filled']) == (
        'running',
        'done',
        2,
    )
    assert {d['cells']['ai_h'] for e, d in seen if e == 'row'} == {'Yes'}
    # The whole run is on the list's stream, in order, for the owner to replay.
    frames = await _frames(db, lid, uid, 4, last_event_id='0-0')
    assert [(e, d) for e, _, d in frames] == seen
    replay = await _frames(db, lid, uid, 3, last_event_id=frames[0][1])
    assert [e for e, _, _ in replay] == ['row', 'row', 'fill']


@needs_db
async def test_fill_survives_redis_down(db, monkeypatch):
    from app.agent import events

    def down():
        raise ConnectionError('redis down')

    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta'])
    monkeypatch.setattr(events, 'redis_sync', down)
    with capture_logs() as logs:
        await _fill(db, lid, 'ai_h', provider=FakeProvider())
    failed = [e for e in logs if e['event'] == 'stream.publish_failed']
    # The two fill frames are always tried; the row frames stop after the first failure, not one per cell.
    assert [(e['stream'], e['frame']) for e in failed] == [('fill', 'fill'), ('fill', 'row'), ('fill', 'fill')]
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_h'").fetchone()['f']
    assert (fill['status'], fill['filled'], _balance(db, uid)) == ('done', 2, 48)


@needs_db
async def test_fill_stream_is_owner_scoped_and_starts_at_the_load_cursor(client, db, monkeypatch):
    from app.agent import events
    from app.lists import ai_columns

    tc, uid = client
    monkeypatch.setattr(events, 'PING_EVERY_MS', 50)
    theirs = _ai_list(db, _user(db), ['Beta'])
    assert tc.get(f'/v1/lists/{theirs}/stream').status_code == 404
    assert tc.get(f'/v1/lists/{uuid.uuid4()}/stream').status_code == 404

    mine = _ai_list(db, uid, ['Acme'])
    ai_columns._publish(mine, 'fill', {'key': 'ai_h', 'fill': {'status': 'queued'}})  # before the load
    cursor = tc.get(f'/v1/lists/{mine}').json()['fill_cursor']
    assert cursor != '0-0'
    idle = _list_with_rows(db, uid, ['Gamma'])
    assert tc.get(f'/v1/lists/{idle}').json()['fill_cursor'] is None  # nothing fills: no Redis read
    await _fill(db, theirs, 'ai_h', provider=FakeProvider())  # another list's frames never show up
    await _fill(db, mine, 'ai_h', provider=FakeProvider())
    frames = await _frames(db, mine, uid, 3, last_event_id=cursor)
    assert [(e, d.get('row_id') is not None) for e, _, d in frames] == [('fill', False), ('row', True), ('fill', False)]
    assert frames[1][2]['row_id'] in {
        str(r['id']) for r in db.execute('SELECT id FROM list_rows WHERE list_id = %s', (mine,))
    }

    # No (or an invalid / out-of-range) Last-Event-ID starts from now: nothing replayed.
    for bad in (None, 'not-an-id', f'{2**64}-0'):
        assert await _frames(db, mine, uid, 1, last_event_id=bad) == [('ping', '', {})]


@needs_db
async def test_an_open_fill_stream_ends_once_the_list_is_gone(db, monkeypatch):
    """#413: the stream re-checks (every MEMBER_RECHECK_SECONDS, 0 here) the list the connect checked. A list deleted
    while it is open gets an error frame and no more cells. A member who left: tests/test_stream_member_sql.py."""
    from contextlib import aclosing

    from app.agent import events
    from app.lists import ai_columns, fill_stream

    monkeypatch.setattr(fill_stream, 'MEMBER_RECHECK_SECONDS', 0)
    ada = _user(db)
    team = member(db, ada).org_id
    lid = _list_with_rows(db, ada, ['Acme'])
    after = events.last_id(ai_columns.fill_stream_key(lid))
    assert after is not None
    ai_columns._publish(lid, 'row', {'row_id': 'r1', 'cells': {}, 'cell_meta': {}})
    stream = fill_stream.fill_sse(uuid.UUID(lid), after, team)
    with as_request(member(db, ada)):  # the request that opened the stream
        async with aclosing(stream) as frames:
            assert (await asyncio.wait_for(anext(frames), 5)).startswith('event: row')
            db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (lid,))
            ai_columns._publish(lid, 'row', {'row_id': 'r2', 'cells': {}, 'cell_meta': {}})
            rest = await asyncio.wait_for(_drain(frames), 5)
    assert len(rest) == 1 and rest[0].startswith('event: error') and 'LIST.NOT_FOUND' in rest[0]


async def _drain(frames) -> list[str]:
    return [f async for f in frames]


@needs_db
async def test_a_redis_blip_mid_run_still_sends_the_last_fill_frame(db, monkeypatch, event_redis):
    from app.agent import events
    from app.lists.ai_columns import fill_stream_key

    calls = 0

    def flaky():
        nonlocal calls
        calls += 1
        if calls == 2:  # the first row frame; the claim frame (call 1) went through
            raise ConnectionError('redis blip')
        return event_redis

    uid = _user(db)
    lid = _ai_list(db, uid, ['Acme', 'Beta', 'Gamma'])
    monkeypatch.setattr(events, 'redis_sync', flaky)
    with capture_logs() as logs:
        await _fill(db, lid, 'ai_h', provider=FakeProvider())
    failed = [(e['stream'], e['frame']) for e in logs if e['event'] == 'stream.publish_failed']
    assert failed == [('fill', 'row')]
    frames = [(f[b'event'], json.loads(f[b'data'])) for _, f in event_redis.xrange(fill_stream_key(lid))]
    assert frames[0][0] == b'fill' and frames[0][1]['fill']['status'] == 'running'
    assert frames[-1][0] == b'fill' and frames[-1][1]['fill']['status'] == 'done'  # the view leaves `running`


@needs_db
async def test_a_fill_queued_by_a_member_removed_since_charges_nothing(db):
    """#323 (from #324's review): the worker runs a fill as the member who queued it. A fill a member queued and who
    was removed before it ran fails `not_a_member`, holding nothing, and the task ends; one queued by a member still
    there spends as them, even on a list a removed colleague made."""
    from psycopg.types.json import Jsonb

    from app.core.scope import ActorGone, acting_as
    from app.lists.ai_columns import queue_fill

    ada, bo = _user(db), _user(db)
    team = member(db, ada).org_id
    member(db, bo)  # Bo's own organization first: the team is a second one
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (team, bo),
    )
    lid = _list_with_rows(db, ada, ['Acme'])
    SEARCH['Acme'], ANSWERS['Acme'] = [ACME_PAGE], {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h', by=bo)
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, team))
    with acting_as(team, bo), pytest.raises(ActorGone):
        await fill_column(lid, 'ai_h', provider=FakeProvider())
    fill = db.execute(
        "SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)
    ).fetchone()['f']
    assert (fill['status'], fill['error'], fill['credits']) == ('failed', 'not_a_member', 0)
    assert db.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0

    cy = _user(db)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (team, cy),
    )
    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (ada, team))  # the list's maker
    assert queue_fill(db, lid, 'ai_h', by=cy)
    with acting_as(team, cy):
        await fill_column(lid, 'ai_h', provider=FakeProvider())
    fill = db.execute(
        "SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)
    ).fetchone()['f']
    assert (fill['status'], fill['filled']) == ('done', 1)
    spender = db.execute("SELECT DISTINCT user_id::text AS u FROM credit_transactions WHERE type = 'deduction'")
    assert [r['u'] for r in spender] == [cy]


@needs_db
async def test_a_header_naming_a_teammate_claims_nothing(db, event_redis):
    """A message for Ada's fill whose header names Bo, another member of the team (a forged message): Bo's scope
    claims nothing, so the fill stays queued as it was, nothing is held or sent, and Ada's own run still fills it."""
    from psycopg.types.json import Jsonb

    from app.lists.ai_columns import fill_stream_key, queue_fill

    ada, bo = _user(db), _user(db)
    team = member(db, ada).org_id
    member(db, bo)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (team, bo),
    )
    lid = _list_with_rows(db, ada, ['Acme'])
    SEARCH['Acme'], ANSWERS['Acme'] = [ACME_PAGE], {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h')  # by nobody: the list's creator, Ada
    queued = db.execute("SELECT config FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)).fetchone()

    with acting_as(team, bo):
        await fill_column(lid, 'ai_h', provider=FakeProvider())

    after = db.execute("SELECT config FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)).fetchone()
    assert after == queued
    assert db.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0
    assert event_redis.xlen(fill_stream_key(lid)) == 0
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,))
    assert {k: v for k, v in fill.fetchone()['f'].items() if k in ('status', 'filled')} == {
        'status': 'done',
        'filled': 1,
    }


@needs_db
async def test_a_fill_whose_member_is_removed_midway_stops_before_the_next_search(db, monkeypatch):
    """Bo is removed from the team during the first cell's search. Writing that cell gets ``ActorGone``, which
    cancels the next cell's slow search before it returns, and the last one before it starts (one cell at a time
    here); the organization then fails the fill ``not_a_member`` and charges nothing. With the gather of
    ``return_exceptions=True`` the fill had, every search ran to its end."""
    from psycopg.types.json import Jsonb

    from app.core.scope import ActorGone, acting_as
    from app.lists import ai_columns
    from app.lists.ai_columns import queue_fill

    ada, bo = _user(db), _user(db)
    team = member(db, ada).org_id
    member(db, bo)
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (team, bo),
    )
    names = ['Acme', 'Beta', 'Gamma']
    lid = _list_with_rows(db, ada, names)
    for name in names:
        SEARCH[name], ANSWERS[name] = [ACME_PAGE], {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    searched: list[str] = []

    async def search(args, ctx) -> ToolResult:
        if searched:
            await asyncio.sleep(10)  # reached only by a fill that goes on after the stop: the count below fails
        else:
            db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, team))
        searched.append(args['query'])
        return await _fake_search(args, ctx)

    monkeypatch.setitem(registry._REGISTRY, 'web_search', ToolDef('web_search', 'test', {}, search))
    monkeypatch.setattr(ai_columns, 'CONCURRENCY', 1)
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    assert queue_fill(db, lid, 'ai_h', by=bo)

    with acting_as(team, bo), pytest.raises(ActorGone):
        await fill_column(lid, 'ai_h', provider=FakeProvider())

    assert len(searched) == 1
    fill = db.execute(
        "SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,)
    ).fetchone()['f']
    assert (fill['status'], fill['error'], fill['credits'], fill['filled']) == ('failed', 'not_a_member', 0, 0)
    held = db.execute("SELECT count(*) AS n FROM credit_transactions WHERE status = 'reserved'").fetchone()['n']
    assert held == 0


def _ai_column(db, lid, key, fill=None):
    from psycopg.types.json import Jsonb

    config = {'prompt': 'Hospitals?', **({'fill': fill} if fill else {})}
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, %s, %s, 'ai', 9, %s)",
        (lid, key, key, Jsonb(config)),
    )


@needs_db
async def test_a_run_of_unanswerable_cells_stops_after_the_first_empty_ones(db):
    from app.lists.ai_columns import EARLY_STOP_AFTER, queue_fill

    uid = _user(db)
    names = [f'Co{i:02d}' for i in range(EARLY_STOP_AFTER + 20)]
    lid = _list_with_rows(db, uid, names)
    for n in names:
        SEARCH[n] = [ACME_PAGE]
        ANSWERS[n] = {'value': None, 'source_url': None}
    _ai_column(db, lid, 'ai_h')
    assert queue_fill(db, lid, 'ai_h')
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,))
    fill = fill.fetchone()['f']
    attempted = fill['empty'] + fill['failed']
    assert fill['status'] == 'done' and fill['filled'] == 0
    assert EARLY_STOP_AFTER <= attempted < EARLY_STOP_AFTER + 5  # the cells already in flight finish
    assert _balance(db, uid) == 50


@needs_db
async def test_one_filled_cell_early_does_not_turn_the_early_stop_off(db):
    from app.lists.ai_columns import EARLY_STOP_AFTER, queue_fill

    uid = _user(db)
    names = [f'Co{i:02d}' for i in range(EARLY_STOP_AFTER + 30)]
    lid = _list_with_rows(db, uid, names)
    for n in names:
        SEARCH[n] = [ACME_PAGE]
        ANSWERS[n] = {'value': None, 'source_url': None}
    ANSWERS['Co00'] = {'value': 'Yes', 'source_url': ACME_PAGE['url']}
    _ai_column(db, lid, 'ai_h')
    assert queue_fill(db, lid, 'ai_h')
    await _fill(db, lid, 'ai_h', provider=FakeProvider())
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_h'", (lid,))
    fill = fill.fetchone()['f']
    assert fill['filled'] == 1 and fill['empty'] < len(names) - 5


@needs_db
def test_the_fill_quota_counts_only_claims_and_stops_the_eleventh(client, db, queued, fill_counter):
    from app.lists.fill_limits import FILLS_PER_HOUR

    tc, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    assert tc.post(f'/v1/lists/{uuid.uuid4()}/columns', json={'label': 'X', 'prompt': 'what?'}).status_code == 404
    assert tc.post(f'/v1/lists/{lid}/columns/name/fill').status_code == 404
    assert tc.post(f'/v1/lists/{lid}/columns', json={'label': ' ', 'prompt': 'what?'}).status_code == 422
    assert fill_counter.counts == {}  # nothing was claimed
    for i in range(FILLS_PER_HOUR):
        db.execute("UPDATE list_columns SET config = config - 'fill' WHERE list_id = %s AND kind = 'ai'", (lid,))
        assert tc.post(f'/v1/lists/{lid}/columns', json={'label': f'C{i}', 'prompt': 'what?'}).status_code == 201
    refused = tc.post(f'/v1/lists/{lid}/columns', json={'label': 'One more', 'prompt': 'what?'})
    assert refused.status_code == 429 and refused.headers['retry-after'] == '3600'
    assert refused.json()['detail']['code'] == 'LIST.AI_FILL_RATE_LIMITED'
    assert db.execute("SELECT count(*) AS n FROM list_columns WHERE label = 'One more'").fetchone()['n'] == 0
    assert len(queued) == FILLS_PER_HOUR


def _org(db, lid):
    return db.execute('SELECT organization_id::text AS o FROM lists WHERE id = %s', (lid,)).fetchone()['o']


@needs_db
def test_the_plan_path_meets_the_quota_and_marks_what_it_refuses(client, db, fill_counter, monkeypatch):
    """A chat plan's fills go through the same quota; the refused columns read failed on the list and are not retried."""
    from celery.exceptions import Retry

    from app import worker
    from app.core.scope import acting_as
    from app.lists.fill_limits import FILLS_PER_HOUR

    _, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    keys = [f'ai_{i}' for i in range(FILLS_PER_HOUR + 3)]
    for k in keys:
        _ai_column(db, lid, k)
    complete = keys[-1]  # nothing left to research: the refusal must not mark it failed
    db.execute(
        'UPDATE list_rows SET cell_meta = jsonb_build_object(%s::text, \'{"status": "filled"}\'::jsonb) WHERE list_id = %s',
        (complete, lid),
    )
    fill_counter.counts[f'rl:list:ai_fill:{_org(db, lid)}'] = 0
    delayed: list[tuple] = []
    monkeypatch.setattr(worker.fill_ai_column, 'delay', lambda *a: delayed.append(a))
    monkeypatch.setattr('app.lists.fill_limits.LIVE_FILLS', 99)
    # only the complete column waits: it is neither queued nor refused
    with acting_as(_org(db, lid), uid), pytest.raises(Retry):
        worker.fill_plan_columns.run(lid, keys, 5, None)
    assert [k for _, k in delayed] == keys[:FILLS_PER_HOUR]
    refused = db.execute(
        "SELECT key, config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = ANY(%s) AND config->'fill'->>'status' = 'failed'",
        (lid, keys),
    ).fetchall()
    assert {r['key'] for r in refused} == set(keys[FILLS_PER_HOUR:-1])
    assert {r['f']['error'] for r in refused} == {'fill_rate_limited'}


@needs_db
def test_a_column_with_nothing_to_research_uses_no_quota(client, db, fill_counter):
    from app.core.scope import acting_as
    from app.lists.plan_fills import queue_plan_fills

    _, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    _ai_column(db, lid, 'ai_done')
    db.execute('UPDATE list_rows SET cell_meta = \'{"ai_done": {"status": "filled"}}\' WHERE list_id = %s', (lid,))
    with acting_as(_org(db, lid), uid):
        assert queue_plan_fills(lid, ['ai_done'], 5, uid, None) == (['ai_done'], [])
    assert fill_counter.counts == {}


@needs_db
def test_a_plan_cannot_take_a_list_past_twenty_ai_columns(client, db):
    from app.agent import plan
    from app.lists.fill_limits import COLUMNS_PER_LIST

    _, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    for i in range(COLUMNS_PER_LIST - 1):
        _ai_column(db, lid, f'ai_c{i}')
    columns = [
        {'key': f'ai_new{i}', 'label': f'New {i}', 'required': False, 'research': 'Hospitals?'} for i in range(3)
    ]
    plan.apply_columns(db, lid, {'entity': 'company', 'columns': columns})
    n = db.execute("SELECT count(*) AS n FROM list_columns WHERE list_id = %s AND kind = 'ai'", (lid,)).fetchone()['n']
    assert n == COLUMNS_PER_LIST
    too_many = {
        'mode': 'build',
        'entity': 'company',
        'target_rows': 5,
        'columns': [{'label': f'Q{i}', 'research': 'Hospitals served?'} for i in range(COLUMNS_PER_LIST + 1)],
    }
    assert 'at most' in plan.build(too_many)


@needs_db
def test_an_organization_has_at_most_three_live_fills(client, db, fill_counter):
    from datetime import UTC, datetime

    from app.core.scope import acting_as
    from app.lists.ai_columns import queue_fill
    from app.lists.fill_limits import LIVE_FILLS, FillLimited

    _, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    org = db.execute('SELECT organization_id::text AS o FROM lists WHERE id = %s', (lid,)).fetchone()['o']
    live = {'status': 'queued', 'queued_at': datetime.now(UTC).isoformat()}
    for i in range(LIVE_FILLS):
        _ai_column(db, lid, f'ai_live{i}', live)
    _ai_column(db, lid, 'ai_next')
    with acting_as(org, uid), pytest.raises(FillLimited) as limited:
        queue_fill(db, lid, 'ai_next', by=uid)
    assert limited.value.code == 'LIST.AI_FILL_BUSY'


@needs_db
def test_a_list_holds_at_most_twenty_ai_columns(client, db, queued):
    from app.lists.fill_limits import COLUMNS_PER_LIST

    tc, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    for i in range(COLUMNS_PER_LIST):
        _ai_column(db, lid, f'ai_c{i}')
    refused = tc.post(f'/v1/lists/{lid}/columns', json={'label': 'Too many', 'prompt': 'what?'})
    assert refused.status_code == 422 and refused.json()['detail']['code'] == 'LIST.TOO_MANY_AI_COLUMNS'
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'ai_c0'", (lid,))
    assert tc.post(f'/v1/lists/{lid}/columns', json={'label': 'Fits', 'prompt': 'what?'}).status_code == 201


@needs_db
def test_restoring_an_ai_column_respects_the_list_cap(client, db, queued):
    from app.lists.fill_limits import COLUMNS_PER_LIST

    tc, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    for i in range(COLUMNS_PER_LIST):
        _ai_column(db, lid, f'ai_c{i}')
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'ai_c0'", (lid,))
    assert tc.post(f'/v1/lists/{lid}/columns', json={'label': 'Fits', 'prompt': 'what?'}).status_code == 201
    refused = tc.post(f'/v1/lists/{lid}/columns/ai_c0/restore')
    assert refused.status_code == 422 and refused.json()['detail']['code'] == 'LIST.TOO_MANY_AI_COLUMNS'
    db.execute("UPDATE list_columns SET deleted_at = now() WHERE list_id = %s AND key = 'ai_c1'", (lid,))
    assert tc.post(f'/v1/lists/{lid}/columns/ai_c0/restore').status_code == 200


@needs_db
def test_the_live_count_runs_under_the_organizations_lock(client, db, fill_counter):
    """Two claims of one organization count one after the other: the check holds an advisory lock to the commit."""
    from app.lists.fill_limits import check

    _, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    with db.transaction():
        check(db, lid, 'false')
        held = db.execute("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()")
        assert held.fetchone()['n'] == 1


@needs_db
def test_a_plan_past_twenty_ai_columns_is_refused_when_proposed(client, db):
    from types import SimpleNamespace

    from app.agent import plan
    from app.agent.tools.handlers.propose_plan import _store
    from app.core.scope import acting_as
    from app.lists.fill_limits import COLUMNS_PER_LIST

    _, uid = client
    lid = _list_with_rows(db, uid, ['Acme'])
    for i in range(COLUMNS_PER_LIST - 1):
        _ai_column(db, lid, f'ai_c{i}')
    new = {'key': 'ai_new', 'label': 'New', 'required': False, 'research': 'Hospitals?'}
    existing = {'key': 'ai_c0', 'label': 'C0', 'required': False, 'research': 'Hospitals?'}
    assert plan.research_over_cap(db, lid, {'columns': [new, existing]}) is None
    over = plan.research_over_cap(db, lid, {'columns': [new, {**new, 'key': 'ai_new2'}]})
    assert over and 'at most 20' in over
    session = db.execute('SELECT chat_session_id::text AS s FROM lists WHERE id = %s', (lid,)).fetchone()['s']
    built = plan.build(
        {
            'mode': 'build',
            'entity': 'company',
            'target_rows': 5,
            'columns': [
                {'key': 'name'},
                {'label': 'A', 'research': 'Hospitals served?'},
                {'label': 'B', 'research': 'Beds?'},
            ],
            'stages': [{'tool': 'search_companies', 'units': 1}],
        }
    )
    assert isinstance(built, dict)
    ctx = SimpleNamespace(session_id=session, user_id=uid, job_id=None)
    with acting_as(_org(db, lid), uid):
        assert 'at most 20' in _store({'list_id': lid}, built, ctx)
