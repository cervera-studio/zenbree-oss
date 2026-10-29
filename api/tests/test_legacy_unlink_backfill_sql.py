"""The migration moves the conversations that an unlink left on a legacy row before its fix (#1099)."""

import pytest

from app.core.migrate import MIGRATIONS
from tests import test_dispatcher_sql, test_sender_pool_sql
from tests.test_dispatcher_sql import DB_URL
from tests.test_legacy_unlink_sql import _world
from tests.test_sender_pool_race_sql import _pool_of

db = test_dispatcher_sql.db
provider = test_dispatcher_sql.provider
linking = test_sender_pool_sql.linking
pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: legacy unlink backfill SQL test NOT run')

BACKFILL = (MIGRATIONS / '20261021165346_legacy_unlink_pins.sql').read_text().split('-- BACKFILL\n', 1)[1]


def _pins(c):
    return {str(r['sender_id']) for r in c.execute('SELECT sender_id FROM outreach_campaign_targets')}


def test_the_backfill_resumes_conversations_left_on_an_unlinked_legacy_row_and_is_idempotent(db, provider, linking):
    _user, _bo, pid, cid, legacy, account = _world(db)
    # What the unlink before the fix left: the link gone, the pins and the pool still on the legacy row.
    db.execute('DELETE FROM project_senders WHERE project_id = %s AND sender_id = %s', (pid, legacy))
    assert _pins(db) == {legacy}
    db.execute(BACKFILL)
    assert _pins(db) == {account}
    assert _pool_of(db, cid) == {account}
    settled = db.execute('SELECT * FROM outreach_campaign_targets ORDER BY id').fetchall()
    db.execute(BACKFILL)
    assert db.execute('SELECT * FROM outreach_campaign_targets ORDER BY id').fetchall() == settled
    assert _pool_of(db, cid) == {account}
    assert db.execute(
        "SELECT tgenabled FROM pg_trigger WHERE tgname = 'home_touch' AND tgrelid = 'outreach_campaign_targets'::regclass"
    ).fetchone() == {'tgenabled': 'O'}
