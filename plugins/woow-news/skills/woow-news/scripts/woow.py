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
        extra = f"open {round(e['open_rate']*100)}%" if e.get("open_rate") else ""
        print(f"{glyph} {e['edition']}   {e.get('date',''):10}   {extra}")
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
    args = p.parse_args(); args.fn(args)


if __name__ == "__main__":
    main()
