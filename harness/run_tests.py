"""Runs the harness tests, one process per test, four at a time.

The hook, stack and init tests spend their time waiting on the subprocesses they start, so a serial
`unittest discover` took 63 s of the gate's 200 s. Usage: python3 harness/run_tests.py"""

import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# As many as pytest's workers in init.sh: other agents share the machine.
WORKERS = 4


def test_ids(suite: unittest.TestSuite) -> list[str]:
    ids = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            ids += test_ids(test)
        elif isinstance(test, unittest.loader._FailedTest):  # an import error: run it so it fails by its own name
            ids.append(test.id().removeprefix('unittest.loader._FailedTest.'))
        else:
            ids.append(test.id())
    return ids


def run(test_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, '-m', 'unittest', '-q', test_id], cwd=Path(__file__).resolve().parent,
                          capture_output=True, text=True, check=False)


def main() -> int:
    here = Path(__file__).resolve().parent
    start = time.monotonic()
    ids = test_ids(unittest.defaultTestLoader.discover(str(here), pattern='test_*.py', top_level_dir=str(here)))
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(zip(ids, pool.map(run, ids)))
    failed = [(test_id, result) for test_id, result in results if result.returncode]
    for test_id, result in failed:
        print(f'FAIL {test_id}\n{result.stdout}{result.stderr}', file=sys.stderr)
    print(f'Ran {len(ids)} harness tests in {time.monotonic() - start:.1f} s: '
          f'{"FAILED (" + str(len(failed)) + ")" if failed else "OK"}')
    return 1 if failed or not ids else 0


if __name__ == '__main__':
    sys.exit(main())
