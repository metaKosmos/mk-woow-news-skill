"""orchestrator.py — cola os estágios do pipeline com state/cost, server-side.

Materializa um workdir temporário (config/prompts/templates + content da edição vindo
do GCS), injeta os segredos como .envmk equivalente, roda os scripts portados via
subprocess, e persiste state + custo + publica HTML/imagem no bucket público.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from state_manager import StateManager, GcsStore, BRT
from cost_tracker import compute_cost
import zma_metrics
import secrets_store

BROKER_DIR = Path(__file__).resolve().parent
PIPELINE = BROKER_DIR / "pipeline"
CONFIG = BROKER_DIR / "config"
PUBLIC_BUCKET = os.environ.get("PUBLIC_BUCKET", "mk-woow-news-public")

# Agendamento (schedule.json no GCS, mesmo padrão de settings.json). weekdays: 0=seg..6=dom
# (datetime.weekday()). Default desligado e em modo revisão (auto_send=False -> não dispara).
SCHEDULE_DEFAULTS = {
    "enabled": False,
    "send_time": "10:00",          # HH:MM em BRT
    "weekdays": [0, 1, 2, 3, 4, 5, 6],
    "auto_send": False,
    "until": None,                 # "YYYY-MM-DD" opcional (janela, ex.: piloto de 7 dias)
    "last_run_date": None,
}
_SCHEDULE_SET_FIELDS = {"enabled", "send_time", "weekdays", "auto_send", "until"}


def _sm():
    return StateManager(GcsStore())


def _resolve_edition_date(edition):
    """Data da edição (YYYY-MM-DD). Daily Drops: a chave já é a data; se não casar
    (legado wNN), cai para hoje em BRT. Conserta o bug de `date` em branco na gaveta."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition or ""):
        return edition
    return datetime.now(BRT).strftime("%Y-%m-%d")


def _rates():
    return yaml.safe_load((CONFIG / "cost_rates.yaml").read_text(encoding="utf-8"))


def _delivery():
    """Seção delivery do newsletter.yaml (from_email/from_name/topic_id + lista fallback)."""
    return yaml.safe_load((CONFIG / "newsletter.yaml").read_text(encoding="utf-8"))["delivery"]


def _workdir(edition):
    """Cria workdir com config/, prompts, templates e content/ da edição (do GCS)."""
    d = Path(tempfile.mkdtemp(prefix=f"woow-{edition}-"))
    (d / "config" / "prompts").mkdir(parents=True)
    (d / "content").mkdir(); (d / "templates").mkdir(); (d / "renders").mkdir()
    for f in CONFIG.glob("*.yaml"):
        (d / "config" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    for f in (CONFIG / "prompts").glob("*.md"):
        (d / "config" / "prompts" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    for f in (BROKER_DIR / "templates").glob("*.j2"):
        (d / "templates" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    env = secrets_store.get_zma_gemini_env()
    (d / ".envmk").write_text("\n".join(f"{k}={v}" for k, v in env.items()), encoding="utf-8")
    return d


def _run_script(workdir, script, args):
    """Roda um script portado com BASE = workdir (copia o script p/ lá p/ isolar paths)."""
    dst = workdir / script
    dst.write_text((PIPELINE / script).read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(dst), *args], cwd=str(workdir),
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"{script} falhou: {proc.stderr[-800:]}")
    return proc.stdout


def _persist_content(sm, wd, edition):
    for f in (wd / "content").glob(f"{edition}*"):
        sm.store.write(f"content/{f.name}", f.read_text(encoding="utf-8"))


def _restore_content(sm, wd, edition):
    for suffix in (".research.json", ".research.md", ".json", ".md", ".usage.json"):
        raw = sm.store.read(f"content/{edition}{suffix}")
        if raw:
            (wd / "content" / f"{edition}{suffix}").write_text(raw, encoding="utf-8")


def _publish_public(wd, edition):
    """Sobe HTML + imagem pro bucket público; devolve (html_url, img_url)."""
    from google.cloud import storage
    bucket = storage.Client().bucket(PUBLIC_BUCKET)
    html = wd / "renders" / f"woow-{edition}.html"
    bucket.blob(f"nl/{edition}.html").upload_from_filename(
        str(html), content_type="text/html; charset=utf-8")
    html_url = f"https://storage.googleapis.com/{PUBLIC_BUCKET}/nl/{edition}.html"
    img_url = ""
    for ext in ("jpg", "png"):
        img = wd / "renders" / f"woow-{edition}-manchete.{ext}"
        if img.exists():
            bucket.blob(f"nl/img/{img.name}").upload_from_filename(str(img))
            img_url = f"https://storage.googleapis.com/{PUBLIC_BUCKET}/nl/img/{img.name}"
            break
    return html_url, img_url


def _read_health(wd, edition):
    """Lê o health.json que a research.py grava no workdir (None se ausente/ilegível)."""
    p = wd / "content" / f"{edition}.research.health.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _record_stage_error(sm, edition, stage, exc):
    """Persiste a falha do estágio em health.last_error (merge raso no health existente,
    p/ não apagar candidates/feed_errors da pesquisa)."""
    try:
        health = dict(sm.get_state(edition).get("health") or {})
        health["last_error"] = {"stage": stage, "message": str(exc)[:500],
                                "at": datetime.now(BRT).isoformat(timespec="seconds")}
        sm.upsert_edition(edition, {"health": health})
    except Exception as inner:  # noqa: BLE001 — registrar erro não pode mascarar o erro real
        print(f"[health] não registrei erro de {edition}/{stage}: {inner}")


def _clear_stage_error(sm, edition):
    """Limpa health.last_error após um estágio concluir OK (no-op se não houver)."""
    health = sm.get_state(edition).get("health")
    if health and "last_error" in health:
        sm.upsert_edition(edition, {"health": {k: v for k, v in health.items()
                                               if k != "last_error"}})


HTML_HISTORY_MAX = 20  # teto do histórico por edição (evita crescimento sem limite no estado)


def _write_render_html(wd, edition, html):
    """Escreve o HTML da edição em renders/ (de onde _publish_public/versioned leem)."""
    (wd / "renders" / f"woow-{edition}.html").write_text(html, encoding="utf-8")


def _publish_html_version(wd, edition, stamp):
    """Sobe uma cópia IMUTÁVEL do HTML atual em nl/hist/<ed>/<stamp>.html (histórico) e
    devolve a URL pública. Diferente do nl/<ed>.html (latest), este objeto nunca é
    sobrescrito — é o snapshot que o painel lista como versão."""
    from google.cloud import storage
    bucket = storage.Client().bucket(PUBLIC_BUCKET)
    key = f"nl/hist/{edition}/{stamp}.html"
    bucket.blob(key).upload_from_filename(
        str(wd / "renders" / f"woow-{edition}.html"), content_type="text/html; charset=utf-8")
    return f"https://storage.googleapis.com/{PUBLIC_BUCKET}/{key}"


def _append_html_history(sm, edition, url, source, by, stamp):
    """Anexa uma versão de HTML ao html_history da edição (merge lê-anexa-grava; o
    upsert_edition faz merge raso, então a lista inteira é reescrita). Cortada em
    HTML_HISTORY_MAX (mantém as mais recentes)."""
    hist = list(sm.get_state(edition).get("html_history") or [])
    hist.append({"url": url, "at": datetime.now(BRT).isoformat(timespec="seconds"),
                 "source": source, "by": by or "", "stamp": stamp})
    hist = hist[-HTML_HISTORY_MAX:]
    sm.upsert_edition(edition, {"html_history": hist})
    return hist


def _publish_edition_html(sm, wd, edition, source, by=""):
    """Publica o HTML de renders/woow-<ed>.html: atualiza o 'latest' estável (nl/<ed>.html,
    o que vai no envio) E grava um snapshot imutável versionado, registrando-o no
    html_history do estado (espelhado pro painel). Devolve (latest_url, img_url)."""
    html_url, img_url = _publish_public(wd, edition)     # latest estável + imagem
    # microssegundos no stamp: 2 publicações no mesmo segundo não colidem no path imutável
    stamp = datetime.now(BRT).strftime("%Y%m%dT%H%M%S%f")
    ver_url = _publish_html_version(wd, edition, stamp)   # snapshot imutável
    _append_html_history(sm, edition, ver_url, source, by, stamp)
    return html_url, img_url


def _generate_manual_html(sm, wd, edition, payload):
    """Estágio 'generate' de campanhas manual_html: pula research/generate, recebe o HTML
    pronto (o CLI lê o arquivo local e manda o conteúdo no payload), publica no bucket
    público e marca 'ready'. `wd` já vem pronto (workdir do run_stage) — assinatura pensada
    para teste isolado (basta mockar _publish_public)."""
    payload = payload or {}
    html = payload.get("html")
    subject = payload.get("subject")
    preheader = payload.get("preheader", "")
    if not html or not subject:
        raise ValueError("manual_html exige 'html' (conteúdo) e 'subject' no payload")
    _write_render_html(wd, edition, html)
    html_url, _ = _publish_edition_html(sm, wd, edition, "manual_html", by=payload.get("_email", ""))
    # front-matter mínimo p/ o send_zma.py ler o subject (o corpo do .md é ignorado no send;
    # o conteúdo real do email é o HTML público via --content-url).
    fm = "---\n" + yaml.safe_dump({"subject": subject}, allow_unicode=True) + "---\n"
    (wd / "content" / f"{edition}.md").write_text(fm, encoding="utf-8")
    _persist_content(sm, wd, edition)
    patch = {"stage": "ready", "type": "manual_html", "subject": subject,
             "preheader": preheader, "preview_url": html_url,
             "date": _resolve_edition_date(edition)}
    if payload.get("list_key"):
        patch["list_key"] = payload["list_key"]  # lista por campanha (override no send)
    sm.upsert_edition(edition, patch)
    _clear_stage_error(sm, edition)
    return {"stage": "ready", "type": "manual_html", "preview_url": html_url, "subject": subject}


def _build_send_args(edition, st, deliv, sender, active_list):
    """Monta os args do send_zma.py (função pura, testável). Lista: list_key por campanha
    (estado) tem precedência sobre a lista-alvo ativa (settings > config). Remetente:
    sender resolvido por get_active_sender (settings > config). manual_html ganha um
    --campaign-name distinto do Daily Drops; news_auto mantém o default do send_zma."""
    args = [f"content/{edition}.md", "--content-url", st.get("preview_url"), "--send",
            "--from-email", sender["from_email"], "--from-name", sender["from_name"],
            "--topic-id", str(deliv["topic_id"])]
    if st.get("type") == "manual_html":
        args += ["--campaign-name", st.get("campaign_name") or f"mK Campanha {edition}"]
    if st.get("list_key"):
        args += ["--list-key", st["list_key"]]
    elif active_list.get("list_key"):
        args += ["--list-key", active_list["list_key"]]
    elif active_list.get("list_name"):
        args += ["--list-name", active_list["list_name"]]
    return args


def run_stage(edition, stage, payload):
    sm = _sm()
    wd = _workdir(edition)
    try:
        _restore_content(sm, wd, edition)

        if stage == "research":
            out = _run_script(wd, "research.py", ["--edition", edition])
            _persist_content(sm, wd, edition)
            patch = {"stage": "researched", "date": _resolve_edition_date(edition)}
            health = _read_health(wd, edition)
            if health is not None:
                patch["health"] = health  # pesquisa OK -> health nova, sem last_error
            sm.upsert_edition(edition, patch)
            summary = (wd / "content" / f"{edition}.research.md").read_text(encoding="utf-8")[:4000]
            return {"stage": "researched", "summary": summary, "log": out.strip()}

        if stage == "generate":
            etype = sm.get_state(edition).get("type", "news_auto")
            if etype == "manual_html":
                return _generate_manual_html(sm, wd, edition, payload)
            _run_script(wd, "generate_content.py", ["--edition", edition])
            _run_script(wd, "generate_image.py", ["--edition", edition])
            img_ext = next((e for e in ("jpg", "png")
                            if (wd / "renders" / f"woow-{edition}-manchete.{e}").exists()), None)
            render_args = ["--edition", edition]
            if img_ext:
                render_args += ["--image-url",
                                f"https://storage.googleapis.com/{PUBLIC_BUCKET}/nl/img/woow-{edition}-manchete.{img_ext}"]
            _run_script(wd, "render_newsletter.py", render_args)
            usage = json.loads((wd / "content" / f"{edition}.usage.json").read_text(encoding="utf-8"))
            cost = compute_cost(usage, _rates())
            meta = json.loads((wd / "content" / f"{edition}.json").read_text(encoding="utf-8")).get("meta", {})
            html_url, img_url = _publish_edition_html(sm, wd, edition, "news_auto")
            _persist_content(sm, wd, edition)
            sm.upsert_edition(edition, {"stage": "ready", "subject": meta.get("subject", ""),
                                        "image_ready": True, "tokens": usage, "cost": cost,
                                        "preview_url": html_url,
                                        "date": meta.get("edition_date") or _resolve_edition_date(edition)})
            _clear_stage_error(sm, edition)
            return {"stage": "ready", "preview_url": html_url, "image_url": img_url,
                    "subject": meta.get("subject", ""), "cost_brl": round(cost["total_brl"], 4)}

        if stage == "send":
            st = sm.get_state(edition)
            send_args = _build_send_args(edition, st, _delivery(),
                                         get_active_sender(sm), get_active_list(sm))
            out = _run_script(wd, "send_zma.py", send_args)
            key = next((l.split(":")[-1].strip() for l in out.splitlines() if "campaignKey" in l), "")
            sm.upsert_edition(edition, {"stage": "sent", "campaign_key": key})
            _clear_stage_error(sm, edition)
            return {"stage": "sent", "campaign_key": key, "log": out.strip()}

        raise ValueError(f"stage inválido: {stage}")
    except Exception as e:  # noqa: BLE001 — registra a falha do estágio antes de propagar
        _record_stage_error(sm, edition, stage, e)
        raise
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def add_pauta(edition, pauta):
    if not pauta:
        raise ValueError("campo 'pauta' obrigatório")
    sm = _sm()
    raw = sm.store.read(f"content/{edition}.research.json")
    items = json.loads(raw) if raw else []
    items.insert(0, {"title": pauta.get("title", ""), "content": pauta.get("content", ""),
                     "date": "", "link": pauta.get("link", ""), "source": "Pauta manual",
                     "categories": ""})
    sm.store.write(f"content/{edition}.research.json", json.dumps(items, ensure_ascii=False, indent=2))
    return {"ok": True, "candidates": len(items)}


# --------------------------------------------------------------- campanhas (type)
CAMPAIGN_TYPES = ("news_auto", "manual_html")


def create_campaign(payload):
    """Registra a edição como campanha de um `type` (news_auto | manual_html). `type` é
    CAMPO no estado, não estágio: não mexe em STAGE_RANK. Bloqueia recriar com type
    divergente se a edição já saiu de 'empty' (evita estado híbrido). O conteúdo (HTML,
    subject) entra depois, no estágio generate (manual_html) ou no pipeline (news_auto)."""
    payload = payload or {}
    edition = payload.get("edition")
    if not edition:
        raise ValueError("campo 'edition' obrigatório")
    etype = payload.get("type", "news_auto")
    if etype not in CAMPAIGN_TYPES:
        raise ValueError(f"type inválido: {etype!r} (use {' | '.join(CAMPAIGN_TYPES)})")
    sm = _sm()
    st = sm.get_state(edition)
    cur_type = st.get("type", "news_auto")
    cur_stage = st.get("stage", "empty")
    if cur_stage != "empty" and cur_type != etype:
        raise ValueError(
            f"edição {edition} já existe como '{cur_type}' em stage '{cur_stage}'; "
            f"resete antes de recriar como '{etype}' (admin: reset).")
    sm.upsert_edition(edition, {"type": etype})  # sem stage:empty (no-op pela monotonia)
    return {"edition": edition, "type": etype, "stage": cur_stage}


# --------------------------------------------------------------- listas (ZMA)
def _envmk_workdir():
    """Workdir mínimo só com .envmk (segredos ZMA) para rodar manage_lists.py."""
    d = Path(tempfile.mkdtemp(prefix="woow-lists-"))
    env = secrets_store.get_zma_gemini_env()
    (d / ".envmk").write_text("\n".join(f"{k}={v}" for k, v in env.items()), encoding="utf-8")
    return d


def _run_manage_lists(args):
    """Roda manage_lists.py num workdir e devolve o JSON da última linha do stdout."""
    wd = _envmk_workdir()
    try:
        out = _run_script(wd, "manage_lists.py", args)
        lines = [l for l in out.splitlines() if l.strip().startswith("{")]
        if not lines:
            raise RuntimeError(f"manage_lists.py sem JSON no stdout: {out[-400:]}")
        return json.loads(lines[-1])
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def get_active_list(sm=None):
    """Lista-alvo do envio diário. Estado mutável (settings.json no GCS) tem
    precedência; cai para delivery.list_key/list_name do newsletter.yaml."""
    sm = sm or _sm()
    raw = sm.store.read("settings.json")
    s = json.loads(raw) if raw else {}
    deliv = _delivery()
    return {"list_key": s.get("active_list_key") or deliv.get("list_key"),
            "list_name": s.get("active_list_name") or deliv.get("list_name"),
            "source": "settings" if s.get("active_list_key") else "config"}


def set_active_list(payload):
    """Grava a lista-alvo do envio diário em settings.json (GCS). Operador pode trocar
    sem redeploy. Redireciona QUEM recebe a news — o CLI confirma antes de chamar."""
    payload = payload or {}
    list_key = payload.get("list_key")
    if not list_key:
        raise ValueError("campo 'list_key' obrigatório")
    sm = _sm()
    s = json.loads(sm.store.read("settings.json") or "{}")
    s["active_list_key"] = list_key
    s["active_list_name"] = payload.get("list_name") or ""
    s["set_by"] = payload.get("_email", "")
    s["set_at"] = datetime.now(BRT).isoformat(timespec="seconds")
    sm.store.write("settings.json", json.dumps(s, ensure_ascii=False, indent=2))
    return {"active_list_key": s["active_list_key"], "active_list_name": s["active_list_name"],
            "set_by": s["set_by"], "set_at": s["set_at"]}


# --------------------------------------------------------------- remetente (sender)
def _verified_senders():
    """Allowlist de senders sabidamente verificados no ZMA (fallback offline quando o
    ZMA não expõe listagem de Senders). Config em newsletter.yaml delivery.verified_senders;
    default: o from_email do delivery (hoje patrick@)."""
    deliv = _delivery()
    vs = deliv.get("verified_senders")
    if isinstance(vs, str):
        vs = [vs]
    return [e.strip().lower() for e in (vs or [deliv.get("from_email", "")]) if e and e.strip()]


def _check_sender_verified(from_email):
    """Diz se `from_email` consta como verificado. Tenta a listagem de Senders do ZMA ao
    vivo (send_zma --list-senders); se indisponível, cai na allowlist configurada. Devolve
    (verified: True|False, source). source='zma' quando confirmado ao vivo, 'allowlist' senão."""
    email = (from_email or "").strip().lower()
    try:  # tentativa ao vivo (endpoint real do ZMA — confirmar por probe)
        senders = (_run_manage_or_send_senders() or {}).get("senders")
        if isinstance(senders, list) and senders:
            verified = {(s.get("email") or "").strip().lower()
                        for s in senders if s.get("verified")}
            return (email in verified, "zma")
    except Exception as exc:  # noqa: BLE001 — endpoint pode não existir; cai na allowlist
        print(f"[sender] listagem ZMA indisponível ({exc}); usando allowlist")
    return (email in _verified_senders(), "allowlist")


def get_active_sender(sm=None):
    """Remetente do envio (GLOBAL: vale p/ news_auto e manual). Estado mutável
    (settings.json) tem precedência; cai para delivery.from_email/from_name do
    newsletter.yaml. Espelha get_active_list."""
    sm = sm or _sm()
    s = json.loads(sm.store.read("settings.json") or "{}")
    deliv = _delivery()
    return {"from_email": s.get("active_from_email") or deliv.get("from_email"),
            "from_name": s.get("active_from_name") or deliv.get("from_name"),
            "source": "settings" if s.get("active_from_email") else "config"}


def set_sender(payload):
    """Grava o remetente ativo (from_email/from_name) em settings.json (GCS), preservando
    a lista-alvo (active_list_key). Vale para TODOS os envios (news diária + manuais). NÃO
    bloqueia sender não verificado: grava e avisa (o ZMA barra no envio com 6610). O CLI
    confirma antes quando verified=False."""
    payload = payload or {}
    from_email = (payload.get("from_email") or "").strip()
    if not from_email or "@" not in from_email:
        raise ValueError("campo 'from_email' obrigatório (email válido)")
    sm = _sm()
    s = json.loads(sm.store.read("settings.json") or "{}")
    s["active_from_email"] = from_email
    s["active_from_name"] = payload.get("from_name") or s.get("active_from_name") or ""
    s["sender_set_by"] = payload.get("_email", "")
    s["sender_set_at"] = datetime.now(BRT).isoformat(timespec="seconds")
    sm.store.write("settings.json", json.dumps(s, ensure_ascii=False, indent=2))
    verified, source = _check_sender_verified(from_email)
    out = {"active_from_email": s["active_from_email"], "active_from_name": s["active_from_name"],
           "verified": verified, "verified_source": source,
           "set_by": s["sender_set_by"], "set_at": s["sender_set_at"]}
    if verified is not True:
        out["warning"] = (f"'{from_email}' não consta como Sender verificado no ZMA; se o "
                          f"envio falhar com erro 6610, verifique o remetente no painel ZMA.")
    return out


def get_senders():
    """Senders do ZMA (best-effort) + o remetente ativo do envio. Se a listagem ao vivo do
    ZMA não estiver disponível, devolve a allowlist configurada como verified_senders."""
    active = get_active_sender()
    try:
        res = _run_manage_or_send_senders()
    except Exception as exc:  # noqa: BLE001
        res = {"senders": None, "note": f"listagem ZMA indisponível: {exc}"}
    if not res.get("senders"):
        res["verified_senders"] = _verified_senders()
    res["active"] = active
    return res


def _run_manage_or_send_senders():
    """Roda send_zma.py --list-senders num workdir com .envmk e devolve o JSON."""
    wd = _envmk_workdir()
    try:
        out = _run_script(wd, "send_zma.py", ["--list-senders"])
        lines = [l for l in out.splitlines() if l.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else {"senders": None}
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def set_html(payload):
    """Override de HTML por edição, sem redeploy: republica o HTML no bucket público e
    atualiza o preview_url da edição. NÃO mexe em stage/type — o template versionado no Git
    segue canônico; isto é override pontual por edição. Avisa se a edição já foi enviada."""
    payload = payload or {}
    edition = payload.get("edition")
    html = payload.get("html")
    if not edition or not html:
        raise ValueError("campos 'edition' e 'html' obrigatórios")
    sm = _sm()
    st = sm.get_state(edition)
    # tempdir mínimo: set_html só escreve o HTML e publica (não precisa dos secrets do _workdir).
    wd = Path(tempfile.mkdtemp(prefix=f"woow-sethtml-{edition}-"))
    (wd / "renders").mkdir()
    try:
        _write_render_html(wd, edition, html)
        html_url, _ = _publish_edition_html(sm, wd, edition, "set_html", by=payload.get("_email", ""))
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    sm.upsert_edition(edition, {"preview_url": html_url})
    out = {"edition": edition, "preview_url": html_url, "stage": st.get("stage", "empty"),
           "versions": len(sm.get_state(edition).get("html_history") or [])}
    if st.get("stage") == "sent":
        out["warning"] = "edição já foi ENVIADA; o override altera só o preview, não reenvia."
    return out


def list_lists():
    """getmailinglists (nome + listkey) + a lista-alvo ativa do envio diário."""
    res = _run_manage_lists(["list"])
    res["active"] = get_active_list()
    return res


def create_list(payload):
    """Cria lista ZMA (até 10 emails/call, batching no script). payload: name, emails
    (lista ou CSV), description?. Devolve {ok, listkey, listname, count}."""
    payload = payload or {}
    name = payload.get("name")
    emails = payload.get("emails")
    if not name or not emails:
        raise ValueError("campos 'name' e 'emails' obrigatórios")
    if isinstance(emails, list):
        emails = ",".join(emails)
    args = ["create", "--name", name, "--emails", emails]
    if payload.get("description"):
        args += ["--description", payload["description"]]
    return _run_manage_lists(args)


def get_queue():
    return _sm().get_queue()


def _refresh_metrics(sm):
    """Atualiza no estado (GCS) as métricas ZMA das últimas edições enviadas. Cada
    edição é isolada por try/except: uma falha do Zoho numa não impede as outras.
    Devolve o payload usado pela rota /metrics."""
    q = sm.get_queue()
    sent = [e for e in q["editions"] if e["stage"] == "sent"][-4:]
    env = secrets_store.get_zma_gemini_env()
    out = []
    for e in sent:
        st = sm.get_state(e["edition"])
        if st.get("campaign_key"):
            try:
                m = zma_metrics.fetch(env, st["campaign_key"])
            except Exception as exc:  # noqa: BLE001 — 1 edição não pode quebrar o resto
                print(f"[metrics] {e['edition']} falhou: {exc}")
                m = None
            if m:
                st["metrics"] = {**m, "fetched_at": datetime.now(BRT).isoformat(timespec="seconds")}
                sm.upsert_edition(e["edition"], {"metrics": st["metrics"]})
        out.append({"edition": e["edition"], "subject": st.get("subject", ""),
                    "metrics": st.get("metrics", {}), "cost": st.get("cost", {})})
    return {"editions": out}


def get_metrics():
    return _refresh_metrics(_sm())


def do_sync():
    """O cron diário bate aqui. Refresca as métricas ZMA ANTES de espelhar o estado;
    falha do Zoho é logada mas NÃO derruba o espelho (o painel continua atualizado)."""
    sm = _sm()
    try:
        _refresh_metrics(sm)
    except Exception as exc:  # noqa: BLE001 — refresh global falho não pode parar o sync
        print(f"[sync] metrics refresh falhou: {exc}")
    return sm.sync_to_firebase()


def reset_edition(edition):
    _sm().reset_edition(edition)
    return {"reset": edition}


# --------------------------------------------------------------- agendamento
def get_schedule(sm=None):
    """Lê o agendamento de schedule.json (GCS); preenche os defaults se ausente."""
    sm = sm or _sm()
    raw = sm.store.read("schedule.json")
    s = json.loads(raw) if raw else {}
    return {**SCHEDULE_DEFAULTS, **s}


def _validate_schedule(s):
    """Valida send_time (HH:MM), weekdays (lista de int 0..6) e until (YYYY-MM-DD|None)."""
    try:
        hh, mm = str(s["send_time"]).split(":")
        h, m = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except Exception:  # noqa: BLE001
        raise ValueError(f"send_time inválido (use HH:MM): {s.get('send_time')!r}")
    wd = s.get("weekdays")
    if (not isinstance(wd, list) or not wd
            or any(not isinstance(d, int) or d < 0 or d > 6 for d in wd)):
        raise ValueError(f"weekdays inválido (lista de int 0=seg..6=dom): {wd!r}")
    until = s.get("until")
    if until not in (None, "") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(until)):
        raise ValueError(f"until inválido (use YYYY-MM-DD): {until!r}")


def set_schedule(payload):
    """Grava o agendamento em schedule.json (GCS). Operador edita sem redeploy. Só os
    campos do agendamento são mexidos; last_run_date é preservado (dedup do tick)."""
    payload = payload or {}
    sm = _sm()
    cur = json.loads(sm.store.read("schedule.json") or "{}")
    s = {**SCHEDULE_DEFAULTS, **cur}
    for k in _SCHEDULE_SET_FIELDS:
        if k in payload:
            s[k] = payload[k]
    if s.get("until") == "":
        s["until"] = None
    _validate_schedule(s)
    s["set_by"] = payload.get("_email", "")
    s["set_at"] = datetime.now(BRT).isoformat(timespec="seconds")
    sm.store.write("schedule.json", json.dumps(s, ensure_ascii=False, indent=2))
    return s


def _mark_schedule_run(sm, date_str):
    """Marca o dia como já rodado (claim) em schedule.json, preservando o resto."""
    s = {**SCHEDULE_DEFAULTS, **json.loads(sm.store.read("schedule.json") or "{}")}
    s["last_run_date"] = date_str
    sm.store.write("schedule.json", json.dumps(s, ensure_ascii=False, indent=2))


def _should_run_now(sched, now_brt, edition_stage):
    """Função pura: decide se o tick deve rodar a edição de hoje. Devolve (bool, motivo).
    now_brt é um datetime em BRT; weekday() dá 0=seg..6=dom."""
    if not sched.get("enabled"):
        return False, "agendamento desligado"
    today = now_brt.strftime("%Y-%m-%d")
    if now_brt.weekday() not in (sched.get("weekdays") or []):
        return False, f"hoje ({now_brt.weekday()}) fora dos dias do agendamento"
    until = sched.get("until")
    if until and today > str(until):
        return False, f"fora da janela (until={until})"
    hh, mm = str(sched.get("send_time", "10:00")).split(":")
    if (now_brt.hour * 60 + now_brt.minute) < (int(hh) * 60 + int(mm)):
        return False, f"ainda não deu o horário ({sched.get('send_time')} BRT)"
    if sched.get("last_run_date") == today:
        return False, "já rodou hoje"
    done = ("sent",) if sched.get("auto_send") else ("ready", "sent")
    if edition_stage in done:
        return False, f"edição já em '{edition_stage}'"
    return True, "ok"


def run_daily(edition, auto_send=False):
    """Pipeline do dia: research -> generate -> (send se auto_send). Reaproveita run_stage;
    a monotonia de stage no StateManager evita rebaixar/duplicar. Pula se já enviada."""
    sm = _sm()
    if sm.get_state(edition).get("stage") == "sent":
        return {"skipped": "já enviada", "edition": edition, "stage": "sent"}
    run_stage(edition, "research", {})
    run_stage(edition, "generate", {})
    if auto_send:
        run_stage(edition, "send", {})
    return {"edition": edition, "auto_send": bool(auto_send),
            "stage": sm.get_state(edition).get("stage")}


def cron_tick():
    """Bate pelo Cloud Scheduler (cron-token). Lê o agendamento, decide se roda hoje e,
    se sim, claima o dia ANTES (evita tick duplo / re-send) e roda o pipeline. Ao fim
    espelha o estado pro Firebase para o painel refletir na hora."""
    sm = _sm()
    sched = get_schedule(sm)
    now = datetime.now(BRT)
    edition = _resolve_edition_date(None)  # hoje em BRT
    stage = sm.get_state(edition).get("stage", "empty")
    ok, reason = _should_run_now(sched, now, stage)
    if not ok:
        return {"ran": False, "reason": reason, "edition": edition, "stage": stage}
    _mark_schedule_run(sm, now.strftime("%Y-%m-%d"))  # claim: não re-tenta no mesmo dia
    try:
        result = run_daily(edition, sched.get("auto_send"))
    except Exception as e:  # noqa: BLE001 — dia já claimado; erro fica no health, sem 500/retry
        result = {"edition": edition, "error": str(e)[:500]}
    sm.sync_to_firebase()
    return {"ran": True, **result}
