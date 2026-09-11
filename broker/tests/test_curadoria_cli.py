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
    b.respostas["/campanhas"] = CURADORIA
    b.respostas["/campanhas/set"] = {"op": "?", "campanha": "daily-drops",
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
    assert broker.rotas() == [("GET", "/campanhas")]
    assert "★ daily-drops" in saida and "woow-beauty" in saida
    assert "janela 14 dia(s)" in saida and "janela 7 dia(s)" in saida
    assert "128 link(s) em 35 edição(ões)" in saida
    assert "herda 'daily-drops'" in saida


def test_criar_manda_slug_nome_e_copiar_de(broker):
    _roda(woow.cmd_curadoria_criar, campanha="woow-beauty", nome="WooW! Beauty",
          copiar_de="daily-drops")
    assert broker.rotas() == [("POST", "/campanhas/set")]
    assert broker.payload("/campanhas/set") == {
        "op": "criar", "campanha": "woow-beauty", "nome": "WooW! Beauty",
        "copiar_de": "daily-drops"}


def test_criar_sem_nome_e_sem_copia_nao_manda_os_campos(broker):
    _roda(woow.cmd_curadoria_criar, campanha="woow-beauty", nome=None, copiar_de=None)
    assert broker.payload("/campanhas/set") == {"op": "criar", "campanha": "woow-beauty"}


# ------------------------------------------------- --campanha omitido não vira campo vazio
def test_set_sem_campanha_nao_manda_o_campo(broker):
    """Campo ausente é o que faz o broker resolver a padrão. Mandar `campanha: null` cairia
    na validação de slug, e mandar "" seria uma campanha inexistente."""
    _roda(woow.cmd_curadoria_set, campanha=None, janela=7, titulo=None)
    payload = broker.payload("/campanhas/set")
    assert payload == {"op": "janela", "janela_dias": 7}
    assert "campanha" not in payload


def test_set_com_campanha_manda_o_slug(broker):
    """Controle positivo do teste acima: o campo tem de continuar chegando quando existe."""
    _roda(woow.cmd_curadoria_set, campanha="woow-beauty", janela=7, titulo=None)
    assert broker.payload("/campanhas/set") == {
        "op": "janela", "campanha": "woow-beauty", "janela_dias": 7}


def test_historico_sem_campanha_e_sem_dias_vai_sem_query(broker):
    _roda(woow.cmd_curadoria_historico, campanha=None, dias=None)
    assert broker.query("/publicados") == "/publicados"


def test_historico_com_dias_manda_a_query(broker):
    _roda(woow.cmd_curadoria_historico, campanha="woow-beauty", dias=7)
    assert broker.query("/publicados") == "/publicados?campanha=woow-beauty&dias=7"


def test_set_titulo_usa_a_op_titulo(broker):
    _roda(woow.cmd_curadoria_set, campanha=None, janela=None, titulo="on")
    assert broker.payload("/campanhas/set") == {"op": "titulo", "titulo_modo": "on"}


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
    assert broker.payload("/campanhas/set") == {"op": "janela", "janela_dias": 0}


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
    assert broker.payload("/campanhas/set") == {
        "op": "bloquear", "link": "https://glossy.co/a", "motivo": "matéria paga"}


def test_bloquear_mostra_que_o_link_ja_estava_bloqueado(broker, diz_sim):
    broker.respostas["/campanhas"] = {
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
    assert broker.payload("/campanhas/set") == {"op": "liberar", "link": "https://glossy.co/a"}


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
    broker.respostas["/campanhas"] = {
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
    broker.respostas["/campanhas"] = {
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
    # Desde a v1.8.0 a campanha é CABEÇALHO de grupo, não sufixo de linha: numa lista só,
    # ordenada pelo id, a edição de campanha nova cai depois de todas as datas nuas
    # (`'w' > '2'` em ASCII) e o operador lê a fila da diária inteira antes de achar a dele.
    assert "woow-beauty — Gaveta" in saida
    assert "WooW! Daily Drops — Gaveta" in saida
    assert "campanha woow-beauty" not in saida  # não sobrou o sufixo antigo
    # e cada edição ficou embaixo do cabeçalho certo
    corpo_beauty = saida.split("woow-beauty — Gaveta")[1]
    assert "2026-09-09" in corpo_beauty and "2026-09-08" not in corpo_beauty


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


# ---------------------------------------- os resíduos que a auditoria de prontidão achou
def test_sources_list_sobrevive_a_feed_sem_nome(broker):
    """Regressão nova da 1.7.0: na base, todo acesso ao nome era guardado com
    `(f.get('source') or '')`. O `mudas` nasceu sem a guarda e o `', '.join` estourava
    TypeError DEPOIS de imprimir a lista inteira, deixando o operador com um traceback no
    lugar do rodapé. `main()` não captura TypeError, então o exit era 1."""
    broker.respostas["/sources"] = {
        "source": "state",
        "feeds": [{"source": "Glossy", "url": "https://glossy.co/feed", "enabled": True,
                   "last_test": None, "publicadas": 3, "materias": 3, "barradas": 0},
                  {"url": "https://retaildive.com/feeds/news/", "enabled": True,
                   "last_test": None, "publicadas": 0, "materias": 0, "barradas": 0}]}
    saida = _roda(woow.cmd_sources_list, campanha=None)
    assert "(sem nome)" in saida
    # O vizinho que continua passando: sem ele, "não estourou" imita "a guarda funciona".
    assert "3 publicada(s)" in saida
    assert "1 fonte(s) ativa(s) sem nenhuma publicação" in saida


def test_sources_list_nao_manda_desativar_quando_a_memoria_e_curta(broker):
    """Procedência só existe desde 02/09/2026. No começo, "0 publicadas" é dado sobre o
    tamanho da memória, não sobre a fonte, e o conselho de desativar caía em fonte saudável.
    Desativar fonte encolhe a pauta, e a pauta tem piso de 3 blocos no generate."""
    broker.respostas["/sources"] = {
        "source": "state", "campanha": "daily-drops", "edicoes_na_memoria": 8,
        "feeds": [{"source": "Business of Fashion", "url": "https://bof.com/feed",
                   "enabled": True, "last_test": None,
                   "publicadas": 0, "materias": 0, "barradas": 0}]}
    saida = _roda(woow.cmd_sources_list, campanha=None)
    assert "NUNCA PUBLICOU" not in saida
    assert "considere 'sources test', trocar a URL ou desativar" not in saida
    assert "0 publicada(s) nas 8 edição(ões) que a memória alcança" in saida
    assert "ainda NÃO é motivo para desativar" in saida


def test_sources_list_volta_a_acusar_quando_a_memoria_cresce(broker):
    """O caminho que segue aberto: com memória longa, 0 publicadas é achado de verdade, e
    era esse o achado que motivou a coluna. Sem este teste, o conserto acima poderia ter
    silenciado a acusação para sempre e nada apontaria isso."""
    broker.respostas["/sources"] = {
        "source": "state", "campanha": "daily-drops", "edicoes_na_memoria": 40,
        "feeds": [{"source": "Business of Fashion", "url": "https://bof.com/feed",
                   "enabled": True, "last_test": None,
                   "publicadas": 0, "materias": 0, "barradas": 0}]}
    saida = _roda(woow.cmd_sources_list, campanha=None)
    assert "NUNCA PUBLICOU" in saida
    assert "considere 'sources test', trocar a URL ou desativar" in saida


def test_sources_list_sem_o_alcance_da_memoria_mantem_o_comportamento_antigo(broker):
    """Broker 1.7.0 anterior a este conserto não manda `edicoes_na_memoria`. Ausência não
    pode virar "memória curta", senão a acusação desaparece contra broker antigo."""
    broker.respostas["/sources"] = {
        "source": "state",
        "feeds": [{"source": "Business of Fashion", "url": "https://bof.com/feed",
                   "enabled": True, "last_test": None,
                   "publicadas": 0, "materias": 0, "barradas": 0}]}
    assert "NUNCA PUBLICOU" in _roda(woow.cmd_sources_list, campanha=None)


def test_checkpoint_1_nao_perde_a_pauta_no_dia_de_muitos_barrados(broker):
    """O broker corta o summary em 4000 e o `.research.md` reserva 3700 para a seção de
    barrados. Cortar em 2000 aqui fazia a lista de candidatos e a cobertura por fonte
    desaparecerem da ÚNICA tela em que o operador revisa a pauta, a partir de 6 barrados, e
    não existe outro caminho para vê-la: não há rota para o `.research.md` e o workdir do
    broker é apagado no `finally` do `run_stage`."""
    barrados_md = "\n".join(f"- Item repetido número {i} com manchete de tamanho realista "
                            f"https://exemplo.com/materia-{i}" for i in range(30))
    summary = ("## Pesquisa\nJanela: 3 dias\n\n"
               f"## Já publicados (barrados)\n{barrados_md}\n\n"
               "## Candidatos\n- Marca X abre loja com provador AR\n\n"
               "## Cobertura por fonte\n- Glossy: 20\n")
    assert len(summary) > 2000, "o cenário precisa passar dos 2000 para valer de teste"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        woow._render_research({"summary": summary, "barrados": 30, "campanha": "daily-drops",
                               "barrados_itens": []})
    saida = buf.getvalue()
    assert "## Candidatos" in saida
    assert "Marca X abre loja com provador AR" in saida
    assert "## Cobertura por fonte" in saida


# ------------------------------------------- "Última pesquisa" do `curadoria status`
# A queue de produção (45 edições, medida em 2026-09-11) tem 9 chaves que não são data e
# nasceram antes do campo `campanha`, logo caem na padrão. A queue vem ordenada por chave, e
# 'w' e 't' > '2' em ASCII: `linhas[-1]` devolvia `webinar-ultima-chamada` para sempre.
# Medido em produção: a pesquisa de 2026-09-11 barrou 4 itens e a linha dizia
# "sem registro de barrados (pesquisa anterior à trava)".
QUEUE_PRODUCAO = [
    {"edition": "2026-09-10", "date": "2026-09-10", "stage": "sent",
     "campanha": "daily-drops", "barrados": 0},
    {"edition": "2026-09-11", "date": "2026-09-11", "stage": "researched",
     "campanha": "daily-drops", "barrados": 4},
    {"edition": "2026-w25", "date": "", "stage": "sent", "campanha": None},
    {"edition": "2026-w26", "date": "2026-06-23", "stage": "sent", "campanha": None},
    {"edition": "2026-w27", "date": "2026-06-23", "stage": "sent", "campanha": None},
    {"edition": "teste-remetente-2026-07-16", "date": "2026-07-16", "stage": "sent",
     "campanha": None},
    {"edition": "webinar-2026-08-21", "date": "2026-08-15", "stage": "ready", "campanha": None},
    {"edition": "webinar-confirmacao", "date": "2026-08-15", "stage": "ready", "campanha": None},
    {"edition": "webinar-lembrete-1h", "date": "2026-08-15", "stage": "ready", "campanha": None},
    {"edition": "webinar-lembrete-24h", "date": "2026-08-15", "stage": "ready", "campanha": None},
    {"edition": "webinar-ultima-chamada", "date": "2026-08-15", "stage": "ready",
     "campanha": None},
]


def test_ultima_pesquisa_e_a_edicao_mais_recente_por_data(broker):
    """Controle: a queue vem NA ORDEM DE PRODUÇÃO, com a chave de webinar por último."""
    assert QUEUE_PRODUCAO[-1]["edition"] == "webinar-ultima-chamada", "fixture fora de ordem"
    broker.respostas["/queue"] = {"editions": QUEUE_PRODUCAO}
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "Última pesquisa: 2026-09-11 (researched)" in saida
    assert "webinar" not in saida
    assert "4 item(ns) barrado(s) por já terem saído" in saida
    assert "sem registro de barrados" not in saida


def test_sem_edicao_diaria_o_legado_mais_RECENTE_ganha_nao_o_de_chave_maior(broker):
    """O limite honesto deste conserto, medido em vez de suposto.

    As 5 edições de webinar têm `date: 2026-08-15`, uma data VÁLIDA, e nasceram sem o campo
    `campanha`, então `campanha_da_edicao` as lê como `daily-drops`. Elas continuam
    candidatas legítimas: excluir chave não-data não as tira da disputa, porque quem as
    qualifica é o campo `date`.

    O que o conserto elimina é a resposta PERMANENTEMENTE errada. Antes, `webinar-ultima-
    chamada` ganhava de qualquer edição diária, para sempre, por ordem de string. Agora ela
    só aparece quando não existe nenhuma edição diária mais recente, o que na cadência diária
    dura menos de um dia.

    O resíduo é higiene de dado, não de código: aquelas 5 edições deveriam estar numa
    campanha própria. Enquanto não estiverem, elas SÃO daily-drops para o sistema.
    Desempate entre as cinco (mesma data, nenhuma com chave de data): pela chave, que dá
    `webinar-ultima-chamada`, determinístico."""
    broker.respostas["/queue"] = {"editions": [e for e in QUEUE_PRODUCAO
                                               if not e["edition"].startswith("2026-09")]}
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "Última pesquisa: webinar-ultima-chamada" in saida, saida
    # e não `teste-remetente-2026-07-16`, que tem a chave "maior" que 'w'? Não: 't' < 'w'.
    # O ponto é a DATA: 2026-08-15 é mais recente que 2026-07-16.
    assert "teste-remetente" not in saida


def test_sem_edicao_nenhuma_o_bloco_de_ultima_pesquisa_some(broker):
    """O caminho que segue aberto: sem candidata, o comando cala em vez de inventar."""
    broker.respostas["/queue"] = {"editions": [
        {"edition": "2026-w25", "date": "", "stage": "sent", "campanha": None}]}
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "Última pesquisa" not in saida
    assert "Memória" in saida          # e o resto do comando continua funcionando


def test_ultima_pesquisa_respeita_a_campanha(broker):
    """Edição de outra campanha não pode virar a "última pesquisa" desta."""
    broker.respostas["/queue"] = {"editions": QUEUE_PRODUCAO + [
        {"edition": "2026-09-12", "date": "2026-09-12", "stage": "researched",
         "campanha": "woow-beauty", "barrados": 9}]}
    saida = _roda(woow.cmd_curadoria_status, campanha=None)
    assert "Última pesquisa: 2026-09-11 (researched)" in saida
    assert "2026-09-12" not in saida


# ------------------------------------ o id que o broker COMPÕE tem de voltar para o CLI
#
# A v1.8.0 fez `create_campaign` compor `<slug>--<data>` a partir de `--edition <data>
# --campanha <slug>`. O CLI guardava `edition = a.edition` e nunca relia a resposta: todo
# passo seguinte batia na edição do Daily Drops daquele dia. No `manual_html` isso é perda
# de dado — `run generate` cai no pipeline automático e o HTML do operador é ignorado.

def _cria(broker, resposta, **args):
    broker.respostas["/campaigns/create"] = resposta
    base = {"edition": "2026-09-09", "type": "news_auto", "campanha": None,
            "html": None, "subject": None, "preheader": None, "list_key": None}
    return _roda(woow.cmd_create_campaign, **{**base, **args})


def test_create_campaign_news_auto_usa_o_id_composto_que_o_broker_devolveu(broker):
    saida = _cria(broker, {"edition": "woow-beauty--2026-09-09", "type": "news_auto",
                           "stage": "empty", "campanha": "woow-beauty"},
                  campanha="woow-beauty")
    assert "woow-beauty--2026-09-09" in saida
    assert "run --edition woow-beauty--2026-09-09" in saida
    # o id que o operador digitou é a edição do Daily Drops daquele dia: mandá-lo rodar ali
    # é o mesmo sequestro que a Parte 1 existe para eliminar, agora como instrução copiável
    assert "run --edition 2026-09-09" not in saida


def test_create_campaign_manual_html_gera_na_edicao_composta(broker, diz_nao):
    """O caso com perda de dado: `run generate` na edição errada cai no pipeline automático
    (ela é news_auto) e DESCARTA o html e o subject que o operador passou."""
    broker.respostas["/run"] = {"preview_url": "https://pub/x.html", "stage": "ready"}
    _cria(broker, {"edition": "woow-beauty--2026-09-09", "type": "manual_html",
                   "stage": "empty", "campanha": "woow-beauty"},
          type="manual_html", campanha="woow-beauty", html=__file__, subject="Beauty #1")
    corridas = [c["payload"]["edition"] for c in broker.chamadas if c["path"] == "/run"]
    assert corridas == ["woow-beauty--2026-09-09"]


def test_create_campaign_sem_campanha_continua_usando_o_id_digitado(broker):
    """O vizinho: sem composição, o id não muda, e nada na tela pode mudar."""
    saida = _cria(broker, {"edition": "2026-09-09", "type": "news_auto",
                           "stage": "empty", "campanha": "daily-drops"})
    assert "run --edition 2026-09-09" in saida


def test_create_campaign_sobrevive_a_resposta_sem_edition(broker):
    """Broker antigo (ou resposta cortada) não pode virar TypeError na cara do operador."""
    saida = _cria(broker, {"type": "news_auto", "stage": "empty"})
    assert "run --edition 2026-09-09" in saida
