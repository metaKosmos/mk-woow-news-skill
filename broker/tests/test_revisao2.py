# broker/tests/test_revisao2.py — defeitos achados na 2a rodada de revisão adversarial,
# todos introduzidos pelas correções da 1a rodada.
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


# ---------------------------------------------- estado corrompido não vira erro de entrada
def test_json_quebrado_no_estado_nao_e_entrada_invalida():
    """json.JSONDecodeError É subclasse de ValueError. Mapear ValueError cru para 400 fazia
    settings.json corrompido responder 'Expecting property name' com cara de erro de
    digitação do operador, e sumir do log de erro."""
    assert issubclass(json.JSONDecodeError, ValueError)
    assert not issubclass(json.JSONDecodeError, orchestrator.EntradaInvalida)


def test_validacao_de_entrada_e_entrada_invalida(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_sources({"op": "add", "source": "X", "url": "sem-esquema.com/feed"})
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_release({"notes": "sem versão"})


@pytest.mark.parametrize("conteudo", ["[]", "null", '"texto"', "42"])
def test_json_valido_com_shape_errado_nao_derruba(tmp_path, monkeypatch, conteudo):
    """`[]` e `null` passam pelo json.loads e estouram AttributeError no .get() seguinte,
    que era exatamente o estrago que a guarda existia para impedir. Um send em 'ready' não
    pode morrer porque alguém deixou o sources.json num shape esquisito."""
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write(orchestrator.SOURCES_KEY, conteudo)
    assert orchestrator.get_sources()["source"] == "config-fallback"
    assert orchestrator._effective_feeds()
    sm.store.write(orchestrator.RELEASE_KEY, conteudo)
    assert orchestrator.get_release() == {}


# ---------------------------------------------- workdir não deixa lixo quando falha
def test_workdir_nao_vaza_tempdir(tmp_path, monkeypatch):
    """A leitura das fontes acontece depois do mkdtemp: quem chamou ainda não recebeu o
    caminho, então o finally dele não limpa nada."""
    import tempfile
    _local_sm(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrator, "_effective_feeds",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GCS fora")))
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {})
    antes = set(pathlib.Path(tempfile.gettempdir()).glob("woow-2026-08-25-*"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            orchestrator._workdir("2026-08-25")
    depois = set(pathlib.Path(tempfile.gettempdir()).glob("woow-2026-08-25-*"))
    assert depois == antes, f"tempdirs órfãos: {depois - antes}"


# ---------------------------------------------- teste antigo não sobrevive à troca de URL
def _com_teste(sm, monkeypatch):
    monkeypatch.setattr(orchestrator, "_probe_feeds", lambda feeds: [
        {"source": f["source"], "found": 9, "kept": 3, "error": None} for f in feeds])
    orchestrator.set_sources({"op": "add", "source": "Portal Teste",
                              "url": "https://portal-teste.com/feed"})
    orchestrator.test_sources({"source": "Portal Teste"})


def test_set_url_descarta_o_teste_da_url_antiga(tmp_path, monkeypatch):
    """Os testes são chaveados por NOME. Sem descartar, o merge ressuscitava o resultado da
    URL velha colado na nova, dizendo "ok" sobre um endereço que ninguém mediu."""
    sm = _local_sm(tmp_path, monkeypatch)
    _com_teste(sm, monkeypatch)
    orchestrator.set_sources({"op": "set-url", "source": "Portal Teste",
                              "url": "https://portal-teste.com/rss"})
    fc = next(f for f in orchestrator.get_sources(sm)["feeds"] if f["source"] == "Portal Teste")
    assert fc["url"].endswith("/rss")
    assert fc["last_test"] is None, "o teste da URL antiga voltou colado na nova"


def test_remove_e_recadastra_nao_herda_teste(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_sources({"op": "add", "source": "Outra", "url": "https://outra/feed"})
    _com_teste(sm, monkeypatch)
    orchestrator.set_sources({"op": "remove", "source": "Portal Teste"})
    orchestrator.set_sources({"op": "add", "source": "Portal Teste",
                              "url": "https://portal-teste.com/rss"})
    fc = next(f for f in orchestrator.get_sources(sm)["feeds"] if f["source"] == "Portal Teste")
    assert fc["last_test"] is None


# ---------------------------------------------- registro concorrente não se perde
def test_dois_operadores_simultaneos_nao_se_apagam(tmp_path, monkeypatch):
    """Com um documento único, quem lesse antes e escrevesse depois desfazia a atualização
    do outro: alguém que JÁ tinha atualizado voltava a aparecer como atrasado no release."""
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.record_client("david@metakosmos.com.br", "1.4.0", "/queue")
    orchestrator.record_client("patrick@metakosmos.com.br", "1.4.0", "/queue")
    # as duas instâncias leem o estado antigo e escrevem, uma depois da outra
    doc_antes = orchestrator.get_clients(sm)
    orchestrator.record_client("david@metakosmos.com.br", "1.5.0", "/status")
    assert doc_antes["clients"]["david@metakosmos.com.br"]["version"] == "1.4.0"  # leitura velha
    orchestrator.record_client("patrick@metakosmos.com.br", "1.5.1", "/status")
    agora = orchestrator.get_clients(sm)["clients"]
    assert agora["david@metakosmos.com.br"]["version"] == "1.5.0"
    assert agora["patrick@metakosmos.com.br"]["version"] == "1.5.1"


def test_clients_json_legado_continua_sendo_lido(tmp_path, monkeypatch):
    """Instalação que já tinha o documento único não pode perder o histórico no upgrade."""
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write(orchestrator.CLIENTS_KEY, json.dumps(
        {"clients": {"joao@metakosmos.com.br": {"version": "1.4.0", "last_seen": "2026-08-24T10:00:00-03:00"}}}))
    assert orchestrator.get_clients(sm)["clients"]["joao@metakosmos.com.br"]["version"] == "1.4.0"
