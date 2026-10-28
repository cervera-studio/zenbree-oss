#!/usr/bin/env bash
# Model-checks the credit-hold lifecycle (CreditHolds.tla) with TLC in Docker. See .claude/skills/credit-holds/SKILL.md.
#
#   check.sh        lists the covered files changed since the sync commit and the holder files missing from the
#                   covered list, checks CreditHolds.cfg, and checks that each open finding still fails by its
#                   recorded trace
#   check.sh --all  also runs the mutants, once CreditHolds.cfg passes: each must break the invariant that guards it
#
# Exit 0 when every run ends as expected, 1 when one does not, 2 when the check cannot run, and 3 when every run ends
# as expected but covered code changed since the sync commit: copy the change into the model, then move the sync.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
tla_version=v1.7.4
tla_sha256=936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88
image=eclipse-temurin@sha256:227118764cccd3c3294617061aaa93a08309c63a395eefa83e6b35032992ea99  # 21-jre
jar_dir="${XDG_CACHE_HOME:-$HOME/.cache}/zenbree-tla/$tla_version"

# The commit at which the model was last confirmed to describe the covered code. Move it only after that check.
sync=initial
covered=(
  api/app/billing/credits.py api/app/billing/tab.py api/app/billing/row_charges.py api/app/billing/bounce_refund.py
  api/app/agent/turn_credits.py api/app/agent/loop.py api/app/lists/delivery.py api/app/lists/rows.py
  api/app/lists/row_phone.py api/app/lists/ai_columns.py api/app/lists/bulk_routes.py
  api/app/scheduling/run_credits.py api/app/scheduling/runner.py api/app/automations/run.py api/app/automations/runner.py
  api/app/automations/steps.py api/app/automations/instantly_step.py api/app/automations/service.py
  api/app/watchlists/runner.py api/app/watchlists/enroll.py api/app/watchlists/service.py
  api/app/outreach/bounces.py api/app/outreach/drafts.py api/app/worker.py api/app/lists/take_back.py
  api/app/lists/lookups.py api/app/billing/plan_period.py
)
# A migration in api/db counts as covered when it names one of these functions or tables. Most migrations do not.
covered_sql='reserve_project_credits|reserve_credits|grow_reservation|settle_hold|settle_reservation_with_slack|release_reservation|reap_stale_reservations|project_month_spend|project_cap_left|increment_credits|deduct_credits|pay_plan_expiry_owed|row_charges|credit_transactions'
# A file that opens, closes or finalizes a hold. One that is not in covered fails the drift check.
holder_pattern='Tab\.(open|stored)|close_stored|row_charges\.finalize|[^_]finalize\(conn|finalize_hold\(|run_credits\.(hold|lock|deliver|close)\('

# Open findings: the invariant the current code breaks, and the actions of TLC's shortest trace. Remove a line once
# the code keeps the invariant, and list the invariant in CreditHolds.cfg.
findings=(
  "BounceRefundHonored RunOpen,RunRecord,BounceRead,BounceApply"
)

# Mutants: id, the invariant that must catch it, the code it stands for, and a perl substitution applied only to
# the CreditHolds.tla line tagged [id]. Change the substitution when you change the text of a tagged line.
mutants=(
  "M1|WalletNeverNegative|grow_reservation without credits_remaining >= p_amount|s/.*//"
  "M2|CapRespected|grow_reservation without the hard cap check|s/.*//"
  "M3|KeptIsWhatCharged|a delivery without Tab.lock: a charge lands on a closed hold|s/IF ~Reserved\(\"turn\"\)/IF FALSE/"
  "M3|ChargeOnLiveHold|a delivery without Tab.lock: a charge lands on a closed hold|s/IF ~Reserved\(\"turn\"\)/IF FALSE/"
  "M4|KeptIsWhatCharged|settle_hold sums refunded charges too (drops state <> 'refunded')|s/cs\[i\]\.state # \"refunded\"/TRUE/"
  "M5|BounceRefundOnce|refund_bounced without h.status IS DISTINCT FROM 'reserved'|s/~Reserved\(charges\[i\]\.hold\)/TRUE/"
  "M6|ChargesWithinHold|AgentLoop._preflight runs a paid call without credits.cover|s/SeqSum\(turn\.calls\) \+ q <= turn\.known - turn\.spent/TRUE/"
  "M7|CreditsConserved|settle_reservation_with_slack refunds the whole hold, forgetting the spend|s/ - spent//"
  "M8|NoPendingOnClosedHold|settle_hold leaves held charges held|s/FinalizeOf\(cs, h\)/cs/"
  "M9|BounceRefundOnce|refund_bounced without the FOR UPDATE re-check of state = 'charged'|s/i # 0 \/\\\\ charges\[i\]\.state = \"charged\"/i # 0/"
  "M10|TurnEndRefundsUnfinished|refund_unfinished refunds every held charge, finished row or not|s/IF finished\[cs\[i\]\.row\]/IF FALSE/"
  "M11|CapRespected|reserve_project_credits without the hard cap check|s/ProjectSpend \+ a <= Cap/TRUE/"
  "M12|WalletNeverNegative|reserve_credits without credits_remaining >= p_amount|s/wallet >= a/TRUE/"
  "M13|ChargeOnLiveHold|a stored-hold record without Tab.lock: a charge lands on a closed hold|s/IF ~Reserved\(\"run\"\)/IF FALSE/"
)

fetch_jar() {
  [[ -f "$jar_dir/tla2tools.jar" ]] && echo "$tla_sha256  $jar_dir/tla2tools.jar" | shasum -a 256 -c --status && return
  mkdir -p "$jar_dir"
  curl -fsSL -o "$jar_dir/tla2tools.jar.part" \
    "https://github.com/tlaplus/tlaplus/releases/download/$tla_version/tla2tools.jar" ||
    { echo "check.sh cannot download tla2tools.jar $tla_version" >&2; exit 2; }
  echo "$tla_sha256  $jar_dir/tla2tools.jar.part" | shasum -a 256 -c --status ||
    { echo "tla2tools.jar checksum mismatch" >&2; rm -f "$jar_dir/tla2tools.jar.part"; exit 2; }
  mv "$jar_dir/tla2tools.jar.part" "$jar_dir/tla2tools.jar"
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# tlc <spec dir> <cfg file name> <output file> [workers]: TLC's exit code. -deadlock: holders end, so terminal states
# are fine. TLC keeps its state files in the container, which --rm deletes.
tlc() {
  local status=0
  docker run --rm --memory 3g -v "$1":/spec:ro -v "$jar_dir":/tla:ro "$image" \
    java -XX:+UseParallelGC -Xmx2g -cp /tla/tla2tools.jar tlc2.TLC -workers "${4:-2}" -deadlock \
    -metadir /tmp/tlc -config "/spec/$2" /spec/CreditHolds.tla >"$3" 2>&1 || status=$?
  return "$status"
}

# variant <dir> <invariant>: a copy of the model in <dir>, with a config that checks only <invariant>.
variant() {
  mkdir -p "$1"
  [[ -f "$1/CreditHolds.tla" ]] || cp "$here/CreditHolds.tla" "$1/"
  sed -n '/^INVARIANTS/q;p' "$here/CreditHolds.cfg" >"$1/variant.cfg"
  printf 'INVARIANTS\n    %s\n' "$2" >>"$1/variant.cfg"
}

violated() { grep -q "^Error: Invariant $1 is violated" "$2"; }

# The action of each step of a TLC trace, after the initial state.
trace_actions() { { grep -oE '^State [0-9]+: <[A-Za-z]+' "$1" || true; } | sed 's/.*<//' | tail -n +2 | paste -sd, -; }

failed=0
fail() {
  failed=1
  echo "FAIL  $1"
  if violated '.*' "$2"; then
    sed -n '/^Error:/,$p' "$2"  # the whole trace: its last state is the one that breaks the invariant
  else
    cat "$2"  # not a violation: a parse error (its line and column come before Error:) or a Docker failure
  fi
}

drift=0
check_drift() {
  if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    echo "SKIP  drift: $repo is not a git checkout"
    return
  fi
  grep -Fq "Sync commit: \`$sync\`" "$here/README.md" ||
    { failed=1; echo "FAIL  the README's sync commit is not $sync: set both to the same commit"; }
  if [[ "$sync" == initial ]]; then
    [[ "$(git -C "$repo" rev-parse --is-shallow-repository)" == false ]] ||
      { echo "check.sh: fetch the full history before resolving the initial commit" >&2; exit 2; }
    sync="$(git -C "$repo" rev-list --max-parents=0 HEAD)"
  fi
  git -C "$repo" cat-file -e "$sync^{commit}" 2>/dev/null ||
    { echo "check.sh: the sync commit $sync is not in this clone (fetch it, or unshallow)" >&2; exit 2; }
  local changed missing file
  changed="$({
    git -C "$repo" diff --name-only "$sync" -- "${covered[@]}"
    git -C "$repo" ls-files --others --exclude-standard -- "${covered[@]}"
    {
      git -C "$repo" diff --name-only --diff-filter=d "$sync" -- api/db
      git -C "$repo" ls-files --others --exclude-standard -- api/db
    } | while read -r file; do if grep -qE "$covered_sql" "$repo/$file"; then echo "$file"; fi; done
  } | sort -u)"
  if [[ -n "$changed" ]]; then
    drift=1
    echo "DRIFT covered files changed since $sync. Copy each change into CreditHolds.tla, then move sync:"
    sed 's/^/      /' <<<"$changed"
  fi
  missing=""
  for file in $(git -C "$repo" grep -l --untracked -E "$holder_pattern" -- api/app); do
    [[ " ${covered[*]} " == *" $file "* ]] || missing+="$file"$'\n'
  done
  if [[ -n "$missing" ]]; then
    failed=1
    echo "FAIL  holder files not in covered: model them (see the action table) and add them to covered:"
    sed '/^$/d; s/^/      /' <<<"$missing"
  fi
}

check_model() {
  local out="$work/model.out"
  if tlc "$here" CreditHolds.cfg "$out"; then
    echo "PASS  CreditHolds.cfg: $(grep -o '[0-9]* distinct states found, 0 states left' "$out" | cut -d, -f1)"
  elif violated '.*' "$out"; then
    fail "CreditHolds.cfg: a property broke (a real bug, or the model no longer matches the code)" "$out"
  else
    fail "CreditHolds.cfg: TLC did not check the model" "$out"
  fi
  local line inv want got
  for line in ${findings[@]+"${findings[@]}"}; do
    read -r inv want <<<"$line"
    variant "$work/finding-$inv" "$inv"
    out="$work/finding-$inv.out"
    # One worker: with two, TLC can report another shortest trace from run to run.
    if tlc "$work/finding-$inv" variant.cfg "$out" 1; then
      echo "FAIL  $inv now holds. The finding is fixed: list $inv in CreditHolds.cfg and drop it from findings"
      failed=1
    elif ! violated "$inv" "$out"; then
      fail "finding $inv: TLC failed for another reason" "$out"
    elif got="$(trace_actions "$out")" && [[ "$got" != "$want" ]]; then
      fail "finding $inv: the shortest trace is now $got, not $want. Read it: a new path may break it" "$out"
    else
      echo "KNOWN $inv is still violated by $want (open finding)"
    fi
  done
}

check_mutants() {
  local line id inv what sub dir out changed
  for line in "${mutants[@]}"; do
    IFS='|' read -r id inv what sub <<<"$line"
    dir="$work/$id-$inv"
    mkdir -p "$dir"
    perl -pe "$sub if /\\[$id\\]/" "$here/CreditHolds.tla" >"$dir/CreditHolds.tla"
    changed="$(diff "$here/CreditHolds.tla" "$dir/CreditHolds.tla" | grep -c '^>' || true)"
    if [[ "$changed" != 1 ]]; then
      echo "FAIL  $id: the substitution changed $changed lines, not 1: update the substitution of $id in check.sh, or move its [$id] tag"
      failed=1
      continue
    fi
    variant "$dir" "$inv"
    out="$dir/tlc.out"
    if tlc "$dir" variant.cfg "$out"; then
      echo "FAIL  $id ($what): $inv still holds, so it no longer guards that code"
      failed=1
    elif violated "$inv" "$out"; then
      echo "PASS  $id ($what): $inv caught it"
    else
      fail "$id: TLC failed for another reason" "$out"
    fi
  done
}

case "${1:-}" in
  "" | --all) ;;
  *) echo "usage: $0 [--all]" >&2; exit 2 ;;
esac
check_drift
docker info >/dev/null 2>&1 || { echo "check.sh needs a running Docker daemon (OrbStack)" >&2; exit 2; }
fetch_jar
run_tlc() {
  # with_heavy_lock replaces the EXIT trap: keep removing $work too.
  trap 'rm -rf "$work"; release_heavy_lock' EXIT
  check_model
  if [[ "${1:-}" == --all ]]; then
    if [[ "$failed" == 0 ]]; then
      check_mutants
    else
      echo "SKIP  mutants: CreditHolds.cfg must pass first, or a mutant reads as caught by a property that already broke"
    fi
  fi
}
# shellcheck source=../../../harness/heavy-lock.sh
source "$repo/harness/heavy-lock.sh"
# TLC takes 2 CPUs for minutes: one heavy job at a time on the machine, like init.sh.
with_heavy_lock "credit_holds/check.sh from $PWD" run_tlc "${1:-}"
trap 'rm -rf "$work"' EXIT
if [[ "$failed" == 0 && "$drift" == 1 ]]; then
  echo "DRIFT the model passed, but it still describes the code at $sync. Exit 3 until sync moves"
  exit 3
fi
exit "$failed"
