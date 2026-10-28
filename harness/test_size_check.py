"""The size ratchet: new files stay within MAX_LINES, baselined ones may only shrink, and must say so."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SizeCheckTest(unittest.TestCase):
    def test_ratchet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'harness').mkdir()
            (root / 'api/app').mkdir(parents=True)
            shutil.copyfile(Path(__file__).with_name('size_check.py'), root / 'harness/size_check.py')

            def write(name, lines):
                (root / 'api/app' / name).write_text('x = 1\n' * lines)

            def check(*args):
                return subprocess.run(['python3', 'harness/size_check.py', *args], cwd=root,
                                      capture_output=True, text=True)

            write('small.py', 400)
            write('test_big.py', 900)  # tests are not checked
            self.assertEqual(check('api').returncode, 0)
            write('god.py', 401)
            self.assertIn('god.py: 401 lines, is over 400', check('api').stderr)
            self.assertEqual(check('--update').returncode, 0)
            self.assertEqual(json.loads((root / 'harness/size-baseline.json').read_text()), {'api/app/god.py': 401})
            self.assertEqual(check('api').returncode, 0)
            write('god.py', 402)
            self.assertIn('grew past its baseline 401', check().stderr)
            write('god.py', 390)
            self.assertIn('below its baseline 401', check().stderr)
            check('--update')
            self.assertEqual(json.loads((root / 'harness/size-baseline.json').read_text()), {})
            self.assertEqual(check().returncode, 0)


if __name__ == '__main__':
    unittest.main()
