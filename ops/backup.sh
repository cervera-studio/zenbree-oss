#!/usr/bin/env bash
# Backup notturno: pg_dump temporaneo → R2 → heartbeat BetterStack. Cron sulla VPS:
#   15 3 * * * cd /opt/zenbree && ops/backup.sh >> /var/log/zenbree-backup.log 2>&1
# Richiede rclone con un remote "r2" (provider Cloudflare).
# BACKUP_BUCKET e BACKUP_HEARTBEAT_URL arrivano da Infisical prod come in deploy-production.sh:
# il deploy cancella /opt/zenbree/.env (#425).
# ponytail: niente rotazione qui, la fa una lifecycle rule sul bucket R2 (es. 30 giorni).
set -euo pipefail
: "${INFISICAL_PROJECT_ID:?Set INFISICAL_PROJECT_ID to your Infisical project ID}"

TOKEN_FILE=${ZENBREE_INFISICAL_TOKEN_FILE:-/home/deploy/.config/infisical/zenbree-prod-token}
[[ -s "$TOKEN_FILE" ]] || { echo 'Infisical read token missing' >&2; exit 1; }
command -v infisical >/dev/null || { echo 'Infisical CLI missing; run ops/bootstrap.sh setup step' >&2; exit 1; }

umask 077
tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT
secrets_file=$tmp_dir/production.env
dump_file=$tmp_dir/backup.dump
roles_file=$tmp_dir/roles.sql
IFS= read -r INFISICAL_TOKEN < "$TOKEN_FILE"
[[ -n "$INFISICAL_TOKEN" ]] || { echo 'Infisical read token is empty' >&2; exit 1; }
export INFISICAL_TOKEN

infisical export --domain=https://eu.infisical.com/api --token="$INFISICAL_TOKEN" \
  --projectId="$INFISICAL_PROJECT_ID" --env=prod --path=/ \
  --format=dotenv --output-file="$secrets_file"
chmod 600 "$secrets_file"
# Il file serve solo a compose (--env-file, letto come dati). Mai `source`: un valore con un apice
# diventerebbe codice shell. Una chiamata per chiave, così l'ordine dell'output non conta.
secret() {
  infisical secrets get "$1" --plain --silent --domain=https://eu.infisical.com/api --token="$INFISICAL_TOKEN" \
    --projectId="$INFISICAL_PROJECT_ID" --env=prod --path=/
}
BACKUP_BUCKET=$(secret BACKUP_BUCKET)
BACKUP_HEARTBEAT_URL=$(secret BACKUP_HEARTBEAT_URL)
unset INFISICAL_TOKEN

stamp=$(date -u +%Y-%m-%dT%H%MZ)
# rclone 1.60 ignora no_head per stream di dimensione ignota; HEAD ?versionId fallisce su R2.
# Il file privato resta sulla VPS, consente retry e viene rimosso anche in caso di errore.
ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" \
  exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$dump_file"
rclone copyto "$dump_file" "r2:${BACKUP_BUCKET:?}/pg/${stamp}.dump" --s3-no-head
# Roles belong to the cluster, so pg_dump leaves them out, and a restore on a new cluster fails on the grants to
# zenbree_app and zenbree_system. No passwords: those two are NOLOGIN, and the login role's stays in Infisical.
ZENBREE_ENV_FILE="$secrets_file" docker compose --env-file "$secrets_file" \
  exec -T postgres sh -c 'pg_dumpall --roles-only --no-role-passwords -U "$POSTGRES_USER"' > "$roles_file"
[[ -s "$roles_file" ]] || { echo 'roles dump is empty' >&2; exit 1; }
rclone copyto "$roles_file" "r2:${BACKUP_BUCKET:?}/pg/${stamp}.roles.sql" --s3-no-head
# se lo script fallisce prima di qui, il heartbeat non arriva e BetterStack apre l'incidente
curl -fsS -m 10 "${BACKUP_HEARTBEAT_URL:?}" > /dev/null
echo "backup ok ${stamp}"
