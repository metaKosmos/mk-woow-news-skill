# broker/tests/test_schedule_cli.py — a árvore `schedule` do CLI, com o broker mockado.
#
# Os 5 comandos de agendamento não tinham teste NENHUM até aqui, e a v1.8.0 mexe nos cinco:
# a agenda deixou de ser um documento só e passou a ser um blob por campanha. O que este
# arquivo segura é o que sai daqui — rota, query string e payload — porque é exatamente
# onde `--campanha` some sem deixar rastro: um `schedule on` que perde a flag responde
# "LIGADO" com a mesma cara, ligando a campanha errada.
#
# Mesmo molde de test_curadoria_cli.py: o mock entra no TRANSPORTE (`broker_client._req`),
# não nas funções do cliente. Trocar `bc.set_schedule` por um lambda testaria o teste.
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

AGENDA_PADRAO = {"campanha": "daily-drops", "enabled": False, "send_time": "10:00",
                 "weekdays": [0, 1, 2, 3, 4, 5, 6], "auto_send": False, "until": None,
                 "last_run_date": None}
AGENDA_BEAUTY = {**AGENDA_PADRAO, "campanha": "woow-beauty", "send_time": "09:00"}


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

    def caminho(self, prefixo):
        return next(c["path"] for c in self.chamadas if c["path"].startswith(prefixo))


@pytest.fixture
def broker(monkeypatch):
    b = BrokerFalso()
    b.respostas["/schedule"] = AGENDA_PADRAO
    b.respostas["/schedule/set"] = AGENDA_PADRAO
    b.respostas["/lists"] = {"active": {"list_name": "Time mK", "source": "settings"}}
    monkeypatch.setattr(broker_client, "_req", b._req)
    return b


@pytest.fixture
def diz_sim(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "s")


def _roda(fn, **args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(argparse.Namespace(**args))
    return buf.getvalue()


# --------------------------------------------------------------------------- fiação
def test_subcomandos_de_schedule_existem(monkeypatch):
    """Controle da fiação: um `set_defaults` esquecido só apareceria em produção."""
    monkeypatch.setattr(sys, "argv", ["woow.py", "schedule", "--help"])
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida), pytest.raises(SystemExit):
        woow.main()
    for nome in ("status", "set", "on", "off", "auto-send"):
        assert nome in saida.getvalue()


@pytest.mark.parametrize("sub", ["status", "set", "on", "off", "auto-send"])
def test_todo_subcomando_de_schedule_aceita_campanha(monkeypatch, sub):
    """Sem `--campanha` em TODOS, a agenda da segunda campanha é inalcançável pelo CLI e o
    operador só descobre pela newsletter que não saiu."""
    monkeypatch.setattr(sys, "argv", ["woow.py", "schedule", sub, "--help"])
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida), pytest.raises(SystemExit):
        woow.main()
    assert "--campanha" in saida.getvalue()


# ------------------------------------------------------------------------- leitura
def test_status_sem_campanha_nao_manda_query(broker):
    _roda(woow.cmd_schedule_status, campanha=None)
    assert broker.caminho("/schedule") == "/schedule"


def test_status_com_campanha_manda_na_query(broker):
    broker.respostas["/schedule"] = AGENDA_BEAUTY
    _roda(woow.cmd_schedule_status, campanha="woow-beauty")
    assert broker.caminho("/schedule") == "/schedule?campanha=woow-beauty"


def test_status_nomeia_a_campanha_na_tela(broker):
    """A agenda deixou de ser uma só: sem o nome no cabeçalho, um `on` que caiu na padrão
    por engano fica indistinguível de um que ligou a campanha certa."""
    broker.respostas["/schedule"] = AGENDA_BEAUTY
    saida = _roda(woow.cmd_schedule_status, campanha="woow-beauty")
    assert "woow-beauty" in saida
    # vizinho: a padrão também é nomeada, não fica implícita
    broker.respostas["/schedule"] = AGENDA_PADRAO
    assert "daily-drops" in _roda(woow.cmd_schedule_status, campanha=None)


def test_dica_de_ligar_carrega_a_campanha(broker):
    broker.respostas["/schedule"] = AGENDA_BEAUTY
    saida = _roda(woow.cmd_schedule_status, campanha="woow-beauty")
    assert "schedule on --campanha woow-beauty" in saida
    # vizinho: na padrão a dica continua limpa, sem flag que o operador não precisa digitar
    broker.respostas["/schedule"] = AGENDA_PADRAO
    saida = _roda(woow.cmd_schedule_status, campanha=None)
    assert "schedule on" in saida and "--campanha" not in saida


# -------------------------------------------------------------------------- escrita
def test_on_com_campanha_vai_no_payload(broker):
    _roda(woow.cmd_schedule_on, campanha="woow-beauty")
    assert broker.payload("/schedule/set") == {"enabled": True, "campanha": "woow-beauty"}


def test_on_sem_campanha_nao_inventa_alvo(broker):
    """Omitir é diferente de mandar vazio: quem escolhe a padrão é o broker, num lugar só."""
    _roda(woow.cmd_schedule_on, campanha=None)
    assert broker.payload("/schedule/set") == {"enabled": True}


def test_off_com_campanha_vai_no_payload(broker):
    _roda(woow.cmd_schedule_off, campanha="woow-beauty")
    assert broker.payload("/schedule/set") == {"enabled": False, "campanha": "woow-beauty"}


def test_set_leva_campanha_junto_com_os_campos(broker):
    _roda(woow.cmd_schedule_set, time="09:00", days="util", until=None,
          campanha="woow-beauty")
    assert broker.payload("/schedule/set") == {
        "send_time": "09:00", "weekdays": [0, 1, 2, 3, 4], "campanha": "woow-beauty"}


def test_set_sem_nenhum_campo_continua_recusando(broker):
    """O vizinho: `--campanha` sozinho não é uma mudança de agenda, e não pode passar a
    contar como uma — senão o comando grava um documento inteiro sem o operador ter pedido."""
    with pytest.raises(SystemExit):
        _roda(woow.cmd_schedule_set, time=None, days=None, until=None,
              campanha="woow-beauty")


def test_auto_send_on_leva_campanha(broker, diz_sim):
    _roda(woow.cmd_schedule_autosend, mode="on", campanha="woow-beauty")
    assert broker.payload("/schedule/set") == {"auto_send": True, "campanha": "woow-beauty"}


def test_auto_send_off_leva_campanha(broker):
    _roda(woow.cmd_schedule_autosend, mode="off", campanha="woow-beauty")
    assert broker.payload("/schedule/set") == {"auto_send": False, "campanha": "woow-beauty"}


def test_auto_send_on_continua_pedindo_confirmacao(broker, monkeypatch):
    """Vizinho da guarda que já existia: auto-send dispara sem revisão humana, e a pergunta
    não pode ter sumido no caminho de acrescentar `--campanha`."""
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    saida = _roda(woow.cmd_schedule_autosend, mode="on", campanha="woow-beauty")
    assert "Cancelado" in saida
    assert not [c for c in broker.chamadas if c["path"] == "/schedule/set"]
