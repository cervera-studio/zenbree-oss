"""The scans of tests/test_scope_entry_points.py catch each form a test or a module could use to slip past them
(review round 1, non-blocking 2 and 3). Each case is a snippet the scan must flag, or one it must let through."""

import ast
import textwrap

import pytest

from app.core.system_reasons import ALLOWED, SystemReason
from tests import scope_scans
from tests.test_scope_entry_points import protected_dependencies

# Written in two pieces, so the scan of this file does not find the statement in its own source.
REDEFINE = 'CREATE OR REPLACE FUNCTION zb.' + 'enter_member(o uuid, u uuid) RETURNS text'

OVERRIDES = {
    'alias import': """
        from app.auth.middleware import current_user as cu
        app.dependency_overrides[cu] = lambda: None
    """,
    'local variable holding the dependency': """
        from app.auth.middleware import current_user
        dep = current_user
        app.dependency_overrides[dep] = lambda: None
    """,
    'local variable holding the overrides': """
        ov = app.dependency_overrides
        ov[current_user] = lambda: None
    """,
    '|=': 'app.dependency_overrides |= {current_user: lambda: None}',
    '.update': 'app.dependency_overrides.update({current_user_or_api_key: lambda: None})',
    '.__setitem__': 'app.dependency_overrides.__setitem__(require_admin, lambda: None)',
    'monkeypatch.setitem': 'monkeypatch.setitem(app.dependency_overrides, current_user, lambda: None)',
    'patch.dict': """
        with patch.dict(app.dependency_overrides, {current_user: lambda: None}):
            pass
    """,
    'getattr with a constant': "app.dependency_overrides[getattr(middleware, 'current_user')] = lambda: None",
    'require_org_admin': 'app.dependency_overrides[middleware.require_org_admin] = lambda: None',
    'require_org_admin_or_api_key': 'app.dependency_overrides[require_org_admin_or_api_key] = lambda: None',
    'current_project': 'app.dependency_overrides[current_project] = lambda: PID',
    'a dependency a route reaches its entry through': 'app.dependency_overrides[_limit_list_reads] = lambda: None',
    'a helper that takes the dependency': """
        def override(dep):
            app.dependency_overrides[dep] = lambda: None
    """,
    'getattr with a variable': 'app.dependency_overrides[getattr(middleware, name)] = lambda: None',
    'a fake of enter_member with no guard': """
        async def enter_member(org, user):
            return 'admin'
        monkeypatch.setattr(middleware, 'enter_member', enter_member)
    """,
    'enter_member by its dotted path': "monkeypatch.setattr('app.core.scope.enter_member', fake)",
    'enter_member assigned': 'middleware.enter_member = fake',
    'patch.object of enter_member': """
        with patch.object(scope, 'enter_member'):
            pass
    """,
    'zb.enter_member redefined in SQL': f'db.execute({REDEFINE!r})',
}

LET_THROUGH = {
    'the token step': """
        from app.auth.middleware import authenticate, authenticate_or_api_key
        app.dependency_overrides[authenticate] = lambda: principal
        app.dependency_overrides[authenticate_or_api_key] = lambda: principal
        app.dependency_overrides.pop(authenticate, None)
        app.dependency_overrides.clear()
    """,
    'a fake of enter_member that refuses a test database': """
        async def enter_member(org, user):
            no_membership_read(settings.database_url)
            return 'admin'
        monkeypatch.setattr(middleware, 'enter_member', enter_member)
    """,
}


def _flags(snippet: str) -> list:
    tree = ast.parse(textwrap.dedent(snippet))
    return scope_scans.overrides(tree, protected_dependencies()) + scope_scans.membership_fakes(tree)


@pytest.mark.parametrize('snippet', OVERRIDES.values(), ids=OVERRIDES.keys())
def test_the_override_scan_flags(snippet):
    assert _flags(snippet)


@pytest.mark.parametrize('snippet', LET_THROUGH.values(), ids=LET_THROUGH.keys())
def test_the_override_scan_lets_through(snippet):
    assert not _flags(snippet)


# Each in lists/routes.py, a module no reason allows.
REASONS = {
    'a keyword argument': 'with system(reason=SystemReason.BACK_OFFICE): pass',
    'an alias of system': """
        from app.core.scope import system as s
        with s(reason_of_the_day): pass
    """,
    'an alias of SystemReason': """
        from app.core.system_reasons import SystemReason as R
        with system(R.BACK_OFFICE): pass
    """,
    'SystemReason re-exported by a module alias': """
        import app.core.system_reasons as sr
        with system(sr.SystemReason.BACK_OFFICE): pass
    """,
    'a variable holding a reason': """
        r = SystemReason.HEALTH
        with system(r): pass
    """,
    'public with a variable': 'dependencies = [public(why)]',
    'SystemReason[name]': "with system(SystemReason['BACK_OFFICE']): pass",
    'getattr(SystemReason, name)': 'with system(getattr(SystemReason, name)): pass',
    'System(...)': """
        from app.core import scope
        with scope._entered(scope.System(SystemReason.BACK_OFFICE)): pass
    """,
    'Member(...)': 'acting(Member(OrgId(org), UserId(user)))',
    'ForOrg(...)': 'from app.core.scope import ForOrg\nscoped = ForOrg(org, "why")',
    'a private helper of scope': 'from app.core.scope import _set\n_set(scope)',
}


def _reason_flags(snippet: str, module: str = 'lists/routes.py') -> list[str]:
    uses, refused = scope_scans.reasons(ast.parse(textwrap.dedent(snippet)), module)
    return refused + [f'{u.reason} in {u.module}' for u in uses if u.module not in ALLOWED[SystemReason[u.reason]]]


@pytest.mark.parametrize('snippet', REASONS.values(), ids=REASONS.keys())
def test_the_reason_scan_flags(snippet):
    assert _reason_flags(snippet)


def test_the_reason_scan_lets_a_written_out_reason_through_where_it_is_allowed():
    snippet = """
        from app.auth.middleware import SystemReason, public
        with system(SystemReason.STRIPE_WEBHOOK): pass
        dependencies = [public(SystemReason.STRIPE_WEBHOOK), public(None)]
    """
    assert not _reason_flags(snippet, 'billing/routes.py')
