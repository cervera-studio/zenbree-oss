import json

import httpx
import pytest

from app.agent import funnel
from app.agent.routes import ChatRequest
from app.llm.base import ToolCall
from tests.conftest import body, member, start_turn
from tests.test_agent import _balance, _job, _run, _user, needs_db
from tests.test_agent import db as db

APPLIED = {
    'locations': ['Piemonte, Italy'],
    'keywords': ['saas'],
    'employee_ranges': ['20,100'],
    'job_titles': ['Account Executive'],
    'total_funding_min': 10_000_000,
}
ORG = {'id': 'c1', 'name': 'Acme', 'primary_domain': 'acme.test'}


def funnel_groups(filters):
    return [g for g in funnel.GROUPS if funnel._only(filters, g)]


async def test_steps_are_the_applied_filters_in_order_and_stop_at_the_first_zero():
    seen: list[dict] = []

    async def count(filters):
        seen.append(filters)
        return 0 if 'employee_ranges' in filters else 64

    built = await funnel.build(count, APPLIED)
    assert built
    assert [(s['group'], s['count']) for s in built['funnel']['steps']] == [('where', 64), ('what', 64), ('size', 0)]
    assert built['funnel']['excluded_index'] == 2
    assert seen[:2] == [{'locations': APPLIED['locations']}, {'locations': APPLIED['locations'], 'keywords': ['saas']}]


async def test_the_last_step_is_the_empty_search_itself_and_costs_no_page():
    seen: list[dict] = []

    async def count(filters):
        seen.append(filters)
        return 10 if len(funnel_groups(filters)) < 5 else 0

    built = await funnel.build(count, APPLIED)
    assert built
    steps = built['funnel']['steps']
    assert [s['count'] for s in steps] == [10, 10, 10, 10, 0]
    assert APPLIED not in seen


async def test_alternatives_drop_one_group_each_count_it_keep_the_filters_and_lose_empty_ones():
    async def count(filters):
        return 0 if 'total_funding_min' in filters or 'locations' not in filters else 7

    built = await funnel.build(count, APPLIED)
    assert built
    assert [o['drop'] for o in built['options']] == ['money', 'signal'][: len(built['options'])]
    for o in built['options']:
        assert o['estimate'] == {'count': 7, 'noun': 'company'}
        assert 'total_funding_min' not in o['filters']
        assert funnel.GROUPS[o['drop']][0] not in o['filters']


async def test_pages_per_funnel_never_exceed_the_cap():
    calls: list[dict] = []

    async def count(filters):
        calls.append(filters)
        return 5

    await funnel.build(count, APPLIED)
    assert len(calls) <= funnel.MAX_PAGES


async def test_a_single_group_has_no_alternative_and_a_failed_count_is_no_funnel():
    async def count(filters):
        return 5

    built = await funnel.build(count, {'locations': ['Torino']})
    assert built and built['options'] == [] and built['funnel']['excluded_index'] == 0

    async def failing(filters):
        return None

    assert await funnel.build(failing, APPLIED) is None


def _apollo(http, *, rows_when=None):
    def respond(request):
        sent = body(request)
        if rows_when and rows_when(sent):
            return httpx.Response(200, json={'organizations': [ORG], 'pagination': {'total_entries': 12}})
        total = 0 if 'q_organization_job_titles' in sent else 11
        return httpx.Response(200, json={'organizations': [], 'pagination': {'total_entries': total}})

    http.handler = respond


SEARCH = {'locations': ['Piemonte, Italy'], 'job_titles': ['Account Executive']}


@needs_db
async def test_an_empty_company_search_pauses_on_its_own_funnel_and_costs_the_user_nothing(db, http, keys, kill_switch):
    kill_switch.add('vendor_disabled:serper')
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'SaaS in Piemonte che assumono AE')
    _apollo(http)
    events, provider = await _run(jid, [('', [ToolCall('s1', 'search_companies', SEARCH)]), ('unused', [])])

    saved = dict(events)['message_saved']
    assert len(provider.turns) == 1
    assert [s['group'] for s in saved['question']['funnel']['steps']] == ['where', 'signal']
    assert saved['question']['funnel']['excluded_index'] == 1
    assert [o['drop'] for o in saved['question']['options']] == [
        'signal'
    ]  # dropping the location leaves job titles alone: 0
    job = db.execute('SELECT status, credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert (job['status'], job['credits_spent']) == ('needs_input', 0)
    assert _balance(db, uid) == 50
    assert db.execute(
        "SELECT credits_used FROM chat_messages WHERE session_id = %s AND role = 'assistant'", (sid,)
    ).fetchone() == {'credits_used': 0}


@needs_db
async def test_a_zero_row_search_the_local_fallback_covers_builds_no_funnel_and_the_model_runs_it(db, http, keys):
    uid = _user(db)
    _, jid = _job(db, uid, 'Idraulici a Torino')
    _apollo(http)
    events, provider = await _run(jid, [('', [ToolCall('s1', 'search_companies', SEARCH)]), ('Ecco.', [])])

    assert 'question' not in dict(events)['message_saved']
    assert len(provider.histories) == 2
    assert 'local_fallback' in json.dumps(provider.histories[1])
    assert len(http.calls) == 1


@needs_db
async def test_a_search_with_rows_never_produces_a_funnel(db, http, keys):
    uid = _user(db)
    _, jid = _job(db, uid, 'SaaS in Piemonte')
    _apollo(http, rows_when=lambda sent: True)
    events, _ = await _run(jid, [('', [ToolCall('s1', 'search_companies', SEARCH)]), ('Found.', [])])
    assert 'question' not in dict(events)['message_saved']
    assert len(http.calls) == 1


@needs_db
async def test_zero_row_searches_in_one_turn_build_one_funnel(db, http, keys, kill_switch):
    kill_switch.add('vendor_disabled:serper')
    uid = _user(db)
    _, jid = _job(db, uid, 'SaaS in Piemonte')
    _apollo(http)
    other = {**SEARCH, 'locations': ['Lombardia, Italy']}
    calls = [ToolCall('s1', 'search_companies', SEARCH), ToolCall('s2', 'search_companies', other)]
    events, provider = await _run(jid, [('', calls), ('unused', [])])
    assert len(provider.turns) == 1 and 'funnel' in dict(events)['message_saved']['question']
    assert len(http.calls) <= 2 + funnel.MAX_PAGES


@needs_db
async def test_a_funnel_whose_count_fails_is_not_rebuilt_by_later_steps(db, http, keys, kill_switch):
    """#259: a failed build leaves the turn going, but its next empty search must not spend funnel pages again."""
    kill_switch.add('vendor_disabled:serper')
    uid = _user(db)
    _, jid = _job(db, uid, 'SaaS in Piemonte')

    def respond(request):
        if 'q_organization_job_titles' not in body(request):  # the funnel's first count: location alone
            return httpx.Response(500)
        return httpx.Response(200, json={'organizations': [], 'pagination': {'total_entries': 0}})

    http.handler = respond
    other = {**SEARCH, 'locations': ['Lombardia, Italy']}
    events, provider = await _run(
        jid,
        [
            ('', [ToolCall('s1', 'search_companies', SEARCH)]),
            ('', [ToolCall('s2', 'search_companies', other)]),
            ('Nothing found.', []),
        ],
    )
    assert not provider.turns and 'question' not in dict(events)['message_saved']
    assert sum('q_organization_job_titles' not in body(r) for r in http.calls) == 1


@needs_db
async def test_picking_an_option_runs_exactly_its_stored_filters(db, http, keys, kill_switch):
    kill_switch.add('vendor_disabled:serper')
    uid = _user(db)
    sid, jid = _job(db, uid, 'SaaS in Piemonte che assumono AE')
    _apollo(http)
    await _run(jid, [('', [ToolCall('s1', 'search_companies', SEARCH)])])

    _, jid2 = start_turn(member(db, uid), ChatRequest(message='Senza segnale', session_id=sid, pick=0))
    stored = db.execute('SELECT pick FROM jobs WHERE id = %s', (jid2,)).fetchone()['pick']
    assert stored == {'tool': 'search_companies', 'args': {'locations': ['Piemonte, Italy']}}
    http.calls.clear()
    _apollo(http, rows_when=lambda sent: True)
    events, provider = await _run(jid2, [('Trovate.', [])])

    assert [body(c).get('q_organization_job_titles') for c in http.calls] == [None]
    assert [d['tool_input'] for e, d in events if e == 'tool_start'] == [{'locations': ['Piemonte, Italy']}]
    history = provider.histories[0]
    assert history[-2]['content'][0]['name'] == 'search_companies'
    assert history[-1]['content'][0]['type'] == 'tool_result'


@needs_db
@pytest.mark.parametrize('pick', [0, 5])
async def test_a_pick_with_nothing_to_pick_is_refused(db, pick):
    from fastapi import HTTPException

    uid = _user(db)
    sid, _ = _job(db, uid, 'hello')
    with pytest.raises(HTTPException) as err:
        start_turn(member(db, uid), ChatRequest(message='x', session_id=sid, pick=pick))
    assert err.value.status_code == 422


@needs_db
async def test_a_maps_fallback_that_finds_nothing_too_pauses_on_the_b2b_funnel(db, http, keys):
    uid = _user(db, credits=50)
    _, jid = _job(db, uid, 'Idraulici in Piemonte che assumono')

    def respond(request):
        if 'serper' in request.url.host:
            return httpx.Response(200, json={'places': []})
        return _apollo_empty(request)

    _apollo(http)
    _apollo_empty = http.handler
    http.handler = respond
    local = {'local_query': 'idraulici', 'local_location': 'Piemonte, Italy'}
    events, provider = await _run(
        jid,
        [
            ('', [ToolCall('s1', 'search_companies', SEARCH)]),
            ('', [ToolCall('s2', 'search_companies', local)]),
            ('unused', []),
        ],
    )

    question = dict(events)['message_saved']['question']
    assert len(provider.turns) == 1
    assert [s['group'] for s in question['funnel']['steps']] == ['where', 'signal']
    assert question['funnel']['entity'] == 'company'


PEOPLE = {'titles': ['Head of Sales'], 'locations': ['Torino, Italy'], 'employee_ranges': ['20,100']}


def _apollo_people(http, *, rows_when=None):
    def respond(request):
        sent = body(request)
        if rows_when and rows_when(sent):
            person = {'id': 'p1', 'first_name': 'Ada', 'title': 'Head of Sales'}
            return httpx.Response(200, json={'people': [person], 'total_entries': 4})
        return httpx.Response(200, json={'people': [], 'total_entries': 0 if 'person_titles' in sent else 9})

    http.handler = respond


@needs_db
async def test_an_empty_people_search_pauses_on_its_funnel_and_a_pick_reruns_the_people_search(db, http, keys):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'Head of sales a Torino')
    _apollo_people(http)
    events, provider = await _run(jid, [('', [ToolCall('s1', 'search_people', PEOPLE)]), ('unused', [])])

    question = dict(events)['message_saved']['question']
    assert len(provider.turns) == 1
    assert question['funnel']['entity'] == 'person'
    assert [s['group'] for s in question['funnel']['steps']] == ['where', 'size', 'who']
    assert [(o['drop'], o['tool'], o['estimate']['noun']) for o in question['options']] == [
        ('who', 'search_people', 'person')
    ]

    _, jid2 = start_turn(member(db, uid), ChatRequest(message='Senza ruolo', session_id=sid, pick=0))
    http.calls.clear()
    _apollo_people(http, rows_when=lambda sent: True)
    events, _ = await _run(jid2, [('Trovate.', [])])
    assert [(d['tool_name'], d['tool_input']) for e, d in events if e == 'tool_start'] == [
        ('search_people', {'locations': ['Torino, Italy'], 'employee_ranges': ['20,100']})
    ]


@needs_db
async def test_a_people_search_with_filters_the_funnel_cannot_carry_gets_no_funnel(db, http, keys):
    uid = _user(db)
    _, jid = _job(db, uid, 'Head of sales di acme.test')
    _apollo_people(http)
    events, provider = await _run(
        jid, [('', [ToolCall('s1', 'search_people', {**PEOPLE, 'company_domains': ['acme.test']})]), ('Nessuno.', [])]
    )
    assert 'question' not in dict(events)['message_saved']
    assert len(provider.histories) == 2
