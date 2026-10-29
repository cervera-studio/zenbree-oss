"""The Claude Code hooks in .claude/settings.json, fed the JSON that Claude Code sends."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def hook_commands():
    settings = json.loads((ROOT / '.claude/settings.json').read_text())
    return [h['command'] for entry in settings['hooks']['PreToolUse'] if entry['matcher'] == 'Bash' for h in entry['hooks']]


def run_bash_hook(name, command, extra_env=None, cwd=None, background=False):
    """Runs the settings entry of the hook `name` only: running every hook for every case made these tests
    about a third of the full gate."""
    (entry,) = [c for c in hook_commands() if f'/{name}.py' in c]
    tool_input = {'command': command, **({'run_in_background': True} if background else {})}
    payload = json.dumps({'tool_name': 'Bash', 'tool_input': tool_input, 'cwd': cwd or str(ROOT)})
    env = {'CLAUDE_PROJECT_DIR': str(ROOT), 'PATH': '/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin',
           **(extra_env or {})}
    return subprocess.run(['sh', '-c', entry], input=payload, env=env, capture_output=True, text=True)


class NoPrivatePostgresTest(unittest.TestCase):
    def test_refuses_a_private_postgres(self):
        for command in [
            'docker run -d --name zb-lead-343-db -e POSTGRES_PASSWORD=x -p 5499:5432 postgres:17-alpine',
            'docker run --rm postgres',
            'docker container run -d library/postgres:17',
            'docker create --name pg postgres:16',
            'cd api && docker run -d -e POSTGRES_PASSWORD=x postgres:17 && sleep 2',
            'docker run -d --name pg \\\n  -e POSTGRES_PASSWORD=x \\\n  postgres:17-alpine',
            "docker run -d 'postgres:17-alpine'",
            'docker run -d postgres@sha256:0123abcd',
            "bash -c 'docker run -d postgres:17'",
        ]:
            with self.subTest(command=command):
                result = run_bash_hook('no-private-postgres', command)
                self.assertEqual(result.returncode, 2)
                self.assertIn('harness/testdb.sh', result.stderr)

    def test_allows_the_shared_database_and_other_containers(self):
        for command in [
            'eval "$(./harness/testdb.sh)"',
            './harness/testdb.sh',
            'docker run --rm -v vol:/v alpine du -sh /v',
            'docker run --rm -e DATABASE_URL=postgresql://u:p@db/x ghcr.io/cervera-studio/zenbree-oss-api',
            'docker exec zenbree-harness-db psql -U postgres -c "select 1"',
            'docker compose up -d postgres',
            'docker rm -fv pg26212',
            'grep -rn "docker run" docs; echo postgres',
        ]:
            with self.subTest(command=command):
                self.assertEqual(run_bash_hook('no-private-postgres', command).returncode, 0)


class NoSleepWaitTest(unittest.TestCase):
    # Waits from the agent sessions of 2026-10-04 to 10-08, the longest first.
    WAITS = [
        'until grep -q "^exit " /tmp/x353-init.out; do sleep 10; done; tail -30 /tmp/x353-init.out',
        'for i in $(seq 1 58); do grep -q "^export VERIFY_WEB\\|^exit" $S/up.log && break; sleep 10; done',
        'for i in $(seq 1 100); do if grep -q end_turn $f; then echo done; break; fi; sleep 5; done',
        'until [ -s /tmp/x.out ] && grep -q "^exit " /tmp/x.out || ! pgrep -f "init.sh web"; do sleep 10; done',
        'while true; do\n  grep -q PASS run.log && break\n  sleep 2\ndone',
        'cd web && sleep 60; tail -5 /tmp/g212-up.log',
        "bash -c 'until test -f done; do sleep 2; done'",
        'timeout 600 bash -c "until test -f x; do sleep 5; done"',
        'sleep 1m',
        '/bin/sleep 30',
        'sleep -- 30',
        'sleep $N; cat out',
        'FOO=1 sleep 30',
        'tail -f api.log | grep -m1 PASS',
        'gh pr checks 446 --watch',
        # Punctuation that shlex joins, prefixes with options, comments (#446 second review).
        'for i in $(seq 30); do sleep 5; done',
        'while ! grep -q PASS $(ls -t *.log | head -1); do sleep 5; done',
        'f() { while true; do sleep 5; done; }; f',
        '(bin/stack psql -c "x" >/dev/null 2>&1 &); sleep 30',
        'x=$(cat f);sleep 30',
        'cd x # go\nsleep 30',
        'curl http://x/#frag && sleep 30',
        'nice -n 10 sleep 30',
        'timeout -s KILL 600 sleep 30',
        'env A=1 nohup sleep 2h',
        'bash -lc "sleep 30"',
        'docker compose logs -f api',
        'tail -F x.log',
        'gh run watch 123',
        # Third review of #446.
        'tail --follow=name x.log',
        'journalctl -f -u docker',
        'watch -n 5 docker ps',
        'echo "#" ; sleep 30',
        'sleep 3 4 5',
        'if sleep 30; then :; fi', 'time -p sleep 30', 'while true; do time sleep 5; done',  # #579 review
    ]

    def test_refuses_a_wait_in_the_foreground(self):
        for command in self.WAITS:
            with self.subTest(command=command):
                result = run_bash_hook('no-sleep-wait', command)
                self.assertEqual(result.returncode, 2)
                self.assertIn('run_in_background', result.stderr)

    def test_allows_the_same_wait_in_the_background(self):
        for command in self.WAITS:
            with self.subTest(command=command):
                self.assertEqual(run_bash_hook('no-sleep-wait', command, background=True).returncode, 0)

    def test_allows_loops_and_sleeps_that_do_not_wait(self):
        for command in [
            'for f in api/db/*.sql; do wc -l "$f"; done',
            'sleep 2 && curl -s localhost:8000/health',
            'grep -rn "sleep" harness/heavy-lock.sh',
            './harness/init.sh all',
            # Quoted words and heredoc bodies are data (#446 review).
            'gh pr edit 446 --body "The longest, until grep -q x; do sleep 10; done, blocked 597 s."',
            "grep -c 'do sleep 10' *.jsonl",
            "echo 'run sleep 10 later'",
            "git commit -m 'fix: loop until done, then sleep 30 s'",
            "cat > body.md <<'EOF'\nfor each PR, do sleep 60\nwhile x; do sleep 10; done\nEOF\ngh pr create --body-file body.md",
            'for f in a b; do echo $f; done; bin/stack up && sleep 2 && curl x',
            'until curl -sf localhost:18000/health; do sleep 1; done',
            'timeout 60 bash -c "until curl -sf x; do sleep 0.5; done"',
            'tail -n 30 api.log',
            'gh pr checks 446',
            'for f in a b; do echo $(basename $f); done; sleep 3',
            'for d in a b; do (cd $d && ls); done; sleep 3',
            '# sleep 30\nls',
            'case $x in a) sleep 3 ;; esac',
            'if test -f x; then sleep 3; fi',
            'git commit -m "$(cat <<\'EOF\'\nwhile x; do sleep 10; done\nEOF\n)"',
            'docker compose logs api | tail -20',
            'docker compose -f compose.yml logs api --tail 50',
            'docker compose -p zenbree-verify -f compose.yml -f compose.verify.yml logs --tail 100 api',
            'for f in a b; do echo "# $f"; cat $f; done\nbin/stack up && sleep 2 && curl x',
            'echo ${#arr[@]} $# ${x#*/}; ls # sleep 30\nsleep 3',
        ]:
            with self.subTest(command=command):
                self.assertEqual(run_bash_hook('no-sleep-wait', command).returncode, 0)


    def test_a_payload_it_cannot_read_passes(self):
        hook = ROOT / '.claude/hooks/no-sleep-wait.py'
        for payload in ['{"tool_input": null}', '{"tool_input": {"command": null}}', '{"tool_input": {"command": 5}}',
                        '[1]', '', 'not json',
                        json.dumps({'tool_input': {'command': 'nice ' * 1200 + 'sleep 30'}})]:
            with self.subTest(payload=payload):
                result = subprocess.run(['python3', '-I', str(hook)], input=payload, capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stderr), (0, ''))


class PrReadyTeardownTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.checkout = self.tmp / 'checkout'
        self.checkout.mkdir()
        self.git('init', '-q', '-b', 'feat/x')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'init')
        self.sha = self.git('rev-parse', 'HEAD')
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        self.fake(bin_dir / 'gh', '[ -z "$FAKE_GH_FAIL" ] || exit 1\n'
                  'echo "{\\"headRefName\\":\\"$FAKE_BRANCH\\",\\"headRefOid\\":\\"$FAKE_OID\\"}"')
        self.fake(bin_dir / 'docker', f'printf "zenbree-verify-ab12\\t{self.checkout}\\nzenbree-verify\\t{self.checkout}\\nzenbree\\t/dev\\n"')
        self.path = f'{bin_dir}:'

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.checkout), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def fake(self, path, body):
        path.write_text(f'#!/bin/sh\n{body}\n')
        path.chmod(0o755)

    def hook(self, command, branch='feat/x', oid='0' * 40, gh_fail=False):
        env = {'PATH': self.path + '/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin',
               'FAKE_BRANCH': branch, 'FAKE_OID': oid, **({'FAKE_GH_FAIL': '1'} if gh_fail else {})}
        return run_bash_hook('pr-ready-teardown', command, env, cwd=str(self.checkout))

    def test_refuses_while_the_stack_checkout_is_on_the_pr_branch(self):
        result = self.hook('gh pr ready 12')
        self.assertEqual(result.returncode, 2)
        self.assertIn(f'VERIFY_ID=ab12 {self.checkout}/.claude/skills/verify/bin/stack down', result.stderr)
        self.assertIn(f'\nVERIFY_ID=default {self.checkout}/.claude/skills/verify/bin/stack down', result.stderr, 'every stack')

    def test_refuses_a_chained_or_prefixed_ready(self):
        for command in ['cd /tmp && gh pr ready 12', 'GH_PROMPT_DISABLED=1 gh pr ready 12 -R o/r', 'git push\ngh pr ready']:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command).returncode, 2)

    def test_refuses_a_detached_checkout_at_the_head_commit(self):
        self.git('checkout', '-q', '--detach')
        self.assertEqual(self.hook('gh pr ready', branch='other', oid=self.sha).returncode, 2)

    def test_allows_a_stack_on_another_branch(self):
        self.assertEqual(self.hook('gh pr ready 12', branch='feat/other').returncode, 0)

    def test_allows_undo_and_other_commands(self):
        for command in [
            'gh pr ready 12 --undo', 'gh pr ready --help', 'gh pr view 12', 'git status; echo ready',
            'grep -rn "gh pr ready" .claude', "rg 'gh pr ready' AGENTS.md", "echo 'run gh pr ready'",
            'git commit -m "docs: tear down before gh pr ready"',
            'VERIFY_ID=ab12 .claude/skills/verify/bin/stack down && gh pr ready 12',
            'gh pr ready "unterminated',
        ]:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command).returncode, 0)

    def test_fails_open_when_gh_fails(self):
        self.assertEqual(self.hook('gh pr ready 12', gh_fail=True).returncode, 0)

    def test_the_settings_register_the_hook(self):
        self.assertTrue(any('pr-ready-teardown.py' in c for c in hook_commands()))


class PrMergeGateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.checkout = self.tmp / 'checkout'
        self.checkout.mkdir()
        self.git('init', '-q', '-b', 'feat/x')
        self.lower = self.commit('lower')
        self.top = self.commit('top')
        # The hook runs git in its own repo, so it runs from a copy inside the scratch repo.
        shutil.copytree(ROOT / '.claude/hooks', self.checkout / '.claude/hooks')
        shutil.copy(ROOT / '.claude/settings.json', self.checkout / '.claude/settings.json')
        self.cache = self.tmp / 'cache'
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        # Like gh pr view: the PR for a number or branch, or for no target (the cwd's branch); fails otherwise.
        gh.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                      'target = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else ""\n'
                      'prs = json.loads(os.environ["FAKE_PRS"])\n'
                      'sys.exit(1) if os.environ.get("FAKE_GH_FAIL") or target not in prs else print(json.dumps(prs[target]))\n')
        gh.chmod(0o755)
        self.path = f'{bin_dir}:'

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.checkout), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, message):
        (self.checkout / 'file').write_text(message)
        self.git('add', 'file')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def passed(self, commit, scope='all', branch='feat%x'):
        """Writes the entry that a full init.sh run leaves for `commit`."""
        entry = self.cache / branch / f'{self.git("rev-parse", commit + "^{tree}")}-{scope}'
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(f'{commit[:8]} at 2026-10-09T10:00Z\n')

    def pr(self, number, branch, oid, base='main', paths=('web/app/page.tsx',)):
        return {'number': number, 'state': 'OPEN', 'headRefName': branch, 'headRefOid': oid, 'baseRefName': base,
                'files': [{'path': p} for p in paths], 'changedFiles': len(paths)}

    def hook(self, command, oid=None, gh_fail=False, paths=('web/app/page.tsx',), prs=None, cwd=None, changed=None):
        pr = self.pr(12, 'feat/x', oid or self.top, paths=paths)
        if changed is not None:
            pr['changedFiles'] = changed
        env = {'PATH': self.path + '/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin',
               'FAKE_PRS': json.dumps(prs or {'12': pr, '': pr}), 'CLAUDE_PROJECT_DIR': str(self.checkout),
               'ZENBREE_GATE_CACHE': str(self.cache), **({'FAKE_GH_FAIL': '1'} if gh_fail else {})}
        return run_bash_hook('pr-merge-gate', command, env, cwd=cwd or str(self.checkout))

    def test_refuses_a_head_with_no_full_pass(self):
        result = self.hook('gh pr merge 12 --squash')
        self.assertEqual(result.returncode, 2)
        self.assertIn(f'#12 (feat/x at {self.top[:8]})', result.stderr)
        self.assertIn('./harness/init.sh all', result.stderr)

    def test_allows_a_head_whose_tree_passed(self):
        self.passed(self.top)
        for command in ['gh pr merge 12 --squash --delete-branch', 'gh pr merge --squash',
                        'gh pr merge --squash --subject "feat: thing (#12)" 12', 'gh pr merge -t "feat: x" -b done 12',
                        'gh pr merge -A a@b.c --match-head-commit abc 12', 'gh pr merge -F body.md 12 -R o/r']:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command).returncode, 0, 'a flag value is not the PR')

    def test_a_merge_by_number_works_from_any_cwd(self):
        self.passed(self.top)
        self.assertEqual(self.hook('gh pr merge 12 -R o/r', cwd=str(self.tmp)).returncode, 0)

    def test_a_scoped_or_older_pass_does_not_count(self):
        self.passed(self.top, scope='api')
        self.passed(self.lower)
        self.assertEqual(self.hook('gh pr merge 12').returncode, 2, 'a follow-up commit after the pass')

    def test_a_pass_above_in_the_stack_covers_the_pr_below(self):
        self.passed(self.top, branch='feat%y')
        self.assertEqual(self.hook('gh pr merge 12', oid=self.lower).returncode, 0)

    def test_a_newer_pass_on_the_same_branch_does_not_cover_an_unpushed_fix(self):
        self.passed(self.top)
        self.assertEqual(self.hook('gh pr merge 12', oid=self.lower).returncode, 2)

    def test_refuses_a_chained_prefixed_or_global_flag_merge(self):
        for command in ['cd /tmp && gh pr merge 12', 'GH_PROMPT_DISABLED=1 gh pr merge -R o/r 12 --auto',
                        './harness/init.sh all && gh pr merge 12', 'gh -R o/r pr merge 12', 'gh --repo=o/r pr merge 12',
                        'gh pr -R o/r merge 12', "gh pr merge 12 --body-file - <<'EOF'\nIt's verified.\nEOF",
                        '/usr/bin/gh pr merge 12', 'if true; then nohup gh pr merge 12; fi',
                        "echo '<<EOF'\ngh pr merge 12\nEOF", 'cat <<<EOF\ngh pr merge 12\nEOF',
                        'bash <<EOF\ngh pr merge 12\nEOF', 'gh pr view 3 # read\ngh pr merge 12',
                        '2>/dev/null gh pr merge 12', 'gh pr view 3 # read \\\ngh pr merge 12',
                        'echo a\\;#b; gh pr merge 12', 'echo \\ #b; gh pr merge 12']:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command).returncode, 2)

    def test_a_redirection_after_the_number_keeps_the_pr(self):
        self.passed(self.lower, branch='feat%y')
        prs = {'12': self.pr(12, 'feat/x', self.top), '': self.pr(13, 'feat/y', self.lower)}
        for command in ['gh pr merge 12 >out', 'gh pr merge 12&>/dev/null', 'gh pr merge 12&>>log',
                        'gh pr merge "12">out', 'gh pr merge 12 2>&1|tail -1', 'gh pr merge \\\n12 >out',
                        'gh 2>/dev/null pr merge 12', 'gh pr 2>/dev/null merge 12']:
            with self.subTest(command=command):
                self.assertIn('#12 (feat/x', self.hook(command, prs=prs).stderr, 'the PR of the cwd passed, not #12')

    def test_an_fd_word_is_not_the_pr(self):
        self.passed(self.lower, branch='feat%y')
        prs = {'12': self.pr(12, 'feat/x', self.top), '': self.pr(13, 'feat/y', self.lower)}
        for command in ['gh pr merge 2>/dev/null 12', 'gh pr merge --squash 12 2>&1 | tail',
                        'gh pr merge 2>&1 12']:
            with self.subTest(command=command):
                self.assertIn('#12 (feat/x', self.hook(command, prs=prs).stderr)
        for command in ['gh pr merge 12>/dev/null', 'gh pr merge --squash 2>&1 | tail']:
            with self.subTest(command=command):
                result = self.hook(command, prs=prs)
                self.assertEqual(result.returncode, 0, result.stderr)  # the passed PR of the cwd, not #12 or #2

    def test_refuses_a_merge_with_no_number_after_another_command(self):
        self.passed(self.top)
        for command in ['cd ../other && gh pr merge --squash', 'gh pr checkout 13 && gh pr merge --squash',
                        'git switch feat/y && gh pr merge', 'git checkout feat/y; gh pr merge --squash']:
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 2, 'the cwd passed, but gh merges another branch')
                self.assertIn('gh pr merge <number>', result.stderr)

    def test_allows_other_commands(self):
        for command in ['gh pr merge --help', 'gh pr merge 12 --disable-auto', 'gh pr view 12', 'gh stack merge --help',
                        'grep -rn "gh pr merge" .claude', 'git commit -m "docs: run the gate before gh pr merge"',
                        "echo it's done"]:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command).returncode, 0)

    def test_a_pr_that_changes_nothing_the_gate_checks_needs_no_run(self):
        for path, code in [('docs/decisions.md', 0), ('.claude/skills/verify/features/lists.md', 0),
                           ('harness/CHECKPOINTS.md', 0), ('harness/init.sh', 2), ('docs/contracts-m1.md', 2),
                           ('.claude/hooks/no-sleep-wait.py', 2), ('ops/backup.sh', 2),
                           ('landing/src/i18n/it.ts', 0), ('landing/src/content.ts', 2)]:
            with self.subTest(path=path):
                self.assertEqual(self.hook('gh pr merge 12', paths=[path]).returncode, code)

    def test_a_file_list_shorter_than_the_count_needs_a_run(self):
        docs = [f'docs/{i}.md' for i in range(100)]
        self.assertEqual(self.hook('gh pr merge 12', paths=docs).returncode, 0)
        self.assertEqual(self.hook('gh pr merge 12', paths=docs, changed=140).returncode, 2, 'gh cut the list')
        no_count = {k: v for k, v in self.pr(12, 'feat/x', self.top, paths=[]).items() if k != 'changedFiles'}
        self.assertEqual(self.hook('gh pr merge 12', prs={'12': no_count}).returncode, 2, 'no count')

    def test_refuses_when_gh_cannot_name_the_pr(self):
        for command, gh_fail in [('gh pr merge 12', True), ('gh pr merge 13', False)]:
            with self.subTest(command=command):
                result = self.hook(command, gh_fail=gh_fail)
                self.assertEqual(result.returncode, 2)
                self.assertIn('gh could not name an open PR', result.stderr)

    def test_a_stack_merge_checks_every_pr_it_merges(self):
        prs = {'12': self.pr(12, 'feat/top', self.top, base='feat/lower'),
               'feat/lower': self.pr(11, 'feat/lower', self.lower)}
        self.assertIn('#12 (feat/top', self.hook('gh stack merge 12 --yes --squash', prs=prs).stderr)
        self.passed(self.top, branch='feat%top')
        self.assertEqual(self.hook('gh stack merge 12 --yes --merge-method squash', prs=prs).returncode, 0,
                         'the pass on the top covers the PR below')
        prs['feat/lower']['headRefOid'] = self.git('commit-tree', '-m', 'x', self.lower + '^{tree}')
        self.assertIn('#11 (feat/lower', self.hook('gh stack merge 12 --yes', prs=prs).stderr,
                      'a PR below that the top does not contain needs its own pass')

    def test_a_stack_merge_names_its_top_pr(self):
        self.passed(self.top)
        for command in ['gh stack merge --yes --squash', 'gh stack merge 3 --yes']:
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 2, 'no number, or a stack number, picks PRs the hook cannot see')
                self.assertIn('gh stack merge <pr-number>', result.stderr)

    def test_refuses_when_it_fails(self):
        (self.cache / 'feat%y' / 'broken-all').mkdir(parents=True)
        result = self.hook('gh pr merge 12')
        self.assertEqual(result.returncode, 2)
        self.assertIn('cannot vouch for this merge', result.stderr)

    def test_the_settings_register_the_hook(self):
        self.assertTrue(any('pr-merge-gate.py' in c for c in hook_commands()))
