# broker/tests/test_dispatch_by_type.py — MAR-176: generate ramifica por `type`
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
import pytest
import orchestrator
from state_manager import StateManager, LocalStore


def _patch_common(tmp_path, monkeypatch):
    """Isola run_stage do GCP: sm local, workdir falso, restore no-op."""
    sm = StateManager(LocalStore(tmp_path))
    wd = tmp_path / "wd"; (wd / "renders").mkdir(parents=True); (wd / "content").mkdir()
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "_workdir", lambda ed: wd)
    monkeypatch.setattr(orchestrator, "_restore_content", lambda *a, **k: None)
    return sm, wd


def test_generate_dispatches_manual_html(tmp_path, monkeypatch):
    sm, wd = _patch_common(tmp_path, monkeypatch)
    sm.upsert_edition("camp-1", {"type": "manual_html"})
    calls = {}
    def _spy(s, w, ed, pl):
        calls["manual"] = (ed, pl)
        return {"stage": "ready"}
    monkeypatch.setattr(orchestrator, "_generate_manual_html", _spy)
    def _boom(*a, **k):  # se o ramo news_auto rodar, o teste falha aqui
        raise AssertionError("news_auto _run_script NÃO deveria rodar p/ manual_html")
    monkeypatch.setattr(orchestrator, "_run_script", _boom)
    out = orchestrator.run_stage("camp-1", "generate", {"html": "<p>x</p>", "subject": "S"})
    assert out == {"stage": "ready"}
    assert calls["manual"][0] == "camp-1"


def test_generate_dispatches_news_auto(tmp_path, monkeypatch):
    sm, wd = _patch_common(tmp_path, monkeypatch)
    sm.upsert_edition("2026-07-08", {"stage": "researched"})  # sem type -> news_auto
    manual_called = {"v": False}
    monkeypatch.setattr(orchestrator, "_generate_manual_html",
                        lambda *a, **k: manual_called.__setitem__("v", True))
    # o 1º passo do news_auto é _run_script(generate_content.py); marcamos que foi alcançado
    monkeypatch.setattr(orchestrator, "_run_script",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("RAN_NEWS_AUTO")))
    with pytest.raises(RuntimeError, match="RAN_NEWS_AUTO"):
        orchestrator.run_stage("2026-07-08", "generate", {})
    assert manual_called["v"] is False  # não caiu no ramo manual
