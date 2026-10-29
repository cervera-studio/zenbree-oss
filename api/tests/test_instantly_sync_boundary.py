"""#930: the code outside app/integrations/instantly_sync reads Instantly's events only through ``outcomes``.

#931, #932 and #942 build on the facts. A module that imports the store, the mapping or the beat directly would write
or read the facts by another rule than the one this package keeps; the error names the module to use instead.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
PACKAGE = 'app.integrations.instantly_sync'
# The modules each outside caller may import; every other caller reads ``outcomes`` alone.
ALLOWED = {
    'integrations/instantly.py': {'outcomes', 'subscription'},  # the status card and the disconnect
    'main.py': {'webhook'},  # the callback's router
    'worker.py': {'task'},  # the beat
}


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == PACKAGE:
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(f'{PACKAGE}.'):
            found.add(node.module.split('.')[3])
        elif isinstance(node, ast.Import):
            found |= {a.name.split('.')[3] for a in node.names if a.name.startswith(f'{PACKAGE}.')}
    return found


def test_outside_callers_read_the_events_through_outcomes_only():
    wrong = []
    for path in APP.rglob('*.py'):
        name = path.relative_to(APP).as_posix()
        if name.startswith('integrations/instantly_sync/'):
            continue
        extra = _imports(ast.parse(path.read_text())) - ALLOWED.get(name, {'outcomes'})
        if extra:
            wrong.append(f'{name} imports {sorted(extra)}: read app.integrations.instantly_sync.outcomes')
    assert not wrong, '\n'.join(wrong)
