import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.outreach import field_routes, routes
from tests.conftest import hold, member, migrated_database, pool_on, project, scoped_conn, sign_in
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: campaign fields SQL test NOT run')

NEWS = {'token': 'recentNews', 'source': {'kind': 'column', 'key': 'recent_news'}, 'fallback': None}
STEP = {'step': 1, 'channel': 'email', 'subject': 'Ciao {{firstName}}', 'send_after_days': 0}


@pytest.fixture
def db(monkeypatch):
    with migrated_database() as (c, conninfo), pool_on(conninfo):
        try:
            for module in (routes, field_routes):
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


def _list(c, uid, rows, pid=None) -> str:
    lid = c.execute(
        'INSERT INTO lists (organization_id, project_id, user_id, name, entity)'
        " VALUES (default_organization(%s), %s, %s, 'L', 'person') RETURNING id",
        (uid, pid or project(c, uid), uid),
    ).fetchone()['id']
    for n, cells in enumerate(rows):
        c.execute('INSERT INTO list_rows (list_id, cells) VALUES (%s, %s)', (lid, json.dumps(cells)))
        for key in cells:
            c.execute(
                "INSERT INTO list_columns (list_id, key, label, kind, position) VALUES (%s, %s, %s, 'static', %s)"
                ' ON CONFLICT DO NOTHING',
                (lid, key, key.replace('_', ' ').title(), n * 100 + len(key)),
            )
    return str(lid)


def _campaign(c, uid, rows, fields=(NEWS,)) -> tuple[TestClient, str]:
    client = _as(uid)
    cid = client.post('/v1/campaigns', json={'name': 'C', 'list_id': _list(c, uid, rows)}).json()['id']
    body = {'body': 'Hi {{firstName}}, ' + ' '.join(f'{{{{{f["token"]}}}}}' for f in fields)}
    res = client.patch(f'/v1/campaigns/{cid}', json={'fields': list(fields), 'steps': [STEP | body]})
    assert res.status_code == 200, res.text
    return client, cid


def _preview(client, cid) -> dict[str, dict]:
    items = client.get(f'/v1/campaigns/{cid}/preview?limit=10').json()['items']
    return {i['target']['first_name']: i for i in items}


def test_a_field_reads_the_persons_own_visible_cell_and_names_the_missing_ones(db):
    uid = _user(db)
    client, cid = _campaign(
        db,
        uid,
        [
            {'first_name': 'Anna', 'email': 'anna@a.test', 'recent_news': 'a new\n{{office}} in [Milan]'},
            {'first_name': 'Bo', 'email': 'bo@a.test'},
        ],
    )
    people = _preview(client, cid)
    assert people['Anna']['missing'] == []
    assert people['Anna']['steps'][0]['body'] == 'Hi Anna, a new office in Milan'
    assert people['Bo']['missing'] == ['recentNews']
    assert people['Bo']['steps'][0]['error'] == 'OUTREACH.COPY_MISSING_FIELD'


def test_a_fallback_fills_a_person_with_no_value(db):
    uid = _user(db)
    client, cid = _campaign(
        db, uid, [{'first_name': 'Bo', 'email': 'bo@a.test'}], fields=(NEWS | {'fallback': 'your recent growth'},)
    )
    bo = _preview(client, cid)['Bo']
    assert (bo['missing'], bo['steps'][0]['body']) == ([], 'Hi Bo, your recent growth')


def test_the_catalog_offers_visible_non_contact_columns_and_refuses_vendor_and_status_keys(db):
    uid = _user(db)
    rows = [
        {'first_name': 'Anna', 'email': 'anna@a.test', 'email_status': 'verified', 'person_id': 'p1', 'city': 'Rome'}
    ]
    client, cid = _campaign(db, uid, rows, fields=())
    for key in ('email_status', 'email_source', 'person_id', 'source'):
        field = {'token': 'leak', 'source': {'kind': 'column', 'key': key}}
        res = client.patch(f'/v1/campaigns/{cid}', json={'fields': [field]})
        assert (res.status_code, res.json()['detail']['code']) == (422, 'CAMPAIGN.INVALID_FIELDS'), key
    catalog = client.get(f'/v1/campaigns/{cid}/fields').json()
    assert [c['source']['key'] for c in catalog['columns']] == ['city']
    assert catalog['columns'][0] == {
        'token': 'city',
        'source': {'kind': 'column', 'key': 'city'},
        'label': 'City',
        'ai': False,
        'filled': 1,
    }
    assert (catalog['people'], catalog['signal']) == (1, [])
    assert catalog['contact_tokens'] == ['firstName', 'lastName', 'companyName', 'role']


def test_a_row_of_another_project_reads_as_missing(db):
    uid = _user(db)
    client, cid = _campaign(db, uid, [{'first_name': 'Anna', 'email': 'anna@a.test', 'recent_news': 'mine'}])
    other = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Other client') RETURNING id",
        (member(db, uid).org_id, uid),
    ).fetchone()['id']
    theirs = _list(db, uid, [{'first_name': 'X', 'recent_news': 'theirs'}], pid=other)
    db.execute(
        'UPDATE outreach_campaign_targets SET row_id = (SELECT id FROM list_rows WHERE list_id = %s)'
        ' WHERE campaign_id = %s',
        (theirs, cid),
    )
    anna = _preview(client, cid)['Anna']
    assert anna['missing'] == ['recentNews']
    catalog = client.get(f'/v1/campaigns/{cid}/fields').json()
    assert [(c['source']['key'], c['filled']) for c in catalog['columns']] == [('recent_news', 0)]


def test_a_cell_an_unsettled_charge_holds_stays_hidden(db):
    uid = _user(db)
    client, cid = _campaign(db, uid, [{'first_name': 'Anna', 'email': 'anna@a.test', 'recent_news': 'bought'}])
    assert _preview(client, cid)['Anna']['missing'] == []
    row = db.execute('SELECT row_id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchone()
    db.execute(
        "SELECT increment_credits(%s, %s, 10, 'purchase', 'stripe_checkout', 'cs_x')", (member(db, uid).org_id, uid)
    )
    db.execute(
        "INSERT INTO row_charges (hold_id, row_id, keys, credits, state) VALUES (%s, %s, '{recent_news}', 1, 'held')",
        (hold(db, uid), row['row_id']),
    )
    assert _preview(client, cid)['Anna']['missing'] == ['recentNews']
    assert client.get(f'/v1/campaigns/{cid}/fields').json()['columns'][0]['filled'] == 0


def test_a_person_with_no_row_has_only_the_contact_tokens(db):
    uid = _user(db)
    client, cid = _campaign(db, uid, [{'first_name': 'Anna', 'email': 'anna@a.test', 'recent_news': 'x'}])
    db.execute('UPDATE outreach_campaign_targets SET row_id = NULL WHERE campaign_id = %s', (cid,))
    anna = _preview(client, cid)['Anna']
    assert anna['missing'] == ['recentNews']


def test_the_signal_fields_read_the_persons_signal(db):
    uid = _user(db)
    client, cid = _campaign(
        db,
        uid,
        [{'first_name': 'Anna', 'email': 'anna@a.test'}, {'first_name': 'Bo', 'email': 'bo@a.test'}],
        fields=({'token': 'signalTitle', 'source': {'kind': 'signal', 'key': 'title'}, 'fallback': 'a new role'},),
    )
    target = db.execute(
        'SELECT t.id, t.row_id, l.id AS list_id FROM outreach_campaign_targets t JOIN outreach_campaigns c'
        " ON c.id = t.campaign_id JOIN lists l ON l.id = c.list_id WHERE t.campaign_id = %s AND t.first_name = 'Anna'",
        (cid,),
    ).fetchone()
    watchlist = db.execute(
        'INSERT INTO watchlists (user_id, list_id, name, job_titles, cadence, next_run_at)'
        " VALUES (%s, %s, 'W', '{SDR}', 'weekly', now()) RETURNING id",
        (uid, target['list_id']),
    ).fetchone()['id']
    signal = db.execute(
        'INSERT INTO signals (watchlist_id, row_id, type, key, data, score, occurred_at)'
        " VALUES (%s, %s, 'job_posting', 'k', %s, 50, now()) RETURNING id",
        (watchlist, target['row_id'], json.dumps({'title': 'Head of Sales', 'city': ''})),
    ).fetchone()['id']
    db.execute('UPDATE outreach_campaign_targets SET signal_id = %s WHERE id = %s', (signal, target['id']))
    people = _preview(client, cid)
    assert people['Anna']['steps'][0]['body'] == 'Hi Anna, Head of Sales'
    assert people['Bo']['steps'][0]['body'] == 'Hi Bo, a new role'
    signal_entries = client.get(f'/v1/campaigns/{cid}/fields').json()['signal']
    assert [(s['token'], s['filled']) for s in signal_entries] == [
        ('signalTitle', 1),
        ('signalCity', 0),
        ('signalCountry', 0),
        ('signalUrl', 0),
    ]


def test_fields_are_edited_only_in_a_draft_and_checked_with_the_steps(db):
    uid = _user(db)
    client, cid = _campaign(db, uid, [{'first_name': 'Anna', 'email': 'anna@a.test', 'recent_news': 'x'}])
    assert client.get(f'/v1/campaigns/{cid}').json()['fields'] == [NEWS]
    long = NEWS | {'fallback': 'y' * 200}
    subject = {'subject': 'x' * 150 + '{{recentNews}}'}
    res = client.patch(f'/v1/campaigns/{cid}', json={'fields': [long], 'steps': [STEP | subject | {'body': 'b'}]})
    assert (res.status_code, res.json()['detail']['code']) == (422, 'CAMPAIGN.COPY_TOO_LONG')
    dup = client.patch(f'/v1/campaigns/{cid}', json={'fields': [NEWS, NEWS]})
    assert dup.json()['detail']['code'] == 'CAMPAIGN.INVALID_FIELDS'
    db.execute("UPDATE outreach_campaigns SET status = 'paused' WHERE id = %s", (cid,))
    res = client.patch(f'/v1/campaigns/{cid}', json={'fields': []})
    assert (res.status_code, res.json()['detail']['code']) == (409, 'CAMPAIGN.NOT_DRAFT')


def test_the_live_preview_uses_the_typed_fields_and_stores_nothing(db):
    uid = _user(db)
    client, cid = _campaign(db, uid, [{'first_name': 'Bo', 'email': 'bo@a.test'}])
    typed = {'steps': [{'channel': 'email', 'subject': 's', 'body': '{{recentNews}}'}]}
    res = client.post(f'/v1/campaigns/{cid}/preview', json=typed | {'fields': [NEWS | {'fallback': 'typed'}]})
    assert res.json()['items'][0]['steps'][0]['body'] == 'typed'
    saved = client.post(f'/v1/campaigns/{cid}/preview', json=typed).json()['items'][0]
    assert saved['missing'] == ['recentNews']
    assert client.get(f'/v1/campaigns/{cid}').json()['fields'] == [NEWS]


def test_a_required_field_that_no_step_uses_is_not_missing(db):
    uid = _user(db)
    client, cid = _campaign(db, uid, [{'first_name': 'Bo', 'email': 'bo@a.test'}])
    preview = f'/v1/campaigns/{cid}/preview'
    unused = {'channel': 'email', 'subject': 's', 'body': 'Hi {{firstName}}'}
    assert client.post(preview, json={'steps': [unused]}).json()['items'][0]['missing'] == []
    in_subject = unused | {'subject': 'About {{recentNews}}'}
    assert client.post(preview, json={'steps': [unused, in_subject]}).json()['items'][0]['missing'] == ['recentNews']


def test_an_expanded_value_over_a_channel_limit_is_too_long_for_that_person(db):
    uid = _user(db)
    rows = [
        {
            'first_name': 'Anna',
            'email': 'anna@a.test',
            'linkedin_url': 'linkedin.com/in/anna',
            'recent_news': 'n' * 500,
        },
        {'first_name': 'Bo', 'email': 'bo@a.test', 'linkedin_url': 'linkedin.com/in/bo', 'recent_news': 'short'},
    ]
    client, cid = _campaign(db, uid, rows)
    typed = [
        {'channel': 'email', 'subject': 'About {{recentNews}}', 'body': 'b'},
        {'channel': 'linkedin_connect', 'body': 'x' * 250 + ' {{recentNews}}'},
    ]
    people = {
        i['target']['first_name']: i['steps']
        for i in client.post(f'/v1/campaigns/{cid}/preview', json={'steps': typed}).json()['items']
    }
    assert [s['error'] for s in people['Anna']] == ['COPY_TOO_LONG', 'COPY_TOO_LONG']
    assert [s['error'] for s in people['Bo']] == [None, None]
