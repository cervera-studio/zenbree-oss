"""Automations (#939): the definition, its versions, the API, the beat and the approval, against real SQL."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.automations import service
from app.core.system_reasons import SystemReason
from tests.automations_support import client, database, make_campaign, make_list, teammate, user
from tests.conftest import call_system, member, project

db = pytest.fixture(database)
pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')


def _body(people, accounts, campaign, **more):
    steps = [
        {'type': 'find_people', 'accounts_list_id': accounts, 'titles': ['Head of Sales']},
        {'type': 'qualify', 'brief': 'B2B SaaS with a sales team', 'min_score': 60},
        {'type': 'find_emails'},
        {'type': 'add_to_campaign', 'campaign_id': campaign},
    ]
    return {'name': 'Outbound', 'credit_cap': 20, 'definition': {'list_id': people, 'steps': steps}, **more}


@pytest.fixture
def api(db, monkeypatch):
    uid = user(db)
    tc = client(db, monkeypatch, uid)
    assert tc.get('/v1/automations', headers={'Authorization': ''}).status_code == 401
    people, _ = make_list(db, uid, 'person')
    accounts, _ = make_list(db, uid, 'company')
    return tc, uid, _body(people, accounts, make_campaign(db, uid))


def test_the_definition_is_validated_against_the_project_before_it_is_saved(db, api):
    tc, uid, body = api
    other = user(db)
    foreign_people, _ = make_list(db, other, 'person')
    foreign_campaign = make_campaign(db, other)
    companies, _ = make_list(db, uid, 'company')

    made = tc.post('/v1/automations', json=body)
    assert made.status_code == 201
    assert (made.json()['version'], made.json()['approved_version'], made.json()['enabled']) == (1, None, True)

    def refused(change, code, status=422):
        got = tc.post('/v1/automations', json=change)
        assert (got.status_code, got.json()['detail']['code'] if status != 422 or 'detail' in got.json() else '') == (
            status,
            code,
        ), got.text

    def definition(**steps):
        return {**body, 'definition': {**body['definition'], **steps}}

    refused(definition(list_id=foreign_people), 'AUTOMATION.LIST_NOT_FOUND')
    refused(definition(list_id=companies), 'AUTOMATION.LIST_NOT_FOUND')
    bad_accounts = [{**body['definition']['steps'][0], 'accounts_list_id': body['definition']['list_id']}]
    refused(definition(steps=bad_accounts), 'AUTOMATION.ACCOUNTS_NOT_FOUND')
    other_campaign = [{'type': 'add_to_campaign', 'campaign_id': foreign_campaign}]
    refused(definition(steps=other_campaign), 'AUTOMATION.CAMPAIGN_NOT_FOUND')
    refused(definition(steps=[{'type': 'write_hubspot'}]), 'AUTOMATION.HUBSPOT_OFF')


def test_the_shape_of_a_definition_is_bounded(api):
    tc, _, body = api
    steps = body['definition']['steps']
    for definition in (
        {**body['definition'], 'steps': list(reversed(steps))},  # the steps run in one order
        {**body['definition'], 'steps': [steps[1], steps[1]]},  # each at most once
        {**body['definition'], 'steps': []},
        {**body['definition'], 'steps': [{'type': 'send_email'}]},  # no step sends
        {**body['definition'], 'steps': [{'type': 'find_emails', 'extra': 1}]},
    ):
        assert tc.post('/v1/automations', json={**body, 'definition': definition}).status_code == 422
    assert tc.post('/v1/automations', json={**body, 'credit_cap': 101}).status_code == 422
    assert tc.post('/v1/automations', json={**body, 'credit_cap': 0}).status_code == 422
    assert tc.post('/v1/automations', json={**body, 'trigger': {'kind': 'schedule'}}).status_code == 422
    assert (
        tc.post('/v1/automations', json={**body, 'trigger': {'kind': 'manual', 'cadence': 'daily'}}).status_code == 422
    )


def test_an_edit_of_the_definition_or_the_cap_is_a_new_version_and_a_rename_is_not(db, api):
    tc, _, body = api
    made = tc.post('/v1/automations', json=body).json()
    url = f'/v1/automations/{made["id"]}'
    assert tc.post(f'{url}/run').status_code == 202

    assert tc.patch(url, json={'name': 'Renamed'}).json()['version'] == 1
    assert tc.patch(url, json={'credit_cap': 20}).json()['version'] == 1  # no change
    edited = tc.patch(url, json={'credit_cap': 30}).json()
    assert (edited['version'], edited['credit_cap'], edited['last_run']['status']) == (2, 30, 'cancelled')
    assert edited['last_run']['error_code'] == 'automation_edited'

    run = tc.post(f'{url}/run').json()
    assert run['version'] == 2 and run['credit_cap'] == 30  # pinned to the version it was queued at
    edited = tc.patch(url, json={'credit_cap': 40}).json()
    assert edited['version'] == 3
    pinned = db.execute('SELECT version, credit_cap, definition FROM automation_runs WHERE id = %s', (run['id'],))
    assert pinned.fetchone()['version'] == 2

    broken = {'definition': {**body['definition'], 'list_id': '00000000-0000-0000-0000-000000000000'}}
    assert tc.patch(url, json=broken).json()['detail']['code'] == 'AUTOMATION.LIST_NOT_FOUND'
    assert tc.get('/v1/automations').json()['automations'][0]['version'] == 3


def test_a_teammate_sees_an_automation_and_only_its_creator_or_an_admin_manages_it(db, api, monkeypatch):
    tc, uid, body = api
    made = tc.post('/v1/automations', json=body).json()
    org = member(db, uid).org_id
    other = client(db, monkeypatch, teammate(db, org), org=org, project_id=project(db, uid))
    assert [a['id'] for a in other.get('/v1/automations').json()['automations']] == [made['id']]
    url = f'/v1/automations/{made["id"]}'
    assert other.patch(url, json={'name': 'Mine'}).json()['detail']['code'] == 'AUTOMATION.FORBIDDEN'
    assert other.post(f'{url}/run').json()['detail']['code'] == 'AUTOMATION.FORBIDDEN'


def test_a_paused_automation_runs_nothing_and_a_resumed_one_is_checked_again(db, api):
    tc, _, body = api
    made = tc.post('/v1/automations', json=body).json()
    url = f'/v1/automations/{made["id"]}'
    queued = tc.post(f'{url}/run').json()
    assert tc.post(f'{url}/run').json()['id'] == queued['id']  # one live run at a time

    paused = tc.patch(url, json={'enabled': False}).json()
    assert (paused['enabled'], paused['last_run']['error_code']) == (False, 'automation_paused')
    assert tc.post(f'{url}/run').json()['detail']['code'] == 'AUTOMATION.PAUSED'
    db.execute("UPDATE lists SET deleted_at = now() WHERE entity = 'company'")
    assert tc.patch(url, json={'enabled': True}).json()['detail']['code'] == 'AUTOMATION.ACCOUNTS_NOT_FOUND'


def test_the_limit_per_project_and_delete(db, api, monkeypatch):
    tc, _, body = api
    monkeypatch.setattr(service, 'MAX_PER_PROJECT', 1)
    made = tc.post('/v1/automations', json=body).json()
    assert tc.post('/v1/automations', json=body).json()['detail']['code'] == 'AUTOMATION.LIMIT'
    assert tc.delete(f'/v1/automations/{made["id"]}').status_code == 204
    assert tc.get(f'/v1/automations/{made["id"]}/runs').status_code == 404
    assert tc.post('/v1/automations', json=body).status_code == 201


def test_the_beat_queues_a_due_schedule_once_and_moves_it_to_its_next_slot(db, api):
    tc, _, body = api
    made = tc.post('/v1/automations', json={**body, 'trigger': {'kind': 'schedule', 'cadence': 'weekly'}}).json()
    assert made['next_run_at'] is not None and made['cadence'] == 'weekly'
    manual = tc.post('/v1/automations', json=body).json()  # a manual one is never due
    was = datetime.now(UTC) - timedelta(days=15, hours=1)
    db.execute('UPDATE automations SET next_run_at = %s WHERE id = %s', (was, made['id']))
    db.execute('UPDATE automations SET next_run_at = %s WHERE id = %s', (was, manual['id']))

    queued = call_system(SystemReason.DUE_WORK, service.schedule_due)
    assert len(queued) == 1 and call_system(SystemReason.DUE_WORK, service.schedule_due) == queued
    moved = db.execute('SELECT next_run_at FROM automations WHERE id = %s', (made['id'],)).fetchone()['next_run_at']
    assert moved == was + timedelta(days=21)
    run = db.execute('SELECT trigger_kind, version FROM automation_runs').fetchone()
    assert (run['trigger_kind'], run['version']) == ('schedule', 1)
