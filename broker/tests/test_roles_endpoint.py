# broker/tests/test_roles_endpoint.py — glue do orchestrator (list/update) com store local.
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import orchestrator
from state_manager import StateManager, LocalStore


def _wire(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_EMAILS", "david@metakosmos.com.br")
    monkeypatch.setenv("OPERATOR_EMAILS", "joao@metakosmos.com.br;patrick@metakosmos.com.br")
    monkeypatch.setenv("ALLOWED_DOMAIN", "metakosmos.com.br")
    sm = StateManager(LocalStore(str(tmp_path)))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


def test_list_roles_before_materialization(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    r = orchestrator.list_roles()
    assert r["materialized"] is False
    assert r["admins"] == ["david@metakosmos.com.br"]
    assert set(r["operators"]) == {"david@metakosmos.com.br", "joao@metakosmos.com.br",
                                   "patrick@metakosmos.com.br"}
    assert r["floor_admins"] == ["david@metakosmos.com.br"]


def test_promote_joao_persists_and_materializes(monkeypatch, tmp_path):
    sm = _wire(monkeypatch, tmp_path)
    out = orchestrator.update_roles({"action": "add-admin",
                                     "email": "joao@metakosmos.com.br",
                                     "_email": "david@metakosmos.com.br"})
    assert out["ok"] and "joao@metakosmos.com.br" in out["admins"]
    assert out["updated_by"] == "david@metakosmos.com.br" and out["updated_at"]
    # materializou no GCS/local e persiste entre chamadas
    persisted = sm.read_roles()
    assert "joao@metakosmos.com.br" in persisted["admins"]
    again = orchestrator.list_roles()
    assert again["materialized"] is True
    assert "joao@metakosmos.com.br" in again["admins"]


def test_update_rejects_foreign_domain(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    try:
        orchestrator.update_roles({"action": "add-operator", "email": "x@gmail.com",
                                   "_email": "david@metakosmos.com.br"})
        assert False, "deveria recusar domínio externo"
    except ValueError:
        pass
