# broker/tests/test_notify_version.py — MAR-428: aviso de versão, registro e release
import json
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))
import pytest
import main
import orchestrator
from state_manager import StateManager, LocalStore


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


# ------------------------------------------------------------------ comparação de versão
@pytest.mark.parametrize("cliente,publicada,esperado", [
    ("1.4.0", "1.5.0", True),     # atrás: avisa
    ("1.5.0", "1.5.0", False),    # em dia
    ("1.6.0", "1.5.0", False),    # à frente (dev local) não é problema do operador
    ("1.4.9", "1.5.0", True),
    ("", "1.5.0", False),         # skill antiga, nem saberia ler o aviso
    (None, "1.5.0", False),
    ("1.4.0-beta", "1.5.0", False),   # não é semver numérico: silêncio > aviso errado
    ("1.4.0", "", False),         # broker sem SKILL_VERSION: não inventa aviso
])
def test_outdated(cliente, publicada, esperado):
    assert main.outdated(cliente, publicada) is esperado


# ------------------------------------------------------------------ nota de release
def test_set_release_grava_autoria(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    r = orchestrator.set_release({"version": "1.5.0", "notes": "fontes viraram autosserviço",
                                  "_email": "david@metakosmos.com.br"})
    assert r["version"] == "1.5.0" and r["notes"] == "fontes viraram autosserviço"
    assert r["by"] == "david@metakosmos.com.br" and r["at"]
    assert orchestrator.get_release()["notes"] == "fontes viraram autosserviço"


def test_set_release_exige_versao(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_release({"notes": "sem versão"})


def test_update_notice_traz_a_nota_da_versao_publicada(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    orchestrator.set_release({"version": "1.5.0", "notes": "sources add/test"})
    aviso = orchestrator.update_notice("1.4.0", "1.5.0")
    assert aviso["client_version"] == "1.4.0" and aviso["latest_version"] == "1.5.0"
    assert aviso["notes"] == "sources add/test"
    assert "1.5.0" in aviso["message"] and "marketplace update" in aviso["message"]


def test_update_notice_ignora_nota_de_outra_versao(tmp_path, monkeypatch):
    """Nota de release velha confunde mais do que ajuda."""
    _local_sm(tmp_path, monkeypatch)
    orchestrator.set_release({"version": "1.4.0", "notes": "coisa antiga"})
    assert orchestrator.update_notice("1.3.0", "1.5.0")["notes"] == ""


def test_update_notice_sem_release_nenhuma(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    aviso = orchestrator.update_notice("1.4.0", "1.5.0")
    assert aviso["notes"] == "" and aviso["message"]


# ------------------------------------------------------------------ registro de clientes
def _spy_writes(sm, monkeypatch):
    escritas = []
    original = sm.store.write

    def _write(key, data):
        escritas.append(key)
        return original(key, data)

    monkeypatch.setattr(sm.store, "write", _write)
    return escritas


def test_record_client_grava_na_primeira_vez(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.record_client("patrick@metakosmos.com.br", "1.4.0", "/queue")
    reg = orchestrator.get_clients()["clients"]["patrick@metakosmos.com.br"]
    assert reg["version"] == "1.4.0" and reg["last_path"] == "/queue" and reg["last_seen"]


def test_record_client_nao_regrava_no_mesmo_dia(tmp_path, monkeypatch):
    """Registro serve para saber quem está atrasado, não para auditar cada chamada."""
    sm = _local_sm(tmp_path, monkeypatch)
    escritas = _spy_writes(sm, monkeypatch)
    orchestrator.record_client("p@metakosmos.com.br", "1.5.0", "/queue")
    orchestrator.record_client("p@metakosmos.com.br", "1.5.0", "/status")
    orchestrator.record_client("p@metakosmos.com.br", "1.5.0", "/metrics")
    assert escritas.count(orchestrator.CLIENTS_KEY) == 1


def test_record_client_regrava_quando_a_versao_muda(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    escritas = _spy_writes(sm, monkeypatch)
    orchestrator.record_client("p@metakosmos.com.br", "1.4.0", "/queue")
    orchestrator.record_client("p@metakosmos.com.br", "1.5.0", "/queue")
    assert escritas.count(orchestrator.CLIENTS_KEY) == 2
    assert orchestrator.get_clients()["clients"]["p@metakosmos.com.br"]["version"] == "1.5.0"


def test_record_client_sem_email_nao_faz_nada(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    assert orchestrator.record_client("", "1.5.0", "/queue") is None
    assert sm.store.read(orchestrator.CLIENTS_KEY) in (None, "")


# ------------------------------------------------------------------ quem está atrasado
def _semeia(sm):
    sm.store.write(orchestrator.CLIENTS_KEY, json.dumps({"clients": {
        "david@metakosmos.com.br": {"version": "1.5.0", "last_seen": "2026-08-25T09:00:00-03:00"},
        "patrick@metakosmos.com.br": {"version": "1.3.0", "last_seen": "2026-08-24T10:00:00-03:00"},
        "joao@metakosmos.com.br": {"version": "", "last_seen": "2026-08-20T10:00:00-03:00"},
    }}))


def test_atrasados_inclui_quem_nao_manda_versao(tmp_path, monkeypatch):
    """Cliente sem versão registrada é skill velha demais para mandar o header."""
    sm = _local_sm(tmp_path, monkeypatch)
    _semeia(sm)
    fora = [x["email"] for x in orchestrator.get_clients_report("1.5.0")["atrasados"]]
    assert fora == ["joao@metakosmos.com.br", "patrick@metakosmos.com.br"]


def test_report_de_admin_traz_a_tabela(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _semeia(sm)
    r = orchestrator.get_clients_report("1.5.0", full=True)
    assert len(r["clients"]) == 3 and r["published"] == "1.5.0"


def test_report_de_operador_so_mostra_ele(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _semeia(sm)
    r = orchestrator.get_clients_report("1.5.0", full=False, email="patrick@metakosmos.com.br")
    assert list(r["clients"]) == ["patrick@metakosmos.com.br"]
    assert r["atrasados_total"] == 2 and "atrasados" not in r


def test_report_sem_ninguem_registrado(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    r = orchestrator.get_clients_report("1.5.0")
    assert r["clients"] == {} and r["atrasados"] == []


# ------------------------------------------------------------------ papéis
def test_release_e_rota_de_admin():
    admins = {"david@metakosmos.com.br"}
    ops = {"patrick@metakosmos.com.br"}
    assert main.authorize("david@metakosmos.com.br", "/admin/release", admins, ops)
    assert not main.authorize("patrick@metakosmos.com.br", "/admin/release", admins, ops)
    # ver quem está em qual versão é ação de operador
    assert main.authorize("patrick@metakosmos.com.br", "/clients", admins, ops)


# ------------------------------------------------------------------ costura handler/aviso
def _notice_fake(cli, pub):
    return {"client_version": cli, "latest_version": pub, "notes": "", "message": "atualize"}


def test_with_notice_injeta_quando_atrasado():
    out = main.with_notice({"ok": True}, 200, "1.4.0", "1.5.0", _notice_fake)
    assert out["ok"] is True and out["_aviso"]["latest_version"] == "1.5.0"


def test_with_notice_calado_quando_em_dia():
    assert main.with_notice({"ok": True}, 200, "1.5.0", "1.5.0", _notice_fake) == {"ok": True}


def test_with_notice_nao_mexe_em_erro():
    """403 e 502 saem limpos: quem está tratando erro não precisa de ruído de versão."""
    assert main.with_notice({"error": "x"}, 403, "1.4.0", "1.5.0", _notice_fake) == {"error": "x"}


def test_with_notice_ignora_corpo_que_nao_e_dict():
    assert main.with_notice([1, 2], 200, "1.4.0", "1.5.0", _notice_fake) == [1, 2]


def test_with_notice_engole_falha_do_aviso():
    """Se o GCS cair, a resposta sai sem aviso — nunca com erro."""
    def _explode(cli, pub):
        raise RuntimeError("GCS fora")
    assert main.with_notice({"ok": True}, 200, "1.4.0", "1.5.0", _explode) == {"ok": True}


# ------------------------------------------------------------------ quem nunca apareceu
ROSTER = {"david@metakosmos.com.br", "patrick@metakosmos.com.br", "joao@metakosmos.com.br"}


def test_quem_nunca_chamou_conta_como_atrasado(tmp_path, monkeypatch):
    """clients.json nasce vazio no deploy. Sem o roster, o relatório diria "todo mundo em
    dia" exatamente no dia em que ninguém atualizou — e o texto do release sairia mentindo."""
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.record_client("david@metakosmos.com.br", "1.5.0", "/clients")
    r = orchestrator.get_clients_report("1.5.0", full=True, roster=ROSTER)
    fora = {x["email"]: x for x in r["atrasados"]}
    assert set(fora) == {"patrick@metakosmos.com.br", "joao@metakosmos.com.br"}
    assert all(x["nunca_chamou"] for x in fora.values())


def test_roster_nao_duplica_quem_ja_esta_atrasado(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.record_client("patrick@metakosmos.com.br", "1.3.0", "/queue")
    r = orchestrator.get_clients_report("1.5.0", full=True, roster=ROSTER)
    patrick = [x for x in r["atrasados"] if x["email"] == "patrick@metakosmos.com.br"]
    assert len(patrick) == 1 and patrick[0]["nunca_chamou"] is False
    assert patrick[0]["version"] == "1.3.0"


def test_sem_roster_o_comportamento_e_o_de_antes(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.record_client("david@metakosmos.com.br", "1.5.0", "/clients")
    assert orchestrator.get_clients_report("1.5.0", full=True)["atrasados"] == []


def test_operador_ve_a_contagem_com_roster(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.record_client("patrick@metakosmos.com.br", "1.3.0", "/queue")
    r = orchestrator.get_clients_report("1.5.0", full=False,
                                        email="patrick@metakosmos.com.br", roster=ROSTER)
    assert r["atrasados_total"] == 3 and "atrasados" not in r
