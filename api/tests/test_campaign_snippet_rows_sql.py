import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.outreach import routes, snippet_rows
from tests.conftest import member, migrated_database, pool_on, project, scoped_conn, sign_in
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: snippet rows SQL test NOT run')


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (routes, snippet_rows):
                monkeypatch.setattr(module, 'get_conn', scoped_conn(c))
            monkeypatch.setattr('app.projects.routes.get_conn', scoped_conn(c))
            monkeypatch.setattr(sys.modules[__name__], '_DB', [c])
            yield c
        finally:
            app.dependency_overrides.clear()


_DB: list = []


def _as(uid: str) -> TestClient:
    sign_in(app, member(_DB[0], uid))
    return TestClient(app)


def _list(c, uid, names: list[str]) -> str:
    lid = c.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        " VALUES (default_organization(%s), %s, %s, 'L', 'person') RETURNING id",
        (uid, project(c, uid), uid),
    ).fetchone()['id']
    for name in names:
        cells = {'first_name': name, 'email': f'{name.lower()}@a.test'}
        c.execute('INSERT INTO list_rows (list_id, cells) VALUES (%s, %s)', (lid, json.dumps(cells)))
    return str(lid)


def _row(c, lid: str, name: str) -> str:
    return str(
        c.execute("SELECT id FROM list_rows WHERE list_id = %s AND cells->>'first_name' = %s", (lid, name)).fetchone()[
            'id'
        ]
    )


def test_the_rows_are_the_campaigns_list_rows_its_people_can_still_get_a_step_from(db):
    uid = _user(db)
    client = _as(uid)
    lid = _list(db, uid, ['Anna', 'Bo', 'Cy', 'Di'])
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': lid}).json()['id']
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (_row(db, lid, 'Bo'),))
    db.execute(
        "UPDATE outreach_campaign_targets SET status = 'replied' WHERE campaign_id = %s AND first_name = 'Cy'", (cid,)
    )
    other = _list(db, uid, ['Di'])
    db.execute(
        "UPDATE outreach_campaign_targets SET row_id = %s WHERE campaign_id = %s AND first_name = 'Di'",
        (_row(db, other, 'Di'), cid),
    )

    res = client.get(f'/v1/campaigns/{cid}/snippet-rows')

    assert res.status_code == 200, res.text
    assert res.json() == {'list_id': lid, 'row_ids': [_row(db, lid, 'Anna')], 'total': 1}


def test_a_deleted_list_covers_no_one(db):
    uid = _user(db)
    client = _as(uid)
    lid = _list(db, uid, ['Anna'])
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': lid}).json()['id']
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (lid,))

    res = client.get(f'/v1/campaigns/{cid}/snippet-rows')

    assert res.json() == {'list_id': None, 'row_ids': [], 'total': 0}


def test_another_organization_gets_not_found(db):
    uid = _user(db)
    cid = _as(uid).post('/v1/campaigns', json={'name': 'C', 'list_id': _list(db, uid, ['Anna'])}).json()['id']

    res = _as(_user(db)).get(f'/v1/campaigns/{cid}/snippet-rows')

    assert (res.status_code, res.json()['detail']['code']) == (404, 'CAMPAIGN.NOT_FOUND')


def test_one_run_takes_at_most_the_cap_and_counts_every_row(db):
    uid = _user(db)
    client = _as(uid)
    lid = _list(db, uid, [f'P{i}' for i in range(201)])
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': lid}).json()['id']

    body = client.get(f'/v1/campaigns/{cid}/snippet-rows').json()

    assert (len(body['row_ids']), body['total']) == (200, 201)
