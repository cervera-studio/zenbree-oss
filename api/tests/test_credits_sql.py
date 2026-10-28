"""Credit functions against a real Postgres. Needs TEST_DATABASE_URL; each run
copies a throwaway database from the migrated api/db template and drops it."""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from tests.conftest import migrated_database, scoped_conn

DB_URL = os.environ.get('TEST_DATABASE_URL')

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: SQL credit test NOT run')


@pytest.fixture
def conn():
    with migrated_database() as (c, _):
        yield c


def _user(c) -> uuid.UUID:
    uid = uuid.uuid4()
    cols = {
        r['column_name']
        for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'user' AND table_schema = current_schema()"
        )
    }
    # Fill whatever NOT NULL columns Better Auth declares.
    now = datetime.now(UTC)
    values = {
        'id': uid,
        'email': f'{uid}@x.test',
        'name': 'T',
        'emailVerified': False,
        'createdAt': now,
        'updatedAt': now,
    }
    use = {k: v for k, v in values.items() if k in cols}
    names = ', '.join(f'"{k}"' for k in use)
    c.execute(f'INSERT INTO "user" ({names}) VALUES ({", ".join(["%s"] * len(use))})', list(use.values()))
    # the signup hook: their own organization's wallet
    c.execute(
        'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (default_organization(%s), %s, 50)',
        (uid, uid),
    )
    return uid


def _org(c, uid) -> uuid.UUID:
    return c.execute('SELECT default_organization(%s) AS id', (uid,)).fetchone()['id']


def _balance(c, org) -> int:
    return c.execute('SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (org,)).fetchone()[
        'credits_remaining'
    ]


def test_reserve_commit_release_settle_reap(conn, monkeypatch, balance_events):
    from app import worker

    c = conn
    uid = _user(c)
    org = _org(c, uid)
    assert _balance(c, org) == 50  # the wallet _org opens

    rid = c.execute('SELECT reserve_credits(%s, %s, 10) AS r', (org, uid)).fetchone()['r']
    assert rid and _balance(c, org) == 40
    assert c.execute('SELECT commit_reservation(%s) AS ok', (rid,)).fetchone()['ok'] is True
    assert c.execute('SELECT commit_reservation(%s) AS ok', (rid,)).fetchone()['ok'] is False  # idempotent
    assert _balance(c, org) == 40

    rid2 = c.execute('SELECT reserve_credits(%s, %s, 5) AS r', (org, uid)).fetchone()['r']
    assert _balance(c, org) == 35
    assert c.execute('SELECT release_reservation(%s) AS ok', (rid2,)).fetchone()['ok'] is True
    assert _balance(c, org) == 40

    # insufficient credits: NULL, nothing written
    assert c.execute('SELECT reserve_credits(%s, %s, 1000) AS r', (org, uid)).fetchone()['r'] is None

    rid3 = c.execute('SELECT reserve_credits(%s, %s, 10) AS r', (org, uid)).fetchone()['r']
    res = c.execute('SELECT settle_reservation_with_slack(%s, 4) AS s', (rid3,)).fetchone()['s']
    assert res == {'net_credits': 6, 'remaining': 34}
    # retry after a lost response returns the same answer, pays nothing twice
    assert c.execute('SELECT settle_reservation_with_slack(%s, 4) AS s', (rid3,)).fetchone()['s'] == res
    assert _balance(c, org) == 34

    rid4 = c.execute('SELECT reserve_credits(%s, %s, 3) AS r', (org, uid)).fetchone()['r']
    c.execute("UPDATE credit_transactions SET created_at = now() - interval '1 hour' WHERE id = %s", (rid4,))
    monkeypatch.setattr(worker, 'get_conn', scoped_conn(c))
    assert worker.reap_stale_reservations() == {'released': 1} and _balance(c, org) == 34
    assert balance_events == [(str(org), False)]  # #243: the released hold queues the wallet's balance rules

    assert c.execute('SELECT deduct_credits(%s, %s, 4) AS b', (org, uid)).fetchone()['b'] == 30
    assert c.execute("SELECT increment_credits(%s, %s, 20, 'purchase') AS b", (org, uid)).fetchone()['b'] == 50
    # every row is on the organization's wallet and records who
    assert c.execute('SELECT DISTINCT organization_id, user_id FROM credit_transactions').fetchall() == [
        {'organization_id': org, 'user_id': uid}
    ]


def test_the_beat_commits_a_settling_hold_as_the_system_role(conn, monkeypatch):
    """The beat runs as System(RESERVATION_REAPER), whose role updates only the granted columns of the hold."""
    from app import worker

    uid = _user(conn)
    org = _org(conn, uid)
    rid = conn.execute('SELECT reserve_credits(%s, %s, 10) AS r', (org, uid)).fetchone()['r']
    assert conn.execute('SELECT mark_reservation_settling(%s) AS ok', (rid,)).fetchone()['ok'] is True
    conn.execute("UPDATE credit_transactions SET created_at = now() - interval '1 hour' WHERE id = %s", (rid,))
    monkeypatch.setattr(worker, 'get_conn', scoped_conn(conn))

    assert worker.commit_settling_reservations() == {'committed': 1}

    held = conn.execute('SELECT status, type FROM credit_transactions WHERE id = %s', (rid,)).fetchone()
    assert held == {'status': 'committed', 'type': 'deduction'} and _balance(conn, org) == 40


def test_members_share_one_wallet(conn):
    """#306: one credit wallet per organization. A second member's hold, settle and release move the same
    balance, the ledger records which member, and another organization's wallet never moves."""
    c = conn
    admin, member, outsider = _user(c), _user(c), _user(c)
    org = _org(c, admin)
    c.execute('DELETE FROM member WHERE "userId" = %s', (member,))  # joins the admin's org instead (#308)
    c.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (org, member, 'member'),
    )

    rid = c.execute('SELECT reserve_credits(%s, %s, 30) AS r', (org, member)).fetchone()['r']
    assert _balance(c, org) == 20
    assert c.execute('SELECT reserve_credits(%s, %s, 30) AS r', (org, admin)).fetchone()['r'] is None  # shared: short
    assert c.execute('SELECT settle_reservation_with_slack(%s, 10) AS s', (rid,)).fetchone()['s'] == {
        'net_credits': 20,
        'remaining': 30,
    }
    rid = c.execute('SELECT reserve_credits(%s, %s, 30) AS r', (org, admin)).fetchone()['r']
    assert c.execute('SELECT release_reservation(%s) AS ok', (rid,)).fetchone()['ok'] is True
    assert _balance(c, org) == 30 and _balance(c, _org(c, outsider)) == 50
    who = c.execute(
        'SELECT user_id, type, reference_type FROM credit_transactions WHERE organization_id = %s ORDER BY created_at',
        (org,),
    ).fetchall()
    assert [(r['user_id'], r['type']) for r in who] == [
        (member, 'deduction'),
        (member, 'refund'),  # the slack goes back to the wallet, recorded for the member who held it
        (admin, 'reservation'),
        (admin, 'refund'),
    ]


def test_a_credit_row_without_its_organization_is_refused(conn):
    """#306 with #308: nothing fills organization_id in from the user (who may be in several organizations)."""
    c = conn
    uid = _user(c)
    rows = {
        'user_credits': ('user_id', '%s'),
        'credit_transactions': ('user_id, type, amount, balance_after', "%s, 'bonus', 1, 1"),
        'purchases': ('user_id, stripe_session_id, pack_id, credits, amount_cents', "%s, 'cs_x', 'p', 1, 1"),
        'billing_customers': ('user_id, stripe_customer_id', "%s, 'cus_x'"),
        'subscriptions': ('stripe_subscription_id, user_id, status, event_created', "'sub_x', %s, 'active', 1"),
        'auto_topups': ('user_id, pack_id, threshold', "%s, 'p', 100"),
    }
    for table, (cols, values) in rows.items():
        with pytest.raises(psycopg.errors.NotNullViolation, match='organization_id'):
            c.execute(f'INSERT INTO {table} ({cols}) VALUES ({values})', (uid,))
    fills = c.execute(
        "SELECT 1 FROM pg_proc WHERE proname = 'fill_organization_id' AND pronamespace = current_schema()::regnamespace"
    )
    assert not fills.fetchone()
