"""Bulk row actions and the fit filter of the rows route (#834) against real SQL. Needs TEST_DATABASE_URL."""

import os
from uuid import uuid4

import pytest

from app.lists import bulk_routes, routes
from tests.conftest import project, scoped_conn
from tests.test_campaign_routes_sql import _DB, _as, _list, db  # noqa: F401  (db is the fixture)
from tests.test_holds_sql import stop
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL not set')


@pytest.fixture
def bulk(db, monkeypatch):  # noqa: F811  (the imported fixture, extended)
    for module in (bulk_routes, routes):
        monkeypatch.setattr(module, 'get_conn', scoped_conn(db))
    return db


def _campaign(c, uid, status='draft', pid=None) -> str:
    return str(
        c.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
            " VALUES (default_organization(%s), %s, %s, 'C', %s) RETURNING id",
            (uid, pid or project(c, uid), uid, status),
        ).fetchone()['id']
    )


def _other_project(c, uid) -> str:
    return str(
        c.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'other')"
            ' RETURNING id',
            (uid, uid),
        ).fetchone()['id']
    )


def _ids(c, lid) -> dict[str, str]:
    return {
        r['cells']['first_name']: str(r['id'])
        for r in c.execute('SELECT id, cells FROM list_rows WHERE list_id = %s', (lid,))
    }


def test_add_to_campaign_applies_the_enrollment_rules(bulk):
    c = bulk
    uid = _user(c)
    c.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'blocked.test')",
        (project(c, uid), uid),
    )
    lid = _list(c, uid, rows=[
        {'first_name': 'Anna', 'email': 'anna@client.test'},
        {'first_name': 'Bea', 'email': 'bea@blocked.test'},  # excluded by the project's rules
        {'first_name': 'Cat', 'email': 'cat@client.test'},  # in another active campaign
        {'first_name': 'Dan', 'email': 'dan@client.test'},  # not asked for
        {'first_name': 'Eve', 'email': 'anna@client.test'},  # same person as Anna
        {'first_name': 'Fay'},  # nobody to reach
    ])  # fmt: skip
    other = _campaign(c, uid, 'active')
    c.execute(
        "INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) VALUES (%s, %s, 'cat@client.test', 'active')",
        (other, uid),
    )
    cid = _campaign(c, uid)
    ids = _ids(c, lid)
    ghost = '00000000-0000-0000-0000-000000000001'

    res = _as(uid).post(
        f'/v1/lists/{lid}/rows/add-to-campaign',
        json={'campaign_id': cid, 'row_ids': [ids['Anna'], ids['Bea'], ids['Cat'], ids['Eve'], ids['Fay'], ghost]},
    )

    assert res.status_code == 200, res.text
    assert res.json() == {
        'added': 1,
        'skipped': {'excluded': 1, 'in_other_campaign': 1, 'duplicate': 1, 'no_contact': 1, 'not_found': 1},
    }
    emails = [
        r['email'] for r in c.execute('SELECT email FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,))
    ]
    assert emails == ['anna@client.test']
    again = _as(uid).post(f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [ids['Anna']]})
    assert again.json() == {'added': 0, 'skipped': {'duplicate': 1}}


def test_add_to_campaign_refuses_another_project_and_a_live_campaign(bulk):
    c = bulk
    uid = _user(c)
    lid = _list(c, uid, rows=[{'first_name': 'Anna', 'email': 'anna@client.test'}])
    row = _ids(c, lid)['Anna']
    elsewhere = _campaign(c, uid, pid=_other_project(c, uid))
    client = _as(uid)

    def add(cid):
        return client.post(f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [row]})

    assert add(elsewhere).status_code == 404
    assert add(elsewhere).json()['detail']['code'] == 'CAMPAIGN.NOT_FOUND'
    assert add(_campaign(c, uid, 'active')).json()['detail']['code'] == 'CAMPAIGN.NOT_DRAFT'
    foreign = client.post(
        f'/v1/lists/{lid}/rows/add-to-campaign',
        json={'campaign_id': _campaign(c, uid), 'row_ids': [row]},
        headers={'X-Project-Id': _other_project(c, uid)},
    )  # the list belongs to the default project
    assert foreign.status_code == 404
    assert foreign.json()['detail']['code'] == 'LIST.NOT_FOUND'
    assert c.execute('SELECT count(*) AS n FROM outreach_campaign_targets').fetchone()['n'] == 0


def test_mark_off_target_rates_bad_inside_the_project_only(bulk):
    c = bulk
    uid = _user(c)
    lid = _list(c, uid, rows=[{'first_name': 'Anna'}, {'first_name': 'Bea'}])
    ids = _ids(c, lid)
    theirs = _list(c, uid, rows=[{'first_name': 'Zed'}])
    zed = _ids(c, theirs)['Zed']
    client = _as(uid)

    res = client.post(
        f'/v1/lists/{lid}/rows/mark-off-target', json={'row_ids': [ids['Anna'], zed]}
    )  # a row of another list is not touched

    assert res.json() == {'marked': 1, 'not_found': 1}
    rated = {r['cells']['first_name']: r['rating'] for r in c.execute('SELECT cells, rating FROM list_rows')}
    assert rated == {'Anna': 'bad', 'Bea': None, 'Zed': None}
    moved = client.post(
        f'/v1/lists/{lid}/rows/mark-off-target',
        json={'row_ids': [ids['Bea']]},
        headers={'X-Project-Id': _other_project(c, uid)},
    )
    assert moved.status_code == 404


def test_rate_rows_sets_the_rating_and_a_null_clears_the_note(bulk):
    c = bulk
    uid = _user(c)
    lid = _list(c, uid, rows=[{'first_name': 'Anna'}, {'first_name': 'Bea'}, {'first_name': 'Cleo'}])
    ids = _ids(c, lid)
    c.execute("UPDATE list_rows SET rating = 'bad', rating_note = 'too small' WHERE list_id = %s", (lid,))
    zed = _ids(c, _list(c, uid, rows=[{'first_name': 'Zed'}]))['Zed']
    client = _as(uid)

    cleared = client.post(f'/v1/lists/{lid}/rows/rate', json={'row_ids': [ids['Anna'], zed], 'rating': None})
    good = client.post(f'/v1/lists/{lid}/rows/rate', json={'row_ids': [ids['Bea']], 'rating': 'good'})

    assert cleared.json() == {'rated': 1, 'not_found': 1}  # a row of another list is not touched
    assert good.json() == {'rated': 1, 'not_found': 0}
    rated = {
        r['cells']['first_name']: (r['rating'], r['rating_note'])
        for r in c.execute('SELECT cells, rating, rating_note FROM list_rows')
    }
    assert rated == {
        'Anna': (None, None),
        'Bea': ('good', 'too small'),
        'Cleo': ('bad', 'too small'),
        'Zed': (None, None),
    }


def test_rate_rows_refuses_another_project_and_a_bad_body(bulk):
    c = bulk
    uid = _user(c)
    lid = _list(c, uid, rows=[{'first_name': 'Anna'}])
    anna = _ids(c, lid)['Anna']
    client = _as(uid)
    url = f'/v1/lists/{lid}/rows/rate'

    foreign = client.post(
        url, json={'row_ids': [anna], 'rating': 'good'}, headers={'X-Project-Id': _other_project(c, uid)}
    )
    too_many = client.post(url, json={'row_ids': [str(uuid4()) for _ in range(1001)], 'rating': 'good'})
    no_rating = client.post(url, json={'row_ids': [anna]})

    assert foreign.status_code == 404
    assert foreign.json()['detail']['code'] == 'LIST.NOT_FOUND'
    assert too_many.status_code == 422
    assert no_rating.status_code == 422
    assert c.execute('SELECT rating FROM list_rows WHERE id = %s', (anna,)).fetchone()['rating'] is None


def test_rows_min_fit_filter(bulk):
    c = bulk
    uid = _user(c)
    lid = _list(c, uid, rows=[{'first_name': 'Anna'}, {'first_name': 'Bea'}, {'first_name': 'Cat'}])
    for name, score in (('Anna', 90), ('Bea', 50)):
        c.execute(
            "UPDATE list_rows SET relevance_score = %s WHERE list_id = %s AND cells->>'first_name' = %s",
            (score, lid, name),
        )
    client = _as(uid)

    def names(query):
        res = client.get(f'/v1/lists/{lid}/rows{query}')
        assert res.status_code == 200, res.text
        return [r['cells']['first_name'] for r in res.json()['rows']]

    assert names('') == ['Anna', 'Bea', 'Cat']
    assert names('?min_fit=80') == ['Anna']
    assert names('?min_fit=50') == ['Anna', 'Bea']
    assert client.get(f'/v1/lists/{lid}/rows?min_fit=101').status_code == 422


def _held_person(c, uid, steps: str):
    pid = project(c, uid)
    lid = _list(c, uid, rows=[{'first_name': 'Anna', 'email': 'anna@client.test'}])
    row = _ids(c, lid)['Anna']
    cid = _campaign(c, uid)
    c.execute('UPDATE outreach_campaigns SET steps = %s::jsonb WHERE id = %s', (steps, cid))
    org = c.execute('SELECT default_organization(%s) AS o', (uid,)).fetchone()['o']
    conn = c.execute(
        "INSERT INTO instantly_connections (organization_id, project_id, name, workspace_id) VALUES (%s, %s, 'x', 'ws')"
        ' RETURNING id',
        (org, pid),
    ).fetchone()['id']
    batch = c.execute(
        'INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, workspace_id, campaign_id,'
        " status) VALUES (%s, %s, %s, 'ws', 'camp', 'completed') RETURNING id",
        (org, pid, conn),
    ).fetchone()['id']
    c.execute(
        'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,'
        " outcome) VALUES (%s, %s, %s, 'ws', 'camp', 'anna@client.test', 'accepted')",
        (org, pid, batch),
    )
    return lid, row, cid


def test_add_to_campaign_leaves_out_a_person_instantly_holds_for_an_email_campaign(bulk):
    c = bulk
    uid = _user(c)
    lid, row, cid = _held_person(c, uid, '[{"channel": "email"}]')
    res = _as(uid).post(f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [row]})
    assert res.json() == {'added': 0, 'skipped': {'held_by_instantly': 1}}
    c.execute("UPDATE instantly_leads SET outcome = 'skipped'")
    assert _as(uid).post(
        f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [row]}
    ).json() == {'added': 1, 'skipped': {}}


def test_a_linkedin_only_campaign_keeps_a_person_instantly_holds_for_email(bulk):
    c = bulk
    uid = _user(c)
    lid, row, cid = _held_person(c, uid, '[{"channel": "linkedin_connect"}]')
    res = _as(uid).post(f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [row]})
    assert res.json() == {'added': 1, 'skipped': {}}


@pytest.mark.parametrize('steps', ['[{"channel": "email"}]', '[{"channel": "linkedin_connect"}]'])
def test_add_to_campaign_leaves_out_a_person_being_stopped_on_every_channel(bulk, steps):
    c = bulk
    uid = _user(c)
    lid, row, cid = _held_person(c, uid, steps)
    stop(c, project(c, uid), 'anna@client.test')
    res = _as(uid).post(f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [row]})
    assert res.json() == {'added': 0, 'skipped': {'stopped': 1}}


@pytest.mark.parametrize('elsewhere', ['replied', 'active'])
def test_a_stop_is_named_over_a_reply_or_another_campaign(bulk, elsewhere):
    c = bulk
    uid = _user(c)
    lid, row, cid = _held_person(c, uid, '[{"channel": "email"}]')
    other = _campaign(c, uid, status='active')
    c.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, status)'
        " VALUES (%s, %s, 'anna@client.test', 'Anna', %s)",
        (other, uid, elsewhere),
    )
    stop(c, project(c, uid), 'anna@client.test')
    res = _as(uid).post(f'/v1/lists/{lid}/rows/add-to-campaign', json={'campaign_id': cid, 'row_ids': [row]})
    assert res.json() == {'added': 0, 'skipped': {'stopped': 1}}
