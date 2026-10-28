# Job timing schema upgrade

Compose initialization applies `api/db/schema_job_timing.sql` on an empty
database. For an existing database, apply the same idempotent migration after
backing up the database and before deploying code that reads the column:

```sh
docker compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < api/db/schema_job_timing.sql
```
