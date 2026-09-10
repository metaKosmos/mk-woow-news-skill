# broker/tests/test_publicados_state.py — memória do que já foi publicado, por campanha.
#
# O índice é DERIVADO dos states em `sent`, como a queue: nada aqui é fonte. O que estes
# testes seguram é o custo (rebuild não pode disparar em upsert de métrica), o isolamento
# entre campanhas e o fail-open (memória ausente ou podre solta a pauta, não trava).
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from state_manager import (  # noqa: E402
    CAMPANHA_PADRAO, PUBLICADOS_MAX_EDICOES, PUBLICADOS_PREFIX,
    LocalStore, StateManager, campanha_da_edicao)


def _item(n, link=None, campo="manchete", source="Glossy"):
    it = {"campo": campo, "source_id": n, "source": source,
          "titulo_fonte": f"Título {n}"}
    if link is not None:
        it["link"] = link
    return it


def _prov(itens):
    return {"pool_size": 10, "publicados": len(itens), "itens": itens, "descartados": []}


def _grava(sm, edition, **campos):
    """Escreve o state cru, sem passar pelo upsert (que dispara os rebuilds)."""
    st = {"edition": edition, **campos}
    sm.store.write(f"editions/{edition}.state.json", json.dumps(st, ensure_ascii=False))
    return st


def _links(blob):
    return [l["link"] for l in blob["links"]]


# ------------------------------------------------------------------ o que entra no índice
def test_so_edicao_enviada_entra(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="researched", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/pesquisada")]))
    _grava(sm, "2026-09-02", stage="ready", date="2026-09-02",
           provenance=_prov([_item(2, "https://a.com/pronta")]))
    _grava(sm, "2026-09-03", stage="sent", date="2026-09-03",
           provenance=_prov([_item(3, "https://a.com/enviada")]))
    sm._rebuild_publicados()
    blob = sm.get_publicados(CAMPANHA_PADRAO)
    assert _links(blob) == ["https://a.com/enviada"]
    assert blob["edicoes"] == 1


def test_enviada_sem_provenance_nao_quebra_e_nao_entra(tmp_path):
    # Edição legada (anterior à v1.6.0) não tem provenance: conta como edição enviada,
    # mas não contribui link nenhum.
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-08-01", stage="sent", date="2026-08-01", subject="Legada")
    _grava(sm, "2026-09-03", stage="sent", date="2026-09-03",
           provenance=_prov([_item(3, "https://a.com/nova")]))
    sm._rebuild_publicados()
    blob = sm.get_publicados(CAMPANHA_PADRAO)
    assert _links(blob) == ["https://a.com/nova"]
    assert blob["edicoes"] == 2


def test_item_sem_link_e_ignorado(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-03", stage="sent", date="2026-09-03",
           provenance=_prov([_item(1), _item(2, ""), _item(3, "   "),
                             _item(4, "https://a.com/boa")]))
    sm._rebuild_publicados()
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/boa"]


def test_shape_gravado_carrega_procedencia(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-08", stage="sent", date="2026-09-08",
           provenance=_prov([_item(1, "https://www.glossy.co/x?utm_source=rss",
                                   campo="secundaria_1", source="Glossy")]))
    sm._rebuild_publicados()
    link = sm.get_publicados(CAMPANHA_PADRAO)["links"][0]
    # Link CRU: a query de tracking continua aí, porque normalizar é trabalho do research.
    assert link["link"] == "https://www.glossy.co/x?utm_source=rss"
    assert link["edition"] == "2026-09-08"
    assert link["date"] == "2026-09-08"
    assert link["campo"] == "secundaria_1"
    assert link["source"] == "Glossy"
    assert link["titulo"] == "Título 1"


def test_date_ausente_cai_na_chave_da_edicao(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-07-04", stage="sent",
           provenance=_prov([_item(1, "https://a.com/sem-date")]))
    sm._rebuild_publicados()
    assert sm.get_publicados(CAMPANHA_PADRAO)["links"][0]["date"] == "2026-07-04"


# ------------------------------------------------------------------------- por campanha
def test_isolamento_entre_campanhas(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01", campanha="daily-drops",
           provenance=_prov([_item(1, "https://a.com/drop")]))
    _grava(sm, "2026-09-02", stage="sent", date="2026-09-02", campanha="fashion-weekly",
           provenance=_prov([_item(2, "https://b.com/weekly")]))
    sm._rebuild_publicados()

    assert _links(sm.get_publicados("daily-drops")) == ["https://a.com/drop"]
    assert _links(sm.get_publicados("fashion-weekly")) == ["https://b.com/weekly"]
    assert sorted(sm.store.list_keys(PUBLICADOS_PREFIX)) == [
        f"{PUBLICADOS_PREFIX}daily-drops.json",
        f"{PUBLICADOS_PREFIX}fashion-weekly.json"]


def test_retrocompat_edicao_sem_campanha_cai_no_padrao(tmp_path):
    # Mesmo caso do `type` ausente = news_auto: state legado não tem `campanha`.
    sm = StateManager(LocalStore(tmp_path))
    assert campanha_da_edicao({"stage": "sent"}) == CAMPANHA_PADRAO
    assert campanha_da_edicao({"campanha": ""}) == CAMPANHA_PADRAO
    _grava(sm, "2026-06-13", stage="sent", date="2026-06-13", subject="Legada",
           provenance=_prov([_item(1, "https://a.com/legada")]))
    sm._rebuild_publicados()
    sm._rebuild_queue()
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/legada"]
    row = [e for e in sm.get_queue()["editions"] if e["edition"] == "2026-06-13"][0]
    assert row["campanha"] == CAMPANHA_PADRAO


def test_campanha_que_perdeu_as_enviadas_fica_vazia(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01", campanha="daily-drops",
           provenance=_prov([_item(1, "https://a.com/drop")]))
    _grava(sm, "2026-09-02", stage="sent", date="2026-09-02", campanha="fashion-weekly",
           provenance=_prov([_item(2, "https://b.com/weekly")]))
    sm._rebuild_publicados()
    assert _links(sm.get_publicados("fashion-weekly")) == ["https://b.com/weekly"]

    sm.reset_edition("2026-09-02")  # a única enviada da fashion-weekly
    blob = sm.get_publicados("fashion-weekly")
    assert blob["links"] == []          # esvaziado, não deixado para trás
    assert blob["edicoes"] == 0
    assert _links(sm.get_publicados("daily-drops")) == ["https://a.com/drop"]


# ------------------------------------------------------------------ teto e idempotência
def test_teto_corta_as_mais_antigas(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    total = PUBLICADOS_MAX_EDICOES + 5
    for i in range(total):
        ed = f"2026-{i:04d}"
        _grava(sm, ed, stage="sent", date=ed,
               provenance=_prov([_item(i, f"https://a.com/{i}")]))
    sm._rebuild_publicados()
    blob = sm.get_publicados(CAMPANHA_PADRAO)

    assert blob["edicoes"] == PUBLICADOS_MAX_EDICOES
    edicoes = {l["edition"] for l in blob["links"]}
    assert f"2026-{total - 1:04d}" in edicoes   # a mais nova ficou
    assert "2026-0000" not in edicoes           # a mais antiga saiu
    assert min(edicoes) == f"2026-{total - PUBLICADOS_MAX_EDICOES:04d}"


def test_rebuild_e_idempotente(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/um"), _item(2, "https://a.com/dois")]))
    _grava(sm, "2026-09-02", stage="sent", date="2026-09-02",
           provenance=_prov([_item(3, "https://a.com/tres")]))
    sm._rebuild_publicados()
    antes = sm.get_publicados(CAMPANHA_PADRAO)

    os.remove(os.path.join(str(tmp_path), PUBLICADOS_PREFIX, f"{CAMPANHA_PADRAO}.json"))
    sm._rebuild_publicados()
    depois = sm.get_publicados(CAMPANHA_PADRAO)

    assert {k: v for k, v in antes.items() if k != "updated_at"} == \
           {k: v for k, v in depois.items() if k != "updated_at"}


# ------------------------------------------------------------------- contrato de saída
def test_rebuild_devolve_doc_por_campanha(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01", campanha="daily-drops",
           provenance=_prov([_item(1, "https://a.com/drop")]))
    _grava(sm, "2026-09-02", stage="sent", date="2026-09-02", campanha="fashion-weekly",
           provenance=_prov([_item(2, "https://b.com/weekly"), _item(3, "https://b.com/w2")]))
    docs = sm._rebuild_publicados()

    assert set(docs) == {"daily-drops", "fashion-weekly"}
    assert docs["daily-drops"]["edicoes"] == 1
    assert len(docs["fashion-weekly"]["links"]) == 2
    # o dict devolvido é o que foi gravado, não uma versão paralela
    assert docs["daily-drops"] == sm.get_publicados("daily-drops")

    sm.reset_edition("2026-09-02")
    docs = sm._rebuild_publicados()
    assert docs["fashion-weekly"]["links"] == []  # esvaziada também sai no relatório


def test_rebuild_publicados_publico_delega(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/um")]))
    publico = sm.rebuild_publicados()
    interno = sm._rebuild_publicados()
    assert set(publico) == set(interno) == {CAMPANHA_PADRAO}
    assert publico[CAMPANHA_PADRAO]["links"] == interno[CAMPANHA_PADRAO]["links"]
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/um"]


# ----------------------------------------------------------------------------- ganchos
def _conta_rebuilds(monkeypatch):
    """Instrumenta _rebuild_publicados preservando o comportamento real."""
    chamadas = []
    original = StateManager._rebuild_publicados

    def espiao(self):
        chamadas.append(1)
        return original(self)

    monkeypatch.setattr(StateManager, "_rebuild_publicados", espiao)
    return chamadas


def test_upsert_sent_dispara_rebuild(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    chamadas = _conta_rebuilds(monkeypatch)
    sm.upsert_edition("2026-09-05", {"stage": "sent", "date": "2026-09-05",
                                     "provenance": _prov([_item(1, "https://a.com/x")])})
    assert len(chamadas) == 1
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/x"]


def test_upsert_so_de_metrics_nao_dispara_rebuild(tmp_path, monkeypatch):
    # É esta asserção que segura o custo: _refresh_metrics faz um upsert por edição em
    # laço, e _rebuild_queue já relê todos os states. Um rebuild de publicados por métrica
    # somaria um segundo termo quadrático a cada sync.
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-09-05", {"stage": "sent", "date": "2026-09-05",
                                     "provenance": _prov([_item(1, "https://a.com/x")])})
    chamadas = _conta_rebuilds(monkeypatch)

    sm.upsert_edition("2026-09-05", {"metrics": {"open_rate": 0.42, "clicked": 3}})
    sm.upsert_edition("2026-09-05", {"health": {"candidates": 46}})
    assert chamadas == []

    # controle positivo: com o mesmo espião, provenance no patch AINDA dispara.
    sm.upsert_edition("2026-09-05", {"provenance": _prov([_item(2, "https://a.com/y")])})
    assert len(chamadas) == 1


def test_reset_tira_os_links_da_edicao(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-09-05", {"stage": "sent", "date": "2026-09-05",
                                     "provenance": _prov([_item(1, "https://a.com/fica")])})
    sm.upsert_edition("2026-09-06", {"stage": "sent", "date": "2026-09-06",
                                     "provenance": _prov([_item(2, "https://a.com/sai")])})
    assert sorted(_links(sm.get_publicados(CAMPANHA_PADRAO))) == \
        ["https://a.com/fica", "https://a.com/sai"]

    sm.reset_edition("2026-09-06")
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/fica"]


# --------------------------------------------------------------------------- fail-open
def test_get_publicados_nasce_sozinho_sem_migracao(tmp_path):
    # Sem nenhum blob no store: a leitura reconstrói e devolve o que os states já dizem.
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/um")]))
    assert sm.store.list_keys(PUBLICADOS_PREFIX) == []
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/um"]


def test_campanha_inexistente_devolve_shape_vazio(tmp_path):
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/um")]))
    blob = sm.get_publicados("campanha-que-nao-existe")
    assert blob == {"campanha": "campanha-que-nao-existe", "updated_at": blob["updated_at"],
                    "edicoes": 0, "links": []}


def test_blob_ilegivel_devolve_shape_vazio(tmp_path):
    # Memória podre significa "não barra ninguém": uma trava que barra tudo quando quebra
    # é indistinguível de uma trava que funciona.
    sm = StateManager(LocalStore(tmp_path))
    for i, lixo in enumerate(["{{{", "[]", "null", '{"campanha": "x", "links": "nao-lista"}']):
        campanha = f"podre-{i}"
        sm.store.write(f"{PUBLICADOS_PREFIX}{campanha}.json", lixo)
        blob = sm.get_publicados(campanha)
        assert blob["links"] == []
        assert blob["edicoes"] == 0
        assert blob["campanha"] == campanha
