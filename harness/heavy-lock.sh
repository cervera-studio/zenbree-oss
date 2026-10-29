# Sourced by harness/init.sh and the verify skill's bin/stack. A full init.sh run takes this lock by default,
# so two full suites never overlap: parallel runs pushed vitest past its 5 s timeout (#343). A verify image
# build takes it too, so a build and a full run never share the CPUs. bin/stack also uses this function for its
# own locks, with HEAVY_LOCK set to their paths. The holder's pid frees the lock when it dies.
HEAVY_LOCK="${ZENBREE_HEAVY_LOCK:-$HOME/.cache/zenbree/heavy.lock}"

with_heavy_lock() { # $1 what waiters see, $2... the command
  local what="$1" pid i=0 start=$SECONDS
  shift
  mkdir -p "$(dirname "$HEAVY_LOCK")"
  until mkdir "$HEAVY_LOCK" 2>/dev/null; do
    pid="$(cat "$HEAVY_LOCK/pid" 2>/dev/null || true)"
    # A live holder keeps the lock however long it runs. Age frees only a lock whose holder died before writing its pid.
    if { [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; } ||
      { [ -z "$pid" ] && [ -n "$(find "$HEAVY_LOCK" -maxdepth 0 -mmin +1)" ]; }; then
      rm -rf "$HEAVY_LOCK"
      continue
    fi
    [ $((i++ % 6)) = 0 ] && echo "waiting for the heavy-job lock: $(cat "$HEAVY_LOCK/what" 2>/dev/null || echo 'another job')" >&2
    sleep 10
  done
  # The wait counts in the gate's time but is not its work: name it apart.
  if ((i)); then echo "waited $((SECONDS - start)) s for the heavy-job lock" >&2; fi
  echo $$ >"$HEAVY_LOCK/pid"
  echo "$what" >"$HEAVY_LOCK/what"
  trap release_heavy_lock EXIT
  "$@"
  release_heavy_lock
  trap - EXIT
}

release_heavy_lock() { # only our own: a waiter may have taken it over after our pid looked dead
  if [ "$(cat "$HEAVY_LOCK/pid" 2>/dev/null)" = "$$" ]; then rm -rf "$HEAVY_LOCK"; fi
}
