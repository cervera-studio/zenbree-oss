"""The web tests read rows, summaries and frames that API tests write (#661): web/lib/api/fixtures. This fails when
the committed files differ from what the API tests write now (a file changed, missing or extra), so a change to
what the API stores also changes what the web tests see.

Usage: python3 harness/web_fixtures.py [--update]
Needs TEST_DATABASE_URL: most producers are SQL tests. --update rewrites the committed folder."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / 'web/lib/api/fixtures'
UPDATE = 'python3 harness/web_fixtures.py --update'
TESTDB = 'eval "$(./harness/testdb.sh)"'


def produce(out: Path) -> bool:
    """Runs the API tests marked ``web_fixture``, which write their fixtures to ``out``."""
    if not os.environ.get('TEST_DATABASE_URL'):
        print(f'TEST_DATABASE_URL is not set, so the SQL tests that write fixtures would skip. Run {TESTDB} first.',
              file=sys.stderr)
        return False
    run = subprocess.run(['uv', 'run', 'pytest', '-m', 'web_fixture', '-q', '-p', 'no:cacheprovider'],
                         cwd=REPO / 'api', env={**os.environ, 'WEB_FIXTURES_OUT': str(out)}, check=False)
    if run.returncode:
        print('the API tests that write the web fixtures failed', file=sys.stderr)
    return run.returncode == 0


def canonical(path: Path) -> str:
    """The file's JSON without the formatter's layout. Compared as text, since in Python 1 == 1.0 == True."""
    return json.dumps(json.loads(path.read_text()), sort_keys=True)


def differences(expected: Path, committed: Path) -> list[str]:
    """Each file whose JSON differs, and each file only one side has."""
    new = {p.name for p in expected.glob('*.json')}
    old = {p.name for p in committed.glob('*.json')}
    diffs = [f'{name}: not committed' for name in sorted(new - old)]
    diffs += [f'{name}: no API test wrote it (did a SQL test skip? {TESTDB} first)' for name in sorted(old - new)]
    diffs += [f'{name}: changed' for name in sorted(new & old) if canonical(expected / name) != canonical(committed / name)]
    return diffs


def update(produced: Path) -> int:
    if not any(produced.glob('*.json')):
        print('the API tests wrote no fixture: the committed ones are kept', file=sys.stderr)
        return 1
    shutil.rmtree(FIXTURES, ignore_errors=True)
    shutil.copytree(produced, FIXTURES)
    # Biome formats JSON under web/ (pnpm lint), and its config may not change: write its layout.
    return subprocess.run(['pnpm', 'exec', 'biome', 'format', '--write', 'lib/api/fixtures'], cwd=REPO / 'web',
                          check=False).returncode


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory() as directory:
        produced = Path(directory)
        if not produce(produced):
            return 1
        if '--update' in argv:
            return update(produced)
        diffs = differences(produced, FIXTURES)
    for diff in diffs:
        print(f'web/lib/api/fixtures/{diff}', file=sys.stderr)
    if diffs:
        print(f'The web fixtures differ from what the API tests write now. Regenerate them: {UPDATE}', file=sys.stderr)
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
