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
import roles as roles_lib

BROKER_DIR = Path(__file__).resolve().parent
PIPELINE = BROKER_DIR / "pipeline"
CONFIG = BROKER_DIR / "config"
PUBLIC_BUCKET = os.environ.get("PUBLIC_BUCKET", "mk-woow-news-public")


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
            html_url, img_url = _publish_public(wd, edition)
            _persist_content(sm, wd, edition)
            sm.upsert_edition(edition, {"stage": "ready", "subject": meta.get("subject", ""),
                                        "image_ready": True, "tokens": usage, "cost": cost,
                                        "preview_url": html_url,
                                        "date": meta.get("edition_date") or _resolve_edition_date(edition)})
            _clear_stage_error(sm, edition)
            return {"stage": "ready", "preview_url": html_url, "image_url": img_url,
                    "subject": meta.get("subject", ""), "cost_brl": round(cost["total_brl"], 4)}

        if stage == "send":
            html_url = sm.get_state(edition).get("preview_url")
            deliv = _delivery()
            target = get_active_list(sm)  # estado mutável tem precedência; cai p/ config
            send_args = [f"content/{edition}.md", "--content-url", html_url, "--send",
                         "--from-email", deliv["from_email"], "--from-name", deliv["from_name"],
                         "--topic-id", str(deliv["topic_id"])]
            if target.get("list_key"):
                send_args += ["--list-key", target["list_key"]]
            elif target.get("list_name"):
                send_args += ["--list-name", target["list_name"]]
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


# --------------------------------------------------------------- papéis (admin/operador)
def _env_role_sets():
    """(admins, operadores) das env vars — o floor de admin e a semente inicial."""
    return (roles_lib.split_emails(os.environ.get("ADMIN_EMAILS", "david@metakosmos.com.br")),
            roles_lib.split_emails(os.environ.get("OPERATOR_EMAILS", "")))


def read_roles():
    """Dict {admins, operators} do roles.json (GCS) ou None se ainda não materializado.
    Usado pelo main.py p/ resolver os papéis efetivos a cada request."""
    return _sm().read_roles()


def list_roles():
    """Papéis efetivos atuais + o floor de env (admins de env, não removíveis pela skill)."""
    env_admins, env_operators = _env_role_sets()
    stored = _sm().read_roles()
    admins, operators = roles_lib.resolve_effective(env_admins, env_operators, stored)
    return {"admins": sorted(admins), "operators": sorted(operators),
            "floor_admins": sorted(env_admins), "materialized": stored is not None}


def update_roles(payload):
    """Aplica uma mudança de papel (add/remove operador|admin). Só admin chega aqui
    (gate no main.py). Materializa o roles.json a partir das env vars na 1ª mutação."""
    payload = payload or {}
    env_admins, env_operators = _env_role_sets()
    sm = _sm()
    stored = sm.read_roles()
    if stored is None:
        stored = roles_lib.seed_from_env(env_admins, env_operators)
    new = roles_lib.apply_change(
        stored, payload.get("action"), payload.get("email"), env_admins,
        domain=os.environ.get("ALLOWED_DOMAIN", "metakosmos.com.br"))
    new["updated_by"] = payload.get("_email", "")
    new["updated_at"] = datetime.now(BRT).isoformat(timespec="seconds")
    sm.write_roles(new)
    admins, operators = roles_lib.resolve_effective(env_admins, env_operators, new)
    return {"ok": True, "action": payload.get("action"),
            "email": roles_lib.normalize(payload.get("email")),
            "admins": sorted(admins), "operators": sorted(operators),
            "updated_by": new["updated_by"], "updated_at": new["updated_at"]}
