# broker/tests/test_schedule.py
import sys, pathlib
from datetime import datetime
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import orchestrator
from state_manager import StateManager, LocalStore, BRT


def _sched(**over):
    s = dict(orchestrator.SCHEDULE_DEFAULTS)
    s.update(over)
    return s


def _dt(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=BRT)


# ---------------------------------------------- _should_run_now (função pura)
def test_disabled_never_runs():
    assert not orchestrator._should_run_now(_sched(enabled=False), _dt(2026, 7, 1, 10, 0), "empty")[0]


def test_runs_only_at_or_after_time():
    s = _sched(enabled=True, send_time="10:00")
    assert not orchestrator._should_run_now(s, _dt(2026, 7, 1, 9, 59), "empty")[0]
    assert orchestrator._should_run_now(s, _dt(2026, 7, 1, 10, 0), "empty")[0]
    assert orchestrator._should_run_now(s, _dt(2026, 7, 1, 10, 15), "empty")[0]


def test_weekday_filter():
    d = _dt(2026, 7, 1, 10, 0)
    wd = d.weekday()
    assert orchestrator._should_run_now(_sched(enabled=True, weekdays=[wd]), d, "empty")[0]
    others = [x for x in range(7) if x != wd]
    assert not orchestrator._should_run_now(_sched(enabled=True, weekdays=others), d, "empty")[0]


def test_until_window():
    s = _sched(enabled=True, until="2026-07-07")
    assert orchestrator._should_run_now(s, _dt(2026, 7, 7, 10, 0), "empty")[0]
    assert not orchestrator._should_run_now(s, _dt(2026, 7, 8, 10, 0), "empty")[0]


def test_dedup_last_run_date():
    s = _sched(enabled=True, last_run_date="2026-07-01")
    assert not orchestrator._should_run_now(s, _dt(2026, 7, 1, 10, 0), "empty")[0]
    assert orchestrator._should_run_now(s, _dt(2026, 7, 2, 10, 0), "empty")[0]


def test_stage_guard_review_vs_auto():
    review = _sched(enabled=True, auto_send=False)
    auto = _sched(enabled=True, auto_send=True)
    # revisão para em ready -> não roda de novo; auto ainda precisa disparar -> roda
    assert not orchestrator._should_run_now(review, _dt(2026, 7, 1, 10, 0), "ready")[0]
    assert orchestrator._should_run_now(auto, _dt(2026, 7, 1, 10, 0), "ready")[0]
    # ambos pulam se já enviada
    assert not orchestrator._should_run_now(review, _dt(2026, 7, 1, 10, 0), "sent")[0]
    assert not orchestrator._should_run_now(auto, _dt(2026, 7, 1, 10, 0), "sent")[0]


# ---------------------------------------------- get/set_schedule (LocalStore)
def test_get_set_schedule_roundtrip(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    assert orchestrator.get_schedule(sm)["enabled"] is False  # default quando ausente
    s = orchestrator.set_schedule({"enabled": True, "send_time": "08:30",
                                   "weekdays": [0, 1, 2, 3, 4],
                                   "_email": "joao@metakosmos.com.br"})
    assert s["enabled"] is True and s["send_time"] == "08:30"
    assert s["set_by"] == "joao@metakosmos.com.br"
    assert orchestrator.get_schedule(sm)["weekdays"] == [0, 1, 2, 3, 4]


def test_set_schedule_preserves_last_run_date(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    orchestrator._mark_schedule_run(sm, "2026-07-01")
    orchestrator.set_schedule({"send_time": "09:00"})
    assert orchestrator.get_schedule(sm)["last_run_date"] == "2026-07-01"


def test_set_schedule_clears_until_on_empty(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    orchestrator.set_schedule({"until": "2026-07-07"})
    assert orchestrator.get_schedule(sm)["until"] == "2026-07-07"
    orchestrator.set_schedule({"until": ""})
    assert orchestrator.get_schedule(sm)["until"] is None


def test_set_schedule_validates(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"send_time": "25:00"})
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"weekdays": [7]})
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"weekdays": []})
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"until": "07/07"})
