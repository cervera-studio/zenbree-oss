"""The web fixtures check: a changed, missing or extra file fails, the formatter's layout does not, and a run with
no test database fails instead of passing on fixtures nobody wrote."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from web_fixtures import differences


class WebFixturesTest(unittest.TestCase):
    def test_differences(self):
        with tempfile.TemporaryDirectory() as directory:
            produced, committed = Path(directory, 'produced'), Path(directory, 'committed')
            produced.mkdir()
            committed.mkdir()
            row = {'lookups': {'email': {'state': 'refunded'}}, 'seq': 2}
            (produced / 'email-refunded.json').write_text(json.dumps(row))
            (committed / 'email-refunded.json').write_text(json.dumps(row, indent=4))
            self.assertEqual(differences(produced, committed), [], 'the layout is not a difference')
            (committed / 'email-refunded.json').write_text(json.dumps({**row, 'seq': 2.0}))
            self.assertEqual(differences(produced, committed), ['email-refunded.json: changed'],
                             'a number that changed type is a difference, though 2 == 2.0 in Python')

            (committed / 'email-refunded.json').write_text(json.dumps({**row, 'seq': 3}))
            (produced / 'phone-found.json').write_text('{}')
            (committed / 'phone-gone.json').write_text('{}')
            self.assertEqual(differences(produced, committed), [
                'phone-found.json: not committed',
                'phone-gone.json: no API test wrote it (did a SQL test skip? eval "$(./harness/testdb.sh)" first)',
                'email-refunded.json: changed',
            ])

    def test_no_test_database_fails(self):
        env = {k: v for k, v in os.environ.items() if k != 'TEST_DATABASE_URL'}
        run = subprocess.run(['python3', str(Path(__file__).with_name('web_fixtures.py'))], env=env,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn('eval "$(./harness/testdb.sh)"', run.stderr)


if __name__ == '__main__':
    unittest.main()
