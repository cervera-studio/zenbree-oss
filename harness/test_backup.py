"""Backup ordering and cleanup with synthetic data; no Docker, R2 or secrets."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BackupTest(unittest.TestCase):
    def test_dump_upload_heartbeat_and_failures(self):
        script = Path(__file__).resolve().parent.parent / 'ops' / 'backup.sh'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries, dumps = root / 'bin', root / 'dumps'
            binaries.mkdir()
            dumps.mkdir()
            log = root / 'calls'
            # no .env: the deploy deletes it (#425), the settings come from Infisical
            token = root / 'token'
            token.write_text('test-token\n')
            for name in ('infisical', 'docker', 'rclone', 'curl'):
                binary = binaries / name
                binary.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, stat, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['BACKUP_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'infisical':
    assert '--token=test-token' in sys.argv
    if sys.argv[1] == 'export':
        output = next(arg.removeprefix('--output-file=') for arg in sys.argv if arg.startswith('--output-file='))
        # a value with a quote must stay data: sourcing this file would create MARKER
        pathlib.Path(output).write_text("BACKUP_BUCKET='test-bucket'\\nOTHER=''$(touch MARKER)''\\n")
    else:
        assert sys.argv[1:3] == ['secrets', 'get'] and '--plain' in sys.argv
        print({'BACKUP_BUCKET': 'test-bucket', 'BACKUP_HEARTBEAT_URL': 'https://heartbeat.test'}[sys.argv[3]])
if name == 'docker':
    assert '--env-file' in sys.argv
    command = ' '.join(sys.argv)
    if 'pg_dumpall --roles-only --no-role-passwords' in command:
        if os.environ.get('BACKUP_TEST_EMPTY_ROLES') != '1':
            sys.stdout.write('CREATE ROLE zenbree_app;')
    else:
        assert 'pg_dump -Fc' in command
        sys.stdout.write('synthetic dump')
if name == 'rclone':
    assert sys.argv[1] == 'copyto'
    source = pathlib.Path(sys.argv[2])
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert sys.argv[3].startswith('r2:test-bucket/pg/')
    if sys.argv[3].endswith('.roles.sql'):
        assert source.read_text() == 'CREATE ROLE zenbree_app;'
    else:
        assert sys.argv[3].endswith('.dump') and source.read_text() == 'synthetic dump'
    assert sys.argv[4:] == ['--s3-no-head']
if os.environ.get('BACKUP_TEST_FAIL') == name:
    sys.exit(17)
''')
                binary.chmod(0o755)
            everything = ['infisical', 'infisical', 'infisical', 'docker', 'rclone', 'docker', 'rclone', 'curl']
            # 'empty-roles' is no binary name: the fake docker prints no roles SQL and the script stops itself
            for failure, expected in (('', everything), ('infisical', everything[:1]), ('docker', everything[:4]),
                                      ('rclone', everything[:5]), ('curl', everything),
                                      ('empty-roles', everything[:6])):
                with self.subTest(failure=failure):
                    log.write_text('')
                    env = {**os.environ, 'PATH': f'{binaries}:{os.environ["PATH"]}', 'TMPDIR': str(dumps),
                           'BACKUP_TEST_LOG': str(log), 'BACKUP_TEST_FAIL': failure,
                           'BACKUP_TEST_EMPTY_ROLES': '1' if failure == 'empty-roles' else '0',
                           'ZENBREE_INFISICAL_TOKEN_FILE': str(token), 'INFISICAL_PROJECT_ID': 'test-project'}
                    result = subprocess.run(['bash', str(script)], cwd=root, env=env,
                                            capture_output=True, text=True, check=False)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    for call in calls:
                        if call[0] == 'infisical':
                            self.assertIn('--projectId=test-project', call)
                    code = {'': 0, 'empty-roles': 1}.get(failure, 17)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual([call[0] for call in calls], expected)
                    uploads = [call[3] for call in calls if call[0] == 'rclone']
                    if failure in ('', 'curl'):
                        stamps = {upload.rsplit('/', 1)[1].split('.')[0] for upload in uploads}
                        self.assertEqual(len(stamps), 1)
                        stamp = stamps.pop()
                        self.assertEqual(uploads, [f'r2:test-bucket/pg/{stamp}.dump',
                                                   f'r2:test-bucket/pg/{stamp}.roles.sql'])
                    if failure == 'empty-roles':
                        self.assertIn('roles dump is empty', result.stderr)
                        self.assertEqual(len(uploads), 1)
                    self.assertEqual(list(dumps.iterdir()), [])
                    self.assertEqual('backup ok' in result.stdout, not failure)
                    self.assertFalse((root / '.env').exists())
                    self.assertFalse((root / 'MARKER').exists())
