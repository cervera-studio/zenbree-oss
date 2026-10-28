"""Plans (#42 #43 #45 #47 #49): pricing, the approval pause, the spend gate,
required columns and the per-row refund. SQL tests reuse test_agent's fixtures."""

import uuid

import pytest
from fastapi import HTTPException

from app.agent import plan as plans
from app.agent.tools import registry
from app.agent.tools.registry import ToolDef, ToolResult
from app.billing.price import Price
from app.core.scope import acting_as
from app.lists.lookup_outcome import searched
from app.llm.base import ToolCall
from tests.conftest import act_as, act_in_own_org, member, project, start_turn
from tests.test_agent import _balance, _cap, _job, _run, _user, needs_db
from tests.test_agent import db as db


def _as_lists_member(db, list_id: str, user_id: str):
    org = db.execute('SELECT organization_id::text FROM lists WHERE id = %s', (list_id,)).fetchone()['organization_id']
    return acting_as(org, user_id)


async def _fake_people(args, ctx):
    rows = [
        {'full_name': 'Ada Rossi', 'title': 'CTO', 'linkedin_url': 'https://linkedin.com/in/ada'},
        {'full_name': 'Bo Bianchi', 'linkedin_url': 'https://linkedin.com/in/bo'},  # no title
    ]
    return ToolResult(ok=True, rows=rows, entity='person')


def _list_row_ids(list_id):
    from app.core.db import get_conn

    with get_conn() as conn:
        return [
            r['id'] for r in conn.execute('SELECT id::text FROM list_rows WHERE list_id = %s ORDER BY seq', (list_id,))
        ]


async def _more_people(args, ctx):
    rows = [
        {'full_name': 'Cy Neri', 'title': 'CFO', 'linkedin_url': 'https://linkedin.com/in/cy'},
        {'full_name': 'Di Verdi', 'title': 'COO', 'linkedin_url': 'https://linkedin.com/in/di'},
    ]
    return ToolResult(ok=True, rows=rows, entity='person')


async def _fake_emails(args, ctx):
    if args.get('fail'):
        return ToolResult(ok=False, error='down', error_code='provider')
    # like enrich_email: without row_ids it picks the batch itself and reports how many it searched
    ids = args.get('row_ids') or _list_row_ids(ctx.list_id)
    updates = [
        {'row_id': r, 'cells': {'email': f'{i}@acme.test', 'email_status': 'verified'}} for i, r in enumerate(ids)
    ]
    return ToolResult(
        ok=True,
        row_updates=updates,
        entity='person',
        outcome=searched('email', updates),
        data={'searched': len(ids), 'found': len(ids)},
    )


_ROW_IDS = {'type': 'object', 'properties': {'row_ids': {'type': 'array'}}}  # what the preflight counts (#418)
EMAILS = Price(credits=2, keys=('email', 'email_status'), found=lambda cells: bool(cells.get('email')), batch=2)
for _name, _handler, _price, _noun in (
    ('plan_people', _fake_people, Price(1, (), lambda cells: False), None),
    ('plan_more_people', _more_people, None, None),
    ('plan_emails', _fake_emails, EMAILS, 'email'),
):
    if not registry.get(_name):
        registry.register(
            ToolDef(
                name=_name,
                description='test',
                input_schema=_ROW_IDS,
                handler=_handler,
                found_noun=_noun,
                price=_price,
            )
        )


def _plan_args(rows=100, email_units=100):
    return {
        'mode': 'build',
        'entity': 'person',
        'target_rows': rows,
        'list_name': 'CTOs',
        'columns': [
            {'key': 'full_name', 'required': True},
            {'key': 'Title', 'label': 'Job title', 'required': True},
            {'key': 'email', 'required': True},
            {'key': 'person_id', 'required': True},  # internal: dropped
        ],
        'stages': [
            {'tool': 'plan_people', 'label': 'Find', 'units': 3},
            {'tool': 'plan_emails', 'label': 'Emails', 'units': email_units, 'per_row': True},
        ],
    }


# ── pure ───────────────────────────────────────────────────────────────────


def test_build_prices_plan_from_tool_costs():
    plan = plans.build(_plan_args())
    assert isinstance(plan, dict)
    assert [c['key'] for c in plan['columns']] == ['full_name', 'title', 'email']
    assert (plan['credits_fixed'], plan['credits_per_row'], plan['estimated_credits']) == (3, 2.0, 203)
    # the user picks another row count: per-row stages scale, fixed ones do not
    assert plans.credits_for(plan, 40) == 83
    assert [(s['units'], s['done']) for s in plans.stages_for(plan, 40)] == [(3, 0), (40, 0)]


def _units(call: ToolCall, session_id: str = 's') -> int:
    from app.agent.units import PricedCall

    tool = registry.get(call.name)
    assert tool is not None
    return PricedCall.of(call, tool, session_id).quote.rows


def test_ai_ark_company_search_is_priced_and_gated_per_row():
    assert _units(ToolCall('a', 'search_companies', {'source': 'aiark'})) == plans.AIARK_COMPANY_PAGE
    assert _units(ToolCall('b', 'search_companies', {'source': 'apollo'})) == 1
    assert _units(ToolCall('c', 'search_companies', {'source': 'aiark', 'local_query': 'bar'})) == 1
    assert _units(ToolCall('d', 'more_rows', {'list_id': 'not-a-uuid'})) == 1
    args = {**_plan_args(), 'stages': [{'tool': 'search_companies', 'units': 2, 'source': 'aiark'}]}
    plan = plans.build(args)
    assert isinstance(plan, dict) and plan['stages'][0]['units'] == 2 * plans.AIARK_COMPANY_PAGE
    assert plan['estimated_credits'] == 0  # rows are free since 2026-10-07


def test_an_argument_the_tool_does_not_take_never_prices_it():
    """#418: enrich_email ignores `items`, so one item must not price its next batch of 50 at 1 unit."""
    assert _units(ToolCall('e', 'enrich_email', {'items': [1]})) == 50


@needs_db
@pytest.mark.parametrize('tool', ['search_companies', 'more_rows'])
async def test_a_tool_input_that_is_not_an_object_fails_only_that_call(db, tool):
    """The model can send arguments that parse to a string (openai_compat): pricing the call must not raise, so
    the handler's own crash ends only that call, not the turn."""
    from app.agent.loop import Turn
    from tests.test_agent import FakeProvider

    uid = _user(db)
    act_in_own_org(db, uid)  # a tool and its pricing run in the turn's scope
    _, jid = _job(db, uid, 'x')
    events: list[tuple[str, dict]] = []
    turn = Turn(
        db.execute('SELECT id, organization_id, user_id, chat_session_id FROM jobs WHERE id = %s', (jid,)).fetchone(),
        FakeProvider([]),
        None,
    )
    turn.emit = lambda e, d: events.append((e, d))  # the frame check refuses a str tool_input on its own
    await turn._run_tool(ToolCall('t1', tool, 'acme'))  # type: ignore[arg-type]  # the str a model can send
    assert [d['status'] for e, d in events if e == 'tool_result'] == ['failed']


@needs_db
def test_more_rows_continuing_ai_ark_is_gated_per_row(db):
    """more_rows counts the rows of the AI Ark page it re-runs, read in its own chat only (#259)."""
    from psycopg.types.json import Jsonb

    uid = _user(db)
    act_in_own_org(db, uid)  # a tool and its pricing run in the turn's scope
    sid, _ = _job(db, uid, 'x')
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity, last_search)'
        " VALUES (default_organization(%s), %s, %s, %s, 'L', 'company', %s) RETURNING id::text",
        (uid, project(db, uid), uid, sid, Jsonb({'tool': 'search_companies', 'args': {'source': 'aiark', 'page': 1}})),
    ).fetchone()['id']
    more = ToolCall('m', 'more_rows', {'list_id': lid})
    assert _units(more, sid) == plans.AIARK_COMPANY_PAGE
    assert _units(more, str(uuid.uuid4())) == 1


@needs_db
def test_more_rows_cannot_rerun_a_search_the_user_dropped(db):
    """#259: more_rows counts as the search behind its list, so a dropped search stays dropped through it."""
    from psycopg.types.json import Jsonb

    from app.agent.loop import Turn
    from app.agent.units import PricedCall
    from tests.test_agent import FakeProvider

    uid = _user(db)
    act_in_own_org(db, uid)  # a tool and its pricing run in the turn's scope
    sid, jid = _job(db, uid, 'x')
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, chat_session_id, name, entity, last_search)'
        " VALUES (default_organization(%s), %s, %s, %s, 'L', 'person', %s) RETURNING id::text",
        (uid, project(db, uid), uid, sid, Jsonb({'tool': 'search_people', 'args': {'page': 1}})),
    ).fetchone()['id']
    turn = Turn(
        db.execute('SELECT id, organization_id, user_id, chat_session_id FROM jobs WHERE id = %s', (jid,)).fetchone(),
        FakeProvider([]),
        lambda e, d: None,
    )
    turn.dropped_tools = frozenset({'search_people'})
    more = registry.get('more_rows')
    assert more is not None
    refusal = turn._preflight(PricedCall.of(ToolCall('m', 'more_rows', {'list_id': lid}), more, sid))
    assert refusal is not None and 'dropped' in (refusal.error or '')


def _optional_emails_plan():
    args = _plan_args()
    args['columns'][0]['description'] = 'Full name'
    args['stages'][1]['optional'] = True
    plan = plans.build(args)
    assert isinstance(plan, dict)
    return plan


def test_build_keeps_the_optional_flag_unit_cost_and_column_description():
    plan = _optional_emails_plan()
    assert [(s['optional'], s['unit_cost']) for s in plan['stages']] == [(False, 1), (True, 2)]
    assert plan['columns'][0]['description'] == 'Full name'
    assert 'description' not in plan['columns'][1]


def test_dropping_a_stage_reprices_the_plan_on_the_server():
    plan = _optional_emails_plan()
    dropped = plans.drop_stages(plan, [1])
    assert (dropped['credits_fixed'], dropped['credits_per_row'], dropped['estimated_credits']) == (3, 0.0, 3)
    assert plans.credits_for(dropped, 40) == 3
    assert [s['tool'] for s in plans.stages_for(dropped, 40)] == ['plan_people']
    assert plans.dropped_tools(dropped) == {'plan_emails'}
    assert plans.drop_stages(dropped, [])['estimated_credits'] == 203


def _old_shape_plan():
    """A plan proposed before #212: no unit_cost, optional or dropped on its stages."""
    return {
        'mode': 'build',
        'entity': 'person',
        'target_rows': 50,
        'columns': [{'key': 'full_name', 'label': '', 'required': True}],
        'stages': [
            {'tool': 'search_people', 'label': 'Find', 'units': 50, 'per_row': True},
            {'tool': 'enrich_email', 'label': 'Emails', 'units': 1, 'per_row': False},
        ],
        'credits_fixed': 50,
        'credits_per_row': 5.0,
        'credits_research_per_row': 0,
    }


def test_approving_an_old_plan_keeps_its_stored_ceiling_without_the_tool_registry(monkeypatch):
    monkeypatch.setattr(plans.registry, 'get', lambda name: None)
    plan = plans.drop_stages(_old_shape_plan(), [])
    assert (plan['credits_fixed'], plan['credits_per_row']) == (50, 5.0)
    assert plans.credits_for(plan, 50) == 300
    with pytest.raises(plans.StageRequired):
        plans.drop_stages(_old_shape_plan(), [1])


def test_per_row_is_not_rounded_so_the_web_prices_the_same_credits():
    plan = {
        'mode': 'build',
        'entity': 'person',
        'target_rows': 64,
        'columns': [],
        'stages': [
            {'tool': 'a', 'label': 'a', 'units': 66, 'per_row': True, 'unit_cost': 1, 'optional': False},
            {'tool': 'b', 'label': 'b', 'units': 64, 'per_row': True, 'unit_cost': 2, 'optional': False},
            {'tool': 'c', 'label': 'c', 'units': 64, 'per_row': True, 'unit_cost': 5, 'optional': True},
        ],
        'credits_fixed': 0,
        'credits_per_row': 8.03125,
    }
    assert plans.credits_for(plans.drop_stages(plan, [2]), 64) == 194


def test_an_unknown_stage_cannot_be_dropped():
    with pytest.raises(plans.StageUnknown):
        plans.drop_stages(_optional_emails_plan(), [2])


@pytest.mark.parametrize(
    'patch',
    [
        {'mode': 'other'},
        {'entity': 'deal'},
        {'target_rows': 0},
        {'target_rows': plans.MAX_PLAN_ROWS + 1},
        {'columns': []},
        {'stages': [{'tool': 'no_such_tool', 'units': 1}]},
        {'stages': [{'tool': 'plan_people', 'units': -1}]},
    ],
)
def test_build_rejects_bad_plans(patch):
    assert isinstance(plans.build({**_plan_args(), **patch}), str)


# ── loop ───────────────────────────────────────────────────────────────────


@needs_db
async def test_large_plan_pauses_then_approved_turn_runs_it(db):
    from app.agent.routes import ChatRequest

    uid = _user(db, credits=500)
    sid, jid = _job(db, uid, '100 CTOs with emails')
    step = [ToolCall('p1', 'propose_plan', _plan_args()), ToolCall('s1', 'plan_people', {})]
    events, provider = await _run(jid, [('', step), ('unused', [])])

    assert len(provider.turns) == 1  # paused on the plan
    question = dict(events)['message_saved']['question']
    plan = question['plan']
    assert (plan['estimated_credits'], plan['version'], plan['needs_approval']) == (203, 1, True)
    assert [d['tool_name'] for e, d in events if e == 'tool_start'] == ['propose_plan']  # search skipped
    job = db.execute('SELECT status FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert job['status'] == 'needs_input'
    lst = db.execute('SELECT id::text, mode FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()
    assert lst == {'id': plan['list_id'], 'mode': 'build'}
    assert _balance(db, uid) == 500

    # a stale or foreign approval is refused, and nothing is left behind
    with pytest.raises(HTTPException) as e:
        start_turn(
            member(db, uid),
            ChatRequest(
                message='ok',
                session_id=uuid.UUID(sid),
                plan_approval={'list_id': plan['list_id'], 'version': 2, 'rows': 40},
            ),
        )
    assert e.value.status_code == 404
    other = _user(db)
    with pytest.raises(HTTPException) as e:
        start_turn(
            member(db, other),
            ChatRequest(message='ok', plan_approval={'list_id': plan['list_id'], 'version': 1, 'rows': 1}),
        )
    assert e.value.status_code == 404
    # the same user's other chat cannot run this list's plan
    with pytest.raises(HTTPException) as e:
        start_turn(
            member(db, uid),
            ChatRequest(message='ok', plan_approval={'list_id': plan['list_id'], 'version': 1, 'rows': 1}),
        )
    assert e.value.status_code == 404
    assert db.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 1

    # the user approves 40 rows: the next turn may spend the re-priced estimate
    _, jid2 = start_turn(
        member(db, uid),
        ChatRequest(
            message='Approve plan v1 for 40 rows',
            session_id=uuid.UUID(sid),
            plan_approval={'list_id': plan['list_id'], 'version': 1, 'rows': 40},
        ),
    )
    stored = db.execute('SELECT approved_rows, approved_credits, approved_job_id::text FROM list_plans').fetchone()
    assert stored == {'approved_rows': 40, 'approved_credits': 83, 'approved_job_id': jid2}
    required = db.execute(
        "SELECT key, label FROM list_columns WHERE list_id = %s AND (config->>'required')::boolean ORDER BY key",
        (plan['list_id'],),
    ).fetchall()
    assert required == [
        {'key': 'email', 'label': 'Email'},
        {'key': 'full_name', 'label': 'Full name'},
        {'key': 'title', 'label': 'Job title'},
    ]

    _, provider2 = await _run(jid2, [('', [ToolCall('s2', 'plan_people', {})]), ('Done.', [])])
    assert 'APPROVED for this run: 40 rows, at most 83 credits' in provider2.histories[0][-1]['content']
    job2 = db.execute(
        'SELECT status, list_id::text, stages, progress_done, progress_total FROM jobs WHERE id = %s', (jid2,)
    ).fetchone()
    assert job2['status'] == 'succeeded' and job2['list_id'] == plan['list_id']
    assert [(s['tool'], s['units'], s['done']) for s in job2['stages']] == [
        ('plan_people', 3, 1),
        ('plan_emails', 40, 0),
    ]
    assert (job2['progress_done'], job2['progress_total']) == (1, 43)

    # a newer version makes the old one stale
    _, jid3 = _job(db, uid, 'make it 200', session_id=sid)
    await _run(jid3, [('', [ToolCall('p2', 'propose_plan', _plan_args(rows=200, email_units=200))]), ('x', [])])
    with pytest.raises(HTTPException) as e:
        start_turn(
            member(db, uid),
            ChatRequest(
                message='ok',
                session_id=uuid.UUID(sid),
                plan_approval={'list_id': plan['list_id'], 'version': 1, 'rows': 40},
            ),
        )
    assert e.value.status_code == 409


@needs_db
async def test_a_second_plan_in_the_step_never_runs_while_the_first_waits_for_its_card(db):
    """#465: a large plan then a small one in one step: the card shows the large one and nothing approves the small."""
    from app.agent.routes import ChatRequest

    uid = _user(db, credits=500)
    sid, jid = _job(db, uid, '74 CTOs with emails')
    large, small = _plan_args(rows=74, email_units=74), _plan_args(rows=23, email_units=23)
    step = [ToolCall('p1', 'propose_plan', large), ToolCall('p2', 'propose_plan', small)]
    events, provider = await _run(jid, [('', step), ('unused', [])])

    assert len(provider.turns) == 1
    plan = dict(events)['message_saved']['question']['plan']
    assert (plan['estimated_credits'], plan['version'], plan['needs_approval']) == (151, 1, True)
    assert db.execute('SELECT version, approved_job_id FROM list_plans').fetchall() == [
        {'version': 1, 'approved_job_id': None}
    ]
    assert [d['tool_name'] for e, d in events if e == 'tool_start'] == ['propose_plan']
    required = "SELECT count(*) AS n FROM list_columns WHERE list_id = %s AND (config->>'required')::boolean"
    assert db.execute(required, (plan['list_id'],)).fetchone()['n'] == 0

    _, jid2 = start_turn(
        member(db, uid),
        ChatRequest(
            message='Approve',
            session_id=uuid.UUID(sid),
            plan_approval={'list_id': plan['list_id'], 'version': 1, 'rows': 74},
        ),
    )
    await _run(jid2, [('', [ToolCall('s1', 'plan_people', {})]), ('Done.', [])])
    job2 = db.execute('SELECT status, stages FROM jobs WHERE id = %s', (jid2,)).fetchone()
    assert job2['status'] == 'succeeded'
    assert [(s['tool'], s['units']) for s in job2['stages']] == [('plan_people', 3), ('plan_emails', 74)]


@needs_db
async def test_a_plan_after_a_question_in_the_same_step_is_never_saved(db):
    """#465: the turn pauses on the question; a small plan behind it would be auto-approved with nobody asked."""
    uid = _user(db, credits=500)
    _, jid = _job(db, uid, 'CTOs')
    step = [
        ToolCall('q1', 'ask_user', {'question': 'Which country?'}),
        ToolCall('p1', 'propose_plan', _plan_args(2, 2)),
    ]
    events, _ = await _run(jid, [('', step), ('unused', [])])

    assert dict(events)['message_saved']['question']['question'] == 'Which country?'
    assert db.execute('SELECT count(*) AS n FROM list_plans').fetchone()['n'] == 0


@needs_db
async def test_unplanned_run_stops_at_the_large_run_threshold(db):
    uid = _user(db, credits=500)
    _, jid = _job(db, uid, 'emails for everyone')
    big = {'row_ids': [str(uuid.uuid4()) for _ in range(51)]}  # 102 credits
    events, _ = await _run(jid, [('', [ToolCall('e1', 'plan_emails', big)]), ('ok', [])])
    result = dict(events)['tool_result']
    assert result['status'] == 'failed' and 'propose_plan' in result['result_preview']
    assert _balance(db, uid) == 500


@needs_db
async def test_small_plan_runs_at_once_and_undelivered_rows_are_refunded(db, monkeypatch):
    from app.lists.export_routes import export_csv
    from app.lists.routes import RowPatch, get_list, get_rows, patch_row
    from app.notifications import service as notifications

    outcomes = []  # #351: the search-done email reads the turn's net spend and undelivered rows from here
    real_outcome = notifications.turn_outcome
    monkeypatch.setattr(notifications, 'turn_outcome', lambda *a: outcomes.append(real_outcome(*a)) or outcomes[-1])

    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, '2 CTOs with emails')
    small = _plan_args(rows=2, email_units=2)  # 3 + 4 credits: no approval needed
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', small)]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'plan_emails', {})]),  # no row ids: progress counts the rows it searched
            ('Done.', []),
        ],
    )

    job = db.execute('SELECT status, credits_spent, stages, result FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert job['status'] == 'succeeded'
    assert [(s['units'], s['done']) for s in job['stages']] == [(3, 1), (2, 2)]
    # #352: the back office's note and each stage's elapsed time
    assert (job['result']['refunded'], job['result']['undelivered']) == (2, 1)
    assert all(s['started_at'] <= s['finished_at'] for s in job['stages'])
    refund = next(d for e, d in events if e == 'credits' and d.get('refund'))
    assert {k: v for k, v in refund.items() if k != 'summary'} == {
        'tool_id': 'e1',
        'tool_name': 'plan_emails',
        'cost': -2,
        'remaining': 98,
        'refund': True,
    }
    assert refund['summary']['refunded'] == 1
    assert job['credits_spent'] == 2 and _balance(db, uid) == 98
    assert outcomes == [('search_done', {'refunded': 2, 'spent': 2, 'undelivered': 1})]

    user = member(db, uid)
    act_as(user.org_id, user.id)  # the user's requests, then the enrichment's own read in their turn
    list_id = db.execute('SELECT id FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['id']
    rows = {r['cells']['full_name']: r['delivered'] for r in get_rows(list_id, user, cursor=0, limit=100)['rows']}
    assert rows == {'Ada Rossi': True, 'Bo Bianchi': False}
    meta = get_list(list_id, user)
    assert (meta['mode'], meta['row_count'], meta['delivered_count']) == ('build', 2, 1)
    csv = export_csv(list_id, user).body.decode()
    assert 'Ada Rossi' in csv and 'Bo Bianchi' not in csv

    # the refund took back what it paid for: completing Bo by hand delivers no free email
    bo = db.execute("SELECT id, cells, cell_meta FROM list_rows WHERE cells->>'full_name' = 'Bo Bianchi'").fetchone()
    assert 'email' not in bo['cells'] and 'email_status' not in bo['cells']
    assert 'email' not in bo['cell_meta']
    refund = db.execute('SELECT state, keys FROM row_charges WHERE row_id = %s', (bo['id'],)).fetchall()
    assert refund == [{'state': 'refunded', 'keys': ['email', 'email_status']}]  # the billed keys it held
    assert ('list_updated', {'list_id': str(list_id), 'entity': 'person', 'rows_updated': 1}) in events
    # the batch an enrichment picks itself passes over it: the vendor is not paid again every turn
    from app.agent.tools.handlers.enrich_email import next_unenriched
    from app.lists.rows import add_rows

    cy = add_rows(db, list_id, 'person', [{'full_name': 'Cy Neri', 'linkedin_url': 'https://linkedin.com/in/cy'}])
    assert next_unenriched(str(list_id)) == cy
    patched = patch_row(list_id, bo['id'], RowPatch(cells={'title': 'CEO'}), user)
    assert not patched['delivered'] and 'email' not in patched['cells']  # the email is missing again
    csv = export_csv(list_id, user).body.decode()
    assert '1@acme.test' not in csv and '0@acme.test' in csv
    rows = get_rows(list_id, user, cursor=0, limit=100)['rows']
    assert not any(r['cells'].get('email') == '1@acme.test' for r in rows)


@needs_db
@pytest.mark.parametrize(
    ('emails', 'stage'),
    [({}, (2, 2)), ({'row_ids': [str(uuid.uuid4())]}, (2, 1))],
    ids=['every delivered row searched', 'one row searched'],
)
async def test_a_per_row_stage_waits_only_for_the_rows_the_search_delivered(db, emails, stage):
    """#568: the plan approved 5 rows and the search found 2; searching both ends the email step."""
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, '5 CTOs with emails')
    await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', _plan_args(rows=5, email_units=5))]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'plan_emails', emails)]),
            ('Done.', []),
        ],
    )
    job = db.execute('SELECT stages, progress_done, progress_total FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert [(s['units'], s['done']) for s in job['stages']] == [(3, 1), stage]
    assert (job['progress_done'], job['progress_total']) == (1 + stage[1], 3 + stage[0])


@needs_db
@pytest.mark.parametrize(
    ('last', 'stage'),
    [
        ([], {'units': 4, 'done': 2}),
        (
            [('', [ToolCall('e2', 'plan_emails', {'fail': True})])],
            {'units': 4, 'done': 2, 'error': {'code': 'provider'}},
        ),
    ],
    ids=['rows added after the search', 'then the email search fails'],
)
async def test_a_per_row_stage_grows_back_with_rows_found_after_it_ran(db, last, stage):
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, '5 CTOs with emails')
    await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', _plan_args(rows=5, email_units=5))]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'plan_emails', {})]),
            ('', [ToolCall('s2', 'plan_more_people', {})]),
            *last,
            ('Done.', []),
        ],
    )
    emails = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid,)).fetchone()['stages'][1]
    assert {k: emails[k] for k in ('units', 'done', 'error') if k in emails} == stage


@needs_db
@pytest.mark.parametrize('email_units', [5, 10], ids=['one contact per company', 'two contacts per company'])
async def test_a_per_row_stage_counts_the_rows_of_the_list_it_searches(db, email_units):
    """A company plan's email step searches the people list: 2 companies, 4 people, 3 searched, then a failure."""
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, '5 companies, their people and emails')
    plan = {
        **_plan_args(rows=5),
        'entity': 'company',
        'columns': [{'key': 'name', 'required': True}],
        'stages': [
            {'tool': 'fake_companies', 'label': 'Companies', 'units': 1},
            {'tool': 'plan_people', 'label': 'People', 'units': 1},
            {'tool': 'plan_emails', 'label': 'Emails', 'units': email_units, 'per_row': True},
        ],
    }
    await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', plan)]),
            ('', [ToolCall('c1', 'fake_companies', {})]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('s2', 'plan_more_people', {})]),
            ('', [ToolCall('e1', 'plan_emails', {'row_ids': [str(uuid.uuid4()) for _ in range(3)]})]),
            ('', [ToolCall('e2', 'plan_emails', {'fail': True})]),
            ('Done.', []),
        ],
    )
    emails = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid,)).fetchone()['stages'][2]
    assert {k: emails[k] for k in ('units', 'done', 'error') if k in emails} == {
        'units': 4,
        'done': 3,
        'error': {'code': 'provider'},
    }


# ── research columns (#59) ─────────────────────────────────────────────────


def _research_args(rows=2):
    args = _plan_args(rows=rows, email_units=0)
    args['columns'] = [*args['columns'], {'label': 'Sells to hospitals', 'research': 'Does it sell to hospitals?'}]
    return args


def test_research_columns_are_priced_per_row():
    plan = plans.build(_research_args(rows=100))
    assert isinstance(plan, dict)
    research = plan['columns'][-1]
    assert (research['key'], research['research']) == ('ai_sells_to_hospitals', 'Does it sell to hospitals?')
    # 3 search calls + 1 credit per researched cell; the turn's tools get the rest
    assert (plan['credits_research_per_row'], plan['estimated_credits']) == (1, 103)
    assert plans.tool_budget(plan, 100, 103) == 3
    assert isinstance(plans.build({**_research_args(), 'columns': [{'research': 'Who?'}]}), str)
    required = [{'label': 'Hospitals', 'research': 'Does it sell to hospitals?', 'required': True}]
    assert plans.build({**_research_args(), 'columns': required}) == 'a research column cannot be required'


@needs_db
async def test_research_columns_fill_from_the_first_rows(db, monkeypatch):
    from app import worker

    queued: list[tuple] = []
    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: queued.append(a))
    uid = _user(db, credits=100)
    sid, jid = _job(db, uid, '2 companies with a research column')
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', _research_args())]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('s2', 'plan_people', {})]),  # duplicates: no new rows
            ('Done.', []),
        ],
    )
    list_id = db.execute('SELECT id::text FROM lists WHERE chat_session_id = %s', (sid,)).fetchone()['id']
    col = db.execute(
        "SELECT kind, config FROM list_columns WHERE list_id = %s AND key = 'ai_sells_to_hospitals'", (list_id,)
    ).fetchone()
    assert col == {'kind': 'ai', 'config': {'prompt': 'Does it sell to hospitals?'}}
    # at plan start (rows already there), on the first rows, at the end of the turn
    assert queued == [(list_id, ['ai_sells_to_hospitals'], 2, jid)] * 3  # capped at the approved rows, for the turn
    assert dict(events)['message_saved']['status'] == 'success'


@needs_db
def test_fill_plan_columns_retries_a_live_fill(db, monkeypatch):
    from celery.exceptions import Retry

    from app import worker
    from app.lists.ai_columns import queue_fill

    uid = _user(db)
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (default_organization(%s), %s, %s, 'L', 'company') RETURNING id::text",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    for i, key in enumerate(('ai_a', 'ai_b')):
        db.execute(
            "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, %s, %s, 'ai', %s, '{}')",
            (lid, key, key, i),
        )
    started: list[str] = []
    monkeypatch.setattr(worker.fill_ai_column, 'delay', lambda _lid, key: started.append(key))
    monkeypatch.setattr(worker.fill_plan_columns, 'retry', lambda **kw: Retry(str(kw['args'])))
    assert queue_fill(db, lid, 'ai_a')  # a fill of ai_a is already live
    turn = _as_lists_member(db, lid, uid)  # the member whose turn queued it, as the worker enters them
    _, jid = _job(db, uid, 'research')
    with turn, pytest.raises(Retry, match=f"\\['ai_a'\\], 2, '{jid}'"):
        worker.fill_plan_columns.run(lid, ['ai_a', 'ai_b', 'ai_deleted'], 2, jid)
    assert started == ['ai_b']  # the deleted column is dropped, the live one retried
    # the retry carries the cap and the turn; after the last retry the chain gives up instead of raising
    fill = db.execute("SELECT config->'fill' AS f FROM list_columns WHERE key = 'ai_b'").fetchone()['f']
    assert (fill['cap'], fill['job']) == (2, jid)
    monkeypatch.setattr(worker.fill_plan_columns, 'max_retries', 0)
    with _as_lists_member(db, lid, uid):
        worker.fill_plan_columns.run(lid, ['ai_a'], 2)


@needs_db
async def test_research_column_never_makes_paid_emails_refund(db, monkeypatch):
    """Research cells fill after the turn: they are never required, so the end-of-turn refund keeps emails."""
    from app import worker

    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: None)
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, 'people with emails and research')
    args = _research_args()
    args['columns'] = [
        {'key': 'full_name', 'required': True},
        {'key': 'email', 'required': True},
        {'label': 'Hospitals', 'research': 'Does it sell to hospitals?'},
    ]
    args['stages'] = [{'tool': 'plan_people', 'units': 1}, {'tool': 'plan_emails', 'units': 2, 'per_row': True}]
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', args)]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'plan_emails', {})]),
            ('Done.', []),
        ],
    )
    assert not [d for e, d in events if e == 'credits' and d.get('refund')]
    emails = [r['cells'].get('email') for r in db.execute('SELECT cells FROM list_rows ORDER BY seq')]
    assert emails == ['0@acme.test', '1@acme.test']
    required = db.execute("SELECT key FROM list_columns WHERE (config->>'required')::boolean ORDER BY key").fetchall()
    assert [r['key'] for r in required] == ['email', 'full_name']


@needs_db
def test_research_key_never_takes_over_a_static_column(db):
    uid = _user(db)
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (default_organization(%s), %s, %s, 'L', 'company') RETURNING id::text",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, 'ai_score', 'AI score', 'static', 0)",
        (lid,),
    )
    plan = plans.build({**_research_args(), 'columns': [{'label': 'Score', 'research': 'What score?'}]})
    assert isinstance(plan, dict) and plan['columns'][0]['key'] == 'ai_score'
    plans.claim_research_keys(db, lid, plan)
    assert plan['columns'][0]['key'] == 'ai_score_2'


@needs_db
async def test_small_plan_with_research_spends_its_own_ceiling_minus_research(db, monkeypatch):
    """#259: auto-approved (5 credits, 2 of them research): tools may spend 5 - 2, not the 100 threshold."""
    from app import worker

    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: None)
    uid = _user(db, credits=500)
    jid, _, ids = _people_list(db, uid, n=2)
    big = {'row_ids': ids[:1]}  # 2 credits: fits 5 - 2
    bigger = {'row_ids': ids[1:]}  # 2 more: over
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', _research_args())]),
            ('', [ToolCall('e1', 'plan_emails', big)]),
            ('', [ToolCall('e2', 'plan_emails', bigger)]),
            ('Done.', []),
        ],
    )
    statuses = [d['status'] for e, d in events if e == 'tool_result' and d['tool_name'] == 'plan_emails']
    assert statuses == ['success', 'failed']
    assert 'at most 3 credits' in [d for e, d in events if e == 'tool_result'][-1]['result_preview']


def _research_only(label, question, rows):
    """A plan with no stages and one research column: ``rows`` credits, all of them billed by the fill task."""
    return {
        'mode': 'build',
        'entity': 'person',
        'target_rows': rows,
        'columns': [{'key': 'full_name'}, {'label': label, 'research': question}],
        'stages': [],
    }


@needs_db
async def test_a_turn_auto_approves_one_plan_and_the_next_waits_for_the_card(db, monkeypatch):
    """#408: research bills outside the turn's spend, so a second auto-approved plan would queue a second fill."""
    from app import worker

    queued: list[tuple] = []
    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: queued.append(a))
    uid = _user(db, credits=500)
    _, jid = _job(db, uid, 'research every lead')
    first = _research_only('Sells to hospitals', 'Does it sell to hospitals?', rows=100)
    second = _research_only('Uses SAP', 'Does it use SAP?', rows=99)
    events, provider = await _run(
        jid,
        [
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('p1', 'propose_plan', first)]),
            ('', [ToolCall('p2', 'propose_plan', second)]),
            ('unused', []),
        ],
    )

    assert len(provider.turns) == 1  # paused on the second plan
    plan = dict(events)['message_saved']['question']['plan']
    assert (plan['version'], plan['estimated_credits'], plan['needs_approval']) == (2, 99, True)
    approved = db.execute('SELECT version FROM list_plans WHERE approved_job_id = %s', (jid,)).fetchall()
    assert approved == [{'version': 1}]
    assert {key for _, keys, *_ in queued for key in keys} == {'ai_sells_to_hospitals'}
    assert db.execute('SELECT status FROM jobs WHERE id = %s', (jid,)).fetchone()['status'] == 'needs_input'


@needs_db
async def test_a_plan_counts_what_the_turn_already_spent(db, monkeypatch):
    """#408: 90 credits of tools, then a plan of 100 research credits: the turn would pass 100 with no card."""
    from app import worker

    queued: list[tuple] = []
    monkeypatch.setattr(worker.fill_plan_columns, 'delay', lambda *a: queued.append(a))
    uid = _user(db, credits=500)
    jid, _, ids = _people_list(db, uid, n=45)
    emails = {'row_ids': ids}  # 90 credits: within the unplanned 100
    events, provider = await _run(
        jid,
        [
            ('', [ToolCall('e1', 'plan_emails', emails)]),
            ('', [ToolCall('p1', 'propose_plan', _research_only('Uses SAP', 'Does it use SAP?', rows=100))]),
            ('unused', []),
        ],
    )

    assert len(provider.turns) == 1
    plan = dict(events)['message_saved']['question']['plan']
    assert (plan['estimated_credits'], plan['needs_approval']) == (100, True)
    assert queued == []
    job = db.execute('SELECT status, credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()
    assert job == {'status': 'needs_input', 'credits_spent': 90}


@needs_db
async def test_research_skips_rows_the_turn_left_undelivered(db, monkeypatch):
    """#150: Bo misses the required title, so the end-of-turn fill researches and bills only Ada."""
    import json

    from app import worker
    from app.lists.ai_columns import fill_column
    from app.llm.base import LLMResponse

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
    uid = _user(db, credits=100)
    _, jid = _job(db, uid, '2 CTOs with research')
    args = _research_args()
    args['columns'] = [
        {'key': 'full_name', 'required': True},
        {'key': 'title', 'required': True},
        {'label': 'Hospitals', 'research': 'Does it sell to hospitals?'},
    ]
    args['stages'] = [{'tool': 'plan_people', 'units': 1}]
    await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', args)]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('Done.', []),
        ],
    )
    _at_start, _on_first_rows, (list_id, keys, cap, job) = plan_fills
    with _as_lists_member(db, list_id, uid):  # the worker runs both as the member whose turn queued them
        worker.fill_plan_columns.run(list_id, keys, cap, job)
        await fill_column(list_id, 'ai_hospitals', provider=Research())

    research = {
        r['cells']['full_name']: r['cells'].get('ai_hospitals')
        for r in db.execute('SELECT cells FROM list_rows WHERE list_id = %s', (list_id,))
    }
    assert research == {'Ada Rossi': 'Yes', 'Bo Bianchi': None}
    assert _balance(db, uid) == 99


@needs_db
def test_plan_never_makes_an_ai_column_required(db):
    from psycopg.types.json import Jsonb

    uid = _user(db)
    lid = db.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity) VALUES (default_organization(%s), %s, %s, 'L', 'person') RETURNING id::text",
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, 'ai_h', 'H', 'ai', 0, %s)",
        (lid, Jsonb({'prompt': 'Hospitals?'})),
    )
    plan = plans.build(
        {**_plan_args(), 'columns': [{'key': 'full_name', 'required': True}, {'key': 'ai_h', 'required': True}]}
    )
    assert isinstance(plan, dict)
    plans.apply_columns(db, lid, plan)
    required = db.execute(
        "SELECT key FROM list_columns WHERE list_id = %s AND (config->>'required')::boolean", (lid,)
    ).fetchall()
    assert [r['key'] for r in required] == ['full_name']


@needs_db
async def test_approval_without_an_optional_stage_is_priced_and_gated_without_it(db):
    from app.agent.routes import ChatRequest

    uid = _user(db, credits=500)
    sid, jid = _job(db, uid, '100 CTOs')
    args = _plan_args()
    args['stages'][1]['optional'] = True
    events, _ = await _run(jid, [('', [ToolCall('p1', 'propose_plan', args)]), ('unused', [])])
    plan = dict(events)['message_saved']['question']['plan']
    approval = {'list_id': plan['list_id'], 'version': 1, 'rows': 100}

    for dropped, code in (([0], 'PLAN.STAGE_REQUIRED'), ([7], 'PLAN.STAGE_UNKNOWN')):
        with pytest.raises(HTTPException) as e:
            start_turn(
                member(db, uid),
                ChatRequest(
                    message='ok', session_id=uuid.UUID(sid), plan_approval={**approval, 'dropped_stages': dropped}
                ),
            )
        assert (e.value.status_code, e.value.detail['code']) == (422, code)
    assert db.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 1

    _, jid2 = start_turn(
        member(db, uid),
        ChatRequest(message='ok', session_id=uuid.UUID(sid), plan_approval={**approval, 'dropped_stages': [1]}),
    )
    stored = db.execute('SELECT approved_rows, approved_credits FROM list_plans').fetchone()
    assert stored == {'approved_rows': 100, 'approved_credits': 3}
    card = db.execute('SELECT question FROM chat_messages WHERE question IS NOT NULL').fetchone()['question']['plan']
    assert [s['dropped'] for s in card['stages']] == [False, True]
    assert (card['approved_rows'], card['estimated_credits'], card['credits_per_row']) == (100, 3, 0.0)

    step = [ToolCall('s1', 'plan_people', {}), ToolCall('e1', 'plan_emails', {})]
    events2, provider = await _run(jid2, [('', step[:1]), ('', step[1:]), ('Done.', [])])
    assert 'dropped these stages, do not run them: Emails' in provider.histories[0][-1]['content']
    done = {d['tool_id']: d for e, d in events2 if e == 'tool_result'}
    assert done['e1']['status'] == 'failed' and 'dropped' in done['e1']['result_preview'] and _balance(db, uid) == 500
    stages = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid2,)).fetchone()['stages']
    assert [s['tool'] for s in stages] == ['plan_people']


@needs_db
async def test_a_refused_call_never_starts_its_stage_clock(db):
    """#352: a stage's elapsed time starts with the first call past the preflight; a refused one leaves it unset."""
    uid = _user(db, credits=3)
    _, jid = _job(db, uid, '2 CTOs with emails')
    events, _ = await _run(
        jid,
        [
            ('', [ToolCall('p1', 'propose_plan', _plan_args(rows=2, email_units=2))]),
            ('', [ToolCall('s1', 'plan_people', {})]),
            ('', [ToolCall('e1', 'plan_emails', {'row_ids': ['a', 'b']})]),  # 4 credits, 3 left: refused
            ('Done.', []),
        ],
    )
    refused = next(d for e, d in events if e == 'tool_result' and d['tool_id'] == 'e1')
    assert refused['status'] == 'failed' and 'Not enough credits' in refused['result_preview']
    people, emails = db.execute('SELECT stages FROM jobs WHERE id = %s', (jid,)).fetchone()['stages']
    assert people['started_at'] <= people['finished_at']
    assert 'started_at' not in emails and 'finished_at' not in emails


# ── enrich_email pricing (#418) ────────────────────────────────────────────


def _people_list(db, uid, n=50):
    from app.lists.rows import add_rows, create_list

    sid, jid = _job(db, uid, 'emails for my list')
    list_id = create_list(db, user_id=uid, session_id=sid, name='People', entity='person')
    rows = [{'full_name': f'P {i}', 'linkedin_url': f'https://linkedin.com/in/p{i}'} for i in range(n)]
    return jid, list_id, add_rows(db, list_id, 'person', rows)


def _found_emails(monkeypatch):
    """Every lookup finds a verified email; the returned list records each row the vendors were asked for."""
    from app.agent.tools.handlers import enrich_email

    looked_up: list[str] = []

    async def found(p, *_):
        looked_up.append(p['row_id'])
        return {'row_id': p['row_id'], 'cells': {'email': f'{p["row_id"]}@acme.test', 'email_status': 'verified'}}

    monkeypatch.setattr(enrich_email, '_enrich_one', found)
    return looked_up


def _stored_emails(db, list_id):
    return db.execute(
        "SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND cells->>'email' IS NOT NULL", (list_id,)
    ).fetchone()['n']


@needs_db
@pytest.mark.parametrize(('credits', 'cap', 'error'), [(2, None, 'Not enough credits'), (1000, 2, 'cap')])
async def test_a_split_enrich_email_call_is_priced_on_every_row_it_names(
    db, monkeypatch, keys, balance_events, credits, cap, error
):
    """#418: 1 row id plus 49 people is 50 emails at 1 credit: with 2 credits left in the wallet, or under a hard
    project cap of 2, it is refused before a vendor runs, like the same 50 rows given as row_ids."""
    uid = _user(db, credits=credits)
    if cap:
        _cap(db, uid, cap, 'hard')
    jid, list_id, ids = _people_list(db, uid)
    looked_up = _found_emails(monkeypatch)
    split = {'row_ids': ids[:1], 'people': [{'row_id': r} for r in ids[1:]]}
    events, _ = await _run(jid, [('', [ToolCall('e1', 'enrich_email', split)]), ('Done.', [])])
    spent = db.execute('SELECT credits_spent FROM jobs WHERE id = %s', (jid,)).fetchone()['credits_spent']
    assert (len(looked_up), _stored_emails(db, list_id), spent, _balance(db, uid)) == (0, 0, 0, credits)
    result = next(d for e, d in events if e == 'tool_result')
    assert result['status'] == 'failed' and error in result['result_preview']


@needs_db
async def test_a_split_enrich_email_call_with_the_credits_bills_every_email(db, monkeypatch, keys, balance_events):
    """#418: with 100 credits the same split call finds and bills all 50 emails at 1 credit each (#398)."""
    uid = _user(db, credits=100)
    jid, list_id, ids = _people_list(db, uid)
    looked_up = _found_emails(monkeypatch)
    split = {'row_ids': ids[:1], 'people': [{'row_id': r} for r in ids[1:]]}
    events, _ = await _run(jid, [('', [ToolCall('e1', 'enrich_email', split)]), ('Done.', [])])
    assert ('credits', {'tool_id': 'e1', 'tool_name': 'enrich_email', 'cost': 50, 'remaining': 50}) in events
    assert (len(looked_up), _stored_emails(db, list_id), _balance(db, uid)) == (50, 50, 50)


@needs_db
async def test_enrich_email_never_looks_up_more_rows_than_its_preflight_priced(db, monkeypatch, keys, balance_events):
    """#418: row_ids sent as an object is no list the preflight counts, so the call is priced on its 1 person;
    the handler then works on that 1 row only, not on the 49 more the object names."""
    uid = _user(db, credits=100)
    jid, list_id, ids = _people_list(db, uid)
    looked_up = _found_emails(monkeypatch)
    odd = {'people': [{'row_id': ids[0]}], 'row_ids': dict.fromkeys(ids[1:], 1)}
    await _run(jid, [('', [ToolCall('e1', 'enrich_email', odd)]), ('Done.', [])])
    assert (len(looked_up), _stored_emails(db, list_id), _balance(db, uid)) == (1, 1, 99)


@needs_db
async def test_enrich_phone_never_looks_up_more_rows_than_its_preflight_priced(db, monkeypatch, keys, balance_events):
    """#418: the same object of 25 row ids prices enrich_phone at its 1 default unit (8 credits): 1 lookup."""
    from app.agent.tools.handlers import enrich_phone

    uid = _user(db, credits=500)
    jid, list_id, ids = _people_list(db, uid, n=25)
    looked_up: list[str] = []

    async def found(p, *_):
        looked_up.append(p['row_id'])
        return {'row_id': p['row_id'], 'cells': {'phone': '+39 02 0000', 'phone_status': 'revealed'}}

    monkeypatch.setattr(enrich_phone, '_phone_one', found)
    await _run(jid, [('', [ToolCall('p1', 'enrich_phone', {'row_ids': dict.fromkeys(ids, 1)})]), ('Done.', [])])
    phones = db.execute("SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND cells ? 'phone'", (list_id,))
    assert (len(looked_up), phones.fetchone()['n'], _balance(db, uid)) == (1, 1, 492)
