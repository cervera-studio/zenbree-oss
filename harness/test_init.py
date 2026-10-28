"""Exercise runner dispatch/failures without installing deps or touching a DB."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


class HarnessTest(unittest.TestCase):
    def test_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('harness', 'api', 'web', 'landing', 'bin'):
                (root / name).mkdir()
            script = root / 'harness/init.sh'
            shutil.copyfile(Path(__file__).with_name('init.sh'), script)
            shutil.copyfile(Path(__file__).with_name('heavy-lock.sh'), root / 'harness/heavy-lock.sh')
            testdb_script = root / 'harness/testdb.sh'
            testdb_script.write_text('#!/bin/bash\n'
                                     'echo "${PWD##*/}:testdb.sh" >> "$HARNESS_LOG"\n'
                                     '[[ -z "${HARNESS_DB_DOWN:-}" ]] || exit 1\n'
                                     'echo "export TEST_DATABASE_URL=postgresql://shared"\n')
            testdb_script.chmod(0o755)
            lock = root / 'heavy.lock'
            log = root / 'commands'
            for name in ('uv', 'pnpm', 'python3', 'gitleaks'):
                binary = root / 'bin' / name
                binary.write_text(
                    '#!/bin/bash\n'
                    'echo "${PWD##*/}:${0##*/} $*" >> "$HARNESS_LOG"\n'
                    'if [[ "$*" == "${HARNESS_FAIL:-}" ]]; then exit 17; fi\n'
                    'if [[ "$*" == "run pytest -q -n 4" && -z "$TEST_DATABASE_URL" ]]; then exit 9; fi\n'
                )
                binary.chmod(0o755)
            env = {**os.environ, 'PATH': f'{root / "bin"}:{os.environ["PATH"]}',
                   'HARNESS_LOG': str(log), 'TEST_DATABASE_URL': 'postgresql://test-only',
                   'ZENBREE_HEAVY_LOCK': str(lock)}
            # A fast run sets this for its children, and `init.sh all --fast` runs these tests.
            for key in ('HARNESS_FAIL', 'ZENBREE_FAST_CLAMPED'):
                env.pop(key, None)
            api = ['api:uv sync --locked', 'api:uv run ruff format --check .', 'api:uv run ruff check .',
                   'api:uv run mypy', 'api:uv run pytest -q -n 4']
            web = ['web:pnpm install --frozen-lockfile', 'web:pnpm lint',
                   'web:pnpm typecheck', 'web:pnpm knip', 'web:pnpm api:types:check', 'web:pnpm test', 'web:pnpm build']
            landing = ['landing:pnpm install --frozen-lockfile', 'landing:pnpm lint',
                       'landing:pnpm check', 'landing:pnpm test', 'landing:pnpm build']
            harness = ["harness:python3 run_tests.py"]

            def size(scope):
                return [f'{root.name}:python3 harness/size_check.py {scope}']

            leaks = [f'{root.name}:gitleaks git --no-banner --redact']
            mig = [f'{root.name}:python3 harness/migration_order.py']
            testdb = [f'{root.name}:testdb.sh']
            fx = [f'{root.name}:python3 harness/web_fixtures.py']

            cases = [
                ([], {}, 0, mig + size('all') + leaks + harness + api + fx + web + landing),
                (['api'], {}, 0, mig + size('api') + api + fx),
                (['web', '--fast'], {'TEST_DATABASE_URL': ''}, 0, size('web') + web[:5]),
                (['--fast'], {'TEST_DATABASE_URL': ''}, 0, mig + size('all') + leaks + harness + api[:4] + web[:5] + landing[:3]),
                (['api', '--fast'], {}, 0, mig + size('api') + api[:4]),
                (['web', '--fast'], {}, 0, size('web') + web[:5]),
                (['landing'], {'TEST_DATABASE_URL': ''}, 0, size('landing') + landing),
                (['landing', '--fast'], {}, 0, size('landing') + landing[:3]),
                ([], {'TEST_DATABASE_URL': ''}, 0, mig + testdb + size('all') + leaks + harness + api + fx + web + landing),
                (['api'], {'TEST_DATABASE_URL': ''}, 0, mig + testdb + size('api') + api + fx),
                (['web'], {'TEST_DATABASE_URL': ''}, 0, testdb + size('web') + fx + web),
                (['api'], {'TEST_DATABASE_URL': '', 'HARNESS_DB_DOWN': '1'}, 1, mig + testdb),
                (['--typo'], {}, 2, []),
                (['--help'], {}, 0, []),
                ([], {'HARNESS_FAIL': 'run_tests.py'}, 17, mig + size('all') + leaks + harness),
                ([], {'HARNESS_FAIL': 'sync --locked'}, 17, mig + size('all') + leaks + harness + api[:1]),
                ([], {'HARNESS_FAIL': 'run pytest -q -n 4'}, 17, mig + size('all') + leaks + harness + api),
                (['web'], {'HARNESS_FAIL': 'harness/web_fixtures.py'}, 17, size('web') + fx),
                (['web'], {'HARNESS_FAIL': 'lint'}, 17, size('web') + fx + web[:2]),
                (['web'], {'HARNESS_FAIL': 'build'}, 17, size('web') + fx + web),
                (['landing'], {'HARNESS_FAIL': 'lint'}, 17, size('landing') + landing[:2]),
                (['landing'], {'HARNESS_FAIL': 'check'}, 17, size('landing') + landing[:3]),
                (['landing'], {'HARNESS_FAIL': 'test'}, 17, size('landing') + landing[:4]),
            ]
            for args, overrides, code, commands in cases:
                with self.subTest(args=args, overrides=overrides):
                    log.write_text('')
                    result = subprocess.run(['bash', str(script), *args], cwd=root / 'web',
                                            env={**env, **overrides}, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(log.read_text().splitlines(), commands)
                    self.assertFalse(lock.exists(), 'the lock is released after every run')
                    if code:
                        self.assertNotIn('PASS', result.stdout)
                    if overrides.get('HARNESS_DB_DOWN'):
                        self.assertIn('start Docker, or set TEST_DATABASE_URL', result.stderr)
                    if code == 17:
                        self.assertIn(' (failed)', result.stdout, 'a failed step names its time too')
                    elif '--fast' in args:
                        self.assertIn('not full verification', result.stdout)
                    if code == 0 and commands and '--fast' not in args:
                        self.assertRegex(result.stdout, r'completed in \d+ s', 'the PASS line gives the total')
                    if code == 0 and commands:
                        step = re.escape(commands[-1].replace(':', ': ', 1))
                        self.assertRegex(result.stdout, rf'time: \d+ s for {step}', 'each step names its time')

            def run(args, timeout=30):
                log.write_text('')
                return subprocess.run(['bash', str(script), *args], cwd=root / 'web', env=env,
                                      capture_output=True, text=True, timeout=timeout)

            with self.subTest('a live holder blocks a full run, not a fast one'):
                lock.mkdir()
                (lock / 'pid').write_text(str(os.getpid()))
                (lock / 'what').write_text('init.sh all from another worktree')
                with self.assertRaises(subprocess.TimeoutExpired) as waited:
                    run(['api'], timeout=3)
                self.assertIn(b'waiting for the heavy-job lock: init.sh all from another worktree', waited.exception.stderr)
                self.assertEqual(log.read_text().splitlines(), mig, 'only the check that depends on main ran')
                self.assertEqual(run(['api', '--fast']).returncode, 0)
                self.assertEqual(log.read_text().splitlines(), mig + size('api') + api[:4])

            with self.subTest('a dead holder frees the lock'):
                dead = subprocess.Popen(['true'])
                dead.wait()
                (lock / 'pid').write_text(str(dead.pid))
                self.assertEqual(run(['api']).returncode, 0)
                self.assertEqual(log.read_text().splitlines(), mig + size('api') + api + fx)
                self.assertFalse(lock.exists())

            with self.subTest('a fast run yields the CPUs to a full run'):
                stub = root / 'qos'
                stub.mkdir()
                clamps = root / 'clamps'
                (stub / 'taskpolicy').write_text('#!/bin/bash\n'
                                                 f'echo "$*" >> {clamps}\n'
                                                 '[[ "$1 $2" == "-c utility" ]] || exit 64\n'
                                                 'shift 2\n'
                                                 'exec "$@"\n')
                (stub / 'taskpolicy').chmod(0o755)
                stubbed = {**env, 'PATH': f'{stub}:{env["PATH"]}'}
                no_taskpolicy = {**env, 'PATH': os.pathsep.join(
                    d for d in env['PATH'].split(os.pathsep) if not Path(d, 'taskpolicy').exists())}

                def clamped(args, run_env):
                    log.write_text('')
                    clamps.write_text('')
                    result = subprocess.run(['bash', str(script), *args], cwd=root / 'web', env=run_env,
                                            capture_output=True, text=True, timeout=30)
                    return result.returncode, log.read_text().splitlines(), clamps.read_text().splitlines()

                self.assertEqual(clamped(['web', '--fast'], stubbed),
                                 (0, size('web') + web[:5], [f'-c utility bash {script} web --fast']),
                                 'once, at utility QoS')
                self.assertEqual(clamped(['web'], stubbed), (0, size('web') + fx + web, []),
                                 'a full run keeps the default QoS')
                self.assertEqual(clamped(['web', '--fast'], no_taskpolicy)[:2], (0, size('web') + web[:5]),
                                 'without taskpolicy, as on Linux, the run is the same')

            with self.subTest('a holder leaves a lock that another job took over'):
                takeover = f'echo 999999 > {lock}/pid'
                helper = root / 'harness/heavy-lock.sh'
                subprocess.run(['bash', '-c', f'source {helper}; with_heavy_lock probe bash -c "{takeover}"'],
                               env=env, check=True)
                self.assertEqual((lock / 'pid').read_text().strip(), '999999')
                shutil.rmtree(lock)

    def test_pass_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('harness', 'api', 'web', 'landing', 'bin'):
                (root / name).mkdir()
            shutil.copyfile(Path(__file__).with_name('init.sh'), root / 'harness/init.sh')
            shutil.copyfile(Path(__file__).with_name('heavy-lock.sh'), root / 'harness/heavy-lock.sh')
            log, cache = root / 'commands', root / 'cache'
            for name in ('uv', 'pnpm', 'python3'):
                (root / 'bin' / name).write_text(
                    '#!/bin/bash\n'
                    'echo "${PWD##*/}:${0##*/} $*" >> "$HARNESS_LOG"\n'
                    'if [[ "$*" == "${HARNESS_FAIL:-}" ]]; then exit 17; fi\n'
                )
            # Stands in for `gh pr list --state merged ... --jq`: the head branches of the merged PRs, one per line.
            (root / 'bin/gh').write_text('#!/bin/bash\nif [[ -n "${HARNESS_GH_DOWN:-}" ]]; then exit 1; fi\n'
                                         'printf "%s\\n" ${HARNESS_MERGED:-}\n')
            for binary in (root / 'bin').iterdir():
                binary.chmod(0o755)
            (root / '.gitignore').write_text('bin/\ncommands\ncache/\nheavy.lock/\n')
            env = {**os.environ, 'PATH': f'{root / "bin"}:{os.environ["PATH"]}', 'HARNESS_LOG': str(log),
                   'TEST_DATABASE_URL': 'postgresql://test-only', 'ZENBREE_HEAVY_LOCK': str(root / 'heavy.lock'),
                   'ZENBREE_GATE_CACHE': str(cache), 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                   'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
            for key in ('HARNESS_FAIL', 'HARNESS_MERGED', 'HARNESS_GH_DOWN', 'ZENBREE_FAST_CLAMPED'):
                env.pop(key, None)

            def git(*args):
                subprocess.run(['git', *args], cwd=root, env=env, check=True, capture_output=True)

            def commit(text):
                (root / 'api/app.py').write_text(text)
                git('add', '-A')
                git('commit', '-qm', text)

            def run(args=('api',), **overrides):
                log.write_text('')
                result = subprocess.run(['bash', 'harness/init.sh', *args], cwd=root, env={**env, **overrides},
                                        capture_output=True, text=True, timeout=30)
                return result.returncode, log.read_text().splitlines(), result.stdout

            mig = [f'{root.name}:python3 harness/migration_order.py']
            api = [*mig, f'{root.name}:python3 harness/size_check.py api',
                   'api:uv sync --locked',
                   'api:uv run ruff format --check .', 'api:uv run ruff check .', 'api:uv run mypy',
                   'api:uv run pytest -q -n 4', f'{root.name}:python3 harness/web_fixtures.py']
            git('init', '-q', '-b', 'main')
            commit('one')
            git('checkout', '-qb', 'feat/x')

            self.assertEqual(run()[:2], (0, api), 'the first full run checks')
            code, commands, out = run()
            self.assertEqual((code, commands), (0, mig), 'a rerun on the same tree checks only what depends on main')
            self.assertIn('PASS (api, cached)', out)
            git('commit', '-q', '--allow-empty', '-m', 'rebased')
            self.assertEqual(run()[1], mig, 'a new commit on the same tree hits the cache')
            entry = next(cache.glob('feat%x/*-api'))
            head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=root, capture_output=True,
                                  text=True).stdout.strip()
            self.assertTrue(entry.read_text().startswith(f'{head} at '), 'and moves the pass to it')
            code, commands, out = run(HARNESS_FAIL='harness/migration_order.py')
            self.assertEqual((code, commands), (17, mig), 'a pass on this tree does not cover a newer migration on main')
            self.assertNotIn('PASS', out)
            self.assertNotEqual(run(['web'])[1], [], 'the scope is part of the key')
            self.assertEqual(run(['web'])[1], [])
            self.assertEqual(run(['api', '--fast'])[1], api[:6], 'a fast run is never cached')

            (root / 'api/app.py').write_text('edited')
            self.assertEqual(run()[1], api, 'uncommitted changes have no stable key')
            self.assertEqual(run()[1], api, 'so nothing was kept for them')
            git('checkout', '-q', '--', '.')

            commit('two')
            self.assertEqual(run(HARNESS_FAIL='run mypy')[0], 17)
            self.assertEqual(run()[1], api, 'a failed run is not kept')
            self.assertEqual(run()[1], mig)

            self.assertEqual(run(HARNESS_GH_DOWN='1')[1], api, 'no entry is trusted when GitHub cannot list merges')
            (cache / 'feat%y').mkdir()
            self.assertEqual(run(HARNESS_MERGED='other feat/x')[1], api, 'the merge of the PR clears its entries')
            self.assertEqual(run(HARNESS_MERGED='feat/x')[1], api, 'and clears them again while it stays merged')
            self.assertTrue((cache / 'feat%y').exists(), 'the entries of other branches stay')

            git('checkout', '-q', 'main')
            self.assertEqual(run()[1], api, 'main is never cached')
            self.assertEqual(run()[1], api)
