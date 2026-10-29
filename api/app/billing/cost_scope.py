"""The scope a cost row is written in. A vendor or a model already charged for the call, so its row must land even
when the member who made the call left during it (interrogate, Consider "Cost and settlement writes inside a
member's tool call"): under ``Member`` the write would raise ``ActorGone`` and lose the row."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.core import scope as scopes


@contextmanager
def cost_scope() -> Iterator[None]:
    """Under ``Member(org, user)``, ``ForOrg(org)``; any other scope as it is. Enter it before ``get_conn``: a scope
    cannot change while this context holds a connection."""
    scope = scopes.current_or_none()
    if not isinstance(scope, scopes.Member):
        yield
        return
    with scopes.acting_for(scope.org, 'cost record'):
        yield
