#!/usr/bin/env python3
"""Ingestão de pauta da WooW! Daily Drops: lê os feeds RSS e monta a lista de candidatos.

Uso:
    python3 research.py --edition 2026-w25 [--days 3]
    python3 research.py --test-feeds            # só testa as fontes, não grava nada

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
import difflib
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import yaml

try:  # feedparser só é preciso p/ baixar feeds; funções puras (build_health) importam sem ele
    import feedparser
except ImportError:
    feedparser = None

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config"
CONTENT = BASE / "content"

# UA de browser: vários feeds (WordPress, BoF) bloqueiam o agent padrão do feedparser.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TAG_RE = re.compile(r"<[^>]+>")
BRT = timezone(timedelta(hours=-3))


def load_yaml(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def strip_html(raw: str) -> str:
    """Remove tags e normaliza espaços/entidades de um resumo de feed."""
    if not raw:
        return ""
    text = html.unescape(TAG_RE.sub(" ", raw))
    return re.sub(r"\s+", " ", text).strip()


# Parâmetros que identificam de ONDE o clique veio, nunca QUAL é a matéria. Ficam no código,
# e não em YAML nem na regra da campanha, de propósito: é por esta porta que alguém apagaria
# a identidade do link sem perceber, e uma chave que colapsa demais passa a barrar matéria
# fresca — falha silenciosa e no pior sentido, porque a pauta encolhe sem ninguém ver.
TRACKING_PARAMS = frozenset((
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref",
))
_PORTA_PADRAO = {"http": "80", "https": "443"}


def canonical_url(u):
    """Chave de identidade de uma matéria a partir da URL. Uma régua só, usada pelo `dedup`
    da rodada e pela memória do que já foi publicado — duas normas divergentes seriam duas
    respostas diferentes para 'esta matéria é a mesma?'.

    Descarta esquema (http e https servem a mesma matéria), `www.`, porta padrão, barra
    final e fragmento; da querystring tira só o que é rastreio. NÃO corta a query inteira:
    publisher que usa `?p=123` como identidade teria todas as matérias colapsadas em uma,
    e a trava barraria a pauta do dia inteira.

    Caixa do path é preservada (servidor pode diferenciar); host vai para minúscula."""
    bruto = html.unescape((u or "").strip())
    if not bruto:
        return ""
    try:
        partes = urlsplit(bruto)
        if not partes.netloc:
            # link relativo ou lixo: devolve o que veio, nunca "" — chave vazia faria duas
            # entradas ruins colidirem e uma barrar a outra.
            return bruto.lower()
        host = (partes.hostname or "").lower()
        porta = partes.port
    except ValueError:
        # IPv6 malformado, porta não numérica OU fora de 0-65535. O `urlsplit` NÃO valida a
        # porta: quem valida é o acesso a `.port`, que é lazy — o ValueError nasce aqui, uma
        # linha depois de onde se espera. Fora deste try, um `:99999` num único link
        # derrubava a pesquisa inteira, e o log acusava falha do research.py sem relação
        # nenhuma com o link que a causou.
        return bruto.lower()
    if host.startswith("www."):
        host = host[4:]
    if porta is not None and str(porta) != _PORTA_PADRAO.get(partes.scheme.lower(), ""):
        host = f"{host}:{porta}"
    caminho = partes.path.rstrip("/")
    mantidos = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                if k not in TRACKING_PARAMS]
    chave = host + caminho
    if mantidos:
        chave += "?" + urlencode(sorted(mantidos))
    return chave


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
        link = canonical_url(it.get("link"))
        title_key = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:80]
        if (link and link in seen_link) or (title_key and title_key in seen_title):
            continue
        if link:
            seen_link.add(link)
        if title_key:
            seen_title.add(title_key)
        out.append(it)
    return out


# ------------------------------------------------------------------- memória do publicado
# O arquivo é INJETADO pelo orchestrator, já recortado pela janela e pela campanha. Este
# script não sabe o que é campanha, não fala com o GCS e não decide janela: lê, aplica e
# relata. A política de retenção mora num lugar só; uma segunda cópia dela aqui viraria
# duas respostas para "isto ainda conta como publicado?".
PUBLICADOS_NOME = "publicados.json"

# Teto de barrados que vão para o health (e daí para o espelho Firebase) e para o .md.
# O state é lido inteiro a cada consulta de edição: lista sem teto incharia o documento
# justamente no dia em que a memória barrasse a pauta toda. O stdout leva todos.
MAX_BARRADOS_RELATADOS = 20

# Espelha MIN_BLOCOS do generate_content.py de propósito, sem importar: o piso de verdade
# é lá, onde a edição é recusada. Aqui o número só decide QUANDO GRITAR — a pesquisa nunca
# se recusa a entregar pauta, porque uma trava que também barra esconde a diferença entre
# "a guarda funcionou" e "a guarda quebrou e recusou tudo".
POOL_MINIMO = 3

# O orchestrator devolve md[:4000] como resumo do Checkpoint 1 (stage "research"). A seção
# de barrados sai perto do topo e é cortada por este orçamento: sem ele, ela sumiria do
# resumo exatamente no dia de muitos barrados, que é o dia em que alguém precisa lê-la.
SUMMARY_LIMITE = 4000
_ORCAMENTO_BARRADOS = 3700

# Stopwords pt/en: palavras presentes em quase toda headline, que inflariam o Jaccard de
# dois títulos sem relação. Palavras de até 2 letras já caem no filtro de tamanho.
_STOPWORDS = frozenset("""
the and for with from that this into out its has have had will are was were but not you
your new now how why who all can get gets says say after over than about more most first
para com que uma dos das por não mais como sobre seu sua seus suas pelo pela pelos pelas
nos nas num numa são foi ser tem ter isso esse essa este esta aos até ainda entre depois
""".split())

_PALAVRA_RE = re.compile(r"[^\W_]+", re.UNICODE)


def load_publicados():
    """Lê a memória do que já saiu. Devolve o documento cru, ou {} quando não dá para ler.

    FAIL-OPEN, e não por preguiça: memória quebrada significa "não barra ninguém", nunca
    "barra tudo". Uma trava que recusa a pauta inteira quando o arquivo corrompe é
    indistinguível, no log, de uma trava funcionando bem num dia sem matéria nova."""
    caminho = CONFIG / PUBLICADOS_NOME
    try:
        bruto = caminho.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Sem {PUBLICADOS_NOME}: nenhum candidato será barrado por repetição.")
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        print(f"AVISO: {PUBLICADOS_NOME} ilegível ({type(exc).__name__}: {exc}). Nada barrado.")
        return {}
    try:
        doc = json.loads(bruto)
    except json.JSONDecodeError as exc:
        # Só JSONDecodeError: um `except ValueError` largo aqui engoliria erro de outra
        # natureza e transformaria defeito de código em "memória vazia, segue o jogo".
        print(f"AVISO: {PUBLICADOS_NOME} não é JSON válido ({exc}). Nada barrado.")
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("links"), list):
        print(f"AVISO: {PUBLICADOS_NOME} em formato inesperado ({type(doc).__name__} sem "
              f"lista `links`). Nada barrado.")
        return {}
    return doc


def indice_publicados(doc):
    """{chave canônica -> entrada} da memória, pela MESMA régua do dedup (`canonical_url`).

    Chave vazia fica de fora: ela casaria com todo candidato sem link e barraria a pauta
    por engano. Repetição na memória mantém a PRIMEIRA entrada — se a mesma URL saiu duas
    vezes, a edição mais antiga é a que explica o barramento."""
    idx = {}
    for entrada in (doc or {}).get("links") or []:
        if not isinstance(entrada, dict):
            continue
        chave = canonical_url(entrada.get("link"))
        if chave:
            idx.setdefault(chave, entrada)
    return idx


def separa_publicados(items, idx):
    """(novos, barrados). Função pura: os aprovados saem como os MESMOS dicionários que
    entraram, na mesma ordem, e nada em `items` é mutado.

    O barrado sai como cópia com `publicado_em` (a edição) e `publicado_date`, para o
    relatório dizer QUANDO saiu: "já publicado" sem data manda o operador procurar à mão
    em qual edição foi, e aí ele para de ler o relatório."""
    novos, barrados = [], []
    for it in items:
        chave = canonical_url(it.get("link"))
        entrada = idx.get(chave) if chave else None
        if entrada is None:
            novos.append(it)
            continue
        marcado = dict(it)
        marcado["publicado_em"] = entrada.get("edition", "")
        marcado["publicado_date"] = entrada.get("date", "")
        barrados.append(marcado)
    return novos, barrados


def _tokens_titulo(titulo):
    return {p for p in _PALAVRA_RE.findall((titulo or "").lower())
            if len(p) > 2 and p not in _STOPWORDS}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parecidos_no_historico(candidates, doc, jac=0.45, seq=0.72):
    """Aponta candidato APROVADO cujo título parece com algo já publicado. NÃO REMOVE NADA.

    Por que só relata: medi as 35 edições já publicadas e todo par de headline parecida
    tinha a mesma URL por trás, ou seja, o ganho marginal desta camada sobre a trava de
    URL foi ZERO. Ela entra como instrumento, para descobrir se existe na prática o caso
    da mesma história vinda de dois publishers — URLs diferentes, headline quase igual —,
    que é o único buraco que a chave de URL não cobre. Vira trava quando o relatório
    mostrar caso real, não antes: filtro promovido sem caso medido é exatamente como a
    pauta encolhe sem ninguém saber por quê.

    Dois sinais, OU entre eles, ambos stdlib: Jaccard de tokens pega reordenação e corte
    ("X compra Y" vs "Y é comprada por X"), e SequenceMatcher pega variação de grafia e
    sufixo que muda os tokens sem mudar a matéria. Um candidato rende no máximo uma linha,
    a do par mais forte — o relatório é para ler, não para auditar."""
    memoria = [e for e in (doc or {}).get("links") or []
               if isinstance(e, dict) and (e.get("titulo") or "").strip()]
    achados = []
    for c in candidates:
        titulo = (c.get("title") or "").strip()
        if not titulo:
            continue
        toks = _tokens_titulo(titulo)
        melhor = None
        for entrada in memoria:
            outro = entrada["titulo"].strip()
            j = _jaccard(toks, _tokens_titulo(outro))
            s = difflib.SequenceMatcher(None, titulo.lower(), outro.lower()).ratio()
            if j < jac and s < seq:
                continue
            if melhor is None or max(j, s) > max(melhor[1], melhor[2]):
                melhor = (entrada, j, s)
        if melhor is None:
            continue
        entrada, j, s = melhor
        achados.append({
            "titulo": titulo,
            "fonte": c.get("source", ""),
            "parecido_com": entrada["titulo"].strip(),
            "publicado_em": entrada.get("edition", ""),
            "jaccard": round(j, 3),
            "seq": round(s, 3),
        })
    return achados


def avalia_pool(candidates, barrados, minimo, alerta_em=None):
    """Texto do alerta, ou None. GRITA e devolve texto — não remove item, não afrouxa
    limiar e não se desliga sozinha. Quem falha no piso real é o generate (MIN_BLOCOS),
    onde o contrato de "não publicar edição capenga" já está escrito e testado; uma
    segunda trava aqui só criaria dois lugares para desligar a mesma proteção.

    Barrados > aprovados é o sinal indireto, e o mais útil dos três: ou a janela da
    memória está grande demais, ou a chave de URL passou a colapsar matérias distintas.
    Nos dois casos a pauta encolhe sem erro nenhum aparecer no log."""
    aprovados, bloqueados = len(candidates), len(barrados)
    if aprovados == 0:
        return (f"ALERTA: pesquisa terminou com pool VAZIO ({bloqueados} barrado(s) por já "
                f"publicados). O generate vai recusar a edição no piso de {minimo} blocos. "
                f"Confira a janela da memória e o --days antes de rodar de novo.")
    if aprovados < minimo:
        return (f"ALERTA: pool com {aprovados} candidato(s), abaixo do piso de {minimo} do "
                f"generate ({bloqueados} barrado(s) por já publicados). A edição não sai.")
    if bloqueados > aprovados:
        return (f"ALERTA: {bloqueados} barrado(s) contra {aprovados} aprovado(s). A maior "
                f"parte da pauta veio da memória: suspeite de janela grande demais ou de "
                f"chave de URL colapsando matérias distintas antes de culpar as fontes.")
    if alerta_em and aprovados < alerta_em:
        # Avisar só ao encostar no piso é avisar quando já não há o que fazer. Aqui a edição
        # ainda sai; o que o operador ganha é tempo de agir antes do dia em que não sair.
        return (f"Atenção: pool magro, {aprovados} candidato(s) ({bloqueados} barrado(s) por "
                f"já publicados). A edição sai, mas o Escritor escolhe as notas de um pool "
                f"apertado. Confira as fontes em `sources list` e a janela em "
                f"`curadoria status`.")
    return None


def enabled_feeds(feeds):
    """Só as fontes ativas. `enabled` ausente vale True (retrocompat com o YAML antigo)."""
    return [f for f in (feeds or []) if f.get("enabled", True)]


def report_as_dicts(report):
    """Converte o report de collect() em JSON serializável (uma linha por fonte)."""
    return [{"source": s, "found": found, "kept": kept, "error": err}
            for (s, found, kept, err) in report]


def build_health(report, candidates, barrados=None, alerta=None, parecidos=None):
    """Resumo de saúde da pesquisa para o painel: nº de candidatos, total de feeds, os que
    erraram (403/timeout/etc.) e o que a memória de publicados barrou. Função pura —
    testável sem rede. `report` é a lista de tuplas (source, encontrados, dentro_da_janela,
    erro) devolvida por collect().

    Os três últimos são opcionais e continuam opcionais: há chamador anterior à curadoria
    passando só dois posicionais, e um parâmetro obrigatório novo quebraria o painel em vez
    de acrescentar informação a ele."""
    feed_errors = [{"source": s, "error": err} for (s, _f, _k, err) in report if err]
    barrados = barrados or []
    parecidos = parecidos or []
    return {
        "candidates": len(candidates),
        "feeds_total": len(report),
        "feed_errors": feed_errors,
        "barrados": len(barrados),
        # Só os campos que o painel mostra, e cortada em MAX_BARRADOS_RELATADOS: o item de
        # pauta inteiro traz o `content` do feed, e a lista completa inflaria o state (lido
        # inteiro a cada consulta de edição) e o espelho Firebase.
        "barrados_itens": [{"titulo": b.get("title", ""), "fonte": b.get("source", ""),
                            "link": b.get("link", ""),
                            "publicado_em": b.get("publicado_em", ""),
                            "publicado_date": b.get("publicado_date", "")}
                           for b in barrados[:MAX_BARRADOS_RELATADOS]],
        "pool_alerta": bool(alerta),
        "alerta": alerta,
        "parecidos": len(parecidos),
        "researched_at": datetime.now(BRT).isoformat(timespec="seconds"),
    }


def _peso(lines):
    """Quanto estas linhas ocupam no arquivo final, com o \\n de cada uma."""
    return sum(len(l) + 1 for l in lines)


def write_research_md(path, edition, days, candidates, report,
                      barrados=None, alerta=None, parecidos=None, publicados=None):
    barrados = barrados or []
    parecidos = parecidos or []
    lines = [f"# Pauta WooW! Daily Drops — {edition}", ""]
    if alerta:
        # No topo: o resumo do Checkpoint 1 é lido de cima para baixo, e alerta em rodapé
        # é alerta que ninguém leu.
        lines.append(f"> **{alerta}**")
        lines.append("")
    lines.append(f"Janela: últimos {days} dias. Candidatos após dedup: **{len(candidates)}**.")
    lines.append("")
    if barrados:
        # Antes da cobertura por fonte porque o orchestrator manda md[:4000] como resumo do
        # Checkpoint 1: no fim do arquivo, esta seção seria cortada no dia de muitos
        # barrados. O laço respeita o mesmo orçamento e corta a LISTA, nunca a seção.
        campanha = (publicados or {}).get("campanha") or "campanha não declarada"
        janela = (publicados or {}).get("janela_dias")
        janela_txt = f"últimos {janela} dias" if janela else "janela não declarada"
        lines.append("## Já publicados (barrados)")
        lines.append("")
        lines.append(f"Memória da campanha `{campanha}`, {janela_txt}. "
                     f"**{len(barrados)}** item(ns) fora da pauta por já terem saído.")
        lines.append("")
        mostrados = 0
        for b in barrados[:MAX_BARRADOS_RELATADOS]:
            saiu = b.get("publicado_em") or "edição não registrada"
            data = (b.get("publicado_date") or "")[:10]
            cabeca = (f"- **{(b.get('title') or 'sem título')[:140]}** — "
                      f"{b.get('source', '')} — saiu em {saiu}")
            if data:
                cabeca += f" ({data})"
            corpo = f"  {(b.get('link') or '')[:200]}"
            if _peso(lines) + _peso([cabeca, corpo]) > _ORCAMENTO_BARRADOS:
                break
            lines.append(cabeca)
            lines.append(corpo)
            mostrados += 1
        if mostrados < len(barrados):
            lines.append(f"- … e mais {len(barrados) - mostrados} barrado(s): a lista inteira "
                         f"sai no stdout do research (log do Cloud Run).")
        lines.append("")
    if parecidos:
        lines.append("## Parecidos com o já publicado (não barrados)")
        lines.append("")
        lines.append("Nenhum destes saiu da pauta: esta camada só relata. Ela existe para "
                     "descobrir a mesma história vinda de dois publishers, que é o caso que "
                     "a chave de URL não pega.")
        lines.append("")
        for p in parecidos[:MAX_BARRADOS_RELATADOS]:
            lines.append(f"- **{p['titulo'][:140]}** ({p['fonte']}) ≈ "
                         f"“{p['parecido_com'][:140]}” — saiu em "
                         f"{p['publicado_em'] or 'edição não registrada'} · "
                         f"jaccard {p['jaccard']}, seq {p['seq']}")
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
    ap.add_argument("--edition", default=None, help="rótulo da edição, ex: 2026-w25")
    ap.add_argument("--days", type=int, default=None, help="override da janela de recência")
    ap.add_argument("--test-feeds", action="store_true",
                    help="baixa cada fonte e imprime o relatório em JSON; não grava nada")
    args = ap.parse_args()

    if feedparser is None:
        sys.exit("Faltam dependências. Rode: pip3 install pyyaml feedparser")

    feeds_cfg = load_yaml("feeds.yaml")
    nl_cfg = load_yaml("newsletter.yaml")
    feeds = enabled_feeds(feeds_cfg["feeds"])
    days = args.days if args.days is not None else nl_cfg["research"]["days_lookback"]
    max_per_source = nl_cfg["research"]["max_per_source"]

    if args.test_feeds:
        # Mesmo caminho de código da pesquisa real (fetch_feed + collect), de propósito:
        # o que interessa é como as fontes respondem DAQUI, de dentro do broker.
        _, report = collect(feeds, days, max_per_source)
        print(json.dumps({"report": report_as_dicts(report)}, ensure_ascii=False))
        return

    if not args.edition:
        sys.exit("--edition é obrigatório (ou use --test-feeds)")

    candidates, report = collect(feeds, days, max_per_source)
    candidates = dedup(candidates)
    # A curadoria roda DEPOIS do collect, nunca dentro dele: collect é o caminho comum com
    # --test-feeds, e o `report` dele é o diagnóstico por fonte que o operador lê no
    # `sources test`. Filtrar lá faria fonte saudável aparecer vazia e mandaria alguém
    # trocar uma URL que estava certa.
    publicados = load_publicados()
    candidates, barrados = separa_publicados(candidates, indice_publicados(publicados))
    parecidos = parecidos_no_historico(candidates, publicados)
    alerta = avalia_pool(candidates, barrados, POOL_MINIMO,
                         nl_cfg["research"].get("pool_min_alerta"))

    CONTENT.mkdir(exist_ok=True)
    json_path = CONTENT / f"{args.edition}.research.json"
    md_path = CONTENT / f"{args.edition}.research.md"
    json_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    write_research_md(md_path, args.edition, days, candidates, report, barrados=barrados,
                      alerta=alerta, parecidos=parecidos, publicados=publicados)
    health_path = CONTENT / f"{args.edition}.research.health.json"
    health_path.write_text(
        json.dumps(build_health(report, candidates, barrados, alerta, parecidos),
                   ensure_ascii=False, indent=2), encoding="utf-8")

    erros = [r for r in report if r[3]]
    print(f"OK research: {len(candidates)} candidatos -> {json_path.name} + {md_path.name}")
    for b in barrados:
        print(f"  JÁ PUBLICADO em {b.get('publicado_em') or 'edição não registrada'}: "
              f"{b.get('title')} ({b.get('source')}) {b.get('link')}")
    if barrados:
        print(f"Barrados pela memória de publicados: {len(barrados)}")
    if parecidos:
        print(f"Parecidos com o histórico: {len(parecidos)} (só relatório, nada removido)")
    if erros:
        print(f"Atenção: {len(erros)} feed(s) com erro:")
        for source, _, _, err in erros:
            print(f"  - {source}: {err}")
    if alerta:
        # stdout E stderr, como relata_descartes: se o stage falhar mais adiante, o
        # orchestrator anexa só o stderr ao health.last_error, e um alerta impresso apenas
        # em stdout sumiria justamente no caso em que ele importa.
        print(alerta)
        print(alerta, file=sys.stderr)
    print(f"Checkpoint 1: revise {md_path} antes de rodar generate_content.py")


if __name__ == "__main__":
    main()
