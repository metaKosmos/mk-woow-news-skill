#!/usr/bin/env bash
# manage-allowlist.sh — gerencia quem pode publicar no blog (allowlist do broker).
#
# A allowlist e a env var ALLOWLIST_EMAILS do broker (Cloud Run). Adicionar/remover
# atualiza essa var e sobe uma nova revisao (rapido, ~15s, sem rebuild).
# So o mantenedor (David/Patrick) roda isto — precisa de gcloud autenticado.
#
# Uso:
#   bash broker/manage-allowlist.sh list
#   bash broker/manage-allowlist.sh add joao@metakosmos.com.br
#   bash broker/manage-allowlist.sh remove tales@metakosmos.com.br
set -e

PROJ=mk-ai-first-ops
REGION=southamerica-east1
SVC=woow-news-broker

current() {
  gcloud functions describe "$SVC" --gen2 --region="$REGION" --project="$PROJ" \
    --format='value(serviceConfig.environmentVariables.ALLOWLIST_EMAILS)'
}

apply() {  # $1 = nova lista separada por ;
  gcloud run services update "$SVC" --region="$REGION" --project="$PROJ" \
    --update-env-vars="ALLOWLIST_EMAILS=$1" --quiet >/dev/null
}

CMD="${1:-list}"
EMAIL="$(printf '%s' "${2:-}" | tr '[:upper:]' '[:lower:]' | xargs)"
LIST="$(current)"

case "$CMD" in
  list)
    echo "Quem pode publicar hoje:"
    printf '%s\n' "$LIST" | tr ';' '\n' | sed 's/^/  • /'
    ;;
  add)
    [ -z "$EMAIL" ] && { echo "uso: add <email>"; exit 2; }
    if printf '%s' ";$LIST;" | grep -qi ";$EMAIL;"; then
      echo "[i] $EMAIL ja esta na lista."; exit 0
    fi
    apply "$LIST;$EMAIL"
    echo "[OK] adicionado: $EMAIL"
    ;;
  remove)
    [ -z "$EMAIL" ] && { echo "uso: remove <email>"; exit 2; }
    NEW="$(printf '%s' "$LIST" | tr ';' '\n' | grep -vix "$EMAIL" | paste -sd ';' -)"
    if [ "$NEW" = "$LIST" ]; then echo "[i] $EMAIL nao estava na lista."; exit 0; fi
    apply "$NEW"
    echo "[OK] removido: $EMAIL"
    ;;
  *)
    echo "uso: bash broker/manage-allowlist.sh [list|add <email>|remove <email>]"; exit 2
    ;;
esac