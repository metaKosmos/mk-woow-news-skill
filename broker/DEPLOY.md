# Deploy do broker woow-news (GCP)

Provisão e deploy do broker no projeto **`mk-ai-first-ops`**. Execução manual.
**Nunca fazer deploy em produção sem confirmação explícita do David.**

O broker é uma Cloud Run function (gen2) que valida o login por email mK, lê as
credenciais (Zoho Marketing Automation, Gemini, Firebase) no Secret Manager e
roda o pipeline da newsletter WooW server-side. As credenciais nunca saem do GCP.
O estado do pipeline fica no bucket privado e os artefatos públicos (preview HTML,
imagens) vão para o bucket público.

---

## 0. Pré-requisitos

```bash
gcloud auth login
gcloud config set project mk-ai-first-ops
gcloud services enable \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com
```

Variáveis usadas abaixo:
```bash
export PROJECT=mk-ai-first-ops
export REGION=southamerica-east1
export RUNTIME_SA=woow-news-broker-runtime@$PROJECT.iam.gserviceaccount.com
```

---

## 1. Secrets (Secret Manager)

Crie os 5 secrets. Os valores reais (David tem por DM) entram via stdin ou
`--data-file`, nunca deixe credencial no histórico do shell.

```bash
printf '%s' 'ZOHO_MA_CLIENT_ID_REAL'      | gcloud secrets create zoho-ma-client-id      --data-file=-
printf '%s' 'ZOHO_MA_CLIENT_SECRET_REAL'  | gcloud secrets create zoho-ma-client-secret  --data-file=-
printf '%s' 'ZOHO_MA_REFRESH_TOKEN_REAL'  | gcloud secrets create zoho-ma-refresh-token  --data-file=-
printf '%s' 'GEMINI_API_KEY_REAL'         | gcloud secrets create gemini-api-newsletter-key --data-file=-
```

O secret do Firebase é um JSON de service account, entra por arquivo:
```bash
gcloud secrets create firebase-service-account --data-file=/path/to/firebase-sa.json
```

Para rotacionar qualquer secret depois (sem tocar em nenhuma máquina):
```bash
printf '%s' 'NOVO_VALOR' | gcloud secrets versions add zoho-ma-refresh-token --data-file=-
```

---

## 2. Buckets do GCS (estado privado + artefatos públicos)

Dois buckets, ambos com uniform bucket-level access (`-b on`):

```bash
# estado do pipeline, privado
gsutil mb -l southamerica-east1 -b on gs://mk-woow-news-state

# artefatos públicos (preview HTML, imagens), leitura pública
gsutil mb -l southamerica-east1 -b on gs://mk-woow-news-public
gsutil iam ch allUsers:objectViewer gs://mk-woow-news-public
```

> Só o bucket público recebe `allUsers:objectViewer`. O de estado permanece
> privado, acessível apenas pela service account de runtime (passo 3).

---

## 3. Service account de runtime (least privilege)

```bash
# SA dedicada (não reusar a default compute SA)
gcloud iam service-accounts create woow-news-broker-runtime \
  --display-name="woow-news broker runtime"

# secretAccessor APENAS nos 5 secrets (nunca project-level)
for S in zoho-ma-client-id zoho-ma-client-secret zoho-ma-refresh-token \
         gemini-api-newsletter-key firebase-service-account; do
  gcloud secrets add-iam-policy-binding $S \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/secretmanager.secretAccessor"
done

# objectAdmin nos 2 buckets (ler/escrever estado e artefatos)
gsutil iam ch serviceAccount:$RUNTIME_SA:roles/storage.objectAdmin gs://mk-woow-news-state
gsutil iam ch serviceAccount:$RUNTIME_SA:roles/storage.objectAdmin gs://mk-woow-news-public
```

---

## 4. OAuth client (login por email)

No console: **APIs & Services → Credentials → Create OAuth client ID → Desktop app**
(nome: `woow-news skill`). Isso gera `client_id` + `client_secret` de **app instalado**
(o secret de app instalado é público por design, o gate real é o verify do ID token
+ a checagem de papel no broker).

Configure também a **OAuth consent screen** como Internal (restrita ao Workspace
metakosmos.com.br).

Pode reusar o client OAuth do blog-mk ou criar um novo `woow-news skill`. O
`client_id` vira `OAUTH_CLIENT_ID` no broker (valida o `aud` do token) e o
`client_secret` vira `OAUTH_CLIENT_SECRET`. O broker serve o `client_secret` ao
CLI pelo endpoint `/oauth-config`, mesma lógica public-by-design do blog-mk: o
repo da skill pode ser público sem expor nada que não seja o secret de app
instalado.

---

## 5. Deploy da function

```bash
cd broker

# gere um token aleatório para o cron antes do deploy
export CRON_TOKEN="$(openssl rand -hex 24)"

gcloud functions deploy woow-news-broker \
  --gen2 \
  --runtime=python312 \
  --region=$REGION \
  --source=. \
  --entry-point=broker \
  --trigger-http \
  --allow-unauthenticated \
  --service-account=$RUNTIME_SA \
  --set-env-vars="ALLOWED_DOMAIN=metakosmos.com.br,OAUTH_CLIENT_ID=SEU_CLIENT_ID.apps.googleusercontent.com,OAUTH_CLIENT_SECRET=GOCSPX-...,ADMIN_EMAILS=david@metakosmos.com.br,OPERATOR_EMAILS=joao@metakosmos.com.br;patrick@metakosmos.com.br,CRON_TOKEN=$CRON_TOKEN,STATE_BUCKET=mk-woow-news-state,PUBLIC_BUCKET=mk-woow-news-public,FIREBASE_DB_URL=https://mk-ai-first-ops.firebaseio.com,SKILL_VERSION=1.0.0,BRL_RATE=5.70"
```

> Os emails em `OPERATOR_EMAILS` são separados por ponto e vírgula porque o
> gcloud usa vírgula para separar as variáveis entre si. Mesma convenção em
> `manage-roles.sh`.

> Nota sobre `--allow-unauthenticated`: é intencional. A function aceita
> invocação não autenticada na borda e confia 100% na verificação do ID token
> (`verify_oauth2_token`) + checagem de papel (admin/operador) dentro do código.
> Mesma racionalização do MVP do blog-mk: o gate real é o email verificado, não
> um token de invocação do Cloud Run.

Pegue a URL publicada:
```bash
gcloud functions describe woow-news-broker --gen2 --region=$REGION --format='value(serviceConfig.uri)'
```

---

## 6. Configurar OAuth e a URL do broker no cliente

O `client_secret` do OAuth NÃO fica no repo da skill: ele é servido pelo broker
no endpoint `/oauth-config`, e o cliente busca em tempo de uso. Então:

- No **broker**, as env vars `OAUTH_CLIENT_ID` e `OAUTH_CLIENT_SECRET` já foram
  definidas no deploy (passo 5). Para rotacionar depois:
  ```bash
  gcloud functions deploy woow-news-broker --gen2 --region=$REGION \
    --update-env-vars="OAUTH_CLIENT_SECRET=GOCSPX-..."
  ```
- No **cliente**, só fica `BROKER_URL` (não-secreto). Nada de client_id/secret
  hardcoded.

> Se trocar/rotacionar o client OAuth, basta atualizar `OAUTH_CLIENT_ID` e
> `OAUTH_CLIENT_SECRET` no broker e redeployar, o cliente pega sozinho.

---

## 7. Smoke test

```bash
# /version é público (usado pela checagem de versão)
curl -s "$(gcloud functions describe woow-news-broker --gen2 --region=$REGION --format='value(serviceConfig.uri)')/version"
# -> {"version":"1.0.0"}
```

Confira nos logs do Cloud Run que os secrets foram lidos do Secret Manager e que
a resposta ao cliente nunca contém credencial:
```bash
gcloud functions logs read woow-news-broker --gen2 --region=$REGION --limit=20
```

---

## Cron tick (agendamento da News)

O agendamento da News (horário/dias/auto-send) é dado mutável em `schedule.json` no bucket
de estado, editado pela skill (`woow.py schedule ...`) sem redeploy. Quem **executa** é um
único Cloud Scheduler que bate periodicamente no broker; o broker lê o `schedule.json` e
roda a edição do dia quando dá o horário (dedup por dia). Reusa o `CRON_TOKEN` do `/sync`.

```bash
# 1) habilite o Cloud Scheduler (uma vez)
gcloud services enable cloudscheduler.googleapis.com

# 2) crie o job (a cada 15 min; o broker decide se é hora de rodar)
BROKER_URL="$(gcloud functions describe woow-news-broker --gen2 --region=$REGION --format='value(serviceConfig.uri)')"
gcloud scheduler jobs create http woow-news-tick \
  --location=$REGION \
  --schedule="*/15 * * * *" \
  --uri="$BROKER_URL/cron/tick" \
  --http-method=POST \
  --headers="X-Cron-Token=$CRON_TOKEN" \
  --attempt-deadline=900s
```

> O tick roda research → generate → (send se `auto_send`) **num único request**. Suba o
> timeout e a memória da function para acomodar o pipeline:
> ```bash
> gcloud functions deploy woow-news-broker --gen2 --region=$REGION \
>   --update-env-vars=... --timeout=900s --memory=1Gi
> ```
> Falha de um dia **não** re-tenta sozinha (o dia já foi "claimado"); um operador roda na
> mão com `woow.py run --edition <hoje>`.

## Papéis e allowlist

Dois níveis de acesso, ambos em env vars do broker:

- **`ADMIN_EMAILS`** (david): mudanças exigem redeploy (passo 5) ou
  `gcloud functions deploy ... --update-env-vars="ADMIN_EMAILS=..."`.
- **`OPERATOR_EMAILS`** (joão, patrick): gerenciados sem rebuild por
  `manage-roles.sh`, que sobe uma nova revisão do Cloud Run (~15s):
  ```bash
  bash broker/manage-roles.sh list
  bash broker/manage-roles.sh add-operator novo@metakosmos.com.br
  bash broker/manage-roles.sh remove-operator antigo@metakosmos.com.br
  ```

O `manage-allowlist.sh` (herdado do blog-mk) continua disponível para a env var
`ALLOWLIST_EMAILS` caso o broker use esse modelo simples em vez de papéis.

---

## Custo

5-10 usuários / uma newsletter por semana → dentro do free tier de Secret
Manager + Cloud Run functions + Cloud Storage + Google Sign-In. Custo realista:
~$0/mês.
