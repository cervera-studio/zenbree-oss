"""The census names the commit that wrote the lines each fix repairs, and skips lines in tests."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class FixCensusTest(unittest.TestCase):
    def test_census(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'harness').mkdir()
            (root / 'api/app').mkdir(parents=True)
            (root / 'api/tests').mkdir(parents=True)
            shutil.copyfile(Path(__file__).with_name('fix_census.py'), root / 'harness/fix_census.py')
            env = {
                **os.environ,
                'GIT_AUTHOR_NAME': 't',
                'GIT_AUTHOR_EMAIL': 't@t',
                'GIT_COMMITTER_NAME': 't',
                'GIT_COMMITTER_EMAIL': 't@t',
            }

            def commit(subject, path, text):
                (root / path).write_text(text)
                subprocess.run(['git', 'add', '-A'], cwd=root, env=env, check=True, capture_output=True)
                subprocess.run(['git', 'commit', '-qm', subject], cwd=root, env=env, check=True, capture_output=True)

            subprocess.run(['git', 'init', '-qb', 'main'], cwd=root, check=True)
            commit('feat: a', 'api/app/a.py', 'one = 1\ntwo = 2\n')
            commit('fix: two', 'api/app/a.py', 'one = 1\ntwo = 3\n')
            commit('fix: two again', 'api/app/a.py', 'one = 1\ntwo = 4\n')
            commit('fix: a test only', 'api/tests/test_a.py', 'x = 1\n')
            commit('fix: add a line', 'api/app/a.py', 'one = 1\ntwo = 4\nthree = 3\n')

            out = root / 'out.tsv'
            subprocess.run(['python3', 'harness/fix_census.py', str(out)], cwd=root, check=True, capture_output=True)
            kinds = {line.split('\t')[3]: line.split('\t')[1] for line in out.read_text().splitlines()}
            self.assertEqual(
                kinds,
                {
                    'fix: add a line': 'no-src-lines',
                    'fix: a test only': 'no-src-lines',
                    'fix: two again': 'fix-of-fix',
                    'fix: two': 'feat/initial',
                },
            )


if __name__ == '__main__':
    unittest.main()
