#!/usr/bin/env python3
"""woow.py — CLI da skill woow-news. Renderiza a gaveta e dirige o pipeline via broker.

Uso:
  python scripts/woow.py status
  python scripts/woow.py queue
  python scripts/woow.py metrics
  python scripts/woow.py run --edition 2026-w26 [--stage research|generate|send]
  python scripts/woow.py add-pauta --edition 2026-w26 --title "..." --content "..." --link "..."
  python scripts/woow.py sync
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
    args = p.parse_args(); args.fn(args)


if __name__ == "__main__":
    main()
