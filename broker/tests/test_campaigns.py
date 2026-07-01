# broker/tests/test_campaigns.py — MAR-176/177: campanha com type + generate manual_html
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))
import pytest
import orchestrator
from state_manager import StateManager, LocalStore


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


# ------------------------------------------------------------- create_campaign (MAR-176)
def test_create_campaign_news_auto(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    r = orchestrator.create_campaign({"edition": "2026-07-01", "type": "news_auto"})
    assert r == {"edition": "2026-07-01", "type": "news_auto", "stage": "empty"}
    assert sm.get_state("2026-07-01")["type"] == "news_auto"
    assert sm.get_state("2026-07-01")["stage"] == "empty"

def test_create_campaign_manual_html(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    r = orchestrator.create_campaign({"edition": "camp-x", "type": "manual_html"})
    assert r["type"] == "manual_html"
    assert sm.get_state("camp-x")["type"] == "manual_html"

def test_create_campaign_default_type_is_news_auto(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    r = orchestrator.create_campaign({"edition": "2026-07-03"})
    assert r["type"] == "news_auto"

def test_create_campaign_invalid_type(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.create_campaign({"edition": "e", "type": "foo"})

def test_create_campaign_requires_edition(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.create_campaign({"type": "manual_html"})

def test_create_campaign_conflict_blocks_divergent_type(tmp_path, monkeypatch):
    # Edição já em andamento como news_auto não pode ser recriada como manual_html (evita híbrido).
    sm = _local_sm(tmp_path, monkeypatch)
    sm.upsert_edition("2026-07-04", {"type": "news_auto", "stage": "researched"})
    with pytest.raises(ValueError):
        orchestrator.create_campaign({"edition": "2026-07-04", "type": "manual_html"})

def test_create_campaign_same_type_in_progress_ok(tmp_path, monkeypatch):
    # Recriar com o MESMO type (idempotente) não levanta e preserva o stage.
    sm = _local_sm(tmp_path, monkeypatch)
    sm.upsert_edition("2026-07-05", {"type": "manual_html", "stage": "ready"})
    r = orchestrator.create_campaign({"edition": "2026-07-05", "type": "manual_html"})
    assert r["stage"] == "ready"
    assert sm.get_state("2026-07-05")["stage"] == "ready"


# ------------------------------------------------------------- _generate_manual_html (MAR-177)
def _manual_wd(tmp_path):
    wd = tmp_path / "wd"
    (wd / "renders").mkdir(parents=True)
    (wd / "content").mkdir(parents=True)
    return wd

def test_generate_manual_html_publishes_and_marks_ready(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    wd = _manual_wd(tmp_path)
    monkeypatch.setattr(orchestrator, "_publish_public",
                        lambda w, ed: (f"https://pub/nl/{ed}.html", ""))
    out = orchestrator._generate_manual_html(
        sm, wd, "2026-07-06",
        {"html": "<html>oi mK</html>", "subject": "Assunto WooW!",
         "preheader": "prévia", "list_key": "LK123"})
    assert out["stage"] == "ready" and out["type"] == "manual_html"
    assert out["preview_url"] == "https://pub/nl/2026-07-06.html"
    # HTML escrito no renders/ (o que o _publish_public consome)
    assert (wd / "renders" / "woow-2026-07-06.html").read_text() == "<html>oi mK</html>"
    st = sm.get_state("2026-07-06")
    assert st["stage"] == "ready"
    assert st["type"] == "manual_html"
    assert st["subject"] == "Assunto WooW!"
    assert st["preheader"] == "prévia"
    assert st["preview_url"] == "https://pub/nl/2026-07-06.html"
    assert st["list_key"] == "LK123"  # lista por campanha gravada no estado
    # .md com front-matter mínimo persistido no store
    md = sm.store.read("content/2026-07-06.md")
    assert md.startswith("---") and "Assunto WooW!" in md

def test_generate_manual_html_requires_html_and_subject(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    wd = _manual_wd(tmp_path)
    monkeypatch.setattr(orchestrator, "_publish_public", lambda w, ed: ("u", ""))
    with pytest.raises(ValueError):
        orchestrator._generate_manual_html(sm, wd, "e", {"subject": "só subject"})
    with pytest.raises(ValueError):
        orchestrator._generate_manual_html(sm, wd, "e", {"html": "<p>sem subject</p>"})

def test_generate_manual_html_no_list_key_leaves_state_clean(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    wd = _manual_wd(tmp_path)
    monkeypatch.setattr(orchestrator, "_publish_public", lambda w, ed: ("u", ""))
    orchestrator._generate_manual_html(sm, wd, "no-lk", {"html": "<p>x</p>", "subject": "S"})
    assert "list_key" not in sm.get_state("no-lk")

def test_manual_md_parses_in_send_zma_read_meta(tmp_path, monkeypatch):
    # Prova real: o front-matter mínimo gerado é lido pelo próprio read_meta do send_zma.
    import send_zma
    sm = StateManager(LocalStore(tmp_path))
    wd = _manual_wd(tmp_path)
    monkeypatch.setattr(orchestrator, "_publish_public", lambda w, ed: ("u", ""))
    orchestrator._generate_manual_html(sm, wd, "2026-07-07",
                                       {"html": "<p>x</p>", "subject": "Olá WooW!"})
    meta = send_zma.read_meta(wd / "content" / "2026-07-07.md")
    assert meta["subject"] == "Olá WooW!"
