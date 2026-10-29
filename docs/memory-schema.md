# User memory schema

`user_memory_doc` stores one personal memory document for each Zenbree user.
The user ID is the primary key and references Better Auth's `user` table with
cascade deletion. New rows default to an empty body and an unlocked state. The
database enforces a maximum body length of 8,000 characters.

The schema file is loaded automatically for new database volumes. To upgrade
an existing database, apply it from the repository root:

```sh
docker compose exec -T postgres psql -U zenbree -d zenbree < api/db/schema_memory.sql
```

The SQL is safe to replay. It creates the table if absent and does not rewrite
existing rows. There is no legacy memory backfill.

Application body writes upsert only `body` and `updated_at`, preserving the
stored lock. Lock changes upsert only `locked` and `updated_at`; creating a row
through that path keeps the default empty body.
