#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG=${1:?usage: deploy-production.sh <image-sha>}
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}$ ]] || { echo 'invalid image SHA' >&2; exit 2; }
: "${INFISICAL_PROJECT_ID:?Set INFISICAL_PROJECT_ID to your Infisical project ID}"

TOKEN_FILE=/home/deploy/.config/infisical/zenbree-prod-token
[[ -s "$TOKEN_FILE" ]] || { echo 'Infisical read token missing' >&2; exit 1; }
command -v infisical >/dev/null || { echo 'Infisical CLI missing; run ops/bootstrap.sh setup step' >&2; exit 1; }

umask 077
tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT
secrets_file=$tmp_dir/production.env
IFS= read -r INFISICAL_TOKEN < "$TOKEN_FILE"
[[ -n "$INFISICAL_TOKEN" ]] || { echo 'Infisical read token is empty' >&2; exit 1; }
export INFISICAL_TOKEN

infisical export --domain=https://eu.infisical.com/api --token="$INFISICAL_TOKEN" \
  --projectId="$INFISICAL_PROJECT_ID" --env=prod --path=/ \
  --format=dotenv --output-file="$secrets_file"
chmod 600 "$secrets_file"

cfg=$(ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod config)
for key in BETTER_AUTH_SECRET BETTER_AUTH_API_KEY BETTER_AUTH_URL OAUTH_TOKEN_ENCRYPTION_KEY RESEND_API_KEY EMAIL_FROM; do
  printf '%s\n' "$cfg" | grep -E "^ +$key: " | grep -qvE ': ""$' || {
    echo "deploy fermato: $key vuota o assente in Infisical prod" >&2
    exit 1
  }
done
# compose falls back to the dev password when the variable is unset
if printf '%s\n' "$cfg" | grep -qE '^ +POSTGRES_PASSWORD: "?postgres"?$'; then
  echo 'deploy fermato: POSTGRES_PASSWORD assente o uguale al default in Infisical prod' >&2
  exit 1
fi
unset cfg INFISICAL_TOKEN

IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod pull -q
# Migrations before the new containers (#268): the new image applies the pending api/db files, each in one
# transaction. A failure (or an edited migration) stops the deploy here, with the old containers still up.
# Schema is forward-only: a rollback runs the previous image on the current schema. SKIP_MIGRATE=1 skips the
# step (and turns the compose `migrate` service into a no-op) for images that predate #268 or lack the runner.
IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod up -d --wait postgres
if [[ "${SKIP_MIGRATE:-}" == 1 ]]; then
  export MIGRATE_COMMAND=true
  echo 'SKIP_MIGRATE=1: migrazioni saltate, schema invariato'
else
  export MIGRATE_COMMAND= # empty beats a stray value from the shell or the env file: compose's default runs
  # Full dump before every migration run: the schema is forward-only, so this file is the only way back from a
  # migration that applied but broke data. Restore steps: AGENTS.md, "Database and schema changes".
  # ponytail: a dump even when nothing is pending (the runner has no dry run); fine at this DB size.
  mkdir -p /opt/zenbree/backups
  # Prune to the newest 4 first, so a nearly full disk frees room instead of failing every deploy; 5 after the dump.
  # The nightly ops/backup.sh to R2 is the long-term copy.
  ls -1t /opt/zenbree/backups/*-pre-migrations.dump 2>/dev/null | tail -n +5 | xargs -r rm -f -- || true
  ls -1t /opt/zenbree/backups/*-pre-migrations.roles.sql 2>/dev/null | tail -n +5 | xargs -r rm -f -- || true
  dump=/opt/zenbree/backups/$(date -u +%Y%m%dT%H%M%SZ)-pre-migrations.dump
  roles=${dump%.dump}.roles.sql
  IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod \
    exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$dump" && [[ -s "$dump" ]] ||
    { rm -f -- "$dump"; echo "deploy fermato: backup $dump non riuscito" >&2; exit 1; }
  # Roles belong to the cluster, so pg_dump leaves out zenbree_app and zenbree_system, which the dump's grants name.
  # A restore on a new cluster creates them from this file first (docs/migrations.md). NOLOGIN: no passwords.
  IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod \
    exec -T postgres sh -c 'pg_dumpall --roles-only --no-role-passwords -U "$POSTGRES_USER"' > "$roles" && [[ -s "$roles" ]] ||
    { rm -f -- "$dump" "$roles"; echo "deploy fermato: backup dei ruoli $roles non riuscito" >&2; exit 1; }
  echo "backup prima delle migrazioni: $dump, $roles"
  IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod \
    run --rm -T --no-deps migrate || { echo 'deploy fermato: migrazioni non applicate' >&2; exit 1; }
fi
# compose does not recreate a container when only the content of an inline `configs` entry changed, so a new
# Vector pipeline (compose.yml `configs.vector`) would never load. Recreate it first: it reads only the logs
# written after it starts, so the new containers' startup lines below are not lost.
# ponytail: recreated on every deploy, a ~2s gap in the old containers' lines; hash the config if that matters.
# A pipeline that does not load would restart-loop and drop every line unnoticed: validate it first, and on
# failure keep the running Vector on its old pipeline rather than stop a deploy whose migrations already ran.
if validation=$(IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" \
  --profile prod run --rm -T --no-deps --entrypoint vector logs validate --no-environment /etc/vector/vector.yaml 2>&1); then
  IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod \
    up -d --no-deps --force-recreate logs 2>&1 | tail -1
else
  # Vector quotes the value it rejects: if that value is a secret, it would reach the deploy's terminal or CI log.
  while IFS= read -r line; do
    value=${line#*=}; value=${value#\'}; value=${value%\'}
    if (( ${#value} >= 8 )); then validation=${validation//"$value"/<redacted>}; fi
  done < "$secrets_file"
  printf '%s\n' "$validation" >&2
  echo 'ATTENZIONE: la nuova pipeline di Vector non è valida; logs resta sulla vecchia (compose.yml configs.vector)' >&2
fi
IMAGE_TAG=$IMAGE_TAG ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" --profile prod up -d --no-build --remove-orphans 2>&1 | tail -3
healthy=0
for ((attempt = 1; attempt <= 12; attempt++)); do
  if curl -fsS --max-time 5 localhost:8000/health >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 5
done
[[ "$healthy" == 1 ]] || { echo 'deploy failed: API health check timed out' >&2; exit 1; }
rm -f /opt/zenbree/.env
docker image prune -af --filter until=168h >/dev/null
echo " deployed ${IMAGE_TAG:0:7}"
