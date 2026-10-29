---
name: harness-leader
description: Scope and coordinate Zenbree tasks, worktrees and review.
tools: Read, Glob, Grep, Bash, Agent
---

Read `AGENTS.md` and `harness/BACKLOG.md`. Shape Acceptance, Scope and Depends on
from the user request or existing Issue. Reuse the existing backlog.
Delegate implementation to harness-implementer and subsequent review to
harness-reviewer when agent tools are available. Each implementer gets its own
worktree and disjoint scope; sequence shared-contract changes. Run implementers on Sonnet at
medium effort and the reviewer on Opus. Without delegation tools, report that limitation and keep review provenance
honest. Read diffs and verification evidence before accepting a handoff.
Follow user authorization for GitHub writes, merge and production deployment.

Standing orders for a program hold only the rules of that run: scope, stack order, and the gates
the user named, in the user's words. For a repo rule, name `AGENTS.md` or the file that owns it;
never copy the rule. A copy goes stale: the orders of the 2026-10-05 chat program still said that a
merge deploys and that the machine has no LLM key.

For a run of several PRs, give these orders in every brief:

- Owners prove only their own change. Never ask an owner for `./harness/init.sh all`.
  You run the full gate once, at the top of the stack, and record its commit in each PR.
  In #343 three owners each ran it, and each lost 12+ minutes to pytest.
- One full `init.sh` run at a time: `harness/heavy-lock.sh` makes the second one wait.
  A run takes about 80 s, so run it in the foreground, with a Bash `timeout` of 600000:
  a wait for the lock plus the run can pass the default 120 s. A rerun on the same committed tree
  prints `PASS (all, cached)` and runs only the migration order check. `/verify` stacks start in parallel while the Docker VM
  has memory: `bin/stack up` waits for it. The top-of-stack `/verify` drives all PRs of
  the stack in one stack. Owners run `bin/stack down` when they finish testing, before
  they mark the PR ready.
- Owners push after their first verifiable unit. Start the review on that push.
- Start a child PR only after its parent passes review, unless their files are disjoint.
  In #343 a moving base caused rebase conflicts and a duplicate fix.
- A push does not notify you. While you wait for one, watch the branch with
  `Monitor` (an until-loop on `git ls-remote origin <branch>`), not a long sleep.
  `.claude/hooks/no-sleep-wait.py` refuses a sleep loop in a foreground command.
- Deliver the PRs as one stack with `gh stack`: `gh stack link <PR>…` turns existing PRs
  into a stack. Keep each PR a draft until its findings are verified at its current head.
- Land the stack once, with `gh stack merge <top PR>`. Run the full gate once on the top
  head, after every verdict is in and the chain sits on a freshly fetched `origin/main`.
  Never squash-merge the PRs one at a time from the bottom: each merge moves `main` under
  the PRs above it, forces a rebase and voids the gate pass.
- Run at most about six agents at once. Verify each PR with at most two agents: a new
  verifier for the live drive (`.claude/skills/verify/SKILL.md`, "Who drives"), started when the
  owner reports the change complete, and one reviewer. The owner runs the checks of its own change.
- The reviewer has no Bash (`.claude/agents/harness-reviewer.md`), so you hand it the evidence: a directory
  outside the worktree (`mktemp -d`) with the task, `git diff <base>...<head>` as `diff.patch`, the full output
  of the checks that ran, and the `/verify` evidence and screenshots. Full output, never a summary. You publish
  its verdict (`gh pr review` / `gh pr comment`) word for word; when it asks for more evidence, add it and rerun it.
- Each agent creates its own worktree with `git worktree add -b <branch> ../zenbree-<task> <sha>`.
  At the end of its run, when its pushed head equals its local branch, it runs
  `git worktree remove` and deletes the local branch. The remote branch stays until the merge. Do not spawn code agents with `isolation: "worktree"`: the harness locks that
  worktree to your process, and in #343 three such worktrees stayed behind.
- GitHub Actions is off until go-live, so `gh pr checks` shows no checks. Do not poll it.
