import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.home.overview import load_overview
from app.main import app
from tests.conftest import member, project, sign_in
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: home overview SQL tests NOT run')


@pytest.fixture
def client(db):
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        yield TestClient(app), uid
    finally:
        app.dependency_overrides.clear()


def _overview(c) -> dict:
    r = c.get('/v1/home/overview')
    assert r.status_code == 200, r.text
    return r.json()


def _list(db, uid, name, entity='person', cells=(), pid=None, created="now() - interval '1 day'"):
    lid = db.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity, created_at)'
        f' VALUES (default_organization(%s), %s, %s, %s, %s, {created}) RETURNING id',
        (uid, pid or project(db, uid), uid, name, entity),
    ).fetchone()['id']
    for i, c in enumerate(cells):
        db.execute(
            "INSERT INTO list_rows (list_id, cells, dedupe_key, created_at) VALUES (%s, %s, %s, now() - interval '1 day')",
            (lid, Jsonb(c), f'k{i}'),
        )
    return str(lid)


def _campaign(db, uid, name, *, status='active', list_id=None, pid=None, steps=(), waiting=0):
    cid = db.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, list_id, steps)'
        ' VALUES (default_organization(%s), %s, %s, %s, %s, %s, %s) RETURNING id',
        (uid, pid or project(db, uid), uid, name, status, list_id, Jsonb(list(steps))),
    ).fetchone()['id']
    for i in range(waiting):
        tid = db.execute(
            "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, %s, 'active')"
            ' RETURNING id',
            (cid, uid, f'p{i}@example.test'),
        ).fetchone()['id']
        db.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient,'
            " idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'x@example.test', %s)",
            (cid, uid, tid, f'{cid}:{tid}:1'),
        )
    return str(cid)


def _job(db, uid, pid, stages, *, list_id=None, done=0, total=0):
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id, title)'
        " VALUES ((SELECT organization_id FROM projects WHERE id = %s), %s, %s, 'Find agencies') RETURNING id",
        (pid, pid, uid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO jobs (organization_id, user_id, chat_session_id, status, stages, list_id, progress_done, progress_total)'
        " VALUES ((SELECT organization_id FROM projects WHERE id = %s), %s, %s, 'running', %s, %s, %s, %s)",
        (pid, uid, sid, Jsonb(stages), list_id, done, total),
    )
    return str(sid)


def _reply(db, uid, name, label='interested', campaign='Camp'):
    db.execute(
        'INSERT INTO notifications (user_id, organization_id, kind, href, data)'
        " VALUES (%s, default_organization(%s), 'reply_urgent', '/inbox', %s)",
        (uid, uid, Jsonb({'name': name, 'label': label, 'campaign': campaign})),
    )


def test_empty_project(client):
    c, _ = client
    body = _overview(c)
    assert body['approvals'] == {'total': 0, 'campaigns': []}
    assert body['replies'] == {'count': 0, 'latest': None}
    assert body['running'] == [] and body['lists'] == [] and body['campaigns'] == []
    assert body['campaign_hint'] is None


def test_another_project_never_appears(client, db):
    c, uid = client
    other = _user(db)
    other_pid = project(db, other)
    _list(db, other, 'Theirs')
    _campaign(db, other, 'Their campaign', waiting=2)
    _job(db, other, other_pid, [{'tool': 'search_people', 'units': 1, 'done': 0}])
    mine = project(db, uid)
    _list(db, uid, 'Mine', pid=mine)
    second = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, %s) RETURNING id',
        (uid, uid, 'Other client'),
    ).fetchone()['id']
    _list(db, uid, 'Other client list', pid=second)
    _campaign(db, uid, 'Other client campaign', pid=second, waiting=1)
    body = _overview(c)
    assert [lst['name'] for lst in body['lists']] == ['Mine']
    assert body['campaigns'] == [] and body['running'] == []
    assert body['approvals']['total'] == 0


def test_replies_are_the_callers_own(client, db):
    c, uid = client
    _reply(db, _user(db), 'Not mine')
    _reply(db, uid, 'Giulia Rossi', label='meeting_request')
    body = _overview(c)['replies']
    assert body['count'] == 1
    assert (body['latest']['name'], body['latest']['campaign'], body['latest']['intent']) == (
        'Giulia Rossi',
        'Camp',
        'meeting_request',
    )


def test_reply_intent_only_for_high_intent_labels(client, db):
    c, uid = client
    _reply(db, uid, 'Marco Neri', label='not_interested')
    assert _overview(c)['replies']['latest']['intent'] is None


def test_a_forged_meeting_request_shows_no_intent(client, db):
    c, uid = client
    db.execute(
        'INSERT INTO notifications (user_id, organization_id, kind, href, data)'
        " VALUES (%s, default_organization(%s), 'reply', '/inbox', %s)",
        (uid, uid, Jsonb({'name': 'Zed', 'label': 'meeting_request', 'unverified': True})),
    )
    assert _overview(c)['replies']['latest']['intent'] is None


def test_approvals_total_and_top_two_campaigns(client, db):
    c, uid = client
    _campaign(db, uid, 'A', waiting=3)
    _campaign(db, uid, 'B', waiting=2)
    _campaign(db, uid, 'C', waiting=1)
    _campaign(db, uid, 'D', waiting=0)
    body = _overview(c)['approvals']
    assert body['total'] == 6
    assert [(x['name'], x['count']) for x in body['campaigns']] == [('A', 3), ('B', 2)]


def test_replies_of_another_organization_are_not_counted(client, db):
    c, uid = client
    other_org = db.execute(
        'INSERT INTO organization (name, slug, "createdAt") VALUES (%s, %s, now()) RETURNING id', ('Other', 'other-org')
    ).fetchone()['id']
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, \'member\', now())',
        (other_org, uid),
    )
    db.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href, data) VALUES (%s, %s, 'reply', '/inbox', %s)",
        (uid, other_org, Jsonb({'name': 'Elsewhere'})),
    )
    _reply(db, uid, 'Here')
    body = _overview(c)['replies']
    assert body['count'] == 1 and body['latest']['name'] == 'Here'
    assert {i['kind'] for i in c.get('/v1/home/todo').json()['items']} == {'replies'}
    assert c.get('/v1/home/todo').json()['items'][0]['count'] == 1


def test_todo_replies_title_is_stripped(client, db):
    c, uid = client
    _reply(db, uid, '   ')
    [item] = c.get('/v1/home/todo').json()['items']
    assert item['kind'] == 'replies' and item['title'] is None


def test_running_entity_follows_the_list_else_none(client, db):
    c, uid = client
    pid = project(db, uid)
    lid = _list(db, uid, 'Firms', entity='company')
    _job(db, uid, pid, [{'tool': 'search_people', 'units': 1, 'done': 0}], list_id=lid)
    runs = _overview(c)['running']
    assert [r['entity'] for r in runs] == ['company']
    db.execute('UPDATE jobs SET list_id = NULL')
    assert [r['entity'] for r in _overview(c)['running']] == [None]


def test_running_stages_rows_and_teammate(client, db):
    c, uid = client
    pid = project(db, uid)
    mate = _user(db)
    db.execute('UPDATE "user" SET name = %s WHERE id = %s', ('Marta', mate))
    db.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (default_organization(%s), %s, \'member\', now())',
        (uid, mate),
    )
    lid = _list(db, uid, 'Building', cells=[{'full_name': 'A'}, {'full_name': 'B'}])
    stages = [
        {'tool': 'search_people', 'units': 10, 'done': 10},
        {'tool': 'enrich_email', 'units': 10, 'done': 4},
        {'tool': 'score', 'units': 2, 'done': 0},
    ]
    sid = _job(db, mate, pid, stages, list_id=lid, done=14, total=22)
    [run] = _overview(c)['running']
    assert run['session_id'] == sid and run['title'] == 'Find agencies'
    assert (run['stages_done'], run['stages_total'], run['stage_tool']) == (1, 3, 'enrich_email')
    assert (run['progress_done'], run['progress_total'], run['rows']) == (14, 22, 2)
    assert run['started_by']['name'] == 'Marta'


def test_stale_and_finished_jobs_are_not_running(client, db):
    c, uid = client
    pid = project(db, uid)
    _job(db, uid, pid, [])
    db.execute("UPDATE jobs SET updated_at = now() - interval '2 hours'")
    assert _overview(c)['running'] == []


def test_lists_newest_activity_first_and_verified_emails(client, db):
    c, uid = client
    people = _list(
        db,
        uid,
        'People',
        cells=[
            {'email': 'a@acme.it', 'email_status': 'verified'},
            {'email': 'a@acme.it', 'email_status': 'catch_all'},
            {'email': 'a@acme.it', 'email_status': 'verified'},
        ],
    )
    _list(db, uid, 'Firms', entity='company', cells=[{'name': 'Acme'}], created="now() - interval '3 days'")
    db.execute("UPDATE list_rows SET created_at = now() WHERE list_id = %s AND dedupe_key = 'k0'", (people,))
    db.execute("UPDATE list_rows SET excluded = true WHERE list_id = %s AND dedupe_key = 'k2'", (people,))
    lists = _overview(c)['lists']
    assert [x['name'] for x in lists] == ['People', 'Firms']
    assert (lists[0]['rows'], lists[0]['verified_emails']) == (2, 1)
    assert (lists[1]['rows'], lists[1]['verified_emails']) == (1, None)
    assert lists[0]['owner']['id'] == uid


def test_lists_are_capped_at_five(client, db):
    c, uid = client
    for i in range(7):
        _list(db, uid, f'L{i}')
    assert len(_overview(c)['lists']) == 5


def test_campaigns_stats_channels_and_status_filter(client, db):
    c, uid = client
    steps = [{'channel': 'email'}, {'channel': 'linkedin_connect'}, {'channel': 'email'}]
    cid = _campaign(db, uid, 'Live', steps=steps, waiting=0)
    _campaign(db, uid, 'Paused', status='paused')
    _campaign(db, uid, 'Draft', status='draft')
    _campaign(db, uid, 'Done', status='completed')
    tid = db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, replied_at)'
        " VALUES (%s, %s, 'r@example.test', 'replied', now()) RETURNING id",
        (cid, uid),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, status,'
        " delivered_at, idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'r@example.test', 'sent', now(), %s)",
        (cid, uid, tid, f'{cid}:{tid}:1'),
    )
    camps = {x['name']: x for x in _overview(c)['campaigns']}
    assert set(camps) == {'Live', 'Paused'}
    assert (camps['Live']['sent'], camps['Live']['replies']) == (1, 1)
    assert camps['Live']['channels'] == ['email', 'linkedin']
    assert camps['Paused']['status'] == 'paused'


def test_campaign_hint_is_the_newest_person_list_with_verified_emails_and_no_campaign(client, db):
    c, uid = client
    ok = [{'email': 'a@acme.it', 'email_status': 'verified'}]
    older = _list(db, uid, 'Older', cells=ok, created="now() - interval '5 days'")
    used = _list(db, uid, 'Used', cells=ok)
    _list(db, uid, 'No verified', cells=[{'email': 'a@acme.it', 'email_status': 'guessed'}])
    _list(db, uid, 'Companies', entity='company', cells=[{'email': 'a@acme.it', 'email_status': 'verified'}])
    _campaign(db, uid, 'Draft on Used', status='draft', list_id=used)
    hint = _overview(c)['campaign_hint']
    assert (hint['list_id'], hint['name'], hint['verified_emails']) == (older, 'Older', 1)


def test_no_hint_once_a_campaign_is_live(client, db):
    c, uid = client
    _list(db, uid, 'Ready', cells=[{'email': 'a@acme.it', 'email_status': 'verified'}])
    _campaign(db, uid, 'Live')
    assert _overview(c)['campaign_hint'] is None


def _job_id(db, session_id) -> str:
    return str(db.execute('SELECT id FROM jobs WHERE chat_session_id = %s', (session_id,)).fetchone()['id'])


def test_a_run_names_its_job_and_whether_it_created_its_list(client, db):
    c, uid = client
    pid = project(db, uid)
    older = _list(db, uid, 'Enriched', cells=[{}])
    enriching = _job(db, uid, pid, [], list_id=older)
    db.execute("UPDATE jobs SET created_at = now() - interval '1 minute', status = 'succeeded', finished_at = now()")
    built = _list(db, uid, 'Built', created='now()')
    building = _job(db, uid, pid, [], list_id=built)
    db.execute(
        "UPDATE jobs SET created_at = now() - interval '1 minute' WHERE chat_session_id = %s AND list_id = %s",
        (building, built),
    )
    body = _overview(c)
    [run] = body['running']
    assert (run['job_id'], run['list_id'], run['new_list']) == (_job_id(db, building), built, True)
    [done] = body['finished']
    assert (done['job_id'], done['list_id'], done['new_list']) == (_job_id(db, enriching), older, False)
    assert {lst['id'] for lst in body['lists']} == {older, built}


def test_finished_holds_the_runs_that_succeeded_in_the_last_two_minutes(client, db):
    c, uid = client
    pid = project(db, uid)
    lid = _list(
        db,
        uid,
        'People',
        cells=[{'email': 'a@acme.it', 'email_status': 'verified'}, {'email': 'a@acme.it', 'email_status': 'catch_all'}],
    )
    stages = [{'tool': 'search_people', 'units': 1, 'done': 1}, {'tool': 'enrich_email', 'units': 1, 'done': 1}]
    sid = _job(db, uid, pid, stages, list_id=lid)
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now() - interval '1 minute'")
    for status, ago in [('succeeded', '3 minutes'), ('failed', '0'), ('cancelled', '0'), ('needs_input', '0')]:
        other = _user(db)
        db.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt")'
            " VALUES (default_organization(%s), %s, 'member', now())",
            (uid, other),
        )
        _job(db, other, pid, stages, list_id=lid)
        db.execute(
            f"UPDATE jobs SET status = %s, finished_at = now() - interval '{ago}' WHERE user_id = %s", (status, other)
        )
    [done] = _overview(c)['finished']
    assert done == {
        'job_id': _job_id(db, sid),
        'session_id': sid,
        'title': 'Find agencies',
        'entity': 'person',
        'list_id': lid,
        'new_list': False,
        'rows': 2,
        'verified_emails': 1,
        'stages_total': 2,
    }


FAILED_CALL = {'id': 't1', 'name': 'search_people', 'verdict': 'failed'}
HIDDEN_FAILED_CALL = {'id': 't0', 'name': 'ask_user', 'verdict': 'failed'}
STAGE_FAILED = [{'tool': 'search_people', 'units': 20, 'done': 0, 'error': {'code': 'failed'}}]


def _ended_searches(db, uid, pid) -> dict[str, str]:
    """One succeeded turn per case, as the loop ends it: the list id of each by case name. ``failed_*`` lists hold
    rows from an earlier run, so only the job tells that this turn failed."""
    cases = {
        'good': ([{}], None, None),
        'good_hidden_call': (
            [{}],
            [],
            [HIDDEN_FAILED_CALL, {'id': 't2', 'name': 'search_people', 'verdict': 'success'}],
        ),
        'empty': ([], [], []),
        'failed_stage': ([{}], STAGE_FAILED, []),
        'failed_call': ([{}], [], [FAILED_CALL]),
    }
    lists = {}
    for name, (cells, stages, tool_calls) in cases.items():
        lid = lists[name] = _list(db, uid, name, cells=cells, pid=pid)
        sid = _job(db, uid, pid, [], list_id=lid)
        result = None
        if tool_calls is not None:
            mid = db.execute(
                "INSERT INTO chat_messages (session_id, user_id, role, content, tool_calls) VALUES (%s, %s, 'assistant',"
                " '', %s) RETURNING id",
                (sid, uid, Jsonb(tool_calls)),
            ).fetchone()['id']
            result = Jsonb({'message_id': str(mid)})
        db.execute(
            "UPDATE jobs SET status = 'succeeded', finished_at = now(), stages = %s, result = %s"
            ' WHERE chat_session_id = %s',
            (Jsonb(stages) if stages is not None else None, result, sid),
        )
    return lists


def test_finished_is_a_succeeded_search_with_rows_and_no_failed_step(client, db):
    c, uid = client
    lists = _ended_searches(db, uid, project(db, uid))
    finished = {f['list_id'] for f in _overview(c)['finished']}
    assert finished == {lists['good'], lists['good_hidden_call']}


def test_a_chat_turn_that_wrote_no_list_never_finishes(client, db):
    c, uid = client
    _job(db, uid, project(db, uid), [])
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now()")
    assert _overview(c)['finished'] == []


def test_refresh_at_is_when_a_run_turns_stale_or_a_finish_leaves(client, db):
    c, uid = client
    pid = project(db, uid)
    user = member(db, uid)
    assert load_overview(db, uid, user.org_id, pid).refresh_at is None
    _job(db, uid, pid, [], list_id=_list(db, uid, 'People', cells=[{}]))
    db.execute("UPDATE jobs SET updated_at = now() - interval '5 minutes'")
    db_now = db.execute('SELECT now() AS t').fetchone()['t']
    stale = load_overview(db, uid, user.org_id, pid).refresh_at
    assert stale is not None and abs((stale - db_now).total_seconds() - 15 * 60) < 5
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now() - interval '30 seconds'")
    leaves = load_overview(db, uid, user.org_id, pid).refresh_at
    assert leaves is not None and abs((leaves - db_now).total_seconds() - 90) < 5
    assert 'refresh_at' not in _overview(c)
