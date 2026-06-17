#!/usr/bin/env python3
"""Geração de conteúdo da WooW! Daily Drops: curadoria + redação via Gemini.

Uso:
    python3 generate_content.py --edition 2026-w25 [--date "quarta-feira, 18 de junho de 2026"]

Lê os candidatos revisados (content/<edition>.research.json) e roda 3 etapas Gemini,
cada uma com seu prompt versionado em config/prompts/ (editável pelo João sem tocar
no código, liga com MAR-134):

    1. classify.md  -> filtra os candidatos pelo território editorial
    2. score.md     -> pontua 0-100 e ordena (rubrica do Revisor)
    3. write.md     -> redige a edição no formato WooW (Escritor), schema fixo de 8 campos

Saídas:
    content/<edition>.json  -> conteúdo estruturado (contrato dos "drops", consumido
                               pelo template WooW na frente A do HTML)
    content/<edition>.md    -> front-matter que o send_zma.py lê + corpo legível

NÃO envia nada. A perna de envio é o send_zma.py, rodada depois, à parte.
"""
import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Faltam dependências. Rode: pip3 install pyyaml")

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config"
PROMPTS = CONFIG / "prompts"
CONTENT = BASE / "content"

# Acumulador de uso de tokens por etapa (lido pelo orchestrator/cost_tracker).
# Aditivo: não altera a lógica de curadoria/redação.
USAGE = {}

WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo"]
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]

REQUIRED_FIELDS = ["cabecalho", "titulo_edicao", "sumario", "manchete",
                   "secundaria_1", "secundaria_2", "sinal_1", "sinal_2"]


def load_env(start: Path) -> dict:
    """Lê o .envmk subindo os diretórios pais (mesma estratégia do send_zma.py)."""
    for parent in [start] + list(start.parents):
        env_path = parent / ".envmk"
        if env_path.exists():
            env = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
            return env
    sys.exit("Não encontrei .envmk subindo a partir de " + str(start))


def load_yaml(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def load_prompt(name):
    return (PROMPTS / name).read_text(encoding="utf-8")


def ptbr_date(dt: datetime) -> str:
    return f"{WEEKDAYS[dt.weekday()]}, {dt.day:02d} de {MONTHS[dt.month - 1]} de {dt.year}"


def strip_html(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def gemini_json(cfg, api_key, model, system_prompt, user_data, expect,
                thinking_budget=0, max_tokens=8192):
    """Chama Gemini via REST e devolve JSON (list ou dict). Sem SDK, só stdlib.

    Em modelos 2.5 (thinking), os tokens de raciocínio consomem maxOutputTokens. Por
    isso thinking_budget=0 nas etapas mecânicas (classify/score) evita truncar o JSON;
    a etapa criativa (write) usa budget > 0 e maxOutputTokens folgado.
    """
    url = f"{cfg['endpoint']}/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_data}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": cfg.get("temperature", 0.4),
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": thinking_budget},
        },
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini HTTP {e.code} ({model}): {e.read().decode('utf-8')[:500]}")
    um = payload.get("usageMetadata", {})
    USAGE[model] = {
        "input": um.get("promptTokenCount", 0),
        "output": um.get("candidatesTokenCount", 0),
        "thinking": um.get("thoughtsTokenCount", 0),
    }
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        sys.exit(f"Resposta Gemini sem texto ({model}): {json.dumps(payload)[:500]}")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # tenta achar o primeiro array/objeto balanceado
        m = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if not m:
            sys.exit(f"Gemini não retornou JSON válido ({model}): {text[:300]}")
        data = json.loads(m.group(1))
    if expect is list and not isinstance(data, list):
        data = data.get("items") or data.get("noticias") or list(data.values())
    return data


def _slim(items, content_len):
    """Versão enxuta para mandar ao LLM: só o necessário para classificar/pontuar."""
    return [{
        "id": c["id"], "title": c["title"], "content": c["content"][:content_len],
        "source": c["source"], "categories": c["categories"], "date": c["date"][:10],
    } for c in items]


def _ids_from(result):
    """Aceita [3,7] ou [{"id":3},...] e devolve o conjunto de ids."""
    ids = set()
    for r in result:
        ids.add(r if isinstance(r, int) else r.get("id"))
    return {i for i in ids if i is not None}


def classify(cfg, key, prompt, candidates):
    """Filtra pelo território. O LLM devolve só os ids aceitos; reanexamos por id."""
    data = "Notícias para classificar (JSON):\n" + json.dumps(_slim(candidates, 500), ensure_ascii=False)
    result = gemini_json(cfg, key, cfg["model_classify"], prompt, data, expect=list,
                         thinking_budget=0, max_tokens=2048)
    accepted = _ids_from(result)
    return [c for c in candidates if c["id"] in accepted]


def score(cfg, key, prompt, items):
    """Pontua 0-100. O LLM devolve {id, score, ...}; mergeamos no item completo por id."""
    data = "Notícias para avaliar (JSON):\n" + json.dumps(_slim(items, 800), ensure_ascii=False)
    result = gemini_json(cfg, key, cfg["model_score"], prompt, data, expect=list,
                         thinking_budget=0, max_tokens=8192)
    by_id = {c["id"]: c for c in items}
    scored = []
    for r in result:
        item = by_id.get(r.get("id"))
        if item:
            scored.append({**item, "score": r.get("score", 0),
                           "score_justification": r.get("score_justification", ""),
                           "low_relevance": r.get("low_relevance", False)})
    return sorted(scored, key=lambda x: x.get("score", 0), reverse=True)


def write_edition(cfg, key, prompt, pool, edition_date):
    data = (f"DATA DA EDIÇÃO: {edition_date}\n\n"
            "Notícias pontuadas para esta edição (JSON):\n"
            + json.dumps(pool, ensure_ascii=False))
    # etapa criativa: deixa o Escritor raciocinar (checklist de 16 itens), com folga de output
    return gemini_json(cfg, key, cfg["model_write"], prompt, data, expect=dict,
                       thinking_budget=cfg.get("write_thinking_budget", 4096),
                       max_tokens=16384)


def validate(content):
    missing = [f for f in REQUIRED_FIELDS if f not in content]
    if missing:
        sys.exit(f"Conteúdo gerado sem os campos: {missing}")
    if not isinstance(content.get("sumario"), list) or len(content["sumario"]) != 5:
        sys.exit("Campo 'sumario' deve ter exatamente 5 itens.")


def build_md(content, meta):
    """Front-matter (lido pelo send_zma.py) + corpo legível dos 5 drops."""
    fm = {
        "subject": meta["subject"],
        "title_html": meta["subject"],
        "edition_label": meta["edition_label"],
        "edition_date": meta["edition_date"],
        "list_name": meta["list_name"],
    }
    out = ["---"]
    for k, v in fm.items():
        out.append(f'{k}: "{str(v).replace(chr(34), chr(39))}"')
    out.append("---")
    out.append("")
    out.append(content["cabecalho"])
    out.append("")
    out.append("Hoje no Drop:")
    for i, s in enumerate(content["sumario"], 1):
        out.append(f"{i}. {s}")
    out.append("")
    for field, label in [("manchete", "Manchete"), ("secundaria_1", "Secundária 1"),
                         ("secundaria_2", "Secundária 2"), ("sinal_1", "Sinal 1"),
                         ("sinal_2", "Sinal 2")]:
        bloco = content[field]
        out.append(f"{label}: {bloco['headline']}")
        out.append(strip_html(bloco["corpo"]))
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Geração de conteúdo WooW! Daily Drops")
    ap.add_argument("--edition", required=True, help="rótulo da edição, ex: 2026-w25")
    ap.add_argument("--date", default=None, help='data por extenso; default = hoje (pt-BR)')
    ap.add_argument("--no-classify", action="store_true", help="pula o filtro de território")
    args = ap.parse_args()

    env = load_env(BASE)
    nl_cfg = load_yaml("newsletter.yaml")
    gcfg = nl_cfg["gemini"]
    key = env.get(gcfg["api_key_env"])
    if not key:
        sys.exit(f"{gcfg['api_key_env']} não está no .envmk")

    research_path = CONTENT / f"{args.edition}.research.json"
    if not research_path.exists():
        sys.exit(f"Não encontrei {research_path}. Rode research.py primeiro (Checkpoint 1).")
    candidates = json.loads(research_path.read_text(encoding="utf-8"))
    if not candidates:
        sys.exit("research.json está vazio. Sem candidatos para gerar conteúdo.")
    for i, c in enumerate(candidates):
        c["id"] = i  # id estável para o LLM referenciar sem reecoar o item inteiro

    edition_date = args.date or ptbr_date(datetime.now())

    print(f"Candidatos: {len(candidates)}")
    if args.no_classify:
        in_territory = candidates
    else:
        in_territory = classify(gcfg, key, load_prompt("classify.md"), candidates)
        print(f"No território: {len(in_territory)}")
    if not in_territory:
        sys.exit("Nenhum candidato passou no classificador. Revise a pauta ou amplie a janela.")

    scored = score(gcfg, key, load_prompt("score.md"), in_territory)
    pool = scored[: gcfg.get("pool_to_writer", 8)]
    print(f"Pontuados: {len(scored)} | enviados ao Escritor: {len(pool)}")
    top = pool[0]
    print(f"Top score: {top.get('score')} — {top.get('title', '')[:70]}")

    content = write_edition(gcfg, key, load_prompt("write.md"), pool, edition_date)
    validate(content)

    deliv = nl_cfg["delivery"]
    meta = {
        "subject": content["titulo_edicao"],
        "edition_label": f"Edição {args.edition}",
        "edition_date": datetime.now().strftime("%Y-%m-%d"),
        "list_name": deliv["list_name"],
        "from_email": deliv["from_email"],
        "from_name": deliv["from_name"],
        "topic_id": deliv["topic_id"],
    }

    CONTENT.mkdir(exist_ok=True)
    json_path = CONTENT / f"{args.edition}.json"
    md_path = CONTENT / f"{args.edition}.md"
    json_path.write_text(json.dumps(
        {"edition": args.edition, "generated_at": datetime.now().isoformat(),
         "meta": meta, "content": content}, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_md(content, meta), encoding="utf-8")

    usage_path = CONTENT / f"{args.edition}.usage.json"
    step_by_model = {
        gcfg["model_classify"]: "classify",
        gcfg["model_score"]: "score",
        gcfg["model_write"]: "write",
    }
    usage_out = {}
    for model, u in USAGE.items():
        usage_out[step_by_model.get(model, model)] = u
    if usage_path.exists():
        prev = json.loads(usage_path.read_text(encoding="utf-8"))
        prev.update(usage_out); usage_out = prev
    usage_path.write_text(json.dumps(usage_out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK conteúdo: {json_path.name} + {md_path.name}")
    print(f"Subject: {content['titulo_edicao']}")
    print("Checkpoint 2 (HTML+mídias) e Checkpoint 3 (disparo) são passos seguintes.")


if __name__ == "__main__":
    main()
