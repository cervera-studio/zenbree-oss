import os
import uuid

import psycopg
import pytest

from app.outreach.drafts import enroll_rows
from app.scheduling.runner import enrollment_context
from tests import test_bulk_rows_sql, test_campaign_routes_sql
from tests.test_bulk_rows_sql import _campaign, _ids
from tests.test_campaign_routes_sql import _as, _list
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL not set')

db, bulk = test_campaign_routes_sql.db, test_bulk_rows_sql.bulk


def linked(c, campaign_id) -> list[tuple[str | None, str | None]]:
    return [
        (r['email'], r['row_email'])
        for r in c.execute(
            "SELECT t.email, r.cells ->> 'email' AS row_email FROM outreach_campaign_targets t"
            ' LEFT JOIN list_rows r ON r.id = t.row_id WHERE t.campaign_id = %s ORDER BY t.email',
            (campaign_id,),
        )
    ]


def test_a_campaign_drafted_from_a_list_links_each_person_to_their_row(db):
    uid = _user(db)
    lid = _list(
        db,
        uid,
        rows=[{'first_name': 'Ada', 'email': 'ada@client.test'}, {'first_name': 'Bo', 'email': 'bo@client.test'}],
    )

    created = _as(uid).post('/v1/campaigns', json={'name': 'Q4', 'list_id': lid})

    assert created.status_code == 201, created.text
    assert linked(db, created.json()['id']) == [
        ('ada@client.test', 'ada@client.test'),
        ('bo@client.test', 'bo@client.test'),
    ]


def test_people_added_from_a_list_link_to_their_row(bulk):
    uid = _user(bulk)
    lid = _list(bulk, uid, rows=[{'first_name': 'Ada', 'email': 'ada@client.test'}])
    cid = _campaign(bulk, uid)

    res = _as(uid).post(
        f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [_ids(bulk, lid)['Ada']]}
    )

    assert res.status_code == 200, res.text
    assert linked(bulk, cid) == [('ada@client.test', 'ada@client.test')]


def test_a_deleted_row_leaves_the_person_in_the_campaign_without_a_link(db):
    uid = _user(db)
    lid = _list(db, uid, rows=[{'first_name': 'Ada', 'email': 'ada@client.test'}])
    cid = _as(uid).post('/v1/campaigns', json={'name': 'Q4', 'list_id': lid}).json()['id']
    before = db.execute(
        'SELECT id, organization_id, row_id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)
    ).fetchone()
    assert before['row_id'] is not None

    db.execute('DELETE FROM list_rows WHERE list_id = %s', (lid,))

    after = db.execute(
        'SELECT id, organization_id, row_id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)
    ).fetchone()
    assert (after['id'], after['organization_id'], after['row_id']) == (before['id'], before['organization_id'], None)


def test_a_person_cannot_link_to_a_row_of_another_organization(db):
    uid, other = _user(db), _user(db)
    lid = _list(db, uid, rows=[{'first_name': 'Ada', 'email': 'ada@client.test'}])
    cid = _as(uid).post('/v1/campaigns', json={'name': 'Q4', 'list_id': lid}).json()['id']
    foreign = _list(db, other, rows=[{'first_name': 'Eve', 'email': 'eve@else.test'}])
    foreign_row = db.execute('SELECT id FROM list_rows WHERE list_id = %s', (foreign,)).fetchone()['id']

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute('UPDATE outreach_campaign_targets SET row_id = %s WHERE campaign_id = %s', (foreign_row, cid))


def test_a_row_deleted_after_the_read_enrolls_the_person_without_a_link(bulk):
    uid = _user(bulk)
    cid = _campaign(bulk, uid)
    campaign = bulk.execute('SELECT * FROM outreach_campaigns WHERE id = %s', (cid,)).fetchone()
    gone = {'id': uuid.uuid4(), 'cells': {'first_name': 'Ada', 'email': 'ada@client.test'}}

    enrolled, _ = enroll_rows(bulk, campaign, uid, [gone], enrollment_context(bulk, campaign), room=10)

    assert enrolled == [str(gone['id'])]
    assert linked(bulk, cid) == [('ada@client.test', None)]
