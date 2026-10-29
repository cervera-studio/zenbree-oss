#!/usr/bin/env bash
# Seeds the running dev database (compose service `postgres`) with dev/seed/lists.sql:
# the admin user dev@zenbree.local / zenbree-dev-1234 and the sample lists. Skips what is already there.
# Run it after `docker compose up` ends the migrations: the seed needs the tables.
# Runs psql inside the postgres container, so it needs no host psql, no host port and no extra image.
# Another compose project (a worktree's stack): COMPOSE_PROJECT_NAME=<name> dev/seed.sh
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
docker compose --project-directory "$repo" -f "$repo/compose.yml" exec -T postgres \
  sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' <"$repo/dev/seed/lists.sql"
