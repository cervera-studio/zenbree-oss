#!/usr/bin/env bash
# One disposable PostgreSQL for the SQL tests of every worktree: each test copies its own database from a
# migrated template, so parallel runs share it. Starts it when it is not running. Usage: eval "$(./harness/testdb.sh)"
set -euo pipefail
name=zenbree-harness-db
port="${HARNESS_DB_PORT:-55432}"
if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != true ]]; then
  docker rm -f "$name" >/dev/null 2>&1 || true
  # A named volume, emptied on each start: an anonymous one outlives a container that is removed
  # from outside, and six such volumes of 0.4-1.1 GB had piled up in the Docker VM.
  docker volume rm "$name" >/dev/null 2>&1 || true
  # The data is thrown away, so no write waits for the disk: every test copies and drops a database.
  docker run --detach --rm --name "$name" -e POSTGRES_PASSWORD=harness -e POSTGRES_DB=zenbree_test \
    -v "$name:/var/lib/postgresql/data" -p "127.0.0.1:$port:5432" postgres:17-alpine \
    postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off >/dev/null
fi
# Over TCP: during initdb the image runs a socket-only server that a socket check reports ready.
# init.sh calls this on every full run, so a container that dies while it starts must end the wait.
until docker exec "$name" pg_isready -q -h 127.0.0.1 -U postgres; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != true ]]; then
    echo "$name stopped while it started: docker logs $name" >&2
    exit 1
  fi
  sleep 1
done
echo "export TEST_DATABASE_URL=postgresql://postgres:harness@localhost:$port/zenbree_test"
