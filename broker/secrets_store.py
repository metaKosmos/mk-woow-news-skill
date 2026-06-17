"""secrets_store.py — credenciais ZMA + Gemini + Firebase no GCP Secret Manager.

Runtime SA com secretAccessor APENAS nestes secrets (per-secret IAM). Lidos em
runtime, mantidos só em memória pelo tempo da requisição.
"""
import json
import os
from functools import lru_cache
from google.cloud import secretmanager

PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT", "mk-ai-first-ops")

SECRET_NAMES = {
    "ZOHO_MA_CLIENT_ID": os.environ.get("SECRET_ZOHO_CLIENT_ID", "zoho-ma-client-id"),
    "ZOHO_MA_CLIENT_SECRET": os.environ.get("SECRET_ZOHO_CLIENT_SECRET", "zoho-ma-client-secret"),
    "ZOHO_MA_REFRESH_TOKEN": os.environ.get("SECRET_ZOHO_REFRESH", "zoho-ma-refresh-token"),
    "GEMINI_API_NEWSLETTER_KEY": os.environ.get("SECRET_GEMINI_KEY", "gemini-api-newsletter-key"),
}


@lru_cache(maxsize=1)
def _client():
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
