"""A edição ganha identidade própria: `<slug>--<data>` fora da padrão, `<data>` nua nela.

Gate das outras partes da v1.8.0. Sem id próprio, `create-campaign --edition 2026-09-15
--campanha woow-beauty` sequestra a edição do Daily Drops daquele dia — mesmo `type`, guard
de type não dispara, e o state, o HTML publicado, a linha da fila e a memória de publicados
passam a ser da campanha errada, em silêncio.

Bloco 1: a régua do id (parse, composição, round-trip).
Bloco 2: a colisão, que é o defeito que motiva a parte inteira.
Bloco 3: retrocompat — o que já existe no bucket não pode mudar de significado.
"""
import datetime as _dt_mod
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import orchestrator  # noqa: E402
from state_manager import (  # noqa: E402
    CAMPANHA_PADRAO,
    LocalStore,
    StateManager,
    campanha_da_edicao,
    edition_id,
    split_edition_id,
)


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


def _campanha(sm, slug, nome=""):
    """Cria a campanha no campanhas.json pelo caminho de produção (set_curadoria)."""
    orchestrator.set_curadoria({"op": "criar", "campanha": slug, "nome": nome or slug,
                                "_email": "david@metakosmos.com.br"})


class _RelogioCongelado(datetime):
    """Mesmo padrão de test_edition_date_brt.py, apontado para o orchestrator.

    O orchestrator faz `from datetime import datetime`, então o nome patcheável é
    `orchestrator.datetime`. Sem congelar, o teste de `_resolve_edition_date` passaria por
    coincidência no dia em que a data do id fosse a de hoje.
    """
    _instante_utc = None

    @classmethod
    def now(cls, tz=None):
        instante = cls._instante_utc
        return instante.astimezone(tz) if tz else instante.replace(tzinfo=None)


def _congela(monkeypatch, instante_utc):
    _RelogioCongelado._instante_utc = instante_utc
    monkeypatch.setattr(orchestrator, "datetime", _RelogioCongelado)


# ------------------------------------------------------------------ bloco 1: a régua do id

def test_id_nu_e_da_campanha_padrao():
    assert split_edition_id("2026-09-15") == (None, "2026-09-15")


def test_id_composto_devolve_os_dois():
    assert split_edition_id("woow-beauty--2026-09-15") == ("woow-beauty", "2026-09-15")


@pytest.mark.parametrize("ed", [
    "2026-w37",            # semanal legado, ainda no bucket
    "webinar-2026-08-21",  # edição de webinar, legada
    "teste-remetente-1",
    "camp-x",
    "",
    None,
    "2026-13-99--x",
    "woow beauty--2026-09-15",   # espaço não é slug
    "WOOW--2026-09-15",          # maiúscula não é slug
    "woow---2026-09-15",         # slug terminando em hífen (o aperto do _SLUG_RE)
    "--2026-09-15",              # sem slug antes do separador
    "woow-beauty--2026-9-15",    # data sem zero à esquerda
    "woow-beauty/2026-09-15",
    123,
])
def test_o_que_nao_e_id_de_edicao_devolve_dois_nones_sem_levantar(ed):
    assert split_edition_id(ed) == (None, None)


def test_slug_longo_demais_nao_e_prefixo_valido():
    # _SLUG_RE tem teto de 40; 41 caracteres não pode virar campanha de um id composto,
    # senão o parse aceita um prefixo que o cadastro de campanha recusaria.
    assert split_edition_id("a" * 41 + "--2026-09-15") == (None, None)
    assert split_edition_id("a" * 40 + "--2026-09-15") == ("a" * 40, "2026-09-15")


def test_slug_com_hifen_duplo_no_meio_ainda_ancora_na_data():
    # `woow--beauty` é slug válido (hífen duplo interno é permitido), então o parse precisa
    # ancorar pela DATA e não por split("--"), que devolveria ['woow','beauty','2026-09-15'].
    assert split_edition_id("woow--beauty--2026-09-15") == ("woow--beauty", "2026-09-15")


def test_edition_id_da_padrao_e_o_id_nu():
    assert edition_id(CAMPANHA_PADRAO, "2026-09-15") == "2026-09-15"
    assert edition_id(None, "2026-09-15") == "2026-09-15"


def test_edition_id_de_campanha_nao_padrao_e_composto():
    assert edition_id("woow-beauty", "2026-09-15") == "woow-beauty--2026-09-15"


def test_round_trip_para_200_slugs_validos():
    data = "2026-09-15"
    for i in range(200):
        slug = f"c{i}-camp" if i % 2 else f"camp-{i}"
        assert split_edition_id(edition_id(slug, data)) == (slug, data)
    # e o vizinho: a padrão continua sem prefixo depois do round-trip
    assert split_edition_id(edition_id(CAMPANHA_PADRAO, data)) == (None, data)


def test_slug_com_forma_de_data_e_recusado_no_cadastro(tmp_path, monkeypatch):
    """Slug e data não podem ser o mesmo namespace, senão `2026-09-15--2026-09-15` é ambíguo.

    `2026-09-15` casa com o _SLUG_RE de hoje. A separação tem que ser feita onde a campanha
    nasce, não no parse do id.
    """
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        _campanha(None, "2026-09-15")
    assert "2026-09-15" in str(e.value)
    # vizinho que continua passando: slug que só COMEÇA com dígito é legítimo
    _campanha(None, "214-drops")
    assert "214-drops" in orchestrator.get_curadoria()["campanhas"]


def test_slug_com_espaco_em_branco_nas_bordas_nao_vira_campanha(tmp_path, monkeypatch):
    """`_SLUG_RE.match("woow-beauty\\n")` é True; fullmatch é False.

    Sem o aperto, a string com newline vira nome de blob (`publicados/woow-beauty\\n.json`,
    e na Parte 2 `schedules/woow-beauty\\n.json`): um índice fantasma que nada relaciona ao
    verdadeiro.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    assert campanha_da_edicao({"campanha": "woow-beauty\n"}) == CAMPANHA_PADRAO
    # vizinho: o slug limpo continua sendo aceito
    assert campanha_da_edicao({"campanha": "woow-beauty"}) == "woow-beauty"
    assert sm is not None


# ---------------------------------------------------- bloco 2: a colisão que motiva a parte

def _cria(edition=None, campanha=None, **extra):
    payload = {"_email": "david@metakosmos.com.br", **extra}
    if edition is not None:
        payload["edition"] = edition
    if campanha is not None:
        payload["campanha"] = campanha
    return orchestrator.create_campaign(payload)


def test_duas_campanhas_no_mesmo_dia_nao_se_sobrescrevem(tmp_path, monkeypatch):
    """O defeito que a Parte 1 existe para fechar, medido nos quatro lugares.

    Antes: mesmo `type`, guard de type não dispara, o upsert grava `campanha: woow-beauty`
    no state do Daily Drops e a memória do Daily Drops perde aquele dia no rebuild.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha(sm, "woow-beauty", "WooW! Beauty")

    _cria(edition="2026-09-15")
    sm.upsert_edition("2026-09-15", {"stage": "ready", "subject": "Daily do dia",
                                     "date": "2026-09-15"})
    antes = sm.store.read("editions/2026-09-15.state.json")

    r = _cria(edition="2026-09-15", campanha="woow-beauty")

    # 1) dois states, e o do Daily Drops byte a byte igual ao que estava
    assert r["edition"] == "woow-beauty--2026-09-15"
    assert sm.store.read("editions/2026-09-15.state.json") == antes
    assert sm.get_state("woow-beauty--2026-09-15")["campanha"] == "woow-beauty"
    assert campanha_da_edicao(sm.get_state("2026-09-15")) == CAMPANHA_PADRAO

    # 2) duas linhas na fila, cada uma com a sua campanha e a MESMA data
    fila = {l["edition"]: l for l in sm.get_queue()["editions"]}
    assert set(fila) == {"2026-09-15", "woow-beauty--2026-09-15"}
    assert fila["2026-09-15"]["campanha"] == CAMPANHA_PADRAO
    assert fila["woow-beauty--2026-09-15"]["campanha"] == "woow-beauty"
    assert fila["woow-beauty--2026-09-15"]["date"] == "2026-09-15"

    # 3) duas memórias: enviar uma não gasta a pauta da outra
    sm.upsert_edition("2026-09-15", {"stage": "sent", "date": "2026-09-15", "provenance": {
        "itens": [{"link": "https://ex.com/a", "campo": "manchete", "source": "S"}]}})
    sm.upsert_edition("woow-beauty--2026-09-15", {
        "stage": "sent", "date": "2026-09-15", "campanha": "woow-beauty",
        "provenance": {"itens": [{"link": "https://ex.com/b", "campo": "manchete",
                                  "source": "S"}]}})
    links_padrao = [e["link"] for e in sm.get_publicados(CAMPANHA_PADRAO)["links"]]
    links_beauty = [e["link"] for e in sm.get_publicados("woow-beauty")["links"]]
    assert links_padrao == ["https://ex.com/a"]
    assert links_beauty == ["https://ex.com/b"]


def test_publicacao_dos_dois_html_nao_colide(tmp_path, monkeypatch):
    """Terceiro lugar da colisão: `nl/<edição>.html`. O id já separa os arquivos."""
    _local_sm(tmp_path, monkeypatch)
    assert orchestrator._publish_key("2026-09-15") == "nl/2026-09-15.html"
    assert orchestrator._publish_key("woow-beauty--2026-09-15") == \
        "nl/woow-beauty--2026-09-15.html"


def test_create_campaign_compoe_o_id_quando_recebe_so_a_data(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha(sm, "woow-beauty")
    r = _cria(edition="2026-09-15", campanha="woow-beauty")
    assert r["edition"] == "woow-beauty--2026-09-15"
    assert r["campanha"] == "woow-beauty"
    assert sm.store.read("editions/2026-09-15.state.json") is None


def test_create_campaign_aceita_o_id_composto_pronto(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha(sm, "woow-beauty")
    r = _cria(edition="woow-beauty--2026-09-15", campanha="woow-beauty")
    assert r["edition"] == "woow-beauty--2026-09-15"


def test_create_campaign_recusa_prefixo_que_nao_bate_com_a_campanha(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha(sm, "woow-beauty")
    _campanha(sm, "woow-food")
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        _cria(edition="woow-food--2026-09-15", campanha="woow-beauty")
    assert "woow-food" in str(e.value) and "woow-beauty" in str(e.value)
    # vizinho que continua passando: o prefixo certo entra
    assert _cria(edition="woow-food--2026-09-15",
                 campanha="woow-food")["edition"] == "woow-food--2026-09-15"


def test_create_campaign_recusa_campanha_inexistente(tmp_path, monkeypatch):
    """Fail-loud, não fail-open: campanha que não existe é erro de digitação."""
    sm = _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        _cria(edition="2026-09-15", campanha="nao-existe")
    assert "nao-existe" in str(e.value)
    # vizinho: a padrão, que sempre existe, continua entrando sem cadastro nenhum
    assert _cria(edition="2026-09-15")["campanha"] == CAMPANHA_PADRAO
    assert sm.get_state("2026-09-15")["stage"] == "empty"


def test_campanha_vazia_nao_e_o_mesmo_que_campanha_omitida(tmp_path, monkeypatch):
    """`if campanha:` deixava `{"campanha": ""}` cair na padrão respondendo 200.

    Mesmo buraco de valor falsy que o set_curadoria fechou com `is not None`.
    """
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        _cria(edition="2026-09-15", campanha="")
    # vizinho: omitir de verdade continua caindo na padrão
    assert _cria(edition="2026-09-15")["campanha"] == CAMPANHA_PADRAO


@pytest.mark.parametrize("ruim", [
    "a/b", "../queue", "woow beauty", "e*", "e?", "e[1]", ".", "..", "", "  ",
])
def test_id_de_edicao_perigoso_e_recusado(tmp_path, monkeypatch, ruim):
    """O id passa a ser digitado pelo operador, e já é nome de blob, segmento de path
    (`nl/hist/<id>/`) e PADRÃO DE GLOB em _persist_content."""
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        _cria(edition=ruim)


@pytest.mark.parametrize("bom", [
    "2026-09-15", "woow-beauty--2026-09-15", "2026-w37", "webinar-2026-08-21",
    "teste-remetente-1", "camp-x", "e", "2026-09-08-b",
])
def test_id_de_edicao_legitimo_continua_passando(tmp_path, monkeypatch, bom):
    """O vizinho da régua: ela recusa o perigoso sem recusar o que já existe no bucket."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha(sm, "woow-beauty")  # o id composto adota o prefixo, e a campanha tem de existir
    assert _cria(edition=bom)["edition"] == bom


# ------------------------------------------------- a data da edição sai do id, não do relógio

def test_resolve_edition_date_le_a_data_do_id_composto(monkeypatch):
    """Sem isto, `date` sai errado em TODA edição de campanha não-padrão.

    O fullmatch de hoje não casa com id composto e a função desce para o carimbo (relógio
    UTC do container) e depois para hoje-BRT. A janela da trava recorta por essa data, então
    o `date` errado corrompe a memória de publicados junto.
    """
    _congela(monkeypatch, datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert orchestrator._resolve_edition_date("woow-beauty--2026-09-15") == "2026-09-15"


def test_resolve_edition_date_ignora_o_carimbo_do_pipeline_no_id_composto(monkeypatch):
    """A CHAVE tem precedência sobre o carimbo, igual ao id nu. O carimbo vem do relógio do
    container, em UTC: edição gerada depois das 21h BRT carimba o dia seguinte."""
    _congela(monkeypatch, datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert orchestrator._resolve_edition_date("woow-beauty--2026-09-15",
                                              "2026-09-11") == "2026-09-15"


def test_resolve_edition_date_do_id_nu_nao_muda(monkeypatch):
    """Vizinho obrigatório: o caminho antigo, intacto."""
    _congela(monkeypatch, datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert orchestrator._resolve_edition_date("2026-09-15") == "2026-09-15"


def test_resolve_edition_date_de_chave_legada_ainda_cai_no_carimbo(monkeypatch):
    """`2026-w37` nunca teve data na chave: continua usando o carimbo, e depois hoje-BRT."""
    _congela(monkeypatch, datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    assert orchestrator._resolve_edition_date("2026-w37", "2026-09-11") == "2026-09-11"
    assert orchestrator._resolve_edition_date("2026-w37") == "2026-09-10"


# ------------------------------------------------------------------ bloco 3: retrocompat

def test_id_nu_continua_sendo_daily_drops_no_rebuild(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    sm.upsert_edition("2026-09-08", {"stage": "sent", "date": "2026-09-08", "provenance": {
        "itens": [{"link": "https://ex.com/legado", "campo": "manchete", "source": "S"}]}})
    docs = sm.rebuild_publicados()
    assert list(docs) == [CAMPANHA_PADRAO]
    assert [e["link"] for e in docs[CAMPANHA_PADRAO]["links"]] == ["https://ex.com/legado"]


def test_id_nu_continua_sendo_daily_drops_na_fila_e_no_recorte(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    sm.upsert_edition("2026-09-08", {"stage": "ready", "date": "2026-09-08"})
    linha = sm.get_queue()["editions"][0]
    assert linha["edition"] == "2026-09-08"
    assert linha["campanha"] == CAMPANHA_PADRAO
    assert linha["date"] == "2026-09-08"


def test_chave_legada_2026_wNN_continua_funcionando(tmp_path, monkeypatch):
    """Ela não é data e não é id composto: tem que atravessar tudo sem levantar."""
    sm = _local_sm(tmp_path, monkeypatch)
    sm.upsert_edition("2026-w37", {"stage": "sent", "date": "2026-09-08", "provenance": {
        "itens": [{"link": "https://ex.com/semanal", "campo": "manchete", "source": "S"}]}})
    assert split_edition_id("2026-w37") == (None, None)
    assert campanha_da_edicao(sm.get_state("2026-w37")) == CAMPANHA_PADRAO
    docs = sm.rebuild_publicados()
    assert [e["link"] for e in docs[CAMPANHA_PADRAO]["links"]] == ["https://ex.com/semanal"]


def test_fila_preenche_date_de_id_composto_sem_state_date(tmp_path, monkeypatch):
    """`queue.json` passa a ter `date` sempre preenchido: é o que um consumidor precisa
    para não parsear o id."""
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("editions/woow-beauty--2026-09-15.state.json", json.dumps(
        {"edition": "woow-beauty--2026-09-15", "stage": "researched",
         "campanha": "woow-beauty"}))
    sm.upsert_edition("2026-09-08", {"stage": "empty"})  # dispara o rebuild da fila
    fila = {l["edition"]: l for l in sm.get_queue()["editions"]}
    assert fila["woow-beauty--2026-09-15"]["date"] == "2026-09-15"


def test_memoria_nao_confunde_id_composto_com_data(tmp_path, monkeypatch):
    """`"date": st.get("date") or ed` punha o id inteiro no campo data.

    O recorte de janela compara string e descarta o que não casa `\\d{4}-\\d{2}-\\d{2}`:
    a entrada sumiria da memória sem erro nenhum, e a matéria voltaria à pauta.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("editions/woow-beauty--2026-09-15.state.json", json.dumps({
        "edition": "woow-beauty--2026-09-15", "stage": "sent", "campanha": "woow-beauty",
        "provenance": {"itens": [{"link": "https://ex.com/x", "campo": "manchete",
                                  "source": "S"}]}}))
    docs = sm.rebuild_publicados()
    assert docs["woow-beauty"]["links"][0]["date"] == "2026-09-15"


def test_metricas_nao_perdem_o_daily_drops_quando_existe_id_composto(tmp_path, monkeypatch):
    """`[-4:]` sobre a fila ordenada por ID: 'w' > '2', então a padrão é despejada.

    Sem exceção e sem log — a rota devolve 200 com as edições erradas.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    for d in ("2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15"):
        sm.upsert_edition(d, {"stage": "sent", "date": d})
    for d in ("2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15"):
        sm.upsert_edition(f"woow-beauty--{d}", {"stage": "sent", "date": d,
                                                "campanha": "woow-beauty"})
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {})
    edicoes = [e["edition"] for e in orchestrator._refresh_metrics(sm)["editions"]]
    assert any(split_edition_id(e) == (None, e) for e in edicoes), \
        f"o Daily Drops sumiu do /metrics: {edicoes}"
    assert any(e.startswith("woow-beauty--") for e in edicoes)


# ---------------------- achados da revisão adversarial: coerência entre id e campo

def test_id_composto_sem_campanha_adota_o_prefixo(tmp_path, monkeypatch):
    """`woow status` mostra o id composto, e o operador o copia. Sem isto, colar
    `woow-beauty--2026-09-15` sem `--campanha` gravava um state SEM o campo `campanha`:
    `campanha_da_edicao` devolvia daily-drops, os links da Beauty entravam na memória da
    diária, e o `curadoria status` da Beauty ficava vazio. O id dizia uma coisa e a
    autoridade dizia outra, com 200 e sem log."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha(sm, "woow-beauty")
    r = _cria(edition="woow-beauty--2026-09-15")
    assert r["campanha"] == "woow-beauty"
    assert sm.get_state("woow-beauty--2026-09-15")["campanha"] == "woow-beauty"


def test_id_composto_de_campanha_inexistente_e_recusado(tmp_path, monkeypatch):
    """Fail-loud: o prefixo virou declaração de campanha, então vale a mesma régua."""
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        _cria(edition="nao-existe--2026-09-15")
    assert "nao-existe" in str(e.value)


def test_id_nu_sem_campanha_continua_na_padrao(tmp_path, monkeypatch):
    """O vizinho obrigatório: id nu não declara campanha nenhuma e não pode passar a
    declarar. Se este quebrar, o adotar-prefixo virou adotar-qualquer-coisa."""
    sm = _local_sm(tmp_path, monkeypatch)
    assert _cria(edition="2026-09-15")["campanha"] == CAMPANHA_PADRAO
    assert "campanha" not in sm.get_state("2026-09-15")


def test_chave_legada_sem_campanha_continua_na_padrao(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    assert _cria(edition="webinar-2026-08-21")["campanha"] == CAMPANHA_PADRAO


def test_historico_nao_deixa_chave_legada_furar_o_recorte_de_dias(tmp_path, monkeypatch):
    """`--dias 7` mantinha link de junho para sempre.

    O docstring de `_data` promete "a mesma régua que `_memoria_publicados` aplica", e a
    régua de lá DESCARTA data que não é data. Aqui a comparação é de string e
    `"2026-w25" >= "2026-09-03"` é verdadeiro ('w' > '0'), então a entrada legada entrava em
    qualquer janela e ficava no topo da ordenação.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("publicados/daily-drops.json", json.dumps({
        "campanha": CAMPANHA_PADRAO, "updated_at": "", "edicoes": 2, "links": [
            {"link": "https://ex.com/legado", "edition": "2026-w25", "date": "2026-w25"},
            {"link": "https://ex.com/ontem", "edition": "2026-09-09", "date": "2026-09-09"},
        ]}))
    _congela(monkeypatch, datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc))
    r = orchestrator.get_publicados_report({"dias": 7})
    assert [e["link"] for e in r["links"]] == ["https://ex.com/ontem"]

    # vizinho obrigatório: sem recorte, a entrada legada continua aparecendo (não some do
    # histórico), só que no fim, não no topo
    todos = orchestrator.get_publicados_report({})
    assert [e["link"] for e in todos["links"]] == ["https://ex.com/ontem",
                                                   "https://ex.com/legado"]


def test_metricas_mantem_a_janela_de_quatro_com_uma_campanha_so(tmp_path, monkeypatch):
    """A realidade de hoje é uma campanha só, e a janela não pode encolher sem aviso."""
    sm = _local_sm(tmp_path, monkeypatch)
    for d in ("2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"):
        sm.upsert_edition(d, {"stage": "sent", "date": d})
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {})
    edicoes = [e["edition"] for e in orchestrator._refresh_metrics(sm)["editions"]]
    assert len(edicoes) == 4
    assert edicoes[-1] == "2026-09-14"


def test_metricas_nao_matam_de_fome_a_campanha_menos_frequente(tmp_path, monkeypatch):
    """Com 5+ campanhas, o teto global cortava por recência e eliminava TODAS as edições da
    que publica menos. É o mesmo defeito que este recorte existe para consertar."""
    sm = _local_sm(tmp_path, monkeypatch)
    for i in range(5):
        slug = f"camp-{i}"
        # camp-4 é semanal: publica uma vez, e há muito tempo
        datas = ["2026-09-01"] if i == 4 else ["2026-09-12", "2026-09-13", "2026-09-14",
                                               "2026-09-15"]
        for d in datas:
            sm.upsert_edition(f"{slug}--{d}", {"stage": "sent", "date": d, "campanha": slug})
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {})
    edicoes = [e["edition"] for e in orchestrator._refresh_metrics(sm)["editions"]]
    campanhas = {e.split("--")[0] for e in edicoes}
    assert "camp-4" in campanhas, f"a semanal sumiu do /metrics: {edicoes}"
    assert len(edicoes) <= orchestrator.METRICS_TETO_GLOBAL


@pytest.mark.parametrize("rota,chamada", [
    ("run", lambda ed: orchestrator.run_stage(ed, "research", {})),
    ("add-pauta", lambda ed: orchestrator.add_pauta(ed, {"title": "t"})),
    ("set-html", lambda ed: orchestrator.set_html({"edition": ed, "html": "<p>x</p>"})),
    ("admin/reset", lambda ed: orchestrator.reset_edition(ed)),
])
def test_a_regua_do_id_vale_em_toda_porta_que_escreve(tmp_path, monkeypatch, rota, chamada):
    """A guarda nascera ligada só no `create_campaign`, e outras quatro rotas aceitavam
    `edition` cru do corpo. Os três modos de falha que ela documenta continuavam
    alcançáveis: `a/b` grava fora, `e*` casa o glob errado, espaço em branco some da tela."""
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        chamada("../queue")


def test_a_regua_nao_recusa_id_legitimo_em_nenhuma_porta(tmp_path, monkeypatch):
    """O vizinho: a guarda tem de deixar passar tudo que já existe. `reset` é a porta mais
    barata de exercitar sem tocar no pipeline."""
    _local_sm(tmp_path, monkeypatch)
    for bom in ("2026-09-15", "woow-beauty--2026-09-15", "2026-w37", "webinar-2026-08-21"):
        assert orchestrator.reset_edition(bom)["reset"] == bom
