#!/usr/bin/env python3
"""
auth.py — Login por email mK para a skill woow-news.

Faz "Entrar com Google" (conta @metakosmos.com.br) via OAuth de app instalado
(loopback localhost), captura o ID token do Google e cacheia localmente.
O ID token (NAO uma credencial sensivel) e enviado ao broker, que valida
o email/dominio antes de operar. Nenhum segredo (Gemini, Zoho) chega nesta maquina.

Uso:
    python scripts/auth.py            # faz login (abre o navegador) e cacheia o token
    python scripts/auth.py --status   # mostra quem esta logado / validade do token
    python scripts/auth.py --logout    # apaga o token cacheado

Outros scripts importam get_id_token() deste modulo para obter um token valido
(renovando via refresh_token automaticamente quando expira).

Config (sem segredos sensiveis — client de app instalado):
    Definidos em config.py (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, BROKER_URL).
"""

import argparse
import http.server
import json
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

try:
    from config import get_oauth_client, ssl_context, SESSION_MAX_AGE_HOURS
except Exception:
    # Permite rodar de qualquer diretorio: adiciona a pasta do script ao path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import get_oauth_client, ssl_context, SESSION_MAX_AGE_HOURS

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 (endpoint publico)
SCOPES = "openid email profile"
HOSTED_DOMAIN = "metakosmos.com.br"

# Cache do token na home do usuario (fora do repo da skill)
TOKEN_CACHE = Path.home() / ".woow-news-auth.json"


def log(msg, level="info"):
    prefix = {"info": "[i]", "ok": "[OK]", "warn": "[!]", "err": "[X]"}.get(level, "[i]")
    print(f"{prefix} {msg}")


def _http_post_form(url, fields):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _decode_jwt_payload(jwt):
    """Decodifica o payload (claims) de um JWT sem validar assinatura — so para leitura local."""
    import base64
    try:
        payload_b64 = jwt.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):  # noqa: N802 (interface da stdlib)
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _OAuthCallbackHandler.code = params["code"][0]
            msg = "Login concluido. Pode fechar esta aba e voltar ao Claude."
        else:
            _OAuthCallbackHandler.error = params.get("error", ["desconhecido"])[0]
            msg = f"Erro no login: {_OAuthCallbackHandler.error}. Tente novamente."
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;padding:40px'>"
            f"<h2>woow-news</h2><p>{msg}</p></body></html>".encode("utf-8")
        )

    def log_message(self, *args):  # silencia logs do servidor
        return


def _pkce_pair():
    """Gera (code_verifier, code_challenge) para PKCE S256."""
    import base64
    import hashlib
    import secrets
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def _run_login_flow():
    """Abre o navegador, captura o code via loopback e troca por tokens (com PKCE)."""
    # Servidor loopback numa porta livre
    server = http.server.HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}"
    verifier, challenge = _pkce_pair()
    client_id, client_secret = get_oauth_client()

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent select_account",
        "hd": HOSTED_DOMAIN,  # dica: restringe ao Workspace mK (UI). Validacao real e no broker.
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(auth_params)}"

    log("Abrindo o navegador para 'Entrar com Google' (use sua conta @metakosmos.com.br)...")
    print(f"    Se nao abrir sozinho, acesse: {auth_url}")
    webbrowser.open(auth_url)

    server.handle_request()  # bloqueia ate o callback
    server.server_close()

    if _OAuthCallbackHandler.error or not _OAuthCallbackHandler.code:
        log(f"Login falhou: {_OAuthCallbackHandler.error}", "err")
        sys.exit(1)

    tokens = _http_post_form(TOKEN_ENDPOINT, {
        "code": _OAuthCallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    })
    return tokens


def _save_cache(tokens, logged_in_at=None):
    claims = _decode_jwt_payload(tokens.get("id_token", ""))
    data = {
        "id_token": tokens.get("id_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + int(tokens.get("expires_in", 3600)) - 60,
        # quando o usuario fez o login INTERATIVO de verdade (preservado no refresh)
        "logged_in_at": logged_in_at if logged_in_at is not None else time.time(),
        "email": claims.get("email"),
        "hd": claims.get("hd"),
    }
    TOKEN_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        TOKEN_CACHE.chmod(0o600)
    except Exception:
        pass
    return data


def _refresh(refresh_token):
    client_id, client_secret = get_oauth_client()
    tokens = _http_post_form(TOKEN_ENDPOINT, {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    })
    # refresh nao retorna novo refresh_token — preserva o atual
    tokens.setdefault("refresh_token", refresh_token)
    return tokens


def login():
    tokens = _run_login_flow()
    data = _save_cache(tokens)
    if data.get("hd") != HOSTED_DOMAIN and not (data.get("email") or "").endswith("@" + HOSTED_DOMAIN):
        log(f"Conta {data.get('email')} nao e do dominio {HOSTED_DOMAIN}. "
            f"O broker vai recusar a publicacao.", "warn")
    log(f"Logado como {data.get('email')}", "ok")
    return data


def get_id_token():
    """Retorna um ID token valido (faz login ou refresh se necessario)."""
    if not TOKEN_CACHE.exists():
        log("Voce ainda nao esta logado. Iniciando login...", "warn")
        return login()["id_token"]

    data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))

    # Teto de sessao: passou do limite desde o ultimo login interativo? Re-autentica.
    logged_in_at = data.get("logged_in_at")
    max_age = SESSION_MAX_AGE_HOURS * 3600
    if logged_in_at is None or (time.time() - logged_in_at) > max_age:
        motivo = ("sessao sem marca de login" if logged_in_at is None
                  else f"sessao expirou (> {SESSION_MAX_AGE_HOURS:g}h desde o ultimo login)")
        log(f"{motivo}. Faca login de novo.", "warn")
        return login()["id_token"]

    if data.get("expires_at", 0) > time.time() and data.get("id_token"):
        return data["id_token"]

    # ID token expirou mas a sessao ainda e valida — tenta refresh (preserva o login original)
    if data.get("refresh_token"):
        try:
            tokens = _refresh(data["refresh_token"])
            return _save_cache(tokens, logged_in_at=logged_in_at)["id_token"]
        except Exception as e:
            log(f"Refresh falhou ({e}). Refazendo login...", "warn")

    return login()["id_token"]


def status():
    if not TOKEN_CACHE.exists():
        log("Nenhum login encontrado. Rode: python scripts/auth.py", "warn")
        return
    data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
    valid = data.get("expires_at", 0) > time.time()
    log(f"Email: {data.get('email')}")
    log(f"Dominio (hd): {data.get('hd')}")
    log(f"Token: {'valido' if valid else 'expirado (sera renovado no proximo uso)'}",
        "ok" if valid else "warn")

    logged_in_at = data.get("logged_in_at")
    if logged_in_at is None:
        log("Sessao sem marca de login — vai pedir re-login no proximo uso.", "warn")
    else:
        restante_h = (logged_in_at + SESSION_MAX_AGE_HOURS * 3600 - time.time()) / 3600
        if restante_h > 0:
            log(f"Sessao expira em ~{restante_h:.1f}h (teto de {SESSION_MAX_AGE_HOURS:g}h). "
                f"Depois disso, novo login.", "ok")
        else:
            log(f"Sessao expirada (passou de {SESSION_MAX_AGE_HOURS:g}h). "
                f"Vai pedir login no proximo uso.", "warn")


def logout():
    if TOKEN_CACHE.exists():
        TOKEN_CACHE.unlink()
        log("Logout feito (token local apagado).", "ok")
    else:
        log("Nada para apagar — voce ja estava deslogado.")


def main():
    p = argparse.ArgumentParser(description="Login por email mK para a skill woow-news")
    p.add_argument("--status", action="store_true", help="Mostra o login atual")
    p.add_argument("--logout", action="store_true", help="Apaga o token local")
    args = p.parse_args()

    if args.status:
        status()
    elif args.logout:
        logout()
    else:
        login()


if __name__ == "__main__":
    main()
