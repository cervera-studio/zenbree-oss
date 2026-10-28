---
name: verify
description: Drive the real Zenbree app (Next.js web on top of the FastAPI API, Celery worker and Postgres) the way a user does, and capture proof. It starts an isolated compose stack, runs headless Chrome over CDP, and checks the API with curl and the database with psql. Use it to confirm a web, API, auth, settings, admin or chat change works end to end. Unit tests and `harness/init.sh` are not enough for that.
---

# Verify Zenbree in the real app

This skill drives a private copy of the whole stack: postgres, redis, api, worker, worker-webhooks, beat and web. The copy is compose project `zenbree-verify`, with its own volumes, ports, secrets and image tag. The developer's own `zenbree` stack (ports 3000/8000) is never touched. **Never drive the developer's stack on :3000/:8000.** Their sessions and data live there.

Paths below are relative to this skill's directory (`.claude/skills/verify/`) in the checkout you are verifying. Run them from that checkout: the stack builds from the checkout that contains `bin/stack`. For a worktree, use that worktree's copy.

Feature recipes live in [`features/README.md`](features/README.md). Read the index, then follow the matching feature file.

## Who drives

Each turn of a drive re-reads the whole context of the agent that drives. In 267 runs (2026-10-02 to 10-16), 56% of the tokens went to fix work done while the stack was up. A paired trial on #915 (#951): the original run in the author's session spent 9.9M tokens on its drive, stack and retry turns (35.9M with the rebase and fixes in the same window, 172 min). A new verifier drove the same cases in 2.4M (2.7M in all, 8 min) and caught a planted regression. From 2026-10-15 to 10-19, 18 drives in the author's session took a median 23 active minutes and 126 tool calls. 15 drives in a new verifier, 12 of them on Sonnet, took a median 5.8 active minutes and 36 calls. Active time leaves out gaps over 120 s. In the verifier drives, the medians were 2.5 minutes for model turns, 1.4 for `bin/browser` and 0.6 for `bin/stack up`.

- Finish the change before the first `bin/stack up`.
- Give the drive to a new subagent that did not write the change. Run it on Sonnet at medium effort: the drive follows a recipe and needs fast turns, not deep reasoning. Its brief holds the worktree path, the diff (`git diff origin/main...HEAD`), the acceptance, the recipe to follow, a free `VERIFY_ID` with ports, and the one-call rule below. It returns one row per case (case, action, read-back, result, artifact) and does not fix.
- The verifier drives each case in one Bash call. Each call is one model turn, and the turns cost more time than the browser. Export the variables once at the start of the call, because a `VERIFY_ID=` prefix sets them for the first command only. Chain the actions and the evidence dumps with `&&`. Join the screenshot and the read-back with `;`, so that a failed step still leaves them. For example:

  ```bash
  export VERIFY_ID=i865 VERIFY_WEB_PORT=13865 VERIFY_API_PORT=18865 && eval "$(bin/stack env)" \
    && bin/browser fill textbox "Nome" "Acme" && bin/browser ax --out "$VERIFY_ARTIFACTS/<feature>/<case>-before.txt" \
    && bin/browser click button "Salva" && bin/browser wait text "Salvato"; rc=$?
  bin/browser screenshot "$VERIFY_ARTIFACTS/<feature>/<case>.png"; bin/browser text --out "$VERIFY_ARTIFACTS/<feature>/<case>.txt"; [ "$rc" = 0 ]
  ```

  Run `ax` in a call of its own only when you need a target name.
- Fix in the session that wrote the change. Then give the verifier only the failed cases to drive again.

## Launch

Prerequisites are Docker, Google Chrome (or set `CHROME_PATH`), Node 22 or later on `PATH`, `curl` and `openssl`.

```bash
# name the stack after your task; repeat the variables on every call
VERIFY_ID=i865 VERIFY_WEB_PORT=13865 VERIFY_API_PORT=18865 bin/stack up      # builds api + web images (first time: several minutes), starts, waits
eval "$(VERIFY_ID=i865 VERIFY_WEB_PORT=13865 VERIFY_API_PORT=18865 bin/stack env)"   # exports VERIFY_WEB VERIFY_API VERIFY_ARTIFACTS VERIFY_STATE VERIFY_PROJECT
VERIFY_ID=i865 VERIFY_WEB_PORT=13865 bin/browser start   # headless Chrome with its own profile, locale cookie it
```

The stack is ready when `bin/stack up` prints both `ready: api health (http://localhost:<api port>/health)` and `ready: web sign-in page (http://localhost:<web port>/auth/sign-in)`. It exits non-zero otherwise. `up` is idempotent. Rerun it after every app code change, because the images are built from the checkout. An image tag is a hash of its build inputs (`api/` or `web/`, committed and uncommitted). So a rerun with no change builds nothing, a web-only change reuses the api image, and two checkouts with the same inputs share one image. The web image bakes the placeholder `http://verify-api.invalid` as its API URL, and the web container writes the stack's API URL over it when it starts, so stacks on other ports share one web image too.

- **Web:** http://localhost:13000. **API:** http://localhost:18000. Flower is not started.
- The schema comes from the `api/db` migrations: the compose service `migrate` applies the pending ones on every `up`, before api and web start. `bin/stack up` runs the seed after that. A migration added in the checkout lands on the next `bin/stack up`; `bin/stack psql -tAc "SELECT name FROM schema_migrations"` lists what ran, and `bin/stack logs migrate` shows a failure.
- Every `bin/stack`, `bin/browser` and `bin/sheet-drag` call needs `VERIFY_ID=<name>`, in the same command: an agent's shell drops exports between calls. The stack gets project `zenbree-verify-<name>` and its own state dir. There is no shared default id: on 2026-10-09 an `up` without one rebuilt another session's stack onto its own branch. The recipes write `bin/stack` and `bin/browser` without the variables, so add them. Pick free ports with `VERIFY_WEB_PORT` / `VERIFY_API_PORT` when another stack runs. Rerunning `up` with other ports rewrites them in the state env, keeps the secrets, and builds nothing.
- **Chat needs an LLM key.** Run `export DEEPSEEK_API_KEY=…` (or `ANTHROPIC_API_KEY`) in the shell before `bin/stack up`. Without a key, every chat turn fails. Never copy the checkout's `.env`: the overlay ignores it on purpose. When the checkout's `.env` or the main checkout's `.env` has a key and the shell exports none, `bin/stack up` stops and prints the command that exports only that key, without printing its value. A recipe that needs a keyless stack (a failed turn) starts it with `VERIFY_LLM=off bin/stack up`, which also drops an exported key. Report a keyless run as `VERIFY_LLM=off`, never as "no key available". `compose.verify.yml` reads the key from the shell each time compose creates a container. Keep it exported for every `bin/stack compose -f <override>.yml up -d <service>` too: an api recreated in a shell without it has no key, and onboarding then fails with `All providers failed` in `bin/stack logs api`. Every `bin/stack compose` subcommand that creates containers (`up`, `run`, `create`, `scale`, `watch`) applies the same check, and on a `VERIFY_LLM=off` stack it passes no key. Run the printed export in the same command as `bin/stack`, because an agent's shell drops exports between calls. With no key in a local `.env`, `bin/stack doctor` still prints `doctor: OK` for a keyless api, so read its `llm:` line.
- Email is not sent. Auth mail (web) and notification mail (API) go through Resend, which needs `RESEND_API_KEY` and `EMAIL_FROM`; the stack has neither, so the web logs `auth.email.failed` and no link, and the API stores notifications without emailing them. To see an email, render it ([notifications](features/notifications.md#emails)). `bin/stack verify-link <email> [path]` rebuilds the verification link (a token signed with this stack's `BETTER_AUTH_SECRET`), landing on `path` (default `/`; a sign-up from an invitation passes `/invite/<id>`), and `bin/stack reset-link <email>` returns the newest pending password-reset link from the `verification` table. Both go through the real Better Auth endpoints; neither proves delivery.

## Real vendor keys

The overlay passes these vendor names from the shell, and an unset one stays inert: `APOLLO_API_KEY`, `AIARK_API_KEY`, `TREG_API_KEY`, `MILLIONVERIFIER_API_KEY`, `EXA_API_KEY`, `EXA_ENABLED`, `SERPER_API_KEY`, `FIRECRAWL_API_KEY`, `DEEPSEEK_USER_ID_SECRET` (and the `*_BASE_URL` of Apollo, AI Ark and Treg). To run the real waterfall, verification or web search, export only the names the recipe needs, in the same command as `bin/stack`, for example `export AIARK_API_KEY="$(infisical secrets get AIARK_API_KEY --env=prod --plain --silent)"` (one call per name). Never write a key into a worktree, print it, or import other prod variables. Real calls bill the vendor: use the fewest rows that prove the change.

## Doctor

Run `bin/stack doctor` first, and again whenever something looks off. It is read-only. It reports:

- **Build:** current or STALE. STALE means the images predate HEAD or uncommitted edits under `api/` or `web/`. Fix it with `bin/stack up`.
- **Services:** one line for each service that `up` starts, read from compose. A service that is not running makes the doctor NOT OK, and its line gives the fix, for example `service: beat exited (1): run bin/stack compose start beat`. The overlay sets `restart: "no"`, so a crashed `beat` or `worker` stays down until you start it, and the drive then fails with symptoms that point elsewhere. The one-shot `migrate` shows `exited (0), one-shot: done`.
- **Ports:** 13000 and 18000 are owned by `zenbree-verify`'s web and api, not by something else.
- **API:** `/health` returns `{"status":"ok","db":"ok","redis":"ok"}`.
- **Web:** `/api/auth/jwks` answers. The API verifies JWTs against it.
- **LLM:** whether chat can answer. `off by choice` means the stack started with `VERIFY_LLM=off`. A keyless api while a local `.env` has a key makes the doctor NOT OK: an api recreated in a shell without the key.
- **Browser:** the pid and port of this run's Chrome, if started.
- **Teardown:** the `bin/stack down` command for this stack, with its `VERIFY_ID`.

It ends with `doctor: OK`, or `doctor: NOT OK` with exit code 1. Do not drive a NOT OK instance. A web 500 on every page right after `up` usually means a stale `web` build. Check with `bin/stack logs web`.

## Use the app like a customer

A customer does not see your test cases. They see the state their account is in. Before you drive, list the states where the change can leave a customer stuck: unable to act, charged for nothing, or shown a wrong answer for good. Look for at least these:

- a vendor kill switch on (`settings` row `vendor_disabled:<provider>`), or a vendor with no key;
- a vendor that fails: a 5xx, a timeout, or a refused request;
- rows, settings, or messages that the code before the change wrote and the new code now reads;
- data a customer brings in another shape, such as a CSV row with only a full name.

Put the stack in each state. Then do the task the way a customer does it: from the UI, and again after a reload. If the change leaves a customer stuck in one of these states, the verify fails.

A state that needs data or a failure is not a reason to skip a screen. The stack is disposable, so reach it:

- **Data.** Insert the rows with `bin/stack psql`. Seeding is setup, not proof: the action under test still runs from the UI.
- **A failed server action.** Run `bin/stack compose stop api`, trigger the action, and read the error the customer sees. Run `bin/stack compose start api` to recover.
- **A pending state.** Run `bin/stack compose pause api` to hold a request in flight, then `unpause`.
- **A vendor that has no key.** Skip only a screen whose state has no stand-in, such as a real OAuth consent page. Report the command you tried and why it failed, as the Evidence rules below ask.

## Drive

**Browser** (`bin/browser`, raw CDP, no dependencies). Every command connects to this run's Chrome, acts on its single tab and exits. Targets are ARIA **role + accessible name**, exactly as `bin/browser ax` prints them. Run `ax` whenever you need a name. Do not guess.

```bash
bin/browser goto "$VERIFY_WEB/auth/sign-in"
bin/browser ax                                   # role "name" tree: the source of target names
bin/browser fill textbox "Email" "user@verify.local"
bin/browser click button "Accedi"
bin/browser click button "Salta per ora"       # a new user lands on /onboarding first
bin/browser wait text "Chi vuoi raggiungere per primo?"   # first-access home; features/auth.md names the closed-welcome heading
bin/browser select combobox "Tipo" "Email"        # native <select> by option text
bin/browser press Enter
bin/browser wait-url /settings/api-keys
bin/browser wait-gone button "Ferma"
bin/browser eval "fetch('/api/auth/token').then(r => r.json())"   # same-origin calls with the session
bin/browser text --out "$VERIFY_ARTIFACTS/<feature>/page.txt"
bin/browser screenshot "$VERIFY_ARTIFACTS/<feature>/page.png" [--full]
bin/browser locale en                            # switch UI language (then goto/reload)
bin/browser upload "input[type=file]" ./list.csv # file inputs, even visually hidden ones
bin/browser viewport 390 844                     # phone layout (clamps to 500 wide); viewport 1440 900 restores
bin/browser screenshot shot.png --width 390 --height 2200   # any command: lay out at 390 wide (under the 500 floor) for that command only
```

Fallback targets are `css "<selector>"` and `text "<exact visible text>"`. If several elements match, add `--nth <i>`. Waits time out after 15 s; change that with `--timeout <ms>`. `click` sends a real mouse event at the element's center. `fill` focuses the field, clears it and inserts text, which React-controlled inputs accept.

- **Locale.** `bin/browser start` sets the `NEXT_LOCALE=it` cookie, so the UI is Italian and the recipes use the Italian names. The product's own default is English (#699): a browser with no cookie and no supported `Accept-Language` gets it. `bin/browser locale <en|es|fr>` and `VERIFY_LOCALE=<code> bin/browser start` give another language. There the names differ, so run `ax` again.
- **Users.** Test the sign-up path itself in the browser ([auth](features/auth.md)). For any other feature, create a verified user with `bin/stack seed-user <email> <password> [name]`. It takes the steps a person takes through the real Better Auth endpoints: sign-up, `verify-link`, the set-password link that the first verification redirects to (#1006), and sign-in. It fails unless the user signs in with the given password. Then sign in from the browser. A new user lands on `/onboarding`; choose `button "Salta per ora"` to reach the app.
- **Theme.** Headless Chrome follows the host's appearance, so on a Mac in dark mode the app renders dark. Pick `Chiaro` in [settings profile](features/settings-profile.md) when a light screenshot matters.
- **Admin.** `bin/stack make-admin <email>` sets `"user".role`. The next page load carries the role in the JWT, so there is no need to sign in again.
- **API.** API calls take `Authorization: Bearer <token>`. For the token, either:
  - create an `sbk_` key in the UI ([api-keys](features/api-keys.md)), or
  - take a session JWT with `bin/browser eval "fetch('/api/auth/token').then(r => r.json())"`.
  
  Then run `curl -sS -H "Authorization: Bearer $TOKEN" "$VERIFY_API/v1/me"`. Errors have the shape `{"detail":{"code","message"}}`.
- **Database.** `bin/stack psql -tAc "<SQL>"` runs against the verify database only.
- **Logs.** `bin/stack logs web|api|worker|worker-webhooks|beat`. Set `TAIL=n` to change the length (default 200). Chat jobs run in `worker`; webhook deliveries in `worker-webhooks`.

## Debug

`bin/browser` drives the app and produces the proof. When a step fails and the cause is in the page (a console error, a failed request, a slow render), open the same screen with the chrome-devtools MCP (`mcp__plugin_chrome-devtools-mcp_chrome-devtools__*`). It reads what `bin/browser` cannot:

- `list_console_messages` / `get_console_message`: errors and warnings, with stack traces.
- `list_network_requests` / `get_network_request`: status, headers and bodies of fetch, XHR and document requests.
- `performance_start_trace` / `performance_stop_trace` / `performance_analyze_insight`, and `lighthouse_audit`: render timing and page audits.

The MCP runs its own Chrome, not the one `bin/browser` started, so it has no session yet:

1. Open the page in an isolated context: `new_page` with `url: "$VERIFY_WEB/auth/sign-in"` and `isolatedContext: "verify"`. Cookies ignore the port, so without the isolated context the profile can carry a session from the developer's stack on :3000.
2. Sign in with the user you seeded: `take_snapshot` for the uids, then `fill` `textbox "Email"` and `textbox "Password"`, and `click` `button "Sign in"`.
3. Reproduce the failing step, then read the console and network lists. Pass `includePreservedMessages` / `includePreservedRequests` to keep entries from before the last navigation.
4. `close_page` when done.

Gotchas:

- The MCP browser has no `NEXT_LOCALE` cookie and renders English, so names differ from the Italian recipes. Click `button "Italiano"` in the footer, or use the English names from `take_snapshot`.
- Every Claude session shares one MCP Chrome profile (`~/.cache/chrome-devtools-mcp/chrome-profile`). A second session that opens the MCP while another holds it fails to start Chrome. With parallel `VERIFY_ID` stacks, debug one at a time.
- The MCP is a Claude Code plugin. Other agents do not have it; they debug with `bin/stack logs` and `bin/browser eval`.
- Debug output is diagnosis, not proof. Re-drive the fixed path with `bin/browser` and capture the evidence below.
- Exception: when headless `bin/browser` cannot reach a path, drive it with the MCP and treat it as proof under the same Evidence rules. Examples are the real clipboard, a browser permission prompt, and a second tab. The MCP Chrome is headed, so on macOS a copy button writes the real clipboard and `pbpaste` reads it back. `navigator.clipboard.readText()` waits on a permission prompt that the MCP cannot grant, so do not use it. The one-profile caveat above still holds.

## Evidence

Write everything under `$VERIFY_ARTIFACTS/<feature-id>/`, which is `~/.cache/zenbree-verify/<VERIFY_ID>/artifacts/`. It is outside the repo and is never committed. Cleanup keeps it.

The proof standard:

- **Use the real user path.** Drive the browser for UI features, and give curl the token a user would have. Do not use internal setters, direct SQL writes or test-only endpoints to produce the result you are proving. SQL is for setup (`make-admin`) and for reading side effects.
- **Capture both the action and the resulting state.** For example, an `ax` dump after `fill` and before `click`, then the `ax`/`text` and a screenshot of the result.
- **Verify side effects** with a second, read-only view: a `psql` row, a `curl` of the API, or a reload of the page. A toast or status message alone is not proof.
- **Use mocks only at existing production boundaries.** Missing vendor keys make those vendors inert by design. Report what that skipped; do not describe it as verified.
- **Redact secrets.** `sbk_` keys, JWTs and verification tokens must not appear in your report. Write `sbk_<redacted>`.
- **Report what failed.** Name the feature id and entry point of every artifact. If a step was unreachable, report the command and the unmet precondition. Do not substitute another path.

## Cleanup

```bash
bin/stack down
```

`bin/stack down` does the following:

- Stops the Chrome this run started (by its recorded pid, never by process name).
- Runs `docker compose -p zenbree-verify down -v --remove-orphans`, which removes only this project's containers, network and volumes.
- Deletes the generated secrets and the Chrome profile.
- Prints the path of the kept artifacts.

The `zenbree-*:verify[-<id>]` images stay as build cache.

Run `bin/stack down` after every run, including failed ones. When you run `bin/stack up`, add `bin/stack down` (with the same `VERIFY_ID`) as the last step of your plan. `up` and `doctor` print it as `teardown: <command>`, with this stack's `VERIFY_ID`. Tear the stack down before you mark the PR ready. The hook `.claude/hooks/pr-ready-teardown.py` refuses `gh pr ready` while a stack that runs your PR's branch or commit is up, and prints the `down` command. Run only `bin/browser stop` if you want to keep the stack up for another drive. Never `docker compose down` without `-p zenbree-verify`: from the repo root, that targets the developer's stack.

Many agents share one Docker VM, so `bin/stack` also limits what a forgotten stack can cost:

- **Lease.** Every `bin/stack` and `bin/browser` call renews the stack's lease. The next `bin/stack up` from any checkout tears down a stack that is idle for `VERIFY_TTL_MIN` minutes (default 45), or whose checkout is gone. If you pause longer than that, expect a fresh database on your next `up`.
- **Memory budget.** Every service of a verify stack has a ceiling (`mem_limit` in `compose.verify.yml`), so a container that grows past it is killed alone, in its own stack. The verify worker runs 2 processes and `worker-webhooks` 1, and Postgres uses 32MB of shared buffers. `bin/stack up` starts when its own need fits in the Docker VM's memory minus a headroom, after it subtracts three amounts: each running verify container at its ceiling, every other container (the developer's stack, the test database) at its use, and the reservations of the other starting stacks. A stack is charged at its ceilings because stacks admitted while the others idled ran the VM out of memory when they all grew (2026-10-16). Its own need is `VERIFY_STACK_MIB` (default 1552, the sum of the ceilings; `harness/test_stack.py` fails when they differ). When an image is missing, it is `VERIFY_BUILD_MIB` (default 2816): the build ends before the containers start, so the two costs do not add. A re-up that builds stops its old containers first: with them running, a rebuild beside another stack did not fit. An uncached web build peaked at 2390 MiB with two page-data workers and no type check, the settings that `compose.verify.yml` passes to the web build (`NEXT_BUILD_WORKERS`, `NEXT_BUILD_SKIP_TYPECHECK`). Next's default of one worker per CPU took it to 3224 MiB, and under a lower ceiling the build stalled for minutes or was killed (2026-10-16). Builds run in their own BuildKit container, `buildx_buildkit_zenbree-verify0`, whose ceiling is `VERIFY_BUILD_MIB`. `bin/stack` creates it, prunes its cache to `VERIFY_BUILD_CACHE_MAX` and stops it after each build. A build in dockerd's own BuildKit grew dockerd to 2 GiB, which no admission saw and which stayed after the build (2026-10-16). The headroom is `VERIFY_HEADROOM_MIB` (default 768: dockerd, BuildKit and the kernel are not in `docker stats`). If the stack does not fit, `up` retries every 10 seconds and prints `waiting for Docker memory: need N MiB, free F of T MiB` about once a minute, with each running stack and each starting stack. It gives up after `VERIFY_WAIT_MIN` minutes (default 20) and names them. A Docker VM short of memory can leave `docker stats` hanging for minutes: admission waits `VERIFY_DOCKER_SEC` (default 30) for each Docker call, prints `docker did not answer in 30 s` and retries, so it never holds `admit.lock` for longer. While it waits, it reaps idle stacks once a minute. With the 5.8 GiB VM, about three stacks run at the same time, or one stack and one build. An uncached build takes about a minute, so a second build waits for that time. One image build runs at a time on the machine: a second build prints `waiting for the heavy-job lock: image build of ...` (`build.lock`). Do not tear down a stack that another agent started. A full `harness/init.sh` run keeps its own lock and prints `waiting for the heavy-job lock` while another full run is active. A verify build takes that lock too, after `build.lock`, so a build and a full run never share the CPUs: together they took a full run to 409 s (2026-10-16). A build that waits prints `waiting for the heavy-job lock: init.sh all from ...`. The admission check itself runs under its own short lock (`admit.lock`, about 2 seconds), so a second `up` can print `waiting for the heavy-job lock: memory admission of ...` for up to 10 seconds.
- **Garbage collection.** `up` removes the verify images that no container uses, and the state dirs with no stack whose newest file is older than 7 days. It also trims the Docker build cache to `VERIFY_BUILD_CACHE_MAX` (default 20GB), least recently used first. The cache is shared with every other project on this Docker. `bin/stack prune` does the same, reaps as `up` does, and lists the stacks it kept.

## Helpers

| Helper | Run | Purpose |
|---|---|---|
| `bin/stack` | `bin/stack help` | Stack lifecycle: `up`, `doctor`, `env`, `logs`, `psql`, `verify-link`, `reset-link`, `seed-user`, `make-admin`, `compose`, `down`, `prune` |
| `bin/browser` | `bin/browser help` | Chrome: `start`, `stop`, `locale`, `goto`, `click`, `fill`, `select`, `press`, `wait`, `wait-url`, `wait-gone`, `ax`, `text`, `screenshot`, `eval`, `url`, `upload`, `viewport`, `contrast` |
| `bin/fake-unipile` | `bin/fake-unipile [port] &` | Stand-in Unipile API on the host (default :18765) so LinkedIn connect reaches the database; wiring in [integrations](features/integrations.md) |
| `bin/fake-posthog` | `bin/fake-posthog [port] > ph.log &` | Stand-in PostHog host on the host (default :18766) that logs each request with its decoded body; wiring in [auth](features/auth.md) |
| `bin/sheet-drag` | `bin/sheet-drag <steps> <px> <ms> [hold ms]` | Drags the open bottom sheet's grab handle with real touch events and prints the sheet's offset during and after the drag; `bin/browser` has no drag command. Use in [lists](features/lists.md) and [campaigns](features/campaigns.md) |
| `bin/fake-apollo` | `bin/fake-apollo [port] > apollo.log &` | Stand-in for Apollo on the host (default :18767): the organization endpoints, so a watchlist run reads job postings, and the people search, so a saved search finds three people, both without a key; wiring in [watchlists](features/watchlists.md) and [saved searches](features/saved-searches.md) |
| `bin/fake-instantly` | `bin/fake-instantly [port] > instantly.log &` | Stand-in Instantly API v2 on the host (default :18768): a workspace, three campaigns and a lead store; one email cuts the connection after it stored the lead. `GET /state` shows what it holds. Wiring in [instantly](features/instantly.md) |
| `bin/webhook-call` | `WEBHOOK_URL=<url> bin/webhook-call [--unsigned \| --old-key \| --wrong-key] <webhook-id> <body> [timestamp]` | Signs one Standard Webhooks call with the secret in `$VERIFY_STATE/hook-secret` (`--old-key`: the one before the last rotate; `--wrong-key`: a random one), posts it with a 30 s timeout, and prints the answer and the status. Use in [automations](features/automations.md#webhook) |
| `bin/webhook-secret` | `bin/webhook-secret save \| redact <file>...` | `save` reads the secret from the open dialog into `$VERIFY_STATE/hook-secret`, keeps the one before as `hook-secret.old`, and masks the dialog for screenshots. `redact` masks every `whsec_` key in text dumps. Neither prints the secret. Use in [automations](features/automations.md#webhook) |
| `compose.verify.yml` | used by `bin/stack` | Overlay on `compose.yml`: reset `.env`, remap ports, verify-only origins, auth secret, LLM key, Apollo, Unipile and Instantly passthrough, `restart: "no"`, no flower |

State (secrets, Chrome profile, image tags, lease) lives in `~/.cache/zenbree-verify/<VERIFY_ID>/`. Override it with `VERIFY_HOME`. `down` deletes every Chrome profile under the state dir, also those of extra browsers, and keeps `artifacts/`. Each `up` that reaches ready appends a line to `~/.cache/zenbree-verify/timings.tsv`: time, id, images built, then the seconds spent waiting for memory, building and starting, and until ready.
