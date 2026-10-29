# Migrations

Every Zenbree database comes from the migrations in `api/db/`. This page describes how they run and how to roll back a deploy. To add a migration, follow the recipe in [recipes.md](recipes.md#add-a-migration).

## How migrations run

- A migration is `<UTC timestamp>_<name>.sql`, for example `20261006093000_projects_note.sql`. Get the timestamp with `date -u +%Y%m%d%H%M%S`.
- `app/core/migrate.py` applies pending migrations in name order.
- A new migration sorts after every migration on `main`. `harness/migration_order.py` checks this.
- Each migration runs once, in one transaction together with its row in `schema_migrations` (name, sha256 checksum, applied_at).
- A run first compares the checksum of every applied migration with its file. If one was edited or deleted, the run stops and applies nothing.
- An applied migration whose file is missing (an older image) only logs a warning. An edited one fails the run. An `api/db/*.sql` file not named `<14 digits>_<name>.sql` also fails the run.
- Later migrations are plain SQL. They need no `IF NOT EXISTS`, and they cannot use `CREATE INDEX CONCURRENTLY` because it cannot run in a transaction.
- A migration that adds a foreign key also adds an index that leads with its first column. `api/tests/test_foreign_key_indexes_sql.py` fails without it and prints the `CREATE INDEX` line to add.

## The baseline

The first migration, `20261005000000_baseline.sql`, is the schema from before migrations. It is idempotent. A database built before migrations (prod, old local volumes) has no `schema_migrations`, so it runs the baseline once on adoption and gains only what it was missing.

## Where they run

- The compose service `migrate` runs `python -m app.core.migrate` before api, worker, beat and web start. So `docker compose up` and the verify stack migrate on every start.
- To apply a new migration to a running local stack, run `docker compose run --rm migrate`.
- The prod deploy (`ops/deploy-production.sh`) runs the same service with the new image before `up`. A failed migration stops the deploy with the old containers still running.
- Before every migration run, the deploy writes a `pg_dump -Fc` to `/opt/zenbree/backups/<UTC>-pre-migrations.dump` and the cluster's roles to `<UTC>-pre-migrations.roles.sql` next to it (the newest 5 of each are kept). If either dump fails, the deploy stops. The nightly `ops/backup.sh` puts the same two files in R2.

## Roles and row-level security

`api/db/20261013195148_row_level_security.sql` adds two roles and a policy on every tenant table. The services connect as the superuser `zenbree`, and the app's pool switches each connection to `zenbree_app` (`api/app/core/db.py`), so the policies hold for every query of the app. Each checkout then enters its scope's role for one transaction. A migration runs as the login role, before any scope exists.

- `zenbree_app` is subject to row-level security. A transaction sees the rows of the organization that `zb.enter_member(org, user)` or `zb.enter_org(org)` set, and none without one.
- `zenbree_system` bypasses row-level security, for work that spans organizations. It reads every tenant table, but it writes only the tables and columns that a migration grants it by name. `api/db/20261014095833_system_role_grants.sql` took the wider grants away and gave the first set. `SYSTEM_WRITES` in `api/tests/test_system_grants_sql.py` lists each of these writes with the `SystemReason` that needs it.
- Both are `NOLOGIN`, and neither has a grant on `jwks`, `account`, `session`, `verification` or `schema_migrations`.
- Roles belong to the cluster, not to a database. `pg_dump` does not save them, so the backups save them in a separate `.roles.sql` file.
- Before a major upgrade of Postgres (17 today, `compose.yml`), run `api/tests/test_tenant_catalog_sql.py` on the new version. `zb.scope` and the catalog test compare each policy with the text that Postgres prints for it. If a new version prints a policy in another form, `zb.scope` recreates each policy and the test fails.

A new table needs no extra SQL for its policy. An event trigger turns row-level security on for every relation that a migration creates, and gives it the policy of its shape:

- A table with `organization_id` gets `organization_id = zb.org()`, and `DEFAULT zb.org()` unless a fill trigger takes the organization from the parent.
- A table labeled `zb:private`, `zb:personal` or `zb:personal-in-org` gets the policy of its label. Label it with `COMMENT ON TABLE` after the `CREATE TABLE`; the trigger reads the comment.
- A table labeled `zb:unscoped` has row-level security off.
- A label replaces the organization rule, so no label goes on organization data. `zb.scope` refuses a label on a table with `organization_id`, and the catalog test also fails it on a table that reaches an organization through a parent. The one exception is `zb:personal-in-org` on `notifications`. A materialized view with `organization_id` therefore has no allowed form: use a view, which runs with `security_invoker`.
- Every labeled table is listed in `LABELLED` in `api/tests/test_tenant_catalog_sql.py`. A new label fails that test until the same PR adds the table to the list.
- A table with none of these stays closed: `zenbree_app` reads no row of it.
- `zenbree_system` gets `SELECT` only, also on a table labeled `zb:unscoped`. If a `System` code path writes the new table, grant that write in the same migration: `INSERT`, `UPDATE (column, ...)` or `DELETE`, with a comment that names the reason. Add the same write to `SYSTEM_WRITES` in `api/tests/test_system_grants_sql.py`, which fails on a grant that the list does not name. `SYSTEM_WRITES` covers tenant tables only, so a grant on a `zb:unscoped` table needs no entry there.

A migration that adds or changes the columns of an existing table runs `SELECT zb.scope('<table>')` to get the policy again. `api/tests/test_tenant_catalog_sql.py` fails on a table whose policy does not match its shape, and prints that line.

## Stop the workers before `row_charges_hold`

`20261013183923_row_charges_hold.sql` names the hold of each per-row charge. It settles every open chat-turn hold by the rule the reaper used before it, so no turn may run while it applies. A worker of the previous image also writes charges without a hold: if it writes one between the migration and `up`, no settle bills that charge. For the deploy that first applies this migration, stop the workers on the VPS (`/opt/zenbree`) and let their running turns end first:

```bash
docker compose --profile prod stop -t 960 worker
```

Then deploy: run `ops/deploy-production.sh <sha>` by hand, or merge the PR whose push to `main` runs the CI `deploy` job. A turn ends within the worker's hard time limit (960 s, `run_job` in `app/worker.py`). Later deploys do not need this step.

## The thread pins of `sender_accounts`

`20261017191816_sender_accounts.sql` gives each conversation the account it started on (`outreach_campaign_targets.sender_id` and `sender_pinned_at`) and each campaign its accounts (`outreach_campaign_senders`). A conversation started when a claim took an email send. Its pin is the account of the newest started send that names one, else the client's link of its provider, else no account, which holds the conversation. `api/tests/pin_invariant.py` states the same rule as a query, and `api/tests/test_sender_accounts_migration_sql.py` runs it on the migrated data. The `outreach_target_pin_is_final` trigger keeps a pin final. Once `sender_pinned_at` is set, it never changes, and `sender_id` can only stay the same, become `NULL`, or move to the identified account of the same mailbox (see the next section). The `ON DELETE SET NULL (sender_id)` of an account delete writes that `NULL`. A conversation whose account is gone stays held and gets no other account. A campaign's accounts are the client's links of its `sender_pool` providers, plus the account of each live conversation, a target that is `active` or `paused` (a paused target resumes). That account is the pin, or the link of the conversation's `sender_provider` when it has no pin yet. The pin backfill disables the `home_touch` trigger on `outreach_campaign_targets` around its `UPDATE`, as `20261013175603_tenant_rows_outreach.sql` does. Without that, each pinned row sends a `home` notification. The migration changes the keys of `project_senders` and writes only the new columns and the new table, so tokens, sends and replies stay as they were.

## The legacy mailbox pins

A mailbox row from before #307 has no `provider_account_id` (a legacy row). It and the identified row of the same address in the organization are one mailbox, so a thread on one goes on from the other. `20261019093817_legacy_mailbox_pins.sql` lets a pin move from a legacy row to that identified row, with the same `sender_pinned_at`. `mailbox_twin` holds the rule, and every other move still fails. `move_legacy_pins` moves a client's conversations from a legacy row the client does not link to the client's identified row of that address, a live one first (#1069). Each connect or link of a mailbox runs it for that client and that account (`_linked_now` in `app/integrations/project_senders.py`), so linking another account resumes none of them. The migration ran it once for every client and every account. The account then joins each live campaign of a live moved conversation. `20261021165346_legacy_unlink_pins.sql` applies the same rule after unlink and member/user release when the identified account was already linked. Release locks affected projects, campaigns, then account rows in id order before deleting links and restoring pins and pools. The system role gains only `UPDATE (sender_id)` on targets and `INSERT` on pools for the existing account-delete and back-office reasons; sends remain unchanged. The migration ends with a backfill that runs `reconcile_legacy_pins` for every client, for the unlinks done since `20261019093817`; `home_touch` is off around that backfill, as in `sender_accounts`.

## The drop of the provider-name pools

`20261019081401_drop_provider_pools.sql` drops `outreach_campaigns.sender_pool` and `outreach_campaign_targets.sender_provider`. The pool and the pin above replaced them, and no code of the same image reads them. `outreach_campaign_sends.sender_provider` and `outreach_inbox_replies.sender_provider` stay, because they record what sent. `api/tests/test_drop_provider_pools_migration_sql.py` fills both columns, runs the migration, and checks that campaigns, targets, pools, sends, replies and project links do not change.

The migration is a rollback floor. The previous image fails on this schema in these places:

| Path of the previous image | What fails | What is written |
|---|---|---|
| Campaign list, detail, PATCH, activation (`_CAMPAIGN_SQL` selects `c.sender_pool`) | the request, with a 500 | nothing |
| New draft: `POST /v1/campaigns` and the `draft_campaign` tool (`sender_pool.preselect` writes `sender_pool`) | the request or the tool call | nothing: the campaign insert is in the same transaction |
| `PUT /v1/campaigns/{id}/senders` (`sender_pool.pick` writes `sender_pool`) | the request, with a 500 | nothing |
| First email of a conversation (`sender_slot.take` writes the target's `sender_provider`) | the claim, logged as `campaign.send.mailbox_failed` | nothing: the slot, the pin and the claim roll back before any mail goes out, and the next beat retries the send |

So the window between the migration and `up` loses no data and sends no mail twice, and the deploy needs no extra step. The previous web image also fails to read a campaign from the new API, which has no `sender_pool`, until `up` replaces it. To go back to the previous image, restore the pre-migrations dump of this deploy (see below), or roll forward.

## The freeze trigger of `target_personalization`

`20261019124500_target_personalization.sql` adds `outreach_campaign_targets.personalization` and `frozen_at`, with no backfill, and the trigger `outreach_target_activates_by_freeze`. The trigger refuses an UPDATE that moves a target from `draft` to `active` unless the transaction-local setting `zenbree.freezing` is `on`. Only `app.outreach.freeze` sets it. An INSERT is not checked.

The migration is a rollback floor. The previous image moves drafts to `active` with a plain UPDATE, so these paths fail on this schema:

| Path of the previous image | What fails | What is written |
|---|---|---|
| `POST /v1/campaigns/{id}/activate` | the request, with a 500 (`CheckViolation`) | nothing: the campaign stays `draft` |
| The approval of an automation's review run (`approval.approve`) | the request, with a 500 | nothing: the run and its drafts stay as they were |

Refill, watchlist and automation enrollments of the previous image insert people as `active`. Those work, and the people have no snapshot, so they render from their contact columns. So the window between the migration and `up` loses no data and sends nothing twice.

To go back to the previous image, drop the trigger first. The two columns are nullable and the previous image does not read them, so this loses nothing:

```
DROP TRIGGER outreach_target_activates_by_freeze ON outreach_campaign_targets;
```

To roll forward again after that, add a new migration that creates the trigger again. Restore the pre-migrations dump only if the drop is not enough.

## Roll back a deploy

The schema is forward-only, with no down migrations. A rollback is the previous image on the current schema:

```
SKIP_MIGRATE=1 ops/deploy-production.sh <sha>
```

`SKIP_MIGRATE=1` is needed for images from before #268, which lack the runner.

## When the previous image no longer fits the schema

This happens after a PR that names itself the rollback floor. Roll forward with a fix, or restore that deploy's pre-migrations dump. A restore loses everything written since the dump: signups, purchases (their Stripe webhooks then need replaying) and sends.

To restore:

1. On a new cluster, create the roles first, from the `.roles.sql` file of the same backup. The dump grants to them, so `pg_restore --exit-on-error` stops without them. On the cluster that made the dump the roles exist: `CREATE ROLE` fails, psql goes on, and `ALTER ROLE` sets their attributes again.

   ```
   grep -E '^(CREATE|ALTER) ROLE zenbree_(app|system)[ ;]' <roles.sql> | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres'
   ```

2. Stop the app containers so nothing holds a connection.

   ```
   docker compose --profile prod stop api worker worker-webhooks beat web
   ```

3. Recreate the database and load the dump. Do not use `pg_restore --clean`: it leaves the objects the bad migration added, and the next deploy fails on them.

   ```
   docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB" && pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error' < <dump>
   ```

4. Deploy the old image.

   ```
   SKIP_MIGRATE=1 ops/deploy-production.sh <previous sha>
   ```
