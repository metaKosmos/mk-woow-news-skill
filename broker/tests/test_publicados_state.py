# broker/tests/test_publicados_state.py — memória do que já foi publicado, por campanha.
#
# O índice é DERIVADO dos states em `sent`, como a queue: nada aqui é fonte. O que estes
# testes seguram é o custo (rebuild não pode disparar em upsert de métrica, nem se repetir
# para sempre em campanha sem envio), o isolamento entre campanhas, o fail-open (memória
# ausente, podre ou store fora do ar solta a pauta, não trava) e a assimetria de custo: o
# índice nunca pode derrubar um envio que já saiu, nem se apagar sozinho.
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
    # Edição legada (anterior à v1.6.0) não tem provenance: não quebra o rebuild, não
    # contribui link nenhum e, por isso, não conta como edição coberta pela memória.
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-08-01", stage="sent", date="2026-08-01", subject="Legada")
    _grava(sm, "2026-09-03", stage="sent", date="2026-09-03",
           provenance=_prov([_item(3, "https://a.com/nova")]))
    sm._rebuild_publicados()
    blob = sm.get_publicados(CAMPANHA_PADRAO)
    assert _links(blob) == ["https://a.com/nova"]
    assert blob["edicoes"] == 1


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


# ------------------------------------------------ correções da revisão adversarial
class _StorePublicadosForaDoAr(LocalStore):
    """LocalStore em que só o prefixo `publicados/` levanta: ler, gravar e listar.

    É o modo de falha real (bucket recusando aquele prefixo, permissão perdida em
    `publicados/`), não um monkeypatch do método que se quer provar. Os states continuam
    gravando normalmente, que é o ponto: o envio já saiu e o state tem que registrar."""

    def read(self, key):
        if key.startswith(PUBLICADOS_PREFIX):
            raise RuntimeError("503 lendo publicados/")
        return super().read(key)

    def write(self, key, data):
        if key.startswith(PUBLICADOS_PREFIX):
            raise RuntimeError("503 gravando publicados/")
        return super().write(key, data)

    def list_keys(self, prefix):
        if prefix.startswith(PUBLICADOS_PREFIX):
            raise RuntimeError("503 listando publicados/")
        return super().list_keys(prefix)


class _StoreSemListagemDeEdicoes(LocalStore):
    """Listar edições levanta: o rebuild que get_publicados dispara morre no meio."""

    def list_editions(self):
        raise RuntimeError("503 listando editions/")


class _StoreContador(LocalStore):
    """Conta leituras de objeto e listagens de edição, para medir o custo por chamada."""

    def __init__(self, root):
        super().__init__(root)
        self.leituras = 0
        self.listagens = 0

    def read(self, key):
        self.leituras += 1
        return super().read(key)

    def list_editions(self):
        self.listagens += 1
        return super().list_editions()

    def zera(self):
        self.leituras = self.listagens = 0


def test_rebuild_que_levanta_nao_derruba_o_envio(tmp_path):
    """O patch `stage=sent` é gravado DEPOIS de send_zma.py ter disparado a newsletter.

    Se o rebuild levantasse, o `except` do run_stage gravaria health.last_error e
    re-levantaria: o operador veria 502 num envio que já saiu, reenviaria, e a lista
    receberia duas vezes. Índice desatualizado é recuperável, envio duplicado não. Este é o
    caminho que `test_memoria_nunca_levanta` (do orchestrator) não alcança: lá o
    `get_publicados` é monkeypatchado, e o gancho do upsert nem entra em cena."""
    sm = StateManager(_StorePublicadosForaDoAr(tmp_path))
    st = sm.upsert_edition("2026-09-05", {"stage": "sent", "date": "2026-09-05",
                                          "provenance": _prov([_item(1, "https://a.com/x")])})
    assert st["stage"] == "sent"
    assert sm.get_state("2026-09-05")["stage"] == "sent"  # gravado, não só devolvido

    # reset_edition tem o mesmo gancho e a mesma regra: é operação de admin, não pode virar
    # 502 porque o índice está fora do ar.
    sm.reset_edition("2026-09-05")
    assert sm.get_state("2026-09-05")["stage"] == "empty"


def test_get_publicados_nao_levanta_com_o_store_fora_do_ar(tmp_path):
    """O docstring promete "nunca levanta" e o resto da trava assume isso: get_publicados
    roda no caminho de todo estágio, e `GET /curadoria` não pode virar 502."""
    sm = StateManager(_StorePublicadosForaDoAr(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/um")]))
    assert sm.get_publicados(CAMPANHA_PADRAO)["links"] == []

    # o outro ramo: a leitura devolve ausente e é o REBUILD que morre no meio.
    sm2 = StateManager(_StoreSemListagemDeEdicoes(tmp_path / "b"))
    blob = sm2.get_publicados(CAMPANHA_PADRAO)
    assert blob["links"] == [] and blob["edicoes"] == 0


def test_mover_de_campanha_migra_os_links_sem_esperar_novo_envio(tmp_path):
    """`create_campaign` grava `campanha` numa edição que já está em `sent`, e esse patch não
    tem nem `stage` nem `provenance`. Sem o gancho, os links seguem indexados na campanha
    antiga (barrando a pauta dela) e faltam na nova até um envio qualquer disparar rebuild."""
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-09-05", {"stage": "sent", "date": "2026-09-05",
                                     "provenance": _prov([_item(1, "https://a.com/x")])})
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/x"]

    sm.upsert_edition("2026-09-05", {"campanha": "fashion-weekly"})  # sem stage, sem provenance

    # Os blobs são lidos DIRETO do store, sem passar por get_publicados: ele reconstrói
    # quando o blob está ausente, e essa reconstrução mascararia o gancho faltando — a
    # campanha nova nasceria certa na primeira leitura e o defeito só apareceria na antiga.
    antigo = json.loads(sm.store.read(f"{PUBLICADOS_PREFIX}{CAMPANHA_PADRAO}.json"))
    assert antigo["links"] == []  # não segue barrando a pauta da campanha que ela deixou
    novo = json.loads(sm.store.read(f"{PUBLICADOS_PREFIX}fashion-weekly.json"))
    assert [l["link"] for l in novo["links"]] == ["https://a.com/x"]


def test_campanha_sem_envio_materializa_o_vazio_e_para_de_refazer(tmp_path):
    """Blob AUSENTE é o gatilho da reconstrução, e campanha sem edição enviada não ganha blob
    no rebuild: sem materializar o vazio, toda chamada refaz o índice inteiro, para sempre.
    Como o workdir é montado em todo estágio, a campanha nova pagaria isso a cada estágio."""
    store = _StoreContador(tmp_path)
    sm = StateManager(store)
    for i in range(5):
        ed = f"2026-09-0{i + 1}"
        _grava(sm, ed, stage="sent", date=ed, provenance=_prov([_item(i, f"https://a.com/{i}")]))

    leituras, listagens = [], []
    for _ in range(3):
        store.zera()
        blob = sm.get_publicados("nova-sem-envio")
        assert blob["links"] == [] and blob["edicoes"] == 0
        leituras.append(store.leituras)
        listagens.append(store.listagens)

    # 1ª: a leitura que falha + um get_state por edição + a releitura pós-rebuild.
    assert leituras[0] >= 7 and listagens[0] == 1
    # 2ª e 3ª: convergiu no blob materializado, sem listar edição nenhuma.
    assert leituras[1] == leituras[2] == 1
    assert listagens[1] == listagens[2] == 0


def test_listagem_de_edicoes_vazia_nao_esvazia_os_blobs(tmp_path, monkeypatch):
    """Bucket errado, prefixo errado ou permissão perdida em `editions/` devolvem lista vazia
    sem levantar. Esvaziar aí apaga a trava inteira em silêncio, e ela não volta sozinha:
    get_publicados só reconstrói no blob ausente, nunca no vazio."""
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01",
           provenance=_prov([_item(1, "https://a.com/um")]))
    sm._rebuild_publicados()
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/um"]

    monkeypatch.setattr(sm.store, "list_editions", lambda: [])
    sm._rebuild_publicados()
    monkeypatch.undo()
    assert _links(sm.get_publicados(CAMPANHA_PADRAO)) == ["https://a.com/um"]


def test_perda_real_das_enviadas_ainda_esvazia(tmp_path):
    """O vizinho que precisa continuar passando: com OUTRAS edições existindo, a campanha que
    de fato perdeu as suas enviadas é esvaziada. Sem ele, "nunca esvazia" imitaria a
    correção de cima."""
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-09-01", stage="sent", date="2026-09-01", campanha="daily-drops",
           provenance=_prov([_item(1, "https://a.com/drop")]))
    _grava(sm, "2026-09-02", stage="sent", date="2026-09-02", campanha="fashion-weekly",
           provenance=_prov([_item(2, "https://b.com/weekly")]))
    sm._rebuild_publicados()

    _grava(sm, "2026-09-02", stage="empty", campanha="fashion-weekly")  # perdeu a única
    sm._rebuild_publicados()
    assert _links(sm.get_publicados("fashion-weekly")) == []
    assert _links(sm.get_publicados("daily-drops")) == ["https://a.com/drop"]


def test_edicoes_conta_so_quem_contribuiu_link(tmp_path):
    """`edicoes` é o que a memória COBRE, não quantas foram consideradas. Procedência entrou
    em 02/09/2026: contando as consideradas, o `curadoria list` anunciaria uma cobertura que
    a memória não tem, e a janela seria calibrada pelo número errado."""
    sm = StateManager(LocalStore(tmp_path))
    _grava(sm, "2026-08-01", stage="sent", date="2026-08-01", subject="Legada sem provenance")
    _grava(sm, "2026-08-02", stage="sent", date="2026-08-02",
           provenance=_prov([_item(1), _item(2, "  ")]))  # provenance sem link aproveitável
    _grava(sm, "2026-09-03", stage="sent", date="2026-09-03",
           provenance=_prov([_item(3, "https://a.com/nova")]))
    sm._rebuild_publicados()

    blob = sm.get_publicados(CAMPANHA_PADRAO)
    assert len(blob["links"]) == 1
    assert blob["edicoes"] == 1  # 3 enviadas, 1 coberta
