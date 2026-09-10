# broker/tests/test_curadoria_cli.py — a árvore `curadoria` do CLI, com o broker mockado.
#
# O mock entra no TRANSPORTE (`broker_client._req`), não nas funções do cliente. É de
# propósito: o que este arquivo precisa segurar é a rota e o payload que saem daqui —
# inclusive a query string, que é onde `--campanha` some ou deixa de sumir. Trocar
# `bc.get_publicados` por um lambda testaria o teste.
import argparse
import contextlib
import io
import pathlib
import sys

import pytest

SCRIPTS = (pathlib.Path(__file__).resolve().parents[2]
           / "plugins" / "woow-news" / "skills" / "woow-news" / "scripts")
sys.path.insert(0, str(SCRIPTS))

import broker_client  # noqa: E402
import woow  # noqa: E402


class BrokerFalso:
    """Registra o que o CLI chamou e devolve a resposta canned da rota."""

    def __init__(self):
        self.chamadas = []
        self.respostas = {}

    def _req(self, method, path, payload=None):
        self.chamadas.append({"method": method, "path": path, "payload": payload})
        return self.respostas.get(path.split("?")[0], {})

    # -- consultas do teste --
    def rotas(self):
        return [(c["method"], c["path"]) for c in self.chamadas]

    def escritas(self):
        return [c for c in self.chamadas if c["method"] == "POST"]

    def payload(self, rota):
        return next(c["payload"] for c in self.chamadas if c["path"] == rota)

    def query(self, prefixo):
        return next(c["path"] for c in self.chamadas if c["path"].startswith(prefixo))


def _campanha(nome, janela=14, titulo="relatorio", **over):
    base = {"nome": nome, "janela_dias": janela, "titulo_modo": titulo,
            "bloqueados": [], "liberados": [], "memoria_links": 0, "memoria_edicoes": 0}
    return {**base, **over}


CURADORIA = {
    "default": "daily-drops",
    "campanhas": {
        "daily-drops": _campanha("WooW! Daily Drops", memoria_links=128, memoria_edicoes=35),
        "woow-beauty": _campanha("WooW! Beauty", janela=7, titulo="off"),
    },
}

PUBLICADOS = {
    "campanha": "daily-drops", "janela_dias": 14, "titulo_modo": "relatorio", "edicoes": 35,
    "links": [
        {"link": "https://glossy.co/a", "edition": "2026-09-08", "date": "2026-09-08",
         "campo": "manchete", "source": "Glossy", "titulo": "Marca X abre loja com provador AR"},
        {"link": "https://voguebusiness.com/b", "edition": "2026-09-07", "date": "2026-09-07",
         "campo": "nota", "source": "Vogue Business", "titulo": "Varejo de luxo testa 3D"},
    ],
    "bloqueados": [], "liberados": [],
}

QUEUE = {"editions": [
    {"edition": "2026-09-07", "date": "2026-09-07", "stage": "sent", "campanha": "daily-drops",
     "itens": 5, "descartados": 0, "links_suspeitos": 0, "barrados": 0},
    {"edition": "2026-09-08", "date": "2026-09-08", "stage": "researched",
     "campanha": "daily-drops", "barrados": 3},
]}

SOURCES = {"source": "state", "campanha": "daily-drops", "edicao_referencia": "2026-09-08",
           "feeds": [
               {"source": "Glossy", "url": "https://glossy.co/feed", "enabled": True,
                "last_test": {"status": "ok", "found": 20, "at": "2026-09-08T21:00:00-03:00"},
                "publicadas": 12, "materias": 12, "barradas": 2},
               {"source": "Business of Fashion", "url": "https://bof.com/feed", "enabled": True,
                "last_test": {"status": "ok", "found": 20, "at": "2026-09-08T21:00:00-03:00"},
                "publicadas": 0, "materias": 0, "barradas": 0},
           ]}


@pytest.fixture
def broker(monkeypatch):
    b = BrokerFalso()
    b.respostas["/curadoria"] = CURADORIA
    b.respostas["/curadoria/set"] = {"op": "?", "campanha": "daily-drops",
                                     "default": "daily-drops", "campanhas": CURADORIA["campanhas"]}
    b.respostas["/publicados"] = PUBLICADOS
    b.respostas["/queue"] = QUEUE
    b.respostas["/sources"] = SOURCES
    b.respostas["/admin/publicados/rebuild"] = {"campanhas": {"daily-drops": 128}}
    monkeypatch.setattr(broker_client, "_req", b._req)
    return b


@pytest.fixture
def diz_sim(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "s")


@pytest.fixture
def diz_nao(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "n")


def _roda(fn, **args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(argparse.Namespace(**args))
    return buf.getvalue()


# --------------------------------------------------------- a árvore existe e bate na rota
def test_subcomandos_de_curadoria_existem(monkeypatch):
    """Controle da fiação: sem isto, um `set_defaults` esquecido só apareceria em produção."""
    monkeypatch.setattr(sys, "argv", ["woow.py", "curadoria", "--help"])
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida), pytest.raises(SystemExit):
        woow.main()
    texto = saida.getvalue()
    for nome in ("list", "criar", "set", "status", "historico",
                 "bloquear", "liberar", "remover", "rebuild"):
        assert nome in texto


def test_cada_subcomando_de_curadoria_tem_funcao(monkeypatch):
    """`--help` mostra o nome mesmo sem `set_defaults(fn=...)`; o que prova a fiação é
    parsear e achar a função."""
    for argv, esperado in (
            (["curadoria", "list"], woow.cmd_curadoria_list),
            (["curadoria", "criar", "--campanha", "x"], woow.cmd_curadoria_criar),
            (["curadoria", "set", "--janela", "7"], woow.cmd_curadoria_set),
            (["curadoria", "status"], woow.cmd_curadoria_status),
            (["curadoria", "historico"], woow.cmd_curadoria_historico),
            (["curadoria", "bloquear", "--link", "https://a.co/x"], woow.cmd_curadoria_bloquear),
            (["curadoria", "liberar", "--link", "https://a.co/x"], woow.cmd_curadoria_liberar),
            (["curadoria", "remover", "--campanha", "x"], woow.cmd_curadoria_remover),
            (["curadoria", "rebuild"], woow.cmd_curadoria_rebuild)):
        monkeypatch.setattr(sys, "argv", ["woow.py", *argv])
        assert woow.monta_parser().parse_args().fn is esperado


def test_list_le_curadoria_e_marca_a_padrao(broker):
    saida = _roda(woow.cmd_curadoria_list)
    assert broker.rotas() == [("GET", "/curadoria")]
    assert "★ daily-drops" in saida and "woow-beauty" in saida
    assert "janela 14 dia(s)" in saida and "janela 7 dia(s)" in saida
    assert "128 link(s) em 35 edição(ões)" in saida
    assert "herda 'daily-drops'" in saida


def test_criar_manda_slug_nome_e_copiar_de(broker):
    _roda(woow.cmd_curadoria_criar, campanha="woow-beauty", nome="WooW! Beauty",
          copiar_de="daily-drops")
    assert broker.rotas() == [("POST", "/curadoria/set")]
    assert broker.payload("/curadoria/set") == {
        "op": "criar", "campanha": "woow-beauty", "nome": "WooW! Beauty",
        "copiar_de": "daily-drops"}


def test_criar_sem_nome_e_sem_copia_nao_manda_os_campos(broker):
    _roda(woow.cmd_curadoria_criar, campanha="woow-beauty", nome=None, copiar_de=None)
    assert broker.payload("/curadoria/set") == {"op": "criar", "campanha": "woow-beauty"}


# ------------------------------------------------- --campanha omitido não vira campo vazio
def test_set_sem_campanha_nao_manda_o_campo(broker):
    """Campo ausente é o que faz o broker resolver a padrão. Mandar `campanha: null` cairia
    na validação de slug, e mandar "" seria uma campanha inexistente."""
    _roda(woow.cmd_curadoria_set, campanha=None, janela=7, titulo=None)
    payload = broker.payload("/curadoria/set")
    assert payload == {"op": "janela", "janela_dias": 7}
    assert "campanha" not in payload


def test_set_com_campanha_manda_o_slug(broker):
    """Controle positivo do teste acima: o campo tem de continuar chegando quando existe."""
    _roda(woow.cmd_curadoria_set, campanha="woow-beauty", janela=7, titulo=None)
    assert broker.payload("/curadoria/set") == {
        "op": "janela", "campanha": "woow-beauty", "janela_dias": 7}


def test_historico_sem_campanha_e_sem_dias_vai_sem_query(broker):
    _roda(woow.cmd_curadoria_historico, campanha=None, dias=None)
    assert broker.query("/publicados") == "/publicados"


def test_historico_com_dias_manda_a_query(broker):
    _roda(woow.cmd_curadoria_historico, campanha="woow-beauty", dias=7)
    assert broker.query("/publicados") == "/publicados?campanha=woow-beauty&dias=7"


def test_set_titulo_usa_a_op_titulo(broker):
    _roda(woow.cmd_curadoria_set, campanha=None, janela=None, titulo="on")
    assert broker.payload("/curadoria/set") == {"op": "titulo", "titulo_modo": "on"}


def test_set_sem_nada_para_mudar_recusa(broker):
    with pytest.raises(SystemExit):
        _roda(woow.cmd_curadoria_set, campanha=None, janela=None, titulo=None)
    assert broker.escritas() == []


# ------------------------------------------------------------------ o kill switch da trava
def test_janela_zero_avisa_em_destaque_e_pede_confirmacao(broker, diz_nao):
    saida = _roda(woow.cmd_curadoria_set, campanha=None, janela=0, titulo=None)
    assert "DESLIGA A TRAVA DE REPETIÇÃO" in saida
    assert "39 links repetidos em 35" in saida
    assert "Cancelado." in saida
    assert broker.escritas() == []


def test_janela_zero_confirmada_grava(broker, diz_sim):
    _roda(woow.cmd_curadoria_set, campanha=None, janela=0, titulo=None)
    assert broker.payload("/curadoria/set") == {"op": "janela", "janela_dias": 0}


def test_janela_maior_que_zero_nao_pergunta_nada(broker, monkeypatch):
    """Encurtar a janela não reabre o defeito; só o 0 reabre. Confirmar tudo treina o
    operador a responder 's' sem ler, que é como a confirmação do 0 perderia o valor."""
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("não devia perguntar"))
    _roda(woow.cmd_curadoria_set, campanha=None, janela=7, titulo=None)
    assert broker.escritas()


# ------------------------------------------------------- confirmação antes de agir
@pytest.mark.parametrize("fn,args", [
    (lambda: woow.cmd_curadoria_bloquear,
     {"campanha": None, "link": "https://glossy.co/a", "motivo": "matéria paga"}),
    (lambda: woow.cmd_curadoria_liberar, {"campanha": None, "link": "https://glossy.co/a"}),
    (lambda: woow.cmd_curadoria_remover, {"campanha": "woow-beauty"}),
    (lambda: woow.cmd_curadoria_rebuild, {}),
])
def test_confirmacao_recusada_nao_chama_o_broker(broker, diz_nao, fn, args):
    saida = _roda(fn(), **args)
    assert "Cancelado." in saida
    assert broker.escritas() == []


def test_bloquear_mostra_o_estado_atual_e_manda_link_e_motivo(broker, diz_sim):
    saida = _roda(woow.cmd_curadoria_bloquear, campanha=None,
                  link="https://glossy.co/a", motivo="matéria paga")
    assert "sem marca manual" in saida  # estado atual, antes de confirmar
    assert "https://glossy.co/a" in saida
    assert broker.payload("/curadoria/set") == {
        "op": "bloquear", "link": "https://glossy.co/a", "motivo": "matéria paga"}


def test_bloquear_mostra_que_o_link_ja_estava_bloqueado(broker, diz_sim):
    broker.respostas["/curadoria"] = {
        "default": "daily-drops",
        "campanhas": {"daily-drops": _campanha(
            "WooW! Daily Drops",
            bloqueados=[{"link": "https://glossy.co/a", "por": "patrick@metakosmos.com.br",
                         "em": "2026-09-08T10:00:00-03:00", "motivo": "matéria paga"}])}}
    saida = _roda(woow.cmd_curadoria_bloquear, campanha=None,
                  link="https://glossy.co/a", motivo=None)
    assert "bloqueado por patrick@metakosmos.com.br" in saida


def test_liberar_confirmado_usa_a_op_liberar(broker, diz_sim):
    _roda(woow.cmd_curadoria_liberar, campanha=None, link="https://glossy.co/a")
    assert broker.payload("/curadoria/set") == {"op": "liberar", "link": "https://glossy.co/a"}


def test_remover_recusa_a_campanha_padrao_sem_chamar_o_broker(broker, diz_sim):
    with pytest.raises(SystemExit):
        _roda(woow.cmd_curadoria_remover, campanha="daily-drops")
    assert broker.escritas() == []


def test_rebuild_confirmado_bate_na_rota_de_admin(broker, diz_sim):
    saida = _roda(woow.cmd_curadoria_rebuild)
    assert ("POST", "/admin/publicados/rebuild") in broker.rotas()
    assert "ADMIN" in saida


# ------------------------------------------------------------------ `curadoria status`
def test_status_renderiza_a_regra_e_os_barrados(broker):
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "14 dia(s)" in saida
    assert "relatorio (mede e reporta, não barra)" in saida
    assert "Última pesquisa: 2026-09-08" in saida
    assert "3 item(ns) barrado(s)" in saida
    assert "Glossy 2" in saida                      # de que fontes veio o barrado
    assert "Marca X abre loja com provador AR" in saida   # e o que barra: a memória
    assert "2026-09-08" in saida


def test_status_com_janela_zero_diz_que_nada_e_barrado(broker):
    broker.respostas["/curadoria"] = {
        "default": "daily-drops",
        "campanhas": {"daily-drops": _campanha("WooW! Daily Drops", janela=0)}}
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "TRAVA DESLIGADA" in saida
    assert "nada está sendo barrado" in saida
    assert not [c for c in broker.chamadas if c["path"].startswith("/publicados")]


def test_status_sem_barrado_nao_pede_a_quebra_por_fonte(broker):
    """A quebra por fonte custa uma chamada a /sources; sem barrado ela imprimiria nada."""
    broker.respostas["/queue"] = {"editions": [
        {"edition": "2026-09-08", "date": "2026-09-08", "stage": "researched",
         "campanha": "daily-drops", "barrados": 0}]}
    _roda(woow.cmd_curadoria_status, campanha=None)
    assert not [c for c in broker.chamadas if c["path"].startswith("/sources")]


def test_status_mostra_o_bloqueio_manual(broker):
    broker.respostas["/curadoria"] = {
        "default": "daily-drops",
        "campanhas": {"daily-drops": _campanha(
            "WooW! Daily Drops",
            bloqueados=[{"link": "https://glossy.co/a", "por": "patrick@metakosmos.com.br",
                         "em": "2026-09-08T10:00:00-03:00", "motivo": "matéria paga"}])}}
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "Bloqueio manual" in saida and "matéria paga" in saida


def test_status_de_campanha_inexistente_explica_em_vez_de_dar_400(broker):
    with pytest.raises(SystemExit):
        _roda(woow.cmd_curadoria_status, campanha="nao-existe")


# ------------------------------------------------------------------ `sources list`
def test_sources_list_marca_a_fonte_que_nunca_publicou(broker):
    saida = _roda(woow.cmd_sources_list, campanha=None)
    assert broker.query("/sources") == "/sources"
    assert "NUNCA PUBLICOU" in saida
    assert "1 fonte(s) ativa(s) sem nenhuma publicação: Business of Fashion" in saida
    assert "12 publicada(s)" in saida and "2 barrada(s)" in saida
    assert "barradas medidas em 2026-09-08" in saida


def test_sources_list_nao_acusa_fonte_desativada(broker):
    """Fonte desativada não publicar não é achado: o alerta é sobre a que está ATIVA e
    mesmo assim não entrega pauta."""
    broker.respostas["/sources"] = {
        "source": "state",
        "feeds": [{"source": "E-Commerce Brasil", "url": "https://ecommercebrasil.com.br/feed",
                   "enabled": False, "last_test": None,
                   "publicadas": 0, "materias": 0, "barradas": 0}]}
    saida = _roda(woow.cmd_sources_list, campanha=None)
    assert "NUNCA PUBLICOU" not in saida
    assert "desativada" in saida
    assert "sem nenhuma publicação" not in saida


def test_sources_list_com_campanha_manda_a_query(broker):
    _roda(woow.cmd_sources_list, campanha="woow-beauty")
    assert broker.query("/sources") == "/sources?campanha=woow-beauty"


def test_sources_list_nao_acusa_fonte_quando_a_contribuicao_nao_foi_medida(broker):
    """`publicadas` ausente é o terceiro resultado: o broker não conseguiu calcular. Chamar
    isso de zero acusaria de inútil uma fonte que ninguém mediu."""
    broker.respostas["/sources"] = {
        "source": "state",
        "feeds": [{"source": "Business of Fashion", "url": "https://bof.com/feed",
                   "enabled": True, "last_test": None}]}
    saida = _roda(woow.cmd_sources_list, campanha=None)
    assert "contribuição não medida" in saida
    assert "NUNCA PUBLICOU" not in saida
    assert "sem nenhuma publicação" not in saida


# ------------------------------------------------------------------ a gaveta (`status`)
def test_gaveta_mostra_barrados_e_a_campanha_nao_padrao(broker):
    broker.respostas["/queue"] = {"editions": [
        {"edition": "2026-09-08", "date": "2026-09-08", "stage": "researched",
         "campanha": "daily-drops", "barrados": 3},
        {"edition": "2026-09-09", "date": "2026-09-09", "stage": "researched",
         "campanha": "woow-beauty", "barrados": 0},
    ]}
    saida = _roda(woow.cmd_status)
    assert "3 barrado(s) por repetição" in saida
    assert "campanha woow-beauty" in saida
    assert "campanha daily-drops" not in saida  # a padrão não polui a linha


# `titulo`/`fonte` é o formato canônico, o que o `build_health` do research grava. Os dois
# nomes chegaram a circular, e o consumidor no broker lia o errado: a coluna de barradas por
# fonte voltava 0 para sempre, sem erro. Ler um nome só aqui é o que faz a próxima
# divergência aparecer como teste vermelho em vez de coluna zerada.
@pytest.mark.parametrize("item", [
    {"titulo": "Marca X abre loja com provador AR", "fonte": "Glossy",
     "link": "https://glossy.co/a", "publicado_em": "2026-09-08", "publicado_date": "2026-09-08"},
])
def test_run_research_renderiza_barrados_e_alerta(broker, item):
    """O que a trava tirou tem de aparecer no Checkpoint 1, não só no log do broker."""
    broker.respostas["/run"] = {
        "stage": "researched", "summary": "# Pauta", "campanha": "daily-drops",
        "barrados": 1, "parecidos": 2, "barrados_itens": [item],
        "alerta": "ALERTA: pool com 2 candidato(s), abaixo do piso de 3 do generate"}
    saida = _roda(woow.cmd_run, edition="2026-09-09", stage="research")
    assert "1 item(ns) fora da pauta" in saida
    assert "Marca X abre loja com provador AR" in saida
    assert "[Glossy]" in saida
    assert "saiu em 2026-09-08" in saida
    assert "relatório, não barrou" in saida
    assert "abaixo do piso de 3" in saida
    assert "add-pauta" in saida


# ------------------------------------------------------------------ create-campaign
def test_create_campaign_manda_a_campanha_quando_informada(broker):
    broker.respostas["/campaigns/create"] = {"edition": "2026-09-09", "type": "news_auto",
                                             "stage": "empty", "campanha": "woow-beauty"}
    _roda(woow.cmd_create_campaign, edition="2026-09-09", type="news_auto", campanha="woow-beauty",
          html=None, subject=None, preheader=None, list_key=None)
    assert broker.payload("/campaigns/create") == {
        "edition": "2026-09-09", "type": "news_auto", "campanha": "woow-beauty"}


def test_create_campaign_sem_campanha_nao_manda_o_campo(broker):
    broker.respostas["/campaigns/create"] = {"edition": "2026-09-09", "type": "news_auto",
                                             "stage": "empty", "campanha": "daily-drops"}
    _roda(woow.cmd_create_campaign, edition="2026-09-09", type="news_auto", campanha=None,
          html=None, subject=None, preheader=None, list_key=None)
    assert broker.payload("/campaigns/create") == {"edition": "2026-09-09", "type": "news_auto"}


def test_checkpoint1_diz_quando_o_barramento_foi_por_titulo(broker, capsys):
    """Item barrado por título carrega um link que NÃO está na memória. Sem dizer o motivo,
    o operador procura esse link no histórico, não acha, e conclui que a trava está errada.
    E a frase 'relatório, não barrou' seria mentira para quem acabou de ver a pauta encolher."""
    woow._render_research({
        "summary": "# Pauta", "campanha": "daily-drops", "barrados": 2, "parecidos": 1,
        "barrados_itens": [
            {"titulo": "Saiu ontem", "fonte": "Glossy", "link": "https://glossy.co/a",
             "publicado_em": "2026-09-08", "motivo": "url"},
            {"titulo": "Mesma história, outro portal", "fonte": "Modern Retail",
             "link": "https://modernretail.co/b", "publicado_em": "2026-09-08",
             "motivo": "titulo"},
        ]})
    saida = capsys.readouterr().out
    assert "por título" in saida
    assert "camada de TÍTULO" in saida
    assert "não barrou" not in saida


def test_checkpoint1_so_relata_quando_a_camada_nao_barrou(broker, capsys):
    """O vizinho: com a camada em relatório, a frase de 'não barrou' tem que continuar
    existindo. Sem ele, um CLI que nunca mais dissesse isso passaria pelo teste de cima."""
    woow._render_research({
        "summary": "# Pauta", "campanha": "daily-drops", "barrados": 1, "parecidos": 3,
        "barrados_itens": [{"titulo": "Saiu ontem", "fonte": "Glossy",
                            "link": "https://glossy.co/a", "motivo": "url"}]})
    saida = capsys.readouterr().out
    assert "relatório, não barrou" in saida
    assert "camada de TÍTULO" not in saida
