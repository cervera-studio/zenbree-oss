"""No scope, no connection (organization-scoping PR 13). The app's pool runs as ``zenbree_app``: a transaction that
skips the scope statement sees no row of any organization and writes nothing, and asking ``get_conn`` with no scope
raises ``ScopeMissing``. The login stays a superuser that may ``SET ROLE`` (the go-live task changes it), so these tests
read the pool the app opens, not a pool of their own."""

import psycopg
import pytest

from app.core import db as core_db
from app.core import scope as scopes
from app.core.system_reasons import SystemReason
from tests.conftest import member, project
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db

pytestmark = needs_db

_SEEN = (
    'SELECT pg_backend_pid() AS pid, current_user AS role, zb.org()::text AS org,'
    ' (SELECT count(*) FROM projects) AS projects'
)


def _raw():
    """A checkout from the app's pool that skips the scope statement: what code that forgets the scope would get."""
    return core_db._pool().connection()


def test_a_transaction_that_skips_the_scope_reads_and_writes_nothing(db):
    """Catches: a pool whose session role is the login role, which bypasses row-level security and reads every
    organization."""
    ada = member(db, _user(db))
    project(db, ada.id)
    project(db, _user(db))

    with _raw() as conn:
        seen = conn.execute(_SEEN).fetchone()
    assert (seen['role'], seen['org'], seen['projects']) == ('zenbree_app', None, 0)

    with pytest.raises(psycopg.errors.InsufficientPrivilege), _raw() as conn:
        conn.execute(
            "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Forgot the scope')",
            (ada.org_id, ada.id),
        )
    assert db.execute('SELECT count(*) AS n FROM projects').fetchone()['n'] == 2


def test_a_session_reset_on_one_checkout_never_reaches_the_next(db):
    """Catches: a role set only when the pool opens a connection. A ``RESET ROLE`` or a session ``set_config`` that
    one checkout commits would decide who every later transaction on that connection is."""
    ada = member(db, _user(db))
    project(db, ada.id)
    with _raw() as conn:
        conn.execute('RESET ROLE')
        conn.execute("SELECT set_config('zb.org_id', %s, false)", (ada.org_id,))
        left = conn.execute(_SEEN).fetchone()
    assert left['role'] != 'zenbree_app'  # the reset took, for this checkout
    assert (left['org'], left['projects']) == (ada.org_id, 1)

    # The pool hands its connections out in turn: within its size the reset one comes back.
    after = []
    for _ in range(core_db.POOL_SIZE + 1):
        with _raw() as conn:
            after.append(conn.execute(_SEEN).fetchone())
    assert left['pid'] in {s['pid'] for s in after}
    assert {(s['role'], s['org'], s['projects']) for s in after} == {('zenbree_app', None, 0)}


def test_system_still_switches_role_from_the_session_role(db):
    """``SET ROLE`` checks the session user, the login, so ``zenbree_app`` as the current role does not stop the
    ``System`` checkout's ``SET LOCAL ROLE zenbree_system``. The next raw checkout is back to ``zenbree_app``."""
    project(db, _user(db))
    project(db, _user(db))
    with scopes.system(SystemReason.HEALTH), core_db.get_conn() as conn:
        seen = conn.execute(_SEEN).fetchone()
    assert (seen['role'], seen['projects']) == ('zenbree_system', 2)
    with _raw() as conn:
        assert conn.execute(_SEEN).fetchone()['role'] == 'zenbree_app'


def test_no_checkout_jit_compiles_a_plan_even_after_one_turned_jit_on(db):
    """Catches: a pool on the server's ``jit = on``. Postgres then compiles every plan that costs more than
    ``jit_above_cost``, which made the campaign list slower (#1049)."""
    assert db.execute('SELECT pg_jit_available() AS ok').fetchone()['ok'], 'no JIT on this server: proves nothing'
    with _raw() as conn:
        conn.execute('SET jit = on')
    plans = []
    for _ in range(core_db.POOL_SIZE + 1):
        with scopes.system(SystemReason.HEALTH), core_db.get_conn() as conn:
            conn.execute('SET LOCAL jit_above_cost = 0')
            [plan] = conn.execute('EXPLAIN (ANALYZE, FORMAT JSON) SELECT count(*) FROM projects').fetchone().values()
            plans.append(plan[0])
    assert [p.get('JIT') for p in plans] == [None] * len(plans)


def test_a_connection_asked_for_with_no_scope_is_refused_and_names_its_caller(db):
    with pytest.raises(scopes.ScopeMissing, match=r'test_session_role_sql\.py:\d+'), core_db.get_conn():
        pass
