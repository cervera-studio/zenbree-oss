"""Saved views (#827): the routes, the project scope and the sidebar count."""

from app.home.nav import load_nav
from tests.conftest import member, project
from tests.test_agent import _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_home_overview import _list


@needs_db
def test_a_view_is_saved_renamed_listed_and_deleted(client, db):
    tc, uid = client
    lid = _list(db, uid, 'Agencies')
    made = tc.post('/v1/saved-views', json={'list_id': lid, 'name': ' Hot ', 'tab': 'delivered', 'query': 'milan'})
    assert made.status_code == 201
    view = made.json()
    assert (view['name'], view['list_name'], view['tab'], view['query']) == ('Hot', 'Agencies', 'delivered', 'milan')

    renamed = tc.patch(f'/v1/saved-views/{view["id"]}', json={'name': 'Milan'})
    assert renamed.json()['name'] == 'Milan'
    assert [v['id'] for v in tc.get('/v1/saved-views').json()['views']] == [view['id']]
    nav, _ = load_nav(db, uid, member(db, uid).org_id, project(db, uid))
    assert nav.views == 1

    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (lid,))
    assert tc.get('/v1/saved-views').json()['views'] == []
    assert load_nav(db, uid, member(db, uid).org_id, project(db, uid))[0].views == 0
    db.execute('UPDATE lists SET deleted_at = NULL WHERE id = %s', (lid,))

    assert tc.delete(f'/v1/saved-views/{view["id"]}').status_code == 204
    gone = tc.delete(f'/v1/saved-views/{view["id"]}')
    assert gone.status_code == 404 and gone.json()['detail']['code'] == 'SAVED_VIEW.NOT_FOUND'


@needs_db
def test_a_view_needs_a_list_of_the_active_project(client, db):
    tc, uid = client
    theirs = _list(db, _user(db), 'Theirs')
    res = tc.post('/v1/saved-views', json={'list_id': theirs, 'name': 'x'})
    assert res.status_code == 404 and res.json()['detail']['code'] == 'LIST.NOT_FOUND'
    assert tc.post('/v1/saved-views', json={'list_id': _list(db, uid, 'Mine'), 'name': ' '}).status_code == 422
    assert tc.post('/v1/saved-views', json={'list_id': theirs, 'name': 'x', 'tab': 'nope'}).status_code == 422


@needs_db
def test_another_projects_view_is_not_found_and_stays(client, db):
    tc, uid = client
    project(db, uid)  # the active project exists before the other one
    other = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id',
        (member(db, uid).org_id, uid, 'Other client'),
    ).fetchone()['id']
    lid = _list(db, uid, 'Elsewhere', pid=other)
    vid = db.execute(
        'INSERT INTO saved_views (organization_id, project_id, list_id, name) VALUES (%s, %s, %s, %s) RETURNING id',
        (member(db, uid).org_id, other, lid, 'Theirs'),
    ).fetchone()['id']
    for res in (tc.patch(f'/v1/saved-views/{vid}', json={'name': 'x'}), tc.delete(f'/v1/saved-views/{vid}')):
        assert res.status_code == 404 and res.json()['detail']['code'] == 'SAVED_VIEW.NOT_FOUND'
    assert db.execute('SELECT name FROM saved_views WHERE id = %s', (vid,)).fetchone()['name'] == 'Theirs'


@needs_db
def test_the_cap_ignores_views_of_deleted_lists(client, db, monkeypatch):
    tc, uid = client
    monkeypatch.setattr('app.views.routes.MAX_PER_PROJECT', 1)
    gone = _list(db, uid, 'Gone')
    assert tc.post('/v1/saved-views', json={'list_id': gone, 'name': 'old'}).status_code == 201
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (gone,))
    kept = _list(db, uid, 'Kept')
    assert tc.post('/v1/saved-views', json={'list_id': kept, 'name': 'a'}).status_code == 201
    full = tc.post('/v1/saved-views', json={'list_id': kept, 'name': 'b'})
    assert full.status_code == 409 and full.json()['detail']['code'] == 'SAVED_VIEW.LIMIT'


@needs_db
def test_a_view_keeps_its_fit_floor_or_none(client, db):
    tc, uid = client
    lid = _list(db, uid, 'Agencies')
    hot = tc.post('/v1/saved-views', json={'list_id': lid, 'name': 'Hot', 'min_fit': 80})
    plain = tc.post('/v1/saved-views', json={'list_id': lid, 'name': 'All', 'min_fit': None})
    assert (hot.status_code, plain.status_code) == (201, 201)
    assert (hot.json()['min_fit'], plain.json()['min_fit']) == (80, None)
    listed = {v['name']: v['min_fit'] for v in tc.get('/v1/saved-views').json()['views']}
    assert listed == {'Hot': 80, 'All': None}
    for edge in (0, 100):
        made = tc.post('/v1/saved-views', json={'list_id': lid, 'name': f'Edge {edge}', 'min_fit': edge})
        assert (made.status_code, made.json().get('min_fit')) == (201, edge)
    for bad in (101, -1, False, '80', 80.0, 80.5):
        assert tc.post('/v1/saved-views', json={'list_id': lid, 'name': 'x', 'min_fit': bad}).status_code == 422
