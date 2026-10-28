"""Per-user exclusions: matching rules, the API, and the search/enrichment paths that honour them."""

import json

from app.agent.tools.handlers import enrich_email
from app.exclusions.rules import guess_kind, is_excluded, normalise
from app.lists.rows import add_rows, create_list
from app.llm.base import ToolCall
from tests.conftest import act_in_own_org, project
from tests.test_agent import _balance, _job, _run, _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db


def test_normalise_matches_list_row_normalisation():
    assert normalise('domain', 'https://www.Acme.com/about') == 'acme.com'
    assert normalise('domain', '@acme.com') == 'acme.com'
    assert normalise('linkedin_url', 'https://it.linkedin.com/in/Ada/?trk=x') == 'it.linkedin.com/in/ada'
    assert normalise('linkedin_url', 'http://www.linkedin.com/in/ada/') == 'linkedin.com/in/ada'
    assert normalise('company_name', '  Acme   SpA ') == 'acme spa'
    assert normalise('email', ' Ada@Acme.COM ') == 'ada@acme.com'
    for kind, bad in [('email', 'ada'), ('domain', 'acme'), ('linkedin_url', 'acme.com/in/x'), ('company_name', ' ')]:
        assert normalise(kind, bad) is None


def test_guess_kind_for_csv_cells():
    assert [guess_kind(v) for v in ('ada@acme.com', 'linkedin.com/in/ada', 'acme.com', 'Acme SpA', 'Acme')] == [
        'email',
        'linkedin_url',
        'domain',
        'company_name',
        'company_name',
    ]


def test_rules_match_companies_and_people():
    rules = frozenset(
        {
            ('domain', 'acme.com'),
            ('company_name', 'beta srl'),
            ('linkedin_url', 'linkedin.com/in/ada'),
            ('email', 'x@y.it'),
        }
    )
    assert is_excluded(rules, {'name': 'Acme', 'website': 'https://www.acme.com/'})
    assert is_excluded(rules, {'full_name': 'Bo', 'email': 'bo@eu.acme.com'})  # subdomain of an excluded domain
    assert is_excluded(rules, {'full_name': 'Ci', 'company_domain': 'acme.com'})
    assert is_excluded(rules, {'name': 'Beta  SRL'})
    assert is_excluded(rules, {'company_name': 'beta srl'})
    assert is_excluded(rules, {'linkedin_url': 'https://www.linkedin.com/in/ada/?trk=1'})
    assert is_excluded(rules, {'email': 'X@y.it'})
    assert not is_excluded(rules, {'name': 'Acme Holdings', 'domain': 'notacme.com', 'email': 'z@y.it'})
    assert not is_excluded(frozenset(), {'domain': 'acme.com'})


def test_shared_social_hosts_are_never_domain_rules():
    for v in ('facebook.com/acme', 'https://www.linkedin.com/company/acme', 'x.com', 'm.facebook.com', 'youtu.be/x'):
        assert normalise('domain', v) is None
    assert normalise('domain', 'xacme.com') == 'xacme.com'


@needs_db
def test_exclusions_api_is_owner_scoped(client, db):
    tc, _ = client
    res = tc.post(
        '/v1/exclusions',
        json={
            'items': [
                {'kind': 'domain', 'value': 'https://www.acme.com/'},
                {'value': 'ada@beta.it'},
                {'value': 'ADA@beta.it'},  # duplicate after normalisation
                {'kind': 'email', 'value': 'not-an-email'},
            ]
        },
    )
    assert res.status_code == 200
    assert res.json() == {'added': 2, 'invalid': ['not-an-email'], 'invalid_count': 1}
    assert tc.post('/v1/exclusions', json={'items': [{'value': 'acme.com'}]}).json()['added'] == 0  # idempotent
    bad = tc.post('/v1/exclusions', json={'items': [{'kind': 'domain', 'value': 'nope'}]})
    assert bad.status_code == 422 and bad.json()['detail']['code'] == 'EXCLUSIONS.INVALID_VALUE'

    listed = tc.get('/v1/exclusions').json()['exclusions']
    assert {(e['kind'], e['value']) for e in listed} == {('domain', 'acme.com'), ('email', 'ada@beta.it')}

    other = _user(db)
    foreign = db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'other.com') RETURNING id",
        (project(db, other), other),
    ).fetchone()['id']
    assert tc.delete(f'/v1/exclusions/{foreign}').status_code == 404
    assert len(tc.get('/v1/exclusions').json()['exclusions']) == 2  # never sees another user's rows
    assert tc.delete(f'/v1/exclusions/{listed[0]["id"]}').status_code == 204
    assert len(tc.get('/v1/exclusions').json()['exclusions']) == 1


@needs_db
async def test_search_results_skip_the_users_exclusions(db):
    uid = _user(db, credits=50)
    _, jid = _job(db, uid, 'marketing agencies')
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'acme.com')",
        (project(db, uid), uid),
    )
    other = _user(db)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'domain', 'beta.it')",
        (project(db, other), other),
    )

    events, _ = await _run(jid, [('', [ToolCall('t1', 'fake_companies', {})]), ('Done.', [])])

    names = [r['cells']['name'] for r in db.execute('SELECT cells FROM list_rows ORDER BY seq').fetchall()]
    assert names == ['Beta']  # both Acme rows dropped; another user's rule does not apply
    result = json.loads(dict(events)['tool_result']['result_preview'])
    assert result['excluded_by_user'] == 2 and result['list']['rows_added'] == 1
    assert 'credits' not in dict(events) and _balance(db, uid) == 50


@needs_db
async def test_enrichment_skips_rows_matching_an_exclusion(db):
    uid = _user(db)
    act_in_own_org(db, uid)  # the enrichment tool of uid's turn
    sid, _ = _job(db, uid, 'emails')
    list_id = create_list(db, user_id=uid, session_id=sid, name='People', entity='person')
    blocked, kept = add_rows(
        db,
        list_id,
        'person',
        [
            {'first_name': 'Ada', 'linkedin_url': 'https://www.linkedin.com/in/ada'},
            {'first_name': 'Bo', 'linkedin_url': 'https://www.linkedin.com/in/bo'},
        ],
    )
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'linkedin_url', 'linkedin.com/in/ada')",
        (project(db, uid), uid),
    )
    assert [p['row_id'] for p in enrich_email.load_rows(list_id, [blocked, kept])] == [kept]
