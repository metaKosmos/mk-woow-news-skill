# broker/tests/test_campanha_cli.py — o grupo `campanha` do CLI (v1.8.0, MAR-523).
#
# Mesma disciplina do test_curadoria_cli.py: o mock entra no TRANSPORTE
# (`broker_client._req`), não nas funções do cliente. O que precisa ficar preso aqui é a rota
# e o payload que saem do CLI, inclusive a query string, que é onde `--campanha` some ou
# deixa de sumir. Trocar `bc.set_campanha` por um lambda testaria o teste.
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
    def __init__(self):
        self.chamadas = []
        self.respostas = {}

    def _req(self, method, path, payload=None):
        self.chamadas.append({"method": method, "path": path, "payload": payload})
        return self.respostas.get(path.split("?")[0], {})

    def rotas(self):
        return [(c["method"], c["path"]) for c in self.chamadas]

    def payload(self, rota):
        return next(c["payload"] for c in self.chamadas if c["path"] == rota)

    def query(self, prefixo):
        return next(c["path"] for c in self.chamadas if c["path"].startswith(prefixo))


STATUS = {
    "campanha": "woow-beauty", "nome": "WooW! Beauty", "padrao": False, "ativa": True,
    "regra": {"janela_dias": 7, "titulo_modo": "off", "bloqueados": 2, "liberados": 0},
    "fontes": {"modo": "lista", "selecionadas": ["Glossy", "Modern Retail", "Sumida"],
               "entram": ["Glossy", "Modern Retail"],
               "avisos": ["'Sumida' não está mais no cadastro de fontes"]},
    "entrega": {"list_key": "LK-BEAUTY", "list_name": "Beleza mK",
                "from_email": "patrick@metakosmos.com.br", "from_name": "WooW! Beauty",
                "origem": {"list_key": "campanha", "list_name": "campanha",
                           "from_email": "settings", "from_name": "campanha"}},
    "formato": {"nome": "daily-drops", "prompt_write": "write.md",
                "template": "woow-daily-drops.html.j2"},
    "perfil": {"research.days_lookback": 7},
    "agenda": {"send_time": "09:00", "weekdays": [0, 1, 2, 3, 4], "enabled": True,
               "auto_send": False, "last_run_date": "2026-09-09"},
    "memoria": {"links": 12, "edicoes": 3},
    "edicoes": {"total": 3, "custo_brl_ultimas": 4.2137,
                "ultimas": [{"edition": "woow-beauty--2026-09-09", "date": "2026-09-09",
                             "stage": "sent", "subject": "Beleza e IA"}]},
    "edicao_referencia": "woow-beauty--2026-09-10",
}

CAMPANHAS = {"default": "daily-drops", "campanhas": {
    "daily-drops": {"nome": "WooW! Daily Drops", "janela_dias": 14, "titulo_modo": "relatorio",
                    "bloqueados": [], "liberados": [], "ativa": True,
                    "fontes": {"modo": "todas", "nomes": []}, "entrega": {},
                    "formato": "daily-drops", "memoria_links": 128, "memoria_edicoes": 35},
    "woow-beauty": {"nome": "WooW! Beauty", "janela_dias": 7, "titulo_modo": "off",
                    "bloqueados": [], "liberados": [], "ativa": True,
                    "fontes": {"modo": "lista", "nomes": ["Glossy"]},
                    "entrega": {"list_key": "LK-BEAUTY"}, "formato": "daily-drops",
                    "memoria_links": 12, "memoria_edicoes": 3}}}


@pytest.fixture
def broker(monkeypatch):
    b = BrokerFalso()
    b.respostas["/campanhas"] = CAMPANHAS
    b.respostas["/campanhas/set"] = {"op": "?", "campanha": "woow-beauty",
                                     "default": "daily-drops",
                                     "campanhas": CAMPANHAS["campanhas"]}
    b.respostas["/campanhas/status"] = STATUS
    b.respostas["/schedule"] = STATUS["agenda"]
    b.respostas["/schedule/set"] = {**STATUS["agenda"], "campanha": "woow-beauty"}
    b.respostas["/metrics"] = {"campanha": "woow-beauty", "editions": [
        {"edition": "woow-beauty--2026-09-09", "subject": "Beleza e IA",
         "metrics": {"open_rate": 0.41}, "cost": {"total_brl": 1.23}}]}
    monkeypatch.setattr(broker_client, "_req", b._req)
    return b


def _roda(fn, **args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(argparse.Namespace(**args))
    return buf.getvalue()


# ------------------------------------------------------------------ fiação do grupo
def test_subcomandos_de_campanha_existem(monkeypatch):
    """Controle da fiação: sem isto, um `set_defaults` esquecido só apareceria em produção."""
    monkeypatch.setattr(sys, "argv", ["woow.py", "campanha", "--help"])
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida), pytest.raises(SystemExit):
        woow.main()
    texto = saida.getvalue()
    for nome in ("list", "criar", "status", "fontes", "entrega", "formato", "agenda",
                 "ativar", "desativar"):
        assert nome in texto, nome


def test_cada_subcomando_de_campanha_tem_funcao():
    p = woow.monta_parser()
    grupo = p._subparsers._group_actions[0].choices["campanha"]
    for nome, sub in grupo._subparsers._group_actions[0].choices.items():
        assert sub.get_default("fn") is not None, nome


def test_grupo_antigo_continua_funcionando_e_avisa(monkeypatch, capsys):
    """`curadoria` é o nome que está na SKILL.md instalada nas máquinas do time. Ele continua
    funcionando; o aviso é do mesmo tipo do aviso de versão: avisa, não bloqueia."""
    p = woow.monta_parser()
    a = p.parse_args(["curadoria", "list"])
    assert a.fn is woow.cmd_curadoria_list
    assert getattr(a, "_grupo_antigo", False) is True
    woow._avisa_nome_novo(a)
    assert "virou `campanha`" in capsys.readouterr().err
    # o vizinho: o grupo NOVO não avisa nada
    b = p.parse_args(["campanha", "list"])
    woow._avisa_nome_novo(b)
    assert capsys.readouterr().err == ""


# ------------------------------------------------------------------ fontes
def test_fontes_usar_manda_a_lista_de_nomes(broker):
    _roda(woow.cmd_campanha_fontes, campanha="woow-beauty",
          usar="Glossy, Modern Retail", todas=False)
    assert broker.payload("/campanhas/set") == {
        "op": "fontes", "campanha": "woow-beauty", "modo": "lista",
        "nomes": ["Glossy", "Modern Retail"]}


def test_fontes_todas_volta_ao_default(broker):
    _roda(woow.cmd_campanha_fontes, campanha="woow-beauty", usar=None, todas=True)
    assert broker.payload("/campanhas/set") == {
        "op": "fontes", "campanha": "woow-beauty", "modo": "todas"}


def test_fontes_sem_escolha_nenhuma_e_recusado_no_cliente(broker):
    """Recusar aqui poupa uma ida ao broker e dá a mensagem com o comando certo dentro."""
    with pytest.raises(SystemExit) as e:
        _roda(woow.cmd_campanha_fontes, campanha="woow-beauty", usar=None, todas=False)
    assert "--usar" in str(e.value) and "--todas" in str(e.value)
    assert not [c for c in broker.chamadas if c["method"] == "POST"]


def test_fontes_com_os_dois_e_recusado(broker):
    with pytest.raises(SystemExit):
        _roda(woow.cmd_campanha_fontes, campanha="woow-beauty", usar="Glossy", todas=True)
    assert not [c for c in broker.chamadas if c["method"] == "POST"]


# ------------------------------------------------------------------ entrega
def test_entrega_manda_so_os_campos_informados(broker):
    """Campo ausente é herança; campo presente e vazio é limpeza. Mandar os quatro sempre
    apagaria o remetente da campanha a cada `--list-key`."""
    _roda(woow.cmd_campanha_entrega, campanha="woow-beauty", list_key="LK-NOVA",
          list_name=None, from_email=None, from_name=None)
    assert broker.payload("/campanhas/set") == {
        "op": "entrega", "campanha": "woow-beauty", "list_key": "LK-NOVA"}


def test_entrega_vazia_limpa_e_chega_como_string_vazia(broker):
    _roda(woow.cmd_campanha_entrega, campanha="woow-beauty", list_key="",
          list_name=None, from_email=None, from_name=None)
    assert broker.payload("/campanhas/set")["list_key"] == ""


def test_entrega_sem_campo_nenhum_e_recusada(broker):
    with pytest.raises(SystemExit):
        _roda(woow.cmd_campanha_entrega, campanha="woow-beauty", list_key=None,
              list_name=None, from_email=None, from_name=None)
    assert not [c for c in broker.chamadas if c["method"] == "POST"]


# ------------------------------------------------------------------ formato e liga/desliga
def test_formato_manda_o_nome(broker):
    _roda(woow.cmd_campanha_formato, campanha="woow-beauty", formato="daily-drops")
    assert broker.payload("/campanhas/set") == {
        "op": "formato", "campanha": "woow-beauty", "formato": "daily-drops"}


def test_ativar_e_desativar_usam_as_ops_certas(broker):
    _roda(woow.cmd_campanha_desativar, campanha="woow-beauty")
    _roda(woow.cmd_campanha_ativar, campanha="woow-beauty")
    ops = [c["payload"]["op"] for c in broker.chamadas if c["path"] == "/campanhas/set"]
    assert ops == ["desativar", "ativar"]


# ------------------------------------------------------------------ agenda
def test_agenda_grava_horario_dias_e_liga_numa_chamada(broker):
    """O passo real do operador é um só. Em três comandos, esquecer o terceiro deixa a
    agenda gravada e DESLIGADA, que é indistinguível de não ter feito nada."""
    _roda(woow.cmd_campanha_agenda, campanha="woow-beauty", time="09:00", days="util",
          until=None, on=True, off=False)
    p = broker.payload("/schedule/set")
    assert p["send_time"] == "09:00"
    assert p["weekdays"] == [0, 1, 2, 3, 4]
    assert p["enabled"] is True
    assert p["campanha"] == "woow-beauty"


def test_agenda_sem_argumento_nenhum_so_mostra(broker):
    _roda(woow.cmd_campanha_agenda, campanha="woow-beauty", time=None, days=None,
          until=None, on=False, off=False)
    assert not [c for c in broker.chamadas if c["method"] == "POST"]
    assert "campanha=woow-beauty" in broker.query("/schedule")


def test_agenda_com_on_e_off_juntos_e_recusada(broker):
    with pytest.raises(SystemExit):
        _roda(woow.cmd_campanha_agenda, campanha="woow-beauty", time=None, days=None,
              until=None, on=True, off=True)


# ------------------------------------------------------------------ o raio-X
def test_status_mostra_os_cinco_eixos_juntos(broker):
    """A tela existe para responder 'por que esta edição saiu assim?' sem abrir cinco
    documentos. Se um eixo sumir dela, o operador volta à caça."""
    saida = _roda(woow.cmd_campanha_status, campanha="woow-beauty")
    assert "Curadoria" in saida and "janela 7" in saida
    assert "Fontes" in saida and "Glossy, Modern Retail" in saida
    assert "Formato" in saida and "write.md" in saida
    assert "Entrega" in saida and "LK-BEAUTY" in saida
    assert "Agenda" in saida and "09:00" in saida
    assert "Memória" in saida and "12 link(s)" in saida
    assert "woow-beauty--2026-09-10" in saida


def test_status_mostra_a_origem_de_cada_campo_da_entrega(broker):
    """Com quatro níveis de precedência, 'para qual lista isso foi?' deixa de ter resposta
    óbvia. Sem a origem, o operador que trocou a lista da campanha e viu o envio ir para
    outra não distingue defeito de herança."""
    saida = _roda(woow.cmd_campanha_status, campanha="woow-beauty")
    assert "[campanha]" in saida
    assert "[settings]" in saida


def test_status_denuncia_fonte_selecionada_que_nao_entra_mais(broker):
    saida = _roda(woow.cmd_campanha_status, campanha="woow-beauty")
    assert "⚠" in saida and "Sumida" in saida


def test_status_diz_quando_a_campanha_esta_desativada(broker):
    broker.respostas["/campanhas/status"] = {**STATUS, "ativa": False}
    saida = _roda(woow.cmd_campanha_status, campanha="woow-beauty")
    assert "DESATIVADA" in saida


def test_status_sobrevive_a_agenda_ilegivel(broker):
    """Blob de agenda podre não pode cegar os outros quatro eixos da tela."""
    broker.respostas["/campanhas/status"] = {**STATUS, "agenda": {"erro": "json quebrado"}}
    saida = _roda(woow.cmd_campanha_status, campanha="woow-beauty")
    assert "não consegui ler" in saida
    assert "LK-BEAUTY" in saida  # o resto continua lá


# ------------------------------------------------------------------ metrics recortado
def test_metrics_com_campanha_vai_na_query(broker):
    _roda(woow.cmd_metrics, campanha="woow-beauty")
    assert "campanha=woow-beauty" in broker.query("/metrics")


def test_metrics_sem_campanha_nao_manda_query(broker):
    """O vizinho: campo ausente e campo vazio não são a mesma coisa para o broker."""
    _roda(woow.cmd_metrics, campanha=None)
    assert broker.query("/metrics") == "/metrics"


def test_metrics_vazio_diz_que_esta_vazio(broker):
    broker.respostas["/metrics"] = {"campanha": "woow-beauty", "editions": []}
    saida = _roda(woow.cmd_metrics, campanha="woow-beauty")
    assert "Nenhuma edição enviada" in saida


def test_aviso_do_remover_nao_promete_efeito_impossivel(broker, monkeypatch, capsys):
    """O texto antigo dizia que as edições da campanha "passam a cair na regra da padrão".
    Desde a v1.8.0 o broker RECUSA remover campanha com edição gravada, então esse efeito não
    existe mais. Aviso que descreve o impossível ensina o operador a não ler aviso."""
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    saida = _roda(woow.cmd_curadoria_remover, campanha="woow-beauty")
    assert "passam a cair na regra da campanha padrão" not in saida
    assert "campanha desativar --campanha woow-beauty" in saida
