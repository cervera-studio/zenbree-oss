#!/usr/bin/env bash
# Runs the agent speed gate (api/bench/test_speed.py, docs/speed-benchmark.md) on the shared test database.
# Actions is off until go-live, so this is how an agent or a perf task gets the number. Usage: harness/bench.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${TEST_DATABASE_URL:-}" ]]; then eval "$(harness/testdb.sh)"; fi
source harness/heavy-lock.sh
bench() { (cd api && uv run pytest -q -s bench/test_speed.py); }
with_heavy_lock "bench.sh from $PWD" bench
