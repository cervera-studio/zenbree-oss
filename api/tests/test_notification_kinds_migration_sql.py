"""The #343 migration on rows from before it: the digest switch becomes a frequency, the kinds widen.

Needs TEST_DATABASE_URL. Builds the schema from every migration but this one, seeds the old shape, then runs it.
"""

import os
import shutil

import psycopg
import pytest

from app.core.migrate import MIGRATIONS, migrate
from tests.conftest import empty_database
from tests.test_tools_sql import _user

DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: notification migration test NOT run')
NAME = '20261007104121_notification_kinds_and_digest.sql'


@pytest.fixture
def before(tmp_path):
    older = tmp_path / 'db'
    older.mkdir()
    for f in MIGRATIONS.glob('*.sql'):
        if f.name != NAME:
            shutil.copy(f, older / f.name)
    with empty_database() as c:
        assert migrate(c, older)
        yield c


def test_the_digest_switch_becomes_a_frequency_and_nobody_is_turned_on_or_off(before):
    on, off, none = _user(before), _user(before), _user(before)
    for uid, digest in ((on, True), (off, False)):
        before.execute(
            'INSERT INTO notification_settings (user_id, digest, events) VALUES (%s, %s, %s)',
            (uid, digest, '{"low_credits": {"email": false, "browser": true}}'),
        )
    before.execute(
        "INSERT INTO notifications (user_id, organization_id, kind, href) VALUES (%s, default_organization(%s), 'reply', '/')",
        (on, on),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        before.execute(
            'INSERT INTO notifications (user_id, organization_id, kind, href)'
            " VALUES (%s, default_organization(%s), 'mailbox_error', '/')",
            (on, on),
        )

    assert migrate(before) == [NAME]

    rows = {
        r['user_id']: r
        for r in before.execute(
            'SELECT user_id::text, digest_frequency, digest_hour, events FROM notification_settings'
        ).fetchall()
    }
    assert rows[on]['digest_frequency'] == 'daily' and rows[off]['digest_frequency'] == 'off'
    assert rows[on]['digest_hour'] == rows[off]['digest_hour'] == 9
    assert rows[off]['events'] == {'low_credits': {'email': False, 'browser': True}}  # choices untouched
    assert none not in rows
    assert not before.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'notification_settings' AND column_name = 'digest'"
        ' AND table_schema = current_schema()'
    ).fetchone()
    for kind in ('linkedin_accepted', 'bounces_high', 'mailbox_error', 'plan_renewed'):
        before.execute(
            'INSERT INTO notifications (user_id, organization_id, kind, href)'
            " VALUES (%s, default_organization(%s), %s, '/')",
            (on, on, kind),
        )
    assert before.execute("SELECT count(*) AS n FROM notifications WHERE kind = 'reply'").fetchone()['n'] == 1
    with pytest.raises(psycopg.errors.CheckViolation):
        before.execute("INSERT INTO notification_settings (user_id, digest_frequency) VALUES (%s, 'monthly')", (none,))
    with pytest.raises(psycopg.errors.CheckViolation):
        before.execute('INSERT INTO notification_settings (user_id, digest_hour) VALUES (%s, 24)', (none,))
