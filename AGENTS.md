# Zenbree — agent entry point

Read this before editing. Harness flow: task → worktree → implement → verify → review → PR.

## Start

1. Read the user's task or linked GitHub Issue in full. Define Acceptance, Scope
   (files/globs) and Depends on before implementation. For a direct chat task,
   record these in the plan/PR; an Issue is not required to start authorized work.
2. Use one dedicated branch/worktree per task, never the main checkout:
   `git worktree add ../zenbree-<task> -b feat/<task> main`.
   Inspect `git status --short` and `git worktree list`; preserve other work.
3. Check active PRs, assigned Issues and worktrees for overlapping files. An
   assignee alone does not identify an agent: multiple worktrees can share one owner.
4. Read the relevant map entries below; run `./harness/init.sh <scope> --fast`
   for a baseline. Record existing failures separately from your changes.
5. Before a run of several PRs, read `.claude/agents/harness-leader.md`. Its run rules
   (when review starts, one stack, one `/verify` at the top) override the defaults of any
   playbook or skill you run under.

## Map

| Path | Read for |
|---|---|
| `docs/decisions.md` | Product, stack and deployment decisions |
| `docs/contracts-m1.md` | What `/openapi.json` cannot say: SSE events, error codes, auth rules, tool contracts, behavior |
| `docs/recipes.md` | "How to add X": the files to touch and the check that proves it, for a tool, vendor, SSE event, list column, config field, migration, settings page and message catalog |
| `docs/migrations.md` | How migrations run, deploy dump, rollback and restore |
| `api/AGENTS.md`, `landing/AGENTS.md` | Layering, tests and size baseline of the API; copy and lint of the landing |
| `harness/BACKLOG.md` | Task shape, ownership, handoff and PR lifecycle |
| `harness/CHECKPOINTS.md` | Review criteria and completion evidence |
| `harness/init.sh` | Shared local and CI checks: `api`, `web`, `landing`, or `all` |
| `harness/fix_census.py` | Which commits the fixes on `main` repair: a feature, an earlier fix, or another kind |
| `.claude/agents/harness-*.md` | Leader, implementer and reviewer roles |
| `.claude/skills/verify/` | `/verify`: drive the real app end to end and capture proof; recipes in `features/` |
| `.claude/skills/credit-holds/`, `api/formal/credit_holds/` | `/credit-holds`: a TLA+ model check of the credit holds, for a change to a hold, a row charge, the wallet or a bounce refund |
| `landing/` | Astro marketing site (static, own container); copy in `src/i18n/<language>.ts`, one per language of `paths` (typed by `Content` in `src/content.ts`) |
| `web/AGENTS.md` | Next.js instructions; also applies inside `web/` |
| `api/app/`, `api/tests/`, `api/db/` | Python code, tests and SQL |
| `compose.yml`, `ops/`, `.github/workflows/ci.yml` | Runtime, deployment and CI |

## Working rules

- Reuse existing code and installed tools. Keep the diff within the agreed Scope.
  A defect the change causes or makes worse is inside it (see the rules table).
- Trace callers before fixing shared behavior; cover the actual failure path.
- Preserve user scoping, authentication, credit accounting, SSRF protection,
  vendor kill switches and secret/PII redaction.
- SQL lives in `api/db/` as migrations. A schema change is a new migration file;
  never edit one that has run anywhere (see Database below).
- Validate against a disposable test database, never production. Do not copy
  production `.env` files into worktrees or print credentials.
- Changes to API/SSE contracts must account for both Python and web consumers.
- Parallel implementation requires disjoint scopes and separate worktrees;
  shared SQL/contracts require coordination. Expand scope explicitly if necessary.
- Do not lower checks to make them pass. Fix failures caused by the change and
  report unrelated baseline failures or unavailable prerequisites honestly.

## Rules and what enforces them

Mistakes agents repeated here, each paired with what stops the next one. When the user corrects
you, fix the mistake and add its row. A row that names no check is a repeat waiting to happen: if
it recurs, replace it with a check (architecture, type, lint, test) in the same change.

| Rule | Enforced by |
|---|---|
| Tailwind uses the scale and theme tokens, never arbitrary values; the breakpoint is `phone:` / `desktop:` | `web/lint/oxlint/tailwind-scale.mjs` in `web/.oxlintrc.json` (`pnpm lint`); the landing, which has no Oxlint, by `landing/lint/check.mjs` (`pnpm lint` in `landing/`), which also bans `<style>`, `style=` and plain CSS |
| Effects that belong in render, initializers or handlers are rewritten, never suppressed | the effect plugin in `web/.oxlintrc.json`, `web/lint/guard.mjs` (pre-commit, `pnpm lint`) |
| A date or time formats in the user's zone, through next-intl's formatter (`useFormatter()`, `getFormatter()`); a raw `Intl.DateTimeFormat` names its `timeZone`, and `toLocaleDateString` / `toLocaleTimeString` are not used | `web/lint/oxlint/date-time-zone.mjs` and `no-restricted-properties` in `web/.oxlintrc.json` (`pnpm lint`); the tests run in `TZ=UTC` like the container (`web/vite.config.ts`) |
| A count names a next-intl number format (`format.number(n, 'credits')`, `{n, number, credits}`). A message gets the number, not a formatted string. `#` prints only in a `one` or `=N` branch | `web/i18n/number-format.test.ts`, which also fails on `t('k', { n: format.number(…) })`. For a plain `{arg}` message argument: `pnpm typecheck`, which generates `web/messages/*.d.json.ts` and fails when a number goes to a plain `{arg}`. For `#`: `web/i18n/count-plural.test.ts` |
| A count followed by its noun sits in a plural of that count, and the plural has a `one` branch: `{n, plural, one {# riga} other {{n, number, credits} righe}}` | `web/i18n/count-plural.test.ts` |
| Every message key exists in every language, and each language formats an argument as `en`, the base language, does and names no argument that `en` lacks (the typecheck reads `en` only) | `web/messages/messages.test.ts` |
| The interface languages are declared once on each side: `LOCALE_NAMES` in `web/i18n/config.ts`, `Locale` in `api/app/core/locale.py`. English is the default and the fallback. No other file lists them or compares a locale to one of them to choose text | `satisfies Record<Locale, object>` on `CATALOGS` in `web/messages/index.ts` (`pnpm typecheck`); `api/tests/test_locale.py`, which finds every module-level table typed `dict[Locale, …]` in `api/app` and fails on a missing language or key; web tables are `Record<Locale, …>` (`pnpm typecheck`). A hand-written list or a branch on one language: judgment |
| A user sees an error through `useErrorText()` by its code, never `error.message`; every API code has an `errors.*` text in every language | `web/i18n/error-codes.test.ts` (codes scanned from `api/app`, and no `error.message` in `app/` or `components/` outside admin) |
| A migration that ran is never edited or deleted: add a new one | checksums in `schema_migrations`, checked by `app/core/migrate.py` before it applies anything (deploy, compose, SQL tests) |
| A new migration is named after main's newest one: production applies migrations in merge order, a fresh database in name order. Rename yours (`date -u +%Y%m%d%H%M%S`) when main gains a newer one | `harness/migration_order.py` in `init.sh` (api), which names the file to rename |
| A migration resolves the existing data that would fail it (duplicates before a unique index). It never relies on a manual production check before merge | nothing: judgment. Prove it with a SQL test that runs the migration on that data, as in `api/tests/test_linkedin_sql.py` |
| Every foreign key has an index that leads with its first column: a parent delete scans the child table once per deleted row | `api/tests/test_foreign_key_indexes_sql.py`; its error lists each key and the `CREATE INDEX` line to add in a new migration |
| Every query runs in a scope, and the database enforces the organization. A checkout with no scope raises `ScopeMissing`; the pool's session role is `zenbree_app`, under row-level security. Only a `SystemReason` (`api/app/core/system_reasons.py`) crosses organizations, in the modules its `ALLOWED` entry lists. A new reason, or a new module for one, needs the user's approval in the PR | `api/tests/test_scope_entry_points.py`: E1 to E4 (each route, task and stream enters a scope), E5 (a connection comes from the pool), E6 (only the checkout sets the role), E7 (a reason only in its modules), E8 (the reasons file matches a pinned sha256: never update it yourself), E9 (a thread hand-off carries the scope). `api/tests/test_session_role_sql.py`, `test_scope_request_sql.py` and `test_task_scope_sql.py` (the checkout and the task scope), `test_tenant_catalog_sql.py` (the tenant path and policy of each table) and `test_scope_sql.py` (what each scope sees and writes) |
| A change to an API, SSE or tool contract updates `docs/contracts-m1.md` in the same PR | shapes come from `/openapi.json` (Pydantic models), which `test_every_success_response_has_a_schema` keeps complete. The doc holds meaning, events and error codes. `test_every_route_the_contract_doc_names_exists` fails on a route the doc names that is gone. What a field means, an event's payload and an error code: judgment |
| A vendor that bills has a price: its tariffs and its USD price live on one `Vendor` in `api/app/billing/vendors.py` | the structure: tariffs sit on the `Vendor` and `record_vendor_call` takes a `Vendor` (`uv run mypy`). `api/tests/test_vendors.py` catches a `paid_call` operation with no tariff and a web search provider that is not registered |
| A path that `docs/recipes.md`, `docs/migrations.md`, `api/AGENTS.md` or `landing/AGENTS.md` names exists | `harness/test_doc_paths.py` (a harness unit test, run by `init.sh all`). A section name or a symbol in those docs: judgment |
| A chat SSE event exists on both sides, and so does each key of its payload: `ChatEventPayloads` (`api/app/agent/event_payloads.py`, whose fields are `CHAT_EVENTS`) equals `SSE_EVENT_TYPES` (`web/lib/chat/types.ts`), `emit(...)` names only listed events, every frame the tests send fits its model, and `SSEPayloads` (`web/lib/chat/event-payloads.ts`) has the camelCased keys of the generated model | `api/tests/test_sse_events.py`, `chat_payloads_match_models` in `api/tests/conftest.py`, and `parity` in `web/lib/chat/event-payloads.test.ts` (`pnpm typecheck`); each error names the file to edit. The types of values and an emit site no test reaches: judgment |
| The other streams (list fill, CSV import, home, nav, notifications, onboarding) send the event names that the web file handling them handles | `api/tests/test_stream_events.py`; its error names the file to edit. Their payloads have no check: judgment |
| A change to an API request or response model regenerates `web/lib/api/schema.gen.ts` (`pnpm api:types` in `web/`) | `pnpm api:types:check` in `harness/init.sh` (web); the message names the command. Existing hand-written web types do not use the file yet |
| An API error is `api_error(status, code, message)` (`app/core/errors_http.py`), never a bare `HTTPException` or a string detail; an SSE `error` frame carries `{code, message}` too | `api/tests/test_error_shape.py` (an AST scan of `api/app`, and the shape of the framework's own 404/405/422/500); SSE frames: judgment |
| Vendor plumbing columns (`*_id`, `source`, `email_source`, `phone_source`) are hidden, and so are the raw contact statuses `email_status` and `phone_status` in row cells: the web reads the state through `row.lookups` | one owner: `api/app/lists/rows.py`. `public_columns` gives the web the public columns. `public_cells` strips `VENDOR_KEYS` and `STATUS_KEYS` from every row it sends (`with_lookups` builds `lookups` from the raw cells first). `ListRow.cells` (`web/lib/lists/api.ts`) types both status keys so that a comparison fails `pnpm typecheck` (TS2367). `api/tests/test_public_cells.py` |
| Every table uses the `Table` primitives (`StackedTable` for phone cards) with their own look: no raw `<table>`, no frame (`border`, `rounded`) on `Table`, no padding, text-size or header-background overrides (also through `[&_th]:bg-*` or a `TableRow` in `TableHeader`) | `web/components/ui/table.test.ts` |
| The web logs through `logger` (`web/lib/logger.ts`), never `console.*`: its level is switched without a deploy (`LOG_LEVEL` on the server, shared with the API; `localStorage['zb-log-level']` in the browser, `warning` there in production so error reports keep their breadcrumbs). A class with dependencies takes it as an injected `Logger`. No setting mutes errors, and the logger redacts secret and contact keys | Biome `noConsole` in `web/biome.json` (`pnpm lint`); only `lib/logger.ts` and the node scripts in `web/lint/` are exempt |
| Conventional Commit subjects, no AI attribution | lefthook `commit-msg` |
| A test that fails in the full run and passes alone is test pollution, not a flake, and the shared test database is not the cause (each SQL test copies its own). Find the test that leaked the foreign value in the failing assertion (grep it across `api/tests/`), check `git log origin/main -- api/tests` for a fix, then fix the leak or file it as a defect `main` also has. Never rerun and merge | nothing: judgment |
| When a change adds pure logic, or changes what pure logic returns for some input, its tests include at least one property-based test next to the example tests: Hypothesis in `api`, fast-check in `web`. Pure logic is code whose result depends only on its arguments (no database, network, clock or randomness), such as a parser, a formatter, a price or credit calculation or a normalizer. A calculation inside a function that also does I/O is pure logic too. The same holds for a step that must be idempotent, such as a backfill or a Celery task that can run again. An agent picks examples that pass its own logic. A property-based test states what must never happen. The property is an invariant, never the same formula again: a round trip (parse what you format), a bound (a balance never below zero), idempotence (a second run changes nothing) or an order (more rows never cost fewer credits). A counterexample that the tool finds stays as a fixed case: `@example` in Hypothesis, a plain Vitest case in `web`. If the library is not yet a dev dependency of the scope, add it with the first property-based test | nothing: judgment. A checkpoint in `harness/CHECKPOINTS.md` |
| A test that guards a high-risk area fails on a planted bug before the PR is ready. Such a test has an assertion that fails when the auth, scoping, credit, migration, vendor or API, SSE or tool contract code it runs is wrong. The rule applies when the change adds that test or adds or changes one of those assertions. The planted bug is a plausible wrong change, not a crash: a dropped `org_id` filter, a flipped comparison, an off-by-one, a hold that is never settled. Plant it in the code the test guards, run the test, see the expected assertion fail, then remove the planted bug. A failure from an import error or a fixture crash does not count. For a bug fix, the pre-fix code is the planted bug. One planted bug can cover several tests. The Verification section of the PR body names each planted bug and the assertions that failed | nothing: judgment. A checkpoint in `harness/CHECKPOINTS.md` |
| No God classes or modules: every class, module and component follows SOLID (one responsibility, extended rather than edited, small interfaces, depends on abstractions). New logic goes in its own module, called from the existing one, not appended to it | `harness/size_check.py` in `init.sh`: a source file stays within 400 lines; files already over it are in `harness/size-baseline.json` and may only shrink (`--update` lowers the entry). SOLID beyond size is judgment: review it |
| A comment says why, never restates the declaration under it | judgment: a checkpoint in `harness/CHECKPOINTS.md`, so every review checks the added comments. `/no-comments` applies the same test |
| A reviewer agent writes nothing outward and runs nothing: it reads the evidence the leader hands it and returns its verdict, which the leader publishes | `tools: Read, Glob, Grep` in the frontmatter of `.claude/agents/harness-reviewer.md`, no Bash (`harness/test_reviewer_tools.py`). A planted instruction in a PR or issue has nothing to run |
| Throw-away code (mocks, dev scaffolding) is gated out at build time and has no tests | nothing: judgment |
| Agents share one Docker VM: no private test database, no verify stack left running | `.claude/hooks/no-private-postgres.py` (a Claude Code hook that refuses `docker run` of a Postgres image); `harness/testdb.sh` (one shared test database on one named volume); `bin/stack` in `/verify` (stack lease, memory budget, garbage collection of images and build cache) |
| A verify stack starts only while the Docker VM has memory for it, and one stack never takes the memory of another. One full `init.sh` run at a time on the machine | ceilings on every verify service (`compose.verify.yml`; `harness/test_stack.py` checks that each has one and that `VERIFY_STACK_MIB` is their sum); `bin/stack up` admission (running verify containers at their ceilings, other containers at their use, reservations of starting stacks and its own need must fit); one image build at a time (`build.lock`); `harness/heavy-lock.sh`, taken by `harness/init.sh` (not `--fast`) and by each verify image build; the second full run or the build waits |
| Tear down your verify stack before you mark the PR ready | `.claude/hooks/pr-ready-teardown.py` (refuses `gh pr ready`); the lease reaps forgotten stacks after 45 idle min |
| Run the full gate once, before the merge: a follow-up commit on an open PR needs only `./harness/init.sh <scope> --fast` for the scopes it changes. Full reruns after each small follow-up cost about 3 min each | `.claude/hooks/pr-merge-gate.py` (refuses `gh pr merge` and `gh stack merge <top PR>` until the pass cache holds a full `all` pass on the head of each PR they merge, or on a PR above it in its stack; a PR that changes nothing in its `CHECKED` list, or only landing copy in `landing/src/i18n/`, needs none) |
| Wait for a background command's notice, not in a sleep loop: such loops blocked agents for 2.7 h in four days | `.claude/hooks/no-sleep-wait.py` (refuses a foreground loop that sleeps 2 s or more, a `sleep` of 10 s or more, and follow commands such as `tail -f` and `gh ... --watch`) |
| A merge to `main` publishes source changes; deployment belongs to each installation | `.github/workflows/ci.yml` runs checks only |
| In a multi-PR run, owners prove only their own change; the full gate runs once, at the top of the stack | nothing: judgment. The briefs in `.claude/agents/harness-leader.md` |
| A defect the change causes or makes worse is fixed in the same PR, never moved to a risk or a follow-up issue. An issue is only for a defect that `main` also has | `/verify` fails a change that leaves a customer stuck (`.claude/skills/verify/SKILL.md`). The rest is judgment: a checkpoint in `harness/CHECKPOINTS.md` that every review applies to each finding, each issue filed during the task and each limit in the PR |

## Finish

Run relevant full checks and review against `harness/CHECKPOINTS.md`. Record the
commands, results, skips and remaining limits in the handoff/PR. Full `all`
checks are required before merge; a fast run is not equivalent. A full run
without `TEST_DATABASE_URL` starts the shared test database itself, and prints the
time of each step and of any wait for the lock. A full run that
passes on a clean branch is kept for its tree: a rerun on the same tree prints
`PASS (all, cached)` with the commit that passed, and runs only `harness/migration_order.py`, whose answer depends on `main`. The key is
the tracked tree only: ignored files such as `.env` and the date are not in it.
Each full run deletes the entries of branches that a merged PR (of the newest
300) came from. In a stack, one
full run on the top PR covers every PR below it. A diff that changes no file
that `init.sh` checks (`CHECKED` in `.claude/hooks/pr-merge-gate.py`) skips the run. Record the skip in the PR. A change to
user-visible web, API, auth, admin or chat behavior also needs `/verify` evidence:
drive it in the real app and record what was proven and what was skipped. Checks
alone are not that proof. Fix a stale or missing feature recipe in the same PR.
Use an independent
reviewer when available, and never describe self-review as independent approval.
Follow existing user authorization for publishing and merging. Deployment is configured
by each installation; a merge here deploys nothing. After an authorized merge, remove only the task's
clean worktree and merged branch, if its agent has not removed them; never force-delete uncommitted work.

## Project

Zenbree is an AI agent for outbound sales: a chat brief becomes a live lead list, then verified contacts, then email + LinkedIn sequences.

- `api/`: FastAPI + Celery worker/beat, Python 3.14, uv, psycopg 3 with plain SQL (no ORM)
- `web/`: Next.js 16 + React 19 + Better Auth + Tailwind 4/shadcn, pnpm, Node 24. Its Next.js is newer than your training data: read `web/AGENTS.md` first.
- `landing/`: the marketing page, static Astro + nginx, pnpm, Node 24. `PUBLIC_APP_URL` (build arg) sets the app links.
- `compose.yml`: the whole stack (postgres, migrate, redis, api, worker, worker-webhooks, beat, flower, web, landing). The same file runs locally and in prod.

`/openapi.json` is the source for request and response shapes. `docs/contracts-m1.md` holds their meaning: stream events, error codes, auth rules and tool contracts.

## Commands

**Public repository:** GitHub Actions is disabled. Keep the included workflows inactive and free of repository secrets; they contain verification jobs only. Each installation owns its deployment. Run full local `./harness/init.sh all` against a disposable database, collect required `/verify` evidence, and review changes before merge. Record failures and unavailable prerequisites honestly. Publishing source or merging a pull request does not authorize deploying any installation.

When Actions runs, CI runs `harness/init.sh` on every PR. Use the verification gate above and never push to `main` directly. Git hooks (lefthook) catch two things at commit time. `pnpm install` in `web/` installs them, through lefthook's npm postinstall. Without a web install, run `web/node_modules/.bin/lefthook install` or `brew install lefthook && lefthook install`. The api hooks also need `uv` on PATH. `pre-commit` fixes staged files (ruff, Biome), runs Oxlint on the staged web files, and runs `web/lint/guard.mjs`, which rejects unapproved Oxlint suppressions, a blanket `biome-ignore`, and edits to the Biome or Oxlint config. `commit-msg` rejects subjects that aren't Conventional Commits and any AI attribution. Don't skip them with `--no-verify`.

`./harness/init.sh [all|api|web|landing] [--fast]` is the check list CI runs. Add a new check there, not in `ci.yml` or `lefthook.yml`, and update `harness/test_init.py`.

On macOS, a `--fast` run runs itself at utility QoS (`taskpolicy -c utility`), so it gives the CPUs to a full run that holds the heavy lock. Eight CPU hogs at the default QoS took the gate's pytest step from 83 s to 155 s, and at utility QoS to 87 s. Start other CPU-heavy work outside the lock the same way, for example `taskpolicy -c utility pnpm build`, the whole `pnpm test`, or a pytest run over many files. Never start `init.sh all` that way: the full run would then run at utility QoS too. Do not use `taskpolicy -b`. It protects the gate as well, but it keeps the work on the efficiency cores, and a cold `tsc` then takes 40 s instead of 7 s. Containers in the Docker VM (verify stacks, the test database) are outside `taskpolicy`.

For one tool at a time:

API (`cd api`):
- `uv run ruff format .` / `uv run ruff check --fix .`: format and lint
- `uv run mypy`: type check (`app/`, config in `pyproject.toml`)
- `uv run pytest tests/test_agent.py::test_dedupe_key`: a single test
- `TEST_DATABASE_URL=… uv run pytest`: also runs the SQL tests, which skip without it (`-rs` prints why). Each SQL test copies a throwaway database from a template that the migration runner (`app.core.migrate.migrate`) builds once for each version of `api/db`. `eval "$(./harness/testdb.sh)"` starts the database that all worktrees share and sets the variable.
- `uv run uvicorn app.main:app --reload` / `uv run celery -A app.worker worker`: run outside compose (point `DATABASE_URL`/`REDIS_URL` at localhost)

Web (`cd web`):
- `pnpm dev`
- `pnpm lint` (Biome, Oxlint, then `lint/guard.mjs`), `pnpm format` (Biome fix), `pnpm typecheck` (tsc), `pnpm knip` (unused files, exports and dependencies)
- `pnpm test lib/chat/stream-store.test.ts`: a single file. By name: `pnpm test -t '<name>'`
- `pnpm auth:generate`: after a Better Auth config/plugin change, writes the missing tables/columns as a new migration `api/db/<timestamp>_better_auth.sql`. It diffs `lib/auth.ts` against the database in `DATABASE_URL`, so point that at a migrated one (local compose: `DATABASE_URL=postgresql://zenbree:postgres@localhost:5432/zenbree`); with nothing to add it prints `already up to date` and writes nothing

Full stack: `docker compose up --build` (web on :3000, api on :8000, flower on :5555; Flower stays off until `FLOWER_BASIC_AUTH=user:password` is in the root `.env`, as in `.env.example`). The root `.env` is optional and gitignored. If one exists, it must match the existing `pgdata` volume's Postgres password.
Dev seed: `dev/seed.sh` runs `dev/seed/lists.sql` in the running `postgres` container (admin user `dev@zenbree.local` / `zenbree-dev-1234`, 6 sample lists, skipped when present). Nothing in prod calls it.

CI also runs an `audit` job (`pip-audit`, `pnpm audit`). When it goes red, investigate and update the affected dependency. The public workflow runs checks only. Image publishing and deployment must be configured by each installation; `ops/` provides optional scripts.

## Architecture

### Auth across the two apps
Better Auth runs inside `web/` (`lib/auth.ts`: email+password with Resend verification, Google, Microsoft, plugins `jwt` + `admin`, UUID ids). Its tables come from the `api/db/` migrations, in the same Postgres the API uses. The web mints a short-lived EdDSA JWT. FastAPI (`app/auth/middleware.py`) verifies it against the web's JWKS (`AUTH_JWKS_URL`; in compose this is `http://web:3000/...`, not localhost). It also accepts `sbk_` personal API keys on routes that use `current_user_or_api_key`. The admin role is Better Auth's `user.role`, carried in the JWT. `require_admin` reads it. User management, bans and impersonation are Better Auth plugin features on the web side and have no Python API. On signup, a Better Auth `databaseHooks` hook creates the `user_credits` row.

The web calls the API two ways, and both return `Result<T, AppError>` and convert snake_case ↔ camelCase:
- `lib/http-client`: **server-only** (`import 'server-only'`), uses `API_BASE_URL` (internal) + `getToken()` from `lib/auth-token.ts`
- `lib/api/browser.ts`: browser side, uses `NEXT_PUBLIC_API_URL` (baked in at build time) + `authClient.token()`, with one refresh-and-retry on 401

The API always speaks snake_case JSON. Errors are `{detail: {code, message}}`.

### Chat turn = one job
1. `POST /v1/chat` (`app/agent/routes.py`) stores the user message, creates a `jobs` row (one active job per user, 409 `CHAT.JOB_RUNNING` otherwise) and enqueues Celery `run_job`. Then it streams SSE.
2. The worker runs `app/agent/loop.py:run_job`: model → tool calls → registry handlers → results → … up to `MAX_STEPS`. Next it runs a relevance-score pass over list rows and persists the assistant message.
3. Frames go to the Redis stream `job:{id}:events` (`app/agent/events.py`, entry ids `<seq>-0`). The HTTP endpoint tails that stream, so closing the connection does not stop the job. `GET /v1/chat/sessions/{id}/stream` with `Last-Event-ID` replays and re-attaches.
4. The web store (`web/lib/chat/stream-store.ts` + `sse-handlers/`, copied from the earlier implementation) consumes the events. Event names and payloads must match `docs/contracts-m1.md`. A new event must be added to `SSE_EVENT_TYPES` in `web/lib/chat/types.ts`, or the store drops it.

Lists (`app/lists/`) follow the List/Row/Column model List/Row/Column. There is one list per (session, entity: company|person). Rows merge on `dedupe_key`, where existing values win. Columns are created from the keys seen, plus `relevance_score`. Chats and lists both belong to a project (`app/projects/`). A project is a client (#307): it also owns the business profile, exclusions, senders (mailboxes, LinkedIn), campaigns and inbox. The web names the active one in `X-Project-Id` (the `zb-project` cookie, so server components send it too); without it the API answers for the organization's default project.

### Tools
Each tool is one module in `app/agent/tools/handlers/<name>.py` that calls `register(ToolDef(...))` at import time. `handlers/__init__.py` imports them all. A handler is `async (args, ctx: ToolContext) -> ToolResult` and must never raise on vendor errors. `rows` are flat dicts using the canonical company/person keys in the contracts doc. `row_updates` patch existing rows.

- **Credits**: the loop takes one hold per job (`reserve_project_credits`), grows it on demand (`grow_reservation`) and settles or releases it at the end. The store bills each found row it lands at the tool's `Price` (`app/lists/delivery.py`): a waterfall that finds nothing bills 0. The credit functions are plpgsql in migrations under `api/db/`, starting with the baseline (`api/db/20261005000000_baseline.sql`). Each charge names its hold (`row_charges.hold_id`), and the turn's close and the beat reaper in `app/worker.py` both settle a hold from those charges (`settle_hold`).
- **Vendor costs**: every paid vendor call goes through `app.billing.paid_call`. It writes `vendor_calls` and holds the per-operation tariff. An operation with no tariff raises. LLM usage is metered separately (`app/llm/metering.py` → `llm_usage`). Both feed `/v1/admin/costs`.
- **Kill switch**: `settings` rows `vendor_disabled:<provider>` / `channel_disabled:<channel>`, read through `app.admin.settings.is_disabled` (30s cache, fail-open).
- Contact enrichment is a port/adapter waterfall (`app/people/ports` + `adapters/composite_contact_enrichment.py`: Apollo, AIArk, Treg). Web search and scraping use Firecrawl / Exa / Serper (`app/scraping/`). LinkedIn goes through Unipile (`app/linkedin/`), which stores only the Unipile account id, never credentials.

### LLM
`app/llm/router.py` sends calls to DeepSeek first and falls back to Anthropic (`LLM_PRIMARY_PROVIDER` / `CHAT_PRIMARY_PROVIDER`). When it is configured, calls go through the Cloudflare AI Gateway (`gateway.py`) on each provider's native endpoint. Model prices come from `app/llm/data/litellm_model_prices.json`. With no LLM key set, chat cannot answer.

### Database and schema changes
Every database comes from the migrations in `api/db/`: `<UTC timestamp>_<name>.sql` (`date -u +%Y%m%d%H%M%S`), applied in name order by `app/core/migrate.py`. A new migration sorts after every migration on `main` (`harness/migration_order.py`). Each runs once in one transaction, and the runner stops, applying nothing, when an applied migration was edited or deleted. Later migrations are plain SQL with no `IF NOT EXISTS` and no `CREATE INDEX CONCURRENTLY`.
The compose service `migrate` runs before every other service; to apply a new migration to a running local stack: `docker compose run --rm migrate`. The schema is forward-only: rollback = the previous image on the current schema (`SKIP_MIGRATE=1 ops/deploy-production.sh <sha>`). The baseline, the deploy dump, the restore procedure and the runner's failure cases are in [docs/migrations.md](docs/migrations.md). Each process has one sync pool (`app/core/db.py`): `with get_conn() as conn:` commits on success, and rows come back as dicts.

### Config
`api/app/core/config.py` holds a single pydantic `Settings`. New fields go at the bottom in a commented block, and a matching entry goes in `api/.env.example`. An empty vendor key makes that vendor inert rather than an error. Web env is in `web/.env.example`. `NEXT_PUBLIC_*` values are build args, so changing them requires rebuilding the web image.

### Web
App Router groups: `app/(app)/` for the authenticated product (chat, lists, billing, settings/integrations), `app/admin/` for the back office (costs, users, chat sessions, jobs, settings), and `app/auth/`. i18n uses next-intl with English as the base language (default and fallback); the languages are `LOCALE_NAMES` in `web/i18n/config.ts`. Messages are split per workstream and per language (`messages/<language>.json`, `admin.<language>.json`, `account.<language>.json`, merged in `messages/index.ts`), and every key of `en` must exist in every other language (`messages.test.ts` checks this). Biome enforces no `any`, no non-null assertions, no unused imports or variables, and cognitive complexity ≤ 25. Oxlint (`web/.oxlintrc.json`) runs the rules that `web/lint/` owns: the effect plugin flags effects that should be render-time code, initializers or event handlers ("You Might Not Need an Effect"), and the `zenbree/*` rules flag arbitrary Tailwind values and dates outside the user's zone (see `web/lint/README.md`). Fix the code; never suppress them or restructure code to dodge them. If you believe a hit is a false alarm, stop and ask the user: only the user approves a suppression (`web/lint/README.md`, `web/lint/guard.mjs`). knip fails on unused exports: drop the `export` or delete the code.

## Conventions

- `ponytail:` comments mark deliberate shortcuts and known limitations ("not done yet, here's why"). Keep the marker when you write one, and read existing ones before "fixing" what looks like a gap.
- Code, comments, commit messages, PR titles and PR descriptions are in English. Some older docs and commits are in Italian.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`. Use `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `ci`, `chore` or `revert`. The scope is optional and names the area, e.g. `fix(agent): …`, `feat(people): …`. PRs squash-merge, so the PR title becomes the commit subject and keeps the PR number: `feat: projects for chats and lists (#100)`.
- No AI attribution anywhere: no `Co-Authored-By: Claude` trailer in commits, no "Generated with Claude Code" line in PR descriptions.
- Python style: ruff format (single quotes, line length 120). The lint rules and their documented ignores are in `api/pyproject.toml`. A `# noqa: CODE` or `# type: ignore[code]` needs a reason after it. TS: Biome, 2 spaces, single quotes, semicolons, trailing commas, width 100.
