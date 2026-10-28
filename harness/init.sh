#!/usr/bin/env bash
# Shared local/CI checks. Requires uv, Node 24+, pnpm and a disposable test DB.
set -euo pipefail
cd "$(dirname "$0")/.."

scope=all
fast=false
for arg in "$@"; do
  case "$arg" in
    api|web|landing|all) scope="$arg" ;;
    --fast) fast=true ;;
    --help) echo 'Usage: ./harness/init.sh [all|api|web|landing] [--fast]'; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# A fast run holds no lock, so it yields the CPUs to a full run. Eight CPU hogs at default QoS took the gate's
# pytest step from 83 s to 155 s, at utility QoS to 87 s. Background QoS protects as well but keeps the run on the
# efficiency cores: a cold tsc took 40 s against 7 s. Linux has no taskpolicy and runs as before.
if [[ "$fast" == true && -z "${ZENBREE_FAST_CLAMPED:-}" ]] && command -v taskpolicy >/dev/null; then
  ZENBREE_FAST_CLAMPED=1 exec taskpolicy -c utility bash "$PWD/harness/init.sh" "$@"
fi

runs() { [[ "$scope" == all || "$scope" == "$1" ]]; }

# Each step prints its time, so a slow gate names its slow step.
timed() {
  local start=$SECONDS status=0 failed=''
  "$@" || status=$?
  if ((status)); then failed=' (failed)'; fi
  echo "time: $((SECONDS - start)) s for ${PWD##*/}: $*$failed"
  return "$status"
}

checks() {
  timed python3 harness/size_check.py "$scope"
  # CI runs these in its own harness job; locally only `all` runs them, so the local gate covers them too.
  if [[ "$scope" == all ]]; then
    (cd harness && timed python3 run_tests.py)
  fi
  if runs api; then
    (
      cd api
      timed uv sync --locked
      timed uv run ruff format --check .
      timed uv run ruff check .
      timed uv run mypy
      # Four workers: the SQL tests each create and drop a database, and with more workers those statements queue inside Postgres.
      if [[ "$fast" == false ]]; then timed uv run pytest -q -n 4; fi
    )
  fi
  # The web tests read rows the API tests write, so a hand edit on either side fails here.
  if [[ "$fast" == false ]] && (runs api || runs web); then timed python3 harness/web_fixtures.py; fi
  if runs web; then
    (
      cd web
      timed pnpm install --frozen-lockfile
      timed pnpm lint
      timed pnpm typecheck
      timed pnpm knip
      # Fails when lib/api/schema.gen.ts no longer matches the API models; the message names the fix.
      timed pnpm api:types:check
      if [[ "$fast" == false ]]; then
        timed pnpm test
        timed pnpm build
      fi
    )
  fi
  if runs landing; then
    (
      cd landing
      timed pnpm install --frozen-lockfile
      timed pnpm lint
      timed pnpm check
      if [[ "$fast" == false ]]; then
        timed pnpm test
        timed pnpm build
      fi
    )
  fi
}

# Before the pass cache: the answer depends on main, which can gain a newer migration after this tree passed.
if runs api; then python3 harness/migration_order.py; fi

# A full pass is kept for the exact tree it ran on, so a rerun on that tree runs nothing. Each entry lives until
# the PR of its branch merges. Agents reran 22% of full runs on an unchanged tree (#444).
cache_root="${ZENBREE_GATE_CACHE:-$HOME/.cache/zenbree/gate}"

cache_entry() { # prints nothing when the tree has no stable key: no branch, main, or uncommitted changes
  local branch
  branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || return 0
  if [[ "$branch" == main || -n "$(git status --porcelain)" ]]; then return 0; fi
  echo "$cache_root/${branch//\//%}/$(git rev-parse 'HEAD^{tree}')-$scope"
}

clear_merged() { # fails when GitHub cannot list the merged PRs: then no entry is trusted
  local merged branch
  merged="$(gh pr list --state merged --limit 300 --json headRefName --jq '.[].headRefName')" || return 1
  while IFS= read -r branch; do
    if [[ -n "$branch" ]]; then rm -rf "$cache_root/${branch//\//%}"; fi
  done <<<"$merged"
}

if [[ "$fast" == true ]]; then
  checks
  echo "PASS ($scope, fast): tests and build omitted; this is not full verification."
else
  entry="$(cache_entry)"
  if [[ -n "$entry" ]] && clear_merged && [[ -f "$entry" ]]; then
    echo "PASS ($scope, cached): $(cat "$entry") passed every check on this same tree; only checks that depend on main ran now."
    # A rebase that keeps the tree, as in a stack after the PR below merged, moves the pass to this commit. The
    # merge hook then sees the PRs below as contained in it (.claude/hooks/pr-merge-gate.py).
    read -r _ passed_at <"$entry"
    echo "$(git rev-parse --short HEAD) $passed_at" >"$entry"
    exit 0
  fi
  # SQL tests must not silently skip. Agents forgot the variable on full runs, so the gate starts the shared
  # local database itself; CI sets its own.
  if (runs api || runs web) && [[ -z "${TEST_DATABASE_URL:-}" ]]; then
    testdb="$(harness/testdb.sh)" || {
      echo 'harness/testdb.sh could not start the shared test database: start Docker, or set TEST_DATABASE_URL.' >&2
      exit 1
    }
    eval "$testdb"
  fi
  # A full run takes the machine-wide lock, so two full runs never overlap. Verify builds do not use it.
  source harness/heavy-lock.sh
  with_heavy_lock "init.sh $scope from $PWD" checks
  if [[ -n "$entry" && "$entry" == "$(cache_entry)" ]]; then
    mkdir -p "$(dirname "$entry")"
    echo "$(git rev-parse --short HEAD) at $(date -u +%Y-%m-%dT%H:%MZ)" >"$entry"
  fi
  echo "PASS ($scope): all requested checks completed in $SECONDS s; inspect test output for skips."
fi
