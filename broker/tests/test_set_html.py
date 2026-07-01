# broker/tests/test_set_html.py — MAR-175: override de HTML por edição (sem redeploy)
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
import pytest
import orchestrator
from state_manager import StateManager, LocalStore


def _patch(tmp_path, monkeypatch):
    # set_html usa um tempdir mínimo próprio (não _workdir/secrets); só stubamos _sm e o publish.
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "_publish_public",
                        lambda w, ed: (f"https://pub/nl/{ed}.html", ""))
    return sm


def test_set_html_updates_preview_without_touching_stage_type(tmp_path, monkeypatch):
    sm = _patch(tmp_path, monkeypatch)
    sm.upsert_edition("2026-07-09", {"type": "manual_html", "stage": "ready",
                                     "preview_url": "https://pub/nl/old.html", "subject": "S"})
    r = orchestrator.set_html({"edition": "2026-07-09", "html": "<p>novo HTML</p>"})
    assert r["preview_url"] == "https://pub/nl/2026-07-09.html"
    assert "warning" not in r
    st = sm.get_state("2026-07-09")
    assert st["preview_url"] == "https://pub/nl/2026-07-09.html"
    assert st["stage"] == "ready"          # não mexeu no stage
    assert st["type"] == "manual_html"     # nem no type
    assert st["subject"] == "S"

def test_set_html_requires_edition_and_html(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_html({"edition": "e"})
    with pytest.raises(ValueError):
        orchestrator.set_html({"html": "<p>x</p>"})

def test_set_html_warns_when_edition_already_sent(tmp_path, monkeypatch):
    sm = _patch(tmp_path, monkeypatch)
    sm.upsert_edition("2026-07-10", {"type": "manual_html", "stage": "sent"})
    r = orchestrator.set_html({"edition": "2026-07-10", "html": "<p>x</p>"})
    assert "warning" in r and "ENVIADA" in r["warning"]
