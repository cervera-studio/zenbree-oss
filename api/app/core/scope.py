"""Who a unit of work acts for. Postgres enforces it: every tenant table has a row-level security policy that reads
the scope ``get_conn`` (app/core/db.py) enters at each checkout (api/db/20261013195148_row_level_security.sql).

One scope per unit of work, entered at its entry point and never passed as an argument:

- an HTTP request: ``current_user`` enters ``Member``, ``require_admin`` and ``public(reason)`` enter ``System``
  (app/auth/middleware.py);
- a block that crosses organizations on purpose: ``system(reason)``; one that acts for an organization with no
  person: ``acting_for(org, why)``.

The scope lives in a ContextVar. asyncio tasks, ``asyncio.to_thread`` and FastAPI's threadpool copy it; a raw thread
or ``ThreadPoolExecutor.submit`` does not. Entering a scope must happen in async code or a ``with`` block: a sync
FastAPI dependency runs in a copied context, so its ``set`` never reaches the endpoint (measured on FastAPI 0.142).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass
from typing import NewType

from app.core.system_reasons import SystemReason

OrgId = NewType('OrgId', str)
UserId = NewType('UserId', str)


@dataclass(frozen=True, slots=True)
class Member:
    """A person acting in one organization. Every checkout runs ``zb.enter_member``, which refuses a person who
    left the organization or is banned: the removed-member check has one spelling."""

    org: OrgId
    user: UserId


@dataclass(frozen=True, slots=True)
class ForOrg:
    """The system acting inside one organization, for no person. Row-level security still holds it to ``org``."""

    org: OrgId
    reason: str  # free text for logs: it cannot cross organizations, so it needs no review


@dataclass(frozen=True, slots=True)
class System:
    """Crosses organizations on purpose: the checkout runs as ``zenbree_system``, which bypasses row-level security."""

    reason: SystemReason


type Scope = Member | ForOrg | System

_SCOPE: ContextVar[Scope | None] = ContextVar('zb_scope', default=None)
# How many connections this context holds: a scope is fixed at checkout, so changing it inside an open ``get_conn``
# would let a helper run with the outer connection's rights (interrogate, Act on 10). A count, since checkouts nest.
_OPEN: ContextVar[int] = ContextVar('zb_open_connections', default=0)


# BaseException, like the two below: the app catches ``Exception`` in many places (the agent loop, webhooks, the
# back office), and none of them may turn a scope failure into a logged error and go on.
class ScopeMissing(BaseException):
    """A connection was asked for outside any scope."""


class ScopeChangeWhileOpen(BaseException):
    """A scope was entered while this context holds a connection checked out under the previous one."""


class ActorGone(BaseException):
    """The ``Member`` of this scope is no longer a live member: removed from the organization, or banned. HTTP maps
    it to 401 ``AUTH.NOT_A_MEMBER``."""

    def __init__(self, scope: Member) -> None:
        super().__init__(f'user {scope.user} is no longer a live member of organization {scope.org}')
        self.scope = scope


def current() -> Scope:
    if (scope := _SCOPE.get()) is None:
        raise ScopeMissing('no scope: enter one at the entry point (current_user, system(...), acting_for(...))')
    return scope


def current_or_none() -> Scope | None:
    return _SCOPE.get()


def connection_opened() -> int:
    """For app/core/db.py: mark one more connection open in this context. Returns the count to restore."""
    held = _OPEN.get()
    _OPEN.set(held + 1)
    return held


def connection_closed(held: int) -> None:
    _OPEN.set(held)


def for_another_thread() -> Context:
    """A copy of this context for work handed to another thread: the scope goes with it, the open connections do
    not. That thread holds none of them, so it may enter a scope of its own (``cost_scope``) while this context still
    holds one."""
    context = copy_context()
    context.run(_OPEN.set, 0)
    return context


def _set(scope: Scope) -> Scope | None:
    if _OPEN.get():
        raise ScopeChangeWhileOpen(f'cannot enter {scope} while a connection is open: close it first')
    previous = _SCOPE.get()
    _SCOPE.set(scope)
    return previous


@contextmanager
def _entered(scope: Scope) -> Iterator[None]:
    previous = _set(scope)
    try:
        yield
    finally:
        _SCOPE.set(previous)


async def enter_member(org: str, user: str) -> str:
    """Enter ``Member(org, user)`` for the rest of this request and return the person's role in ``org``. Raises
    ``ActorGone`` when the database refuses the membership. No reset: the request's context ends with it."""
    from app.core.db import member_role

    _set(Member(OrgId(org), UserId(user)))
    return await asyncio.to_thread(member_role)


def enter_system_for_request(reason: SystemReason) -> None:
    """``require_admin`` and ``public(reason)``: the rest of the request runs as ``System(reason)``."""
    _set(System(reason))


@contextmanager
def acting_as(org: str, user: str) -> Iterator[Member]:
    """Run a block as ``user`` in ``org``. Takes ids, so no code outside this module builds a scope: the scan in
    tests/test_scope_entry_points.py refuses ``Member(...)``, ``ForOrg(...)`` and ``System(...)`` elsewhere."""
    member = Member(OrgId(org), UserId(user))
    with _entered(member):
        yield member


@contextmanager
def acting_for(org: str, reason: str) -> Iterator[ForOrg]:
    """Run a block for ``org`` with no person. The outer scope comes back on exit."""
    scope = ForOrg(OrgId(org), reason)
    with _entered(scope):
        yield scope


@contextmanager
def system(reason: SystemReason) -> Iterator[System]:
    """Run a block across organizations, for a reason ``SystemReason.ALLOWED`` lists for the calling module."""
    scope = System(reason)
    with _entered(scope):
        yield scope


def still_live() -> None:
    """One empty scoped checkout: raises ``ActorGone`` when the ``Member`` left. Sync: call it in a thread."""
    from app.core.db import get_conn

    with get_conn():
        pass
