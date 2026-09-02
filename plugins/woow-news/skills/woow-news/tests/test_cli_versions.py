# Primeiro teste do lado do CLIENTE. Não existia nenhum, e foi por isso que a tabela do
# `versions` pôde contradizer o próprio rodapé sem ninguém notar.
import io
import contextlib
import sys
import types
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

# stub do broker_client ANTES de importar o woow (que faz `import broker_client as bc`),
# para o teste não precisar de rede nem de login
_fake = types.ModuleType("broker_client")


class BrokerError(Exception):
    pass


_fake.BrokerError = BrokerError
_fake.get_clients = lambda: {}
sys.modules.setdefault("broker_client", _fake)

import woow  # noqa: E402


def _render(resposta):
    woow.bc.get_clients = lambda: resposta
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        woow.cmd_versions(None)
    return buf.getvalue()


def _marcados(saida):
    return {l.split()[1] for l in saida.splitlines() if l.startswith(" !")}


def test_quem_esta_a_frente_nao_e_marcado():
    """Janela entre o merge e o deploy: o repo já subiu de versão e o broker ainda não.
    Quem está à frente aparecia com '!' sob a legenda '! = atrasado'."""
    saida = _render({
        "published": "1.3.0",
        "clients": {
            "david@metakosmos.com.br": {"version": "1.5.0", "last_seen": "2026-08-25T09:00:00-03:00"},
            "patrick@metakosmos.com.br": {"version": "1.3.0", "last_seen": "2026-08-25T09:00:00-03:00"},
        },
        "atrasados": [],
    })
    assert _marcados(saida) == set()
    assert "Todo mundo em dia" in saida


def test_controle_positivo_quem_esta_atras_e_marcado():
    """Sem isto, "nada marcado" passaria por acerto mesmo se a marcação estivesse morta."""
    saida = _render({
        "published": "1.5.0",
        "clients": {
            "joao@metakosmos.com.br": {"version": "1.3.0", "last_seen": "2026-08-24T10:00:00-03:00"},
            "patrick@metakosmos.com.br": {"version": "", "last_seen": "2026-08-20T10:00:00-03:00"},
        },
        "atrasados": [
            {"email": "joao@metakosmos.com.br", "version": "1.3.0",
             "last_seen": "2026-08-24T10:00:00-03:00", "nunca_chamou": False},
            {"email": "patrick@metakosmos.com.br", "version": "",
             "last_seen": "2026-08-20T10:00:00-03:00", "nunca_chamou": False},
        ],
    })
    assert _marcados(saida) == {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"}
    assert "2 atrasado(s)" in saida


def test_tabela_e_rodape_nunca_discordam():
    """O invariante que os dois defeitos violaram, cada um para um lado: a contagem do
    rodapé tem que ser exatamente o que a tabela marcou. Inclui o cliente registrado sem
    versão (skill anterior à 1.5.0), que o broker conta e a comparação semver perdia."""
    resposta = {
        "published": "1.5.0",
        "clients": {
            "david@metakosmos.com.br": {"version": "1.5.0", "last_seen": "2026-08-25T09:00:00-03:00"},
            "joao@metakosmos.com.br": {"version": "", "last_seen": "2026-08-20T10:00:00-03:00"},
            "patrick@metakosmos.com.br": {"version": "1.3.0", "last_seen": "2026-08-24T10:00:00-03:00"},
        },
        "atrasados": [
            {"email": "joao@metakosmos.com.br", "version": "", "last_seen": "", "nunca_chamou": False},
            {"email": "patrick@metakosmos.com.br", "version": "1.3.0", "last_seen": "", "nunca_chamou": False},
        ],
    }
    saida = _render(resposta)
    assert len(_marcados(saida)) == len(resposta["atrasados"])
    assert _marcados(saida) == {x["email"] for x in resposta["atrasados"]}


def test_quem_nunca_chamou_aparece_com_o_motivo():
    saida = _render({
        "published": "1.5.0",
        "clients": {"david@metakosmos.com.br": {"version": "1.5.0", "last_seen": "2026-08-25T09:00:00-03:00"}},
        "atrasados": [{"email": "joao@metakosmos.com.br", "version": "", "last_seen": "",
                       "nunca_chamou": True}],
    })
    assert "nunca chamou o broker" in saida
    assert "1 atrasado(s)" in saida


def test_operador_nao_recebe_a_lista_e_ve_so_a_contagem():
    saida = _render({
        "published": "1.5.0",
        "clients": {"patrick@metakosmos.com.br": {"version": "1.3.0", "last_seen": "2026-08-24T10:00:00-03:00"}},
        "atrasados_total": 2,
    })
    assert "tabela completa só para admin" in saida
    assert _marcados(saida) == {"patrick@metakosmos.com.br"}  # fallback semver na própria linha


def test_status_mostra_descarte_e_link_suspeito(monkeypatch, capsys):
    """MAR-483: o descarte precisa aparecer no comando que o operador roda todo dia."""
    import woow
    monkeypatch.setattr(woow.bc, "queue", raising=False, value=lambda: {"editions": [
        {"edition": "2026-09-03", "date": "2026-09-03", "stage": "ready",
         "itens": 4, "descartados": 1, "links_suspeitos": 2,
         "motivos": ["source_id_fora_do_pool"]},
        {"edition": "2026-09-02", "date": "2026-09-02", "stage": "sent",
         "itens": 5, "descartados": 0, "links_suspeitos": 0},
    ]})
    woow.cmd_status(None)
    out = capsys.readouterr().out
    assert "4 itens" in out and "1 descartado(s): source_id_fora_do_pool" in out
    assert "2 link(s) suspeito(s)" in out
    assert "5 itens" not in out  # edição completa não polui a listagem
