"""The send status machine against the real table.

Needs TEST_DATABASE_URL; copies a throwaway database from the migrated api/db template.
"""

import re

import pytest

from app.core.scope import acting_for
from app.outreach import dispatcher, send_states
from tests import test_dispatcher_sql
from tests.test_dispatcher_sql import DB_URL, NOW, _campaign
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: send states SQL test NOT run')


def _send(c, status: str) -> str:
    uid = _user(c)
    cid = _campaign(c, uid)
    t = c.execute('SELECT id, owner_id FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchone()
    return c.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               idempotency_key, delivered_at)
           VALUES (%s, %s, %s, 1, 'email', %s, 'anna@client.test', gen_random_uuid()::text,
                   CASE WHEN %s = 'sent' THEN %s END) RETURNING id""",
        (cid, t['owner_id'], t['id'], status, status, NOW),
    ).fetchone()['id']


def _status(c, send_id) -> dict:
    return c.execute('SELECT status, error_code FROM outreach_campaign_sends WHERE id = %s', (send_id,)).fetchone()


def _beat(step, s: dict, *args, **kwargs) -> None:
    with acting_for(str(s['organization_id']), 'campaign beat'):
        step(s, *args, **kwargs)


def _row(c, send_id) -> dict:
    return c.execute(
        """SELECT s.id, s.organization_id, s.target_id, s.step, s.status, t.next_step FROM outreach_campaign_sends s
           JOIN outreach_campaign_targets t ON t.id = s.target_id WHERE s.id = %s""",
        (send_id,),
    ).fetchone()


def test_the_table_names_exactly_the_states_the_check_allows(db):
    check = db.execute(
        """SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
           WHERE conrelid = 'outreach_campaign_sends'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%%status = ANY%%'"""
    ).fetchone()['def']
    assert set(re.findall(r"'(\w+)'::text", check)) == send_states.ALLOWED.keys()


def test_a_forbidden_move_changes_no_row(db):
    send_id = _send(db, 'sent')
    assert send_states.move(db, send_id, ('sending',), 'approved', set_='error_code = %s', params=('X',)) is False
    assert _status(db, send_id) == {'status': 'sent', 'error_code': None}


def test_an_allowed_move_changes_its_row(db):
    send_id = _send(db, 'sending')
    assert send_states.move(db, send_id, ('sending',), 'failed', set_='error_code = %s', params=('X',)) is True
    assert _status(db, send_id) == {'status': 'failed', 'error_code': 'X'}


def test_keep_sending_notes_the_code_only_on_a_send_in_flight(db):
    flying, done = _send(db, 'sending'), _send(db, 'sent')
    assert send_states.keep_sending(db, flying, 'UNKNOWN_OUTCOME') is True
    assert send_states.keep_sending(db, done, 'UNKNOWN_OUTCOME') is False
    assert _status(db, flying) == {'status': 'sending', 'error_code': 'UNKNOWN_OUTCOME'}
    assert _status(db, done) == {'status': 'sent', 'error_code': None}


def test_a_stale_check_before_the_claim_leaves_a_send_in_flight_alone(db):
    """Two beats read the same approved send; one claims it, the other's late exclusion must not skip it."""
    send_id = _send(db, 'sending')
    _beat(dispatcher._settle, _row(db, send_id), 'skipped', 'EXCLUDED', claimed=False)
    assert _status(db, send_id) == {'status': 'sending', 'error_code': None}


def test_a_send_that_did_not_move_does_not_advance_its_person(db):
    send_id = _send(db, 'skipped')
    _beat(dispatcher._linkedin_done, _row(db, send_id), 'sent', None, at=NOW)
    assert (_row(db, send_id)['status'], _row(db, send_id)['next_step']) == ('skipped', 1)


def test_a_settled_send_advances_its_person(db):
    send_id = _send(db, 'sending')
    _beat(dispatcher._linkedin_done, _row(db, send_id), 'sent', None, at=NOW)
    assert (_row(db, send_id)['status'], _row(db, send_id)['next_step']) == ('sent', 2)
