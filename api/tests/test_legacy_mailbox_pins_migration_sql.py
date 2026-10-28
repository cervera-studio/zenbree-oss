"""20261019093817_legacy_mailbox_pins.sql on the conversations that the relink of #1069 left held."""

import os
import shutil

import pytest

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import empty_database, link, project, sender
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: legacy mailbox pins migration NOT run')
NAME = '20261019093817_legacy_mailbox_pins.sql'


@pytest.fixture
def before(tmp_path):
    for f in MIGRATIONS.glob('*.sql'):
        if f.name < NAME:
            shutil.copy(f, tmp_path / f.name)
    with empty_database() as c:
        assert migrate(c, tmp_path)
        shutil.copy(MIGRATIONS / NAME, tmp_path / NAME)
        yield c, tmp_path


def _client(c, uid, name: str) -> str:
    return str(
        c.execute(
            'INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, %s)'
            ' RETURNING id',
            (uid, uid, name),
        ).fetchone()['id']
    )


def _campaign(c, uid, pid: str, status: str = 'active') -> str:
    return str(
        c.execute(
            """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps)
               VALUES (default_organization(%s), %s, %s, 'C', %s, '[]') RETURNING id""",
            (uid, pid, uid, status),
        ).fetchone()['id']
    )


def _person(c, uid, campaign: str, email: str, account: str, status: str = 'active') -> None:
    c.execute(
        """INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, status, sender_id, sender_pinned_at)
               VALUES (%s, %s, %s, %s, %s, '2026-10-01T08:00:00Z')""",
        (campaign, uid, email, status, account),
    )


def test_a_held_conversation_moves_to_the_clients_account_of_its_mailbox(before):
    c, directory = before
    uid = _user(c)
    pid, other = project(c, uid), _client(c, uid, 'Client 2')
    legacy = sender(c, uid, projects=[other], account_label='a@acme.test')
    gone = sender(
        c, uid, projects=[other], account_label='a@acme.test', provider_account_id='sub-old', status='disconnected'
    )
    link(c, pid, gone)
    account = sender(c, uid, projects=[pid], account_label=' A@acme.test', provider_account_id='sub-a')
    no_twin = sender(c, uid, projects=[other], account_label='m@acme.test')
    sender(c, uid, projects=[pid], account_label='b@acme.test', provider_account_id='sub-b')
    live, stopped, kept = _campaign(c, uid, pid), _campaign(c, uid, pid, 'stopped'), _campaign(c, uid, other)
    _person(c, uid, live, 'anna@client.test', legacy)
    _person(c, uid, live, 'cleo@client.test', legacy, 'replied')
    _person(c, uid, live, 'eve@client.test', no_twin)
    _person(c, uid, stopped, 'fay@client.test', legacy)
    _person(c, uid, kept, 'dora@client.test', legacy)

    assert migrate(c, directory) == [NAME]

    pins = {
        r['email']: str(r['sender_id']) for r in c.execute('SELECT email, sender_id FROM outreach_campaign_targets')
    }
    assert pins == {
        'anna@client.test': account,
        'cleo@client.test': account,
        'eve@client.test': no_twin,
        'fay@client.test': account,
        'dora@client.test': legacy,
    }
    pools = c.execute('SELECT campaign_id::text, sender_id::text FROM outreach_campaign_senders').fetchall()
    assert {(r['campaign_id'], r['sender_id']) for r in pools} == {(live, account)}
