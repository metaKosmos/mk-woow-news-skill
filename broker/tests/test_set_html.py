# broker/tests/test_set_html.py — MAR-175: override de HTML por edição (sem redeploy)
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
import pytest
import orchestrator
from state_manager import StateManager, LocalStore


def _patch(tmp_path, monkeypatch):
    # set_html usa um tempdir mínimo próprio (não _workdir/secrets); stubamos _sm e as 2 publicações.
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "_publish_public",
                        lambda w, ed: (f"https://pub/nl/{ed}.html", ""))
    monkeypatch.setattr(orchestrator, "_publish_html_version",
                        lambda w, ed, stamp: f"https://pub/nl/hist/{ed}/{stamp}.html")
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

def test_set_html_appends_to_history(tmp_path, monkeypatch):
    # Cada set_html guarda uma versão nova (não sobrescreve o histórico).
    sm = _patch(tmp_path, monkeypatch)
    sm.upsert_edition("2026-07-11", {"type": "manual_html", "stage": "ready"})
    r1 = orchestrator.set_html({"edition": "2026-07-11", "html": "<p>v1</p>",
                                "_email": "patrick@metakosmos.com.br"})
    r2 = orchestrator.set_html({"edition": "2026-07-11", "html": "<p>v2</p>",
                                "_email": "patrick@metakosmos.com.br"})
    assert r1["versions"] == 1 and r2["versions"] == 2
    hist = sm.get_state("2026-07-11")["html_history"]
    assert [h["source"] for h in hist] == ["set_html", "set_html"]
    assert hist[0]["url"] != hist[1]["url"]        # snapshots distintos (imutáveis)
    assert hist[0]["by"] == "patrick@metakosmos.com.br"
    row = [e for e in sm.get_queue()["editions"] if e["edition"] == "2026-07-11"][0]
    assert row["html_versions"] == 2
