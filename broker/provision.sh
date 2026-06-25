#!/usr/bin/env bash
# provision.sh — provisiona infra GCP + deploya o broker woow-news (MAR-133).
#
# RODAR NO SEU TERMINAL (gcloud autenticado: `gcloud auth login` se pedir reauth).
# A partir da pasta broker/ do repo:  cd .../mk-woow-news-skill/broker && bash provision.sh
#
# PRE-REQUISITOS (você faz no Console ANTES de rodar):
#   1. Os 5 secrets criados no Secret Manager do projeto mk-ai-first-ops:
#        zoho-ma-client-id, zoho-ma-client-secret, zoho-ma-refresh-token,
#        gemini-api-newsletter-key, firebase-service-account
#   2. Realtime Database criada no Firebase (projeto mk-ai-first-ops) + regra de
#        leitura publica em /woow_news (ver checklist).
#
# VARIAVEIS que voce exporta antes de rodar (o script avisa se faltar):
#   export OAUTH_CLIENT_ID="728697073490-....apps.googleusercontent.com"  # client Desktop reusado do blog-mk
#   export OAUTH_CLIENT_SECRET="GOCSPX-..."                               # secret do mesmo client (pega no blog-broker/Console)
#   export FIREBASE_DB_URL="https://mk-ai-first-ops-default-rtdb.firebaseio.com"
set -euo pipefail

PROJECT=mk-ai-first-ops
REGION=southamerica-east1
SVC=woow-news-broker
SA="woow-news-broker-runtime@${PROJECT}.iam.gserviceaccount.com"

# --- checagem de variaveis ---
: "${OAUTH_CLIENT_ID:?defina OAUTH_CLIENT_ID antes de rodar}"
: "${OAUTH_CLIENT_SECRET:?defina OAUTH_CLIENT_SECRET antes de rodar}"
: "${FIREBASE_DB_URL:?defina FIREBASE_DB_URL antes de rodar}"
# Reusa o CRON_TOKEN ja deployado (re-run nao deve trocar o token e quebrar o n8n).
CRON_TOKEN="$(gcloud functions describe "$SVC" --gen2 --region="$REGION" --project="$PROJECT" \
  --format='value(serviceConfig.environmentVariables.CRON_TOKEN)' 2>/dev/null || true)"
[ -z "$CRON_TOKEN" ] && CRON_TOKEN="$(openssl rand -hex 24)"

echo "==> Projeto + APIs"
gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudfunctions.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com storage.googleapis.com

echo "==> Buckets (privado p/ estado, publico p/ HTML/imagem do email)"
gcloud storage buckets create "gs://mk-woow-news-state"  --location="$REGION" --uniform-bucket-level-access || true
gcloud storage buckets create "gs://mk-woow-news-public" --location="$REGION" --uniform-bucket-level-access || true
gcloud storage buckets add-iam-policy-binding "gs://mk-woow-news-public" \
  --member=allUsers --role=roles/storage.objectViewer

echo "==> Service account de runtime (least privilege)"
gcloud iam service-accounts create woow-news-broker-runtime \
  --display-name="woow-news broker runtime" || true

echo "==> secretAccessor nos 5 secrets (per-secret, nunca project-level)"
for S in ZOHO_MA_CLIENT_ID ZOHO_MA_CLIENT_SECRET ZOHO_MA_REFRESH_TOKEN GEMINI_API_NEWSLETTER_KEY firebase-service-account; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
done

echo "==> objectAdmin nos 2 buckets"
gcloud storage buckets add-iam-policy-binding "gs://mk-woow-news-state"  --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin
gcloud storage buckets add-iam-policy-binding "gs://mk-woow-news-public" --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin

echo "==> Deploy do broker (Cloud Run function gen2)"
gcloud functions deploy "$SVC" --gen2 --runtime=python312 --region="$REGION" \
  --source=. --entry-point=broker --trigger-http --allow-unauthenticated \
  --service-account="$SA" \
  --set-env-vars="ALLOWED_DOMAIN=metakosmos.com.br,OAUTH_CLIENT_ID=${OAUTH_CLIENT_ID},OAUTH_CLIENT_SECRET=${OAUTH_CLIENT_SECRET},ADMIN_EMAILS=david@metakosmos.com.br,OPERATOR_EMAILS=joao@metakosmos.com.br;patrick@metakosmos.com.br,CRON_TOKEN=${CRON_TOKEN},STATE_BUCKET=mk-woow-news-state,PUBLIC_BUCKET=mk-woow-news-public,FIREBASE_DB_URL=${FIREBASE_DB_URL},SKILL_VERSION=1.2.0,BRL_RATE=5.70"

URL="$(gcloud functions describe "$SVC" --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"
echo "=================================================================="
echo "BROKER URL:  $URL"
echo "CRON_TOKEN:  $CRON_TOKEN     (guarde: vai no env do n8n como WOOW_CRON_TOKEN)"
echo "Smoke test /version:"
curl -s "${URL}/version"; echo
echo "=================================================================="
echo "Me mande a BROKER URL acima para eu preencher config.py + URLs do hub e dar push no repo."
