import asyncio
import contextlib
import json
import threading
from datetime import UTC, datetime

import pytest
from psycopg.types.json import Jsonb

from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.lists import ai_columns
from app.llm.base import LLMResponse
from tests.test_agent import _balance, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_ai_columns import _fill, _list_with_rows
from tests.test_ai_columns import fill_counter as fill_counter
from tests.test_credit_callers_sql import _ledger
from tests.test_row_phone_credits_sql import _reap

pytestmark = needs_db

NEWS = {'url': 'https://acme.test/news', 'title': 'News'}
ANSWERS: dict[str, dict | Exception] = {}


def _line(name: str, text: str | None = None) -> dict:
    return {'text': text or f'Congrats {name} on the 12 million round.', 'used': ['ai_news']}


class Model:
    def __init__(self, on_ask=None) -> None:
        self.asked: list[str] = []
        self.facts: list[list[str]] = []
        self.on_ask = on_ask

    async def chat(self, *, system, messages, **kw):
        content = json.loads(messages[0]['content'])
        name = content['company']['name']
        self.asked.append(name)
        self.facts.append(sorted(content['research']))
        if self.on_ask:
            self.on_ask(name)
        answer = ANSWERS[name]
        if isinstance(answer, Exception):
            raise answer
        return LLMResponse(text=json.dumps(answer))


@pytest.fixture(autouse=True)
def searches(monkeypatch):
    calls: list[dict] = []

    async def search(args, ctx):
        calls.append(args)
        return ToolResult(ok=False, error='a snippet never searches')

    monkeypatch.setitem(
        registry._REGISTRY, 'web_search', ToolDef('web_search', 'test', {}, search, provider='firecrawl')
    )
    ANSWERS.clear()
    return calls


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    from app import worker

    calls: list[tuple] = []
    monkeypatch.setattr(worker.fill_ai_column, 'delay', lambda *a: calls.append(a))
    return calls


def _research(db, lid: str, names: list[str], key: str = 'ai_news') -> None:
    for name in names:
        meta = {'status': 'filled', 'source': NEWS, 'error': None, 'at': datetime.now(UTC).isoformat()}
        db.execute(
            'UPDATE list_rows SET cells = cells || %s, cell_meta = cell_meta || %s'
            " WHERE list_id = %s AND cells->>'name' = %s",
            (Jsonb({key: f'{name} raised 12 million'}), Jsonb({key: meta}), lid, name),
        )
        ANSWERS[name] = _line(name)


def _researched_list(db, uid: str, names: list[str], researched: list[str]) -> tuple[str, dict[str, str]]:
    lid = _list_with_rows(db, uid, names)
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'ai_news', 'Recent news', 'ai', 9, %s)",
        (lid, Jsonb({'prompt': 'Recent news?', 'fill': {'status': 'done'}})),
    )
    _research(db, lid, researched)
    rows = db.execute("SELECT id::text, cells->>'name' AS name FROM list_rows WHERE list_id = %s", (lid,)).fetchall()
    return lid, {r['name']: r['id'] for r in rows}


def _snippet(tc, lid: str, rows: list[str], approved: int | None, inputs=('ai_news',)):
    body = {'label': 'Opener', 'prompt': 'One line on their news', 'mode': 'snippet', 'inputs': list(inputs)}
    body |= {'row_ids': rows} | ({'approved_credits': approved} if approved is not None else {})
    return tc.post(f'/v1/lists/{lid}/columns', json=body)


def _cells(db, lid: str, key: str = 'ai_opener') -> dict[str, tuple]:
    rows = db.execute(
        "SELECT cells->>'name' AS name, cells->>%s AS value, cell_meta->%s->>'status' AS status,"
        " cell_meta->%s->>'error' AS error FROM list_rows WHERE list_id = %s",
        (key, key, key, lid),
    ).fetchall()
    return {r['name']: (r['status'], r['value'], r['error']) for r in rows}


def _fill_state(db, lid: str) -> dict:
    return db.execute(
        "SELECT config->'fill' AS f FROM list_columns WHERE list_id = %s AND key = 'ai_opener'", (lid,)
    ).fetchone()['f']


def _charges(db) -> list[tuple]:
    rows = db.execute(
        'SELECT c.keys, c.credits, c.state FROM row_charges c JOIN credit_transactions t ON t.id = c.hold_id'
        " WHERE t.reference_type = 'ai_column' ORDER BY c.created_at"
    ).fetchall()
    return [(r['keys'], r['credits'], r['state']) for r in rows]


async def test_a_snippet_charges_only_the_rows_it_fills_and_a_retry_charges_once(client, db, searches, enqueued):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme', 'Beta', 'Gamma', 'Delta'], ['Acme', 'Beta', 'Gamma'])
    ANSWERS['Beta'] = _line('Beta', 'Congrats Beta on the 15 million round.')
    ANSWERS['Gamma'] = RuntimeError('timeout')

    est = tc.post(f'/v1/lists/{lid}/columns/estimate', json={'inputs': ['ai_news'], 'row_ids': list(ids.values())})
    assert est.json() == {'research_rows': 3, 'credits': 3}
    refused = _snippet(tc, lid, list(ids.values()), None)
    assert (refused.status_code, refused.json()['detail']['code']) == (422, 'LIST.SNIPPET_APPROVAL_REQUIRED')
    assert _snippet(tc, lid, list(ids.values()), 0).status_code == 422
    assert _snippet(tc, lid, [], 3).status_code == 422
    created = _snippet(tc, lid, list(ids.values()), est.json()['credits'])
    assert (created.status_code, created.json()['config']['mode'], enqueued) == (201, 'snippet', [(lid, 'ai_opener')])

    model = Model()
    await _fill(db, lid, 'ai_opener', provider=model)

    assert _cells(db, lid) == {
        'Acme': ('filled', 'Congrats Acme on the 12 million round.', None),
        'Beta': ('empty', None, 'ungrounded'),
        'Gamma': ('failed', None, 'snippet_failed'),
        'Delta': (None, None, None),
    }
    fill = _fill_state(db, lid)
    assert (fill['status'], fill['filled'], fill['empty'], fill['failed'], fill['credits']) == ('done', 1, 1, 1, 1)
    assert _balance(db, uid) == 49

    ANSWERS['Gamma'] = _line('Gamma')
    for _ in range(2):  # the retry fills the failed cell; a second retry finds nothing to fill
        assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/fill').status_code == 202
        await _fill(db, lid, 'ai_opener', provider=model)

    assert _cells(db, lid)['Gamma'] == ('filled', 'Congrats Gamma on the 12 million round.', None)
    assert sorted(model.asked) == ['Acme', 'Beta', 'Gamma', 'Gamma']
    assert (_balance(db, uid), _charges(db)) == (48, [(['ai_opener'], 1, 'charged')] * 2)
    assert searches == []


async def test_approved_credits_bound_the_fill_and_its_retries(client, db):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme', 'Beta', 'Gamma'], ['Acme', 'Beta', 'Gamma'])
    assert _snippet(tc, lid, list(ids.values()), 2).status_code == 201

    await _fill(db, lid, 'ai_opener', provider=Model())
    assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/fill').status_code == 202
    await _fill(db, lid, 'ai_opener', provider=Model())

    assert sorted(s for s, *_ in _cells(db, lid).values() if s) == ['filled', 'filled']
    assert _balance(db, uid) == 48


async def test_a_regenerate_overwrites_only_with_a_new_snippet_and_charges_a_row_once_per_run(client, db, fill_counter):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme', 'Beta', 'Gamma'], ['Acme', 'Beta', 'Gamma'])
    assert _snippet(tc, lid, list(ids.values()), 3).status_code == 201
    await _fill(db, lid, 'ai_opener', provider=Model())
    assert _balance(db, uid) == 47

    ANSWERS['Acme'] = _line('Acme', 'Acme, 12 million is a fine start.')
    ANSWERS['Beta'] = _line('Beta', 'Beta, 99 million is a fine start.')  # invented: Beta keeps its snippet
    body = {'row_ids': [ids['Acme'], ids['Beta']], 'approved_credits': 2}
    assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/regenerate', json=body).status_code == 202
    assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/regenerate', json=body).status_code == 409
    model = Model()
    await _fill(db, lid, 'ai_opener', provider=model)

    regenerated = {
        'Acme': ('filled', 'Acme, 12 million is a fine start.', None),
        'Beta': ('filled', 'Congrats Beta on the 12 million round.', None),
        'Gamma': ('filled', 'Congrats Gamma on the 12 million round.', None),
    }
    assert (_cells(db, lid), sorted(model.asked), _balance(db, uid)) == (regenerated, ['Acme', 'Beta'], 46)
    fill = _fill_state(db, lid)
    assert (fill['status'], fill['filled'], fill['empty'], fill['credits']) == ('done', 1, 1, 1)

    db.execute(
        "UPDATE list_columns SET config = jsonb_set(config, '{fill,status}', '\"queued\"')"
        " WHERE list_id = %s AND key = 'ai_opener'",
        (lid,),
    )
    again = Model()
    await _fill(db, lid, 'ai_opener', provider=again)

    assert (_cells(db, lid), again.asked, _balance(db, uid)) == (regenerated, ['Beta'], 46)
    assert len(_charges(db)) == 4
    assert sum(fill_counter.counts.values()) == 2  # the create and the regenerate meet the quota


async def test_a_regenerate_never_overwrites_a_hand_edit(client, db):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme'], ['Acme'])
    assert _snippet(tc, lid, [ids['Acme']], 1).status_code == 201
    await _fill(db, lid, 'ai_opener', provider=Model())
    tc.patch(f'/v1/lists/{lid}/rows/{ids["Acme"]}', json={'cells': {'ai_opener': 'My own line'}})

    body = {'row_ids': [ids['Acme']], 'approved_credits': 1}
    assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/regenerate', json=body).status_code == 202
    model = Model()
    await _fill(db, lid, 'ai_opener', provider=model)

    assert (_cells(db, lid)['Acme'][:2], model.asked, _balance(db, uid)) == (('manual', 'My own line'), [], 49)


async def test_a_hand_edit_made_while_a_regenerate_runs_stays_and_costs_nothing(client, db):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme'], ['Acme'])
    assert _snippet(tc, lid, [ids['Acme']], 1).status_code == 201
    await _fill(db, lid, 'ai_opener', provider=Model())
    body = {'row_ids': [ids['Acme']], 'approved_credits': 1}
    assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/regenerate', json=body).status_code == 202
    ANSWERS['Acme'] = _line('Acme', 'Acme, 12 million is a fine start.')
    edit = {'cells': {'ai_opener': 'My own line'}}
    patched: list[int] = []
    model = Model(
        on_ask=lambda _: patched.append(tc.patch(f'/v1/lists/{lid}/rows/{ids["Acme"]}', json=edit).status_code)
    )

    await _fill(db, lid, 'ai_opener', provider=model)

    assert (patched, model.asked) == ([200], ['Acme'])
    assert (_cells(db, lid)['Acme'][:2], _balance(db, uid), len(_charges(db))) == (('manual', 'My own line'), 49, 1)


async def test_a_hand_edit_never_frees_approved_credits_for_a_retry(client, db):
    tc, uid = client
    names = ['Acme', 'Beta', 'Gamma', 'Delta', 'Echo']
    lid, ids = _researched_list(db, uid, names, ['Acme', 'Beta', 'Gamma'])
    assert _snippet(tc, lid, list(ids.values()), 3).status_code == 201
    await _fill(db, lid, 'ai_opener', provider=Model())
    assert _balance(db, uid) == 47

    _research(db, lid, ['Delta', 'Echo'])
    edit = {'cells': {'ai_opener': 'My own line'}}
    assert tc.patch(f'/v1/lists/{lid}/rows/{ids["Acme"]}', json=edit).status_code == 200
    assert tc.post(f'/v1/lists/{lid}/columns/ai_opener/fill').status_code == 202
    model = Model()
    await _fill(db, lid, 'ai_opener', provider=model)

    assert (model.asked, _balance(db, uid), len(_charges(db))) == ([], 47, 3)


async def test_a_deleted_input_column_feeds_no_snippet(client, db):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme', 'Beta'], ['Acme'])
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'ai_jobs', 'Open jobs', 'ai', 10, %s)",
        (lid, Jsonb({'prompt': 'Open jobs?', 'fill': {'status': 'done'}})),
    )
    _research(db, lid, ['Acme', 'Beta'], key='ai_jobs')
    ANSWERS['Acme'] = _line('Acme')
    assert _snippet(tc, lid, list(ids.values()), 2, inputs=('ai_news', 'ai_jobs')).status_code == 201
    assert tc.delete(f'/v1/lists/{lid}/columns/ai_jobs').status_code == 204
    model = Model()

    await _fill(db, lid, 'ai_opener', provider=model)

    assert (model.asked, model.facts, _balance(db, uid)) == (['Acme'], [['ai_news']], 49)


@pytest.mark.parametrize(
    ('switch', 'stopped'), [('vendor_disabled:ai_snippets', True), ('vendor_disabled:firecrawl', False)]
)
async def test_the_snippet_kill_switch_stops_the_fill_before_it_holds_credits(client, db, kill_switch, switch, stopped):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme'], ['Acme'])
    assert _snippet(tc, lid, [ids['Acme']], 1).status_code == 201
    kill_switch.add(switch)
    model = Model()

    await _fill(db, lid, 'ai_opener', provider=model)

    fill = _fill_state(db, lid)
    if stopped:
        assert (fill['status'], fill['error'], model.asked, _balance(db, uid)) == ('failed', 'vendor_disabled', [], 50)
        assert _ledger(db, uid) == []
    else:
        assert (fill['status'], model.asked, _balance(db, uid)) == ('done', ['Acme'], 49)


async def test_a_snippet_fill_whose_worker_dies_bills_what_it_wrote_and_the_reaper_refunds_the_rest(
    client, db, monkeypatch
):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme', 'Beta'], ['Acme', 'Beta'])
    assert _snippet(tc, lid, list(ids.values()), 2).status_code == 201
    real = ai_columns._write_cell
    lock, written = threading.Lock(), []
    stored, dead = threading.Event(), threading.Event()

    def write(run, row_id, cell):
        with lock:
            first = not written
            written.append(row_id)
        if not first:  # the second cell: this worker runs no further line
            dead.wait(30)
            raise RuntimeError('the worker died')
        ok = real(run, row_id, cell)
        stored.set()
        return ok

    monkeypatch.setattr(ai_columns, '_write_cell', write)
    monkeypatch.setattr(ai_columns, 'CONCURRENCY', 1)
    fill = asyncio.create_task(_fill(db, lid, 'ai_opener', provider=Model()))
    try:
        assert await asyncio.to_thread(stored.wait, 30), 'the fill never wrote a cell'
        _reap(db)

        assert _balance(db, uid) == 49
        assert _ledger(db, uid) == [
            ('deduction', 'committed', 2, 'ai_column'),
            ('refund', 'committed', 1, 'reservation.slack'),
        ]
        assert _charges(db) == [(['ai_opener'], 1, 'charged')]
    finally:
        dead.set()
        with contextlib.suppress(Exception):
            await fill


def test_a_snippet_reads_only_research_columns_of_its_own_list(client, db):
    tc, uid = client
    lid, ids = _researched_list(db, uid, ['Acme'], ['Acme'])
    other, _ = _researched_list(db, uid, ['Beta'], [])
    db.execute("UPDATE list_columns SET key = 'ai_elsewhere' WHERE list_id = %s AND key = 'ai_news'", (other,))
    assert _snippet(tc, lid, [ids['Acme']], 1).status_code == 201
    for inputs in (['name'], ['ai_opener'], ['ai_elsewhere'], ['ai_news', 'source']):
        refused = _snippet(tc, lid, [ids['Acme']], 1, inputs=inputs)
        estimate = tc.post(f'/v1/lists/{lid}/columns/estimate', json={'inputs': inputs, 'row_ids': [ids['Acme']]})
        for r in (refused, estimate):
            assert (r.status_code, r.json()['detail']['code']) == (422, 'LIST.SNIPPET_INPUTS_INVALID'), inputs
    research = {'row_ids': [ids['Acme']], 'approved_credits': 1}
    assert tc.post(f'/v1/lists/{lid}/columns/ai_news/regenerate', json=research).status_code == 404


def test_snippet_routes_are_owner_scoped(client, db, enqueued):
    tc, _ = client
    theirs, ids = _researched_list(db, _user(db), ['Acme'], ['Acme'])
    db.execute(
        'INSERT INTO list_columns (list_id, key, label, kind, position, config)'
        " VALUES (%s, 'ai_opener', 'Opener', 'ai', 10, %s)",
        (theirs, Jsonb({'mode': 'snippet', 'prompt': 'p', 'inputs': ['ai_news'], 'row_ids': list(ids.values())})),
    )
    rows = list(ids.values())
    assert _snippet(tc, theirs, rows, 1).status_code == 404
    assert (
        tc.post(f'/v1/lists/{theirs}/columns/estimate', json={'inputs': ['ai_news'], 'row_ids': rows}).status_code
        == 404
    )
    regenerate = {'row_ids': rows, 'approved_credits': 1}
    assert tc.post(f'/v1/lists/{theirs}/columns/ai_opener/regenerate', json=regenerate).status_code == 404
    assert enqueued == []
