# broker/tests/test_sender.py — MAR-175: autosserviço de remetente (settings.json, global)
import sys, pathlib, json
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
import pytest
import orchestrator
from state_manager import StateManager, LocalStore


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


# ------------------------------------------------------------- get_active_sender
def test_get_active_sender_default_from_config(tmp_path):
    # Sem settings.json, cai no delivery do newsletter.yaml (patrick@ / "WooW! Daily Drops").
    sm = StateManager(LocalStore(tmp_path))
    s = orchestrator.get_active_sender(sm)
    assert s["from_email"] == "patrick@metakosmos.com.br"
    assert s["from_name"] == "WooW! Daily Drops"
    assert s["source"] == "config"

def test_get_active_sender_override_from_settings(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.store.write("settings.json", json.dumps({"active_from_email": "novo@metakosmos.com.br",
                                                "active_from_name": "mK Campanhas"}))
    s = orchestrator.get_active_sender(sm)
    assert s["from_email"] == "novo@metakosmos.com.br"
    assert s["from_name"] == "mK Campanhas"
    assert s["source"] == "settings"


# ------------------------------------------------------------- set_sender
def test_set_sender_verified_no_warning(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrator, "_check_sender_verified", lambda e: (True, "zma"))
    r = orchestrator.set_sender({"from_email": "patrick@metakosmos.com.br",
                                 "from_name": "WooW!", "_email": "op@metakosmos.com.br"})
    assert r["verified"] is True
    assert "warning" not in r
    assert json.loads(sm.store.read("settings.json"))["active_from_email"] == "patrick@metakosmos.com.br"

def test_set_sender_unverified_warns_but_saves(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrator, "_check_sender_verified", lambda e: (False, "allowlist"))
    r = orchestrator.set_sender({"from_email": "david@metakosmos.com.br",
                                 "_email": "op@metakosmos.com.br"})
    assert r["verified"] is False
    assert "6610" in r["warning"]
    # gravou mesmo assim (não bloqueia)
    assert json.loads(sm.store.read("settings.json"))["active_from_email"] == "david@metakosmos.com.br"

def test_set_sender_preserves_active_list(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrator, "_check_sender_verified", lambda e: (True, "zma"))
    orchestrator.set_active_list({"list_key": "LK-INTERNA", "list_name": "Time mK"})
    orchestrator.set_sender({"from_email": "patrick@metakosmos.com.br", "_email": "op@x"})
    s = json.loads(sm.store.read("settings.json"))
    assert s["active_list_key"] == "LK-INTERNA"          # remetente não apagou a lista
    assert s["active_from_email"] == "patrick@metakosmos.com.br"

def test_set_sender_requires_valid_email(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_sender({"from_email": "  "})
    with pytest.raises(ValueError):
        orchestrator.set_sender({"from_email": "sem-arroba"})


# ------------------------------------------------------------- _check_sender_verified
def test_check_sender_verified_from_zma_live(monkeypatch):
    monkeypatch.setattr(orchestrator, "_run_manage_or_send_senders",
                        lambda: {"senders": [{"email": "novo@metakosmos.com.br", "verified": True}]})
    assert orchestrator._check_sender_verified("novo@metakosmos.com.br") == (True, "zma")
    assert orchestrator._check_sender_verified("outro@metakosmos.com.br") == (False, "zma")

def test_check_sender_verified_falls_back_to_allowlist(monkeypatch):
    # ZMA indisponível -> allowlist do newsletter.yaml (seed: patrick@).
    monkeypatch.setattr(orchestrator, "_run_manage_or_send_senders",
                        lambda: (_ for _ in ()).throw(RuntimeError("sem endpoint")))
    assert orchestrator._check_sender_verified("patrick@metakosmos.com.br") == (True, "allowlist")
    assert orchestrator._check_sender_verified("david@metakosmos.com.br") == (False, "allowlist")
