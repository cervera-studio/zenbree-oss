"""Every credit hold goes through ``app/billing/tab.py`` (#660): a holder that reserves, grows, settles or releases on
its own bypasses the records that ``settle_hold`` and the reaper settle it from."""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
CREDITS = APP / 'billing' / 'credits.py'
TAB = APP / 'billing' / 'tab.py'
MODULE = 'app.billing.credits'
# Who may call each function of app.billing.credits from outside it.
ALLOWED = {'reserve': {TAB}, 'grow': {TAB}, 'settle': set(), 'release': set()}
# The SQL functions behind them, which only credits.py calls.
SQL_CALL = re.compile(
    r'\b(reserve_project_credits|grow_reservation|settle_reservation_with_slack|settle_hold|release_reservation'
    r'|mark_reservation_settling|commit_reservation)\s*\('
)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{_dotted(node.value)}.{node.attr}'
    return ''


def _calls(tree: ast.Module) -> list[tuple[str, int]]:
    """References to ``credits.<name>``, through any name the module is imported as, and to a name imported from
    ``app.billing.credits`` (also renamed): a reference, not only a call, so ``r = credits.release`` and
    ``partial(credits.release, ...)`` count too."""
    module = {'credits', MODULE}
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'app.billing':
            module |= {alias.asname for alias in node.names if alias.name == 'credits' and alias.asname}
        elif isinstance(node, ast.Import):
            module |= {alias.asname for alias in node.names if alias.name == MODULE and alias.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == MODULE:
            imported |= {alias.asname or alias.name: alias.name for alias in node.names if alias.name in ALLOWED}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _dotted(node.value) in module:
            name = node.attr
        elif isinstance(node, ast.Name):
            name = imported.get(node.id, '')
        else:
            continue
        if name in ALLOWED:
            found.append((name, node.lineno))
    return found


def _sql_calls(tree: ast.Module) -> list[tuple[str, int]]:
    """Calls of the hold's SQL functions in string literals; docstrings only name them."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        (match, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        for match in SQL_CALL.findall(node.value)
    ]


def _sources() -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text())) for path in sorted(APP.rglob('*.py')) if path != CREDITS]


def test_only_the_tab_reserves_or_grows_and_nothing_settles_or_releases_a_hold_by_hand():
    found = [
        f'{path.relative_to(APP.parent)}:{line} credits.{name}'
        for path, tree in _sources()
        for name, line in _calls(tree)
        if path not in ALLOWED[name]
    ]
    assert not found, f'open, record and close a Tab (app/billing/tab.py) in these files instead: {found}'


def test_only_credits_calls_the_sql_functions_of_a_hold():
    found = [
        f'{path.relative_to(APP.parent)}:{line} {name}' for path, tree in _sources() for name, line in _sql_calls(tree)
    ]
    assert not found, f'call app/billing/tab.py (a Tab) from these files, not the SQL of a hold: {found}'
