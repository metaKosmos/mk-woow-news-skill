#!/usr/bin/env python3
"""Ingestão de pauta da WooW! Daily Drops: lê os feeds RSS e monta a lista de candidatos.

Uso:
    python3 research.py --edition 2026-w25 [--days 3]

Lê config/feeds.yaml (fontes) e config/newsletter.yaml (janela de recência), baixa
cada feed, filtra pelos itens dos últimos N dias, deduplica por link e título, e
grava dois arquivos:

    content/<edition>.research.json  -> candidatos estruturados (input do generate_content.py)
    content/<edition>.research.md    -> resumo legível (material do Checkpoint 1)

Esta é a perna de PESQUISA. Não chama LLM, não envia nada. O Checkpoint 1 acontece
entre este script e o generate_content.py: um humano revisa o .research.md (e pode
editar o .research.json) antes da geração de conteúdo.
"""
import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
    import feedparser
except ImportError:
    sys.exit("Faltam dependências. Rode: pip3 install pyyaml feedparser")

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config"
CONTENT = BASE / "content"

# UA de browser: vários feeds (WordPress, BoF) bloqueiam o agent padrão do feedparser.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TAG_RE = re.compile(r"<[^>]+>")


def load_yaml(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def strip_html(raw: str) -> str:
    """Remove tags e normaliza espaços/entidades de um resumo de feed."""
    if not raw:
        return ""
    text = html.unescape(TAG_RE.sub(" ", raw))
    return re.sub(r"\s+", " ", text).strip()


def entry_date(entry):
    """Retorna a data do item como datetime UTC, ou None se o feed não trouxer."""
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            return datetime(*tm[:6], tzinfo=timezone.utc)
    return None


class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python 3.9 não segue 308 sozinho. Trata como 301."""

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)


_OPENER = urllib.request.build_opener(_Redirect308)


def fetch_feed(url):
    """Baixa os bytes do feed com headers de browser e entrega ao feedparser.

    Buscar os bytes por conta própria (em vez do fetch interno do feedparser)
    contorna servidores que mandam content-type text/html, segue redirecionamentos
    308 e deixa o parser tolerante do feedparser lidar com XML malformado.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    })
    with _OPENER.open(req, timeout=30) as resp:
        return resp.read()


def collect(feeds, days, max_per_source):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates = []
    report = []  # (source, encontrados, dentro_da_janela, erro)
    for f in feeds:
        source, url = f["source"], f["url"]
        fetch_err = None
        try:
            parsed = feedparser.parse(fetch_feed(url))
        except Exception as exc:  # noqa: BLE001 — guarda o erro real (403/404/timeout)
            fetch_err = f"{type(exc).__name__}: {exc}"
            try:  # fallback: deixa o feedparser buscar pela URL
                parsed = feedparser.parse(url, agent=USER_AGENT)
            except Exception as exc2:  # noqa: BLE001
                report.append((source, 0, 0, fetch_err or str(exc2)))
                continue
        if not parsed.entries:
            reason = fetch_err or parsed.get("bozo_exception", "sem itens")
            report.append((source, 0, 0, f"falha ao ler ({reason})"))
            continue
        kept = 0
        for entry in parsed.entries[:max_per_source]:
            dt = entry_date(entry)
            if dt is not None and dt < cutoff:
                continue  # fora da janela
            content = strip_html(entry.get("summary", ""))
            if not content and entry.get("content"):
                content = strip_html(entry["content"][0].get("value", ""))
            cats = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
            candidates.append({
                "title": (entry.get("title") or "").strip(),
                "content": content,
                "date": dt.isoformat() if dt else "",
                "link": (entry.get("link") or "").strip(),
                "source": source,
                "categories": ", ".join(cats),
            })
            kept += 1
        report.append((source, len(parsed.entries), kept, None))
    return candidates, report


def dedup(items):
    """Remove duplicados por link e por título normalizado (mesma notícia em 2 portais)."""
    seen_link, seen_title, out = set(), set(), []
    for it in items:
        link = it["link"].split("?")[0].rstrip("/").lower()
        title_key = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:80]
        if (link and link in seen_link) or (title_key and title_key in seen_title):
            continue
        if link:
            seen_link.add(link)
        if title_key:
            seen_title.add(title_key)
        out.append(it)
    return out


def write_research_md(path, edition, days, candidates, report):
    lines = [f"# Pauta WooW! Daily Drops — {edition}", ""]
    lines.append(f"Janela: últimos {days} dias. Candidatos após dedup: **{len(candidates)}**.")
    lines.append("")
    lines.append("## Cobertura por fonte")
    lines.append("")
    lines.append("| Fonte | Itens no feed | Dentro da janela | Status |")
    lines.append("|---|---|---|---|")
    for source, found, kept, err in report:
        status = "ok" if not err else f"erro: {err}"
        lines.append(f"| {source} | {found} | {kept} | {status} |")
    lines.append("")
    lines.append("## Candidatos (revisar antes de gerar)")
    lines.append("")
    by_source = {}
    for c in candidates:
        by_source.setdefault(c["source"], []).append(c)
    for source, items in by_source.items():
        lines.append(f"### {source} ({len(items)})")
        lines.append("")
        for c in items:
            date = c["date"][:10] if c["date"] else "sem data"
            lines.append(f"- **{c['title']}** ({date})")
            if c["content"]:
                lines.append(f"  {c['content'][:240]}")
            lines.append(f"  {c['link']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Ingestão RSS da WooW! Daily Drops")
    ap.add_argument("--edition", required=True, help="rótulo da edição, ex: 2026-w25")
    ap.add_argument("--days", type=int, default=None, help="override da janela de recência")
    args = ap.parse_args()

    feeds_cfg = load_yaml("feeds.yaml")
    nl_cfg = load_yaml("newsletter.yaml")
    feeds = feeds_cfg["feeds"]
    days = args.days if args.days is not None else nl_cfg["research"]["days_lookback"]
    max_per_source = nl_cfg["research"]["max_per_source"]

    candidates, report = collect(feeds, days, max_per_source)
    candidates = dedup(candidates)

    CONTENT.mkdir(exist_ok=True)
    json_path = CONTENT / f"{args.edition}.research.json"
    md_path = CONTENT / f"{args.edition}.research.md"
    json_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    write_research_md(md_path, args.edition, days, candidates, report)

    erros = [r for r in report if r[3]]
    print(f"OK research: {len(candidates)} candidatos -> {json_path.name} + {md_path.name}")
    if erros:
        print(f"Atenção: {len(erros)} feed(s) com erro:")
        for source, _, _, err in erros:
            print(f"  - {source}: {err}")
    print(f"Checkpoint 1: revise {md_path} antes de rodar generate_content.py")


if __name__ == "__main__":
    main()
