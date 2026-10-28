import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.main import app
from tests.conftest import member, project, sign_in
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db
from tests.test_home_overview import _campaign, _ended_searches, _job, _list

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: home stats SQL tests NOT run')


@pytest.fixture
def client(db):
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        yield TestClient(app), uid
    finally:
        app.dependency_overrides.clear()


def _stats(c) -> dict:
    r = c.get('/v1/home/stats')
    assert r.status_code == 200, r.text
    return r.json()


def _row(db, lid, key, cells, ago):
    db.execute(
        f"INSERT INTO list_rows (list_id, cells, dedupe_key, created_at) VALUES (%s, %s, %s, now() - interval '{ago}')",
        (lid, Jsonb(cells), key),
    )


def _sent(db, uid, cid, n, ago, recipient):
    tid = db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email) VALUES (%s, %s, %s)'
        ' ON CONFLICT (campaign_id, lower(btrim(email))) WHERE coalesce(btrim(email), %s) <> %s'
        ' DO UPDATE SET email = excluded.email RETURNING id',
        (cid, uid, recipient, '', ''),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient, status,'
        f" idempotency_key, delivered_at) VALUES (%s, %s, %s, %s, 'email', %s, 'sent', %s, now() - interval '{ago}')",
        (cid, uid, tid, n, recipient, f'{cid}:{tid}:{n}'),
    )


def _inbound(db, uid, pid, label, ago, n, kind='reply', person=None):
    db.execute(
        'INSERT INTO outreach_inbound_messages (organization_id, project_id, owner_id, channel, kind,'
        ' provider_message_id, from_name, from_address, label, received_at) VALUES'
        f" (default_organization(%s), %s, %s, 'email', %s, %s, 'Giulia', %s, %s, now() - interval '{ago}')",
        (uid, pid, uid, kind, f'm{n}-{ago}-{label}', person or f'p{n}@x.test', label),
    )


def test_empty_project(client):
    c, _ = client
    body = _stats(c)
    assert all(k == {'value': 0, 'previous': 0} for k in body['kpis'].values())
    assert len(body['weeks']) == 8
    assert all(w['messages_sent'] == 0 and w['replies'] == 0 for w in body['weeks'])
    assert body['funnel'] == dict.fromkeys(['leads', 'contacts', 'contacted', 'replied', 'positive'], 0)
    assert body['activity'] == []


def test_counts_windows_and_funnel(client, db):
    c, uid = client
    pid = project(db, uid)
    lid = _list(db, uid, 'People', pid=pid)
    _row(db, lid, 'a', {'email': 'a@x.test'}, '2 days')
    _row(db, lid, 'b', {'phone': '+39123'}, '3 days')
    _row(db, lid, 'c', {'name': 'no contact'}, '3 days')
    _row(db, lid, 'old', {'email': 'o@x.test'}, '40 days')
    _row(db, lid, 'ancient', {'email': 'z@x.test'}, '90 days')
    cid = _campaign(db, uid, 'Live', list_id=lid, pid=pid)
    _sent(db, uid, cid, 1, '1 day', 'a@x.test')
    _sent(db, uid, cid, 2, '1 hour', 'a@x.test')
    _sent(db, uid, cid, 1, '45 days', 'o@x.test')
    _inbound(db, uid, pid, 'interested', '1 day', 1)
    _inbound(db, uid, pid, 'not_now', '2 days', 2)
    _inbound(db, uid, pid, 'meeting_request', '40 days', 3)
    _inbound(db, uid, pid, 'interested', '1 day', 4, kind='auto_reply')
    body = _stats(c)
    assert body['kpis'] == {
        'leads': {'value': 3, 'previous': 1},
        'contacts': {'value': 2, 'previous': 1},
        'messages_sent': {'value': 2, 'previous': 1},
        'replies': {'value': 2, 'previous': 1},
        'positive_replies': {'value': 1, 'previous': 1},
    }
    assert body['funnel'] == {'leads': 3, 'contacts': 2, 'contacted': 1, 'replied': 2, 'positive': 1}
    assert sum(w['messages_sent'] for w in body['weeks']) == 3
    assert sum(w['replies'] for w in body['weeks']) == 3
    assert body['weeks'][-1]['messages_sent'] + body['weeks'][-2]['messages_sent'] == 2


def test_activity_is_merged_newest_first_and_capped(client, db):
    c, uid = client
    pid = project(db, uid)
    lid = _list(db, uid, 'People', pid=pid, cells=[{}])
    sid = _job(db, uid, pid, [], list_id=lid)
    db.execute(
        "UPDATE jobs SET status = 'succeeded', finished_at = now() - interval '3 hours' WHERE chat_session_id = %s",
        (sid,),
    )
    cid = _campaign(db, uid, 'Live', list_id=lid, pid=pid)
    db.execute("UPDATE outreach_campaigns SET activated_at = now() - interval '2 hours' WHERE id = %s", (cid,))
    _inbound(db, uid, pid, 'interested', '1 hour', 1)
    got = _stats(c)['activity']
    assert [(a['kind'], a['title'], a['link']) for a in got] == [
        ('reply_received', 'Giulia', '/inbox'),
        ('campaign_started', 'Live', f'/campaigns/{cid}'),
        ('search_finished', 'Find agencies', f'/lists/{lid}'),
    ]
    for i in range(12):
        _inbound(db, uid, pid, 'other', f'{i + 2} minutes', 10 + i)
    assert len(_stats(c)['activity']) == 10


def test_a_search_that_found_no_row_is_not_a_finished_search(client, db):
    c, uid = client
    pid = project(db, uid)
    lid = _list(db, uid, 'Planned', pid=pid)
    sid = _job(db, uid, pid, [], list_id=lid)
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE chat_session_id = %s", (sid,))
    assert _stats(c)['activity'] == []
    _row(db, lid, 'a', {'email': 'a@x.test'}, '1 minute')
    assert [a['kind'] for a in _stats(c)['activity']] == ['search_finished']


def test_a_search_with_a_failed_step_is_not_a_finished_search(client, db):
    c, uid = client
    lists = _ended_searches(db, uid, project(db, uid))
    links = {a['link'] for a in _stats(c)['activity'] if a['kind'] == 'search_finished'}
    assert links == {f'/lists/{lists["good"]}', f'/lists/{lists["good_hidden_call"]}'}


def test_another_project_never_counts(client, db):
    c, uid = client
    other = _user(db)
    opid = project(db, other)
    olid = _list(db, other, 'Theirs', pid=opid)
    _row(db, olid, 'x', {'email': 'x@x.test'}, '1 day')
    ocid = _campaign(db, other, 'Theirs', list_id=olid, pid=opid)
    _sent(db, other, ocid, 1, '1 day', 'x@x.test')
    _inbound(db, other, opid, 'interested', '1 day', 1)
    mine = project(db, uid)
    second = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, %s) RETURNING id',
        (uid, uid, 'Other client'),
    ).fetchone()['id']
    slid = _list(db, uid, 'Sibling', pid=second)
    _row(db, slid, 'y', {'email': 'y@x.test'}, '1 day')
    _inbound(db, uid, second, 'interested', '1 day', 2)
    body = _stats(c)
    assert all(k == {'value': 0, 'previous': 0} for k in body['kpis'].values())
    assert body['activity'] == [] and mine != second


def test_funnel_counts_people_not_messages(client, db):
    c, uid = client
    pid = project(db, uid)
    _inbound(db, uid, pid, 'interested', '1 day', 1, person='Same@x.test')
    _inbound(db, uid, pid, 'interested', '2 days', 2, person='same@x.test')
    body = _stats(c)
    assert body['kpis']['replies']['value'] == 2 and body['kpis']['positive_replies']['value'] == 2
    assert body['funnel']['replied'] == 1 and body['funnel']['positive'] == 1


def test_deleted_lists_and_excluded_rows_stay_out(client, db):
    c, uid = client
    pid = project(db, uid)
    gone = _list(db, uid, 'Gone', pid=pid)
    _row(db, gone, 'g', {'email': 'g@x.test'}, '1 day')
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (gone,))
    kept = _list(db, uid, 'Kept', pid=pid)
    _row(db, kept, 'k', {'email': 'k@x.test'}, '1 day')
    db.execute('UPDATE list_rows SET excluded = true WHERE list_id = %s', (kept,))
    sid = _job(db, uid, pid, [], list_id=gone)
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE chat_session_id = %s", (sid,))
    nolist = _job(db, uid, pid, [])
    db.execute("UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE chat_session_id = %s", (nolist,))
    body = _stats(c)
    assert body['kpis']['leads']['value'] == 0 and body['kpis']['contacts']['value'] == 0
    assert body['activity'] == []
