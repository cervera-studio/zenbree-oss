"""Postgres: one sync psycopg pool per process, rows as dicts, every checkout inside the caller's scope.

A checkout with no scope raises ``ScopeMissing``, naming the code that asked. The first statement of each checkout
(app/core/scope.py) sets the role for that transaction only:

- ``Member``: ``SET LOCAL ROLE zenbree_app``, then ``zb.enter_member(org, user)``, which refuses a removed or banned
  member (SQLSTATE ZB401, raised here as ``ActorGone``) and sets the transaction-local ``zb.org_id`` and
  ``zb.actor_id`` that the policies read;
- ``ForOrg``: ``SET LOCAL ROLE zenbree_app``, then ``zb.enter_org(org)``;
- ``System``: ``SET LOCAL ROLE zenbree_system``, which bypasses row-level security, and clears both settings, so an
  insert's ``DEFAULT zb.org()`` never takes a value a previous user of the connection left on the session.

Between those transactions the session role is ``zenbree_app`` too (``_session_role``, when the pool opens a
connection and each time one comes back). A statement after a ``commit()`` inside the block, outside the
transaction the scope entered, has no organization and so sees no row of an organization's table. A session-level
``RESET ROLE`` or ``set_config(..., false)`` on one checkout never reaches the next one (interrogate, Act on 2).
The login stays a superuser that may ``SET ROLE``: this guards against code that forgets its scope, not against
code that removes it on purpose.
"""

import atexit
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from types import FrameType
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core import scope as scopes
from app.core.config import settings

# The SQLSTATE zb.enter_member raises for a person who is no longer a live member.
ACTOR_GONE_SQLSTATE = 'ZB401'
POOL_SIZE = 10


# Private: a checkout taken from it directly skips the scope (tests/scope_scans.py ``raw_connections``).
@cache
def _pool() -> ConnectionPool:
    # Opened lazily so importing the app never needs a live DB.
    p = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=POOL_SIZE,
        kwargs={'row_factory': dict_row},
        configure=_session_role,
        reset=_session_role,
        open=True,
    )
    atexit.register(p.close)  # else __del__ joins threads at interpreter shutdown
    return p


def _session_role(conn: psycopg.Connection[Any]) -> None:
    """``RESET ALL`` drops a session ``zb.*`` setting a checkout left; what the connection string set survives it.
    Autocommit sends the statements in one round trip with no transaction to commit. The pool discards a
    connection whose callback raises, so a connection that could not take the role never serves a checkout.

    ``jit = off``: Postgres JIT-compiles every plan that costs more than ``jit_above_cost``, and the cost grows with
    the data, not with the time the query takes. JIT made the campaign list of a 100-campaign client 70 ms slower
    (773 ms against 703 ms), and 350 ms slower with the inlining and optimization that start at a cost of 500k
    (forced on the same plan, #1049). A query that gains from JIT can ``SET LOCAL jit = on``."""
    conn.autocommit = True
    conn.execute('RESET ALL; SET jit = off; SET ROLE zenbree_app')
    conn.autocommit = False


# Yields Any: about 60 callers index fetchone() with no None check, which a typed connection refuses.
@contextmanager
def get_conn(timeout: float | None = None) -> Iterator[Any]:
    """``with get_conn() as conn:`` commits on success, rolls back on error. The scope comes from app/core/scope.py.

    Raises ``ScopeMissing`` with no scope, and ``ActorGone`` at checkout, before the caller's first statement, when
    the ``Member`` left; the connection goes back to the pool. A ``conn.commit()`` or ``conn.rollback()`` in the block
    would end the transaction the scope entered: E6 (tests/test_scope_entry_points.py) refuses both outside this
    module."""
    with _checkout(timeout) as (conn, _role):
        yield conn


def member_role() -> str:
    """The checkout ``scope.enter_member`` makes: the role ``zb.enter_member`` returns, so a request reads the
    membership once."""
    with _checkout(None) as (_conn, role):
        return str(role)


@contextmanager
def _checkout(timeout: float | None) -> Iterator[tuple[Any, str | None]]:
    scope = scopes.current_or_none()
    if scope is None:
        raise scopes.ScopeMissing(f'no scope at {_caller()}')
    with _pool().connection(timeout=timeout) as conn, entered(conn, scope) as role:
        yield conn, role


@contextmanager
def entered(conn: psycopg.Connection[Any], scope: scopes.Scope) -> Iterator[str | None]:
    """``scope`` entered on the transaction ``conn`` has open, and the connection counted as open for the scope
    ContextVar. Shared with the tests that hand the app their own connection (tests/conftest.py, ``scoped_conn``),
    so their route SQL runs under the scope a pooled checkout enters."""
    held = scopes.connection_opened()
    try:
        yield _enter(conn, scope)
    finally:
        scopes.connection_closed(held)


def _enter(conn: psycopg.Connection[Any], scope: scopes.Scope) -> str | None:
    """One round trip: a statement list with literals goes over the simple query protocol."""
    first: sql.Composable
    match scope:
        case scopes.Member(org=org, user=user):
            first = sql.SQL('SET LOCAL ROLE zenbree_app; SELECT zb.enter_member({}::uuid, {}::uuid) AS role').format(
                sql.Literal(org), sql.Literal(user)
            )
        case scopes.ForOrg(org=org):
            first = sql.SQL('SET LOCAL ROLE zenbree_app; SELECT zb.enter_org({}::uuid) AS role').format(
                sql.Literal(org)
            )
        case scopes.System():
            first = sql.SQL('SET LOCAL ROLE zenbree_system; SELECT zb.enter_org(NULL) AS role')
    try:
        cursor = conn.execute(first)
        cursor.nextset()  # past SET's empty result, to the enter function's row
        row = cursor.fetchone()
    except psycopg.Error as e:
        if isinstance(scope, scopes.Member) and e.sqlstate == ACTOR_GONE_SQLSTATE:
            raise scopes.ActorGone(scope) from e
        raise
    return row['role'] if row else None


def _caller() -> str:
    """The first frame outside this module and contextlib: the code that asked for the connection."""
    frame: FrameType | None = sys._getframe(1)
    while frame and (frame.f_code.co_filename == __file__ or frame.f_code.co_filename.endswith('contextlib.py')):
        frame = frame.f_back
    if frame is None:
        return 'unknown'
    path = frame.f_code.co_filename
    return f'{path[path.rfind("/app/") + 1 :] if "/app/" in path else path}:{frame.f_lineno}'
