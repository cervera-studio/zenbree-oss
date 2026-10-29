import pytest

from app.core.migrate import MIGRATIONS
from tests import test_dispatcher_sql
from tests.test_dispatcher_sql import DB_URL
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: repricing SQL test NOT run')
db = test_dispatcher_sql.db
REPRICING = '20261007143736_credit_repricing.sql'


def _wallet(c, credits=None) -> str:
    uid = _user(c)
    if credits is None:
        c.execute(
            'INSERT INTO user_credits (organization_id, user_id) VALUES (default_organization(%s), %s)', (uid, uid)
        )
    else:
        c.execute(
            'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (default_organization(%s), %s, %s)',
            (uid, uid, credits),
        )
    return uid


def _balance(c, uid) -> int:
    return c.execute('SELECT credits_remaining FROM user_credits WHERE user_id = %s', (uid,)).fetchone()[
        'credits_remaining'
    ]


def test_new_wallets_open_with_25(db):
    assert _balance(db, _wallet(db)) == 25


def test_balances_keep_their_euro_value_rounded_up(db):
    rich, small, empty = _wallet(db, 10050), _wallet(db, 1), _wallet(db, 0)
    db.execute((MIGRATIONS / REPRICING).read_text())  # the fixture ran it on an empty schema: run it on this data
    assert [_balance(db, u) for u in (rich, small, empty)] == [3015, 1, 0]
