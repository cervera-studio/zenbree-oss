from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb

from app.home import routes
from app.home.nav import load_nav
from tests.conftest import as_request, member, project
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db
from tests.test_home_overview import _campaign, _list, _reply
from tests.test_home_stream import Stream, listener_attached

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: sidebar nav SQL tests NOT run')


def _nav(db, uid):
    pid = project(db, uid)
    return load_nav(db, uid, member(db, uid).org_id, pid)


def _session(
    db, uid, title, job=None, *, question=None, updated='now()', done=0, total=0, stages=None, tool_calls=None
):
    """A chat of uid's project whose latest job is ``job``, started by a fresh user: one active job per user."""
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id, title)'
        ' VALUES (default_organization(%s), %s, %s, %s) RETURNING id',
        (uid, project(db, uid), uid, title),
    ).fetchone()['id']
    if job:
        mid = db.execute(
            'INSERT INTO chat_messages (session_id, user_id, role, content, question, tool_calls)'
            " VALUES (%s, %s, 'assistant', '', %s, %s) RETURNING id",
            (sid, uid, Jsonb(question) if question else None, Jsonb(tool_calls) if tool_calls is not None else None),
        ).fetchone()['id']
        db.execute(
            'INSERT INTO jobs (organization_id, user_id, chat_session_id, status, result, progress_done, progress_total,'
            ' stages, updated_at) VALUES ((SELECT organization_id FROM chat_sessions WHERE id = %s), %s, %s, %s, %s, %s,'
            f' %s, %s, {updated})',
            (sid, _user(db), sid, job, Jsonb({'message_id': str(mid)}), done, total, Jsonb(stages) if stages else None),
        )
    return str(sid)


def test_counts_skip_deleted_lists_and_other_projects(db):
    uid = _user(db)
    other = _user(db)
    _list(db, uid, 'Kept', cells=[{}])
    gone = _list(db, uid, 'Gone', cells=[{}])
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (gone,))
    _list(db, other, 'Theirs', cells=[{}])
    _campaign(db, uid, 'Draft', status='draft', waiting=2)
    _campaign(db, uid, 'Live')
    _reply(db, uid, 'Ada')
    _reply(db, other, 'Bob')
    nav, _ = _nav(db, uid)
    assert (nav.lists, nav.campaigns, nav.approvals, nav.replies) == (1, 2, 2, 1)


def test_each_chat_has_the_status_of_its_latest_job(db):
    uid = _user(db)
    _session(db, uid, 'none')
    _session(db, uid, 'running', 'running', done=3, total=10)
    _session(db, uid, 'stale', 'running', updated="now() - interval '1 hour'")
    _session(db, uid, 'failed', 'failed')
    _session(db, uid, 'asking', 'needs_input', question={'question': 'Which city?', 'options': []})
    _session(db, uid, 'plan', 'needs_input', question={'question': '', 'options': [], 'plan': {}})
    _session(db, uid, 'done', 'succeeded')
    _session(db, uid, 'stopped', 'cancelled')
    nav, stale_at = _nav(db, uid)
    status = {s.title: s.status for s in nav.sessions}
    assert status == {
        'none': None,
        'running': 'running',
        'stale': 'error',
        'failed': 'error',
        'asking': 'asking',
        'plan': 'plan',
        'done': 'done',
        'stopped': 'done',
    }
    running = next(s for s in nav.sessions if s.title == 'running')
    assert (running.progress_done, running.progress_total) == (3, 10)
    assert stale_at is not None
    assert timedelta(minutes=19) < stale_at - running.updated_at < timedelta(minutes=21)


SEARCH_FAILED = {'id': 't1', 'name': 'search_people', 'verdict': 'failed'}
STAGE_FAILED = [{'tool': 'search_people', 'units': 20, 'done': 0, 'error': {'code': 'failed'}}]


def test_a_finished_turn_with_a_failed_step_is_an_error_like_the_chat_header(db):
    uid = _user(db)
    _session(db, uid, 'stage', 'succeeded', stages=STAGE_FAILED)
    _session(db, uid, 'search', 'succeeded', tool_calls=[{'name': 'propose_plan', 'verdict': 'success'}, SEARCH_FAILED])
    _session(db, uid, 'stopped', 'cancelled', tool_calls=[SEARCH_FAILED])
    _session(db, uid, 'brief only', 'succeeded', tool_calls=[{'name': 'set_brief', 'verdict': 'failed'}])
    _session(db, uid, 'no failed step', 'succeeded', stages=[{'tool': 'search_people', 'units': 20, 'done': 20}])
    _session(db, uid, 'odd shape', 'succeeded', tool_calls={'name': 'search_people', 'verdict': 'failed'})
    plan = {'question': '', 'options': [], 'plan': {}}
    _session(db, uid, 'plan', 'needs_input', question=plan, stages=STAGE_FAILED, tool_calls=[SEARCH_FAILED])
    status = {s.title: s.status for s in _nav(db, uid)[0].sessions}
    assert status == {
        'stage': 'error',
        'search': 'error',
        'stopped': 'error',
        'brief only': 'done',
        'no failed step': 'done',
        'odd shape': 'done',
        'plan': 'plan',
    }


def test_a_running_or_asking_turn_keeps_its_status_with_a_failed_step(db):
    uid = _user(db)
    _session(db, uid, 'running', 'running', stages=STAGE_FAILED, tool_calls=[SEARCH_FAILED])
    question = {'question': 'Which city?', 'options': []}
    _session(db, uid, 'asking', 'needs_input', question=question, stages=STAGE_FAILED, tool_calls=[SEARCH_FAILED])
    status = {s.title: s.status for s in _nav(db, uid)[0].sessions}
    assert status == {'running': 'running', 'asking': 'asking'}


def test_lists_count_only_lists_with_rows(db):
    uid = _user(db)
    _list(db, uid, 'Failed search')
    assert _nav(db, uid)[0].lists == 0
    _list(db, uid, 'Found', cells=[{}])
    assert _nav(db, uid)[0].lists == 1


def test_meta_counts_the_chats_live_lists_and_their_rows(db):
    uid = _user(db)
    sid = _session(db, uid, 'Agencies', 'succeeded')
    people = _list(db, uid, 'People', cells=[{}, {}, {}])
    companies = _list(db, uid, 'Companies', entity='company', cells=[{}, {}])
    gone = _list(db, uid, 'Gone', cells=[{}])
    db.execute('UPDATE lists SET chat_session_id = %s WHERE id = ANY(%s)', (sid, [people, companies, gone]))
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (gone,))
    db.execute("UPDATE list_rows SET excluded = true WHERE list_id = %s AND dedupe_key = 'k0'", (people,))
    [s] = _nav(db, uid)[0].sessions
    assert (s.list_count, s.people, s.companies) == (2, 2, 2)


async def test_the_stream_sends_nav_when_a_job_finishes(db):
    uid = _user(db)
    sid = _session(db, uid, 'Agencies', 'running')
    user = member(db, uid)
    with as_request(user):
        s = Stream(await routes.nav_stream(user=user, project_id=project(db, uid)))
    try:
        event, body = await s.next()
        assert (event, body['sessions'][0]['status']) == ('snapshot', 'running')
        await listener_attached(db)
        db.execute("UPDATE jobs SET status = 'succeeded' WHERE chat_session_id = %s", (sid,))
        event, body = await s.next()
        assert (event, body['sessions'][0]['status']) == ('nav', 'done')
        _list(db, uid, 'New', cells=[{}])
        event, body = await s.next()
        assert (event, body['lists']) == ('nav', 1)
    finally:
        await s.close()


def test_the_200_newest_chats_pinned_first(db):
    uid = _user(db)
    db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id, title, updated_at)'
        " SELECT default_organization(%s), %s, %s, 'c' || g, now() - g * interval '1 minute'"
        ' FROM generate_series(1, 201) g',
        (uid, project(db, uid), uid),
    )
    db.execute("UPDATE chat_sessions SET pinned_at = now() WHERE title = 'c201'")
    titles = [s.title for s in _nav(db, uid)[0].sessions]
    assert (len(titles), titles[:3], titles[-1]) == (200, ['c201', 'c1', 'c2'], 'c199')
