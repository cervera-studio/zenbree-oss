"""No God modules (SOLID: one responsibility per module): a source file stays within MAX_LINES.

Files already over it are listed in size-baseline.json with their line count, which may only go down:
a file that shrinks must lower its entry (``--update`` rewrites the list), so the next change cannot regrow it.
Usage: python3 harness/size_check.py [all|api|web|landing] [--update]
"""

import json
import sys
from pathlib import Path

MAX_LINES = 400
ROOTS = {'api': ('api/app',), 'web': ('web/app', 'web/components', 'web/lib'), 'landing': ('landing/src',)}
SUFFIXES = {'.py', '.ts', '.tsx', '.astro', '.mjs'}
REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / 'harness/size-baseline.json'
#: Written by a script, never by hand: a hand-written file cannot hide from the cap by its name.
GENERATED = {'web/lib/api/schema.gen.ts'}


def sizes(areas):
    for area in areas:
        for root in ROOTS[area]:
            for path in sorted((REPO / root).rglob('*')):
                if (
                    path.suffix in SUFFIXES
                    and path.is_file()
                    and '.test.' not in path.name
                    and not path.name.startswith('test_')
                    and path.relative_to(REPO).as_posix() not in GENERATED
                    and 'node_modules' not in path.parts
                ):
                    yield path.relative_to(REPO).as_posix(), len(path.read_text().splitlines())


def main(argv):
    scope = next((a for a in argv if not a.startswith('--')), 'all')
    areas = list(ROOTS) if scope == 'all' else [scope]
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    found = dict(sizes(areas))
    if '--update' in argv:
        kept = {k: v for k, v in baseline.items() if not k.startswith(tuple(f'{a}/' for a in areas))}
        kept.update({k: n for k, n in found.items() if n > MAX_LINES})
        BASELINE.write_text(json.dumps(dict(sorted(kept.items())), indent=2) + '\n')
        return 0
    errors = []
    for path, n in found.items():
        cap = baseline.get(path)
        if n > max(MAX_LINES, cap or 0):
            what = f'grew past its baseline {cap}' if cap else f'is over {MAX_LINES} lines'
            errors.append(f'{path}: {n} lines, {what}. Split it by responsibility (no God modules).')
        elif cap and n < cap:
            errors.append(f'{path}: {n} lines, below its baseline {cap}. Lower it: python3 harness/size_check.py --update')
    for path in baseline:
        if path.startswith(tuple(f'{a}/' for a in areas)) and path not in found:
            errors.append(f'{path}: in the baseline but gone. Run python3 harness/size_check.py --update')
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
