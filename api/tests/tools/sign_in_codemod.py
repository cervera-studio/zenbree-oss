"""Rewrite each test's override of an auth dependency into ``sign_in`` (tests/conftest.py), so the request still
runs ``current_user``: it enters ``Member`` and reads the membership in the database, as in production.

    uv run python tests/tools/sign_in_codemod.py [tests/test_x.py ...]   # default: every tests/test_*.py

- ``app.dependency_overrides[current_user | current_user_or_api_key | require_admin] = lambda ...: EXPR`` becomes
  ``sign_in(app, EXPR)``; ``= name`` becomes ``sign_in(app, name())``. A lambda's body ran at each request and now
  runs once, at the override: every body in tests/ only builds a ``CurrentUser``.
- Two overrides in a row with the same body become one ``sign_in``, which replaces both token steps.
- ``app.dependency_overrides.pop(<one of them>[, None])`` becomes ``sign_out(app)``.
- It adds the import from tests.conftest; ``ruff check --fix`` then drops the imports left unused.

It prints what it cannot rewrite, and what needs a hand edit after it: a ``CurrentUser`` that names its
``org_role`` (``sign_in`` reads the role from the database, so the test writes it there) and a ``require_admin``
override (the user also needs ``role = 'admin'`` in the database). tests/test_scope_entry_points.py fails while any
override is left.
"""

import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
NAMES = 'current_user|current_user_or_api_key|require_admin'
ASSIGN = re.compile(rf'^(?P<indent>\s*)app\.dependency_overrides\[(?P<dep>{NAMES})\] = (?P<rhs>.*)$')
POP = re.compile(rf'^(?P<indent>\s*)app\.dependency_overrides\.pop\(({NAMES})(?:, None)?\)$')
LAMBDA = re.compile(r'^lambda(?: [^:]*)?: (?P<body>.*)$', re.S)


def _balanced(text: str) -> bool:
    return text.count('(') == text.count(')') and text.count('[') == text.count(']')


def _rewrite(path: Path) -> tuple[str, list[str], set[str]]:
    lines = path.read_text().splitlines()
    out: list[str] = []
    notes: list[str] = []
    used: set[str] = set()
    previous: str | None = None  # the last sign_in line written, to fold a second override with the same body
    i = 0
    while i < len(lines):
        line = lines[i]
        if m := POP.match(line):
            out.append(f'{m["indent"]}sign_out(app)')
            used.add('sign_out')
            previous = None
            i += 1
            continue
        m = ASSIGN.match(line)
        if not m:
            out.append(line)
            previous = None
            i += 1
            continue
        rhs, start = m['rhs'], i
        while not _balanced(rhs) and i + 1 < len(lines):  # a body that spans lines
            i += 1
            rhs += '\n' + lines[i]
        i += 1
        if (lam := LAMBDA.match(rhs)) is not None:
            body = lam['body']
        elif re.fullmatch(r'\w+', rhs):
            body = f'{rhs}()'
        else:
            notes.append(f'{path.name}:{start + 1}: cannot rewrite, edit by hand: {line.strip()}')
            out.extend(lines[start:i])
            previous = None
            continue
        body = re.sub(r'\n\s*', ' ', body).replace('( ', '(').replace(' )', ')')
        new = f'{m["indent"]}sign_in(app, {body})'
        used.add('sign_in')
        if 'org_role=' in body:
            notes.append(f'{path.name}:{start + 1}: org_role is read from the database now: write it there')
        if m['dep'] == 'require_admin':
            notes.append(f'{path.name}:{start + 1}: require_admin also reads "user".role: make the user an admin')
        if new != previous:
            out.append(new)
        previous = new
    return '\n'.join(out) + '\n', notes, used


def _import(text: str, names: set[str]) -> str:
    """Add ``names`` to the file's ``from tests.conftest import``, or a new one after the last top-level import."""
    if not names:
        return text
    existing = re.search(r'^from tests\.conftest import (.+)$', text, re.M)
    if existing and '(' not in existing[1]:
        have = {n.strip() for n in existing[1].split(',')}
        line = f'from tests.conftest import {", ".join(sorted(have | names))}'
        return text[: existing.start()] + line + text[existing.end() :]
    lines = text.splitlines()
    last = max(n for n, line in enumerate(lines) if re.match(r'(from \S+ import|import) ', line))
    while lines[last].rstrip().endswith('('):  # a parenthesised import: insert after its closing line
        last = next(n for n in range(last, len(lines)) if lines[n].strip() == ')')
    lines.insert(last + 1, f'from tests.conftest import {", ".join(sorted(names))}')
    return '\n'.join(lines) + '\n'


def main(paths: list[str]) -> int:
    files = [Path(p).resolve() for p in paths] or sorted(TESTS.glob('test_*.py'))
    notes: list[str] = []
    for path in files:
        text, found, used = _rewrite(path)
        if not used:
            notes += found
            continue
        path.write_text(_import(text, used))
        notes += found
        sys.stdout.write(f'rewrote {path.relative_to(TESTS.parent)}\n')
    sys.stdout.write(''.join(f'{n}\n' for n in notes))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
