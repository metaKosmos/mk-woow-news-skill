"""main.py — Broker woow-news (Cloud Run function gen2).

Valida login Google mK (ID token), aplica papéis (admin/operador), lê segredos no
Secret Manager e orquestra os passos do pipeline server-side. Nenhum segredo sai do GCP.

Rotas:
  GET  /version       público — versão publicada (checagem de versão da skill)
  GET  /oauth-config  público — client_id/secret de Desktop (molde blog-mk)
  GET  /sync          operador OU token de cron — espelha estado -> Firebase
  GET  /queue         operador — devolve queue.json
  GET  /metrics       operador — métricas ZMA + custo das últimas edições
  POST /run           operador — orquestra estágio (research|generate|send)
  POST /add-pauta     operador — injeta pauta manual no próximo research
  POST /admin/reset   admin    — limpa/recria estado de uma edição
"""
import os
import re

ADMIN_ONLY = {"/admin/reset"}


def _split_emails(raw):
    return {e.strip().lower() for e in re.split(r"[,;]", raw or "") if e.strip()}


def authorize(email, path, admins, operators):
    """True se `email` pode chamar `path`. Admin pode tudo; operador, só rotas não-admin."""
    email = (email or "").lower()
    path = path.rstrip("/") or "/"
    if email in admins:
        return True
    if path in ADMIN_ONLY:
        return False
    return email in operators


def _handlers():
    import functions_framework
    import json
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    ALLOWED_AUDIENCE = os.environ.get("OAUTH_CLIENT_ID", "")
    OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "")
    ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "metakosmos.com.br")
    ADMINS = _split_emails(os.environ.get("ADMIN_EMAILS", "david@metakosmos.com.br"))
    OPERATORS = _split_emails(os.environ.get("OPERATOR_EMAILS", "")) | ADMINS
    CRON_TOKEN = os.environ.get("CRON_TOKEN", "")
    SKILL_VERSION = os.environ.get("SKILL_VERSION", "1.0.0")
    adapter = google_requests.Request()

    def j(body, status=200):
        return (json.dumps(body, ensure_ascii=False), status,
                {"Content-Type": "application/json; charset=utf-8"})

    def verify(request):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise PermissionError("Token ausente. Rode: python scripts/auth.py")
        claims = google_id_token.verify_oauth2_token(
            auth.split(" ", 1)[1], adapter, ALLOWED_AUDIENCE or None)
        email = (claims.get("email") or "").lower()
        if not email or not claims.get("email_verified"):
            raise PermissionError("Email não verificado.")
        if not (claims.get("hd") == ALLOWED_DOMAIN or email.endswith("@" + ALLOWED_DOMAIN)):
            raise PermissionError(f"Email {email} fora do domínio {ALLOWED_DOMAIN}.")
        return email

    @functions_framework.http
    def broker(request):
        path = request.path.rstrip("/") or "/"
        method = request.method

        if path == "/version" and method == "GET":
            return j({"version": SKILL_VERSION})
        if path == "/oauth-config" and method == "GET":
            return j({"client_id": ALLOWED_AUDIENCE, "client_secret": OAUTH_CLIENT_SECRET})

        if path == "/sync" and method == "GET":
            import orchestrator
            if CRON_TOKEN and request.headers.get("X-Cron-Token") == CRON_TOKEN:
                return j(orchestrator.do_sync())
            try:
                email = verify(request)
            except PermissionError as e:
                return j({"error": str(e)}, 403)
            if not authorize(email, path, ADMINS, OPERATORS):
                return j({"error": "não autorizado"}, 403)
            return j(orchestrator.do_sync())

        try:
            email = verify(request)
        except PermissionError as e:
            return j({"error": str(e)}, 403)
        if not authorize(email, path, ADMINS, OPERATORS):
            return j({"error": f"{email} não autorizado para {path}"}, 403)

        payload = request.get_json(silent=True) or {}
        import orchestrator
        try:
            if path == "/queue" and method == "GET":
                return j(orchestrator.get_queue())
            if path == "/metrics" and method == "GET":
                return j(orchestrator.get_metrics())
            if path == "/run" and method == "POST":
                return j(orchestrator.run_stage(payload.get("edition"), payload.get("stage"), payload))
            if path == "/add-pauta" and method == "POST":
                return j(orchestrator.add_pauta(payload.get("edition"), payload.get("pauta")))
            if path == "/admin/reset" and method == "POST":
                return j(orchestrator.reset_edition(payload.get("edition")))
            return j({"error": f"rota desconhecida: {path}"}, 404)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {email} {path}: {e}")
            return j({"error": str(e)}, 502)

    return broker


# Entry-point exportado para o Cloud Run (functions-framework procura `broker`).
try:
    broker = _handlers()
except Exception:  # libs ausentes em teste local — `authorize` continua importável
    broker = None
