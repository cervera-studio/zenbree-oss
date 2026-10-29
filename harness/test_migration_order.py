"""A branch migration named before main's newest fails; one named after it, or main itself, passes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class MigrationOrderTest(unittest.TestCase):
    def test_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'harness').mkdir()
            (root / 'api/db').mkdir(parents=True)
            shutil.copyfile(Path(__file__).with_name('migration_order.py'), root / 'harness/migration_order.py')
            env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                   'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}

            def git(*args):
                subprocess.run(['git', *args], cwd=root, env=env, check=True, capture_output=True)

            def add(name):
                (root / 'api/db' / name).write_text('select 1;\n')

            def check():
                result = subprocess.run(['python3', 'harness/migration_order.py'], cwd=root,
                                        capture_output=True, text=True)
                return result.returncode, result.stderr

            git('init', '-q', '-b', 'main')
            self.assertEqual(check()[0], 1, 'no main to compare with fails rather than passes')
            add('20261006110243_organization_credits.sql')
            git('add', '-A')
            git('commit', '-qm', 'main')
            self.assertEqual(check(), (0, ''), 'main itself passes')

            git('checkout', '-qb', 'feat/x')
            add('20261006102759_unipile_account_unique.sql')  # the c77b83de merge, before its 53199471 rename
            code, err = check()
            self.assertEqual(code, 1, 'an uncommitted migration is checked too')
            self.assertIn('api/db/20261006102759_unipile_account_unique.sql sorts before '
                          '20261006110243_organization_credits.sql', err)
            self.assertIn('_unipile_account_unique.sql (it has run nowhere yet)', err)

            (root / 'api/db/20261006102759_unipile_account_unique.sql').rename(
                root / 'api/db/20261006123127_unipile_account_unique.sql')
            self.assertEqual(check(), (0, ''), 'the renamed migration passes')
            git('add', '-A')
            git('commit', '-qm', 'branch')

            git('checkout', '-q', 'main')
            add('20261007000000_newer_on_main.sql')
            git('add', '-A')
            git('commit', '-qm', 'main moves on')
            git('checkout', '-q', 'feat/x')
            self.assertEqual(check()[0], 1, 'main gaining a newer migration fails the branch until it renames')
