# broker/tests/test_run_daily_retoma.py — MAR-194: a tentativa seguinte não refaz o que já
# deu certo.
#
# Sem isto, o retry do tick é caro demais para ter teto útil: cada nova tentativa refaria o
# `generate` inteiro, que são três chamadas ao Gemini de texto mais o art-director e o Nano
# Banana. Três tentativas custariam três edições.
#
# A régua é o STAGE_RANK, que já existe e já governa a monotonia do estado. Aqui ele passa a
# governar também o que rodar: estágio cujo posto o estado já alcançou não roda de novo.
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import orchestrator  # noqa: E402
from state_manager import LocalStore, StateManager  # noqa: E402

EDICAO = "daily-drops-2026-09-17"


@pytest.fixture
def sm(tmp_path, monkeypatch):
    s = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: s)
    return s


@pytest.fixture
def rodados(monkeypatch):
    """Registra que estágios run_daily mandou rodar, sem rodar nenhum."""
    chamados = []

    def _fake(edition, stage, payload):
        chamados.append(stage)
        # O estágio de verdade avança o estado; sem isto o teste não distingue "pulou" de
        # "rodou e não gravou", e o caso do `empty` passaria por vacuidade.
        orchestrator._sm().upsert_edition(
            edition, {"stage": {"research": "researched", "generate": "ready",
                                "send": "sent"}[stage]})
        return {"stage": stage}

    monkeypatch.setattr(orchestrator, "run_stage", _fake)
    return chamados


def test_edicao_do_zero_roda_tudo(sm, rodados):
    """Controle positivo: sem ele, 'nunca roda nada' imitaria 'pula o que já foi feito'."""
    orchestrator.run_daily(EDICAO, auto_send=True)
    assert rodados == ["research", "generate", "send"]


def test_pesquisa_feita_nao_e_refeita(sm, rodados):
    sm.upsert_edition(EDICAO, {"stage": "researched"})
    orchestrator.run_daily(EDICAO, auto_send=True)
    assert rodados == ["generate", "send"]


def test_edicao_pronta_so_envia(sm, rodados):
    """O caso que paga o retry: a falha foi no envio, e refazer o generate custaria o dia."""
    sm.upsert_edition(EDICAO, {"stage": "ready"})
    orchestrator.run_daily(EDICAO, auto_send=True)
    assert rodados == ["send"]


def test_edicao_pronta_sem_auto_send_nao_roda_nada(sm, rodados):
    sm.upsert_edition(EDICAO, {"stage": "ready"})
    resultado = orchestrator.run_daily(EDICAO, auto_send=False)
    assert rodados == []
    assert resultado["stage"] == "ready"


def test_edicao_enviada_continua_saindo_pela_porta_de_cima(sm, rodados):
    """Comportamento que já existia, e que o pulo não pode ter absorvido em silêncio:
    'já enviada' é resposta própria, não um caso de lista vazia."""
    sm.upsert_edition(EDICAO, {"stage": "sent"})
    resultado = orchestrator.run_daily(EDICAO, auto_send=True)
    assert rodados == []
    assert resultado["skipped"] == "já enviada"


def test_sem_auto_send_o_envio_nao_entra_mesmo_do_zero(sm, rodados):
    orchestrator.run_daily(EDICAO, auto_send=False)
    assert rodados == ["research", "generate"]
