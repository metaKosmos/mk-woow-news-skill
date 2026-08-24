# broker/tests/test_sources.py — MAR-426: fontes RSS como estado mutável de operador
import json
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))
import pytest
import yaml
import orchestrator
import research
from state_manager import StateManager, LocalStore


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


def _write(sm, feeds, **meta):
    doc = {"feeds": feeds, "set_by": "", "set_at": "", "tested_by": "", "tested_at": "", **meta}
    sm.store.write(orchestrator.SOURCES_KEY, json.dumps(doc, ensure_ascii=False))
    return doc


# ------------------------------------------------------------------ seed (sem sources.json)
def test_get_sources_faz_seed_do_yaml(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    r = orchestrator.get_sources()
    assert r["source"] == "config"
    nomes = [f["source"] for f in r["feeds"]]
    assert "Modern Retail" in nomes and "E-Commerce Brasil" in nomes
    assert all("enabled" in f and "last_test" in f for f in r["feeds"])


def test_seed_respeita_enabled_false(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    ecb = next(f for f in orchestrator.get_sources()["feeds"] if f["source"] == "E-Commerce Brasil")
    assert ecb["enabled"] is False
    assert "E-Commerce Brasil" not in [f["source"] for f in orchestrator._effective_feeds()]


def test_effective_feeds_so_ativas_e_so_source_url(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": False}])
    assert orchestrator._effective_feeds() == [{"source": "A", "url": "https://a/feed"}]


# ------------------------------------------------------------------ add
def test_add_grava_autoria_e_vira_estado(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    r = orchestrator.set_sources({"op": "add", "source": "Retail Dive",
                                  "url": "https://www.retaildive.com/feeds/news/",
                                  "_email": "patrick@metakosmos.com.br"})
    assert r["op"] == "add" and r["set_by"] == "patrick@metakosmos.com.br"
    cur = orchestrator.get_sources(sm)
    assert cur["source"] == "state"
    nova = next(f for f in cur["feeds"] if f["source"] == "Retail Dive")
    assert nova["enabled"] is True
    assert nova["added_by"] == "patrick@metakosmos.com.br" and nova["added_at"]


def test_add_carimba_at_no_last_test_do_probe(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_sources({"op": "add", "source": "X", "url": "https://x/feed",
                              "last_test": {"status": "ok", "found": 9, "kept": 3, "error": None}})
    nova = next(f for f in orchestrator.get_sources(sm)["feeds"] if f["source"] == "X")
    assert nova["last_test"]["found"] == 9 and nova["last_test"]["at"]


@pytest.mark.parametrize("url", ["", "retaildive.com/feed", "ftp://x/feed", None])
def test_add_rejeita_url_invalida(tmp_path, monkeypatch, url):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "add", "source": "X", "url": url})


def test_add_rejeita_nome_duplicado(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "add", "source": "modern retail", "url": "https://outra/feed"})


def test_add_rejeita_url_duplicada_ignorando_barra_e_caixa(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://A.com/Feed/", "enabled": True}])
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "add", "source": "B", "url": "https://a.com/feed"})


def test_add_sem_nome(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "add", "source": "  ", "url": "https://x/feed"})


# ------------------------------------------------------------------ set-url / enable / disable / remove
def test_set_url_troca_e_zera_last_test(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "Fast Company", "url": "https://www.fastcompany.com/feed",
                 "enabled": True, "last_test": {"status": "erro", "error": "403"}},
                {"source": "Outra", "url": "https://outra/feed", "enabled": True}])
    orchestrator.set_sources({"op": "set-url", "source": "Fast Company",
                              "url": "https://www.fastcompany.com/latest/rss"})
    f = next(x for x in orchestrator.get_sources(sm)["feeds"] if x["source"] == "Fast Company")
    assert f["url"] == "https://www.fastcompany.com/latest/rss"
    assert f["last_test"] is None


def test_set_url_rejeita_url_de_outra_fonte(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": True}])
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "set-url", "source": "A", "url": "https://b/feed"})


def test_disable_e_enable(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": True}])
    assert orchestrator.set_sources({"op": "disable", "source": "A"})["active"] == 1
    assert orchestrator.set_sources({"op": "enable", "source": "A"})["active"] == 2


def test_remove_tira_da_lista(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": True}])
    r = orchestrator.set_sources({"op": "remove", "source": "B"})
    assert r["active"] == 1
    assert [f["source"] for f in orchestrator.get_sources(sm)["feeds"]] == ["A"]


def test_fonte_inexistente(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "disable", "source": "Não Existe"})


def test_op_invalida(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": "drop-all", "source": "A"})


# ------------------------------------------------------------------ guarda da última fonte ativa
@pytest.mark.parametrize("op", ["disable", "remove"])
def test_nao_deixa_pesquisa_sem_fonte(tmp_path, monkeypatch, op):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": False}])
    with pytest.raises(ValueError):
        orchestrator.set_sources({"op": op, "source": "A"})
    assert orchestrator._effective_feeds() == [{"source": "A", "url": "https://a/feed"}]


def test_remove_de_fonte_ja_desativada_passa(tmp_path, monkeypatch):
    """A guarda vale para a última ATIVA — tirar uma desativada nunca esvazia a pesquisa."""
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": False}])
    orchestrator.set_sources({"op": "remove", "source": "B"})
    assert [f["source"] for f in orchestrator.get_sources(sm)["feeds"]] == ["A"]


# ------------------------------------------------------------------ autoria: editar != testar
def test_teste_nao_sobrescreve_quem_editou(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_sources({"op": "add", "source": "A", "url": "https://a/feed",
                              "_email": "joao@metakosmos.com.br"})
    monkeypatch.setattr(orchestrator, "_probe_feeds",
                        lambda feeds: [{"source": f["source"], "found": 5, "kept": 2, "error": None}
                                       for f in feeds])
    orchestrator.test_sources({"_email": "patrick@metakosmos.com.br"})
    cur = orchestrator.get_sources(sm)
    assert cur["set_by"] == "joao@metakosmos.com.br"
    assert cur["tested_by"] == "patrick@metakosmos.com.br"


def test_test_sources_persiste_last_test(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": True}])
    monkeypatch.setattr(orchestrator, "_probe_feeds", lambda feeds: [
        {"source": "A", "found": 10, "kept": 4, "error": None},
        {"source": "B", "found": 0, "kept": 0, "error": "HTTPError: 403"}])
    r = orchestrator.test_sources({})
    assert r["persisted"] is True and len(r["report"]) == 2
    feeds = {f["source"]: f for f in orchestrator.get_sources(sm)["feeds"]}
    assert feeds["A"]["last_test"]["status"] == "ok" and feeds["A"]["last_test"]["found"] == 10
    assert feeds["B"]["last_test"]["status"] == "erro" and "403" in feeds["B"]["last_test"]["error"]


def test_test_sources_url_avulsa_nao_grava(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrator, "_probe_feeds",
                        lambda feeds: [{"source": feeds[0]["source"], "found": 3, "kept": 1, "error": None}])
    r = orchestrator.test_sources({"source": "Nova", "url": "https://nova/feed"})
    assert r["persisted"] is False
    assert sm.store.read(orchestrator.SOURCES_KEY) in (None, "")


def test_test_sources_so_uma_fonte_mesmo_desativada(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "A", "url": "https://a/feed", "enabled": True},
                {"source": "B", "url": "https://b/feed", "enabled": False}])
    vistos = []

    def _fake_probe(feeds):
        vistos.extend(feeds)
        return [{"source": f["source"], "found": 0, "kept": 0, "error": None} for f in feeds]

    monkeypatch.setattr(orchestrator, "_probe_feeds", _fake_probe)
    orchestrator.test_sources({"source": "B"})
    assert [f["source"] for f in vistos] == ["B"]


def test_test_sources_fonte_inexistente(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        orchestrator.test_sources({"source": "Não Existe"})


# ------------------------------------------------------------------ a ligação: workdir usa o estado
def test_workdir_materializa_as_fontes_do_estado(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _write(sm, [{"source": "Só Essa", "url": "https://so-essa/feed", "enabled": True},
                {"source": "Desligada", "url": "https://off/feed", "enabled": False}])
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {"K": "v"})
    wd = orchestrator._workdir("2026-08-24")
    try:
        escrito = yaml.safe_load((wd / "config" / "feeds.yaml").read_text(encoding="utf-8"))
    finally:
        import shutil; shutil.rmtree(wd, ignore_errors=True)
    assert escrito == {"feeds": [{"source": "Só Essa", "url": "https://so-essa/feed"}]}


# ------------------------------------------------------------------ research.py (funções puras)
def test_enabled_feeds_default_true():
    feeds = [{"source": "A", "url": "u"}, {"source": "B", "url": "u2", "enabled": False},
             {"source": "C", "url": "u3", "enabled": True}]
    assert [f["source"] for f in research.enabled_feeds(feeds)] == ["A", "C"]


def test_report_as_dicts():
    assert research.report_as_dicts([("A", 10, 3, None), ("B", 0, 0, "403")]) == [
        {"source": "A", "found": 10, "kept": 3, "error": None},
        {"source": "B", "found": 0, "kept": 0, "error": "403"}]
