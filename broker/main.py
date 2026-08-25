"""main.py — Broker woow-news (Cloud Run function gen2).

Valida login Google mK (ID token), aplica papéis (admin/operador), lê segredos no
Secret Manager e orquestra os passos do pipeline server-side. Nenhum segredo sai do GCP.

Rotas:
  GET  /version       público — versão publicada (checagem de versão da skill)
  GET  /oauth-config  público — client_id/secret de Desktop (molde blog-mk)
  GET  /sync          operador OU token de cron — espelha estado -> Firebase
  POST /cron/tick     token de cron (OU operador) — roda o agendamento se for a hora
  GET  /schedule      operador — devolve o agendamento (schedule.json)
  POST /schedule/set  operador — grava o agendamento (horário/dias/auto-send/janela)
  GET  /queue         operador — devolve queue.json
  GET  /metrics       operador — métricas ZMA + custo das últimas edições
  POST /run           operador — orquestra estágio (research|generate|send)
  POST /add-pauta     operador — injeta pauta manual no próximo research
  POST /campaigns/create   operador — registra edição como campanha (type news_auto|manual_html)
  POST /campaigns/set-html operador — override do HTML de uma edição (sem redeploy)
  GET  /lists         operador — lista as mailing lists ZMA + alvo ativo do envio
  POST /lists/create  operador — cria lista ZMA + contatos (addlistandleads)
  POST /lists/set-active operador — troca a lista-alvo do envio diário (settings.json)
  GET  /senders       operador — Senders do ZMA (best-effort) + remetente ativo
  POST /senders/set-active operador — troca o remetente ativo (settings.json, global)
  GET  /sources       operador — fontes RSS da pesquisa (sources.json) + seed do feeds.yaml
  POST /sources/set   operador — edita as fontes (add|remove|enable|disable|set-url)
  POST /sources/test  operador — baixa os feeds de dentro do broker e devolve o status
  GET  /clients       operador — quem opera a skill e em que versão (tabela só p/ admin)
  POST /admin/release admin    — grava a nota da versão publicada (aparece no aviso)
  POST /admin/reset   admin    — limpa/recria estado de uma edição

Toda resposta autenticada carrega `_aviso` quando o cliente manda `X-Skill-Version`
mais antigo que o publicado. O aviso é do broker, não do cliente: assim ele aparece em
qualquer comando, sem depender de alguém rodar a checagem de versão.
"""
import os
import re

ADMIN_ONLY = {"/admin/reset", "/admin/release"}


def outdated(client_version, published_version):
    """True quando o cliente está atrás da versão publicada.

    False quando o cliente não mandou versão (skill anterior à 1.5.0, que nem saberia
    ler o aviso) ou quando algum lado não é semver numérico: aviso errado é pior que
    silêncio, e foi assim que a checagem antiga ficou muda sem ninguém notar."""
    def _p(v):
        try:
            return tuple(int(x) for x in (v or "").split("."))
        except (ValueError, AttributeError):
            return None
    cli, pub = _p(client_version), _p(published_version)
    return bool(cli and pub and pub > cli)


def with_notice(body, status, client_version, published_version, notice_fn):
    """Devolve o corpo com `_aviso` quando o cliente está atrás da versão publicada.

    Fica fora do handler para ser testável sem functions_framework. Nunca levanta: se
    montar o aviso falhar, a resposta sai como estava — aviso de update não pode
    derrubar um envio de newsletter."""
    if status != 200 or not isinstance(body, dict):
        return body
    if not outdated(client_version, published_version):
        return body
    try:
        return {**body, "_aviso": notice_fn(client_version, published_version)}
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] não montei o aviso de update: {exc}")
        return body


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
        cli_ver = request.headers.get("X-Skill-Version", "")

        def jj(body, status=200):
            """j() mais o aviso de update. Só toca o GCS quando o cliente está atrás —
            comparar versão é de graça, então quem está em dia não paga leitura."""
            def _notice(cli, pub):
                import orchestrator as _o
                return _o.update_notice(cli, pub)

            return j(with_notice(body, status, cli_ver, SKILL_VERSION, _notice), status)

        if path == "/version" and method == "GET":
            return j({"version": SKILL_VERSION})
        if path == "/oauth-config" and method == "GET":
            return j({"client_id": ALLOWED_AUDIENCE, "client_secret": OAUTH_CLIENT_SECRET})

        if path == "/sync" and method == "GET":
            import orchestrator
            if CRON_TOKEN and request.headers.get("X-Cron-Token") == CRON_TOKEN:
                return jj(orchestrator.do_sync())
            try:
                email = verify(request)
            except PermissionError as e:
                return jj({"error": str(e)}, 403)
            if not authorize(email, path, ADMINS, OPERATORS):
                return jj({"error": "não autorizado"}, 403)
            return jj(orchestrator.do_sync())

        if path == "/cron/tick" and method == "POST":
            import orchestrator
            if CRON_TOKEN and request.headers.get("X-Cron-Token") == CRON_TOKEN:
                return jj(orchestrator.cron_tick())
            try:
                email = verify(request)  # fallback: operador pode disparar o tick na mão
            except PermissionError as e:
                return jj({"error": str(e)}, 403)
            if not authorize(email, path, ADMINS, OPERATORS):
                return jj({"error": "não autorizado"}, 403)
            return jj(orchestrator.cron_tick())

        try:
            email = verify(request)
        except PermissionError as e:
            return jj({"error": str(e)}, 403)
        if not authorize(email, path, ADMINS, OPERATORS):
            return jj({"error": f"{email} não autorizado para {path}"}, 403)

        payload = request.get_json(silent=True) or {}
        import orchestrator
        try:  # quem opera, em que versão. Registro nunca pode derrubar a rota.
            orchestrator.record_client(email, cli_ver, path)
        except Exception as exc:  # noqa: BLE001
            print(f"[clients] não registrei {email}: {exc}")
        try:
            if path == "/queue" and method == "GET":
                return jj(orchestrator.get_queue())
            if path == "/metrics" and method == "GET":
                return jj(orchestrator.get_metrics())
            if path == "/run" and method == "POST":
                return jj(orchestrator.run_stage(payload.get("edition"), payload.get("stage"), payload))
            if path == "/add-pauta" and method == "POST":
                return jj(orchestrator.add_pauta(payload.get("edition"), payload.get("pauta")))
            if path == "/campaigns/create" and method == "POST":
                return jj(orchestrator.create_campaign({**payload, "_email": email}))
            if path == "/campaigns/set-html" and method == "POST":
                return jj(orchestrator.set_html({**payload, "_email": email}))
            if path == "/senders" and method == "GET":
                return jj(orchestrator.get_senders())
            if path == "/senders/set-active" and method == "POST":
                return jj(orchestrator.set_sender({**payload, "_email": email}))
            if path == "/clients" and method == "GET":
                return jj(orchestrator.get_clients_report(
                    SKILL_VERSION, full=(email in ADMINS), email=email,
                    roster=(ADMINS | OPERATORS)))
            if path == "/admin/release" and method == "POST":
                return jj(orchestrator.set_release(
                    {**payload, "version": SKILL_VERSION, "_email": email}))
            if path == "/sources" and method == "GET":
                return jj(orchestrator.get_sources())
            if path == "/sources/set" and method == "POST":
                return jj(orchestrator.set_sources({**payload, "_email": email}))
            if path == "/sources/test" and method == "POST":
                return jj(orchestrator.test_sources({**payload, "_email": email}))
            if path == "/lists" and method == "GET":
                return jj(orchestrator.list_lists())
            if path == "/lists/create" and method == "POST":
                return jj(orchestrator.create_list(payload))
            if path == "/lists/set-active" and method == "POST":
                return jj(orchestrator.set_active_list({**payload, "_email": email}))
            if path == "/schedule" and method == "GET":
                return jj(orchestrator.get_schedule())
            if path == "/schedule/set" and method == "POST":
                return jj(orchestrator.set_schedule({**payload, "_email": email}))
            if path == "/admin/reset" and method == "POST":
                return jj(orchestrator.reset_edition(payload.get("edition")))
            return jj({"error": f"rota desconhecida: {path}"}, 404)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {email} {path}: {e}")
            return jj({"error": str(e)}, 502)

    return broker


# Entry-point exportado para o Cloud Run (functions-framework procura `broker`).
try:
    broker = _handlers()
except Exception:  # libs ausentes em teste local — `authorize` continua importável
    broker = None
