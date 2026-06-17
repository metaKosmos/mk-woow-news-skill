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


def run_stage(edition, stage, payload):
    sm = _sm()
    wd = _workdir(edition)
    try:
        _restore_content(sm, wd, edition)

        if stage == "research":
            out = _run_script(wd, "research.py", ["--edition", edition])
            _persist_content(sm, wd, edition)
            sm.upsert_edition(edition, {"stage": "researched",
                                        "date": _resolve_edition_date(edition)})
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
            return {"stage": "ready", "preview_url": html_url, "image_url": img_url,
                    "subject": meta.get("subject", ""), "cost_brl": round(cost["total_brl"], 4)}

        if stage == "send":
            html_url = sm.get_state(edition).get("preview_url")
            deliv = yaml.safe_load((CONFIG / "newsletter.yaml").read_text(encoding="utf-8"))["delivery"]
            send_args = [f"content/{edition}.md", "--content-url", html_url, "--send",
                         "--from-email", deliv["from_email"], "--from-name", deliv["from_name"],
                         "--topic-id", str(deliv["topic_id"])]
            if deliv.get("list_key"):
                send_args += ["--list-key", deliv["list_key"]]
            elif deliv.get("list_name"):
                send_args += ["--list-name", deliv["list_name"]]
            out = _run_script(wd, "send_zma.py", send_args)
            key = next((l.split(":")[-1].strip() for l in out.splitlines() if "campaignKey" in l), "")
            sm.upsert_edition(edition, {"stage": "sent", "campaign_key": key})
            return {"stage": "sent", "campaign_key": key, "log": out.strip()}

        raise ValueError(f"stage inválido: {stage}")
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


def get_queue():
    return _sm().get_queue()


def get_metrics():
    sm = _sm()
    q = sm.get_queue()
    sent = [e for e in q["editions"] if e["stage"] == "sent"][-4:]
    env = secrets_store.get_zma_gemini_env()
    out = []
    for e in sent:
        st = sm.get_state(e["edition"])
        if st.get("campaign_key"):
            m = zma_metrics.fetch(env, st["campaign_key"])
            if m:
                st["metrics"] = {**m, "fetched_at": datetime.utcnow().isoformat()}
                sm.upsert_edition(e["edition"], {"metrics": st["metrics"]})
        out.append({"edition": e["edition"], "subject": st.get("subject", ""),
                    "metrics": st.get("metrics", {}), "cost": st.get("cost", {})})
    return {"editions": out}


def do_sync():
    return _sm().sync_to_firebase()


def reset_edition(edition):
    _sm().reset_edition(edition)
    return {"reset": edition}
