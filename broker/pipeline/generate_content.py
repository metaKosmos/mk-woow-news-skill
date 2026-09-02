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
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
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

REQUIRED_FIELDS = ["cabecalho", "titulo_edicao", "sumario", "manchete"]

# Os 5 blocos de notícia, na ordem em que a edição os apresenta. Só a manchete é
# obrigatória: com o pool curto a edição encolhe (piso em MIN_BLOCOS) em vez de ser
# completada com item inventado, que foi o defeito de 24/08, 31/08 e 02/09 (MAR-483).
BLOCK_FIELDS = ["manchete", "secundaria_1", "secundaria_2", "sinal_1", "sinal_2"]
MIN_BLOCOS = 3

# A frase clicável chega marcada, sem href: quem escreve o link é este arquivo, copiando
# o campo `link` do item de pauta. O Escritor não recebe URL nenhuma (ver write_edition).
LINK_MARK_RE = re.compile(r"<strong\b[^>]*\bdata-link\b[^>]*>(.*?)</strong>",
                          re.DOTALL | re.IGNORECASE)
# href com aspas duplas, simples OU sem aspas: <a href=https://x> é HTML válido o bastante
# para o cliente de e-mail abrir, então tem que ser visível para a guarda de procedência.
ANCHOR_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)

# UA de browser na checagem de link: publisher que barra agente desconhecido devolveria
# 403 e sujaria o relatório. A checagem registra, não bloqueia.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


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
        "id": c["id"], "title": c.get("title", ""),
        "content": (c.get("content") or "")[:content_len],
        "source": c.get("source", ""), "categories": c.get("categories", ""),
        "date": (c.get("date") or "")[:10],
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
    """Redige a edição. O pool vai ENXUTO, sem o campo `link`: o Escritor aponta a fonte
    pelo `id` e não tem URL para copiar nem para inventar. A alucinação de 31/08 aconteceu
    com os links reais dentro do prompt, então tirá-los é parte da correção, não detalhe."""
    data = (f"DATA DA EDIÇÃO: {edition_date}\n\n"
            "Notícias pontuadas para esta edição (JSON):\n"
            + json.dumps(_slim(pool, 800), ensure_ascii=False))
    # etapa criativa: deixa o Escritor raciocinar (checklist de 16 itens), com folga de output
    return gemini_json(cfg, key, cfg["model_write"], prompt, data, expect=dict,
                       thinking_budget=cfg.get("write_thinking_budget", 4096),
                       max_tokens=16384)


# ------------------------------------------------------------------ procedência do link
def _norm_link(u):
    """Normaliza para comparar href com link de pauta: entidade HTML, barra final e caixa.

    O unescape não é detalhe: o href sai do corpo já escapado (`&amp;`, `&quot;`) e o link
    da pauta é cru, então sem ele um link legítimo com querystring seria lido como forasteiro."""
    return html.unescape((u or "").strip()).rstrip("/").lower()


def _hrefs(corpo):
    """Todos os destinos de <a> do corpo, com ou sem aspas."""
    return [next(g for g in m.groups() if g is not None) for m in ANCHOR_RE.finditer(corpo or "")]


def _texto(valor):
    """Campo de texto puro que o template interpola SEM escape (autoescape está off em
    render_newsletter.jinja_env). Tag aqui vira HTML vivo: um <a href> no sumário, na
    headline ou no título sai clicável no e-mail com procedência zero, que é o mesmo dano
    que bind_links existe para impedir."""
    return strip_html(valor) if isinstance(valor, str) else ""


def _descarte(campo, headline, source_id, motivo, detalhe=""):
    return {"campo": campo, "headline": headline, "source_id": source_id,
            "motivo": motivo, "detalhe": detalhe}


def _como_id(valor):
    """Aceita 3 e "3" (o modelo alterna), recusa o resto. None se não for id."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.strip().lstrip("-").isdigit():
        return int(valor.strip())
    return None


def bind_links(content, pool):
    """Injeta o href de cada bloco copiando o `link` do item de pauta que ele aponta.

    Devolve (blocos, descartes). Campo AUSENTE não é descarte: é edição curta, que passou
    a ser permitida. Bloco PRESENTE que não prova de onde veio é descartado, porque foi
    exatamente assim que item sem fonte virou e-mail enviado (MAR-483)."""
    by_id = {_como_id(c.get("id")): c for c in pool if _como_id(c.get("id")) is not None}
    blocos, descartes, usados = [], [], set()
    for campo in BLOCK_FIELDS:
        bloco = content.get(campo)
        if not isinstance(bloco, dict):
            continue
        headline = bloco.get("headline", "")
        sid = _como_id(bloco.get("source_id"))
        if sid is None:
            descartes.append(_descarte(campo, headline, bloco.get("source_id"),
                                       "source_id_ausente"))
            continue
        item = by_id.get(sid)
        if item is None:
            descartes.append(_descarte(campo, headline, sid, "source_id_fora_do_pool"))
            continue
        if sid in usados:
            # duas notas da mesma matéria sairiam com o MESMO link e procedência limpa:
            # a edição pareceria ter 5 fontes tendo uma só.
            descartes.append(_descarte(campo, headline, sid, "source_id_repetido"))
            continue
        link = (item.get("link") or "").strip()
        if not link:
            descartes.append(_descarte(campo, headline, sid, "item_sem_link",
                                       item.get("title", "")))
            continue
        corpo = bloco.get("corpo") or ""
        marcas = LINK_MARK_RE.findall(corpo)
        if not marcas:
            descartes.append(_descarte(campo, headline, sid, "sem_marcador_de_link"))
            continue
        if len(marcas) > 1:
            descartes.append(_descarte(campo, headline, sid, "marcadores_de_link_demais",
                                       f"{len(marcas)} marcadores"))
            continue
        href = html.escape(link, quote=True)
        corpo = LINK_MARK_RE.sub(
            lambda m: f'<strong><a href="{href}">{m.group(1)}</a></strong>', corpo, count=1)
        usados.add(sid)
        blocos.append({"campo": campo, "headline": headline, "corpo": corpo,
                       "source_id": sid, "source": item.get("source", ""),
                       "link": link, "titulo_fonte": item.get("title", "")})
    return blocos, descartes


def enforce_provenance(blocos, pool):
    """Rede de segurança: o prompt proíbe <a>, mas se o Escritor escrever um assim mesmo,
    o destino tem que estar na pauta do dia. Mede procedência, não formatação: link que
    ESTÁ no pool passa, senão a guarda recusaria tudo e pareceria estar funcionando."""
    permitidos = {_norm_link(c.get("link")) for c in pool if (c.get("link") or "").strip()}
    ok, descartes = [], []
    for b in blocos:
        fora = [h for h in _hrefs(b["corpo"]) if _norm_link(h) not in permitidos]
        if fora:
            descartes.append(_descarte(b["campo"], b["headline"], b["source_id"],
                                       "link_fora_do_pool", fora[0][:200]))
        else:
            ok.append(b)
    return ok, descartes


def recompose(content, blocos):
    """Recoloca os blocos sobreviventes nos primeiros campos e recorta o sumário.

    O sumário é posicional (item 1 = manchete, item 2 = secundária 1...), então só dá para
    recortá-lo quando o tamanho bate com o número de blocos entregues. Quando não bate, ele
    volta como veio e o validate() recusa: adivinhar a correspondência publicaria chamada
    de um item em cima de outro."""
    novo = {k: v for k, v in content.items() if k not in BLOCK_FIELDS}
    novo["cabecalho"] = _texto(content.get("cabecalho"))
    novo["titulo_edicao"] = _texto(content.get("titulo_edicao"))
    entregues = [c for c in BLOCK_FIELDS if isinstance(content.get(c), dict)]
    sumario = content.get("sumario")
    sumario = [_texto(s) for s in sumario] if isinstance(sumario, list) else []
    for i, b in enumerate(blocos):
        novo[BLOCK_FIELDS[i]] = {"headline": _texto(b["headline"]), "corpo": b["corpo"],
                                 "source_id": b["source_id"]}
    if len(sumario) == len(entregues):
        pos = {campo: i for i, campo in enumerate(entregues)}
        novo["sumario"] = [sumario[pos[b["campo"]]] for b in blocos]
    else:
        novo["sumario"] = sumario
    return novo


def apply_provenance(content, pool):
    """bind -> enforce -> recompose. Devolve (content, provenance)."""
    blocos, d1 = bind_links(content, pool)
    blocos, d2 = enforce_provenance(blocos, pool)
    descartes = d1 + d2
    novo = recompose(content, blocos)
    # `titulo_edicao` é o ASSUNTO do e-mail e o H1 da página, e foi escrito para a manchete.
    # Se ela caiu, outra notícia subiu para o topo e o assunto passa a prometer matéria que
    # não está dentro: o mesmo dano da MAR-483 entrando pela porta do lado. A headline da
    # manchete que sobrou é menos trabalhada como chamada, mas é verdadeira.
    titulo_substituido = False
    if blocos and any(d["campo"] == "manchete" for d in descartes):
        novo["titulo_edicao"] = novo["manchete"]["headline"]
        titulo_substituido = True
    prov = {"pool_size": len(pool), "publicados": len(blocos),
            "titulo_substituido": titulo_substituido,
            "itens": [{"campo": BLOCK_FIELDS[i], "source_id": b["source_id"],
                       "source": b["source"], "link": b["link"],
                       "titulo_fonte": b["titulo_fonte"]} for i, b in enumerate(blocos)],
            "descartados": descartes}
    return novo, prov


# ------------------------------------------------------------------ checagem de link
def _http_status(url, timeout):
    """(status, url_final) seguindo redirecionamento. Isolado para o teste trocar."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.geturl()
    except urllib.error.HTTPError as e:  # 404/403 são resposta, não falha do instrumento
        return e.code, e.url or url


def check_links(itens, timeout=8):
    """Confere os links publicados e REGISTRA o resultado. Não bloqueia nada.

    Motivo de não bloquear: publisher que barra IP de datacenter devolve 403 para link
    legítimo, e o broker já tem esse caso com Business of Fashion. Um veto por HTTP
    derrubaria item honesto justamente nos dias de pool curto. Suspeita é raiz de domínio
    (link sem caminho de matéria) e 4xx que não seja 403."""
    rel = {"checked_at": datetime.now().isoformat(timespec="seconds"),
           "itens": [], "sem_path": [], "suspeitos": []}
    for it in itens:
        campo, link = it.get("campo", ""), (it.get("link") or "")
        sem_path = not urllib.parse.urlsplit(link).path.strip("/")
        linha = {"campo": campo, "link": link, "status": None, "final_url": "",
                 "erro": "", "sem_path": sem_path}
        try:
            linha["status"], linha["final_url"] = _http_status(link, timeout)
        except Exception as exc:  # noqa: BLE001 — rede caída não vira acusação de link
            linha["erro"] = f"{type(exc).__name__}: {exc}"[:200]
        rel["itens"].append(linha)
        if sem_path:
            rel["sem_path"].append(campo)
        st = linha["status"]
        morto = st is not None and 400 <= st < 500 and st != 403
        if sem_path or morto:
            rel["suspeitos"].append({"campo": campo, "link": link, "status": st,
                                     "motivo": "raiz_de_dominio" if sem_path else "http_4xx"})
    return rel


def relata_descartes(provenance):
    """Imprime o que foi descartado e por quê, em stdout E stderr.

    O stderr não é redundância: quando validate() derruba o script, `_run_script` anexa
    apenas `proc.stderr` ao RuntimeError que vira `health.last_error`, então o motivo
    impresso só em stdout some justamente no caso em que ele importa."""
    for d in provenance.get("descartados") or []:
        linha = f"  DESCARTADO {d['campo']} ({d['motivo']}): {d['headline']}"
        print(linha)
        print(linha, file=sys.stderr)
    if provenance.get("titulo_substituido"):
        aviso = "  AVISO: a manchete original caiu; o assunto virou a headline da manchete nova"
        print(aviso)
        print(aviso, file=sys.stderr)
    redigidos = provenance.get("publicados", 0) + len(provenance.get("descartados") or [])
    print(f"Itens com fonte confirmada: {provenance.get('publicados', 0)} de {redigidos} redigidos")


def _diagnostico(provenance):
    """Diz ONDE olhar quando a edição é recusada. Sem isto a mensagem sugeria ampliar a
    janela de dias, que é o conselho errado no caso mais provável: pauta cheia e o Escritor
    ignorando a marcação do prompt."""
    motivos = [d.get("motivo") for d in ((provenance or {}).get("descartados") or [])]
    if not motivos:
        return "Nenhuma nota foi redigida: veja a resposta do modelo."
    do_prompt = {"sem_marcador_de_link", "marcadores_de_link_demais", "source_id_ausente"}
    if set(motivos) <= do_prompt:
        return (f"Todos os {len(motivos)} descartes são de marcação ({', '.join(sorted(set(motivos)))}): "
                f"o Escritor não seguiu o prompt. NÃO é escassez de pauta, não adianta mexer em --days.")
    return f"Motivos dos {len(motivos)} descartes: {', '.join(sorted(set(motivos)))}."


def validate(content, provenance=None):
    """Recusa a edição em vez de publicar item sem fonte. O generate falhando é o que
    impede o envio: run_daily encadeia research -> generate -> send, e a exceção para
    antes do send.

    A contagem de blocos vem ANTES dos campos obrigatórios de propósito: com zero blocos a
    `manchete` também some, e a mensagem "sem os campos: ['manchete']" mandava procurar JSON
    quebrado do modelo em vez do motivo real."""
    blocos = [c for c in BLOCK_FIELDS if isinstance(content.get(c), dict)]
    if len(blocos) < MIN_BLOCOS:
        sys.exit(f"Só {len(blocos)} item(ns) com fonte confirmada, e o piso é {MIN_BLOCOS}. "
                 f"Edição NÃO publicada. {_diagnostico(provenance)}")
    missing = [f for f in REQUIRED_FIELDS if f not in content]
    if missing:
        sys.exit(f"Conteúdo gerado sem os campos: {missing}")
    if blocos != BLOCK_FIELDS[:len(blocos)]:
        sys.exit(f"Blocos fora de ordem depois do corte: {blocos}")
    if not isinstance(content.get("sumario"), list) or len(content["sumario"]) != len(blocos):
        sys.exit(f"Sumário com {len(content.get('sumario') or [])} itens para "
                 f"{len(blocos)} notícia(s). Sem alinhamento, a chamada iria no item errado.")


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
        bloco = content.get(field)
        if not isinstance(bloco, dict):
            continue  # edição curta: o bloco não existe, e isso agora é legítimo
        out.append(f"{label}: {bloco['headline']}")
        out.append(strip_html(bloco["corpo"]))
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Geração de conteúdo WooW! Daily Drops")
    ap.add_argument("--edition", required=True, help="rótulo da edição, ex: 2026-w25")
    ap.add_argument("--date", default=None, help='data por extenso; default = hoje (pt-BR)')
    ap.add_argument("--no-classify", action="store_true", help="pula o filtro de território")
    ap.add_argument("--skip-link-check", action="store_true",
                    help="não bate HTTP nos links publicados (a checagem só registra)")
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
    content, provenance = apply_provenance(content, pool)
    relata_descartes(provenance)
    validate(content, provenance)

    links = None
    if not args.skip_link_check:
        links = check_links(provenance["itens"])
        for s in links["suspeitos"]:
            print(f"  ATENÇÃO {s['campo']}: {s['motivo']} ({s['status']}) {s['link']}")

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
         "meta": meta, "content": content, "provenance": provenance,
         "link_check": {k: links[k] for k in ("checked_at", "sem_path", "suspeitos")}
         if links else None}, ensure_ascii=False, indent=2), encoding="utf-8")
    if links:  # relatório completo à parte; o _persist_content leva tudo p/ o GCS
        (CONTENT / f"{args.edition}.links.json").write_text(
            json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
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
