"""PreToolUse hook: refuse `gh pr merge` and `gh stack merge` until a full `harness/init.sh all` passed on the
head of every PR they merge.

Until go-live no CI runs, so nothing else holds the full gate before a merge. With this hook, a follow-up push
on an open PR needs only the fast checks: the full run happens once, before the merge. Agents reran the full
gate after each small follow-up, about three minutes each (2026-10-09). The evidence is the pass cache of
init.sh. A pass on a PR above in the same stack covers the PRs below it. A PR that changes nothing the gate
checks needs no run. When the hook cannot tell which PRs merge, or it fails, it refuses: a refusal costs a
retry, a wrong pass ships an unverified head."""

import json
import os
from pathlib import Path
import subprocess
import sys

# python3 -I leaves the script's directory off sys.path.
sys.path.append(str(Path(__file__).resolve().parent))
from shell_line import commands, gh_command, pr_target  # noqa: E402

CACHE = Path(os.environ.get('ZENBREE_GATE_CACHE') or Path.home() / '.cache/zenbree/gate')
# Git runs in this repo, whatever the session's cwd: `gh pr merge 12 -R o/r` from /tmp must find the commits.
REPO = Path(__file__).resolve().parents[2]
# What `init.sh all` checks: the apps, the contract doc that test_openapi.py reads, and the harness tests with
# the hooks, the verify stack and the backup they cover. The harness's own .md files are docs.
CHECKED = ('api/', 'web/', 'landing/', 'harness/', 'ops/', '.claude/hooks/', '.claude/settings.json',
           '.claude/skills/verify/bin/', 'docs/contracts-m1.md')
# Landing copy cannot break the API or the web, and `init.sh landing --fast` already type-checks it on each push.
# A full run for a wording fix cost about 4 min (owner decision, 2026-10-15).
UNCHECKED = ('landing/src/i18n/',)
PR_VALUE_FLAGS = ('-t', '--subject', '-b', '--body', '-F', '--body-file', '-A', '--author-email',
                  '--match-head-commit')
STACK_VALUE_FLAGS = ('--merge-method',)
# A stack deeper than this is a loop in the base branches, not a stack.
MAX_STACK = 20


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else None


def view(target, repo, cwd):
    """The open PR that gh names for `target` (None: the branch of `cwd`), or None."""
    fields = 'number,state,headRefName,headRefOid,baseRefName,files,changedFiles'
    out = run(['gh', 'pr', 'view', *([target] if target else []), *repo, '--json', fields], cwd)
    pr = json.loads(out) if out else None
    return pr if pr and pr.get('state') == 'OPEN' else None


def stack_below(top, repo, cwd):
    """`top` and the open PRs under it, down its base branches: what `gh stack merge <top>` merges."""
    prs = [top]
    while len(prs) < MAX_STACK and (below := view(prs[-1]['baseRefName'], repo, cwd)):
        prs.append(below)
    return prs


def checked(path):
    if path.startswith(UNCHECKED) or (path.startswith('harness/') and path.endswith('.md')):
        return False
    return path.startswith(CHECKED)


def needs_run(pr):
    paths = [f['path'] for f in pr.get('files') or []]
    # gh lists at most 100 files, so a shorter list than the count may hide a checked one.
    total = pr.get('changedFiles')
    return not isinstance(total, int) or len(paths) < total or any(checked(p) for p in paths)


def passed(head, branch):
    """True when a full pass covers `head`: a pass on its tree, or on a commit of another branch that contains it.
    A newer commit of the same branch does not count: it may hold the fix that `head` lacks."""
    tree = run(['git', 'rev-parse', '--verify', '--quiet', f'{head}^{{tree}}'], REPO)
    if tree is None:
        return False
    for entry in CACHE.glob('*/*-all'):
        if entry.name == f'{tree}-all':
            return True
        if entry.parent.name == branch.replace('/', '%'):
            continue
        # An entry holds "<short sha> at <date>": the commit that passed.
        commit = (entry.read_text().split() or [''])[0]
        if commit and run(['git', 'merge-base', '--is-ancestor', head, commit], REPO) is not None:
            return True
    return False


def merged_prs(words, earlier, cwd):
    """The open PRs that this command merges: None when it merges nothing, a message when it is unclear which."""
    if (args := gh_command(words, 'pr', 'merge')) is not None:
        if {'--help', '-h', '--disable-auto'} & set(args):
            return None
        target, repo = pr_target(args, PR_VALUE_FLAGS)
        # With no number gh picks the branch of the cwd, which a cd, a checkout or a switch before it can change.
        if target is None and any(earlier):
            return 'Name the PR: gh pr merge <number>. After another command, the hook cannot tell which branch merges.'
        pr = view(target, repo, cwd)
        return [pr] if pr else f'gh could not name an open PR for gh pr view {target or ""} in {cwd}.'
    if (args := gh_command(words, 'stack', 'merge')) is not None:
        if {'--help', '-h'} & set(args):
            return None
        target, repo = pr_target(args, STACK_VALUE_FLAGS)
        top = view(target, repo, cwd) if target else None
        if top is None:
            return 'Name the top PR of the stack by its PR number: gh stack merge <pr-number>.'
        return stack_below(top, repo, cwd)
    return None


def refusal(line, cwd):
    words_list = commands(line)
    if words_list is None:
        if 'gh' in line and 'merge' in line:
            return 'The hook cannot split this command line. Run gh pr merge <number> in a command of its own.'
        return None
    for i, words in enumerate(words_list):
        prs = merged_prs(words, words_list[:i], cwd)
        if isinstance(prs, str):
            return prs
        for pr in prs or []:
            if needs_run(pr) and not passed(pr['headRefOid'], pr['headRefName']):
                return (f'No full gate passed on #{pr["number"]} ({pr["headRefName"]} at {pr["headRefOid"][:8]}). '
                        'In its worktree, at that commit, run ./harness/init.sh all, then merge in a separate '
                        'command. A pass on a PR above it in its stack also counts. If that commit is not local, '
                        'run git fetch first.')
    return None


try:
    payload = json.load(sys.stdin)
    message = refusal(payload.get('tool_input', {}).get('command', ''), payload.get('cwd') or os.getcwd())
except Exception as error:  # noqa: BLE001 - Claude Code lets a command through when its hook crashes
    message = f'pr-merge-gate.py failed ({error!r}), so it cannot vouch for this merge.'
if message:
    print(message, file=sys.stderr)
    sys.exit(2)
