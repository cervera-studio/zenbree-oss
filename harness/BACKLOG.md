# Backlog and handoff

GitHub Issues in `cervera-studio/zenbree-oss` are the shared backlog. Keep existing
`api`, `web`, `infra`, `port` and `needs-decision` labels. Do not infer readiness
from an area label or rewrite existing Issues in bulk.

## Task shape

Before starting, capture:

- **Acceptance:** concrete observable outcomes, including relevant failure cases.
- **Scope:** files/globs and shared contracts touched.
- **Depends on:** Issue numbers or `None`; resolve prerequisites before claiming readiness.

Existing Issues can acquire these sections when picked up. A direct user request
can use the conversation and PR for this information without creating a new Issue.

## Ownership and lifecycle

1. Inspect `gh issue list --state open --limit 100`, `gh pr list`, and
   `git worktree list`; read related Issues with `gh issue view <n> --comments`.
   Paginate when needed. Check scopes, not merely labels or assignees.
2. Identify the task's owner, branch and worktree in its handoff. Issue assignment
   represents ownership, an open PR represents review, and a merged PR with
   `Closes #<n>` closes the task. Existing epic Issues may cover several PRs.
3. Before parallel work, confirm scopes are disjoint. Coordinate schema and
   shared-contract changes even when filenames differ.
4. Record plan, progress, blockers, verification and review in the existing task
   thread. Publish Issue/PR comments only within user-authorized GitHub work;
   otherwise keep the handoff in the conversation.
5. A blocked task records the missing prerequisite and next action. Use the
   existing `needs-decision` label only for actual product/design decisions.
6. Before landing, fetch the current base, resolve conflicts in your worktree and
   recheck the resulting diff. Require the verification gate in `AGENTS.md` and review; merge only within
   authorization. After go-live, `main` deploys production.

Do not introduce a second local backlog or duplicate status labels.
