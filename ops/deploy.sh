#!/usr/bin/env bash
# Deploy di main sulla VPS: ops/deploy.sh [host]. Lo lancia la CI a ogni push su main.
# Le immagini le costruisce la CI su GHCR (tag = SHA); qui arrivano solo compose.yml e ops/
# via git archive, poi pull + restart. La VPS deve essere loggata su ghcr.io (la CI lo fa prima).
# Rollback: sulla VPS `SKIP_MIGRATE=1 ops/deploy-production.sh <sha precedente>`. Lo schema è forward-only: l'immagine
# precedente gira sullo schema attuale (SKIP_MIGRATE serve per immagini senza runner, cioè prima di #268).
# ponytail: tar non cancella i file rimossi dal repo; se ne togli uno che conta, rimuovilo anche da /opt/zenbree.
set -euo pipefail
HOST=${1:?usage: ops/deploy.sh deploy@your-server}
SHA=$(git rev-parse main)
git archive main | ssh "$HOST" "cd /opt/zenbree && tar -x && echo ${SHA:0:7} > REVISION"
ssh "$HOST" "cd /opt/zenbree && ops/deploy-production.sh $SHA"
echo " deployed ${SHA:0:7}"
