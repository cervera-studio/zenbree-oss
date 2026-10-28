"""`bin/stack up` in a throwaway checkout, against a fake docker: memory admission, the build lock and the LLM
key guard. Nothing is built or started. FAKE_STATS lines are `<id> <name> <use> / <limit>`, as docker stats prints."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
STACK = '.claude/skills/verify/bin/stack'
LLM_VARS = ('DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY', 'VERIFY_LLM')

FAKE_DOCKER = r"""#!/bin/sh
case "$1" in
  info) echo $((${FAKE_TOTAL_MIB:-5922} * 1048576)) ;;
  stats) [ -z "${FAKE_HANG:-}" ] || exec sleep 60; printf '%s\n' "${FAKE_STATS:-}" ;;
  ps) case "$*" in *' -q'*) echo "${FAKE_OWN_IDS:-}" ;; *working_dir*) echo "${FAKE_PROJECT_DIR:-}" ;; *compose.project*) echo "${FAKE_PROJECTS:-}" ;; esac ;;
  image) [ -n "${FAKE_IMAGES:-}" ] || exit 1 ;;
  compose) case "$*" in *' down '*) exit 0 ;; *' printenv '*) printf '%s' "${FAKE_API_KEY:-}"; exit 0 ;;
      *' ps '*) printf '%s\n' "${FAKE_PS:-}"; exit 0 ;; *' config '*) printf '%s' "${FAKE_CONFIG:-}"; exit 0 ;;
      *' port web 3000') echo "127.0.0.1:$VERIFY_WEB_PORT"; exit 0 ;; *' port api 8000') echo "127.0.0.1:$VERIFY_API_PORT"; exit 0 ;;
      *' stop') echo stop >>"$VERIFY_HOME/compose-calls"; exit 0 ;;
      *' build '*) echo build >>"$VERIFY_HOME/compose-calls"
        [ -d "$VERIFY_HOME/build.lock" ] && [ -d "$VERIFY_HOME/heavy.lock" ] &&
        printenv BUILDX_BUILDER >"$VERIFY_HOME/built-under-lock" ;; esac
    printenv DEEPSEEK_API_KEY >"$VERIFY_HOME/compose-key"
    cat "$VERIFY_HOME/$VERIFY_ID/reserve" >"$VERIFY_HOME/captured" 2>/dev/null; exit 1 ;;
esac
exit 0
"""


def git(cwd, *args):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull}
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, env=env)


class StackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'bin').mkdir()
        docker = self.home / 'bin/docker'
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        # A checkout of its own, so neither a developer's .env nor the real tree reaches the script.
        self.checkout = self.home / 'checkout'
        for path in (STACK, f'{Path(STACK).parent}/llm-key.sh', 'harness/heavy-lock.sh'):
            (self.checkout / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / path, self.checkout / path)
        for context in ('api', 'web'):  # the image tags hash these build contexts
            (self.checkout / context).mkdir()
            (self.checkout / context / '.keep').touch()
        git(self.checkout, 'init', '-q')
        git(self.checkout, 'add', '-A')
        git(self.checkout, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false',
            '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'init')

    def stack(self, *args, checkout=None, **env):
        base = {k: v for k, v in os.environ.items() if k not in LLM_VARS} | {'GIT_CONFIG_GLOBAL': os.devnull}
        env = {**base, 'PATH': f'{self.home / "bin"}:{os.environ["PATH"]}', 'VERIFY_HOME': str(self.home),
               'VERIFY_ID': 'mine', 'VERIFY_WEB_PORT': '23871', 'VERIFY_API_PORT': '23872',
               'VERIFY_WAIT_MIN': '0', 'VERIFY_POLL_SEC': '0',
               'ZENBREE_HEAVY_LOCK': str(self.home / 'heavy.lock'), **env}
        script = (checkout or self.checkout) / STACK
        return subprocess.run([str(script), *args], env=env, capture_output=True, text=True, timeout=60)

    def up(self, **env):
        return self.stack('up', **env)


class VerifyIdTest(StackTest):
    def test_a_capital_letter_dies_before_any_state_exists(self):
        result = self.up(VERIFY_ID='orgscopeA')
        self.assertEqual(result.returncode, 2)
        self.assertIn('lowercase letters, digits', result.stderr)
        self.assertFalse((self.home / 'orgscopeA').exists())


class RequiredIdTest(StackTest):
    def test_up_without_a_verify_id_dies_before_any_state_exists(self):
        result = self.up(VERIFY_ID='')
        self.assertEqual(result.returncode, 2)
        self.assertIn('set VERIFY_ID=<name>', result.stderr)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ['bin', 'checkout'])

    def test_help_and_prune_need_no_verify_id(self):
        for command in ('help', '--help'):
            self.assertIn('bin/stack up', self.stack(command, VERIFY_ID='').stderr)
        (self.home / 'default').mkdir()
        (self.home / 'default/stack.env').touch()
        result = self.stack('prune', VERIFY_ID='', FAKE_PROJECTS='zenbree-verify-ghost', FAKE_PROJECT_DIR='/gone/checkout')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('reaped: zenbree-verify-ghost', result.stderr)
        self.assertFalse((self.home / 'default/lease').exists(), 'no lease renewed for an old default stack')


class DownTest(StackTest):
    def test_down_removes_every_chrome_profile_and_keeps_the_artifacts(self):
        state = self.home / 'mine'
        for profile in ('chrome', 'cy/mine/chrome', 'admin'):
            (state / profile).mkdir(parents=True)
            (state / profile / 'Local State').touch()
        (state / 'artifacts').mkdir()
        (state / 'artifacts/proof.png').touch()
        (state / 'stack.env').touch()
        self.assertEqual(self.stack('down').returncode, 0)
        self.assertEqual(sorted(p.name for p in state.rglob('*')), ['artifacts', 'cy', 'mine', 'proof.png'])

    def test_prune_reaps_a_stack_with_no_state_dir(self):
        result = self.stack('prune', FAKE_PROJECTS='zenbree-verify-ghost', FAKE_PROJECT_DIR='/gone/checkout')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('reaped: zenbree-verify-ghost', result.stderr)


class AdmissionTest(StackTest):
    def captured(self):
        return (self.home / 'captured').read_text().split()

    def reserve(self, id, pid, mib):
        (self.home / id).mkdir()
        (self.home / id / 'reserve').write_text(f'{pid} {mib}\n')

    def test_a_missing_image_reserves_the_build(self):
        result = self.up(FAKE_STATS='abc zenbree-web-1 700MiB / 5.78GiB')
        self.assertEqual(self.captured()[1], '2816', result.stderr)
        self.assertFalse((self.home / 'mine/reserve').exists(), 'the reservation is removed when up ends')

    def test_a_build_runs_in_the_capped_builder_under_the_build_and_gate_locks_and_frees_them(self):
        result = self.up()
        self.assertEqual((self.home / 'built-under-lock').read_text().strip(), 'zenbree-verify', result.stderr)
        self.assertFalse((self.home / 'build.lock').exists(), 'a failed build frees the lock')
        self.assertFalse((self.home / 'heavy.lock').exists(), 'a failed build frees the gate lock')

    def test_present_images_reserve_only_the_stack(self):
        self.up(FAKE_IMAGES='1')
        self.assertEqual(self.captured()[1], '1552')
        self.assertFalse((self.home / 'built-under-lock').exists(), 'no build, no lock')

    def test_a_full_vm_waits_then_dies_naming_the_numbers(self):
        result = self.up(FAKE_STATS='abc zenbree-worker-1 4GiB / 5.78GiB')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no Docker memory after 0 min: need 2816 MiB, free 1058 of 5922 MiB', result.stderr)
        self.assertFalse((self.home / 'captured').exists())

    def test_a_hung_docker_stats_fails_admission_and_frees_the_lock(self):
        result = self.up(FAKE_HANG='1', VERIFY_DOCKER_SEC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('docker did not answer in 1 s', result.stderr)
        self.assertFalse((self.home / 'admit.lock').exists())
        self.assertFalse((self.home / 'captured').exists())

    def test_units_are_parsed(self):
        result = self.up(FAKE_STATS='a x 1GiB / 5GiB\nb x 512MiB / 5GiB\nc x 1048576B / 5GiB\nd x 1024KiB / 5GiB',
                         FAKE_TOTAL_MIB='4000')
        self.assertIn('need 2816 MiB, free 1694 of 4000 MiB', result.stderr)  # 4000 - 768 - (1024+512+1+1)

    def test_a_verify_container_counts_at_its_ceiling_and_one_without_at_its_use(self):
        result = self.up(FAKE_STATS='a zenbree-verify-x-worker-1 100MiB / 384MiB\n'
                                    'b zenbree-verify-x-llmstub-1 50MiB / 2.9296875GiB', FAKE_TOTAL_MIB='3000')
        self.assertIn('need 2816 MiB, free 1798 of 3000 MiB', result.stderr)  # 3000 - 768 - (384+50)

    def test_the_verify_builder_is_not_counted_twice_next_to_its_reservation(self):
        result = self.up(FAKE_STATS='b buildx_buildkit_zenbree-verify0 2.5GiB / 2.75GiB', FAKE_TOTAL_MIB='3000')
        self.assertIn('need 2816 MiB, free 2232 of 3000 MiB', result.stderr)  # 3000 - 768

    def test_the_developers_stack_counts_at_its_use_not_its_ceiling(self):
        result = self.up(FAKE_STATS='a zenbree-worker-1 100MiB / 2GiB', FAKE_TOTAL_MIB='3000')
        self.assertIn('need 2816 MiB, free 2132 of 3000 MiB', result.stderr)  # 3000 - 768 - 100

    def test_a_live_reservation_of_another_stack_counts(self):
        sleeper = subprocess.Popen(['sleep', '60'])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.reserve('other', sleeper.pid, 3200)
        result = self.up()
        self.assertIn('need 2816 MiB, free 1954 of 5922 MiB', result.stderr)
        self.assertIn('starting: other', result.stderr)

    def test_a_second_build_waits_for_the_memory_of_the_first(self):  # a build takes about a minute
        sleeper = subprocess.Popen(['sleep', '60'])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.reserve('other', sleeper.pid, 2816)
        result = self.up()
        self.assertIn('need 2816 MiB, free 2338 of 5922 MiB', result.stderr)  # 5922 - 768 - 2816

    def test_a_dead_reservation_is_ignored(self):
        dead = subprocess.Popen(['true'])
        dead.wait()
        self.reserve('other', dead.pid, 4000)
        self.up()
        self.assertEqual(self.captured()[1], '2816')
        self.assertFalse((self.home / 'other/reserve').exists())

    def test_a_reup_without_a_build_does_not_count_its_own_containers_twice(self):
        self.up(FAKE_STATS='own1 zenbree-verify-mine-web-1 4GiB / 5.78GiB', FAKE_OWN_IDS='own1', FAKE_IMAGES='1')
        self.assertEqual(self.captured()[1], '1552')

    def test_a_reup_that_builds_beside_another_stack_stops_its_old_containers_before_the_build(self):
        result = self.up(FAKE_STATS='own1 zenbree-verify-mine-worker-1 100MiB / 1GiB\n'
                                    'other zenbree-verify-x-worker-1 100MiB / 1552MiB', FAKE_OWN_IDS='own1')
        self.assertEqual(self.captured()[1], '2816', result.stderr)
        self.assertEqual((self.home / 'compose-calls').read_text().split(), ['stop', 'build'])

    def test_a_reup_whose_container_id_starts_with_digits_is_not_charged_as_another_container(self):
        result = self.up(FAKE_STATS='659721abcdef zenbree-verify-mine-beat-1 5GiB / 5.78GiB', FAKE_OWN_IDS='659721abcdef')
        self.assertEqual(self.captured()[1], '2816', result.stderr)


class CeilingsTest(unittest.TestCase):
    OVERLAY = REPO / '.claude/skills/verify/compose.verify.yml'

    def ceilings(self):  # service -> MiB, from the overlay's `  <service>:` blocks
        text = self.OVERLAY.read_text().split('\nservices:\n', 1)[1]
        blocks = re.split(r'^  ([a-z-]+):', text, flags=re.M)[1:]
        return {name: int(m[1]) if (m := re.search(r'mem_limit: (\d+)m', body)) else None
                for name, body in zip(blocks[::2], blocks[1::2])}

    def test_every_started_service_has_a_ceiling_and_their_sum_is_the_stack_charge(self):
        stack = (REPO / STACK).read_text()
        started = re.search(r'dc up -d --no-build ([a-z -]+)\n', stack)[1].split()
        ceilings = self.ceilings()
        for service in started:
            self.assertIsNotNone(ceilings.get(service), f'{service} has no mem_limit in {self.OVERLAY.name}')
        charge = int(re.search(r'STACK_MIB="\$\{VERIFY_STACK_MIB:-(\d+)\}"', stack)[1])
        self.assertEqual(charge, sum(ceilings[s] for s in started),
                         f'set VERIFY_STACK_MIB in {STACK} to the sum of the ceilings')


class DoctorServicesTest(StackTest):
    """FAKE_CONFIG has the shape of `docker compose config --format json`: migrate is the one-shot api waits on.
    Ports, /health and jwks answer, and the stack is keyless on purpose, so only the services decide the verdict."""
    LONG_RUNNING = ('api', 'beat', 'postgres', 'redis', 'web', 'worker', 'worker-webhooks')
    CONFIG = json.dumps({'services': {name: {} for name in LONG_RUNNING} | {
        'migrate': {}, 'api': {'depends_on': {'migrate': {'condition': 'service_completed_successfully'}}}}})

    def setUp(self):
        super().setUp()
        curl = self.home / 'bin/curl'
        curl.write_text('#!/bin/sh\nexit 0\n')
        curl.chmod(0o755)
        self.up(VERIFY_LLM='off')  # fails at compose up, after it records the image tags: the build is current

    def doctor(self, verify_id='mine', **states):
        ps = {name: 'running 0' for name in self.LONG_RUNNING} | {'migrate': 'exited 0'} | states
        result = self.stack('doctor', VERIFY_ID=verify_id, FAKE_CONFIG=self.CONFIG,
                            FAKE_PS='\n'.join(f'{name} {state}' for name, state in ps.items() if state))
        return result, [x for x in result.stdout.splitlines() if x.startswith('service:')]

    def assert_flagged(self, line, **states):
        result, lines = self.doctor(**states)
        self.assertIn(line, lines)
        self.assertEqual((result.stdout.splitlines()[-1], result.returncode), ('doctor:    NOT OK', 1), result.stdout)

    def test_a_healthy_stack_lists_every_service_and_passes(self):
        result, lines = self.doctor()
        self.assertEqual(lines, ['service:   api running', 'service:   beat running',
                                 'service:   migrate exited (0), one-shot: done', 'service:   postgres running',
                                 'service:   redis running', 'service:   web running', 'service:   worker running',
                                 'service:   worker-webhooks running'])
        self.assertEqual((result.stdout.splitlines()[-1], result.returncode), ('doctor:    OK', 0), result.stdout)

    def test_a_stopped_beat_names_the_fix_and_fails_the_doctor(self):
        self.assert_flagged('service:   beat exited (0): run VERIFY_ID=mine bin/stack compose start beat',
                            beat='exited 0')

    def test_a_missing_service_fails_the_doctor(self):
        self.assert_flagged('service:   worker-webhooks missing: run VERIFY_ID=mine bin/stack up',
                            **{'worker-webhooks': ''})

    def test_a_failed_migrate_fails_the_doctor(self):
        self.assert_flagged('service:   migrate exited (1): run VERIFY_ID=mine bin/stack compose start migrate',
                            migrate='exited 1')

    def test_a_paused_service_names_unpause(self):
        self.assert_flagged('service:   api paused: run VERIFY_ID=mine bin/stack compose unpause api', api='paused 0')

    def test_no_service_list_fails_the_doctor(self):
        result = self.stack('doctor', FAKE_CONFIG='{"services": {}}', FAKE_PS='')
        self.assertIn('service:   compose config gave no services', result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_the_fix_names_the_default_id_too(self):
        (self.home / 'default').mkdir()
        shutil.copy(self.home / 'mine/stack.env', self.home / 'default/stack.env')
        _, lines = self.doctor(verify_id='default', beat='exited 0')
        self.assertIn('service:   beat exited (0): run VERIFY_ID=default bin/stack compose start beat', lines)


class ReupPortsTest(StackTest):
    def env(self):
        text = (self.home / 'mine/stack.env').read_text()
        return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)

    def test_a_reup_on_other_ports_follows_them_and_keeps_the_secrets(self):
        self.up()
        first = self.env()
        self.up(VERIFY_WEB_PORT='23881', VERIFY_API_PORT='23882')
        second = self.env()
        moved = ('VERIFY_WEB_PORT', 'VERIFY_API_PORT')
        for key in moved:
            self.assertNotEqual(first[key], second[key], key)
        # One web image for every port: the container writes the API URL over the baked placeholder.
        self.assertEqual(second['NEXT_PUBLIC_API_URL'], 'http://verify-api.invalid')
        for key in first.keys() - set(moved):
            self.assertEqual(first[key], second[key], key)
        self.assertIn('POSTGRES_PASSWORD', first)


class LlmKeyGuardTest(StackTest):
    SECRET = 'sk-sentinel-123'

    def dotenv(self, text, checkout=None):
        (checkout or self.checkout).joinpath('.env').write_text(text)

    def assert_refused(self, result, key='DEEPSEEK_API_KEY'):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'no LLM key is exported, but {self.checkout / ".env"} has {key}', result.stderr)
        self.assertIn('VERIFY_LLM=off bin/stack up', result.stderr)
        self.assertNotIn(self.SECRET, result.stdout + result.stderr)
        self.assertFalse((self.home / 'captured').exists(), 'refused before docker compose')
        self.assertFalse((self.home / 'mine').exists(), 'refused before any state or reservation')

    def test_a_key_in_the_checkout_env_that_is_not_exported_refuses_up(self):
        self.dotenv(f'POSTGRES_USER=zenbree\nDEEPSEEK_API_KEY="{self.SECRET}"\n')
        self.assert_refused(self.up())

    def test_the_printed_export_sets_the_key_in_bash_and_zsh(self):
        self.dotenv(f"DEEPSEEK_API_KEY='{self.SECRET}' # mine\r\n")
        [command] = [line for line in self.up().stderr.splitlines() if line.startswith('  export ')]
        for shell in ('/bin/bash', 'zsh'):
            if shutil.which(shell):
                out = subprocess.run([shell, '-c', f'{command}; printenv DEEPSEEK_API_KEY'], capture_output=True,
                                     text=True)
                self.assertEqual(out.stdout, f'{self.SECRET}\n', shell)

    def test_an_anthropic_key_refuses_too(self):
        self.dotenv(f'DEEPSEEK_API_KEY=\nANTHROPIC_API_KEY={self.SECRET}\n')
        self.assert_refused(self.up(), 'ANTHROPIC_API_KEY')

    def test_the_printed_export_quotes_a_checkout_path_with_a_quote_and_a_space(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        odd = self.home / "o'k dir"
        shutil.copytree(self.checkout, odd)
        [command] = [x for x in self.stack('up', checkout=odd).stderr.splitlines() if x.startswith('  export ')]
        out = subprocess.run(['/bin/bash', '-c', f'{command}; printenv DEEPSEEK_API_KEY'], capture_output=True, text=True)
        self.assertEqual(out.stdout, f'{self.SECRET}\n', out.stderr)

    def test_empty_and_commented_keys_do_not_refuse(self):
        self.dotenv(f'DEEPSEEK_API_KEY= # paste here\nANTHROPIC_API_KEY=""\n# DEEPSEEK_API_KEY={self.SECRET}\n'
                    'ANTHROPIC_API_KEY=  \r\n')
        result = self.up()
        self.assertTrue((self.home / 'captured').exists(), result.stderr)

    def test_an_exported_key_reaches_compose(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        result = self.up(DEEPSEEK_API_KEY='exported')
        self.assertEqual((self.home / 'compose-key').read_text().strip(), 'exported', result.stderr)

    def test_verify_llm_off_starts_a_keyless_stack_and_records_it(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        result = self.up(VERIFY_LLM='off', DEEPSEEK_API_KEY='exported')
        self.assertEqual((self.home / 'compose-key').read_text(), '', result.stderr)
        self.assertIn('VERIFY_LLM=off', (self.home / 'mine/stack.env').read_text())

    def test_a_worktree_finds_the_key_in_the_main_checkout_env(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        tree = self.home / 'tree'
        git(self.checkout, 'worktree', 'add', '-q', str(tree))
        result = self.stack('up', checkout=tree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'but {self.checkout.resolve() / ".env"} has DEEPSEEK_API_KEY', result.stderr)

    def test_compose_subcommands_that_create_containers_are_guarded(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        for args in (['up', '-d', 'api'], ['-f', 'o.yml', 'run', '--rm', 'api'], ['create', 'api'],
                     ['--parallel', '2', 'up']):
            self.assertIn('no LLM key is exported', self.stack('compose', *args).stderr, args)
        for args in (['stop', 'api'], ['exec', '-T', 'api', 'sh', '-c', 'echo up'], ['--profile']):
            self.assertNotIn('no LLM key is exported', self.stack('compose', *args).stderr, args)

    def test_compose_follows_a_stack_started_keyless(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        self.up(VERIFY_LLM='off')
        result = self.stack('compose', 'up', '-d', 'api', DEEPSEEK_API_KEY='exported')
        self.assertNotIn('no LLM key is exported', result.stderr)
        self.assertEqual((self.home / 'compose-key').read_text(), '', result.stderr)

    def doctor_llm(self, **env):
        [line] = [x for x in self.stack('doctor', **env).stdout.splitlines() if x.startswith('llm:')]
        return line

    def test_doctor_flags_a_keyless_api_while_the_env_has_a_key(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        self.up(DEEPSEEK_API_KEY='exported')
        self.assertIn(f'no key, chat turns fail, but {self.checkout / ".env"} has DEEPSEEK_API_KEY', self.doctor_llm())
        self.assertIn('key present', self.doctor_llm(FAKE_API_KEY='exported'))
        self.assertNotIn('off by choice', self.doctor_llm(VERIFY_LLM='off'), 'doctor reads the recorded mode')

    def test_doctor_names_a_keyless_stack_started_on_purpose(self):
        self.dotenv(f'DEEPSEEK_API_KEY={self.SECRET}\n')
        self.up(VERIFY_LLM='off')
        self.assertEqual(self.doctor_llm(), 'llm:       off by choice (VERIFY_LLM=off), chat turns fail')


if __name__ == '__main__':
    unittest.main()
