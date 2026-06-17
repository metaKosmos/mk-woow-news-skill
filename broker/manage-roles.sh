#!/usr/bin/env bash
# manage-roles.sh — gerencia papéis do broker woow-news (admin/operador).
#   bash broker/manage-roles.sh list
#   bash broker/manage-roles.sh add-operator joao@metakosmos.com.br
#   bash broker/manage-roles.sh remove-operator patrick@metakosmos.com.br
set -e
PROJ=mk-ai-first-ops; REGION=southamerica-east1; SVC=woow-news-broker
get() { gcloud functions describe "$SVC" --gen2 --region="$REGION" --project="$PROJ" \
  --format="value(serviceConfig.environmentVariables.$1)"; }
apply() { gcloud run services update "$SVC" --region="$REGION" --project="$PROJ" \
  --update-env-vars="$1=$2" --quiet >/dev/null; }
CMD="${1:-list}"; EMAIL="$(printf '%s' "${2:-}" | tr '[:upper:]' '[:lower:]' | xargs)"
OPS="$(get OPERATOR_EMAILS)"; ADM="$(get ADMIN_EMAILS)"
case "$CMD" in
  list) echo "Admins: $ADM"; echo "Operadores: $OPS";;
  add-operator) apply OPERATOR_EMAILS "$OPS;$EMAIL"; echo "[OK] operador + $EMAIL";;
  remove-operator)
    NEW="$(printf '%s' "$OPS" | tr ';' '\n' | grep -vix "$EMAIL" | paste -sd ';' -)"
    apply OPERATOR_EMAILS "$NEW"; echo "[OK] operador - $EMAIL";;
  *) echo "uso: list | add-operator <email> | remove-operator <email>"; exit 2;;
esac
