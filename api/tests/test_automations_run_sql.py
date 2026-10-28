"""Runs of automations (#939) against real SQL, with every vendor and the model faked: the review run and its approval,
the credit cap, what is refused before a credit is spent, a step that fails or is cut off, and a worker that dies."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.agent.tools.handlers import enrich_email, search_people
from app.agent.tools.registry import ToolResult
from app.automations import approval, held_writes, hubspot, qualify, runner, service
from app.automations.models import AutomationIn, AutomationPatch, Definition
from app.core.errors import err, ok
from app.core.scope import acting_as
from app.core.system_reasons import SystemReason
from app.lists import lookup_outcome
from app.outreach import dispatcher
from tests.automations_support import connect_hubspot, database, make_campaign, make_list, user
from tests.conftest import call_system, member, project
from tests.test_campaign_fields_sql import NEWS, STEP
from tests.test_campaign_targets_row_sql import linked

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')
db = pytest.fixture(database)


class Vendors:
    """Apollo's people search, the email lookup and the model, as the steps see them."""

    def __init__(self):
        self.people: dict[str, list[dict]] = {}  # company domain -> people
        self.emails: dict[str, str] = {}  # full name -> email found
        self.searches: list[str] = []
        self.looked_up: list[str] = []
        self.failures = 0  # the next calls of the search fail this many times
        self.garbage = False

    async def search(self, args, ctx):
        self.searches.append(args['company_domains'][0])
        if self.failures:
            self.failures -= 1
            return ToolResult(ok=False, error='people search failed')
        return ToolResult(ok=True, rows=self.people.get(args['company_domains'][0], []), entity='person')

    async def enrich(self, args, ctx):
        from app.core.db import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                'SELECT id, cells FROM list_rows WHERE id = ANY(%s::uuid[])', ([str(i) for i in args['row_ids']],)
            ).fetchall()
        updates = []
        for row in rows[: ctx.max_units]:
            name = row['cells']['full_name']
            self.looked_up.append(name)
            email = self.emails.get(name)
            cells = {'email': email, 'email_status': 'verified' if email else 'not_found'}
            updates.append({'row_id': str(row['id']), 'cells': cells})
        outcome = lookup_outcome.searched('email', updates)
        return ToolResult(ok=True, row_updates=updates, entity='person', outcome=outcome)

    async def chat(self, **kwargs):
        if self.garbage:
            return SimpleNamespace(text='not json')
        ids = json.loads(kwargs['messages'][0]['content'].split('\n\n')[-1])
        scored = [{'id': p['id'], 'score': 10 if 'Intern' in p.get('title', '') else 90} for p in ids]
        return SimpleNamespace(text=json.dumps({'scores': scored}))


@pytest.fixture
def vendors(monkeypatch):
    fake = Vendors()
    monkeypatch.setattr(search_people, 'handle', fake.search)
    monkeypatch.setattr(enrich_email, 'handle', fake.enrich)
    monkeypatch.setattr(qualify, 'get_provider', lambda: fake)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(runner.asyncio, 'sleep', no_wait)
    return fake


def person(name, title='Head of Sales', domain='acme.it'):
    return {'full_name': name, 'first_name': name.split()[0], 'last_name': name.split()[1], 'title': title,
            'company_domain': domain, 'company_name': 'Acme'}  # fmt: skip


class World:
    """A user, an accounts list, the people list and a campaign, and the automation over them."""

    def __init__(
        self, db, vendors, *, credits=200, campaign='active', cap=50, steps=None, domains=('acme.it',), hubspot=False
    ):
        self.db, self.vendors = db, vendors
        self.uid = user(db, credits=credits)
        if hubspot:
            connect_hubspot(db, self.uid)
        self.org = member(db, self.uid).org_id
        self.people, _ = make_list(db, self.uid, 'person')
        self.accounts, _ = make_list(db, self.uid, 'company', [{'name': d, 'domain': d} for d in domains])
        self.campaign = make_campaign(db, self.uid, status=campaign)
        listed = steps or ['find_people', 'qualify', 'find_emails', 'add_to_campaign']
        by_kind = {
            'find_people': {
                'type': 'find_people',
                'accounts_list_id': self.accounts,
                'titles': ['Head of Sales'],
                'per_account': 5,
            },
            'qualify': {'type': 'qualify', 'brief': 'sales leaders', 'min_score': 50},
            'find_emails': {'type': 'find_emails'},
            'add_to_campaign': {'type': 'add_to_campaign', 'campaign_id': self.campaign},
            'write_hubspot': {'type': 'write_hubspot'},
        }
        body = AutomationIn.model_validate(
            {
                'name': 'Outbound',
                'credit_cap': cap,
                'definition': {'list_id': self.people, 'steps': [by_kind[k] for k in listed]},
            }
        )
        with self.scope():
            self.id = str(service.create(member(db, self.uid), project(db, self.uid), body)['id'])

    def scope(self):
        return acting_as(self.org, self.uid)

    async def run(self) -> dict:
        with self.scope(), self.db.transaction():
            automation = self.db.execute('SELECT * FROM automations WHERE id = %s', (self.id,)).fetchone()
            queued = service.enqueue(self.db, automation, datetime.now(UTC), 'manual')
        with self.scope():
            await runner.run_automation(queued['id'])
        return self.row(queued['id'])

    def row(self, run_id) -> dict:
        return self.db.execute('SELECT * FROM automation_runs WHERE id = %s', (run_id,)).fetchone()

    def steps(self, run_id) -> dict[str, dict]:
        rows = self.db.execute('SELECT * FROM automation_run_steps WHERE run_id = %s ORDER BY position', (run_id,))
        return {r['kind']: r for r in rows}

    def balance(self) -> int:
        return self.db.execute(
            'SELECT credits_remaining AS n FROM user_credits WHERE user_id = %s', (self.uid,)
        ).fetchone()['n']

    def targets(self) -> list[tuple[str, str]]:
        rows = self.db.execute('SELECT email, status FROM outreach_campaign_targets ORDER BY email').fetchall()
        return [(r['email'], r['status']) for r in rows]

    async def write_held(self, run_id):
        with self.scope():
            await held_writes.write(run_id)

    def approve(self, run_id):
        with self.scope():
            return approval.approve(member(self.db, self.uid), project(self.db, self.uid), self.id, run_id)


def _team(vendors, *names):
    vendors.people['acme.it'] = [person(n) for n in names]
    vendors.emails.update({n: f'{n.split()[0].lower()}@acme.it' for n in names})


async def test_the_first_run_is_reviewed_and_later_runs_use_the_campaigns_status(db, vendors):
    """Done when (#939): the first run of a version enters as draft; once approved, the campaign's normal status."""
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi', 'Bob Neri')

    first = await world.run()
    assert (first['status'], first['review'], first['credits_spent'], first['error_code']) == (
        'succeeded',
        True,
        4,
        None,
    )
    assert world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft')]
    assert linked(db, world.campaign) == [('ada@acme.it', 'ada@acme.it'), ('bob@acme.it', 'bob@acme.it')]
    assert world.balance() == 196  # two scores and two emails found; the rest of the hold went back
    steps = world.steps(first['id'])
    assert {k: (s['status'], s['credits']) for k, s in steps.items()} == {
        'find_people': ('succeeded', 0),
        'qualify': ('succeeded', 2),
        'find_emails': ('succeeded', 2),
        'add_to_campaign': ('succeeded', 0),
    }
    assert steps['add_to_campaign']['output']['status'] == 'draft'
    assert 'ada@acme.it' not in json.dumps(
        [dict(s, started_at=None, finished_at=None) for s in steps.values()], default=str
    )

    # Not approved yet: the next run is reviewed too.
    _team(vendors, 'Cleo Gallo')
    assert (await world.run())['review'] is True

    approved = world.approve(first['id'])
    assert approved['approved_at'] is not None and approved['summary']['promoted'] == 2
    assert world.targets()[:2] == [
        ('ada@acme.it', 'active'),
        ('bob@acme.it', 'active'),
    ]  # promoted in a running campaign

    _team(vendors, 'Dan Verdi')
    later = await world.run()
    assert (later['review'], later['status']) == (False, 'succeeded')
    assert dict(world.targets())['dan@acme.it'] == 'active'
    assert db.execute('SELECT count(*) AS n FROM automation_enrollments').fetchone()['n'] == 4


async def test_a_run_over_the_accounts_keeps_the_people_as_the_search_found_them(db, vendors):
    """Only a signal run stamps the searched domain on the people it finds (``signal_trigger.people_at_account``
    matches them by it): a run over the accounts adds them as the vendor described them."""
    world = World(db, vendors, steps=['find_people'])
    vendors.people['acme.it'] = [person('Ada Rossi', domain=None)]
    assert (await world.run())['status'] == 'succeeded'
    added = db.execute('SELECT cells FROM list_rows WHERE list_id = %s', (world.people,)).fetchall()
    assert [r['cells'].get('company_domain') for r in added] == [None]


async def test_a_draft_campaign_stays_a_draft_and_approval_never_activates_it(db, vendors):
    world = World(db, vendors, campaign='draft')
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    world.approve(first['id'])
    _team(vendors, 'Bob Neri')
    await world.run()
    assert world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft')]
    assert db.execute('SELECT status FROM outreach_campaigns').fetchone()['status'] == 'draft'


async def test_an_edit_sends_the_next_run_back_to_review_and_a_stale_run_cannot_be_approved(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    world.approve(first['id'])
    assert (await world.run())['review'] is False

    with world.scope():
        from app.automations.models import AutomationPatch

        service.update(member(db, world.uid), project(db, world.uid), world.id, AutomationPatch(credit_cap=60))
    _team(vendors, 'Bob Neri')
    second = await world.run()
    assert (second['version'], second['review']) == (2, True)
    with world.scope():
        db.execute('UPDATE automations SET version = 3')

    with pytest.raises(HTTPException) as refused:
        world.approve(second['id'])
    assert refused.value.detail['code'] == 'AUTOMATION.RUN_OUTDATED'


async def test_the_credit_cap_stops_a_run_and_the_people_it_did_not_reach_go_first_next_time(db, vendors):
    world = World(db, vendors, cap=3)
    _team(vendors, 'Ada Rossi', 'Bob Neri', 'Cleo Gallo', 'Dan Verdi', 'Eve Conti')

    first = await world.run()
    assert (first['status'], first['error_code'], first['credits_spent']) == ('succeeded', 'credit_cap_reached', 3)
    assert world.steps(first['id'])['qualify']['credits'] == 3
    # No credit is left to look anybody up: the three scored wait, with their scores, for the next run.
    assert world.steps(first['id'])['find_emails']['output']['not_reached'] == 3
    assert world.balance() == 197
    assert db.execute('SELECT count(*) AS n FROM automation_rows').fetchone()['n'] == 0  # scored, none taken in

    second = await world.run()
    # The three scored are not scored again: the cap goes to the two not reached, then to one email.
    steps = world.steps(second['id'])
    assert (steps['qualify']['credits'], steps['find_emails']['credits'], second['credits_spent']) == (2, 1, 3)
    assert db.execute('SELECT count(*) AS n FROM automation_rows').fetchone()['n'] == 1


async def test_qualify_scores_against_its_own_brief_not_the_chats_score_and_bills_once_for_each_version(db, vendors):
    world = World(db, vendors, steps=['qualify'])
    for i, (name, title, chat_score) in enumerate([('Ada Rossi', 'Head of Sales', 5), ('Bob Neri', 'Intern', 95)]):
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key, relevance_score) VALUES (%s, %s, %s, %s)',
            (world.people, Jsonb({'full_name': name, 'title': title}), f'chat-{i}', chat_score),
        )

    first = await world.run()
    assert (first['status'], first['credits_spent'], first['summary']['qualify']['kept']) == ('succeeded', 2, 1)
    marks = db.execute(
        "SELECT cells ->> 'full_name' AS name, s.score FROM automation_scores s JOIN list_rows r ON r.id = s.row_id"
        ' ORDER BY 1'
    ).fetchall()
    assert [(m['name'], m['score']) for m in marks] == [('Ada Rossi', 90), ('Bob Neri', 10)]
    chat = db.execute(
        'SELECT relevance_score AS n FROM list_rows WHERE list_id = %s ORDER BY seq', (world.people,)
    ).fetchall()
    assert [r['n'] for r in chat] == [5, 95]  # the chat's own scores are not touched

    db.execute('DELETE FROM automation_rows')
    again = await world.run()
    assert again['credits_spent'] == 0  # the same version never pays for a person twice

    with world.scope():
        service.update(
            member(db, world.uid),
            project(db, world.uid),
            world.id,
            AutomationPatch(
                definition=Definition.model_validate(
                    {'list_id': world.people, 'steps': [{'type': 'qualify', 'brief': 'another brief', 'min_score': 50}]}
                )
            ),
        )
    db.execute('DELETE FROM automation_rows')
    edited = await world.run()
    assert edited['credits_spent'] == 2  # a new version scores against its new brief


async def test_a_person_a_rule_or_a_reply_refuses_costs_nothing(db, vendors):
    world = World(db, vendors)
    other_campaign = make_campaign(db, world.uid, status='active')
    db.execute(
        "INSERT INTO exclusions (organization_id, project_id, kind, value) VALUES (%s, %s, 'domain', 'blocked.it')",
        (world.org, project(db, world.uid)),
    )
    vendors.people['acme.it'] = [
        person('Ada Rossi'),
        person('Bob Neri', domain='blocked.it'),
        person('Cleo Gallo'),
    ]
    vendors.emails = {'Ada Rossi': 'ada@acme.it', 'Cleo Gallo': 'cleo@acme.it'}
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, replied_at)'
        " VALUES (%s, %s, 'cleo@acme.it', 'replied', now())",
        (other_campaign, world.uid),
    )
    db.execute(
        'UPDATE list_rows SET cells = cells || \'{"email": "cleo@acme.it", "email_status": "verified"}\' WHERE false'
    )

    run = await world.run()
    steps = world.steps(run['id'])
    assert steps['find_people']['output']['people_excluded'] == 1  # Bob never reached the list
    assert steps['qualify']['credits'] == 2  # Ada and Cleo: Cleo's address is not known yet, so the reply is seen later
    assert 'Bob Neri' not in vendors.looked_up
    assert world.targets()[0] == ('ada@acme.it', 'draft')
    assert ('cleo@acme.it', 'draft') not in world.targets()  # replied: left out by the enrollment rule


async def test_a_step_that_fails_before_any_charge_is_tried_again_and_three_failures_end_the_run(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    vendors.failures = 2
    first = await world.run()
    assert first['status'] == 'succeeded' and world.steps(first['id'])['find_people']['attempts'] == 3

    vendors.failures = 99
    _team(vendors, 'Bob Neri')
    db.execute('UPDATE automations SET accounts_seq = 0')
    failed = await world.run()
    steps = world.steps(failed['id'])
    assert (failed['status'], failed['error_code'], failed['credits_spent']) == ('failed', 'vendor_failed', 0)
    assert (steps['find_people']['status'], steps['find_people']['attempts']) == ('failed', 3)
    assert {steps[k]['status'] for k in ('qualify', 'find_emails', 'add_to_campaign')} == {'skipped'}
    assert world.balance() == 198  # the hold of the failed run went back whole


async def test_the_model_that_answers_nothing_usable_charges_nothing(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    vendors.garbage = True
    run = await world.run()
    assert (run['status'], run['error_code'], run['credits_spent']) == ('failed', 'llm_failed', 0)
    assert world.balance() == 200 and world.targets() == []


async def test_a_run_whose_automation_is_paused_midway_writes_no_more(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    real = vendors.enrich

    async def pause_then_search(args, ctx):
        db.execute('UPDATE automations SET enabled = false')
        return await real(args, ctx)

    vendors.enrich = pause_then_search
    from app.automations import steps

    steps.enrich_email.handle = pause_then_search
    run = await world.run()
    assert (run['status'], run['error_code']) == ('cancelled', 'automation_paused')
    states = {k: s['status'] for k, s in world.steps(run['id']).items()}
    assert states['qualify'] == 'succeeded' and states['find_emails'] == 'skipped'
    assert world.targets() == [] and run['credits_spent'] == 1  # what was scored stays charged; nothing was enrolled
    assert db.execute('SELECT count(*) AS n FROM automation_rows').fetchone()['n'] == 0


async def test_a_cancel_stops_a_running_run_before_its_next_step(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    real = vendors.chat

    async def cancel_while_scoring(**kwargs):
        db.execute('UPDATE automation_runs SET cancel_requested = true')
        return await real(**kwargs)

    vendors.chat = cancel_while_scoring
    run = await world.run()
    assert (run['status'], run['error_code'], run['credits_spent']) == ('cancelled', 'cancelled', 0)
    assert world.balance() == 200  # the scores were not stored, so they were not charged


async def test_a_dead_worker_leaves_the_run_unknown_its_step_uncertain_and_its_hold_settled(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    with world.scope(), db.transaction():
        automation = db.execute('SELECT * FROM automations WHERE id = %s', (world.id,)).fetchone()
        queued = service.enqueue(db, automation, datetime.now(UTC), 'manual')
    with world.scope():
        claimed = runner.claim(queued['id'])
        runner._begin(claimed, 0, 1)  # the first step is going when the worker dies
    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour'")

    call_system(SystemReason.DUE_WORK, runner.maintain)
    run = world.row(queued['id'])
    assert (run['status'], run['error_code']) == ('unknown', 'worker_outcome_unknown')
    states = [s['status'] for s in world.steps(queued['id']).values()]
    assert states == ['uncertain', 'skipped', 'skipped', 'skipped']
    assert world.balance() == 200

    with world.scope():
        assert runner.claim(queued['id']) is None  # never replayed


async def test_a_run_is_refused_when_its_resources_went_away_or_the_wallet_is_empty(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    db.execute("UPDATE outreach_campaigns SET status = 'completed'")
    ended = await world.run()
    assert (ended['status'], ended['error_code']) == ('failed', 'invalid_configuration') and vendors.searches == []

    poor = World(db, vendors, credits=0)
    refused = await poor.run()
    assert (refused['status'], refused['error_code']) == ('failed', 'insufficient_credits')
    assert poor.steps(refused['id']) == {}


async def test_two_claims_of_one_run_work_once(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    with world.scope(), db.transaction():
        automation = db.execute('SELECT * FROM automations WHERE id = %s', (world.id,)).fetchone()
        queued = service.enqueue(db, automation, datetime.now(UTC), 'manual')
    with world.scope():
        assert runner.claim(queued['id']) is not None
        assert runner.claim(queued['id']) is None
    assert db.execute("SELECT count(*) AS n FROM credit_transactions WHERE type = 'reservation'").fetchone()['n'] == 1


class _Hubspot:
    def __init__(self):
        self.written: list[str] = []
        self.token = 'token'

    async def write_person(self, token, person):
        self.written.append(person['email'])
        return ok(('contact-1', 'company-1', True))

    async def access_token(self, connection, provider):
        return self.token


async def test_the_hubspot_step_writes_each_contact_once_and_never_without_write_back(db, vendors, monkeypatch):
    fake = _Hubspot()
    monkeypatch.setattr(hubspot.contacts, 'write_person', fake.write_person)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=['find_people', 'qualify', 'find_emails'])
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    first = await world.run()
    assert first['status'] == 'succeeded'
    with world.scope():
        from app.automations.models import AutomationPatch, Definition

        steps = [
            *world.db.execute('SELECT definition FROM automations').fetchone()['definition']['steps'],
            {'type': 'write_hubspot'},
        ]
        definition = Definition.model_validate({'list_id': world.people, 'steps': steps})
        patch = AutomationPatch(definition=definition)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as off:
            service.update(member(db, world.uid), project(db, world.uid), world.id, patch)
        assert off.value.detail['code'] == 'AUTOMATION.HUBSPOT_OFF'
        connect_hubspot(db, world.uid)
        service.update(member(db, world.uid), project(db, world.uid), world.id, patch)
    db.execute('UPDATE automations SET approved_version = version')  # reviewed: this test is about the write
    db.execute('DELETE FROM automation_rows')  # the same two people are taken in again by this version

    run = await world.run()
    assert run['status'] == 'succeeded' and sorted(fake.written) == ['ada@acme.it', 'bob@acme.it']
    assert db.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 2

    db.execute('DELETE FROM automation_rows')
    again = await world.run()
    assert again['status'] == 'succeeded' and len(fake.written) == 2  # remembered: not written twice
    assert world.steps(again['id'])['write_hubspot']['output']['already_written'] == 2


async def test_a_hubspot_failure_after_a_write_is_not_replayed_by_the_step(db, vendors, monkeypatch):
    fake = _Hubspot()
    calls = []

    async def fail_second(token, person):
        calls.append(person['email'])
        return ok(('c', None, False)) if len(calls) == 1 else err('HUBSPOT.HTTP_ERROR', detail='down')

    monkeypatch.setattr(hubspot.contacts, 'write_person', fail_second)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=['find_people', 'qualify', 'find_emails'])
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    connect_hubspot(db, world.uid)
    with world.scope():
        from app.automations.models import AutomationPatch, Definition

        steps = [
            *db.execute('SELECT definition FROM automations').fetchone()['definition']['steps'],
            {'type': 'write_hubspot'},
        ]
        service.update(
            member(db, world.uid),
            project(db, world.uid),
            world.id,
            AutomationPatch(definition=Definition.model_validate({'list_id': world.people, 'steps': steps})),
        )
    db.execute('UPDATE automations SET approved_version = version')  # reviewed: this test is about the failure
    run = await world.run()
    step = world.steps(run['id'])['write_hubspot']
    assert (run['status'], run['error_code'], step['status'], step['attempts']) == (
        'failed',
        'hubspot_failed',
        'failed',
        1,
    )
    assert step['output']['existing'] == 1 and len(calls) == 2
    assert db.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 1


async def test_a_cancel_during_the_hubspot_write_records_who_the_step_wrote(db, vendors, monkeypatch):
    fake = _Hubspot()

    async def write_then_cancel(token, person):
        db.execute('UPDATE automation_runs SET cancel_requested = true')  # lands while HubSpot holds the contact
        return await fake.write_person(token, person)

    monkeypatch.setattr(hubspot.contacts, 'write_person', write_then_cancel)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=['find_people', 'qualify', 'find_emails', 'write_hubspot'], hubspot=True)
    _team(vendors, 'Ada Rossi')
    db.execute('UPDATE automations SET approved_version = version')  # reviewed: a review run writes nobody
    before = world.balance()

    run = await world.run()

    steps = world.steps(run['id'])
    written = steps['write_hubspot']
    assert (run['status'], run['error_code']) == ('cancelled', 'cancelled')
    assert (written['status'], written['output'].get('created'), written['credits']) == ('succeeded', 1, 0)
    assert run['summary']['write_hubspot']['created'] == 1
    assert fake.written == ['ada@acme.it']
    assert db.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 1
    assert before - world.balance() == run['credits_spent'] == 2  # qualify and find_emails, before the cancel


@pytest.mark.parametrize('broken', ['_record', 'write_person'])
async def test_an_error_after_a_hubspot_write_fails_the_step_without_a_retry(db, vendors, monkeypatch, broken):
    fake = _Hubspot()
    record = hubspot._record

    def record_once(user_id, email, written):
        if broken == '_record' and len(fake.written) == 2:
            raise psycopg.errors.OperationalError('the database went away')
        record(user_id, email, written)

    async def write_once(token, person):
        if broken == 'write_person' and fake.written:
            raise RuntimeError('the client broke')
        return await fake.write_person(token, person)

    monkeypatch.setattr(hubspot, '_record', record_once)
    monkeypatch.setattr(hubspot.contacts, 'write_person', write_once)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=['find_people', 'qualify', 'find_emails', 'write_hubspot'], hubspot=True)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    db.execute('UPDATE automations SET approved_version = version')  # reviewed: a review run writes nobody

    run = await world.run()

    step = world.steps(run['id'])['write_hubspot']
    assert (run['status'], run['error_code'], step['status'], step['attempts']) == (
        'failed',
        'hubspot_failed',
        'failed',
        1,
    )
    assert step['output']['created'] == 1
    assert db.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 1


@pytest.fixture
def queued(monkeypatch):
    """The held writes approval hands to a worker: recorded here, run by the test."""
    ids: list[str] = []
    monkeypatch.setattr(held_writes, 'queue', ids.append)
    return ids


async def test_a_run_in_review_writes_nobody_to_hubspot_and_approval_queues_the_write(db, vendors, monkeypatch, queued):
    fake = _Hubspot()
    monkeypatch.setattr(hubspot.contacts, 'write_person', fake.write_person)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(
        db, vendors, steps=['find_people', 'qualify', 'find_emails', 'add_to_campaign', 'write_hubspot'], hubspot=True
    )
    _team(vendors, 'Ada Rossi', 'Bob Neri', 'Cleo Gallo')
    first = await world.run()
    step = world.steps(first['id'])['write_hubspot']
    assert (first['status'], step['status'], step['output']) == ('succeeded', 'succeeded', {'held_for_approval': 3})
    assert fake.written == [] and db.execute('SELECT count(*) AS n FROM crm_contact_writes').fetchone()['n'] == 0
    assert len(world.row(first['id'])['hubspot_row_ids']) == 3

    db.execute(
        "INSERT INTO exclusions (organization_id, project_id, kind, value) VALUES (%s, %s, 'email', 'bob@acme.it')",
        (world.org, project(db, world.uid)),
    )  # Bob was excluded after the run
    other = make_campaign(db, world.uid, status='active')
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, replied_at)'
        " VALUES (%s, %s, 'cleo@acme.it', 'replied', now())",
        (other, world.uid),
    )  # Cleo answered another campaign

    world.approve(first['id'])
    assert fake.written == [] and queued == [str(first['id'])]  # the request wrote nothing: a worker does

    await asyncio.gather(world.write_held(first['id']), world.write_held(first['id']))  # a double approval
    assert fake.written == ['ada@acme.it']
    summary = world.row(first['id'])['summary']['hubspot_after_approval']
    assert (summary['created'], summary['error_code']) == (1, None)
    assert world.row(first['id'])['hubspot_row_ids'] == [] and world.row(first['id'])['hubspot_claimed_at'] is None
    world.approve(first['id'])
    assert queued == [str(first['id'])]  # nothing is held any more: approving again queues nothing
    await world.write_held(first['id'])
    assert fake.written == ['ada@acme.it']


async def test_a_hubspot_write_that_fails_after_approval_stays_held_until_it_is_queued_again(
    db, vendors, monkeypatch, queued
):
    fake = _Hubspot()
    down = [True]

    async def write(token, person):
        return err('HUBSPOT.HTTP_ERROR', detail='down') if down[0] else await fake.write_person(token, person)

    monkeypatch.setattr(hubspot.contacts, 'write_person', write)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(
        db, vendors, steps=['find_people', 'qualify', 'find_emails', 'add_to_campaign', 'write_hubspot'], hubspot=True
    )
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    approved = world.approve(first['id'])
    assert approved['approved_at'] and 'hubspot_after_approval' not in approved['summary']
    await world.write_held(first['id'])
    row = world.row(first['id'])
    assert row['summary']['hubspot_after_approval']['error_code'] == 'hubspot_failed'
    assert len(row['hubspot_row_ids']) == 1 and row['hubspot_claimed_at'] is None and fake.written == []

    down[0] = False
    world.approve(first['id'])
    assert queued == [str(first['id'])] * 2
    await world.write_held(first['id'])
    assert fake.written == ['ada@acme.it'] and world.row(first['id'])['hubspot_row_ids'] == []


async def test_a_claim_that_is_live_stops_a_second_write_and_a_stale_one_does_not(db, vendors, monkeypatch, queued):
    fake = _Hubspot()
    monkeypatch.setattr(hubspot.contacts, 'write_person', fake.write_person)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(
        db, vendors, steps=['find_people', 'qualify', 'find_emails', 'add_to_campaign', 'write_hubspot'], hubspot=True
    )
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    world.approve(first['id'])
    db.execute('UPDATE automation_runs SET hubspot_claimed_at = now() WHERE id = %s', (first['id'],))
    await world.write_held(first['id'])
    assert fake.written == []  # another task holds the people

    db.execute(
        "UPDATE automation_runs SET hubspot_claimed_at = now() - interval '1 hour' WHERE id = %s", (first['id'],)
    )
    await world.write_held(first['id'])  # that task died: the claim is stale
    assert fake.written == ['ada@acme.it']


async def test_approval_checks_the_rules_again_and_only_a_finished_run_in_review_is_approved(db, vendors):

    world = World(db, vendors)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    first = await world.run()
    other = make_campaign(db, world.uid, status='active')
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, replied_at)'
        " VALUES (%s, %s, 'ada@acme.it', 'replied', now())",
        (other, world.uid),
    )  # Ada answered another campaign after her draft was made

    approved = world.approve(first['id'])
    assert approved['summary']['promoted'] == 1
    assert [t for t in world.targets() if t[0].startswith(('ada', 'bob'))] == [
        ('ada@acme.it', 'replied'),
        ('bob@acme.it', 'active'),
    ]  # her draft is gone
    assert world.approve(first['id'])['approved_at'] == approved['approved_at']  # approving twice is the same approval

    with world.scope():
        automation = db.execute('SELECT * FROM automations WHERE id = %s', (world.id,)).fetchone()
        queued = service.enqueue(db, automation, datetime.now(UTC), 'manual')
    with pytest.raises(HTTPException) as refused:
        world.approve(queued['id'])
    assert refused.value.detail['code'] == 'AUTOMATION.NOT_IN_REVIEW'


async def test_cancel_ends_a_queued_run_and_flags_a_running_one(db, vendors):
    world = World(db, vendors)
    scope_user = member(db, world.uid)
    with world.scope():
        automation = db.execute('SELECT * FROM automations WHERE id = %s', (world.id,)).fetchone()
        queued = service.enqueue(db, automation, datetime.now(UTC), 'manual')
        cancelled = service.cancel_run(scope_user, project(db, world.uid), world.id, queued['id'])
    assert (cancelled['status'], cancelled['error_code']) == ('cancelled', 'cancelled')
    with world.scope():
        again = service.enqueue(db, automation, datetime.now(UTC) + timedelta(seconds=1), 'manual')
        runner.claim(again['id'])
        running = service.cancel_run(scope_user, project(db, world.uid), world.id, again['id'])
    assert (running['status'], running['cancel_requested']) == ('running', True)


async def test_an_automation_deleted_mid_run_stops_the_run_and_charges_nothing(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    real = vendors.chat

    async def delete_while_scoring(**kwargs):
        with world.scope():
            service.delete(member(db, world.uid), project(db, world.uid), world.id)
        return await real(**kwargs)

    vendors.chat = delete_while_scoring
    await world.run()
    assert db.execute('SELECT count(*) AS n FROM automation_runs').fetchone()['n'] == 0
    assert world.balance() == 200 and world.targets() == []
    assert db.execute('SELECT count(*) AS n FROM row_charges').fetchone()['n'] == 0


async def test_the_hubspot_grant_checked_is_the_creators_not_the_editors(db, vendors):

    from app.auth.middleware import CurrentUser
    from app.automations.models import AutomationPatch, Definition

    world = World(db, vendors, steps=['qualify'])
    editor = user(db)
    connect_hubspot(db, editor)  # the editor can write to HubSpot; the creator cannot
    admin = CurrentUser(id=editor, org_id=world.org, org_role='admin')
    definition = Definition.model_validate(
        {'list_id': world.people, 'steps': [{'type': 'qualify', 'brief': 'x'}, {'type': 'write_hubspot'}]}
    )
    with world.scope(), pytest.raises(HTTPException) as refused:
        service.update(admin, project(db, world.uid), world.id, AutomationPatch(definition=definition))
    assert refused.value.detail['code'] == 'AUTOMATION.HUBSPOT_OFF'


async def test_a_run_that_dies_after_its_steps_never_enrolls_or_charges_the_same_person_twice(db, vendors, monkeypatch):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    real_finish = runner.finish

    def die(run, status, code):
        raise RuntimeError('worker killed before the close')

    monkeypatch.setattr(runner, 'finish', die)
    with pytest.raises(RuntimeError):
        await world.run()
    monkeypatch.setattr(runner, 'finish', real_finish)
    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour'")
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert world.balance() == 196  # the four charges the steps recorded stay; the rest of the hold went back

    again = await world.run()
    assert (again['status'], again['credits_spent']) == ('succeeded', 0)  # scored and looked up: nothing to pay
    assert world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft')]
    assert world.balance() == 196


async def test_the_drafts_of_a_review_run_that_died_are_promoted_by_the_next_approval(db, vendors, monkeypatch):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    real_finish = runner.finish

    def die(run, status, code):
        raise RuntimeError('worker killed before the close')

    monkeypatch.setattr(runner, 'finish', die)
    with pytest.raises(RuntimeError):
        await world.run()
    monkeypatch.setattr(runner, 'finish', real_finish)
    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour'")
    call_system(SystemReason.DUE_WORK, runner.maintain)
    dead = db.execute('SELECT id, status FROM automation_runs').fetchone()
    assert dead['status'] == 'unknown'
    again = await world.run()  # nobody is new, so it ends at once and is in review too
    assert again['status'] == 'succeeded' and world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft')]
    approved = world.approve(again['id'])
    assert approved['summary']['promoted'] == 2
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'active')]


def _send(db, campaign, email):
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, step, channel, status, recipient, idempotency_key, delivered_at)'
        " SELECT id, owner_id, 1, 'email', 'sent', %s, %s, now() FROM outreach_campaigns WHERE id = %s",
        (email, f'{campaign}:{email}:1', campaign),
    )


HUBSPOT_STEPS = ['find_people', 'qualify', 'find_emails', 'add_to_campaign', 'write_hubspot']


async def test_a_crash_in_the_held_write_keeps_the_people_held_and_says_so(db, vendors, monkeypatch, queued):
    fake = _Hubspot()

    async def crash(token, person):
        raise RuntimeError('worker hit a bug')

    monkeypatch.setattr(hubspot.contacts, 'write_person', crash)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=HUBSPOT_STEPS, hubspot=True)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    world.approve(first['id'])
    with pytest.raises(RuntimeError):
        await world.write_held(first['id'])
    row = world.row(first['id'])
    assert len(row['hubspot_row_ids']) == 1 and row['hubspot_claimed_at'] is None
    assert row['summary']['hubspot_after_approval']['error_code'] == 'write_crashed'

    monkeypatch.setattr(hubspot.contacts, 'write_person', fake.write_person)
    world.approve(first['id'])
    await world.write_held(first['id'])
    assert fake.written == ['ada@acme.it'] and world.row(first['id'])['hubspot_row_ids'] == []


async def test_a_crash_after_a_held_write_keeps_the_count_and_says_it_crashed(db, vendors, monkeypatch, queued):
    fake = _Hubspot()
    record = hubspot._record

    def record_once(user_id, email, written):
        if len(fake.written) == 2:
            raise psycopg.errors.OperationalError('the database went away')
        record(user_id, email, written)

    monkeypatch.setattr(hubspot, '_record', record_once)
    monkeypatch.setattr(hubspot.contacts, 'write_person', fake.write_person)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=HUBSPOT_STEPS, hubspot=True)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    first = await world.run()
    world.approve(first['id'])
    await world.write_held(first['id'])
    row = world.row(first['id'])
    assert len(row['hubspot_row_ids']) == 2 and row['hubspot_claimed_at'] is None
    after = row['summary']['hubspot_after_approval']
    assert (after['error_code'], after['created']) == ('write_crashed', 1)


async def test_the_held_write_is_not_refused_for_a_message_the_automations_own_campaign_sent(
    db, vendors, monkeypatch, queued
):
    fake = _Hubspot()
    monkeypatch.setattr(hubspot.contacts, 'write_person', fake.write_person)
    monkeypatch.setattr(hubspot, '_access_token', fake.access_token)
    world = World(db, vendors, steps=HUBSPOT_STEPS, hubspot=True)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    first = await world.run()
    world.approve(first['id'])
    _send(db, world.campaign, 'ada@acme.it')  # the promoted campaign emailed Ada before the retry
    other = make_campaign(db, world.uid, status='active')
    _send(db, other, 'bob@acme.it')  # another campaign emailed Bob

    await world.write_held(first['id'])
    assert fake.written == ['ada@acme.it']
    assert world.row(first['id'])['summary']['hubspot_after_approval']['refused_since'] == 1


async def test_a_review_run_still_going_when_an_earlier_one_is_approved_can_be_approved_when_it_dies(
    db, vendors, monkeypatch
):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    _team(vendors, 'Ada Rossi', 'Bob Neri')

    def die(run, status, code):
        raise RuntimeError('worker killed before the close')

    real_finish = runner.finish
    monkeypatch.setattr(runner, 'finish', die)
    with pytest.raises(RuntimeError):
        await world.run()  # its claim came before the approval below, so it is in review
    monkeypatch.setattr(runner, 'finish', real_finish)
    world.approve(first['id'])
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'draft')]

    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour' WHERE status = 'running'")
    call_system(SystemReason.DUE_WORK, runner.maintain)
    dead = db.execute("SELECT id FROM automation_runs WHERE status = 'unknown'").fetchone()
    assert world.approve(dead['id'])['summary']['promoted'] == 1
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'active')]


def _edit_cap(world, cap):
    with world.scope():
        service.update(
            member(world.db, world.uid), project(world.db, world.uid), world.id, AutomationPatch(credit_cap=cap)
        )


async def test_an_edit_removes_the_drafts_of_an_unapproved_run_and_the_next_run_takes_them_again(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    world.approve(first['id'])  # version 1
    _edit_cap(world, 60)
    _team(vendors, 'Ada Rossi', 'Fabio Neri')
    second = await world.run()  # version 2, in review: Fabio is a draft nobody approved
    assert world.targets() == [('ada@acme.it', 'active'), ('fabio@acme.it', 'draft')]

    _edit_cap(world, 70)  # version 3
    assert world.targets() == [('ada@acme.it', 'active')]
    assert (
        db.execute('SELECT count(*) AS n FROM automation_rows WHERE run_id = %s', (second['id'],)).fetchone()['n'] == 0
    )

    third = await world.run()
    assert (third['version'], third['review']) == (3, True)
    assert world.targets() == [('ada@acme.it', 'active'), ('fabio@acme.it', 'draft')]
    assert world.approve(third['id'])['summary']['promoted'] == 1
    assert world.targets() == [('ada@acme.it', 'active'), ('fabio@acme.it', 'active')]


async def test_an_edit_keeps_a_draft_that_was_sent_something(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Fabio Neri')
    await world.run()
    target = db.execute("SELECT id FROM outreach_campaign_targets WHERE email = 'fabio@acme.it'").fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient,'
        " idempotency_key) SELECT campaign_id, owner_id, id, 1, 'email', email, 'k' FROM outreach_campaign_targets"
        ' WHERE id = %s',
        (target,),
    )
    _edit_cap(world, 60)
    assert world.targets() == [('fabio@acme.it', 'draft')]


async def test_a_campaign_whose_people_are_drafts_waiting_for_approval_is_not_completed(db, vendors, monkeypatch):
    monkeypatch.setattr(dispatcher.webhooks, 'emit', lambda *args, **kwargs: None)
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    assert world.targets() == [('ada@acme.it', 'draft')]

    def status():
        return db.execute('SELECT status FROM outreach_campaigns WHERE id = %s', (world.campaign,)).fetchone()['status']

    with world.scope():
        assert dispatcher.complete_finished() == 0
    assert status() == 'active'  # the approval that promotes Ada has not come yet
    world.approve(first['id'])
    assert world.targets() == [('ada@acme.it', 'active')]

    other = make_campaign(db, world.uid, status='active')
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status) SELECT id, owner_id, %s, %s'
        ' FROM outreach_campaigns WHERE id = %s',
        ('refill@acme.it', 'draft', other),
    )
    with world.scope():
        dispatcher.complete_finished()
    done = db.execute('SELECT status FROM outreach_campaigns WHERE id = %s', (other,)).fetchone()['status']
    assert done == 'completed'  # any other draft still does not keep a campaign open


async def test_an_edit_keeps_the_drafts_of_a_dead_run_that_an_approval_of_its_version_covered(db, vendors, monkeypatch):
    world = World(db, vendors, campaign='draft')
    _team(vendors, 'Ada Rossi')
    real_finish = runner.finish

    def die(run, status, code):
        raise RuntimeError('worker killed before the close')

    monkeypatch.setattr(runner, 'finish', die)
    with pytest.raises(RuntimeError):
        await world.run()
    monkeypatch.setattr(runner, 'finish', real_finish)
    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour'")
    call_system(SystemReason.DUE_WORK, runner.maintain)
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    second = await world.run()
    world.approve(second['id'])  # version 1 is approved; the campaign is a draft, so both stay drafts
    assert world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft')]

    _edit_cap(world, 60)
    assert world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft')]
    assert db.execute('SELECT count(*) AS n FROM automation_rows').fetchone()['n'] == 2

    # A later approval of the new version moves approved_version on; version 1's approval still covers Ada.
    _team(vendors, 'Ada Rossi', 'Bob Neri', 'Carl Bianchi')
    third = await world.run()
    world.approve(third['id'])
    assert world.targets() == [('ada@acme.it', 'draft'), ('bob@acme.it', 'draft'), ('carl@acme.it', 'draft')]


async def test_an_edit_removes_the_drafts_of_a_run_that_succeeded_unapproved_while_its_version_was_approved(
    db, vendors
):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    real = vendors.chat

    async def approve_while_scoring(**kwargs):
        world.approve(first['id'])
        vendors.chat = real
        return await real(**kwargs)

    vendors.chat = approve_while_scoring
    second = await world.run()
    assert second['review'] and second['status'] == 'succeeded'
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'draft')]

    _edit_cap(world, 60)  # no approval reaches the second run now: Bob leaves and is taken again
    assert world.targets() == [('ada@acme.it', 'active')]
    third = await world.run()
    world.approve(third['id'])
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'active')]


async def test_an_edit_that_moves_the_campaign_removes_covered_drafts_from_the_old_one(db, vendors, monkeypatch):
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    first = await world.run()
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    real_chat, real_finish = vendors.chat, runner.finish

    async def approve_while_scoring(**kwargs):
        world.approve(first['id'])
        vendors.chat = real_chat
        return await real_chat(**kwargs)

    def die(run, status, code):
        raise RuntimeError('worker killed before the close')

    vendors.chat = approve_while_scoring
    monkeypatch.setattr(runner, 'finish', die)
    with pytest.raises(RuntimeError):
        await world.run()  # a review run of the approved version that dies after it added Bob: covered
    monkeypatch.setattr(runner, 'finish', real_finish)
    db.execute("UPDATE automation_runs SET started_at = now() - interval '1 hour' WHERE status = 'running'")
    call_system(SystemReason.DUE_WORK, runner.maintain)
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'draft')]

    moved = make_campaign(db, world.uid, status='active')
    definition = db.execute('SELECT definition FROM automations WHERE id = %s', (world.id,)).fetchone()['definition']
    for step in definition['steps']:
        if step['type'] == 'add_to_campaign':
            step['campaign_id'] = moved
    with world.scope():
        service.update(
            member(db, world.uid),
            project(db, world.uid),
            world.id,
            AutomationPatch(definition=Definition.model_validate(definition)),
        )
    assert world.targets() == [('ada@acme.it', 'active')]  # no approval promotes in the old campaign: Bob leaves
    third = await world.run()
    world.approve(third['id'])
    assert db.execute(
        "SELECT email, status FROM outreach_campaign_targets WHERE campaign_id = %s AND email = 'bob@acme.it'", (moved,)
    ).fetchone() == {'email': 'bob@acme.it', 'status': 'active'}


async def test_a_pause_that_also_edits_removes_the_old_drafts(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Fabio Neri')
    await world.run()
    with world.scope():
        service.update(
            member(db, world.uid), project(db, world.uid), world.id, AutomationPatch(enabled=False, credit_cap=60)
        )
    assert world.targets() == []


async def test_a_run_going_during_an_edit_loses_its_drafts_when_it_ends(db, vendors):
    world = World(db, vendors)
    _team(vendors, 'Fabio Neri')
    real = vendors.chat

    async def edit_while_scoring(**kwargs):
        _edit_cap(world, 60)
        return await real(**kwargs)

    vendors.chat = edit_while_scoring
    run = await world.run()
    assert (run['status'], run['version']) == ('succeeded', 1)
    assert world.targets() == []  # its drafts came after the edit; the end of the run removed them
    assert db.execute('SELECT count(*) AS n FROM automation_rows').fetchone()['n'] == 0


async def test_drafts_of_a_version_another_run_approved_do_not_keep_a_campaign_open(db, vendors, monkeypatch):
    monkeypatch.setattr(dispatcher.webhooks, 'emit', lambda *args, **kwargs: None)
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    await world.run()  # a review run that is never approved itself
    db.execute('UPDATE automations SET approved_version = version')  # another run of its version was
    with world.scope():
        assert dispatcher.complete_finished() == 1


async def test_a_campaign_completes_once_the_automation_that_held_it_open_is_deleted(db, vendors, monkeypatch):
    monkeypatch.setattr(dispatcher.webhooks, 'emit', lambda *args, **kwargs: None)
    world = World(db, vendors)
    _team(vendors, 'Ada Rossi')
    await world.run()
    with world.scope():
        assert dispatcher.complete_finished() == 0
        service.delete(member(db, world.uid), project(db, world.uid), world.id)
        assert dispatcher.complete_finished() == 1


async def test_a_person_with_no_value_waits_as_a_draft_after_an_approval_and_after_a_later_run(db, vendors):
    world = World(db, vendors)
    steps = [STEP | {'body': 'Hi {{firstName}}, {{recentNews}}'}]
    db.execute(
        'UPDATE outreach_campaigns SET fields = %s, steps = %s WHERE id = %s',
        (Jsonb([NEWS]), Jsonb(steps), world.campaign),
    )
    _team(vendors, 'Ada Rossi', 'Bob Neri')
    first = await world.run()
    db.execute(
        "UPDATE list_rows SET cells = cells || '{\"recent_news\": \"a new office\"}' WHERE cells ->> 'full_name' = 'Ada Rossi'"
    )

    assert world.approve(first['id'])['summary']['promoted'] == 1
    assert world.targets() == [('ada@acme.it', 'active'), ('bob@acme.it', 'draft')]

    _team(vendors, 'Cleo Gallo')
    assert (await world.run())['review'] is False
    missing = {
        r['email']: r['missing']
        for r in db.execute("SELECT email, personalization -> 'missing' AS missing FROM outreach_campaign_targets")
    }
    assert dict(world.targets())['cleo@acme.it'] == 'draft'
    assert missing == {'ada@acme.it': [], 'bob@acme.it': ['recentNews'], 'cleo@acme.it': ['recentNews']}
