from datetime import UTC, datetime

import pytest

from app.integrations.instantly_store import HELD
from app.outreach import stop_outbox
from app.outreach.stop_outbox import ConfirmedByHand, Later, Refused, Retry, Stop, StopStatus
from tests.conftest import project
from tests.test_holds_sql import OUTCOMES, lead
from tests.test_holds_sql import db as db
from tests.test_tools_sql import _user

NOW = datetime(2026, 10, 17, 9, tzinfo=UTC)


def _request(db, pid, emails, source=None) -> int:
    return stop_outbox.request(
        db, project_id=pid, emails=emails, reason='human_reply', cause='inbound:test', source_lead_id=source
    )


def test_a_stop_is_queued_once_per_held_lead_and_the_source_lead_is_kept(db):
    pid, other = project(db, _user(db)), project(db, _user(db))
    leads = {o: lead(db, pid, f'{o}@acme.test', o) for o in OUTCOMES}
    db.execute("UPDATE instantly_leads SET campaign_id = 'own'")
    lead(db, other, 'accepted@acme.test')
    emails = [f'{o}@acme.test' for o in OUTCOMES]
    assert _request(db, pid, emails, source=leads['accepted']) == len(HELD)
    assert _request(db, pid, emails) == 0
    rows = db.execute('SELECT lead_id::text, project_id::text, status, keep_lead FROM outreach_stops').fetchall()
    assert {(r['lead_id'], r['status'], r['keep_lead']) for r in rows} == {
        (leads[o], 'pending', o == 'accepted') for o in HELD
    }
    assert {r['project_id'] for r in rows} == {pid}


def _stop(db) -> Stop:
    pid = project(db, _user(db))
    lead(db, pid, 'ann@acme.test')
    _request(db, pid, ['ann@acme.test'])
    row = db.execute('SELECT id::text, status, attempts FROM outreach_stops').fetchone()
    return Stop(row['id'], StopStatus(row['status']), row['attempts'])


def test_settle_writes_only_over_the_row_it_read(db):
    read = _stop(db)
    moved = stop_outbox.settle(db, read, Later('INSTANTLY.UNREACHABLE'), NOW)
    assert moved == Stop(read.id, StopStatus.PENDING, 1)
    assert stop_outbox.settle(db, read, Refused('INSTANTLY.STOP_SCOPE'), NOW) is None
    assert stop_outbox.settle(db, moved, Retry(), NOW) is None
    failed = stop_outbox.settle(db, moved, Refused('INSTANTLY.STOP_SCOPE'), NOW)
    assert failed == Stop(read.id, StopStatus.FAILED, 2)
    row = db.execute('SELECT last_error_code, next_attempt_at FROM outreach_stops').fetchone()
    assert row == {'last_error_code': 'INSTANTLY.STOP_SCOPE', 'next_attempt_at': NOW + stop_outbox.backoff(0)}


@pytest.mark.parametrize('by_hand', [True, False])
def test_a_failed_stop_leaves_by_the_users_retry_or_confirmation(db, by_hand):
    read = _stop(db)
    failed = stop_outbox.settle(db, read, Refused('INSTANTLY.STOP_SCOPE'), NOW)
    assert failed is not None
    user = _user(db)
    done = stop_outbox.settle(db, failed, ConfirmedByHand(user) if by_hand else Retry(), NOW)
    row = db.execute(
        'SELECT status, attempts, confirmed_by, confirmed_user_id::text AS u FROM outreach_stops'
    ).fetchone()
    if by_hand:
        assert row == {'status': 'confirmed', 'attempts': 1, 'confirmed_by': 'user', 'u': user}
        assert done is not None and stop_outbox.settle(db, done, Retry(), NOW) is None
    else:
        assert row == {'status': 'pending', 'attempts': 0, 'confirmed_by': None, 'u': None}
