"""Row ratings fed back to the agent (#46) and more rows from the same search (#48)."""

import uuid

import pytest

from app.agent.tools import registry
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult
from app.billing.price import Price
from app.llm.base import ToolCall
from tests.conftest import act_in_own_org, project
from tests.test_agent import _balance, _chat, _job, _org, _run, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db

PAGES = {1: ['a', 'b'], 2: ['b', 'c', 'd']}  # page 2 repeats b (already in the list); d is excluded
fetched: list[tuple[str, int]] = []


def _fake(entity):
    async def search(args, ctx):
        fetched.append((entity, args['page']))
        page = PAGES.get(args['page'], [])
        rows = (
            [{'full_name': f'P {n}', 'email': f'{n}@x.test'} for n in page]
            if entity == 'person'
            else [{'name': n, 'domain': f'{n}.test'} for n in page]
        )
        return ToolResult(ok=True, data={'count': len(rows)}, rows=rows, entity=entity)

    return search


@pytest.fixture
def fake_search(monkeypatch):
    for tool, entity in (('search_companies', 'company'), ('search_people', 'person')):
        monkeypatch.setitem(
            registry._REGISTRY, tool, ToolDef(tool, 't', {}, _fake(entity), price=Price(1, (), lambda cells: False))
        )
    fetched.clear()


@needs_db
async def test_more_rows_extends_the_named_list_once_per_page(client, db, fake_search):
    tc, uid = client
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'd.test')",
        (project(db, uid), uid),
    )
    sid, jid = _job(db, uid, 'CEOs and agencies in Milan')
    first = [
        ToolCall('p', 'search_people', {'titles': ['CEO'], 'page': 1}),
        ToolCall('c', 'search_companies', {'locations': ['Milan'], 'page': 1}),
    ]
    await _run(jid, [('', first), ('ok', [])])
    assert _balance(db, uid) == 50
    lists = {r['entity']: str(r['id']) for r in db.execute('SELECT id, entity FROM lists')}

    async def more(args):
        _, jid = _job(db, uid, 'more rows', session_id=sid)
        return await _run(jid, [('', [ToolCall('m', 'more_rows', args)]), ('ok', [])])

    # The chat's current list is the person one: without an id nothing is fetched.
    events, _ = await more({})
    assert fetched == [('person', 1), ('company', 1)] and _balance(db, uid) == 50
    assert next(d for e, d in events if e == 'tool_result')['status'] == 'failed'

    events, _ = await more({'list_id': lists['company']})
    rows = db.execute('SELECT cells FROM list_rows WHERE list_id = %s ORDER BY seq', (lists['company'],))
    assert [r['cells']['name'] for r in rows] == ['a', 'b', 'c']  # b already there, d excluded
    assert fetched[-1] == ('company', 2) and _balance(db, uid) == 50  # more_rows is free (rows are)
    assert next(d for e, d in events if e == 'list_updated')['rows_added'] == 1
    assert tc.get(f'/v1/lists/{lists["company"]}').json()['can_expand'] is True

    await more({'list_id': lists['company']})
    assert fetched[-1] == ('company', 3) and _balance(db, uid) == 50  # empty page: free
    last = db.execute('SELECT last_search FROM lists WHERE id = %s', (lists['company'],)).fetchone()['last_search']
    assert last == {
        'tool': 'search_companies',
        'args': {'locations': ['Milan'], 'page': 2},
        'fetched': 5,
        'exhausted': True,
    }
    assert tc.get(f'/v1/lists/{lists["company"]}').json()['can_expand'] is False
    assert tc.get(f'/v1/lists/{lists["person"]}').json()['can_expand'] is True

    await more({'list_id': lists['company']})  # exhausted: refused before the vendor
    assert len(fetched) == 4


@needs_db
def test_remember_search_never_moves_back_and_skips_unpaged(db):
    from app.lists.last_search import remember_search
    from app.lists.rows import create_list

    lid = create_list(db, user_id=(uid := _user(db)), session_id=_chat(db, uid), name='L', entity='company')
    act_in_own_org(db, uid)  # the search tools of uid's turn

    def last():
        return db.execute('SELECT last_search FROM lists WHERE id = %s', (lid,)).fetchone()['last_search']

    remember_search(lid, 'search_companies', {'locations': ['Milan'], 'page': 3})
    remember_search(lid, 'search_companies', {'locations': ['Milan'], 'page': 2})
    assert last()['args']['page'] == 3
    remember_search(lid, 'search_companies', {'local_query': 'bar'})
    remember_search(lid, 'save_to_list', {'items': []})
    assert last()['args'] == {'locations': ['Milan'], 'page': 3}
    db.execute('UPDATE lists SET last_search = NULL WHERE id = %s', (lid,))
    remember_search(lid, 'search_companies', {})  # no filters at all still counts
    assert last() == {'tool': 'search_companies', 'args': {'page': 1}, 'fetched': 0}
    remember_search(lid, 'search_people', {'titles': ['CEO']})
    assert last() == {'tool': 'search_people', 'args': {'titles': ['CEO'], 'page': 1}, 'fetched': 0}


@needs_db
async def test_more_rows_refuses_lists_outside_the_chat(db, fake_search):
    from app.agent.tools.handlers.more_rows import more_rows
    from app.lists.rows import create_list

    uid = _user(db)
    act_in_own_org(db, uid)  # the tool runs in uid's turn
    sid = str(
        db.execute(
            'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s) RETURNING id',
            (
                uid,
                project(db, uid),
                uid,
            ),
        ).fetchone()['id']
    )
    other_sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s) RETURNING id',
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    other = create_list(db, user_id=uid, session_id=str(other_sid), name='L', entity='company')
    db.execute('UPDATE lists SET last_search = %s WHERE id = %s', ('{"tool": "search_companies", "args": {}}', other))
    for list_id in (other, 'not-a-uuid', str(uuid.uuid4())):
        res = await more_rows({'list_id': list_id}, ToolContext(user_id=uid, session_id=sid))
        assert not res.ok
    assert not (await more_rows({}, ToolContext(user_id=uid, session_id=sid, list_id=other))).ok
    assert fetched == []


@needs_db
async def test_rated_rows_reach_the_agent(client, db):
    from app.agent.loop import Turn
    from app.agent.tools.handlers.list_row_feedback import list_row_feedback
    from app.lists.rows import add_rows, create_list

    tc, uid = client
    sid = str(
        db.execute(
            'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s) RETURNING id',
            (
                uid,
                project(db, uid),
                uid,
            ),
        ).fetchone()['id']
    )
    lid = create_list(db, user_id=uid, session_id=sid, name='L', entity='company')
    good, bad, _ = add_rows(db, lid, 'company', [{'name': n, 'domain': f'{n}.it'} for n in ('Acme', 'Beta', 'Gamma')])

    url = f'/v1/lists/{lid}/rows'
    assert tc.put(f'{url}/{good}/rating', json={'rating': 'good', 'note': 'ignored'}).json()['rating'] == 'good'
    rated = tc.put(f'{url}/{bad}/rating', json={'rating': 'bad', 'note': ' too big '}).json()
    assert (rated['rating'], rated['rating_note']) == ('bad', 'too big')
    assert tc.put(f'{url}/{bad}/rating', json={'rating': 'meh'}).status_code == 422
    assert tc.put(f'{url}/{uuid.uuid4()}/rating', json={'rating': 'bad'}).status_code == 404
    other = create_list(db, user_id=(someone := _user(db)), session_id=_chat(db, someone), name='X', entity='company')
    assert tc.put(f'/v1/lists/{other}/rows/{good}/rating', json={'rating': 'bad'}).status_code == 404

    act_in_own_org(db, uid)  # the tool, then the turn's state, run in uid's turn
    res = await list_row_feedback({}, ToolContext(user_id=uid, session_id=sid))
    assert res.ok
    assert res.data['bad'] == [{'row_id': bad, 'name': 'Beta', 'domain': 'Beta.it', 'note': 'too big'}]
    assert [r['name'] for r in res.data['good']] == ['Acme'] and 'note' in res.data['how_to_read']
    job = {'id': uuid.uuid4(), 'organization_id': _org(db, uid), 'user_id': uid, 'chat_session_id': sid}
    turn = Turn(job, None, lambda e, d: None)  # type: ignore[arg-type]  # provider unused
    turn._load()
    state = turn._state()
    assert '3 rows (1 rated good, 1 bad: see list_row_feedback)' in state and 'too big' not in state

    cleared = tc.put(f'{url}/{bad}/rating', json={'rating': None, 'note': 'x'}).json()
    assert cleared['rating'] is None and cleared['rating_note'] is None


@needs_db
async def test_the_agent_reads_a_lists_emails_from_the_chat_state(client, db):
    """Demo QA chat-12: asked how many emails are verified, the agent bought a lookup it did not need."""
    from app.agent.loop import Turn
    from app.lists.rows import add_rows, create_list

    _, uid = client
    sid = _chat(db, uid)
    lid = create_list(db, user_id=uid, session_id=sid, name='L', entity='person')
    found = [('verified', 'a'), ('verified', 'b'), ('verified', 'c'), ('catch_all', 'd'), ('mx_ok', 'e')]
    add_rows(
        db,
        lid,
        'person',
        [{'full_name': n, 'email': f'{n}@x.test', 'email_status': s} for s, n in found]
        + [
            {'full_name': 'f', 'email_status': 'not_found'},
            {'full_name': 'g', 'email_status': 'failed'},
            {'full_name': 'h'},
        ],
    )
    act_in_own_org(db, uid)
    job = {'id': uuid.uuid4(), 'organization_id': _org(db, uid), 'user_id': uid, 'chat_session_id': sid}
    turn = Turn(job, None, lambda e, d: None)  # type: ignore[arg-type]  # provider unused
    turn._load()

    assert (
        f'person list {lid}: 8 rows; emails: 3 verified, 1 on a domain that accepts any address (cannot be '
        'verified), 1 found but not verified, 1 not found, 1 lookup failed (can be retried), 1 not searched yet'
    ) in turn._state()


@pytest.fixture
def free_search(monkeypatch):
    monkeypatch.setitem(registry._REGISTRY, 'search_companies', ToolDef('search_companies', 't', {}, _fake('company')))
    fetched.clear()


def _tool_error(events):
    return next(d for e, d in events if e == 'tool_result')


@needs_db
async def test_a_free_search_needs_a_positive_balance(db, free_search):
    uid = _user(db, credits=0)
    _, jid = _job(db, uid, 'agencies in Milan')
    events, _ = await _run(jid, [('', [ToolCall('c', 'search_companies', {'page': 1})]), ('ok', [])])
    assert fetched == [] and _tool_error(events)['status'] == 'failed'


@needs_db
async def test_free_rows_count_per_organization_and_stop_at_the_daily_cap(db, free_search, monkeypatch):
    from app.agent import free_rows

    uid = _user(db)
    _, jid = _job(db, uid, 'agencies in Milan')
    await _run(jid, [('', [ToolCall('c', 'search_companies', {'page': 1})]), ('ok', [])])
    org = db.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    assert fetched == [('company', 1)] and free_rows.today(str(org)) == 2 and _balance(db, uid) == 50

    monkeypatch.setattr(free_rows, 'PER_DAY', 2)
    _, jid = _job(db, uid, 'more agencies')
    events, _ = await _run(jid, [('', [ToolCall('c2', 'search_companies', {'page': 2})]), ('ok', [])])
    assert fetched == [('company', 1)] and _tool_error(events)['status'] == 'failed'


def test_parallel_free_searches_cannot_both_pass_the_last_page(monkeypatch):
    from app.agent import free_rows

    monkeypatch.setattr(free_rows, 'PER_DAY', free_rows.PAGE)
    key = free_rows._key('org-race')
    assert free_rows._hold(key) is True  # both checks run before either search settles
    assert free_rows._hold(key) is False
    free_rows.settle(key, 3)
    assert free_rows.today('org-race') == 3


@needs_db
async def test_web_search_needs_a_positive_balance_and_stops_at_the_daily_cap(db, monkeypatch):
    """#964: web_search has no price but calls a paid vendor, so it gets the free searches' balance rule and a cap."""
    from app.agent import free_rows
    from app.agent.tools.handlers import web

    searched: list[str] = []

    def fake_search(query, num=10):
        searched.append(query)
        return {'success': True, 'query': query, 'provider': 'exa', 'results': []}

    monkeypatch.setattr(web, 'web_search', fake_search)
    monkeypatch.setattr(web, 'record_vendor_call', lambda *a, **kw: None)
    monkeypatch.setattr(free_rows, 'WEB_SEARCHES_PER_DAY', 2)
    calls = [ToolCall(f'w{i}', 'web_search', {'query': f'q{i}'}) for i in range(3)]

    broke = _user(db, credits=0)
    _, jid = _job(db, broke, 'look things up')
    events, _ = await _run(jid, [('', calls[:1]), ('ok', [])])
    assert searched == [] and _tool_error(events)['status'] == 'failed'

    uid = _user(db)
    _, jid = _job(db, uid, 'look things up')
    events, _ = await _run(jid, [('', calls), ('ok', [])])
    results = {d['tool_id']: d for e, d in events if e == 'tool_result'}
    assert searched == ['q0', 'q1'] and results['w2']['status'] == 'failed'
    assert 'web searches for today' in results['w2']['result_preview']
