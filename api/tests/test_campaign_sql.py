"""Campaign constraints and replay on a disposable PostgreSQL schema."""

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.conftest import project
from tests.test_agent import DB_URL, _user
from tests.test_agent import db as db

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: campaign SQL tests NOT run')


def campaign(conn, uid):
    return conn.execute(
        'INSERT INTO outreach_campaigns(organization_id, project_id, owner_id, name) VALUES (default_organization(%s), %s, %s, %s) RETURNING *',
        (uid, project(conn, uid), uid, 'Test campaign'),
    ).fetchone()


def target(conn, campaign_id, owner_id, email='person@example.test', linkedin_url=''):
    return conn.execute(
        'INSERT INTO outreach_campaign_targets(campaign_id, owner_id, email, linkedin_url)'
        ' VALUES (%s, %s, %s, %s) RETURNING *',
        (campaign_id, owner_id, email, linkedin_url),
    ).fetchone()


def test_owner_identity_and_send_constraints(db):
    uid, other_uid = _user(db), _user(db)
    own, other = campaign(db, uid), campaign(db, other_uid)
    assert own['status'] == 'draft' and own['approve_before_sending']
    assert own['block_active_duplicates'] and own['block_prior_contacts']
    assert own['steps'] == []
    person = target(db, own['id'], uid)
    assert (person['status'], person['source'], person['next_step']) == ('draft', 'manual', 1)
    db.execute('UPDATE outreach_campaigns SET approve_before_sending = false WHERE id = %s', (own['id'],))
    refill = db.execute(
        'INSERT INTO outreach_campaign_targets(campaign_id, owner_id, email, source)'
        " VALUES (%s, %s, 'refill@example.test', 'refill') RETURNING status",
        (own['id'], uid),
    ).fetchone()
    assert (
        refill['status'] == 'draft'
    )  # the column default is draft: an enrollment path that wants a running campaign to contact them sets the status
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        target(db, own['id'], other_uid, 'other-owner@example.test')
    with pytest.raises(psycopg.errors.UniqueViolation):
        target(db, own['id'], uid, ' PERSON@example.test ')
    # Email-only targets do not all collide on the absent LinkedIn URL.
    target(db, own['id'], uid, 'second@example.test')
    target(db, other['id'], other_uid)  # uniqueness is campaign scoped
    target(db, own['id'], uid, None, 'https://www.linkedin.com/in/ada/')
    with pytest.raises(psycopg.errors.UniqueViolation):
        target(db, own['id'], uid, None, 'http://linkedin.com/in/ADA?utm_source=test#about')
    with pytest.raises(psycopg.errors.CheckViolation):
        target(db, own['id'], uid, None)

    send_sql = (
        'INSERT INTO outreach_campaign_sends(campaign_id, owner_id, target_id, step, channel,'
        ' recipient, idempotency_key) VALUES (%s, %s, %s, 1, %s, %s, %s) RETURNING *'
    )
    params = (own['id'], uid, person['id'], 'email', person['email'], f'{own["id"]}:{person["id"]}:1')
    sent = db.execute(send_sql, params).fetchone()
    assert sent['status'] == 'awaiting_approval' and sent['body'] is None
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(send_sql, params)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(send_sql, (*params[:-1], 'a-different-key'))  # same target/step still cannot be claimed twice
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(send_sql, (other['id'], other_uid, person['id'], 'email', person['email'], 'wrong-campaign'))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(send_sql, (own['id'], other_uid, None, 'email', person['email'], 'wrong-owner'))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE outreach_campaign_sends SET status = 'sent' WHERE id = %s", (sent['id'],))
    db.execute(
        "UPDATE outreach_campaign_sends SET status = 'sent', delivered_at = now(), body = '' WHERE id = %s",
        (sent['id'],),
    )
    db.execute('DELETE FROM outreach_campaign_targets WHERE id = %s', (person['id'],))
    ledger = db.execute('SELECT * FROM outreach_campaign_sends WHERE id = %s', (sent['id'],)).fetchone()
    assert ledger['target_id'] is None and ledger['body'] == ''
    assert ledger['recipient'] == person['email'] and ledger['idempotency_key'] == params[-1]
    db.execute('DELETE FROM "user" WHERE id = %s', (uid,))
    # #333: the organization's campaign and its ledger outlive their creator, who becomes nobody.
    assert db.execute('SELECT owner_id FROM outreach_campaign_sends').fetchall() == [{'owner_id': None}]
    assert db.execute('SELECT count(*) AS n FROM outreach_campaigns').fetchone()['n'] == 2


def test_sql_step_limit_and_window_guards(db):
    uid = _user(db)
    for invalid in ({}, None, [None] * 13):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                'INSERT INTO outreach_campaigns(organization_id, project_id, owner_id, name, steps) VALUES (default_organization(%s), %s, %s, %s, %s)',
                (uid, project(db, uid), uid, 'Bad steps', Jsonb(invalid)),
            )
    valid = campaign(db, uid)
    for assignment in (
        'send_window_start_hour = 18',
        'send_window_end_hour = 25',
        "send_window_days = '{}'",
        'send_window_days = ARRAY[7]',
        'send_window_days = ARRAY[NULL]::smallint[]',
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(f'UPDATE outreach_campaigns SET {assignment} WHERE id = %s', (valid['id'],))
    db.execute('UPDATE outreach_campaigns SET steps = %s WHERE id = %s', (Jsonb([{}] * 12), valid['id']))
    # SQL caps storage; validate_steps owns detailed shape at the application boundary.
