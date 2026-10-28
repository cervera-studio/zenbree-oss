# Sourced by bin/stack. Agents started keyless stacks while a local .env held an LLM key, then reported chat as
# "needs an LLM key" (#530, #657). The doctor's warning did not stop it, so a keyless stack needs VERIFY_LLM=off.

llm_read() { # $1 key, $2 dotenv file: a pipeline that prints the key's value; the guard and its hint share it
  local q="'" file
  file="$(printf %q "$2")"
  echo "grep $q^$1=$q $file | tail -1 | sed -e ${q}s/^$1=//$q -e ${q}s/[[:space:]]#.*//$q -e \"s/[\\\"$q[:space:]]//g\""
}

local_llm_key() { # "<key> <file>" for an LLM key in this checkout's .env or the main checkout's
  local main f k
  main="$(cd "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
  for f in "$REPO/.env" "$main/.env"; do
    [ -f "$f" ] || continue
    for k in DEEPSEEK_API_KEY ANTHROPIC_API_KEY; do
      [ -n "$(eval "$(llm_read "$k" "$f")")" ] && { echo "$k $f"; return 0; }
    done
  done
  return 1
}

llm_guard() { # $1 mode: on, or off for a keyless stack on purpose
  local found key file
  if [ "$1" = off ]; then
    [ -n "${DEEPSEEK_API_KEY:-}${ANTHROPIC_API_KEY:-}" ] && say "VERIFY_LLM=off: the exported LLM key is not passed on"
    unset DEEPSEEK_API_KEY ANTHROPIC_API_KEY
    return 0
  fi
  [ -n "${DEEPSEEK_API_KEY:-}${ANTHROPIC_API_KEY:-}" ] && return 0
  found="$(local_llm_key)" || return 0
  read -r key file <<<"$found"
  die "no LLM key is exported, but $file has $key, so chat turns would fail. Export it without printing it, in the
same command as bin/stack (an agent's shell drops exports between calls):
  export $key=\"\$($(llm_read "$key" "$file"))\"
For a keyless stack on purpose (a recipe that proves a failed turn): VERIFY_LLM=off bin/stack up"
}

recorded_llm() { (sed -n 's/^VERIFY_LLM=//p' "$ENV_FILE" 2>/dev/null || true) | tail -1; }

compose_creates() { # $@ compose args: true when the subcommand creates containers, which read the key from this shell
  while [ $# -gt 0 ]; do
    case "$1" in
      -f | --file | -p | --project-name | --profile | --env-file | --project-directory | --ansi | --progress | --parallel)
        [ $# -ge 2 ] || return 1
        shift 2 ;;
      -*) shift ;;
      up | run | create | scale | watch) return 0 ;;
      *) return 1 ;;
    esac
  done
  return 1
}
