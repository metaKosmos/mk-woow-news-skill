#!/usr/bin/env python3
"""woow.py — CLI da skill woow-news. Renderiza a gaveta e dirige o pipeline via broker.

Uso:
  python scripts/woow.py status
  python scripts/woow.py queue
  python scripts/woow.py metrics
  python scripts/woow.py run --edition 2026-06-17 [--stage research|generate|send]
  python scripts/woow.py add-pauta --edition 2026-06-17 --title "..." --content "..." --link "..."
  python scripts/woow.py sync
  python scripts/woow.py list-lists
  python scripts/woow.py create-list --name "Time mK Daily Drops" --emails-file team.txt
  python scripts/woow.py set-list --list-key <KEY>   # ou --name "Time mK Daily Drops"
  python scripts/woow.py create-campaign --edition 2026-07-01 --type manual_html \
      --html campanha.html --subject "..." --preheader "..." --list-key <KEY> [--campanha daily-drops]
  python scripts/woow.py list-senders
  python scripts/woow.py set-sender --from-email patrick@metakosmos.com.br --from-name "WooW!"
  python scripts/woow.py set-html --edition 2026-07-01 --html novo.html
  python scripts/woow.py versions
  python scripts/woow.py release --notes "fontes RSS viraram autosservico"
  python scripts/woow.py sources list [--campanha daily-drops] | test [--name "Fast Company"]
  python scripts/woow.py sources add --name "Retail Dive" --url https://www.retaildive.com/feeds/news/
  python scripts/woow.py sources set-url --name "Fast Company" --url https://www.fastcompany.com/latest/rss
  python scripts/woow.py sources enable | disable | remove --name "E-Commerce Brasil"
  python scripts/woow.py curadoria list
  python scripts/woow.py curadoria criar --campanha woow-beauty [--nome "..."] [--copiar-de daily-drops]
  python scripts/woow.py curadoria set [--campanha X] [--janela 14] [--titulo relatorio|on|off]
  python scripts/woow.py curadoria status | historico [--campanha X] [--dias 14]
  python scripts/woow.py curadoria bloquear --link URL [--campanha X] [--motivo "..."]
  python scripts/woow.py curadoria liberar --link URL [--campanha X]
  python scripts/woow.py curadoria remover --campanha woow-beauty
  python scripts/woow.py curadoria rebuild
  python scripts/woow.py schedule status
  python scripts/woow.py schedule set --time 10:00 --days diario [--until 2026-07-07]
  python scripts/woow.py schedule on | off
  python scripts/woow.py schedule auto-send on | off
"""
import argparse, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker_client as bc  # noqa: E402

STAGE_GLYPH = {"sent": "✓ Enviado   ", "ready": "◷ Pronto    ", "generated": "○ Gerado    ",
               "researched": "○ Pesquisado", "empty": "— Vazio     "}

# Campanha padrão do broker. Existe aqui só para a renderização saber o que NÃO precisa
# dizer: `status` roda todo dia e não vai gastar uma chamada a /curadoria para descobrir
# que a edição é do Daily Drops. Quem decide a padrão continua sendo o broker.
CAMPANHA_PADRAO = "daily-drops"


def cmd_status(_):
    q = bc.queue()
    print("WooW! Daily Drops — Gaveta\n" + "━" * 26)
    for e in q["editions"]:
        glyph = STAGE_GLYPH.get(e["stage"], e["stage"])
        bits = []
        if e.get("open_rate"):
            bits.append(f"open {round(e['open_rate']*100)}%")
        if e.get("html_versions", 0) > 1:
            bits.append(f"{e['html_versions']} versões HTML")
        # Procedência (MAR-483): edição com menos de 5 itens é legítima, mas quem opera
        # precisa ver que houve descarte e por qual motivo procurar no queue.
        if e.get("itens") is not None and e["itens"] < 5:
            bits.append(f"{e['itens']} itens")
        if e.get("descartados"):
            motivos = ", ".join(e.get("motivos") or []) or "motivo não registrado"
            bits.append(f"{e['descartados']} descartado(s): {motivos}")
        # Barrado é o oposto de descartado: o item nem chegou a virar bloco, porque já
        # tinha saído. Sem esta linha, a edição curta parecia dia fraco de notícia.
        if e.get("barrados"):
            bits.append(f"{e['barrados']} barrado(s) por repetição")
        if (e.get("campanha") or CAMPANHA_PADRAO) != CAMPANHA_PADRAO:
            bits.append(f"campanha {e['campanha']}")
        if e.get("links_suspeitos"):
            bits.append(f"⚠ {e['links_suspeitos']} link(s) suspeito(s)")
        print(f"{glyph} {e['edition']}   {e.get('date',''):10}   {'  ·  '.join(bits)}")
    c = Counter(e["stage"] for e in q["editions"])
    print(f"\nCobertura: {c.get('ready',0)} pronto · {c.get('generated',0)} gerado · "
          f"{c.get('researched',0)} pesquisado · {c.get('sent',0)} enviado")


def cmd_queue(_):
    import json
    print(json.dumps(bc.queue(), ensure_ascii=False, indent=2))


def cmd_metrics(_):
    for e in bc.metrics()["editions"]:
        m, cost = e.get("metrics", {}), e.get("cost", {})
        print(f"{e['edition']} — {e.get('subject','')}")
        print(f"  open {m.get('open_rate')} · click {m.get('click_rate')} · bounce {m.get('bounce_rate')}"
              f" · custo R$ {round(cost.get('total_brl',0),2)}")


def _render_research(r):
    """Checkpoint 1: a pauta mais o que a curadoria tirou dela.

    O que foi barrado sai FORA do summary porque o summary é cortado no broker, e o dia
    de muitos barrados é justamente o dia em que o corte comeria o que interessa."""
    print(r.get("summary", "")[:2000])
    if r.get("barrados"):
        campanha = r.get("campanha") or CAMPANHA_PADRAO
        print(f"\nCuradoria ({campanha}): {r['barrados']} item(ns) fora da pauta por já terem saído")
        for it in (r.get("barrados_itens") or [])[:10]:
            quando = it.get("publicado_date") or it.get("publicado_em") or ""
            titulo = it.get("titulo") or ""
            # Uma chave só, de propósito. Aceitar `source` como alternativa faria o CLI
            # continuar bonito no dia em que o broker mudasse o nome do campo, e a
            # divergência só apareceria como coluna zerada em outro lugar.
            fonte = it.get("fonte") or "?"
            # O motivo não é enfeite: item barrado por TÍTULO carrega um link que NÃO está
            # na memória, e sem dizer isso o operador vai procurar esse link no histórico e
            # não achar, e concluir que a trava está errada.
            por_titulo = it.get("motivo") == "titulo"
            marca = " (por título ≈, não por URL)" if por_titulo else ""
            print(f"  · {titulo[:58]}  [{fonte}]{marca}"
                  + (f" — saiu em {quando}" if quando else ""))
        if len(r.get("barrados_itens") or []) > 10:
            print(f"  ... e mais {len(r['barrados_itens']) - 10}")
    # Quantos vieram da camada de título decide a frase seguinte. Dizer "não barrou" com a
    # camada em `on` seria mentir para quem acabou de ver a pauta encolher.
    barrou_por_titulo = sum(1 for it in (r.get("barrados_itens") or [])
                            if it.get("motivo") == "titulo")
    if barrou_por_titulo:
        print(f"\n{barrou_por_titulo} desses saíram pela camada de TÍTULO "
              "(`curadoria set --titulo relatorio` volta a só reportar).")
    elif r.get("parecidos"):
        print(f"\n{r['parecidos']} título(s) parecido(s) com o que já saiu — relatório, não barrou.")
    if r.get("alerta"):
        print(f"\n⚠ {r['alerta']}")
        print("  Saídas: add-pauta para injetar matéria na mão, curadoria set --janela N")
        print("  para encurtar a janela, ou --janela 0 para desligar a trava da campanha.")


def cmd_run(a):
    if a.stage:
        r = bc.run(a.edition, a.stage)
        # research tem renderização própria: o dict cru despejava barrados_itens inteiro.
        if a.stage == "research":
            _render_research(r)
        else:
            print(r)
        return
    r = bc.run(a.edition, "research")
    _render_research(r)
    if input("\nAdicionar pauta manual? [s/N] ").strip().lower() == "s":
        print("Use: python scripts/woow.py add-pauta --edition", a.edition, "--title ... --content ...")
        return
    g = bc.run(a.edition, "generate")
    print(f"\nPreview: {g.get('preview_url')}\nCusto estimado: R$ {g.get('cost_brl')}")
    if input("\nDisparar agora? [s/N] ").strip().lower() == "s":
        print(bc.run(a.edition, "send"))
    else:
        print("Edição em 'ready' (não enviada).")


def cmd_add_pauta(a):
    print(bc.add_pauta(a.edition, {"title": a.title, "content": a.content, "link": a.link}))


def cmd_sync(_):
    print(bc.sync())


def _read_emails(csv, file):
    raw = Path(file).read_text(encoding="utf-8") if file else (csv or "")
    if not raw:
        sys.exit("Forneça --emails \"a@x,b@x\" ou --emails-file caminho.txt")
    out, seen = [], set()
    for tok in raw.replace(",", "\n").splitlines():
        e = tok.strip().strip(",").strip()
        if e and "@" in e and e.lower() not in seen:
            seen.add(e.lower()); out.append(e)
    if not out:
        sys.exit("Nenhum email válido (precisa de '@').")
    return out


def cmd_list_lists(_):
    r = bc.list_lists()
    active = (r.get("active") or {})
    akey = active.get("list_key")
    print("Listas ZMA  (→ = alvo do envio diário)\n" + "━" * 38)
    for it in r.get("lists", []):
        mark = "→ " if it["listkey"] == akey else "  "
        cnt = f"{it.get('count','?')} cont." if it.get("count") is not None else ""
        print(f"{mark}{(it['listname'] or '')!r:34} {cnt:9} {it['listkey']}")
    print(f"\nAlvo atual do envio diário: {active.get('list_name')!r} ({active.get('source')})")


def cmd_create_list(a):
    emails = _read_emails(a.emails, a.emails_file)
    print(f"Criar a lista ZMA {a.name!r} com {len(emails)} contato(s)?")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    r = bc.create_list(a.name, emails, a.description)
    if r.get("listkey"):
        print(f"OK lista criada — listkey: {r['listkey']} ({r.get('count')} contatos)")
        print(f"Para apontar a news diária pra ela: "
              f"python scripts/woow.py set-list --list-key {r['listkey']}")
    else:
        print(r)


def cmd_set_list(a):
    if not a.list_key and not a.name:
        sys.exit("Forneça --list-key KEY ou --name \"Nome da lista\"")
    r = bc.list_lists()
    lists = r.get("lists", [])
    if a.list_key:
        match = next((x for x in lists if x["listkey"] == a.list_key), None)
    else:
        match = next((x for x in lists if (x["listname"] or "").strip() == a.name.strip()), None)
    if not match:
        sys.exit("Lista não encontrada no ZMA. Rode: python scripts/woow.py list-lists")
    cur = (r.get("active") or {})
    cnt = f" ({match.get('count')} contatos)" if match.get("count") is not None else ""
    print(f"Alvo atual : {cur.get('list_name')!r}")
    print(f"Novo alvo  : {(match['listname'] or '').strip()!r}{cnt}")
    print("\nTrocar o destinatário do ENVIO DIÁRIO da news para esta lista?")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    print(bc.set_active_list(match["listkey"], match["listname"]))


_DAYNAMES = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]  # 0=seg .. 6=dom


def _parse_days(s):
    s = (s or "").strip().lower()
    if s in ("diario", "diária", "diaria", "todos", "*"):
        return [0, 1, 2, 3, 4, 5, 6]
    if s in ("util", "uteis", "úteis", "semana"):
        return [0, 1, 2, 3, 4]
    out = []
    for tok in s.replace(";", ",").split(","):
        t = tok.strip()[:3]
        if t in _DAYNAMES:
            out.append(_DAYNAMES.index(t))
    if not out:
        sys.exit("--days inválido. Use 'diario', 'util' ou nomes: seg,ter,qua,qui,sex,sab,dom")
    return sorted(set(out))


def _fmt_days(weekdays):
    wd = sorted(weekdays or [])
    if wd == [0, 1, 2, 3, 4, 5, 6]:
        return "todos os dias"
    if wd == [0, 1, 2, 3, 4]:
        return "dias úteis (seg-sex)"
    return ", ".join(_DAYNAMES[d] for d in wd) if wd else "(nenhum)"


def _render_schedule(s):
    print("WooW! Daily Drops — Agendamento\n" + "━" * 33)
    print(f"Estado   : {'LIGADO' if s.get('enabled') else 'desligado'}")
    print(f"Horário  : {s.get('send_time')} BRT")
    print(f"Dias     : {_fmt_days(s.get('weekdays'))}")
    print(f"Modo     : {'AUTO-SEND (dispara sozinho)' if s.get('auto_send') else 'revisão (gera, não dispara)'}")
    if s.get("until"):
        print(f"Janela   : até {s.get('until')}")
    if s.get("last_run_date"):
        print(f"Último run: {s.get('last_run_date')}")
    try:
        active = (bc.list_lists().get("active") or {})
        print(f"Alvo     : {active.get('list_name')!r} ({active.get('source')})")
    except Exception:  # noqa: BLE001 — alvo é informativo; não trava o status
        pass
    if not s.get("enabled"):
        print("\nPara ligar: python scripts/woow.py schedule on")


def cmd_schedule_status(_):
    _render_schedule(bc.get_schedule())


def cmd_schedule_set(a):
    cfg = {}
    if a.time is not None:
        cfg["send_time"] = a.time
    if a.days is not None:
        cfg["weekdays"] = _parse_days(a.days)
    if a.until is not None:
        cfg["until"] = a.until or None
    if not cfg:
        sys.exit("Informe ao menos --time, --days ou --until.")
    s = bc.set_schedule(cfg)
    print("Agendamento atualizado.\n")
    _render_schedule(s)


def cmd_schedule_on(_):
    print("Agendamento LIGADO.\n"); _render_schedule(bc.set_schedule({"enabled": True}))


def cmd_schedule_off(_):
    print("Agendamento desligado.\n"); _render_schedule(bc.set_schedule({"enabled": False}))


def cmd_schedule_autosend(a):
    if a.mode == "on":
        print("AUTO-SEND liga o disparo SEM revisão humana: no horário, a News vai pra")
        print("lista-alvo automaticamente, sem ninguém conferir o preview antes.")
        try:
            active = (bc.list_lists().get("active") or {})
            print(f"Alvo atual do envio: {active.get('list_name')!r}")
        except Exception:  # noqa: BLE001
            pass
        if input("\nLigar auto-send? [s/N] ").strip().lower() != "s":
            print("Cancelado."); return
        print("\nAUTO-SEND ligado.\n"); _render_schedule(bc.set_schedule({"auto_send": True}))
    else:
        print("AUTO-SEND desligado (volta ao modo revisão).\n")
        _render_schedule(bc.set_schedule({"auto_send": False}))


def cmd_create_campaign(a):
    edition = a.edition
    # `--campanha` (qual newsletter recorrente, e portanto qual regra de curadoria a edição
    # herda) é ortogonal a `--type` (qual pipeline roda). Omitido, o broker usa a padrão.
    extra = {"campanha": a.campanha} if a.campanha else None
    if a.type == "news_auto":
        r = bc.create_campaign(edition, "news_auto", extra)
        print(f"Campanha {edition!r} registrada como news_auto (stage {r.get('stage')}).")
        print(f"Curadoria: herda a regra da campanha {(r.get('campanha') or CAMPANHA_PADRAO)!r}.")
        print(f"Rode o pipeline: python scripts/woow.py run --edition {edition}")
        return
    # manual_html: sobe HTML pronto + copy, publica, mostra preview e pergunta se dispara
    if not a.html or not a.subject:
        sys.exit("manual_html exige --html arquivo.html e --subject \"...\"")
    html = Path(a.html).read_text(encoding="utf-8")
    if not html.strip():
        sys.exit(f"Arquivo HTML vazio: {a.html}")
    bc.create_campaign(edition, "manual_html", extra)
    g = bc.run(edition, "generate", {"html": html, "subject": a.subject,
                                     "preheader": a.preheader or "", "list_key": a.list_key})
    print(f"Campanha manual {edition!r} pronta.")
    print(f"Assunto : {a.subject}")
    print(f"Preview : {g.get('preview_url')}")
    if a.list_key:
        print(f"Lista   : {a.list_key} (override por campanha)")
    print("\nConfira o preview no navegador antes de disparar.")
    if input("\nDisparar agora? [s/N] ").strip().lower() == "s":
        print(bc.run(edition, "send"))
    else:
        print(f"Campanha em 'ready' (não enviada). Para disparar depois: "
              f"python scripts/woow.py run --edition {edition} --stage send")


def _sender_verified(senders_resp, email):
    """True se `email` consta como verificado na resposta do /senders (listagem ZMA ou,
    se indisponível, allowlist configurada)."""
    email = (email or "").strip().lower()
    senders = senders_resp.get("senders")
    if senders:
        return email in {(s.get("email") or "").strip().lower() for s in senders if s.get("verified")}
    allow = senders_resp.get("verified_senders") or []
    return email in {e.strip().lower() for e in allow}


def cmd_list_senders(_):
    r = bc.get_senders()
    active = (r.get("active") or {})
    print("Senders ZMA  (✓ = verificado)\n" + "━" * 29)
    senders = r.get("senders")
    if senders:
        for s in senders:
            print(f"{'✓' if s.get('verified') else '·'} {s.get('email')}")
    else:
        print("(ZMA não expôs a listagem; usando allowlist configurada)")
        for e in (r.get("verified_senders") or []):
            print(f"✓ {e}")
        if r.get("note"):
            print(f"nota: {r['note']}")
    print(f"\nRemetente ativo do envio: {active.get('from_email')!r} ({active.get('source')})")


def cmd_set_sender(a):
    try:
        sr = bc.get_senders()
    except Exception:  # noqa: BLE001 — verificação é informativa; não trava a troca
        sr = {}
    cur = (sr.get("active") or {})
    verified = _sender_verified(sr, a.from_email)
    print(f"Remetente atual: {cur.get('from_email')!r}")
    print(f"Novo remetente : {a.from_email!r}" + (f" — {a.from_name}" if a.from_name else ""))
    if not verified:
        print(f"\n⚠ {a.from_email} NÃO consta como Sender verificado no ZMA.")
        print("  Se não estiver verificado no painel ZMA, o disparo falha com erro 6610.")
    print("\nTrocar o REMETENTE de TODOS os envios (news diária + campanhas manuais)?")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    print(bc.set_sender(a.from_email, a.from_name))


def cmd_set_html(a):
    html = Path(a.html).read_text(encoding="utf-8")
    if not html.strip():
        sys.exit(f"Arquivo HTML vazio: {a.html}")
    r = bc.set_html(a.edition, html)
    print(f"HTML da edição {a.edition!r} republicado.")
    print(f"Preview: {r.get('preview_url')}")
    if r.get("versions"):
        print(f"Versões no histórico: {r['versions']} (visíveis no painel)")
    if r.get("warning"):
        print(f"⚠ {r['warning']}")


# ------------------------------------------------------------------- fontes (feeds RSS)
def _fmt_last_test(lt):
    if not lt:
        return "sem teste"
    quando = (lt.get("at") or "")[5:16].replace("T", " ")
    if lt.get("status") == "ok":
        return f"ok · {lt.get('found', 0)} itens · {quando}"
    return f"ERRO · {(lt.get('error') or 'falhou')[:44]}"


def _print_report(report):
    print(f"{'Fonte':26} {'Itens':>6} {'Janela':>7}  Status")
    print("─" * 74)
    for r in report:
        err = r.get("error")
        print(f"{(r.get('source') or '')[:26]:26} {r.get('found', 0):>6} {r.get('kept', 0):>7}  "
              f"{'ok' if not err else 'erro: ' + str(err)[:38]}")
    ruins = [r for r in report if r.get("error")]
    print(f"\n{len(report) - len(ruins)}/{len(report)} fonte(s) ok. Medido de dentro do broker, que é de")
    print("onde a pesquisa baixa os feeds — pode diferir do que você vê no navegador.")


def _fmt_contribuicao(f):
    """O que a fonte entregou de verdade, não se o feed responde.

    `publicadas` ausente é o terceiro resultado: o broker não conseguiu calcular a
    contribuição (memória ilegível, por exemplo). Não é o mesmo que zero, e chamar de
    zero acusaria de inútil uma fonte que ninguém mediu."""
    pub = f.get("publicadas")
    if pub is None:
        return "contribuição não medida"
    barradas = f.get("barradas") or 0
    if pub:
        txt = f"{pub} publicada(s) · {f.get('materias', pub)} matéria(s)"
    elif f.get("enabled", True):
        txt = "⚠ NUNCA PUBLICOU nesta campanha"
    else:
        txt = "0 publicada(s) (desativada)"  # desativada não publicar não é achado
    return txt + (f" · {barradas} barrada(s)" if barradas else "")


def cmd_sources_list(a):
    r = bc.get_sources(campanha=a.campanha)
    feeds = r.get("feeds", [])
    print("WooW! Daily Drops — Fontes da pesquisa  (✓ = ativa)\n" + "━" * 51)
    for f in feeds:
        mark = "✓" if f.get("enabled", True) else "·"
        print(f"{mark} {(f.get('source') or '')[:24]:24} {_fmt_last_test(f.get('last_test'))}")
        print(f"    {_fmt_contribuicao(f)}")
        print(f"    {f.get('url')}")
        if f.get("note"):
            print(f"    nota: {f['note']}")
    ativas = len([f for f in feeds if f.get("enabled", True)])
    print(f"\n{ativas} ativa(s) de {len(feeds)} · origem: {r.get('source')}")
    if r.get("campanha"):
        ref = f" · barradas medidas em {r['edicao_referencia']}" if r.get("edicao_referencia") else ""
        print(f"Contribuição da campanha {r['campanha']!r}{ref}")
    # Fonte ativa que nunca publicou é o achado que a lista antiga escondia: ela responde,
    # passa no teste e mesmo assim não entrega pauta nenhuma. Repetido no rodapé porque é
    # decisão de operador (trocar ou desativar), não detalhe de linha.
    mudas = [f.get("source") for f in feeds
             if f.get("enabled", True) and f.get("publicadas") == 0]
    if mudas:
        print(f"⚠ {len(mudas)} fonte(s) ativa(s) sem nenhuma publicação: {', '.join(mudas)}")
        print("  Ativa e sem entregar pauta: considere 'sources test', trocar a URL ou desativar.")
    if r.get("set_by"):
        print(f"Última edição: {r['set_by']} em {r.get('set_at')}")
    if r.get("tested_at"):
        print(f"Último teste : {r.get('tested_at')}")


def cmd_sources_test(a):
    print("Baixando os feeds de dentro do broker...\n")
    r = bc.test_sources(**({"source": a.name} if a.name else {}))
    _print_report(r.get("report", []))


def _probe(nome, url):
    """Testa a URL no broker antes de gravar e devolve (report, last_test)."""
    print(f"Testando {url} de dentro do broker...\n")
    report = bc.test_sources(url=url, source=nome).get("report", [])
    _print_report(report)
    res = report[0] if report else {}
    return res, {"status": "erro" if res.get("error") else "ok", "found": res.get("found", 0),
                 "kept": res.get("kept", 0), "error": res.get("error")}


def cmd_sources_add(a):
    res, lt = _probe(a.name, a.url)
    if res.get("error"):
        print("\n⚠ A fonte não respondeu do broker. Cadastrar assim deixa ela na lista sem trazer pauta.")
    print(f"\nCadastrar {a.name!r} como fonte da pesquisa?")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    r = bc.set_sources("add", source=a.name, url=a.url, note=a.note, last_test=lt)
    print(f"OK — {r['active']} fonte(s) ativa(s). Vale já na próxima pesquisa, sem redeploy.")


def cmd_sources_set_url(a):
    res, _ = _probe(a.name, a.url)
    if res.get("error"):
        print("\n⚠ A URL nova também não respondeu do broker.")
    print(f"\nTrocar a URL da fonte {a.name!r}?")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    r = bc.set_sources("set-url", source=a.name, url=a.url)
    print(f"OK — URL de {r['source']!r} atualizada.")


def cmd_sources_enable(a):
    r = bc.set_sources("enable", source=a.name)
    print(f"{r['source']!r} ativada — {r['active']} fonte(s) ativa(s).")


def cmd_sources_disable(a):
    r = bc.set_sources("disable", source=a.name)
    print(f"{r['source']!r} desativada — {r['active']} fonte(s) ativa(s).")


def cmd_sources_remove(a):
    print(f"Remover {a.name!r} da lista de fontes?")
    print("(para tirar da pesquisa mas manter cadastrada, use 'sources disable')")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    r = bc.set_sources("remove", source=a.name)
    print(f"Removida — {r['active']} fonte(s) ativa(s).")


# ------------------------------------------------- curadoria (a regra é da campanha)
# `curadoria` governa a REGRA de uma campanha recorrente; `create-campaign` e `set-html`
# governam UMA edição. Nomes parecidos, coisas diferentes, e é aqui que o operador erra.
TITULO_EXPLICA = {"relatorio": "relatorio (mede e reporta, não barra)",
                  "on": "on (barra também por título parecido)",
                  "off": "off (nem mede)"}


def _confirma(pergunta="[s/N] "):
    return input(pergunta).strip().lower() == "s"


def _fmt_janela(dias):
    if not dias:
        return "0 dia(s) — TRAVA DESLIGADA: a campanha aceita repetir matéria"
    return f"{dias} dia(s) — matéria enviada fica fora da pauta por {dias} dia(s)"


def _regra_atual(campanha=None):
    """Regra em vigor, já resolvendo a campanha padrão quando ninguém passou --campanha.

    Resolver aqui, e não deixar o broker resolver, é o que permite mostrar ao operador o
    estado ANTES de ele confirmar: sem isto, a confirmação seria às cegas."""
    doc = bc.get_curadoria()
    default = doc.get("default") or CAMPANHA_PADRAO
    slug = campanha or default
    campanhas = doc.get("campanhas") or {}
    if slug not in campanhas:
        sys.exit(f"Campanha não encontrada: {slug!r}. "
                 f"Rode: python scripts/woow.py curadoria list")
    return slug, campanhas[slug], default


def _print_regra(c, prefixo="    "):
    janela, titulo = c.get("janela_dias"), c.get("titulo_modo")
    print(f"{prefixo}janela {janela} dia(s) · título {titulo} · "
          f"memória {c.get('memoria_links', 0)} link(s) em {c.get('memoria_edicoes', 0)} edição(ões)")
    bloq, livres = len(c.get("bloqueados") or []), len(c.get("liberados") or [])
    if bloq or livres:
        print(f"{prefixo}{bloq} bloqueio(s) manual(is) · {livres} liberação(ões)")
    if not janela:
        print(f"{prefixo}⚠ janela 0: esta campanha aceita repetir matéria já enviada")


def cmd_curadoria_list(_):
    r = bc.get_curadoria()
    default = r.get("default") or CAMPANHA_PADRAO
    campanhas = r.get("campanhas") or {}
    print("WooW! — Curadoria por campanha  (★ = padrão)\n" + "━" * 44)
    for slug, c in sorted(campanhas.items()):
        print(f"{'★' if slug == default else ' '} {slug:24} {c.get('nome') or ''}")
        _print_regra(c)
    print(f"\n{len(campanhas)} campanha(s). Edição sem --campanha herda {default!r}.")
    print("A regra é da CAMPANHA; a edição herda. Não existe ajuste por edição.")


def cmd_curadoria_criar(a):
    r = bc.set_curadoria("criar", campanha=a.campanha, nome=a.nome, copiar_de=a.copiar_de)
    slug = r.get("campanha")
    nova = (r.get("campanhas") or {}).get(slug, {})
    print(f"Campanha {slug!r} criada" + (f" — {nova['nome']}" if nova.get("nome") else "") + ".")
    if a.copiar_de:
        print(f"Regra copiada de {a.copiar_de!r} (sem os bloqueios e liberações de lá).")
    _print_regra(nova)
    print("\nPara uma edição herdar esta regra:")
    print(f"  python scripts/woow.py create-campaign --edition <ID> --campanha {slug}")


def cmd_curadoria_set(a):
    if a.janela is None and a.titulo is None:
        sys.exit("Informe ao menos --janela N ou --titulo relatorio|on|off.")
    slug, regra, default = _regra_atual(a.campanha)
    print(f"Campanha : {slug}" + (" (padrão)" if slug == default else ""))
    if a.janela is not None:
        print(f"Janela   : {regra.get('janela_dias')} dia(s)  ->  {a.janela} dia(s)")
    if a.titulo is not None:
        print(f"Título   : {regra.get('titulo_modo')}  ->  {a.titulo}")
    if a.janela == 0:
        # Kill switch: é a única mudança de regra que reabre o defeito que a trava fechou,
        # então é a única que pede confirmação.
        print("\n⚠ JANELA 0 DESLIGA A TRAVA DE REPETIÇÃO NESTA CAMPANHA.")
        print("  Matéria já enviada volta a poder sair de novo, inclusive no dia seguinte.")
        print("  Foi essa a situação medida antes da trava: 39 links repetidos em 35")
        print("  edições, todos com 1 ou 2 dias de intervalo.")
        print("  Bloqueio manual continua valendo; a memória, não.")
        if not _confirma("\nDesligar a trava de repetição desta campanha? [s/N] "):
            print("Cancelado."); return
    r = None
    if a.janela is not None:
        r = bc.set_curadoria("janela", campanha=a.campanha, janela_dias=a.janela)
    if a.titulo is not None:
        r = bc.set_curadoria("titulo", campanha=a.campanha, titulo_modo=a.titulo)
    print("\nRegra atualizada.")
    _print_regra((r.get("campanhas") or {}).get(r.get("campanha"), {}))


def cmd_curadoria_status(a):
    """O comando que responde 'por que essa matéria não apareceu na edição de hoje'."""
    slug, regra, default = _regra_atual(a.campanha)
    nome = f" ({regra['nome']})" if regra.get("nome") else ""
    print(f"WooW! — Curadoria de {slug}{nome}\n" + "━" * 48)
    print(f"Janela        : {_fmt_janela(regra.get('janela_dias'))}")
    modo = regra.get("titulo_modo")
    print(f"Camada título : {TITULO_EXPLICA.get(modo, modo)}")
    print(f"Memória       : {regra.get('memoria_links', 0)} link(s) de "
          f"{regra.get('memoria_edicoes', 0)} edição(ões) enviada(s)")

    linhas = [e for e in (bc.queue().get("editions") or [])
              if (e.get("campanha") or CAMPANHA_PADRAO) == slug and e.get("stage") != "empty"]
    if linhas:
        ult = linhas[-1]
        print(f"\nÚltima pesquisa: {ult['edition']} ({ult.get('stage')})")
        if ult.get("barrados") is None:
            print("  sem registro de barrados (pesquisa anterior à trava)")
        else:
            print(f"  {ult['barrados']} item(ns) barrado(s) por já terem saído")
        if ult.get("barrados"):
            # A quebra por fonte só existe quando houve barrado; sem isto, o comando
            # pagaria uma chamada a /sources todo dia para imprimir nada.
            src = bc.get_sources(campanha=slug)
            porf = [(f.get("source"), f.get("barradas") or 0) for f in (src.get("feeds") or [])]
            porf = sorted([x for x in porf if x[1]], key=lambda x: -x[1])
            if porf:
                print("  por fonte: " + " · ".join(f"{s} {n}" for s, n in porf))
            print("  título a título, o detalhe sai no run --stage research")

    bloq = regra.get("bloqueados") or []
    if bloq:
        print("\nBloqueio manual (vale mesmo com a janela em 0):")
        for b in bloq[:10]:
            print(f"  {b.get('link')}" + (f"  — {b['motivo']}" if b.get("motivo") else ""))
    for livre in (regra.get("liberados") or [])[:10]:
        print(f"\nLiberado à mão (pode repetir): {livre.get('link')}")

    dias = regra.get("janela_dias") or 0
    if not dias:
        print("\n⚠ Janela 0: nada está sendo barrado por repetição nesta campanha.")
        return
    links = (bc.get_publicados(campanha=slug, dias=dias).get("links") or [])
    print(f"\nJá saiu nos últimos {dias} dia(s) — é esta lista que barra:")
    if not links:
        print("  (nada: a memória está vazia nessa janela)")
    for e in links[:15]:
        quando = e.get("date") or e.get("edition") or ""
        print(f"  {quando:10}  {(e.get('source') or '')[:18]:18} {(e.get('titulo') or e.get('link') or '')[:44]}")
    if len(links) > 15:
        print(f"  ... e mais {len(links) - 15}. Lista inteira: curadoria historico --dias {dias}")


def cmd_curadoria_historico(a):
    r = bc.get_publicados(campanha=a.campanha, dias=a.dias)
    links = r.get("links") or []
    print(f"WooW! — Já publicado em {r.get('campanha')}  "
          f"(janela da trava: {r.get('janela_dias')} dia(s))\n" + "━" * 60)
    for e in links:
        quando = e.get("date") or e.get("edition") or ""
        print(f"{quando:10}  {(e.get('source') or '')[:18]:18} {(e.get('titulo') or '')[:44]}")
        print(f"            {e.get('link')}")
    recorte = f" Recorte: últimos {a.dias} dia(s)." if a.dias else ""
    print(f"\n{len(links)} link(s) de {r.get('edicoes', 0)} edição(ões) enviada(s).{recorte}")
    print(f"{len(r.get('bloqueados') or [])} bloqueio(s) manual(is) · "
          f"{len(r.get('liberados') or [])} liberação(ões).")


def _estado_do_link(regra, link):
    b = next((x for x in (regra.get("bloqueados") or []) if x.get("link") == link), None)
    if b:
        return "bloqueado por " + (b.get("por") or "?") + (f" — {b['motivo']}" if b.get("motivo") else "")
    if any(x.get("link") == link for x in (regra.get("liberados") or [])):
        return "liberado à mão (hoje pode repetir)"
    return "sem marca manual (segue só a janela)"


def cmd_curadoria_bloquear(a):
    slug, regra, _default = _regra_atual(a.campanha)
    print(f"Campanha : {slug}")
    print(f"Link     : {a.link}")
    print(f"Atual    : {_estado_do_link(regra, a.link)}")
    print("Novo     : bloqueado" + (f" — {a.motivo}" if a.motivo else ""))
    print("\nBloqueio não expira e vale mesmo com a janela em 0: este link não volta")
    print("à pauta desta campanha até alguém rodar 'curadoria liberar'.")
    if not _confirma():
        print("Cancelado."); return
    r = bc.set_curadoria("bloquear", campanha=a.campanha, link=a.link, motivo=a.motivo)
    n = len(((r.get("campanhas") or {}).get(r.get("campanha"), {})).get("bloqueados") or [])
    print(f"Bloqueado — {n} bloqueio(s) manual(is) em {r.get('campanha')!r}.")


def cmd_curadoria_liberar(a):
    slug, regra, _default = _regra_atual(a.campanha)
    print(f"Campanha : {slug}")
    print(f"Link     : {a.link}")
    print(f"Atual    : {_estado_do_link(regra, a.link)}")
    print("Novo     : liberado")
    print("\nLiberar tira o link da memória E do bloqueio: ele pode voltar à pauta")
    print("mesmo já tendo saído nos últimos dias.")
    if not _confirma():
        print("Cancelado."); return
    r = bc.set_curadoria("liberar", campanha=a.campanha, link=a.link)
    n = len(((r.get("campanhas") or {}).get(r.get("campanha"), {})).get("liberados") or [])
    print(f"Liberado — {n} liberação(ões) em {r.get('campanha')!r}.")


def cmd_curadoria_remover(a):
    slug, regra, default = _regra_atual(a.campanha)
    if slug == default:
        sys.exit(f"{slug!r} é a campanha padrão e não pode ser removida.")
    print(f"Remover a REGRA de curadoria da campanha {slug!r}"
          + (f" ({regra['nome']})" if regra.get("nome") else "") + "?")
    _print_regra(regra, prefixo="  ")
    print("\nA memória do que já saiu nessa campanha NÃO é apagada aqui, e as edições")
    print("que apontam para ela passam a cair na regra da campanha padrão.")
    if not _confirma():
        print("Cancelado."); return
    r = bc.set_curadoria("remover", campanha=a.campanha)
    print(f"Removida — restam {len(r.get('campanhas') or {})} campanha(s).")


def cmd_curadoria_rebuild(_):
    print("Reconstrói a memória do que já foi publicado, lendo os states das edições")
    print("enviadas. O índice é 100% derivado: nada se perde, tudo se recalcula.")
    print("Comando de ADMIN (david@).")
    if not _confirma("\nReconstruir agora? [s/N] "):
        print("Cancelado."); return
    r = bc.rebuild_publicados()
    for slug, n in sorted((r.get("campanhas") or {}).items()):
        print(f"  {slug:24} {n} link(s)")
    print(f"\n{len(r.get('campanhas') or {})} campanha(s) reindexada(s).")


# ------------------------------------------------------ versao da skill e anuncio
def _fmt_quando(iso):
    return (iso or "")[:16].replace("T", " ")


def cmd_versions(_):
    from config import local_version, is_outdated
    r = bc.get_clients()
    pub = r.get("published", "")
    local = local_version()
    print("WooW! skill — versões\n" + "━" * 22)
    print(f"Nesta máquina      : {local or '(desconhecida)'}")
    print(f"Publicada no broker: {pub}")
    rel = r.get("release") or {}
    if rel.get("version") == pub and rel.get("notes"):
        print(f"O que mudou        : {rel['notes']}")
    clients = r.get("clients") or {}
    atrasados = r.get("atrasados")
    fora = {x["email"] for x in atrasados} if atrasados is not None else None
    if clients:
        print("\nQuem usou a skill  (! = atrasado)")
        for email, info in sorted(clients.items()):
            # a marca vem da lista do broker, não de uma segunda conta aqui: assim tabela e
            # rodapé não podem discordar. O is_outdated só entra no ramo do operador, que
            # não recebe a lista e só tem a própria linha.
            atras = (email in fora) if fora is not None else is_outdated(info.get("version"), pub)
            print(f" {'!' if atras else ' '} {email:34} {info.get('version') or '?':9} {_fmt_quando(info.get('last_seen'))}")
    if atrasados is not None:
        nunca = [x for x in atrasados if x.get("nunca_chamou")]
        if not atrasados:
            print("\nTodo mundo em dia.")
        else:
            print(f"\n{len(atrasados)} atrasado(s):")
            for x in atrasados:
                estado = "nunca chamou o broker" if x.get("nunca_chamou") else \
                    f"{x.get('version') or 'sem versão'} · visto {_fmt_quando(x.get('last_seen'))}"
                print(f"  · {x['email']:34} {estado}")
            if nunca:
                print(f"  ({len(nunca)} nunca chamou: pode estar em versão antiga ou nem usar a skill)")
    elif r.get("atrasados_total") is not None:
        print(f"\n{r['atrasados_total']} atrasado(s) no time (tabela completa só para admin).")


def cmd_release(a):
    """Grava a nota da versão publicada e devolve o texto de anúncio pronto para o Slack.
    O broker não posta sozinho por decisão: quem cola é uma pessoa."""
    from config import local_version
    r = bc.get_clients()
    if r.get("atrasados") is None:  # só admin recebe a lista completa; /admin/release é ADMIN_ONLY
        sys.exit("release é comando de admin (david@). Peça a ele para publicar a nota.")
    pub = r.get("published", "")
    local = local_version()
    print(f"Versão publicada no broker: {pub}")
    if local and local != pub:
        print(f"⚠ Esta máquina está em {local}. A nota é sobre a PUBLICADA ({pub}).")
    print(f"Nota: {a.notes}")
    print("\nGravar essa nota? (ela aparece no aviso de quem está desatualizado)")
    if input("[s/N] ").strip().lower() != "s":
        print("Cancelado."); return
    bc.set_release(a.notes)
    atrasados = r.get("atrasados") or []
    print("\n" + "━" * 60)
    print(f"*WooW! skill v{pub} no ar*")
    print(a.notes)
    print("Para atualizar: `/plugin marketplace update mk-skills`")
    if atrasados:
        quem = ", ".join(
            f"{x['email'].split('@')[0]}@ ({'nunca abriu a skill' if x.get('nunca_chamou') else (x['version'] or 'versão antiga')})"
            for x in atrasados)
        print(f"Ainda não atualizaram: {quem}")
    print("━" * 60)
    print("Copie o bloco acima e cole no Slack.")


def monta_parser():
    """Fora do main() para o teste conseguir perguntar qual função cada subcomando chama.
    Com a árvore montada dentro do main(), a única forma de checar a fiação era rodar o
    comando de verdade."""
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("queue").set_defaults(fn=cmd_queue)
    sub.add_parser("metrics").set_defaults(fn=cmd_metrics)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    r = sub.add_parser("run"); r.add_argument("--edition", required=True); r.add_argument("--stage")
    r.set_defaults(fn=cmd_run)
    ap = sub.add_parser("add-pauta")
    ap.add_argument("--edition", required=True); ap.add_argument("--title", required=True)
    ap.add_argument("--content", default=""); ap.add_argument("--link", default="")
    ap.set_defaults(fn=cmd_add_pauta)
    sub.add_parser("list-lists").set_defaults(fn=cmd_list_lists)
    cl = sub.add_parser("create-list")
    cl.add_argument("--name", required=True)
    cl.add_argument("--emails", default=None, help="CSV de emails")
    cl.add_argument("--emails-file", default=None, help="arquivo (1 por linha ou CSV)")
    cl.add_argument("--description", default=None)
    cl.set_defaults(fn=cmd_create_list)
    sl = sub.add_parser("set-list")
    sl.add_argument("--list-key", default=None); sl.add_argument("--name", default=None)
    sl.set_defaults(fn=cmd_set_list)
    cc = sub.add_parser("create-campaign")
    cc.add_argument("--edition", required=True)
    cc.add_argument("--type", choices=["news_auto", "manual_html"], default="news_auto")
    cc.add_argument("--html", default=None, help="arquivo HTML pronto (manual_html)")
    cc.add_argument("--subject", default=None, help="assunto do email (manual_html)")
    cc.add_argument("--preheader", default=None, help="preheader/preview text (manual_html)")
    cc.add_argument("--list-key", default=None, help="lista ZMA por campanha (override do alvo global)")
    cc.add_argument("--campanha", default=None,
                    help="campanha recorrente cuja regra de curadoria a edição herda "
                         "(omitido: a padrão). Crie antes com 'curadoria criar'.")
    cc.set_defaults(fn=cmd_create_campaign)
    sub.add_parser("list-senders").set_defaults(fn=cmd_list_senders)
    ss = sub.add_parser("set-sender")
    ss.add_argument("--from-email", required=True)
    ss.add_argument("--from-name", default=None)
    ss.set_defaults(fn=cmd_set_sender)
    sh = sub.add_parser("set-html")
    sh.add_argument("--edition", required=True)
    sh.add_argument("--html", required=True, help="arquivo HTML que substitui o preview da edição")
    sh.set_defaults(fn=cmd_set_html)
    sub.add_parser("versions").set_defaults(fn=cmd_versions)
    rel = sub.add_parser("release")
    rel.add_argument("--notes", required=True, help="uma linha sobre o que mudou nesta versão")
    rel.set_defaults(fn=cmd_release)
    src = sub.add_parser("sources")
    srcsub = src.add_subparsers(dest="sources_cmd", required=True)
    sls = srcsub.add_parser("list")
    sls.add_argument("--campanha", default=None,
                     help="de qual campanha contar publicadas/barradas (omitido: a padrão)")
    sls.set_defaults(fn=cmd_sources_list)
    stt = srcsub.add_parser("test")
    stt.add_argument("--name", default=None, help="testa só essa fonte (mesmo desativada)")
    stt.set_defaults(fn=cmd_sources_test)
    sad = srcsub.add_parser("add")
    sad.add_argument("--name", required=True, help="nome da fonte, ex: \"Retail Dive\"")
    sad.add_argument("--url", required=True, help="URL do feed RSS/Atom")
    sad.add_argument("--note", default=None)
    sad.set_defaults(fn=cmd_sources_add)
    sul = srcsub.add_parser("set-url")
    sul.add_argument("--name", required=True); sul.add_argument("--url", required=True)
    sul.set_defaults(fn=cmd_sources_set_url)
    for _op, _fn in (("enable", cmd_sources_enable), ("disable", cmd_sources_disable),
                     ("remove", cmd_sources_remove)):
        _p = srcsub.add_parser(_op)
        _p.add_argument("--name", required=True)
        _p.set_defaults(fn=_fn)
    # `--campanha` é sempre opcional (menos em criar/remover, que nomeiam a campanha): sem
    # ele o broker resolve a padrão. Exigir o slug todo dia só faria o operador digitar
    # "daily-drops" em toda linha para não mudar nada.
    cur = sub.add_parser("curadoria")
    cursub = cur.add_subparsers(dest="curadoria_cmd", required=True)
    cursub.add_parser("list").set_defaults(fn=cmd_curadoria_list)
    ccr = cursub.add_parser("criar")
    ccr.add_argument("--campanha", required=True, help="slug: minúsculas, números e hífen")
    ccr.add_argument("--nome", default=None, help="nome legível, ex: \"WooW! Beauty\"")
    ccr.add_argument("--copiar-de", default=None,
                     help="copia a regra de outra campanha (sem bloqueios nem liberações)")
    ccr.set_defaults(fn=cmd_curadoria_criar)
    cse = cursub.add_parser("set")
    cse.add_argument("--campanha", default=None)
    cse.add_argument("--janela", type=int, default=None,
                     help="dias que a matéria fica fora da pauta; 0 desliga a trava")
    cse.add_argument("--titulo", choices=["relatorio", "on", "off"], default=None,
                     help="camada de similaridade de título")
    cse.set_defaults(fn=cmd_curadoria_set)
    cst = cursub.add_parser("status")
    cst.add_argument("--campanha", default=None)
    cst.set_defaults(fn=cmd_curadoria_status)
    chi = cursub.add_parser("historico")
    chi.add_argument("--campanha", default=None)
    chi.add_argument("--dias", type=int, default=None, help="recorte do histórico")
    chi.set_defaults(fn=cmd_curadoria_historico)
    cbl = cursub.add_parser("bloquear")
    cbl.add_argument("--link", required=True); cbl.add_argument("--campanha", default=None)
    cbl.add_argument("--motivo", default=None)
    cbl.set_defaults(fn=cmd_curadoria_bloquear)
    clb = cursub.add_parser("liberar")
    clb.add_argument("--link", required=True); clb.add_argument("--campanha", default=None)
    clb.set_defaults(fn=cmd_curadoria_liberar)
    crm = cursub.add_parser("remover")
    crm.add_argument("--campanha", required=True)
    crm.set_defaults(fn=cmd_curadoria_remover)
    cursub.add_parser("rebuild").set_defaults(fn=cmd_curadoria_rebuild)
    sch = sub.add_parser("schedule")
    ssub = sch.add_subparsers(dest="schedule_cmd", required=True)
    ssub.add_parser("status").set_defaults(fn=cmd_schedule_status)
    sset = ssub.add_parser("set")
    sset.add_argument("--time", default=None, help="HH:MM em BRT (ex.: 10:00)")
    sset.add_argument("--days", default=None, help="diario | util | seg,ter,qua,qui,sex,sab,dom")
    sset.add_argument("--until", default=None, help="YYYY-MM-DD (janela opcional; vazio limpa)")
    sset.set_defaults(fn=cmd_schedule_set)
    ssub.add_parser("on").set_defaults(fn=cmd_schedule_on)
    ssub.add_parser("off").set_defaults(fn=cmd_schedule_off)
    sas = ssub.add_parser("auto-send")
    sas.add_argument("mode", choices=["on", "off"])
    sas.set_defaults(fn=cmd_schedule_autosend)
    return p


def main():
    args = monta_parser().parse_args()
    try:
        args.fn(args)
    except bc.BrokerError as e:
        # 403 de papel e 502 de validação viravam traceback, e a última linha mandava
        # refazer o login — remédio errado para "você não é admin" ou "URL inválida".
        sys.exit(f"\n{e}")
    except KeyboardInterrupt:
        sys.exit("\nCancelado.")


if __name__ == "__main__":
    main()
