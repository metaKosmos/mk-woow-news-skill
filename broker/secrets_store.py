"""secrets_store.py — credenciais ZMA + Gemini + Firebase no GCP Secret Manager.

Runtime SA com secretAccessor APENAS nestes secrets (per-secret IAM). Lidos em
runtime, mantidos só em memória pelo tempo da requisição.
"""
import json
import os
from functools import lru_cache

PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT", "mk-ai-first-ops")

# Nomes dos secrets no GCP. David cadastrou usando os nomes do .envmk (MAIÚSCULO);
# mantemos esses nomes aqui (override por env SECRET_* se mudar no futuro).
SECRET_NAMES = {
    "ZOHO_MA_CLIENT_ID": os.environ.get("SECRET_ZOHO_CLIENT_ID", "ZOHO_MA_CLIENT_ID"),
    "ZOHO_MA_CLIENT_SECRET": os.environ.get("SECRET_ZOHO_CLIENT_SECRET", "ZOHO_MA_CLIENT_SECRET"),
    "ZOHO_MA_REFRESH_TOKEN": os.environ.get("SECRET_ZOHO_REFRESH", "ZOHO_MA_REFRESH_TOKEN"),
    "GEMINI_API_NEWSLETTER_KEY": os.environ.get("SECRET_GEMINI_KEY", "GEMINI_API_NEWSLETTER_KEY"),
}


@lru_cache(maxsize=1)
def _client():
    # Lazy import: mantém o módulo carregável offline (testes), igual ao GcsStore.
    from google.cloud import secretmanager
    return secretmanager.SecretManagerServiceClient()


def _access(secret_name, version="latest"):
    name = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/{version}"
    resp = _client().access_secret_version(request={"name": name})
    return resp.payload.data.decode("utf-8").strip()


@lru_cache(maxsize=1)
def get_zma_gemini_env():
    """Devolve dict no formato que os scripts do pipeline esperam no .envmk (cacheado)."""
    return {key: _access(secret) for key, secret in SECRET_NAMES.items()}


def get_firebase_credentials():
    """Service account JSON do Firebase (secret separado, raw JSON)."""
    return json.loads(_access(os.environ.get("SECRET_FIREBASE_SA", "firebase-service-account")))
