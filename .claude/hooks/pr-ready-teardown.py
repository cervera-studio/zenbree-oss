"""PreToolUse hook: refuse `gh pr ready` while that PR's verify stack still runs.

Agents marked a PR ready and left its verify stack up. Each stack holds about 1 GB of the shared Docker VM
until the lease reaps it 45 idle minutes later, which starves the other agents' builds. The hook fails
open: when gh or docker cannot answer, it lets the command through."""

import json
import os
from pathlib import Path
import subprocess
import sys

# python3 -I leaves the script's directory off sys.path.
sys.path.append(str(Path(__file__).resolve().parent))
from shell_line import commands as shell_commands, gh_command, pr_target  # noqa: E402


def run(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else None


payload = json.load(sys.stdin)
cwd = payload.get('cwd') or os.getcwd()
commands = shell_commands(payload.get('tool_input', {}).get('command', '')) or []

for i, words in enumerate(commands):
    args = gh_command(words, 'pr', 'ready')
    if args is None:
        continue
    if {'--undo', '--help', '-h'} & set(args):
        continue
    # The hook runs before the whole line, so `bin/stack down && gh pr ready` would see the stack still up.
    if any(w.endswith('bin/stack') and 'down' in earlier for earlier in commands[:i] for w in earlier):
        continue
    try:
        target, repo = pr_target(args)
        pr = run(['gh', 'pr', 'view', *([target] if target else []), *repo, '--json', 'headRefName,headRefOid'], cwd)
        projects = run(
            ['docker', 'ps', '--format',
             '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}']
        )
        if pr is None or projects is None:
            continue
        head = json.loads(pr)
    except (OSError, subprocess.SubprocessError, ValueError):
        continue
    stacks = {}
    for row in projects.splitlines():
        project, _, directory = row.partition('\t')
        if project.startswith('zenbree-verify') and os.path.isdir(directory):
            stacks[project] = directory
    downs = []
    for project, directory in sorted(stacks.items()):
        branch = run(['git', '-C', directory, 'rev-parse', '--abbrev-ref', 'HEAD'])
        sha = run(['git', '-C', directory, 'rev-parse', 'HEAD'])
        if branch != head['headRefName'] and sha != head['headRefOid']:
            continue
        stack_id = project.removeprefix('zenbree-verify').removeprefix('-')
        downs.append(f'VERIFY_ID={stack_id or "default"} {directory}/.claude/skills/verify/bin/stack down')
    if downs:
        print(
            'Tear down the verify stacks of this PR before you mark it ready: they hold Docker memory the other '
            'agents need. If another agent started a stack, wait for it to finish instead. Run:\n' + '\n'.join(downs),
            file=sys.stderr,
        )
        sys.exit(2)
