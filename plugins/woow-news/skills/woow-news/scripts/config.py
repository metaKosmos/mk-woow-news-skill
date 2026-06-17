"""
config.py — Configuracao publica da skill woow-news (sem segredos no repo).

Este arquivo NAO contem credenciais. O OAuth client (client_id + client_secret de
app instalado) e buscado em tempo de uso do broker (`GET /oauth-config`), para nao
ficar neste repositorio publico. O client_secret de Desktop e publico por design do
Google; o gate real de operacao e a allowlist no broker.

A unica config aqui e a URL publica do broker. Tudo pode ser sobrescrito por env.
"""

import json
import os
import ssl
import urllib.request


def ssl_context():
    """Contexto SSL com CA bundle confiavel.

    O Python do python.org no macOS nao usa o keychain do sistema e pode nao ter
    CA bundle (erro CERTIFICATE_VERIFY_FAILED). Preferimos o bundle do `certifi`
    (instalado pelo setup.sh); se nao houver, caimos no contexto padrao.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_oauth_cache = None


def get_oauth_client():
    """Retorna (client_id, client_secret) do OAuth de app instalado.

    Ordem: env vars (WOOW_NEWS_GOOGLE_CLIENT_ID/SECRET) -> endpoint /oauth-config do
    broker. Buscado de forma preguicosa (so quando o login precisa), entao operacoes
    offline como --dry-run nao dependem de rede.
    """
    global _oauth_cache
    cid = os.environ.get("WOOW_NEWS_GOOGLE_CLIENT_ID")
    csec = os.environ.get("WOOW_NEWS_GOOGLE_CLIENT_SECRET")
    if cid and csec:
        return cid, csec
    if _oauth_cache:
        return _oauth_cache
    with urllib.request.urlopen(f"{BROKER_URL}/oauth-config", timeout=15,
                                context=ssl_context()) as r:
        d = json.loads(r.read().decode("utf-8"))
    _oauth_cache = (d["client_id"], d["client_secret"])
    return _oauth_cache


# URL base do broker (Cloud Run function, projeto mk-ai-first-ops, southamerica-east1).
BROKER_URL = os.environ.get(
    "WOOW_NEWS_BROKER_URL",
    "https://woow-news-broker-PLACEHOLDER.run.app",  # TODO: substituir pela URL real do deploy (Task 9)
).rstrip("/")

# Tempo maximo de sessao (horas) antes de exigir novo login interativo no navegador.
# Passado esse periodo desde o ULTIMO login de verdade, o refresh token para de
# valer e o usuario precisa entrar com a conta mK de novo. Ajuste aqui a politica.
SESSION_MAX_AGE_HOURS = float(os.environ.get("WOOW_NEWS_SESSION_MAX_AGE_HOURS", "12"))
