"""HubSpot opt-outs in an automation follow the person's other aliases (#1013, #937), as campaign enrollment does:
the run refuses the person before any credit, and the email step neither stores nor bills an address of an identity
that opted out. With a campaign, the email step refuses the opt-outs of the creator and of the campaign owner (#1122)."""

import os

import pytest
from psycopg.types.json import Jsonb

from app.agent.tools.handlers import enrich_email
from app.audience.link import mark_rows
from app.automations import gate, qualify, runner
from app.automations.models import Definition
from tests.automations_support import connect_hubspot, database, make_list, teammate
from tests.conftest import project
from tests.test_automations_run_sql import Vendors, World

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')
db = pytest.fixture(database)

ADA = 'https://linkedin.com/in/ada-rossi'
BEA = 'https://linkedin.com/in/bea-neri'
BEA_OTHER = 'https://linkedin.com/in/bea-n'
CLEO = 'https://linkedin.com/in/cleo-bassi'


@pytest.fixture
def vendors(monkeypatch):
    fake = Vendors()
    monkeypatch.setattr(enrich_email, 'handle', fake.enrich)
    monkeypatch.setattr(qualify, 'get_provider', lambda: fake)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(runner.asyncio, 'sleep', no_wait)
    return fake


def _linked(db, uid, rows: list[dict]) -> list[str]:
    """Rows of a list of the project, linked into identities as the link task does."""
    _, ids = make_list(db, uid, 'person', rows)
    mark_rows(db, ids)
    for marked in db.execute('SELECT list_row_id, source FROM audience_link_queue').fetchall():
        db.execute('SELECT audience_link_row(%s, %s)', (marked['list_row_id'], marked['source']))
    db.execute('DELETE FROM audience_link_queue')
    return ids


def _opted_out(db, uid, email):
    db.execute(
        "INSERT INTO crm_gate_checks (owner_id, email, reason) VALUES (%s, %s, 'HUBSPOT_OPTED_OUT')", (uid, email)
    )


async def test_an_opt_out_refuses_the_other_aliases_of_the_person_in_a_run_with_no_campaign(db, vendors):
    world = World(db, vendors, steps=['find_emails'], hubspot=True)
    # Ada's profile and the address she opted out with are one identity: the run refuses her before any credit.
    # Bea's two addresses are one identity: only the address the email step finds tells who she is.
    _linked(
        db,
        world.uid,
        [
            {'full_name': 'Ada Rossi', 'email': 'ada@acme.it', 'linkedin_url': ADA},
            {'full_name': 'Bea Neri', 'email': 'bea@acme.it', 'linkedin_url': BEA_OTHER},
            {'full_name': 'Bea N', 'email': 'bea.neri@acme.it', 'linkedin_url': BEA_OTHER},
        ],
    )
    _opted_out(db, world.uid, 'ada@acme.it')
    _opted_out(db, world.uid, 'bea@acme.it')
    for i, cells in enumerate(
        [{'full_name': 'Ada Rossi', 'linkedin_url': ADA}, {'full_name': 'Bea Neri', 'linkedin_url': BEA}]
    ):
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s)',
            (world.people, Jsonb(cells), f'{world.people}-{i}'),
        )
    vendors.emails = {'Ada Rossi': 'ada.rossi@acme.it', 'Bea Neri': 'bea.neri@acme.it'}
    before = world.balance()

    run = await world.run()

    steps = world.steps(run['id'])
    assert run['summary']['taken']['refused_before_any_credit'] == {'excluded': 1}
    assert vendors.looked_up == ['Bea Neri']
    found = steps['find_emails']
    assert (found['output']['refused_by_rules'], found['credits']) == (1, 0)
    assert found['output']['with_contact'] == 0  # Bea's profile no longer goes on to a LinkedIn step
    assert world.balance() == before
    stored = db.execute("SELECT cells ->> 'email' AS email FROM list_rows WHERE list_id = %s", (world.people,))
    assert {r['email'] for r in stored} == {None}

    no_campaign = Definition.model_validate({'list_id': world.people, 'steps': [{'type': 'write_hubspot'}]})
    refused = gate.rescreen(db, project(db, world.uid), world.uid, no_campaign)
    assert (refused({'linkedin_url': ADA}), refused({'linkedin_url': BEA})) == ('excluded', None)


async def test_the_email_step_refuses_the_opt_outs_of_a_teammate_who_owns_the_campaign(db, vendors):
    world = World(db, vendors, steps=['find_emails', 'add_to_campaign'], hubspot=True)
    mate = teammate(db, world.org)
    connect_hubspot(db, mate)
    db.execute('UPDATE outreach_campaigns SET owner_id = %s WHERE id = %s', (mate, world.campaign))
    bea_other_email = 'bea@acme.it'
    _linked(
        db,
        world.uid,
        [
            {'full_name': 'Bea N', 'email': bea_other_email, 'linkedin_url': BEA_OTHER},
            {'full_name': 'Bea N', 'email': 'bea.neri@acme.it', 'linkedin_url': BEA_OTHER},
        ],
    )
    _opted_out(db, mate, 'ada.rossi@acme.it')
    _opted_out(db, mate, bea_other_email)
    _opted_out(db, world.uid, 'cleo@acme.it')
    for i, cells in enumerate(
        [
            {'full_name': 'Ada Rossi', 'linkedin_url': ADA},
            {'full_name': 'Bea Neri', 'linkedin_url': BEA},
            {'full_name': 'Cleo Bassi', 'linkedin_url': CLEO},
        ]
    ):
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s)',
            (world.people, Jsonb(cells), f'{world.people}-{i}'),
        )
    vendors.emails = {'Ada Rossi': 'ada.rossi@acme.it', 'Bea Neri': 'bea.neri@acme.it', 'Cleo Bassi': 'cleo@acme.it'}
    before = world.balance()

    run = await world.run()

    found = world.steps(run['id'])['find_emails']
    assert sorted(vendors.looked_up) == ['Ada Rossi', 'Bea Neri', 'Cleo Bassi']
    assert (found['output']['refused_by_rules'], found['credits'], found['output']['with_contact']) == (3, 0, 0)
    assert world.balance() == before
    stored = db.execute("SELECT cells ->> 'email' AS email FROM list_rows WHERE list_id = %s", (world.people,))
    assert {r['email'] for r in stored} == {None}
    assert world.targets() == []
