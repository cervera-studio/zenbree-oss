"""The dry-run preview of an automation (#940): every missing prerequisite, what a run would work on and the most it
may spend, read with nothing written and no vendor called."""

import os

import pytest

from tests.automations_support import (
    client,
    connect_hubspot,
    database,
    make_campaign,
    make_list,
    teammate,
    user,
)
from tests.conftest import member, project

db = pytest.fixture(database)
pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')

PEOPLE = [
    {'full_name': 'Ada', 'email': 'ada@acme.test', 'email_status': 'verified'},
    {'full_name': 'Bob', 'email': 'bob@acme.test'},  # typed by hand: no verdict, still sent to
    {'full_name': 'Cleo', 'email': 'cleo@acme.test', 'email_status': 'invalid'},
    {'full_name': 'Dan', 'linkedin_url': 'https://linkedin.com/in/dan'},
    {'full_name': 'Eva'},
]
STEPS = [
    {'type': 'find_people', 'accounts_list_id': None, 'titles': ['Head of Sales']},
    {'type': 'qualify', 'brief': 'B2B SaaS'},
    {'type': 'find_emails'},
    {'type': 'add_to_campaign', 'campaign_id': None},
]


@pytest.fixture
def world(db, monkeypatch):
    uid = user(db)
    people, ids = make_list(db, uid, 'person', [*PEOPLE, {'full_name': 'Gone', 'email': 'gone@acme.test'}])
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (ids[-1],))
    accounts, account_ids = make_list(db, uid, 'company', [{'domain': 'a.test'}, {'domain': 'b.test'}, {}])
    db.execute('UPDATE list_rows SET excluded = true WHERE id = %s', (account_ids[-1],))
    campaign = make_campaign(db, uid, status='active')
    steps = [{**STEPS[0], 'accounts_list_id': accounts}, *STEPS[1:3], {**STEPS[3], 'campaign_id': campaign}]
    body = {'credit_cap': 100, 'definition': {'list_id': people, 'steps': steps}}
    return client(db, monkeypatch, uid), uid, body, ids


def _codes(answer) -> list[str]:
    return [p['code'] for p in answer['problems']]


def test_a_preview_lists_every_missing_prerequisite_in_order(db, world):
    tc, *_ = world
    stranger = user(db)
    foreign_people, _ = make_list(db, stranger, 'person', PEOPLE)
    steps = [{'type': 'add_to_campaign', 'campaign_id': make_campaign(db, stranger)}, {'type': 'write_hubspot'}]
    got = tc.post(
        '/v1/automations/preview', json={'credit_cap': 5, 'definition': {'list_id': foreign_people, 'steps': steps}}
    )
    assert got.status_code == 200, got.text
    assert _codes(got.json()) == [
        'AUTOMATION.LIST_NOT_FOUND',
        'AUTOMATION.CAMPAIGN_NOT_FOUND',
        'AUTOMATION.HUBSPOT_OFF',
    ]
    # Nothing of another organization is counted or named.
    assert (got.json()['people'], got.json()['with_email'], got.json()['campaign']) == (None, None, None)


def test_a_preview_counts_what_a_run_works_on_and_the_most_it_may_spend(db, world):
    tc, _, body, _ = world
    campaign = body['definition']['steps'][3]['campaign_id']
    got = tc.post('/v1/automations/preview', json=body).json()
    assert got == {
        'problems': [],
        'steps': [
            {'type': 'find_people', 'credits_each': 0},
            {'type': 'qualify', 'credits_each': 1},
            {'type': 'find_emails', 'credits_each': 1},
            {'type': 'add_to_campaign', 'credits_each': 0},
        ],
        'people': 5,
        'with_email': 2,
        'with_contact': 3,
        'accounts': 2,
        'campaign': {'id': campaign, 'name': 'Outreach', 'status': 'active'},
        'instantly': None,
        'signals': None,
        'people_per_run': 25,
        'accounts_per_run': 10,
        'max_credits': 50,
    }

    lower_cap = tc.post('/v1/automations/preview', json={**body, 'credit_cap': 20}).json()
    assert lower_cap['max_credits'] == 20
    free = {'list_id': body['definition']['list_id'], 'steps': [{'type': 'add_to_campaign', 'campaign_id': campaign}]}
    free_answer = tc.post('/v1/automations/preview', json={'credit_cap': 20, 'definition': free}).json()
    assert (free_answer['max_credits'], free_answer['accounts']) == (0, None)


def _counts(db) -> dict:
    tables = (
        'automations',
        'automation_runs',
        'automation_rows',
        'automation_scores',
        'vendor_calls',
        'credit_transactions',
        'row_charges',
        'crm_contact_writes',
        'outreach_campaign_targets',
        'list_rows',
    )
    counts = {t: db.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n'] for t in tables}
    return counts | {'credits': db.execute('SELECT sum(credits_remaining) AS n FROM user_credits').fetchone()['n']}


def test_a_preview_writes_nothing_and_calls_no_vendor(db, world, monkeypatch):
    from app.agent.tools.handlers import enrich_email, search_people
    from app.automations import hubspot, qualify

    def vendor(*args, **kwargs):
        raise AssertionError('a preview called a vendor')

    for module, name in ((search_people, 'handle'), (enrich_email, 'handle'), (qualify, 'score')):
        monkeypatch.setattr(module, name, vendor)
    monkeypatch.setattr(hubspot, 'fetch_hubspot', vendor)
    tc, uid, body, _ = world
    connect_hubspot(db, uid)
    body['definition']['steps'].append({'type': 'write_hubspot'})
    saved = tc.post('/v1/automations', json={'name': 'Outbound', **body}).json()
    before = _counts(db)

    unsaved = tc.post('/v1/automations/preview', json=body).json()
    existing = tc.post('/v1/automations/preview', json={**body, 'automation_id': saved['id']}).json()

    assert (unsaved['problems'], unsaved['people'], existing['people']) == ([], 5, 5)
    assert _counts(db) == before


def test_an_automation_id_previews_as_its_creator_in_its_project(db, world, monkeypatch):
    tc, uid, body, ids = world
    saved = tc.post('/v1/automations', json={'name': 'Outbound', **body}).json()
    db.execute(
        'INSERT INTO automation_rows (automation_id, row_id, organization_id) VALUES (%s, %s, %s)',
        (saved['id'], ids[0], saved['organization_id']),
    )
    org = member(db, uid).org_id
    mate = teammate(db, org)
    other = client(db, monkeypatch, mate, org=org, project_id=project(db, uid))
    hubspot = {**body, 'definition': {**body['definition'], 'steps': [{'type': 'write_hubspot'}]}}

    def preview(**more) -> dict:
        return other.post('/v1/automations/preview', json={**hubspot, **more}).json()

    connect_hubspot(db, mate)  # the teammate may write to HubSpot; the creator may not
    as_creator = preview(automation_id=saved['id'])
    assert (_codes(as_creator), as_creator['people']) == (['AUTOMATION.HUBSPOT_OFF'], 4)  # one taken in already
    assert _codes(preview()) == []

    db.execute('DELETE FROM user_oauth_connections WHERE user_id = %s', (mate,))
    connect_hubspot(db, uid)
    assert _codes(preview(automation_id=saved['id'])) == []
    assert _codes(preview()) == ['AUTOMATION.HUBSPOT_OFF']

    stranger = user(db)
    foreign = client(db, monkeypatch, stranger).post(
        '/v1/automations/preview', json={**body, 'automation_id': saved['id']}
    )
    assert (foreign.status_code, foreign.json()['detail']['code']) == (404, 'AUTOMATION.NOT_FOUND')


@pytest.mark.parametrize(
    'gone',
    [
        'UPDATE "user" SET banned = true WHERE id = %(uid)s',
        'DELETE FROM member WHERE "userId" = %(uid)s',
        'UPDATE automations SET user_id = NULL WHERE id = %(automation)s',  # the account was deleted
    ],
)
def test_a_creator_who_is_no_longer_a_member_is_a_problem_of_the_preview_and_the_enable(db, world, monkeypatch, gone):
    tc, uid, body, _ = world
    saved = tc.post('/v1/automations', json={'name': 'Outbound', **body, 'enabled': False}).json()
    org = member(db, uid).org_id
    admin = teammate(db, org)
    db.execute('UPDATE member SET role = %s WHERE "userId" = %s', ('admin', admin))
    other = client(db, monkeypatch, admin, org=org, project_id=project(db, uid))
    db.execute(gone, {'uid': uid, 'automation': saved['id']})

    # The grant of a creator who is gone is not checked: only a new automation makes it run.
    hubspot = {
        **body,
        'definition': {**body['definition'], 'steps': [*body['definition']['steps'], {'type': 'write_hubspot'}]},
    }
    previewed = other.post('/v1/automations/preview', json={**hubspot, 'automation_id': saved['id']})
    assert _codes(previewed.json()) == ['AUTOMATION.CREATOR_GONE']
    enabled = other.patch(f'/v1/automations/{saved["id"]}', json={'enabled': True})
    assert (enabled.status_code, enabled.json()['detail']['code']) == (422, 'AUTOMATION.CREATOR_GONE')
