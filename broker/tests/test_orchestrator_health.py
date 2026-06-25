# broker/tests/test_orchestrator_health.py
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))
import orchestrator
import research
from state_manager import StateManager, LocalStore


def test_build_health_counts_and_errors():
    # report = (source, encontrados, dentro_da_janela, erro) — só os com erro entram.
    report = [("BoF", 10, 3, None), ("Vogue", 0, 0, "403"), ("WWD", 5, 2, None)]
    candidates = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    h = research.build_health(report, candidates)
    assert h["candidates"] == 3
    assert h["feeds_total"] == 3
    assert h["feed_errors"] == [{"source": "Vogue", "error": "403"}]
    assert "researched_at" in h


def test_record_stage_error_preserves_research_health(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-06-17", {"stage": "researched",
                                     "health": {"candidates": 46, "feeds_total": 10, "feed_errors": []}})
    orchestrator._record_stage_error(sm, "2026-06-17", "generate", RuntimeError("boom"))
    h = sm.get_state("2026-06-17")["health"]
    assert h["candidates"] == 46  # falha do generate não apaga a saúde da pesquisa
    assert h["last_error"]["stage"] == "generate"
    assert "boom" in h["last_error"]["message"]


def test_clear_stage_error(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-06-17", {"health": {"candidates": 5,
                                                "last_error": {"stage": "send", "message": "x", "at": "t"}}})
    orchestrator._clear_stage_error(sm, "2026-06-17")
    h = sm.get_state("2026-06-17")["health"]
    assert "last_error" not in h
    assert h["candidates"] == 5


def test_clear_stage_error_noop_without_error(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-06-17", {"health": {"candidates": 5}})
    orchestrator._clear_stage_error(sm, "2026-06-17")  # não deve quebrar
    assert sm.get_state("2026-06-17")["health"]["candidates"] == 5
