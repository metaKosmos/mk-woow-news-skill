"""Os cinco eixos de config que a v1.8.0 põe na campanha: fontes, entrega, formato, perfil
editorial e o liga/desliga.

Todo teste de bloqueio aqui tem o vizinho que continua passando. Uma suíte onde "tudo
recusa" imita uma guarda que funciona: se `set_curadoria` levantasse em qualquer entrada, os
testes de recusa passariam todos e nenhum diria que a gravação legítima ainda grava.

A retrocompat tem bloco próprio no fim, porque ela é o que decide se o deploy pode sair sem
migrar nada: campanha gravada pela v1.7.0 não tem nenhum dos quatro campos novos, e o que a
ausência significa é decidido em UM lugar (os acessores `_campanha_ativa`, `_fontes_da_campanha`,
`_entrega_da_campanha`, `_formato_da_campanha`), não em cada chamador.
"""
import json
import pathlib
import sys
from datetime import datetime

import pytest
import yaml

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))

import orchestrator  # noqa: E402
from state_manager import CAMPANHA_PADRAO, LocalStore, StateManager  # noqa: E402


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


def _campanha(slug, **kw):
    return orchestrator.set_curadoria({"op": "criar", "campanha": slug,
                                       "_email": "david@metakosmos.com.br", **kw})


def _regra_gravada(sm, slug):
    return orchestrator.get_curadoria(sm)["campanhas"][slug]


# =========================================================== Parte 3: fontes por campanha
def test_selecao_de_fontes_recorta_a_pesquisa_da_campanha(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy", "Modern Retail"]})
    escolhidas = {f["source"] for f in orchestrator._effective_feeds(sm, "woow-beauty")}
    assert escolhidas == {"Glossy", "Modern Retail"}


def test_campanha_sem_selecao_continua_com_todas(tmp_path, monkeypatch):
    """O vizinho do teste acima: quem não escolheu nada pesquisa em tudo, como antes."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    todas = {f["source"] for f in orchestrator._effective_feeds(sm)}
    assert {f["source"] for f in orchestrator._effective_feeds(sm, "woow-beauty")} == todas
    assert len(todas) > 2


def test_fonte_de_uma_campanha_nao_entra_no_feeds_da_outra(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("a")
    _campanha("b")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "a", "modo": "lista",
                                "nomes": ["Glossy"]})
    orchestrator.set_curadoria({"op": "fontes", "campanha": "b", "modo": "lista",
                                "nomes": ["VentureBeat"]})
    assert {f["source"] for f in orchestrator._effective_feeds(sm, "a")} == {"Glossy"}
    assert {f["source"] for f in orchestrator._effective_feeds(sm, "b")} == {"VentureBeat"}


def test_selecao_que_nao_casa_com_o_cadastro_e_recusada_nomeando_o_nome(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty",
                                    "modo": "lista", "nomes": ["Glossy", "Glosy"]})
    assert "Glosy" in str(e.value)
    assert "Glossy" not in str(e.value).replace("Glosy", "")


def test_selecao_que_resolve_para_zero_fonte_e_recusada(tmp_path, monkeypatch):
    """Decisão 12: seleção vazia NÃO cai para 'todas'. Recusa na gravação, que é onde o
    operador ainda está olhando; ao vivo, o efeito seria pauta curta sem ninguém saber."""
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty",
                                    "modo": "lista", "nomes": []})
    assert "nenhuma fonte" in str(e.value).lower() or "vazia" in str(e.value).lower()


def test_voltar_para_todas_e_sempre_permitido(tmp_path, monkeypatch):
    """O vizinho da recusa acima: `modo: todas` sem nomes não é seleção vazia, é o default."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy"]})
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "todas"})
    assert orchestrator._fontes_da_campanha(_regra_gravada(sm, "woow-beauty"))["modo"] == "todas"
    assert len(orchestrator._effective_feeds(sm, "woow-beauty")) > 1


def test_fonte_desativada_no_cadastro_sai_da_selecao_sem_derrubar(tmp_path, monkeypatch):
    """Fonte que sai do cadastro depois some da seleção. Não levanta: a seleção é do
    operador, o cadastro é global, e um desativar global não pode quebrar outra campanha."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy", "Modern Retail"]})
    orchestrator.set_sources({"op": "disable", "source": "Glossy", "_email": "d@mk"})
    assert {f["source"] for f in orchestrator._effective_feeds(sm, "woow-beauty")} == {"Modern Retail"}


# ========================================================= Parte 4: entrega por campanha
_NIVEIS = ("edicao", "campanha", "settings", "config")


def _grava_settings(sm, **campos):
    sm.store.write("settings.json", json.dumps(campos, ensure_ascii=False))


def test_precedencia_de_entrega_nivel_a_nivel(tmp_path, monkeypatch):
    """Os quatro níveis da decisão 6, com o de cima removido a cada vez."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                               "list_key": "LK-CAMPANHA", "from_email": "beauty@metakosmos.com.br"})
    _grava_settings(sm, active_list_key="LK-SETTINGS", active_from_email="global@metakosmos.com.br")

    st = {"list_key": "LK-EDICAO"}
    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", st, "woow-beauty")
    assert (r["list_key"], r["origem"]["list_key"]) == ("LK-EDICAO", "edicao")

    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", {}, "woow-beauty")
    assert (r["list_key"], r["origem"]["list_key"]) == ("LK-CAMPANHA", "campanha")
    assert (r["from_email"], r["origem"]["from_email"]) == ("beauty@metakosmos.com.br", "campanha")

    orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                                "list_key": "", "from_email": ""})
    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", {}, "woow-beauty")
    assert (r["list_key"], r["origem"]["list_key"]) == ("LK-SETTINGS", "settings")

    sm.store.write("settings.json", "{}")
    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", {}, "woow-beauty")
    assert r["origem"]["list_key"] == "config"
    assert r["list_key"] == orchestrator._delivery()["list_key"]


def test_lista_de_uma_campanha_nao_aparece_nos_args_da_outra(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("a")
    _campanha("b")
    orchestrator.set_curadoria({"op": "entrega", "campanha": "a", "list_key": "LK-A"})
    orchestrator.set_curadoria({"op": "entrega", "campanha": "b", "list_key": "LK-B"})
    args_a = orchestrator._build_send_args(
        "a--2026-09-15", {}, orchestrator._delivery(), {"from_email": "x@y", "from_name": "X"},
        {}, orchestrator.resolve_entrega(sm, "a--2026-09-15", {}, "a"))
    assert "LK-A" in args_a and "LK-B" not in args_a


def test_campanha_nao_default_ganha_nome_proprio_no_zma(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty", nome="WooW! Beauty")
    entrega = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15",
                                           {"date": "2026-09-15"}, "woow-beauty")
    args = orchestrator._build_send_args(
        "woow-beauty--2026-09-15", {"date": "2026-09-15"}, orchestrator._delivery(),
        {"from_email": "x@y", "from_name": "X"}, {}, entrega)
    assert "--campaign-name" in args
    assert args[args.index("--campaign-name") + 1] == "WooW! Beauty 2026-09-15"


def test_daily_drops_nao_manda_campaign_name(tmp_path, monkeypatch):
    """O vizinho do teste acima. Quem monta o nome do Daily Drops é o send_zma
    (`mK Newsletter <stem>`); mandar o arg aqui renomearia o histórico do painel do ZMA."""
    sm = _local_sm(tmp_path, monkeypatch)
    entrega = orchestrator.resolve_entrega(sm, "2026-09-15", {"date": "2026-09-15"},
                                           CAMPANHA_PADRAO)
    args = orchestrator._build_send_args(
        "2026-09-15", {}, orchestrator._delivery(), {"from_email": "x@y", "from_name": "X"},
        {}, entrega)
    assert "--campaign-name" not in args


def test_remetente_invalido_e_recusado(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                                    "from_email": "nao-e-email"})


# ========================================================= Parte 5: formato e perfil
def test_perfil_so_toca_as_chaves_da_lista_fechada(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    base = yaml.safe_load((orchestrator.CONFIG / "newsletter.yaml").read_text(encoding="utf-8"))
    doc = orchestrator._merge_perfil(base, {"research.days_lookback": 7,
                                            "gemini.model_write": "modelo-pirata",
                                            "delivery.list_key": "LK-PIRATA"})
    assert doc["research"]["days_lookback"] == 7
    assert doc["gemini"]["model_write"] == base["gemini"]["model_write"]
    assert doc["delivery"] == base["delivery"]


def test_segredo_custo_e_entrega_sao_identicos_para_qualquer_perfil(tmp_path, monkeypatch):
    """Asserção direta da decisão do plano: nenhum perfil, por mais criativo, muda
    `gemini.api_key_env`, `gemini.endpoint`, os modelos ou o bloco `delivery`."""
    _local_sm(tmp_path, monkeypatch)
    base = yaml.safe_load((orchestrator.CONFIG / "newsletter.yaml").read_text(encoding="utf-8"))
    perfis = [{}, {"formato": "daily-drops"}, {"research.days_lookback": 30},
              {"gemini.api_key_env": "CHAVE_DO_ATACANTE"},
              {"gemini.endpoint": "https://exfil.example/v1"},
              {"delivery": {"from_email": "atacante@example.com"}},
              {"gemini": {"api_key_env": "X"}}]
    for perfil in perfis:
        doc = orchestrator._merge_perfil(base, perfil)
        assert doc["gemini"]["api_key_env"] == base["gemini"]["api_key_env"], perfil
        assert doc["gemini"]["endpoint"] == base["gemini"]["endpoint"], perfil
        assert doc["gemini"]["model_write"] == base["gemini"]["model_write"], perfil
        assert doc["delivery"] == base["delivery"], perfil


def test_formato_precisa_existir_no_formatos_yaml(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.set_curadoria({"op": "formato", "campanha": "woow-beauty",
                                    "formato": "nao-existe"})
    assert "nao-existe" in str(e.value)


def test_formato_que_existe_grava(tmp_path, monkeypatch):
    """O vizinho da recusa acima."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "formato", "campanha": "woow-beauty",
                                "formato": "daily-drops"})
    assert orchestrator._formato_da_campanha(_regra_gravada(sm, "woow-beauty")) == "daily-drops"


def test_formatos_yaml_tem_o_formato_de_hoje(tmp_path, monkeypatch):
    fmts = orchestrator._formatos()
    dd = fmts["daily-drops"]
    assert dd["prompt_write"] == "write.md"
    assert dd["template"] == "woow-daily-drops.html.j2"
    assert (BROKER / "config" / "prompts" / dd["prompt_write"]).exists()
    assert (BROKER / "templates" / dd["template"]).exists()


# ============================================================= liga/desliga da campanha
def test_desativar_tira_do_tick_e_ativar_devolve(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "desativar", "campanha": "woow-beauty"})
    assert orchestrator._campanha_ativa(_regra_gravada(sm, "woow-beauty")) is False
    orchestrator.set_curadoria({"op": "ativar", "campanha": "woow-beauty"})
    assert orchestrator._campanha_ativa(_regra_gravada(sm, "woow-beauty")) is True


def test_campanha_desativada_recusa_edicao_nova(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "desativar", "campanha": "woow-beauty"})
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.create_campaign({"edition": "2026-09-15", "campanha": "woow-beauty",
                                      "type": "news_auto"})
    assert "desativada" in str(e.value).lower()


def test_a_padrao_nao_se_desativa(tmp_path, monkeypatch):
    """`ativa: false` na padrão também bloquearia `create-campaign` do id nu, que é a
    operação manual do dia a dia. Quem pausa o envio é `schedule off`, e a mensagem diz isso."""
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.set_curadoria({"op": "desativar", "campanha": CAMPANHA_PADRAO})
    assert "schedule off" in str(e.value)


def test_remover_recusa_campanha_com_edicao_gravada(tmp_path, monkeypatch):
    """Decisão 11: apagar a regra deixaria `publicados/<slug>.json` e os states órfãos, e o
    rebuild os traria de volta como campanha desconhecida."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    sm.upsert_edition("woow-beauty--2026-09-15", {"stage": "ready", "campanha": "woow-beauty"})
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.set_curadoria({"op": "remover", "campanha": "woow-beauty"})
    assert "woow-beauty--2026-09-15" in str(e.value) or "edição" in str(e.value)


def test_remover_campanha_sem_edicao_continua_funcionando(tmp_path, monkeypatch):
    """O vizinho da recusa acima: a v1.7.0 publicou o `remover` e ele não pode ter sumido."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    r = orchestrator.set_curadoria({"op": "remover", "campanha": "woow-beauty"})
    assert "woow-beauty" not in r["campanhas"]


def test_mensagem_de_remover_a_padrao_nao_promete_comando_inexistente(tmp_path, monkeypatch):
    """Decisão 10: trocar a campanha padrão está fora de escopo e o comando não existe.
    A v1.7.0 escreveu 'troque a padrão antes', que manda o operador procurar o que não há."""
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida) as e:
        orchestrator.set_curadoria({"op": "remover", "campanha": CAMPANHA_PADRAO})
    assert "troque a padrão antes" not in str(e.value)


# ===================================================================== retrocompat
def test_campanha_da_v170_sem_os_campos_novos(tmp_path, monkeypatch):
    """O documento que está em produção HOJE não tem `ativa`, `fontes`, `entrega` nem
    `formato`. O que a ausência significa é decidido nos acessores, e ela não pode virar
    campanha desligada, pesquisa vazia nem envio sem alvo."""
    sm = _local_sm(tmp_path, monkeypatch)
    legado = {"default": CAMPANHA_PADRAO,
              "campanhas": {CAMPANHA_PADRAO: {"nome": "WooW! Daily Drops", "janela_dias": 14,
                                              "titulo_modo": "relatorio",
                                              "bloqueados": [], "liberados": [],
                                              "set_by": "", "set_at": ""}}}
    sm.store.write("campanhas.json", json.dumps(legado, ensure_ascii=False))
    regra = orchestrator.get_curadoria(sm)["campanhas"][CAMPANHA_PADRAO]
    assert orchestrator._campanha_ativa(regra) is True
    assert orchestrator._fontes_da_campanha(regra)["modo"] == "todas"
    assert orchestrator._entrega_da_campanha(regra) == {}
    assert orchestrator._formato_da_campanha(regra) == ""
    assert len(orchestrator._effective_feeds(sm, CAMPANHA_PADRAO)) == \
        len(orchestrator._effective_feeds(sm))


def test_settings_global_continua_valendo_quando_a_campanha_nao_define_nada(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _grava_settings(sm, active_list_key="LK-GLOBAL", active_list_name="Time mK",
                    active_from_email="patrick@metakosmos.com.br", active_from_name="WooW!")
    r = orchestrator.resolve_entrega(sm, "2026-09-15", {}, CAMPANHA_PADRAO)
    assert (r["list_key"], r["origem"]["list_key"]) == ("LK-GLOBAL", "settings")
    assert (r["from_email"], r["origem"]["from_email"]) == ("patrick@metakosmos.com.br", "settings")


def test_set_list_sem_campanha_produz_o_mesmo_alvo_de_antes(tmp_path, monkeypatch):
    """Enquanto só existir a padrão, `set-list` sem `--campanha` tem o mesmo efeito prático
    de quando escrevia `settings.json`: o envio da edição do dia vai para a lista gravada."""
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_active_list({"list_key": "LK-NOVA", "list_name": "Lista Nova",
                                  "_email": "d@mk"})
    entrega = orchestrator.resolve_entrega(sm, "2026-09-15", {}, CAMPANHA_PADRAO)
    args = orchestrator._build_send_args(
        "2026-09-15", {}, orchestrator._delivery(), {"from_email": "x@y", "from_name": "X"},
        {}, entrega)
    assert args[args.index("--list-key") + 1] == "LK-NOVA"


def test_perfil_ausente_deixa_o_newsletter_yaml_byte_a_byte(tmp_path, monkeypatch):
    """Campanha sem perfil não reescreve o YAML do container: o merge só roda quando há o
    que aplicar, e um round-trip de yaml.safe_dump apagaria os comentários do arquivo."""
    sm = _local_sm(tmp_path, monkeypatch)
    destino = tmp_path / "newsletter.yaml"
    original = (orchestrator.CONFIG / "newsletter.yaml").read_text(encoding="utf-8")
    destino.write_text(original, encoding="utf-8")
    orchestrator._write_newsletter_yaml(destino, CAMPANHA_PADRAO, sm)
    assert destino.read_text(encoding="utf-8") == original


def test_perfil_ilegivel_nao_derruba_o_workdir(tmp_path, monkeypatch):
    """Fail-open no que barra conteúdo: perfil podre deixa o YAML do container como está,
    que é o comportamento de antes da v1.8.0, em vez de derrubar o estágio."""
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("campanhas.json", "{isto não é json")
    destino = tmp_path / "newsletter.yaml"
    original = (orchestrator.CONFIG / "newsletter.yaml").read_text(encoding="utf-8")
    destino.write_text(original, encoding="utf-8")
    orchestrator._write_newsletter_yaml(destino, "woow-beauty", sm)
    assert destino.read_text(encoding="utf-8") == original


# ================================ Parte 5 no pipeline: o formato resolvido no workdir
# Os dois scripts rodam ISOLADOS (o `_run_script` copia um arquivo só para o workdir e roda
# com BASE = workdir), então cada um resolve o formato lendo os YAMLs que o orchestrator
# injeta. Estes testes batem no resolvedor de cada um, e o eixo que importa é o mesmo dos
# outros fallbacks do repo: buraco na config não pode jogar fora a edição.
import generate_content  # noqa: E402
import render_newsletter  # noqa: E402


def _workdir_config(tmp_path, monkeypatch, newsletter=None, formatos=None):
    cfg = tmp_path / "config"
    cfg.mkdir()
    if newsletter is not None:
        (cfg / "newsletter.yaml").write_text(yaml.safe_dump(newsletter), encoding="utf-8")
    if formatos is not None:
        (cfg / "formatos.yaml").write_text(yaml.safe_dump(formatos), encoding="utf-8")
    monkeypatch.setattr(generate_content, "CONFIG", cfg)
    monkeypatch.setattr(render_newsletter, "CONFIG", cfg)
    return cfg


_CATALOGO = {"formatos": {"daily-drops": {"prompt_write": "write.md",
                                          "template": "woow-daily-drops.html.j2",
                                          "campos_obrigatorios": ["cabecalho", "titulo_edicao",
                                                                  "sumario", "manchete"]},
                          "so-manchete": {"prompt_write": "outro.md",
                                          "template": "outro.html.j2",
                                          "campos_obrigatorios": ["manchete"]}}}


def test_sem_formato_escolhido_roda_com_o_de_hoje(tmp_path, monkeypatch):
    _workdir_config(tmp_path, monkeypatch, newsletter={"research": {}}, formatos=_CATALOGO)
    assert generate_content.formato_cfg() == generate_content.FORMATO_DEFAULT
    assert render_newsletter.template_do_formato() == render_newsletter.TEMPLATE_DEFAULT


def test_formato_escolhido_troca_prompt_template_e_campos(tmp_path, monkeypatch):
    """O vizinho do teste acima: quando o formato existe, ele MANDA. Sem este, um resolvedor
    que devolvesse o default para tudo passaria em todos os testes de fallback."""
    _workdir_config(tmp_path, monkeypatch, newsletter={"formato": "so-manchete"},
                    formatos=_CATALOGO)
    fmt = generate_content.formato_cfg()
    assert (fmt["prompt_write"], fmt["template"]) == ("outro.md", "outro.html.j2")
    assert fmt["campos_obrigatorios"] == ["manchete"]
    assert render_newsletter.template_do_formato() == "outro.html.j2"


def test_formatos_yaml_ausente_roda_com_o_de_hoje(tmp_path, monkeypatch):
    _workdir_config(tmp_path, monkeypatch, newsletter={"formato": "so-manchete"})
    assert generate_content.formato_cfg() == generate_content.FORMATO_DEFAULT
    assert render_newsletter.template_do_formato() == render_newsletter.TEMPLATE_DEFAULT


def test_formatos_yaml_ilegivel_roda_com_o_de_hoje(tmp_path, monkeypatch):
    cfg = _workdir_config(tmp_path, monkeypatch, newsletter={"formato": "so-manchete"})
    (cfg / "formatos.yaml").write_text("{isto: nao é: yaml", encoding="utf-8")
    assert generate_content.formato_cfg() == generate_content.FORMATO_DEFAULT
    assert render_newsletter.template_do_formato() == render_newsletter.TEMPLATE_DEFAULT


def test_formato_desconhecido_no_catalogo_roda_com_o_de_hoje(tmp_path, monkeypatch):
    _workdir_config(tmp_path, monkeypatch, newsletter={"formato": "sumiu"}, formatos=_CATALOGO)
    assert generate_content.formato_cfg() == generate_content.FORMATO_DEFAULT
    assert render_newsletter.template_do_formato() == render_newsletter.TEMPLATE_DEFAULT


def test_validate_sem_campos_continua_exigindo_os_quatro_de_hoje():
    """A assinatura de antes (`validate(content)`) é o gabarito e não pode ter mudado."""
    with pytest.raises(SystemExit) as e:
        generate_content.validate({"manchete": {"corpo": "x"}, "secundaria_1": {"corpo": "y"},
                                   "sinal_1": {"corpo": "z"}})
    assert "cabecalho" in str(e.value)


def test_sources_report_marca_quais_fontes_entram_na_campanha(tmp_path, monkeypatch):
    """Sem a marca, a tela mostra dez fontes ativas e nenhuma pista de que só duas são
    pesquisadas por esta campanha, e 'por que a pauta veio curta' fica sem resposta."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy", "Modern Retail"]})
    r = orchestrator.get_sources_report({"campanha": "woow-beauty"}, sm)
    assert r["selecao"]["modo"] == "lista"
    entram = {f["source"] for f in r["feeds"] if f["entra"]}
    fora = {f["source"] for f in r["feeds"] if not f["entra"]}
    assert entram == {"Glossy", "Modern Retail"}
    assert "VentureBeat" in fora


def test_sources_report_da_campanha_sem_selecao_marca_todas_as_ativas(tmp_path, monkeypatch):
    """O vizinho: sem seleção, `entra` acompanha `enabled` e a legenda não muda."""
    sm = _local_sm(tmp_path, monkeypatch)
    orchestrator.set_sources({"op": "disable", "source": "Glossy", "_email": "d@mk"})
    r = orchestrator.get_sources_report({}, sm)
    assert r["selecao"]["modo"] == "todas"
    for f in r["feeds"]:
        assert f["entra"] is bool(f.get("enabled", True)), f["source"]


def test_campanha_status_junta_os_cinco_eixos(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty", nome="WooW! Beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy"]})
    orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                                "list_key": "LK-BEAUTY"})
    orchestrator.set_curadoria({"op": "formato", "campanha": "woow-beauty",
                                "formato": "daily-drops"})
    r = orchestrator.get_campanha_status({"campanha": "woow-beauty"}, sm)
    assert r["campanha"] == "woow-beauty" and r["ativa"] is True and r["padrao"] is False
    assert r["fontes"]["entram"] == ["Glossy"]
    assert (r["entrega"]["list_key"], r["entrega"]["origem"]["list_key"]) == ("LK-BEAUTY", "campanha")
    assert r["formato"]["nome"] == "daily-drops"
    assert r["agenda"]["enabled"] is False        # campanha nova não manda e-mail sozinha
    assert r["agenda"]["auto_send"] is False
    assert r["edicao_referencia"].startswith("woow-beauty--")


def test_campanha_status_avisa_fonte_selecionada_que_saiu_do_cadastro(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy", "Modern Retail"]})
    orchestrator.set_sources({"op": "disable", "source": "Glossy", "_email": "d@mk"})
    r = orchestrator.get_campanha_status({"campanha": "woow-beauty"}, sm)
    assert r["fontes"]["entram"] == ["Modern Retail"]
    assert any("Glossy" in a for a in r["fontes"]["avisos"])


def test_campanha_status_recusa_campanha_que_nao_existe(tmp_path, monkeypatch):
    """Relatório é `estrito`: `?campanha=daily-drop` (typo de uma letra) devolvendo 200 com
    tudo zerado não deixa o operador distinguir erro de digitação de campanha vazia."""
    sm = _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.get_campanha_status({"campanha": "daily-drop"}, sm)


def test_metrics_por_campanha_filtra_pela_fila(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    for ed, campanha in (("2026-09-08", None), ("woow-beauty--2026-09-09", "woow-beauty")):
        patch = {"stage": "sent", "date": ed[-10:], "subject": ed}
        if campanha:
            patch["campanha"] = campanha
        sm.upsert_edition(ed, patch)
    monkeypatch.setattr(orchestrator.secrets_store, "get_zma_gemini_env", lambda: {})
    todas = orchestrator.get_metrics()
    assert {e["edition"] for e in todas["editions"]} == {"2026-09-08", "woow-beauty--2026-09-09"}
    so_beauty = orchestrator.get_metrics({"campanha": "woow-beauty"})
    assert [e["edition"] for e in so_beauty["editions"]] == ["woow-beauty--2026-09-09"]
    with pytest.raises(orchestrator.EntradaInvalida):
        orchestrator.get_metrics({"campanha": "nao-existe"})


# ============================== o que o teste de mutação achou que a suíte não segurava
def test_selecao_que_zera_ao_vivo_nao_cai_para_todas(tmp_path, monkeypatch):
    """Decisão 12, no caminho de LEITURA e não no de gravação. A guarda de gravação recusa
    seleção vazia, mas ela não alcança o caso em que TODAS as fontes selecionadas são
    desativadas depois, no cadastro global. Se aí a seleção caísse para 'todas', a campanha
    de beleza passaria a pesquisar em varejo, com 200 e sem log, e ninguém notaria até ler a
    edição. O certo é sair com zero fonte: a pauta vem curta, o alerta dispara e o generate
    recusa no piso de 3 blocos, que é o contrato que já existe."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "fontes", "campanha": "woow-beauty", "modo": "lista",
                                "nomes": ["Glossy", "Modern Retail"]})
    for nome in ("Glossy", "Modern Retail"):
        orchestrator.set_sources({"op": "disable", "source": nome, "_email": "d@mk"})
    assert orchestrator._effective_feeds(sm, "woow-beauty") == []
    # o vizinho: quem não selecionou nada continua com todas as que sobraram ativas
    assert len(orchestrator._effective_feeds(sm, CAMPANHA_PADRAO)) > 1


def test_entrega_gravada_com_campo_vazio_le_como_ausente(tmp_path, monkeypatch):
    """O acessor é o contrato, e ele vale para documento que outro escritor deixou: campo
    vazio (ou só espaço) é ausência, e valor com espaço em volta é normalizado. Sem isto,
    um `list_key: "  "` gravado à mão venceria a precedência e o envio iria para lugar
    nenhum, com a tela dizendo que a campanha tem alvo próprio."""
    sujo = {"list_key": "  ", "list_name": "", "from_email": "  p@mk  ", "from_name": None}
    assert orchestrator._entrega_da_campanha({"entrega": sujo}) == {"from_email": "p@mk"}
    assert orchestrator._entrega_da_campanha({"entrega": "não é dicionário"}) == {}
    assert orchestrator._entrega_da_campanha({}) == {}


def test_campanha_desativada_nao_entra_no_tick(tmp_path, monkeypatch):
    """A tela do acessor não basta: o que importa é o TICK deixar de escolhê-la, e com o
    motivo escrito. Campanha que parou de sair sem nenhuma linha em lugar nenhum é o pior
    modo de falha desta rota."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    for slug in (CAMPANHA_PADRAO, "woow-beauty"):
        orchestrator.set_schedule({"campanha": slug, "send_time": "09:00",
                                   "weekdays": [0, 1, 2, 3, 4, 5, 6], "enabled": True})
    agora = datetime(2026, 9, 15, 9, 5, tzinfo=orchestrator.BRT)

    aprovadas, ignoradas, erros = orchestrator._candidatas_do_tick(sm, agora, "2026-09-15")
    assert {t[1] for t in aprovadas} == {CAMPANHA_PADRAO, "woow-beauty"}, "controle positivo"

    orchestrator.set_curadoria({"op": "desativar", "campanha": "woow-beauty"})
    aprovadas, ignoradas, erros = orchestrator._candidatas_do_tick(sm, agora, "2026-09-15")
    assert {t[1] for t in aprovadas} == {CAMPANHA_PADRAO}
    assert any(i["campanha"] == "woow-beauty" and "desativada" in i["reason"]
               for i in ignoradas), ignoradas
    assert erros == []


def test_catalogo_de_formato_malformado_nao_derruba_o_pipeline(tmp_path, monkeypatch):
    """Entrada que não é dicionário (`daily-drops: "texto"`) é o formato de YAML editado à
    mão mais provável, e é a que um `.get` cru transforma em AttributeError no meio da
    geração."""
    _workdir_config(tmp_path, monkeypatch, newsletter={"formato": "torto"},
                    formatos={"formatos": {"torto": "isto devia ser um dicionário"}})
    assert generate_content.formato_cfg() == generate_content.FORMATO_DEFAULT
    assert render_newsletter.template_do_formato() == render_newsletter.TEMPLATE_DEFAULT


def test_render_woow_usa_o_template_do_formato(tmp_path, monkeypatch):
    """O ponto de produção, não o vizinho: `template_do_formato()` estar certo não prova que
    `render_woow` o CHAMA. Um teste só no resolvedor fica verde com o render lendo a
    constante, e é assim que a campanha nova sai com o HTML da diária."""
    pedidos = []

    class _EnvFalso:
        def get_template(self, nome):
            pedidos.append(nome)
            class _T:
                def render(self, **kw):
                    return "<html>ok</html>"
            return _T()

    cfg = _workdir_config(tmp_path, monkeypatch, newsletter={"formato": "so-manchete"},
                          formatos=_CATALOGO)
    conteudo = tmp_path / "content"
    conteudo.mkdir()
    (conteudo / "2026-09-15.json").write_text(json.dumps({"content": {"manchete": {}}}),
                                              encoding="utf-8")
    monkeypatch.setattr(render_newsletter, "CONTENT", conteudo)
    monkeypatch.setattr(render_newsletter, "RENDERS", tmp_path / "renders")
    monkeypatch.setattr(render_newsletter, "jinja_env", lambda: _EnvFalso())
    render_newsletter.render_woow("2026-09-15")
    assert pedidos == ["outro.html.j2"], pedidos
    assert cfg.exists()


def test_campanha_pode_trocar_so_o_nome_exibido_do_remetente(tmp_path, monkeypatch):
    """Achado do ensaio local. `from_name` é o nome exibido e é INDEPENDENTE do endereço:
    "WooW! Beauty <patrick@metakosmos.com.br>" é o arranjo que a v1.8.0 recomenda enquanto só
    o patrick@ estiver verificado no ZMA. Tratando os dois como par, a campanha que define só
    o nome — o caminho recomendado — tinha a escolha ignorada em silêncio e o e-mail saía
    assinado "WooW! Daily Drops"."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty", nome="WooW! Beauty")
    orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                                "from_name": "WooW! Beauty"})
    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", {}, "woow-beauty")
    assert (r["from_name"], r["origem"]["from_name"]) == ("WooW! Beauty", "campanha")
    assert (r["from_email"], r["origem"]["from_email"]) == \
        (orchestrator._delivery()["from_email"], "config")
    args = orchestrator._build_send_args(
        "woow-beauty--2026-09-15", {}, orchestrator._delivery(),
        {"from_email": "x@y", "from_name": "X"}, {}, r)
    assert args[args.index("--from-name") + 1] == "WooW! Beauty"


def test_nome_da_lista_continua_colado_na_chave(tmp_path, monkeypatch):
    """O vizinho, e o motivo de a LISTA continuar sendo par: chave e nome identificam o mesmo
    objeto no ZMA. Se a campanha define só o nome, quem manda no envio segue sendo a chave do
    nível de baixo, e mostrar o nome da campanha ao lado dela seria descrever uma lista que
    não é a alvo."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                                "list_name": "Beleza mK"})
    _grava_settings(sm, active_list_key="LK-SETTINGS", active_list_name="Time mK")
    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", {}, "woow-beauty")
    assert r["list_key"] == "LK-SETTINGS"
    assert (r["list_name"], r["origem"]["list_name"]) == ("Time mK", "settings")


def test_nome_de_lista_nao_e_herdado_de_um_nivel_mais_geral(tmp_path, monkeypatch):
    """Segundo achado do ensaio. Campanha com `list_key` e sem `list_name` fazia o nome cair
    para o do container: a tela anunciava "Time mK Daily Drops" ao lado da chave da lista de
    beleza. Rótulo errado é pior que rótulo nenhum."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator.set_curadoria({"op": "entrega", "campanha": "woow-beauty",
                                "list_key": "LK-BEAUTY"})
    r = orchestrator.resolve_entrega(sm, "woow-beauty--2026-09-15", {}, "woow-beauty")
    assert (r["list_key"], r["origem"]["list_key"]) == ("LK-BEAUTY", "campanha")
    assert (r["list_name"], r["origem"]["list_name"]) == ("", "ausente")
