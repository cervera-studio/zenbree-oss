"""The migration runner (#268) against a real Postgres. Needs TEST_DATABASE_URL."""

import os
import shutil
import uuid

import psycopg
import pytest
from psycopg.rows import tuple_row

from app.core.migrate import MIGRATIONS, MigrationError, migrate
from tests.conftest import empty_database

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: migration runner tests NOT run')
BASELINE = '20261005000000_baseline.sql'


@pytest.fixture
def conn():
    with empty_database(row_factory=tuple_row) as c:
        yield c


@pytest.fixture
def migrations(tmp_path):
    shutil.copy(MIGRATIONS / BASELINE, tmp_path)
    (tmp_path / '20991231000000_note.sql').write_text('ALTER TABLE projects ADD COLUMN note text;')
    return tmp_path


def _applied(conn):
    return [r[0] for r in conn.execute('SELECT name FROM schema_migrations ORDER BY name')]


def test_fresh_database_runs_everything_once(conn, migrations):
    assert migrate(conn, migrations) == [BASELINE, '20991231000000_note.sql']
    assert migrate(conn, migrations) == []
    conn.execute('SELECT note FROM projects')


def test_existing_database_adopts_by_running_the_idempotent_baseline(conn, migrations):
    # a database built before migrations and behind main, like prod: one later column missing, live data
    conn.execute((MIGRATIONS / BASELINE).read_text())
    conn.execute('ALTER TABLE jobs DROP COLUMN first_row_at')
    conn.execute("INSERT INTO settings (key, value) VALUES ('kept', 'true') ON CONFLICT DO NOTHING")
    assert migrate(conn, migrations) == [BASELINE, '20991231000000_note.sql']
    conn.execute('SELECT first_row_at FROM jobs')
    assert conn.execute("SELECT value FROM settings WHERE key = 'kept'").fetchone() == (True,)
    assert migrate(conn, migrations) == []


def test_edited_migration_stops_before_anything_runs(conn, migrations):
    migrate(conn, migrations)
    (migrations / '20991231000000_note.sql').write_text('ALTER TABLE projects ADD COLUMN note varchar(10);')
    (migrations / '20991231000001_more.sql').write_text('ALTER TABLE projects ADD COLUMN more text;')
    with pytest.raises(MigrationError, match='20991231000000_note'):
        migrate(conn, migrations)
    assert _applied(conn) == [BASELINE, '20991231000000_note.sql']


def test_missing_applied_file_only_warns(conn, migrations, caplog):
    # a rollback to an older image: the schema stays forward, pending files still run
    migrate(conn, migrations)
    (migrations / '20991231000000_note.sql').unlink()
    (migrations / '20991231000001_more.sql').write_text('ALTER TABLE projects ADD COLUMN more text;')
    assert migrate(conn, migrations) == ['20991231000001_more.sql']
    assert '20991231000000_note.sql' in caplog.text


def test_misnamed_file_fails_before_anything_runs(conn, migrations):
    (migrations / 'schema_inbox.sql').write_text('ALTER TABLE projects ADD COLUMN stray text;')
    with pytest.raises(MigrationError, match='schema_inbox'):
        migrate(conn, migrations)
    assert _applied(conn) == []


def test_failed_migration_rolls_back_and_is_not_recorded(conn, migrations):
    (migrations / '20991231000001_bad.sql').write_text('ALTER TABLE projects ADD COLUMN half text; SELECT 1/0;')
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(conn, migrations)
    assert _applied(conn) == [BASELINE, '20991231000000_note.sql']
    with pytest.raises(psycopg.errors.UndefinedColumn):
        conn.execute('SELECT half FROM projects')


def test_organizations_backfill_existing_users_and_their_rows(conn, tmp_path):
    # #305 on a database like prod: users with data from before organizations, and a signed-in session
    # #305's two migrations and every one after them (e.g. #308's stricter fill) run on the old data.
    new = {p.name for p in MIGRATIONS.glob('*.sql') if p.name >= '20261006075913_better_auth.sql'}
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < min(new):
            shutil.copy(path, tmp_path)
    migrate(conn, tmp_path)
    owner, idle = uuid.uuid4(), uuid.uuid4()
    for uid in (owner, idle):
        conn.execute(
            'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)',
            (uid, f'U {uid}', f'{uid}@x.test'),
        )
    conn.execute(
        'INSERT INTO session ("expiresAt", token, "updatedAt", "userId") VALUES (now() + interval \'1 day\', %s, now(), %s)',
        (uuid.uuid4().hex, owner),
    )
    pid = conn.execute("INSERT INTO projects (user_id, name) VALUES (%s, 'P') RETURNING id", (owner,)).fetchone()[0]
    sid = conn.execute(
        'INSERT INTO chat_sessions (user_id, project_id) VALUES (%s, %s) RETURNING id', (owner, pid)
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO lists (user_id, chat_session_id, project_id, name, entity) VALUES (%s, %s, %s, 'L', 'person')"
        ' RETURNING id',
        (owner, sid, pid),
    ).fetchone()[0]
    conn.execute("INSERT INTO outreach_campaigns (owner_id, name, list_id) VALUES (%s, 'C', %s)", (owner, lid))
    conn.execute("INSERT INTO user_api_keys (user_id, label, key_hash) VALUES (%s, 'k', %s)", (owner, '0' * 64))

    for name in sorted(new):
        shutil.copy(MIGRATIONS / name, tmp_path)
    assert migrate(conn, tmp_path) == sorted(new)

    orgs = dict(
        conn.execute(
            'SELECT m."userId", m."organizationId" FROM member m JOIN organization o ON o.id = m."organizationId"'
            ' WHERE m.role = \'admin\' AND o.slug = m."userId"::text'
        ).fetchall()
    )
    assert set(orgs) == {owner, idle} and len(set(orgs.values())) == 2
    assert conn.execute('SELECT count(*) FROM member').fetchone()[0] == 2
    assert conn.execute('SELECT "activeOrganizationId" FROM session').fetchone()[0] == str(orgs[owner])
    for table in ('projects', 'chat_sessions', 'lists', 'outreach_campaigns', 'user_api_keys'):
        assert conn.execute(f'SELECT DISTINCT organization_id FROM {table}').fetchall() == [(orgs[owner],)], table
        nullable = conn.execute(
            'SELECT is_nullable FROM information_schema.columns WHERE table_schema = current_schema()'
            " AND table_name = %s AND column_name = 'organization_id'",
            (table,),
        ).fetchone()
        assert nullable == ('NO',), table


def test_organization_credits_backfill_keeps_wallet_customer_and_subscription(conn, tmp_path):
    # #306 on a database like prod: a user with a balance, ledger, open hold and a Stripe plan from before
    new = '20261006110243_organization_credits.sql'
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name < new:
            shutil.copy(path, tmp_path)
    migrate(conn, tmp_path)
    uid = uuid.uuid4()
    conn.execute('INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)', (uid, 'U', 'u@x.t'))
    conn.execute('SELECT default_organization(%s)', (uid,))  # the signup hook
    conn.execute('INSERT INTO user_credits (user_id) VALUES (%s)', (uid,))
    conn.execute("SELECT increment_credits(%s, 100, 'purchase')", (uid,))
    hold = conn.execute('SELECT reserve_credits(%s, 30)', (uid,)).fetchone()[0]
    conn.execute("INSERT INTO billing_customers (user_id, stripe_customer_id) VALUES (%s, 'cus_1')", (uid,))
    conn.execute(
        'INSERT INTO subscriptions (stripe_subscription_id, user_id, plan_id, status, event_created)'
        " VALUES ('sub_1', %s, 'pro', 'active', 1)",
        (uid,),
    )
    conn.execute(
        "INSERT INTO purchases (user_id, stripe_session_id, pack_id, credits, amount_cents) VALUES (%s, 'cs_1', 'p', 100, 1900)",
        (uid,),
    )
    conn.execute("INSERT INTO auto_topups (user_id, enabled, pack_id, threshold) VALUES (%s, true, 'p', 100)", (uid,))
    # A teammate (#308) who joined the user's organization: their own wallet paid for what they spent there
    mate = uuid.uuid4()
    conn.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)', (mate, 'M', 'm@x.t')
    )
    conn.execute('SELECT default_organization(%s)', (mate,))  # the signup hook: their own organization
    conn.execute('INSERT INTO user_credits (user_id) VALUES (%s)', (mate,))
    conn.execute("SELECT deduct_credits(%s, 7, 'tool')", (mate,))
    conn.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") SELECT "organizationId", %s, %s, now()'
        ' FROM member WHERE "userId" = %s',
        (mate, 'member', uid),
    )
    # Joined the user's organization before founding their own: the oldest membership is not their wallet's
    late = uuid.uuid4()
    conn.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)', (late, 'L', 'l@x.t')
    )
    conn.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") SELECT "organizationId", %s, %s,'
        ' now() - interval \'1 day\' FROM member WHERE "userId" = %s',
        (late, 'member', uid),
    )
    lates = conn.execute(
        'INSERT INTO organization (name, slug, "createdAt") VALUES (%s, %s, now()) RETURNING id', ('L', str(late))
    ).fetchone()[0]
    conn.execute(
        'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
        (lates, late, 'admin'),
    )
    conn.execute('INSERT INTO user_credits (user_id) VALUES (%s)', (late,))
    conn.execute("SELECT deduct_credits(%s, 3, 'tool')", (late,))
    # Founded an organization but has no member row in it (any more): still their wallet, never an abort
    orphan = uuid.uuid4()
    conn.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)', (orphan, 'O', 'z@x.t')
    )
    orphans = conn.execute('SELECT default_organization(%s)', (orphan,)).fetchone()[0]
    conn.execute('DELETE FROM member WHERE "userId" = %s', (orphan,))
    conn.execute('INSERT INTO user_credits (user_id) VALUES (%s)', (orphan,))
    # No organization at all: one is founded for them
    bare = uuid.uuid4()
    conn.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)', (bare, 'N', 'n@x.t')
    )
    conn.execute('INSERT INTO user_credits (user_id) VALUES (%s)', (bare,))

    shutil.copy(MIGRATIONS / new, tmp_path)
    assert migrate(conn, tmp_path) == [new]

    org = conn.execute('SELECT id FROM organization WHERE slug = %s', (str(uid),)).fetchone()[0]
    mates = conn.execute('SELECT id FROM organization WHERE slug = %s', (str(mate),)).fetchone()[0]
    for table in (
        'user_credits',
        'credit_transactions',
        'billing_customers',
        'subscriptions',
        'purchases',
        'auto_topups',
    ):
        got = conn.execute(f'SELECT DISTINCT organization_id FROM {table} WHERE user_id = %s', (uid,)).fetchall()
        assert got == [(org,)], table
        nullable = conn.execute(
            'SELECT is_nullable FROM information_schema.columns WHERE table_schema = current_schema()'
            " AND table_name = %s AND column_name = 'organization_id'",
            (table,),
        ).fetchone()
        assert nullable == ('NO',), table
    assert conn.execute('SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (org,)).fetchone() == (
        120,
    )
    # The teammate's wallet and spend stay theirs: their own organization, not the team they also belong to
    for table in ('user_credits', 'credit_transactions'):
        got = conn.execute(f'SELECT DISTINCT organization_id FROM {table} WHERE user_id = %s', (mate,)).fetchall()
        assert got == [(mates,)], table
    assert conn.execute(
        'SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (mates,)
    ).fetchone() == (43,)
    for table in ('user_credits', 'credit_transactions'):
        got = conn.execute(f'SELECT DISTINCT organization_id FROM {table} WHERE user_id = %s', (late,)).fetchall()
        assert got == [(lates,)], table
    assert conn.execute('SELECT organization_id FROM user_credits WHERE user_id = %s', (orphan,)).fetchone() == (
        orphans,
    )
    founded = conn.execute(
        'SELECT o.id FROM organization o JOIN member m ON m."organizationId" = o.id AND m."userId" = %s'
        " AND m.role = 'admin' WHERE o.slug = %s",
        (bare, str(bare)),
    ).fetchone()
    assert founded and conn.execute(
        'SELECT organization_id FROM user_credits WHERE user_id = %s', (bare,)
    ).fetchone() == (founded[0],)
    # The hold taken before the move settles on the organization's wallet
    assert conn.execute('SELECT release_reservation(%s)', (hold,)).fetchone() == (True,)
    assert conn.execute('SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (org,)).fetchone() == (
        150,
    )
    # Credit calls and rows name their organization now: the user-keyed functions are gone, nothing fills it in
    with pytest.raises(psycopg.errors.UndefinedFunction):
        conn.execute("SELECT deduct_credits(%s, 5, 'tool')", (uid,))
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute('INSERT INTO auto_topups (user_id, pack_id, threshold) VALUES (%s, %s, 100)', (uid, 'p'))
