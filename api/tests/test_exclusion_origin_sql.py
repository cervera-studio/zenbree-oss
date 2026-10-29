import pytest

from app.core.migrate import MIGRATIONS
from tests import test_dispatcher_sql
from tests.conftest import project
from tests.test_dispatcher_sql import DB_URL
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: exclusion origin SQL test NOT run')
db = test_dispatcher_sql.db
MIGRATION = '20261008160327_exclusion_origin_key_last4.sql'


def test_the_migration_gives_existing_exclusions_an_origin(db):
    db.execute('ALTER TABLE exclusions DROP COLUMN origin')
    db.execute('ALTER TABLE user_api_keys DROP COLUMN last4')
    user = _user(db)
    pid = project(db, user)
    for value, source, opt_out in [('a.test', 'onboarding', None), ('b.test', None, 'now()'), ('c.test', None, None)]:
        db.execute(
            'INSERT INTO exclusions (project_id, user_id, kind, value, source, reply_opt_out_at)'
            f" VALUES (%s, %s, 'domain', %s, %s, {opt_out or 'NULL'})",
            (pid, user, value, source),
        )
    db.execute(
        'INSERT INTO user_api_keys (organization_id, user_id, key_hash, label)'
        " VALUES (default_organization(%s), %s, repeat('a', 64), 'old')",
        (user, user),
    )

    db.execute((MIGRATIONS / MIGRATION).read_text())

    origins = {r['value']: r['origin'] for r in db.execute('SELECT value, origin FROM exclusions').fetchall()}
    assert origins == {'a.test': 'profile', 'b.test': 'unsubscribed', 'c.test': 'manual'}
    assert db.execute('SELECT last4 FROM user_api_keys').fetchone()['last4'] is None
