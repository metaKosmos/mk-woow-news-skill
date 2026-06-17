#!/usr/bin/env python3
"""
version_check.py — Avisa se ha uma versao mais nova da skill woow-news.

Compara a versao local (arquivo VERSION) com a versao publicada (endpoint
GET /version do broker). Imprime UMA linha de aviso quando houver atualizacao.
Tolerante a falha de rede (nunca quebra o fluxo da skill).

Uso:
    python scripts/version_check.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SKILL_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = SKILL_DIR / "VERSION"


def _local_version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _remote_version():
    try:
        from broker_client import version
        return version()
    except Exception:
        return None


def _parse(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return None


def main():
    local = _local_version()
    remote = _remote_version()
    if not local or not remote:
        return  # sem dados suficientes — silencioso
    lp, rp = _parse(local), _parse(remote)
    if lp and rp and rp > lp:
        print(f"[!] woow-news v{local} instalada · v{remote} disponível → "
              f"rode /plugin marketplace update mk-skills "
              f"(ou re-suba o zip no claude.ai). Mais novo no Claude Desktop/Cowork.")


if __name__ == "__main__":
    main()
