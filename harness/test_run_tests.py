"""The parallel runner fails the gate step when one test fails or cannot import, and passes when all pass."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RunTestsTest(unittest.TestCase):
    def run_in(self, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(Path(__file__).with_name('run_tests.py'), root / 'run_tests.py')
            for name, body in files.items():
                (root / name).write_text(body)
            return subprocess.run([sys.executable, str(root / 'run_tests.py')], capture_output=True, text=True,
                                  check=False)

    def test_exit_code_follows_the_tests(self):
        ok = 'import unittest\nclass A(unittest.TestCase):\n    def test_a(self): pass\n'
        bad = 'import unittest\nclass B(unittest.TestCase):\n    def test_b(self): self.fail("boom")\n'
        self.assertEqual(self.run_in({'test_ok.py': ok}).returncode, 0)
        failed = self.run_in({'test_ok.py': ok, 'test_bad.py': bad})
        self.assertEqual(failed.returncode, 1)
        self.assertIn('FAIL test_bad.B.test_b', failed.stderr)
        self.assertIn('boom', failed.stderr)
        broken = self.run_in({'test_ok.py': ok, 'test_broken.py': 'import nothing_by_this_name\n'})
        self.assertEqual(broken.returncode, 1)
        self.assertIn('nothing_by_this_name', broken.stderr)
        self.assertEqual(self.run_in({}).returncode, 1, 'no test found is a failure, not a pass')
