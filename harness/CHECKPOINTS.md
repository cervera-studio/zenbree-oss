# Review checkpoints

## Before approval

- Acceptance is met, including relevant error paths; changed files fit the Scope.
- Each finding answers one question: does `main` give the same bad result for the
  same input? If not, the change causes the defect or makes it worse, and the PR
  fixes it. Such a defect blocks approval when it takes away something the user
  could do (an action, data, a retry), however rare its input. If `main` has it
  too, it goes to an issue that says so and never blocks. Apply the same question
  to each issue filed during the task and to each limit in the PR description:
  "risk", "follow-up" and "out of scope" are not reasons to defer a defect the
  change causes (the first draft of #510 deferred three such regressions).
- Dependencies are resolved and overlapping work has been coordinated.
- Existing patterns and product decisions are respected; no unrelated refactors.
- No God classes or modules: each changed class, module and component keeps one
  responsibility and follows SOLID; new logic sits in its own module rather than
  growing an existing one (`harness/size_check.py` catches size, not design).
- Auth/user scoping, billing, secrets and SSRF protections remain effective.
- SQL changes are new `api/db/` migrations, no edit to one that ran; API/SSE consumers agree.
- Relevant regression tests demonstrate behavior rather than mirror implementation.
- New pure logic, a change to what pure logic returns, and a step that must be
  idempotent each have a property-based test next to their example tests. The property
  is an invariant, not the code's formula again, and each counterexample the tool found
  is a fixed case (rule in the `AGENTS.md` rules table).
- Each added test, and each added or changed assertion, that guards auth, scoping,
  credits, migrations, vendors or an API, SSE or tool contract has planted-bug proof in
  the Verification section of the PR body: a plausible wrong change and the assertion
  it failed. Without that proof, the PR is not ready: the test may pass for the wrong
  reason.
- Each comment the diff adds says why. A comment that restates the code under it,
  or a section divider, is a finding (five cleanup commits removed them after merge).
  The `/no-comments` skill applies the same test.
- New lint suppressions, and code restructured so a rule stops firing, count as
  lowered checks. These need the user's explicit approval, quoted in the PR: a change
  to `web/biome.json` or `web/.oxlintrc.json` (and so their hashes), to `APPROVED` or the
  checks in `web/lint/guard.mjs`, to the `lint` script, or a `web/lint/oxlint/*.mjs`
  rule made weaker (with or without its case in the rule's test).
- Full checks pass for affected scopes; report baseline failures, skipped tests
  and unavailable prerequisites. `--fast` is never full verification. In a stack,
  one full run on the top PR covers every PR below it; a later push to any PR of
  the stack makes that run stale.
- User-visible web, API, auth, admin or chat changes carry `/verify` evidence
  (`.claude/skills/verify/`) from the real app: each acceptance item driven, side
  effects read back, skipped steps named with their missing prerequisite.
- Reviewer inspected the actual diff and evidence. Record APPROVED or
  CHANGES_REQUESTED with concrete file/line findings; self-review is identified.

## Before merge

- Current base integrated; verification gate from `AGENTS.md` met on the proposed revision. Until go-live, the owner accepts full local `./harness/init.sh all`, required `/verify` evidence and review while GitHub Actions remains unavailable. After go-live, full CI checks must be green.
- PR explains behavior, verification and limits, and links/closes the relevant Issue.
- Merge is authorized. Until go-live a merge deploys nothing. After go-live, `main` deploys production.

## After merge

- Confirm the merge and deployment status; do not equate merge with healthy production.
- Remove only this task's clean worktree and merged branch. Preserve ongoing work.

## Running checks

`./harness/init.sh [all|api|web|landing] [--fast]` works from any directory.
Install uv, Node 24+ and the pnpm version in `web/package.json` first. The script
installs locked dependencies; it may need network access. Full API and web checks require
`TEST_DATABASE_URL` pointing to a disposable PostgreSQL database with permission
to create and drop databases (`CREATEDB`). Never use production credentials.

For a local database, use the one that all worktrees share. `harness/testdb.sh` starts the `zenbree-harness-db` container if it is not running, waits until it is ready, and prints the export. A full `./harness/init.sh` runs it when `TEST_DATABASE_URL` is not set. To run pytest by hand against it:

```bash
eval "$(./harness/testdb.sh)"
```

Each SQL test copies its own database from a template migrated once for each version of `api/db`, so parallel runs from different worktrees do not collide. Do not start a second test database, and do not stop the shared one: another worktree can be using it.

Fast mode runs dependency installs and the static checks only: Ruff format and lint,
mypy, Biome, TypeScript and knip, and the landing's lint and `astro check`.
Full mode also runs pytest, Vitest, the Next build and the landing's tests and build, matching CI. Image build,
push and deployment remain CI jobs. Inspect pytest skip reasons even on success.
Check the harness itself with `python3 harness/run_tests.py`.
