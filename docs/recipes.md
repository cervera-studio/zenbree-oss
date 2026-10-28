# Recipes for common changes

Each recipe lists the files to touch and the check that proves the change. A step marked **Not enforced** has no test, lint or type that fails when you skip it. Check those steps by hand. Paths are relative to the repo root. Read [AGENTS.md](../AGENTS.md) first.

Run the checks for one scope with `./harness/init.sh <api|web|landing> --fast`. Run a single test with the commands in [AGENTS.md](../AGENTS.md#commands).

- [Add a tool](#add-a-tool)
- [Add a vendor](#add-a-vendor)
- [Add an SSE event](#add-an-sse-event)
- [Add a list column](#add-a-list-column)
- [Add a config field](#add-a-config-field)
- [Add a migration](#add-a-migration)
- [Add a web settings page](#add-a-web-settings-page)
- [Add a message catalog](#add-a-message-catalog)
- [Add an interface language](#add-an-interface-language)
- [Change a row state the web shows](#change-a-row-state-the-web-shows)

## Add a tool

1. Create `api/app/agent/tools/handlers/<name>.py`. Define `async def handle(args, ctx: ToolContext) -> ToolResult` and end the module with `register(ToolDef(...))`. Use `api/app/agent/tools/handlers/search_jobs_signals.py` as a small model. A handler never raises on a vendor error: it returns `ToolResult(ok=False, error=...)`.
2. Nothing to import: `api/app/agent/tools/handlers/__init__.py` imports every public module in the package, so a new module registers itself. Start a helper module's name with an underscore (like `_rows.py`) so it is not imported as a tool.
3. Set the fields of `ToolDef` (`api/app/agent/tools/registry.py`):
   - A tool that bills per row declares a `Price` (`api/app/billing/price.py`) and passes it as `price`. A tool with no `price` is free. `credits` is the price of one row found, `batch` the rows a call that names no row can deliver, `keys` the cells a charge buys, and `found` tells a found row from a miss by the cells of its update. Return each row as a `row_update`. The store bills each one that `PRICE.found` accepts and that lands, at most the rows the call was estimated for (`api/app/lists/delivery.py`). The handler returns no charge, as `enrich_phone.py` shows. The scorer hides every key of every price (`registry.billed_keys()`).
   - `provider` names the vendor for the admin kill switch. The loop refuses the call when `vendor_disabled:<provider>` is on.
   - `found_noun` names what the tool finds, for the summary card.
   - A tool that bills per row takes `row_ids`, `people` or `items` in its `input_schema`. `units` in `api/app/billing/price.py` reads only those keys, and otherwise uses `Price.batch`.
4. A paid tool needs no edit in `api/app/agent/plan.py`, `api/app/agent/units.py` or `api/app/agent/vendors.py`. A plan prices a stage from `ToolDef.price`, and `vendors.py` records the vendors that `paid_call` saw. Edit `units.py` or `plan.py` only for a tool that needs a special price, as `search_companies` with AI Ark does.
5. If the tool calls a paid vendor, follow [Add a vendor](#add-a-vendor).
6. Add the label `chat.tools.<name>` to `web/messages/en.json` and `web/messages/it.json`.
7. Describe the tool in `docs/contracts-m1.md`, section "Agent tools".
8. Add a test in `api/tests/test_<name>.py` (see `api/tests/test_find_rows.py`). A test that needs the database uses `_job(db, user_id, ...)` from `tests/test_agent.py` once for each user: a user can hold one active job (`uq_jobs_one_active_per_user`), so a second `_job` for the same user raises `UniqueViolation`. Make a second user, or finish the first job (`UPDATE jobs SET status = 'succeeded'`, as `test_agent.py` does) before the second `_job`: passing the same `session_id` does not help, because `uq_jobs_one_active_per_chat` allows one live job for each chat as well.

Proof:

- `cd api && uv run pytest tests/test_tool_cards.py tests/test_<name>.py`. `test_tool_cards.py` fails when a tool that writes `rows` or `row_updates` has no `found_noun`. It also fails when a visible tool has no `chat.tools.<name>` label in both languages.
- `cd api && uv run mypy`.
- **Not enforced:** the contract doc entry in step 7. `test_every_route_the_contract_doc_names_exists` checks only routes, not tools.

## Add a vendor

1. Add the key to `Settings` in `api/app/core/config.py`, as in [Add a config field](#add-a-config-field). An empty key must make the vendor inert: the client exposes `is_configured` (see `ApolloClient`) and the handler returns `ok=False` when it is false.
2. Write the client under `api/app/people/` or `api/app/scraping/adapters/`. Return `Result` values (`app.core.errors`), never raise for a vendor error. The client reads its own key from `settings`. The registry does not hold the key, because each key has one reader.
3. Register the vendor in `api/app/billing/vendors.py`: `<VENDOR> = register(Vendor(name='<vendor>', usd_per_unit=...))`. The `Vendor` holds the vendor's facts in one place: its name (the `vendor_calls.vendor` value and the kill switch `vendor_disabled:<vendor>`), the USD price of one unit of its meter, the name of that meter on the log line (`meter`, `credits` by default), and its tariffs. Write the source of the price in a comment.
4. If the client returns a `Result`, route every paid call through `paid_call('<vendor>.<operation>', lambda: ..., **fields)` from `api/app/billing/paid_call.py`, and add one `Tariff` per operation to the vendor's `tariffs`. `api/tests/test_vendors.py` fails when a `paid_call` names an operation with no tariff. An `Err` never bills.
5. Otherwise, write the row yourself with `record_vendor_call(<VENDOR>, '<operation>', metered=..., units=..., success=...)`. `metered` is the count on the vendor's meter, and the row costs `metered * usd_per_unit`. The function takes the `Vendor`, so mypy fails when a caller passes a vendor name as a string. A web search provider is looked up by name in `VENDORS`, and `api/tests/test_vendors.py` fails when `app/scraping` returns a provider that is not registered.
6. Set `provider='<vendor>'` on each `ToolDef` that uses it. The loop then honors the kill switch `vendor_disabled:<vendor>`. If the vendor depends on the arguments, call `vendor_disabled_now()` from `api/app/admin/settings.py` in the handler.
7. Show the switch in the back office: add `vendor_disabled_<vendor>` to `DESCRIBED` in `web/app/admin/settings/page.tsx`, and a label under `admin.settings` in `web/messages/admin.en.json` and `web/messages/admin.it.json`. `api/tests/test_vendors.py` fails when a registered vendor or a mailbox provider (`MAILBOX_PROVIDERS`) is missing from `DESCRIBED`. An admin adds the row itself with `POST /v1/admin/settings`, which accepts only `vendor_disabled:<name>` and `channel_disabled:<name>`.
8. Provenance applies only to a source of contact data. Implement `ContactEnrichmentService` (`api/app/people/ports/contact_enrichment.py`) in `api/app/people/adapters/`, add it to the chain in `composite_contact_enrichment.py`, and build its `Provenance` with `contact_provenance` in `api/app/people/provenance.py`. **Not enforced:** the `extraction_method` label table in the docstring of `api/app/core/provenance.py`. Add a row there.
9. Add the key to `api/.env.example`. Production values live in the Infisical `zenbree` project, `prod` environment (see `README.md`). **Not enforced:** both.

Proof:

- `cd api && uv run pytest tests/test_vendors.py tests/test_<vendor>*.py` and `uv run mypy`. Vendor keys must never reach logs or URLs: copy the pattern of `api/tests/test_scraping_secrets.py`.
- Every test already runs with `paid_call`'s database writes redirected to a fake (`vendor_db` in `api/tests/conftest.py`), so no test hits a real vendor or database.

## Add an SSE event

1. Write its payload model in `api/app/agent/event_payloads.py` and add a field named after the event to `ChatEventPayloads`. That class is the one list of chat event names (`CHAT_EVENTS` in `api/app/agent/events.py` derives from it), and it reaches `/openapi.json` through `POST /v1/chat`. A key the loop leaves out of a frame is optional; the models forbid extra keys.
2. Emit it from the loop with `emit('<event>', {...})`. `Publisher` (`api/app/agent/events.py`) writes each frame to the Redis stream `job:{id}:events`, and the HTTP endpoint tails that stream. `api/app/agent/step_text.py` (`text_dropped`) is a small example. `api/tests/test_sse_events.py` fails when an `emit(...)` names an event that `CHAT_EVENTS` lacks or when no code emits a listed event. **Not enforced:** an event name held in a variable, or passed as a keyword, is not scanned.
3. Run `cd web && pnpm api:types`, and commit the regenerated `web/lib/api/schema.gen.ts`.
4. Add the name to `SSE_EVENT_TYPES` in `web/lib/chat/types.ts`, and its camelCase payload to `SSEPayloads` in `web/lib/chat/event-payloads.ts`. `web/lib/chat/stream-request.ts` drops any event not in `SSE_EVENT_TYPES`. `pnpm typecheck` fails at `parity` in `web/lib/chat/event-payloads.test.ts` when a payload key is in the model or in `SSEPayloads` only, and the error names the file to edit.
5. Write the handler in `web/lib/chat/sse-handlers/<event>.ts`, typed `SSEHandler<'<event>'>` (see `text-dropped.ts`). Data arrives in camelCase and typed as `SSEData<'<event>'>`, so a key that is not in `SSEPayloads` fails `pnpm typecheck`.
6. Add a row `<event>: handleX` to `SSE_HANDLERS` in `web/lib/chat/sse-handlers/index.ts`. The mapped type makes `pnpm typecheck` fail when a name has no row.
7. Add a row to the events table in `docs/contracts-m1.md`, section "Chat events, in the order they can arrive" (under "Server-sent events"): name, `id` column, and what the payload means.

Proof:

- `cd web && pnpm typecheck && pnpm api:types:check && pnpm test lib/chat`.
- `cd api && uv run pytest tests/test_sse_events.py tests/test_agent.py`. Every chat frame that a test makes the agent send is validated against its model (`chat_payloads_match_models` in `api/tests/conftest.py`), so an emit site whose keys differ from the model fails the test that sends it. With `TEST_DATABASE_URL` set (see `AGENTS.md`), the SQL tests send the frames of every kind.
- **Not enforced:** the contract doc row, the types of the values (the web check compares keys), and an emit site that no test reaches. A user-visible change also needs `/verify` evidence (see `.claude/skills/verify/features/chat.md`).

The other streams (list fill, CSV import, home, nav, notifications, onboarding) have no payload models. `api/tests/test_stream_events.py` compares their event names with the web file that handles them. Add a new event name on both sides, or the test names the file to edit. A stream missing from `STREAMS` there is not checked.

## Add a list column

A column is created from the keys a row carries (`ensure_columns` in `api/app/lists/rows.py`), so most cells need no schema change.

1. For a canonical key, add it to `CANONICAL` in `api/app/lists/rows.py`, in column order. Any other key becomes a column after the canonical ones, with its label made from the key.
2. For a vendor id or a vendor name that the user must not see, name it `*_id`, or add it to `VENDOR_KEYS`. `public_columns` is the one owner of what the list API and the exports show.
3. For a user-defined research column, the code is in `api/app/lists/ai_columns.py` (kind `ai`). A new column `kind` also needs a migration that changes the `list_columns_kind_check` constraint, and the `Literal` of `ListColumn.kind` in `api/app/lists/routes.py`.
4. In the web, `renderFor` in `web/lib/lists/columns.ts` maps a column to a `CellRender`. Add a render there and a branch in `web/components/lists/list-cells.tsx`. `COMPOSITES` in `web/lib/lists/column-model.ts` folds several keys into one column.
5. A CSV import target follows `CANONICAL`: `TARGETS` in `api/app/lists/csv_import.py` derives from it. `IMPORT_TARGETS` in `web/lib/lists/columns.ts` copies it by hand. **Not enforced** unless a web test compares them (none does today).
6. Update `docs/contracts-m1.md` (canonical keys under "Agent tools", and the list shapes under "Lists").

Proof:

- `cd api && uv run pytest tests/test_ai_columns.py` (or the test for the module you changed).
- `cd web && pnpm test lib/lists components/lists`.
- **Not enforced:** a plumbing key that does not end in `_id` and is not in `VENDOR_KEYS` shows to the user. Check the list API response by hand. The contract doc update is also not enforced.

## Add a config field

1. Add the field at the bottom of `Settings` in `api/app/core/config.py`, in a commented block. Give it a default that is safe when the variable is unset. An empty vendor key means the vendor is inert.
2. Add a matching entry, with a comment, to `api/.env.example`. **Not enforced.**
3. Compose passes the root `.env` to the API through `env_file`, so `compose.yml` needs no edit. Production values live in the Infisical `zenbree` project, `prod` environment. **Not enforced.**
4. A value the web reads goes in `web/.env.example`. A `NEXT_PUBLIC_*` value is a build argument in `compose.yml`, and changing it rebuilds the web image.

Proof: `cd api && uv run pytest tests/test_config.py` and `uv run mypy`. `test_config.py` shows how a test sets a variable with `monkeypatch.setenv` and builds `Settings(_env_file=None)`.

## Add a migration

1. Name the file `api/db/<UTC timestamp>_<name>.sql`. Get the timestamp with `date -u +%Y%m%d%H%M%S`. It must sort after the newest migration on `main`. If `main` gains a newer one, rename yours.
2. Write plain SQL. Do not use `IF NOT EXISTS` or `CREATE INDEX CONCURRENTLY`. The runner applies each file in one transaction.
3. Resolve the existing data that would fail the migration, such as duplicates before a unique index. **Not enforced:** prove it with a SQL test that runs the migration on that data (see `api/tests/test_linkedin_sql.py`).
4. Never edit or delete a migration that has run.
5. Give every new foreign key an index that leads with its first column. `api/tests/test_foreign_key_indexes_sql.py` fails without it and prints the `CREATE INDEX` line to add.
6. If a `System` scope writes the new table, grant that write to `zenbree_system` in the migration. A new table gives that role `SELECT` only, also a table labeled `zb:unscoped`. Grant `INSERT`, `UPDATE (column, ...)` or `DELETE`, with a comment that names the `SystemReason`, and add the write to `SYSTEM_WRITES` in `api/tests/test_system_grants_sql.py` (tenant tables only: a `zb:unscoped` table needs no entry).
7. For a Better Auth change, `cd web && pnpm auth:generate` writes `api/db/<timestamp>_better_auth.sql` (see [AGENTS.md](../AGENTS.md#commands)).
8. Apply it to a running local stack with `docker compose run --rm migrate`. See [migrations.md](migrations.md) for the runner, the deploy dump and rollback.

Proof:

- `./harness/init.sh api --fast` runs `harness/migration_order.py`, which names the file to rename when its timestamp is older than `main`'s newest.
- `eval "$(./harness/testdb.sh)"`, then `cd api && uv run pytest tests/test_<area>_sql.py`. The `*_sql.py` tests build a database from `api/db` and fail on a bad migration.
- The runner compares checksums in `schema_migrations` before it applies anything, so an edited migration fails the deploy, compose and SQL tests.
- A `System` path that writes without its grant fails its own SQL test with `InsufficientPrivilege`. `api/tests/test_system_grants_sql.py` fails on a grant that `SYSTEM_WRITES` does not list.

## Add a web settings page

1. Create `web/app/(app)/settings/<x>/page.tsx`, a server component (see `exclusions/page.tsx`). Put forms and server actions beside it in `actions.ts`, and UI in `web/components/<x>/`.
2. Add the data access in `web/lib/<x>/api.ts`. Start the file with `import 'server-only'`, call `httpClient` with `getToken()`, and parse the response with a `zod` schema (see `web/lib/exclusions/api.ts`).
3. Add the tab to `ALL_TABS` in `web/components/settings/settings-tabs.tsx` (`{ href: '/settings/<x>', key: '<x>' }`, with `orgAdminOnly: true` for admin-only pages). The phone menu reads the same list.
4. Add the tab label `settings.tabs.<key>` and the page strings to `web/messages/settings.en.json` and `settings.it.json`, as in [Add a message catalog](#add-a-message-catalog). First search both files for the namespace you plan to add (`grep -n '"<x>"' web/messages/settings.*.json`): `settings.preferences`, for one, already exists. If it exists, add your keys to it. A second block with the same name is a duplicate JSON key, and `pnpm lint` and `pnpm typecheck` both fail on it.
5. Update the expected menu in `web/components/settings/settings-shell.test.tsx`. That test lists every settings `href`.
6. If the page calls a new API route, add the route in `api/app/<x>/routes.py` and document it in `docs/contracts-m1.md`.
7. `web/lib/config/api-routes.ts` (`API_ROUTES`) is used by chat, lists and projects. **Not enforced:** the exclusions and memory pages use their own `BASE` constant. Follow the neighbouring code.

Proof:

- `cd web && pnpm lint && pnpm typecheck && pnpm knip && pnpm test components/settings`. `pnpm lint` enforces the Tailwind scale and the effect rules. `pnpm knip` fails on an unused export.
- A user-visible page also needs `/verify` evidence (see `.claude/skills/verify/features/`).

## Add a message catalog

Use a new catalog only for a new workstream. For a few strings, add keys to the existing file for that area (`web/messages/settings.*.json`, `account.*.json` and so on).

1. Create `web/messages/<name>.<language>.json` for every language in `LOCALE_NAMES` (`web/i18n/config.ts`), each with one top-level namespace. Write `en` first: it is the base language.
2. In `web/messages/index.ts`, import every file and spread each one into its language in `CATALOGS`.
3. Use the strings with `useTranslations('<namespace>')` or `getTranslations('<namespace>')`.
4. Format a count with a named number format and a plural (see the rules table in [AGENTS.md](../AGENTS.md#rules-and-what-enforces-them)).

Proof:

- `cd web && pnpm test messages i18n`. `messages.test.ts` fails when a language differs from `en` in keys or in how an argument is formatted, or names an argument that `en` lacks. `i18n/number-format.test.ts` and `i18n/count-plural.test.ts` check counts.
- `cd web && pnpm typecheck`. It generates `web/messages/*.d.json.ts` first and types every call against `en`. A catalog missing from the `en` spread fails here, and so does a language in `LOCALE_NAMES` with no entry in `CATALOGS`.
- **Not enforced:** two catalogs that use the same top-level namespace. The later spread silently replaces the earlier one.

## Change a row state the web shows

The web tests for list rows, the chat list panel, the tool chip and the admin transcript read JSON that API tests write: `web/lib/api/fixtures`. A test marked `web_fixture` writes one file through the helpers in `api/tests/web_fixtures.py`. They replace only the values that change between runs: the row id, `created_at` and the list id in a `result_preview`.

1. To show a new state, write it from the API test that reaches it, as `api/tests/test_enrich_email.py` does. Load the file in the web test, and do not copy its JSON by hand.
2. After a change to what the API stores or sends, run `eval "$(./harness/testdb.sh)"` and then `python3 harness/web_fixtures.py --update`. Commit the new files and fix the web tests that now fail.

Proof:

- `python3 harness/web_fixtures.py`, which the full `./harness/init.sh` runs for `api` and `web`. It fails when a committed file differs from what the API tests write now, is missing, or is no longer written. The message names the command that regenerates them.
- **Not enforced:** a web test that still builds a row by hand.

## Add an interface language

English is the base language: the default, the fallback and the text every other language is checked against. Each side declares its languages once. Add the language to the API and the database first: the web's types come from the API.

1. Database: a new migration in `api/db/` that widens `user_locale_check` on `"user".locale`, as `api/db/20261015094629_user_locale_languages.sql` does.
2. API: add the code to `Locale` in `api/app/core/locale.py`. `uv run pytest tests/test_locale.py tests/test_email_locale.py` then names what is missing (`mypy` does not: it accepts a table that lacks a key):
   - `api/app/notifications/emails/<code>.py`, a copy of `en.py` put into the language (its own `_pl` when the language counts 0 as singular, as `fr.py` does), listed in `LANGUAGES` in `api/app/notifications/emails/__init__.py`;
   - an entry in every module-level table typed `dict[Locale, …]`. `test_locale.py` finds them by their annotation: today `_NAMES` in `api/app/agent/language.py`, `_COPY` in `api/app/notifications/layout.py` and `api/app/notifications/push.py`, `BRIEF` and `FILTER_LABELS` in `api/app/onboarding/brief_words.py`, `REASONS` in `api/app/lists/csv_plan.py` and `DEFAULT_NAMES` in `api/app/projects/routes.py`. A new table of text gets that type, or the test does not see it.
   - the language's name in `LANGUAGES` of `api/tests/test_user_language.py`, which spells the agent's instruction out.
3. Web: add the code and the language's own name to `LOCALE_NAMES` in `web/i18n/config.ts`. `pnpm typecheck` then asks for:
   - every catalog `web/messages/<name>.<code>.json` (copy each `*.en.json`), imported and spread in `web/messages/index.ts`;
   - an entry in `COPY` and `FALLBACK` in `web/lib/email-copy.ts`;
   - an entry in `OG_LOCALES` in `web/app/layout.tsx`.
   Run `pnpm api:types` so `web/lib/api/schema.gen.ts` has the new code. Words that follow a count unchanged in the new language go in `AGREES_WITH_ANY` of `web/i18n/count-plural.test.ts`; check each one cannot hide an agreement error. A language with its own legal text adds it to `WRITTEN_IN` in `web/app/(public)/legal-language-note.tsx` and to the terms and privacy pages.
4. Landing: add the code and its path to `paths` in `landing/src/content.ts` (`Locale` and `locales` follow from it), its copy to `content` there, write `landing/src/i18n/<code>.ts` (typed by `Content`), add `landing/src/pages/<code>/index.astro`, the code to `i18n.locales` in `landing/astro.config.mjs` and its Open Graph name to `OG_LOCALES` in `landing/src/components/Seo.astro`. The phone menu's language row in `landing/src/components/Landing.astro` is `grid-cols-4`: a fifth language needs `grid-cols-5` there.
5. Link preview: add the language's copy to `docs/brand/og-card.html`, render `og-<code>.png` as its comment says and copy it into both `landing/public/` and `web/public/`. Nothing checks the image exists: a missing one is a broken preview on every shared link in that language.
6. Verify: `bin/browser locale <code>` in `.claude/skills/verify/bin/browser` accepts only the listed codes.

Proof:

- `cd api && uv run pytest tests/test_locale.py tests/test_email_locale.py tests/test_user_language.py`: every table has the language, every email renders in it, and the agent is told to write in it.
- `cd web && pnpm typecheck && pnpm test messages i18n`: the catalogs have the keys and the arguments of `en`.
- `/verify`: one path through the app in the new language, one email and a phone-width screenshot of Settings and of the landing menu.
- **Not enforced:** the quality of the translation, and that the terms and privacy pages (`web/app/(public)/terms`, `privacy`) exist in the language. They fall back to English, with the note.
