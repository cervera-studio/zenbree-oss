"""#209: a chat goal, the free draft_campaign tool and the home to-do list, against real SQL."""

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agent.loop import Turn
from app.agent.routes import ChatRequest
from app.agent.tools import registry
from app.agent.tools.registry import ToolContext, ToolResult
from app.agent.units import PricedCall
from app.llm.base import ToolCall
from app.main import app
from tests import test_agent
from tests.conftest import act_in_own_org, member, project, sign_in, start_turn
from tests.test_agent import FakeProvider, _balance, _job, _run, _user, needs_db

pytestmark = needs_db

db = test_agent.db


PEOPLE = [
    {'first_name': 'Anna', 'email': 'anna@client.test'},
    {'first_name': 'Bea', 'email': 'bea@client.test'},
]


def _people(c, uid, sid, rows=PEOPLE, entity='person') -> str:
    lid = c.execute(
        "INSERT INTO lists (organization_id, project_id, user_id, name, entity, chat_session_id) VALUES (default_organization(%s), %s, %s, 'L', %s, %s) RETURNING id",
        (uid, project(c, uid), uid, entity, sid),
    ).fetchone()['id']
    for cells in rows:
        c.execute('INSERT INTO list_rows (list_id, cells) VALUES (%s, %s)', (lid, json.dumps(cells)))
    return str(lid)


def _goal(c, sid):
    return c.execute('SELECT goal FROM chat_sessions WHERE id = %s', (sid,)).fetchone()['goal']


async def test_goal_is_kept_for_every_later_turn_and_never_rewritten(db):
    uid = _user(db)
    sid, jid = start_turn(member(db, uid), ChatRequest(message='find CTOs', goal='campaign'))
    _, provider = await _run(jid, [('ok', [])])
    assert 'draft_campaign' in provider.systems[0]

    db.execute("UPDATE jobs SET status = 'succeeded'")
    sid2, jid2 = start_turn(member(db, uid), ChatRequest(message='more', session_id=uuid.UUID(sid)))
    assert sid2 == sid
    _, provider = await _run(jid2, [('ok', [])])
    assert 'draft_campaign' in provider.systems[0]  # a plain follow-up keeps the goal

    plain, _ = start_turn(member(db, _user(db)), ChatRequest(message='hi'))
    assert _goal(db, plain) is None
    db.execute("UPDATE jobs SET status = 'succeeded'")
    start_turn(member(db, uid), ChatRequest(message='again', session_id=uuid.UUID(sid), goal=None))
    other = _user(db)
    own, _ = start_turn(member(db, other), ChatRequest(message='x'))
    db.execute("UPDATE jobs SET status = 'succeeded'")
    start_turn(member(db, other), ChatRequest(message='y', session_id=uuid.UUID(own), goal='campaign'))
    assert _goal(db, own) is None  # an existing chat is never given a goal later
    assert _goal(db, sid) == 'campaign'


async def test_a_plain_chat_prompt_has_no_campaign_paragraph(db):
    uid = _user(db)
    _, jid = _job(db, uid, 'find CTOs')
    _, provider = await _run(jid, [('ok', [])])
    assert 'draft_campaign' not in provider.systems[0]


async def test_calling_draft_campaign_twice_leaves_one_draft_and_charges_nothing(db):
    uid = _user(db, credits=50)
    sid, jid = _job(db, uid, 'find CTOs')
    lid = _people(db, uid, sid)
    call = lambda n: [ToolCall(n, 'draft_campaign', {'name': 'Q4 CTOs'})]  # noqa: E731
    events, _ = await _run(jid, [('', call('d1')), ('', call('d2')), ('done', [])])

    rows = db.execute('SELECT id::text, name, status, list_id::text FROM outreach_campaigns').fetchall()
    assert [(r['name'], r['status'], r['list_id']) for r in rows] == [('Q4 CTOs', 'draft', lid)]
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 2
    assert _balance(db, uid) == 50
    results = [d for e, d in events if e == 'tool_result']
    assert [r['campaign'] for r in results] == [{'id': rows[0]['id'], 'name': 'Q4 CTOs', 'target_count': 2}] * 2
    saved = db.execute('SELECT tool_calls FROM chat_messages WHERE role = %s', ('assistant',)).fetchone()
    assert [t['campaign']['id'] for t in saved['tool_calls']] == [rows[0]['id']] * 2


async def test_a_second_call_enrolls_only_people_added_since(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    ctx = ToolContext(user_id=uid, session_id=sid)
    tool = registry.get('draft_campaign')
    first = await tool.handler({'name': 'A'}, ctx)
    assert first.ok is False

    _people(db, uid, sid)
    one = await tool.handler({'name': 'A'}, ctx)
    db.execute(
        'INSERT INTO list_rows (list_id, cells) VALUES ((SELECT id FROM lists), %s)',
        (json.dumps({'first_name': 'Cy', 'email': 'cy@client.test'}),),
    )
    two = await tool.handler({'name': 'B'}, ctx)
    assert (one.data['enrolled'], two.data['enrolled'], two.data['target_count']) == (2, 1, 3)
    assert one.campaign['id'] == two.campaign['id']
    assert db.execute('SELECT count(*) AS n FROM outreach_campaigns').fetchone()['n'] == 1


@pytest.mark.parametrize('case', ['no_list', 'empty_list', 'company_list', 'other_chat', 'nobody_reachable'])
async def test_draft_campaign_refuses_without_this_chats_people(db, case):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    other_sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s) RETURNING id',
        (
            uid,
            project(db, uid),
            uid,
        ),
    ).fetchone()['id']
    if case == 'empty_list':
        _people(db, uid, sid, rows=[])
    if case == 'company_list':
        _people(db, uid, sid, entity='company')
    if case == 'other_chat':
        _people(db, uid, other_sid)
    if case == 'nobody_reachable':
        _people(db, uid, sid, rows=[{'first_name': 'Eve'}])
    result = await registry.get('draft_campaign').handler({'name': 'A'}, ToolContext(user_id=uid, session_id=sid))
    assert result.ok is False and result.error and result.campaign is None
    assert db.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 0
    assert db.execute('SELECT count(*) AS n FROM outreach_campaigns').fetchone()['n'] == 0


async def test_draft_campaign_refusal_names_why_nobody_was_added(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    _people(db, uid, sid)
    _campaign(db, uid, 'live', emails=[p['email'] for p in PEOPLE], status='active')
    result = await registry.get('draft_campaign').handler({'name': 'A'}, ToolContext(user_id=uid, session_id=sid))
    assert result.ok is False and result.error and 'another campaign' in result.error


def test_another_users_list_cannot_be_stamped_with_this_chat(db):
    """The list names the organization of its chat, so a foreign organization's list cannot carry this chat id."""
    uid, other = _user(db), _user(db)
    sid, _ = _job(db, uid, 'x')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _people(db, other, sid)


async def test_draft_campaign_does_not_raise_on_bad_input(db):
    uid = _user(db)
    act_in_own_org(db, uid)
    result = await registry.get('draft_campaign').handler({}, ToolContext(user_id=uid, session_id='nope'))
    assert result.ok is False


def test_a_dropped_stage_blocks_even_a_free_tool():
    tool = registry.get('draft_campaign')
    job = {'id': 'j', 'organization_id': 'o', 'user_id': 'u', 'chat_session_id': 's'}
    turn = Turn(job, FakeProvider([]), lambda e, d: None)
    call = ToolCall('d1', 'draft_campaign', {'name': 'A'})
    assert turn._preflight(PricedCall.of(call, tool, 's')) is None
    turn.dropped_tools = frozenset({'draft_campaign'})
    refusal = turn._preflight(PricedCall.of(call, tool, 's'))
    assert isinstance(refusal, ToolResult) and refusal.ok is False


async def test_a_declined_draft_stays_declined_in_later_turns_of_the_chat(db):
    uid = _user(db, credits=500)
    sid, jid = start_turn(member(db, uid), ChatRequest(message='100 CTOs to email', goal='campaign'))
    args = {
        'mode': 'build',
        'entity': 'person',
        'target_rows': 100,
        'list_name': 'CTOs',
        'columns': [{'key': 'full_name', 'required': True}, {'key': 'email', 'required': True}],
        'stages': [
            {'tool': 'search_people', 'units': 3},
            {'tool': 'enrich_email', 'units': 100, 'per_row': True},
            {'tool': 'enrich_phone', 'units': 100, 'per_row': True},  # over LARGE_RUN_CREDITS: the card asks
            {'tool': 'draft_campaign', 'units': 1, 'optional': True},
        ],
    }
    events, _ = await _run(jid, [('', [ToolCall('p1', 'propose_plan', args)]), ('unused', [])])
    plan = dict(events)['message_saved']['question']['plan']
    approval = {'list_id': plan['list_id'], 'version': 1, 'rows': 100, 'dropped_stages': [3]}
    _, jid2 = start_turn(member(db, uid), ChatRequest(message='ok', session_id=uuid.UUID(sid), plan_approval=approval))
    await _run(jid2, [('Done.', [])])
    for cells in PEOPLE:
        db.execute('INSERT INTO list_rows (list_id, cells) VALUES (%s, %s)', (plan['list_id'], json.dumps(cells)))

    _, jid3 = start_turn(member(db, uid), ChatRequest(message='looks good', session_id=uuid.UUID(sid)))
    events3, provider = await _run(jid3, [('', [ToolCall('d1', 'draft_campaign', {'name': 'Q4'})]), ('ok', [])])

    assert 'call draft_campaign' not in provider.systems[0]
    assert 'declined the campaign draft' in provider.systems[0]
    assert 'draft_campaign' not in provider.tools[0] and 'search_people' in provider.tools[0]

    small = {
        **args,
        'target_rows': 2,
        'list_id': plan['list_id'],
        'stages': [
            {'tool': 'search_people', 'units': 1},
            {'tool': 'enrich_email', 'units': 2, 'per_row': True},
            {'tool': 'draft_campaign', 'units': 1, 'optional': True},
        ],
    }
    _, jid4 = start_turn(member(db, uid), ChatRequest(message='draft it after all', session_id=uuid.UUID(sid)))
    _, provider4 = await _run(jid4, [('', [ToolCall('p2', 'propose_plan', small)]), ('ok', [])])
    assert 'draft_campaign' not in provider4.tools[0] and 'draft_campaign' in provider4.tools[1]
    done = {d['tool_id']: d for e, d in events3 if e == 'tool_result'}
    assert done['d1']['status'] == 'failed' and 'dropped' in done['d1']['result_preview']
    assert db.execute('SELECT count(*) AS n FROM outreach_campaigns').fetchone()['n'] == 0


def test_the_campaign_route_still_opens_a_new_campaign_each_time_and_stamps_the_list(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    lid = _people(db, uid, sid)
    sign_in(app, member(db, uid))
    try:
        client = TestClient(app)
        a = client.post('/v1/campaigns', json={'name': 'A', 'list_id': lid})
        b = client.post('/v1/campaigns', json={'name': 'B', 'list_id': lid})
    finally:
        app.dependency_overrides.clear()
    assert a.status_code == b.status_code == 201
    assert a.json()['id'] != b.json()['id'] and a.json()['target_count'] == 2
    assert {r['list_id'] for r in db.execute('SELECT list_id::text FROM outreach_campaigns')} == {lid}


def _campaign(c, uid, name, waiting=0, emails=(), status='draft'):
    cid = c.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status) VALUES (default_organization(%s), %s, %s, %s, %s) RETURNING id',
        (uid, project(c, uid), uid, name, status),
    ).fetchone()['id']
    for email in emails:
        c.execute(
            "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, %s, 'active')",
            (cid, uid, email),
        )
    for n in range(waiting):
        tid = c.execute(
            "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, %s, 'active')"
            ' RETURNING id',
            (cid, uid, f'p{n}@{name}.test'),
        ).fetchone()['id']
        c.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient,'
            " idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'x@y.test', %s)",
            (cid, uid, tid, f'{cid}:{n}'),
        )
    return str(cid)


def _notify(c, uid, kind, name=None, read=False):
    c.execute(
        'INSERT INTO notifications (user_id, organization_id, kind, href, data, read_at)'
        ' VALUES (%s, default_organization(%s), %s, %s, %s, %s)',
        (uid, uid, kind, '/inbox', json.dumps({'name': name} if name else {}), 'now()' if read else None),
    )


def _todo(db, uid):
    user = member(db, uid)
    sign_in(app, user)
    try:
        r = TestClient(app).get('/v1/home/todo')
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    return r.json()['items']


def test_todo_is_empty_for_a_new_user(db):
    assert _todo(db, _user(db)) == []


def test_todo_lists_approvals_replies_and_a_running_search_in_order(db):
    uid, other = _user(db), _user(db)
    small, big = _campaign(db, uid, 'small', 1), _campaign(db, uid, 'big', 3)
    _campaign(db, uid, 'quiet')
    _notify(db, uid, 'reply', 'Old Name')
    _notify(db, uid, 'reply_urgent', 'Marco Rossi')
    _notify(db, uid, 'reply', 'Read One', read=True)
    _notify(db, uid, 'search_done')
    sid, _ = _job(db, uid, 'find CTOs')
    db.execute('UPDATE chat_sessions SET title = %s WHERE id = %s', ('CTOs in Milan', sid))
    db.execute("UPDATE jobs SET status = 'running'")
    _campaign(db, other, 'theirs', 5)
    _notify(db, other, 'reply', 'Stranger')
    assert small != big

    assert _todo(db, uid) == [
        {'kind': 'approvals', 'count': 4, 'title': 'big', 'href': f'/campaigns/{big}'},
        {'kind': 'replies', 'count': 2, 'title': 'Marco Rossi', 'href': '/inbox'},
        {'kind': 'running', 'count': 1, 'title': 'CTOs in Milan', 'href': f'/?s={sid}'},
    ]


def test_todo_leaves_out_a_stale_job_and_replies_without_a_name(db):
    uid = _user(db)
    _job(db, uid, 'x')
    db.execute("UPDATE jobs SET status = 'running', updated_at = now() - interval '25 minutes'")
    _notify(db, uid, 'reply')
    assert _todo(db, uid) == [{'kind': 'replies', 'count': 1, 'title': None, 'href': '/inbox'}]


def test_todo_does_not_count_a_held_message_whose_person_moved_on(db):
    uid = _user(db)
    cid = _campaign(db, uid, 'c', 2)
    db.execute("UPDATE outreach_campaign_targets SET status = 'replied' WHERE email LIKE 'p0@%'")
    assert _todo(db, uid) == [{'kind': 'approvals', 'count': 1, 'title': 'c', 'href': f'/campaigns/{cid}'}]


def test_todo_leaves_out_a_running_job_whose_chat_was_deleted(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    db.execute("UPDATE jobs SET status = 'running'")
    db.execute('DELETE FROM chat_sessions WHERE id = %s', (sid,))
    assert _todo(db, uid) == []


SEQUENCE = [
    {
        'channel': 'email',
        'subject': 'Ciao {{firstName}}',
        'body': 'Buongiorno {{firstName}}, di {{company}}.',
        'send_after_days': 0,
    },
    {'channel': 'email', 'body': 'Un promemoria.', 'send_after_days': 3, 'threaded': True},
]


def _steps(c, cid):
    return c.execute('SELECT steps FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()['steps']


async def _draft(args, uid, sid):
    return await registry.get('draft_campaign').handler(args, ToolContext(user_id=uid, session_id=sid))


async def test_draft_campaign_writes_the_sequence_on_the_draft_and_a_rewrite_replaces_it(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    _people(db, uid, sid)
    first = await _draft({'name': 'A', 'steps': SEQUENCE}, uid, sid)
    assert first.ok and first.data['steps_written'] == 2 and first.campaign['id'] == first.data['campaign_id']
    assert [(s['step'], s['channel'], s['threaded']) for s in _steps(db, first.data['campaign_id'])] == [
        (1, 'email', False),
        (2, 'email', True),
    ]
    again = await _draft({'name': 'A', 'steps': SEQUENCE[:1]}, uid, sid)
    assert again.data['campaign_id'] == first.data['campaign_id']
    assert len(_steps(db, first.data['campaign_id'])) == 1


async def test_draft_campaign_without_steps_keeps_the_copy(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    _people(db, uid, sid)
    first = await _draft({'name': 'A', 'steps': SEQUENCE}, uid, sid)
    await _draft({'name': 'A'}, uid, sid)
    assert len(_steps(db, first.data['campaign_id'])) == 2


async def test_draft_campaign_writes_an_invite_with_no_note(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    _people(db, uid, sid)
    invite = {'channel': 'linkedin_connect', 'send_after_days': 2}
    result = await _draft({'name': 'A', 'steps': [SEQUENCE[0], invite]}, uid, sid)
    assert result.ok, result.error
    assert [(s['channel'], s['body']) for s in _steps(db, result.data['campaign_id'])][1] == ('linkedin_connect', '')


@pytest.mark.parametrize(
    ('steps', 'reason'),
    [
        ([{'channel': 'email', 'body': 'Hi', 'send_after_days': 0}], 'subject'),
        ([{'channel': 'email', 'subject': 'S', 'body': 'Hi {{nickname}}', 'send_after_days': 0}], 'nickname'),
        ([{'channel': 'email', 'subject': 'S', 'body': 'Hi [Nome]', 'send_after_days': 0}], '[Nome]'),
        ([{'channel': 'email', 'subject': 'S\nT', 'body': 'Hi', 'send_after_days': 0}], 'single line'),
        ([{'channel': 'linkedin_connect', 'body': 'x' * 301, 'send_after_days': 0}], '300'),
        ([{'channel': 'email', 'subject': 'S', 'body': 'Hi', 'send_after_days': 0, 'tone': 'x'}], 'tone'),
        ('not a list', 'list'),
        (
            [
                {'channel': 'email', 'subject': 'S', 'body': 'Hi', 'send_after_days': 0},
                {'channel': 'linkedin_message', 'body': 'Hi', 'send_after_days': 3},
            ],
            'step 2: A LinkedIn message requires an earlier LinkedIn invitation',
        ),
    ],
)
async def test_draft_campaign_refuses_bad_steps_with_a_reason_and_writes_nothing(db, steps, reason):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    _people(db, uid, sid)
    result = await _draft({'name': 'A', 'steps': steps}, uid, sid)
    assert result.ok is False and reason in (result.error or '')
    assert db.execute('SELECT count(*) AS n FROM outreach_campaigns').fetchone()['n'] == 0


async def test_draft_campaign_takes_the_drafts_own_field_tokens(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)
    _people(db, uid, sid)
    news = [{'channel': 'email', 'subject': 'S', 'body': 'Hi {{recentNews}}', 'send_after_days': 0}]
    refused = await _draft({'name': 'A', 'steps': news}, uid, sid)
    assert refused.ok is False and 'recentNews' in (refused.error or '')
    cid = (await _draft({'name': 'A'}, uid, sid)).data['campaign_id']
    field = {'token': 'recentNews', 'source': {'kind': 'column', 'key': 'recent_news'}, 'fallback': None}
    db.execute('UPDATE outreach_campaigns SET fields = %s WHERE id = %s', (json.dumps([field]), cid))
    written = await _draft({'name': 'A', 'campaign_id': cid, 'steps': news}, uid, sid)
    assert written.ok, written.error
    assert written.data['fields'] == ['{{recentNews}}']
    other = [{'channel': 'email', 'subject': 'S', 'body': 'Hi {{city}}', 'send_after_days': 0}]
    again = await _draft({'name': 'A', 'campaign_id': cid, 'steps': other}, uid, sid)
    assert again.ok is False and 'The draft also has the fields {{recentNews}}' in (again.error or '')


async def test_a_rewrite_that_would_drop_copy_b_is_refused_and_changes_nothing(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)
    _people(db, uid, sid)
    cid = (await _draft({'name': 'A', 'steps': SEQUENCE}, uid, sid)).data['campaign_id']
    tested = [_steps(db, cid)[0] | {'b': {'subject': 'Due minuti?', 'body': 'Copia B'}}, _steps(db, cid)[1]]
    db.execute('UPDATE outreach_campaigns SET steps = %s WHERE id = %s', (json.dumps(tested), cid))

    for args in ({'name': 'A', 'campaign_id': cid, 'steps': SEQUENCE[:1]}, {'name': 'A', 'steps': SEQUENCE[:1]}):
        refused = await _draft(args, uid, sid)
        assert refused.ok is False and 'second copy (B)' in (refused.error or ''), args
        assert _steps(db, cid) == tested
    kept = await _draft({'name': 'A', 'campaign_id': cid}, uid, sid)  # no steps: the copy is kept, B too
    assert kept.ok and _steps(db, cid) == tested


async def test_draft_campaign_writes_copy_b_and_a_rewrite_that_passes_b_keeps_the_test(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)
    _people(db, uid, sid)
    b = {'subject': 'Due minuti, {{firstName}}?', 'body': 'Copia B per {{company}}.'}
    tested = [SEQUENCE[0] | {'b': b}, SEQUENCE[1] | {'b': {'body': 'Promemoria B.'}}]
    written = await _draft({'name': 'A', 'steps': tested}, uid, sid)
    assert written.ok, written.error
    cid = written.data['campaign_id']
    assert [s.get('b') for s in _steps(db, cid)] == [b, {'subject': '', 'body': 'Promemoria B.'}]

    rewrite = [SEQUENCE[0] | {'body': 'Nuova copia A.', 'b': b}, SEQUENCE[1]]
    again = await _draft({'name': 'A', 'campaign_id': cid, 'steps': rewrite}, uid, sid)
    assert again.ok, again.error
    assert [(s['body'], s.get('b')) for s in _steps(db, cid)] == [('Nuova copia A.', b), ('Un promemoria.', None)]

    before = _steps(db, cid)
    bad = [SEQUENCE[0] | {'b': {'subject': 'Ciao [Name]', 'body': 'B'}}, SEQUENCE[1]]
    refused = await _draft({'name': 'A', 'campaign_id': cid, 'steps': bad}, uid, sid)
    assert refused.ok is False and 'variant b' in (refused.error or '') and '[Name]' in (refused.error or '')
    assert _steps(db, cid) == before


async def test_draft_campaign_never_rewrites_a_live_campaign_or_another_chats_draft(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    _people(db, uid, sid)
    mine = (await _draft({'name': 'A'}, uid, sid)).data['campaign_id']
    db.execute("UPDATE outreach_campaigns SET status = 'active' WHERE id = %s", (mine,))
    live = await _draft({'campaign_id': mine, 'steps': SEQUENCE}, uid, sid)
    assert live.ok is False and 'active' in live.error and _steps(db, mine) == []

    other_sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id)'
        ' VALUES (default_organization(%s), %s, %s) RETURNING id',
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    _people(db, uid, other_sid, rows=[{'first_name': 'Dan', 'email': 'dan@client.test'}])
    foreign = (await _draft({'name': 'B'}, uid, other_sid)).data['campaign_id']
    mine_before = db.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (mine,)).fetchone()
    result = await _draft({'campaign_id': foreign, 'steps': SEQUENCE}, uid, sid)
    assert result.ok is False and _steps(db, foreign) == []
    assert db.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (mine,)).fetchone() == mine_before


async def test_draft_campaign_targets_the_named_draft_not_the_newest(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    lid = _people(db, uid, sid)
    older = (await _draft({'name': 'Old'}, uid, sid)).data['campaign_id']
    db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, list_id)'
        ' SELECT organization_id, project_id, owner_id, %s, list_id FROM outreach_campaigns WHERE id = %s',
        ('Newest', older),
    )
    result = await _draft({'campaign_id': older, 'steps': SEQUENCE}, uid, sid)
    assert result.ok and result.campaign == {'id': older, 'name': 'Old', 'target_count': 2}
    assert len(_steps(db, older)) == 2 and lid


def test_the_campaign_names_its_list_and_the_chat_that_built_it(db):
    uid = _user(db)
    sid, _ = _job(db, uid, 'x')
    act_in_own_org(db, uid)  # the turn runs as its member
    lid = _people(db, uid, sid)
    sign_in(app, member(db, uid))
    try:
        client = TestClient(app)
        cid = client.post('/v1/campaigns', json={'name': 'A', 'list_id': lid}).json()['id']
        built = client.get(f'/v1/campaigns/{cid}').json()
        db.execute('UPDATE lists SET chat_session_id = NULL WHERE id = %s', (lid,))
        imported = client.get(f'/v1/campaigns/{cid}').json()
    finally:
        app.dependency_overrides.clear()
    assert (built['list_id'], built['chat_session_id']) == (lid, str(sid))
    assert (imported['list_id'], imported['chat_session_id']) == (lid, None)
