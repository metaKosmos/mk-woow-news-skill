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
      --html campanha.html --subject "..." --preheader "..." --list-key <KEY>
  python scripts/woow.py list-senders
  python scripts/woow.py set-sender --from-email patrick@metakosmos.com.br --from-name "WooW!"
  python scripts/woow.py set-html --edition 2026-07-01 --html novo.html
  python scripts/woow.py versions
  python scripts/woow.py release --notes "fontes RSS viraram autosservico"
  python scripts/woow.py sources list | test [--name "Fast Company"]
  python scripts/woow.py sources add --name "Retail Dive" --url https://www.retaildive.com/feeds/news/
  python scripts/woow.py sources set-url --name "Fast Company" --url https://www.fastcompany.com/latest/rss
  python scripts/woow.py sources enable | disable | remove --name "E-Commerce Brasil"
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


def cmd_run(a):
    if a.stage:
        print(bc.run(a.edition, a.stage))
        return
    r = bc.run(a.edition, "research")
    print(r.get("summary", "")[:2000])
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
    if a.type == "news_auto":
        r = bc.create_campaign(edition, "news_auto")
        print(f"Campanha {edition!r} registrada como news_auto (stage {r.get('stage')}).")
        print(f"Rode o pipeline: python scripts/woow.py run --edition {edition}")
        return
    # manual_html: sobe HTML pronto + copy, publica, mostra preview e pergunta se dispara
    if not a.html or not a.subject:
        sys.exit("manual_html exige --html arquivo.html e --subject \"...\"")
    html = Path(a.html).read_text(encoding="utf-8")
    if not html.strip():
        sys.exit(f"Arquivo HTML vazio: {a.html}")
    bc.create_campaign(edition, "manual_html")
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


def cmd_sources_list(_):
    r = bc.get_sources()
    feeds = r.get("feeds", [])
    print("WooW! Daily Drops — Fontes da pesquisa  (✓ = ativa)\n" + "━" * 51)
    for f in feeds:
        mark = "✓" if f.get("enabled", True) else "·"
        print(f"{mark} {(f.get('source') or '')[:24]:24} {_fmt_last_test(f.get('last_test'))}")
        print(f"    {f.get('url')}")
        if f.get("note"):
            print(f"    nota: {f['note']}")
    ativas = len([f for f in feeds if f.get("enabled", True)])
    print(f"\n{ativas} ativa(s) de {len(feeds)} · origem: {r.get('source')}")
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


def main():
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
    srcsub.add_parser("list").set_defaults(fn=cmd_sources_list)
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
    args = p.parse_args()
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
