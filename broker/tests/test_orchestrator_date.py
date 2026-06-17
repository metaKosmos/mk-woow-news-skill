# broker/tests/test_orchestrator_date.py
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import orchestrator


def test_daily_key_is_its_own_date():
    # Daily Drops: a chave da edição já é a data → vira o campo `date` direto.
    assert orchestrator._resolve_edition_date("2026-06-17") == "2026-06-17"


def test_legacy_week_key_falls_back_to_today():
    # Chave legada (wNN) não casa o formato → cai para hoje em YYYY-MM-DD.
    out = orchestrator._resolve_edition_date("2026-w25")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out)


def test_empty_edition_is_safe():
    out = orchestrator._resolve_edition_date("")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out)
