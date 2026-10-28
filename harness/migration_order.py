"""A new migration sorts after every migration on main.

The runner applies pending migrations in name order, but production applied main's migrations in merge order.
A branch migration named before main's newest runs last in production and earlier on a fresh database, so the
two schemas are built in different orders (7 merges did this; 53199471 had to rename one after the fact).
A migration that has not merged has run nowhere that matters, so renaming it is safe.
Usage: python3 harness/migration_order.py [--base REF]   (default: the newest of origin/main and main)
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = 'api/db'
NAME = re.compile(r'^\d{14}_.+\.sql$')


def git(*args):
    return subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True)


def on_ref(ref):
    listing = git('ls-tree', '--name-only', f'{ref}:{DB}')
    return {n for n in listing.stdout.split() if NAME.match(n)} if listing.returncode == 0 else None


def main(argv):
    if argv and (argv[0] != '--base' or len(argv) != 2):
        print('Usage: python3 harness/migration_order.py [--base REF]', file=sys.stderr)
        return 2
    refs = argv[1:] or ['origin/main', 'main']
    listings = [names for names in map(on_ref, refs) if names is not None]
    if not listings:
        print(f'migration_order: none of {", ".join(refs)} exists. Fetch main: git fetch origin main', file=sys.stderr)
        return 1
    merged = set().union(*listings)
    newest = max(merged, default='')
    here = {p.name for p in (REPO / DB).glob('*.sql') if NAME.match(p.name)}
    late = sorted(n for n in here - merged if n < newest)
    for name in late:
        print(
            f'{DB}/{name} sorts before {newest}, which main already has. Rename it to '
            f'{DB}/$(date -u +%Y%m%d%H%M%S)_{name[15:]} (it has run nowhere yet).',
            file=sys.stderr,
        )
    return 1 if late else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
