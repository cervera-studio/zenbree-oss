"""Instantly events (#930): the fact store against real SQL. Needs TEST_DATABASE_URL."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.core.scope import acting_for
from app.integrations.instantly_sync import mapping, outcomes, store
from tests.conftest import member, migrated_database, project, scoped_conn
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: Instantly events SQL test NOT run')

WS = 'ws-1'
CAMPAIGN = str(uuid.uuid4())
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def db():
    with migrated_database() as (c, _conninfo):
        yield c


def seed(c, emails=('ana@acme.test',), *, workspace=WS, campaign=CAMPAIGN, outcome='accepted') -> store.Binding:
    """A user's project with a connected workspace and an accepted batch of ``emails``."""
    uid = _user(c)
    org, pid = member(c, uid).org_id, project(c, uid)
    conn_id = c.execute(
        """INSERT INTO instantly_connections (organization_id, project_id, connected_by, name, workspace_id)
           VALUES (%s, %s, %s, 'Main', %s) RETURNING id::text""",
        (org, pid, uid, workspace),
    ).fetchone()['id']
    eid = c.execute(
        """INSERT INTO instantly_enrollments (organization_id, project_id, connection_id, created_by, workspace_id,
               campaign_id, mapping, status) VALUES (%s, %s, %s, %s, %s, %s, '{}', 'completed') RETURNING id""",
        (org, pid, conn_id, uid, workspace, campaign),
    ).fetchone()['id']
    for email in emails:
        c.execute(
            """INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id, email,
                   outcome) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (org, pid, eid, workspace, campaign, email, outcome),
        )
    c.execute(
        'INSERT INTO instantly_sync_state (connection_id, organization_id, project_id, workspace_id)'
        ' VALUES (%s, %s, %s, %s)',
        (conn_id, org, pid, workspace),
    )
    return store.Binding(organization_id=org, project_id=pid, connection_id=conn_id, workspace_id=workspace)


def record(c, binding, *items) -> store.Recorded:
    with acting_for(binding.organization_id, 'test'), scoped_conn(c)() as conn:
        return store.record(conn, binding, list(items))


def hook(event_type, email='ana@acme.test', *, at=NOW, step=1, workspace=WS, **extra):
    payload = {
        'event_type': event_type,
        'timestamp': at.isoformat(),
        'workspace': workspace,
        'campaign_id': CAMPAIGN,
        'lead_email': email,
        'step': step,
        **extra,
    }
    return mapping.from_webhook(payload, now=NOW)


def email(ue_type, ref, email_='ana@acme.test', *, at=NOW, auto=0, step=None):
    item = {
        'id': ref,
        'message_id': f'<{ref}@mail>',
        'ue_type': ue_type,
        'lead': email_,
        'campaign_id': CAMPAIGN,
        'timestamp_email': at.isoformat(),
        'is_auto_reply': auto,
        'step': step,
    }
    return mapping.from_email(item, now=NOW)


def facts(c, binding) -> list[dict]:
    return c.execute(
        'SELECT * FROM instantly_events WHERE connection_id = %s ORDER BY changed', (binding.connection_id,)
    ).fetchall()


def test_a_replayed_delivery_is_recorded_once(db):
    b = seed(db)
    first = record(db, b, hook('email_sent', email=' Ana@Acme.TEST '))
    again = record(db, b, hook('email_sent'), hook('email_sent'))
    assert len(first.changed) == 1 and not again.changed and again.duplicate == 2
    assert [f['kind'] for f in facts(db, b)] == ['sent']
    assert record(db, b, hook('email_sent', step=2)).changed  # another step is another send


@pytest.mark.parametrize('order', ['webhook_first', 'api_first'])
def test_a_send_seen_by_the_webhook_and_the_api_is_one_fact_with_both_ids(db, order):
    b = seed(db)
    push, pull = hook('email_sent', at=NOW), email(1, 'em-1', at=NOW + timedelta(seconds=40), step=1)
    first, second = (push, pull) if order == 'webhook_first' else (pull, push)
    kept = record(db, b, first).changed
    assert record(db, b, second).changed == kept  # the second source enriches the first's row: same id
    [row] = facts(db, b)
    assert row['push_key'] and row['provider_ref'] == 'em-1' and row['message_id'] == '<em-1@mail>'
    assert row['time_basis'] == 'event'  # the webhook's time is the event's


def test_a_late_send_never_revives_a_bounced_lead(db):
    b = seed(db)
    status = mapping.from_lead({'email': 'ana@acme.test', 'status': -1}, CAMPAIGN, now=NOW)
    record(db, b, *status)
    record(db, b, hook('email_bounced', at=NOW - timedelta(hours=3)))  # the exact time arrives later
    record(db, b, hook('email_sent', at=NOW - timedelta(hours=1), step=3))
    [bounce] = [f for f in facts(db, b) if f['kind'] == 'bounced']
    assert bounce['time_basis'] == 'event' and bounce['occurred_at'] == NOW - timedelta(hours=3)


def test_a_label_is_recorded_when_it_changes_not_when_a_second_source_repeats_it(db):
    b = seed(db)
    earlier = NOW - timedelta(days=1)
    record(db, b, hook('lead_interested', at=earlier))
    lead = {'email': 'ana@acme.test', 'status': 1, 'lt_interest_status': 1}
    record(db, b, *mapping.from_lead(lead | {'timestamp_last_interest_change': earlier.isoformat()}, CAMPAIGN, now=NOW))
    later = NOW.isoformat()
    record(
        db,
        b,
        *mapping.from_lead(
            {**lead, 'lt_interest_status': -1, 'timestamp_last_interest_change': later}, CAMPAIGN, now=NOW
        ),
    )
    assert [f['label'] for f in facts(db, b)] == ['interested', 'not_interested']


def test_a_custom_label_is_counted_not_guessed(db):
    b = seed(db)
    lead = {'email': 'ana@acme.test', 'lt_interest_status': 77, 'timestamp_last_interest_change': NOW.isoformat()}
    done = record(db, b, *mapping.from_lead(lead, CAMPAIGN, now=NOW))
    assert done.ignored == {'unmapped': 1} and not facts(db, b)
    assert db.execute('SELECT ignored FROM instantly_sync_state').fetchone()['ignored'] == {'unmapped': 1}


def test_another_projects_lead_in_a_shared_workspace_is_not_recorded(db):
    mine = seed(db, ('ana@acme.test',))
    theirs = seed(db, ('bob@acme.test',), campaign=str(uuid.uuid4()))
    done = record(db, mine, hook('reply_received', email='bob@acme.test'))
    assert done.ignored == {'unknown_lead': 1}
    assert not facts(db, mine) and not facts(db, theirs)


def test_a_payload_of_another_workspace_is_not_recorded(db):
    b = seed(db)
    assert record(db, b, hook('email_sent', workspace='ws-other')).ignored == {'workspace': 1}


def test_a_lead_instantly_never_took_is_not_recorded(db):
    b = seed(db, outcome='skipped')
    assert record(db, b, hook('email_sent')).ignored == {'unknown_lead': 1}


def test_a_fact_cannot_be_rewritten(db):
    b = seed(db)
    record(db, b, email(2, 'em-6', auto=0))
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE instantly_events SET reply_nature = 'auto'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE instantly_events SET kind = 'sent'")


def test_the_feed_pages_by_change_and_leaves_waiting_replies_out(db):
    b = seed(db, ('ana@acme.test', 'bob@acme.test'))
    record(db, b, email(1, 'em-0', step=1), hook('reply_received', email='bob@acme.test'))
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        page = outcomes.changes(conn, b.project_id, after=0, limit=10)
    assert [f.kind for f in page.items] == ['sent']
    record(db, b, email(2, 'em-7', 'bob@acme.test', auto=0))
    with acting_for(b.organization_id, 'test'), scoped_conn(db)() as conn:
        later = outcomes.changes(conn, b.project_id, after=page.next_after, limit=10)
    assert [(f.kind, f.reply_nature) for f in later.items] == [('reply', 'human')]
