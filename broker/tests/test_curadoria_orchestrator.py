# broker/tests/test_curadoria_orchestrator.py — regra de curadoria por campanha e a memória
# do que já foi publicado, do lado do orchestrator (estado, recorte e injeção no workdir).
import sys, pathlib, json, re
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


def _enviada(sm, edition, links, campanha=None, date=None):
    """Grava uma edição ENVIADA com procedência, que é de onde a memória se alimenta."""
    patch = {"stage": "sent", "date": date or edition,
             "provenance": {"publicados": len(links),
                            "itens": [{"campo": "manchete", "source_id": i, "source": s,
                                       "link": l, "titulo_fonte": t}
                                      for i, (s, l, t) in enumerate(links)]}}
    if campanha:
        patch["campanha"] = campanha
    sm.upsert_edition(edition, patch)


# ------------------------------------------------------------------ campanhas.json
def test_campanha_padrao_nasce_sozinha(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    doc = orchestrator.get_curadoria(sm)
    assert doc["default"] == "daily-drops"
    assert doc["campanhas"]["daily-drops"]["titulo_modo"] == "relatorio"
    assert doc["campanhas"]["daily-drops"]["janela_dias"] > 0


def test_criar_janela_e_titulo(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "criar", "campanha": "woow-beauty",
                                "nome": "WooW Beauty", "_email": "p@mk"})
    orchestrator.set_curadoria({"op": "janela", "campanha": "woow-beauty", "janela_dias": 30})
    r = orchestrator.set_curadoria({"op": "titulo", "campanha": "woow-beauty",
                                    "titulo_modo": "on"})
    beauty = r["campanhas"]["woow-beauty"]
    assert (beauty["janela_dias"], beauty["titulo_modo"], beauty["nome"]) == (30, "on", "WooW Beauty")
    # a padrão não foi tocada: regra de uma campanha não vaza para a outra
    assert r["campanhas"]["daily-drops"]["titulo_modo"] == "relatorio"


def test_copiar_de_leva_a_regra_e_nao_os_vetos(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "janela", "janela_dias": 21})
    orchestrator.set_curadoria({"op": "bloquear", "link": "https://x.com/a"})
    r = orchestrator.set_curadoria({"op": "criar", "campanha": "nova",
                                    "copiar_de": "daily-drops"})
    assert r["campanhas"]["nova"]["janela_dias"] == 21
    assert r["campanhas"]["nova"]["bloqueados"] == []


def test_criar_duplicada_e_slug_invalido(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_curadoria({"op": "criar", "campanha": "daily-drops"})
    for ruim in ("Daily Drops", "com espaço", "-comeca-com-hifen", "", "x" * 41):
        with pytest.raises(orchestrator.EntradaInvalida):
            orchestrator.set_curadoria({"op": "criar", "campanha": ruim})


def test_op_invalida_e_campanha_inexistente(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_curadoria({"op": "apagar-tudo"})
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_curadoria({"op": "janela", "campanha": "nao-existe", "janela_dias": 5})


def test_janela_fora_de_faixa_e_nao_numerica(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    for ruim in (-1, 3651, "muitos", None):
        with pytest.raises(orchestrator.EntradaInvalida):
            orchestrator.set_curadoria({"op": "janela", "janela_dias": ruim})


def test_padrao_nao_pode_ser_removida(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "criar", "campanha": "descartavel"})
    orchestrator.set_curadoria({"op": "remover", "campanha": "descartavel"})
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_curadoria({"op": "remover", "campanha": "daily-drops"})


def test_bloquear_e_liberar_sao_exclusivos(tmp_path, monkeypatch):
    """O mesmo link nas duas listas faria o resultado depender da ordem de aplicação, que é
    exatamente onde o operador se perde: 'bloqueei e continua vindo'."""
    _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "bloquear", "link": "https://x.com/a", "motivo": "chato"})
    r = orchestrator.set_curadoria({"op": "liberar", "link": "https://x.com/a"})
    d = r["campanhas"]["daily-drops"]
    assert [e["link"] for e in d["liberados"]] == ["https://x.com/a"]
    assert d["bloqueados"] == []
    r = orchestrator.set_curadoria({"op": "bloquear", "link": "https://x.com/a"})
    d = r["campanhas"]["daily-drops"]
    assert [e["link"] for e in d["bloqueados"]] == ["https://x.com/a"]
    assert d["liberados"] == []


def test_bloquear_url_invalida(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_curadoria({"op": "bloquear", "link": "glossy.co/sem-esquema"})


# ------------------------------------------------------------------ recorte da memória
def test_memoria_exclui_a_propria_edicao(tmp_path, monkeypatch):
    """Rerodar a pesquisa de uma edição já enviada não pode barrar os links dela mesma:
    a edição ficaria sem pauta nenhuma, e o motivo seria invisível."""
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/a", "A")])
    _enviada(sm, "2026-09-09", [("Glossy", "https://glossy.co/b", "B")])
    mem = orchestrator._memoria_publicados("2026-09-09", sm)
    assert [e["link"] for e in mem["links"]] == ["https://glossy.co/a"]


def test_memoria_exclui_edicoes_posteriores(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-01", [("Glossy", "https://glossy.co/velho", "V")])
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/novo", "N")])
    mem = orchestrator._memoria_publicados("2026-09-02", sm)
    assert [e["link"] for e in mem["links"]] == ["https://glossy.co/velho"]


def test_memoria_aplica_a_janela(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-08-01", [("Glossy", "https://glossy.co/antigo", "A")])
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/recente", "R")])
    orchestrator.set_curadoria({"op": "janela", "janela_dias": 7})
    mem = orchestrator._memoria_publicados("2026-09-09", sm)
    assert [e["link"] for e in mem["links"]] == ["https://glossy.co/recente"]
    assert mem["janela_dias"] == 7


def test_janela_zero_desliga_a_memoria_mas_nao_o_bloqueio(tmp_path, monkeypatch):
    """Bloqueio é veto explícito do operador, não memória: desligar a janela não pode
    ressuscitar o que alguém mandou nunca mais aparecer."""
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/a", "A")])
    orchestrator.set_curadoria({"op": "janela", "janela_dias": 0})
    orchestrator.set_curadoria({"op": "bloquear", "link": "https://glossy.co/vetado"})
    mem = orchestrator._memoria_publicados("2026-09-09", sm)
    assert [e["link"] for e in mem["links"]] == ["https://glossy.co/vetado"]


def test_liberados_viajam_no_doc_sem_serem_filtrados_aqui(tmp_path, monkeypatch):
    """A liberação NÃO é aplicada aqui, e isso é a correção, não uma folga.

    Filtrar por igualdade de string neste ponto fazia o operador que digita a URL como vê no
    site não liberar nada: a memória guarda o link publicado, com `www.`, barra final e
    `utm_source`. Quem guarda pode guardar cru; quem COMPARA tem que normalizar, e a régua
    (`canonical_url`) mora no research. Aqui só provamos que a lista chega inteira até lá."""
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://www.glossy.co/a/?utm_source=rss", "A"),
                                ("Modern Retail", "https://modernretail.co/b", "B")])
    orchestrator.set_curadoria({"op": "liberar", "link": "https://glossy.co/a"})
    mem = orchestrator._memoria_publicados("2026-09-09", sm)
    assert mem["liberados"] == ["https://glossy.co/a"]
    assert len(mem["links"]) == 2  # o filtro é do research, não daqui


def test_memoria_e_isolada_por_campanha(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "criar", "campanha": "woow-beauty"})
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/daily", "D")])
    _enviada(sm, "2026-09-08-b", [("Glossy", "https://glossy.co/beauty", "B")],
             campanha="woow-beauty", date="2026-09-08")
    sm.upsert_edition("2026-09-09-b", {"campanha": "woow-beauty", "date": "2026-09-09"})
    mem = orchestrator._memoria_publicados("2026-09-09-b", sm)
    assert [e["link"] for e in mem["links"]] == ["https://glossy.co/beauty"]


def test_memoria_nunca_levanta(tmp_path, monkeypatch):
    """`_workdir` roda em TODO estágio, `send` incluído. Índice podre não pode derrubar um
    envio de newsletter: fail-open é a política, e o erro vai para o log."""
    sm = _local_sm(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "get_publicados", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("gcs off")))
    mem = orchestrator._memoria_publicados("2026-09-09", sm)
    assert mem["links"] == []


def test_campanha_desconhecida_cai_na_padrao(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    sm.upsert_edition("2026-09-09", {"campanha": "sumiu", "date": "2026-09-09"})
    mem = orchestrator._memoria_publicados("2026-09-09", sm)
    assert mem["links"] == []  # não levantou


# ------------------------------------------------------------------ injeção no workdir
def test_popula_workdir_escreve_publicados_json(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/a", "A")])
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {"K": "v"})
    wd = orchestrator._workdir("2026-09-09")
    try:
        doc = json.loads((wd / "config" / "publicados.json").read_text(encoding="utf-8"))
        assert [e["link"] for e in doc["links"]] == ["https://glossy.co/a"]
        assert doc["campanha"] == "daily-drops"
    finally:
        import shutil; shutil.rmtree(wd, ignore_errors=True)


# ------------------------------------------------------------------ create_campaign
def test_create_campaign_com_campanha(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "criar", "campanha": "woow-beauty"})
    r = orchestrator.create_campaign({"edition": "2026-09-15", "campanha": "woow-beauty"})
    assert r["campanha"] == "woow-beauty"
    assert sm.get_state("2026-09-15")["campanha"] == "woow-beauty"


def test_create_campaign_recusa_campanha_inexistente(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.create_campaign({"edition": "2026-09-15", "campanha": "nao-existe"})


# ------------------------------------------------------------------ relatórios
def test_historico_ordena_do_mais_novo(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-07", [("Glossy", "https://glossy.co/velho", "V")])
    _enviada(sm, "2026-09-09", [("Glossy", "https://glossy.co/novo", "N")])
    r = orchestrator.get_publicados_report({}, sm)
    assert [e["link"] for e in r["links"]] == ["https://glossy.co/novo", "https://glossy.co/velho"]


def test_contribuicao_por_fonte_conta_o_que_saiu(tmp_path, monkeypatch):
    """O número que faltava ao `sources list`: fonte cadastrada e ativa que nunca publicou
    nada é indistinguível, hoje, de uma que publica todo dia."""
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/a", "A"),
                                ("Glossy", "https://glossy.co/b", "B"),
                                ("AR Insider", "https://arinsider.co/c", "C")])
    por_fonte = orchestrator.contribuicao_por_fonte(sm)["por_fonte"]
    assert por_fonte["Glossy"]["publicadas"] == 2
    assert por_fonte["AR Insider"]["publicadas"] == 1
    assert "Business of Fashion" not in por_fonte


def test_sources_report_marca_fonte_que_nunca_publicou(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/a", "A")])
    feeds = orchestrator.get_sources_report({}, sm)["feeds"]
    por_nome = {f["source"]: f for f in feeds}
    assert por_nome["Glossy"]["publicadas"] == 1
    assert por_nome["Business of Fashion"]["publicadas"] == 0


def test_curadoria_report_traz_o_tamanho_da_memoria(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _enviada(sm, "2026-09-08", [("Glossy", "https://glossy.co/a", "A")])
    r = orchestrator.get_curadoria_report({}, sm)
    assert r["campanhas"]["daily-drops"]["memoria_links"] == 1


# --------------------------------------------- integração research -> orchestrator
def test_barradas_por_fonte_atravessa_o_health_de_verdade(tmp_path, monkeypatch):
    """Contrato entre dois módulos, exercido pelo caminho de produção.

    O `build_health` do research é quem GRAVA `barrados_itens`, e o `contribuicao_por_fonte`
    é quem LÊ. Cada lado tinha teste próprio e os dois passavam com nomes de chave
    divergentes (`fonte` contra `source`): a coluna de barradas voltava 0 para sempre, sem
    erro nenhum, e uma fonte que só produz repetição ficava igual a uma que nunca repete.
    Montar o dict do health na mão neste teste reproduziria o mesmo ponto cego, então ele
    vem do produtor real."""
    import research
    sm = _local_sm(tmp_path, monkeypatch)
    barrados = [{"title": "Matéria repetida", "source": "Glossy",
                 "link": "https://glossy.co/a", "publicado_em": "2026-09-08",
                 "publicado_date": "2026-09-08"}]
    health = research.build_health(report=[], candidates=[], barrados=barrados)
    sm.upsert_edition("2026-09-09", {"stage": "researched", "date": "2026-09-09",
                                     "health": health})
    por_fonte = orchestrator.contribuicao_por_fonte(sm)["por_fonte"]
    assert por_fonte["Glossy"]["barradas"] == 1


# ------------------------------------------------ correções da revisão adversarial
def test_a_chave_da_edicao_manda_sobre_o_carimbo_do_pipeline():
    """O `edition_date` é calculado no relógio do container, que roda em UTC: edição gerada
    depois das 21h BRT carimba o dia seguinte. Como a memória é recortada por essa data, um
    dia a mais faz a janela pular a véspera e devolve inteira a repetição de 1 dia, que é a
    esmagadora maioria dos casos medidos. A chave é a identidade da edição e tem precedência."""
    assert orchestrator._resolve_edition_date("2026-09-09", "2026-09-10") == "2026-09-09"
    # e o vizinho: sem chave de data (legado wNN), o carimbo ainda serve
    assert orchestrator._resolve_edition_date("2026-w25", "2026-09-10") == "2026-09-10"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                        orchestrator._resolve_edition_date("2026-w25", None))


def test_dias_invalido_e_entrada_do_operador_nao_broker_quebrado(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    for ruim in ("abc", "7d", "-3", "0", "999999999", "1e9"):
        with pytest.raises(orchestrator.EntradaInvalida):
            orchestrator.get_publicados_report({"dias": ruim}, sm)
    orchestrator.get_publicados_report({"dias": "7"}, sm)  # o vizinho válido segue passando


def test_campanha_inexistente_no_relatorio_recusa(tmp_path, monkeypatch):
    """No relatório, silêncio é pior que erro: `?campanha=daily-drop` devolvia 200 com zero
    links, e o operador não distinguia typo de memória vazia. Na PESQUISA continua fail-open,
    porque lá recusar significa edição sem newsletter."""
    sm = _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.get_publicados_report({"campanha": "daily-drop"}, sm)
    sm.upsert_edition("2026-09-09", {"campanha": "sumiu", "date": "2026-09-09"})
    assert orchestrator._memoria_publicados("2026-09-09", sm)["links"] == []


def test_slug_nao_texto_e_400_e_nao_502(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    for ruim in (7, ["a"], {"a": 1}, True):
        with pytest.raises(orchestrator.EntradaInvalida):
            orchestrator.set_curadoria({"op": "criar", "campanha": ruim})


def test_gravacao_perdida_por_concorrencia_nao_devolve_confirmacao(tmp_path, monkeypatch):
    """`campanhas.json` é read-modify-write sem trava. A corrida continua existindo; o que
    não pode existir é o operador receber 200 com o próprio veto listado quando outra escrita
    concorrente o sobrescreveu."""
    sm = _local_sm(tmp_path, monkeypatch)
    original = sm.store.write

    def engole_a_escrita(key, data):
        if key == orchestrator.CAMPANHAS_KEY:
            return  # simula a escrita do concorrente vencendo a nossa
        return original(key, data)

    monkeypatch.setattr(sm.store, "write", engole_a_escrita)
    r = orchestrator.set_curadoria({"op": "bloquear", "link": "https://glossy.co/a"})
    assert "aviso_gravacao" in r and "não ficou" in r["aviso_gravacao"]


def test_gravacao_bem_sucedida_nao_gera_aviso(tmp_path, monkeypatch):
    """O vizinho: sem ele, um aviso emitido sempre passaria pelo teste de cima."""
    _local_sm(tmp_path, monkeypatch)
    r = orchestrator.set_curadoria({"op": "bloquear", "link": "https://glossy.co/a"})
    assert "aviso_gravacao" not in r


def test_titulo_modo_viaja_no_doc(tmp_path, monkeypatch):
    """O modo é do operador e quem aplica é o research: se ele não chegar no doc, o comando
    vira um botão que não liga em nada."""
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_curadoria({"op": "titulo", "titulo_modo": "on"})
    assert orchestrator._memoria_publicados("2026-09-09", sm)["titulo_modo"] == "on"
