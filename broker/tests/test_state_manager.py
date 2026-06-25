# broker/tests/test_state_manager.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from state_manager import StateManager, LocalStore

def test_upsert_edition_and_queue(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-w25", {"stage": "researched", "subject": "S25", "date": "2026-06-16"})
    sm.upsert_edition("2026-w25", {"stage": "ready", "image_ready": True})  # merge, não sobrescreve subject
    st = sm.get_state("2026-w25")
    assert st["subject"] == "S25"
    assert st["stage"] == "ready"
    assert st["image_ready"] is True
    assert "researched_at" in st.get("timestamps", {})
    assert "ready_at" in st.get("timestamps", {})
    q = sm.get_queue()
    rows = [e for e in q["editions"] if e["edition"] == "2026-w25"]
    assert len(rows) == 1
    assert rows[0]["stage"] == "ready"
    assert rows[0]["subject"] == "S25"

def test_queue_sorted_and_coverage(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-w24", {"stage": "sent", "date": "2026-06-13"})
    sm.upsert_edition("2026-w26", {"stage": "ready", "date": "2026-06-17"})
    eds = [e["edition"] for e in sm.get_queue()["editions"]]
    assert eds == sorted(eds)
    cov = sm.coverage()
    assert cov["ready"] == 1
    assert cov["sent"] == 1

def test_stage_rank_monotonic():
    from state_manager import STAGE_RANK
    assert STAGE_RANK["empty"] < STAGE_RANK["researched"] < STAGE_RANK["generated"] < STAGE_RANK["ready"] < STAGE_RANK["sent"]

def test_stage_never_downgrades(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-w27", {"stage": "sent"})
    sm.upsert_edition("2026-w27", {"stage": "ready"})  # tentativa de rebaixar
    assert sm.get_state("2026-w27")["stage"] == "sent"

def test_health_and_metrics_coexist(tmp_path):
    # O merge raso não pode deixar metrics apagar health (nem vice-versa).
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-06-17", {"stage": "researched",
                                     "health": {"candidates": 46, "feed_errors": []}})
    sm.upsert_edition("2026-06-17", {"metrics": {"open_rate": 0.5, "clicked": 0}})
    st = sm.get_state("2026-06-17")
    assert st["health"]["candidates"] == 46
    assert st["metrics"]["open_rate"] == 0.5
    # ordem inversa: gravar health depois preserva metrics.
    sm.upsert_edition("2026-06-17", {"health": {"candidates": 50, "feed_errors": []}})
    st = sm.get_state("2026-06-17")
    assert st["metrics"]["open_rate"] == 0.5
    assert st["health"]["candidates"] == 50
