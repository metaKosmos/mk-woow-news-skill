# broker/tests/test_schedule.py
import sys, pathlib
from datetime import datetime
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import orchestrator
from state_manager import StateManager, LocalStore, BRT


def _sched(**over):
    s = dict(orchestrator.SCHEDULE_DEFAULTS)
    s.update(over)
    return s


def _dt(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=BRT)


# ---------------------------------------------- _should_run_now (função pura)
def test_disabled_never_runs():
    assert not orchestrator._should_run_now(_sched(enabled=False), _dt(2026, 7, 1, 10, 0), "empty")[0]


def test_runs_only_at_or_after_time():
    s = _sched(enabled=True, send_time="10:00")
    assert not orchestrator._should_run_now(s, _dt(2026, 7, 1, 9, 59), "empty")[0]
    assert orchestrator._should_run_now(s, _dt(2026, 7, 1, 10, 0), "empty")[0]
    assert orchestrator._should_run_now(s, _dt(2026, 7, 1, 10, 15), "empty")[0]


def test_weekday_filter():
    d = _dt(2026, 7, 1, 10, 0)
    wd = d.weekday()
    assert orchestrator._should_run_now(_sched(enabled=True, weekdays=[wd]), d, "empty")[0]
    others = [x for x in range(7) if x != wd]
    assert not orchestrator._should_run_now(_sched(enabled=True, weekdays=others), d, "empty")[0]


def test_until_window():
    s = _sched(enabled=True, until="2026-07-07")
    assert orchestrator._should_run_now(s, _dt(2026, 7, 7, 10, 0), "empty")[0]
    assert not orchestrator._should_run_now(s, _dt(2026, 7, 8, 10, 0), "empty")[0]


def test_dedup_last_run_date():
    s = _sched(enabled=True, last_run_date="2026-07-01")
    assert not orchestrator._should_run_now(s, _dt(2026, 7, 1, 10, 0), "empty")[0]
    assert orchestrator._should_run_now(s, _dt(2026, 7, 2, 10, 0), "empty")[0]


def test_stage_guard_review_vs_auto():
    review = _sched(enabled=True, auto_send=False)
    auto = _sched(enabled=True, auto_send=True)
    # revisão para em ready -> não roda de novo; auto ainda precisa disparar -> roda
    assert not orchestrator._should_run_now(review, _dt(2026, 7, 1, 10, 0), "ready")[0]
    assert orchestrator._should_run_now(auto, _dt(2026, 7, 1, 10, 0), "ready")[0]
    # ambos pulam se já enviada
    assert not orchestrator._should_run_now(review, _dt(2026, 7, 1, 10, 0), "sent")[0]
    assert not orchestrator._should_run_now(auto, _dt(2026, 7, 1, 10, 0), "sent")[0]


# ---------------------------------------------- get/set_schedule (LocalStore)
def test_get_set_schedule_roundtrip(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    assert orchestrator.get_schedule(sm)["enabled"] is False  # default quando ausente
    s = orchestrator.set_schedule({"enabled": True, "send_time": "08:30",
                                   "weekdays": [0, 1, 2, 3, 4],
                                   "_email": "joao@metakosmos.com.br"})
    assert s["enabled"] is True and s["send_time"] == "08:30"
    assert s["set_by"] == "joao@metakosmos.com.br"
    assert orchestrator.get_schedule(sm)["weekdays"] == [0, 1, 2, 3, 4]


def test_set_schedule_preserves_last_run_date(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    orchestrator._mark_schedule_run(sm, "2026-07-01")
    orchestrator.set_schedule({"send_time": "09:00"})
    assert orchestrator.get_schedule(sm)["last_run_date"] == "2026-07-01"


def test_set_schedule_clears_until_on_empty(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    orchestrator.set_schedule({"until": "2026-07-07"})
    assert orchestrator.get_schedule(sm)["until"] == "2026-07-07"
    orchestrator.set_schedule({"until": ""})
    assert orchestrator.get_schedule(sm)["until"] is None


def test_set_schedule_validates(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"send_time": "25:00"})
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"weekdays": [7]})
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"weekdays": []})
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"until": "07/07"})


# ---------------------------------------------- prazo e nova tentativa no mesmo dia
def test_prazo_padrao_e_tres_horas_depois_do_envio():
    """09:00 dá 12:00, o prazo que o David fixou em 25/10/2026 para o Daily Drops."""
    assert orchestrator._prazo_envio(_sched(send_time="09:00")) == 12 * 60


def test_prazo_configurado_vence_o_padrao():
    assert orchestrator._prazo_envio(_sched(send_time="09:00", prazo_envio="11:30")) == 11 * 60 + 30


def test_prazo_padrao_nao_passa_da_meia_noite():
    assert orchestrator._prazo_envio(_sched(send_time="22:30")) == 23 * 60 + 59


def test_set_schedule_valida_e_limpa_o_prazo(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    assert orchestrator.set_schedule({"prazo_envio": "11:00"})["prazo_envio"] == "11:00"
    with pytest.raises(ValueError):
        orchestrator.set_schedule({"prazo_envio": "25:00"})
    assert orchestrator.set_schedule({"prazo_envio": ""})["prazo_envio"] is None


def test_claim_conta_as_tentativas_do_dia_e_zera_no_seguinte(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    orchestrator._mark_schedule_run(sm, "2026-07-01", agora=_dt(2026, 7, 1, 9, 0))
    orchestrator._mark_schedule_run(sm, "2026-07-01", agora=_dt(2026, 7, 1, 9, 15))
    s = orchestrator.get_schedule(sm)
    assert s["tentativas"] == 2 and s["tentativa_em"] == "2026-07-01T09:15:00-03:00"
    orchestrator._mark_schedule_run(sm, "2026-07-02", agora=_dt(2026, 7, 2, 9, 0))
    assert orchestrator.get_schedule(sm)["tentativas"] == 1


def _hoje(**over):
    """Agenda que já rodou hoje (2026-07-01), tentativa começada às 09:00."""
    base = dict(enabled=True, auto_send=True, send_time="09:00", last_run_date="2026-07-01",
                tentativas_data="2026-07-01", tentativa_em="2026-07-01T09:00:00-03:00",
                tentativas=1)
    return _sched(**{**base, **over})


def _barrada(ultima="2026-07-01T09:00:40-03:00"):
    return {"envio_zma": {"status": "barrado_revisao", "ultima_tentativa_em": ultima}}


def _falhou(at="2026-07-01T09:04:00-03:00"):
    return {"health": {"last_error": {"stage": "generate", "message": "x", "at": at}}}


def test_barrada_pela_revisao_tenta_de_novo_antes_do_prazo():
    ok, motivo = orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 11, 45), "ready", _barrada())
    assert ok and "revisão" in motivo


def test_barrada_pela_revisao_para_no_prazo():
    assert not orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 12, 0), "ready", _barrada())[0]


def test_barrada_com_a_tentativa_ainda_rodando_nao_tenta_de_novo():
    sched = _hoje(tentativa_em="2026-07-01T09:15:00-03:00")
    assert not orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 30), "ready", _barrada())[0]


def test_falha_desta_tentativa_tenta_de_novo():
    ok, motivo = orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 9, 15), "empty", _falhou())
    assert ok and "2 de 3" in motivo


def test_falha_de_tentativa_anterior_nao_conta_com_a_atual_rodando():
    sched = _hoje(tentativa_em="2026-07-01T09:15:00-03:00", tentativas=2)
    assert not orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 30), "empty", _falhou())[0]


def test_terceira_falha_encerra_o_dia():
    sched = _hoje(tentativas=3)
    assert not orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 45), "empty", _falhou())[0]


def test_contador_de_ontem_nao_conta_hoje():
    sched = _hoje(tentativas_data="2026-06-30", tentativas=3)
    assert orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 15), "empty", _falhou())[0]


def test_falha_depois_do_prazo_nao_tenta_de_novo():
    assert not orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 12, 0), "empty", _falhou())[0]


def test_ja_rodou_sem_falha_registrada_segue_sem_rodar():
    """O vizinho: com a edição em andamento ou sem rastro, o dedup de sempre."""
    ok, motivo = orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 9, 15), "empty", {})
    assert not ok and motivo == "já rodou hoje"


def test_tentativa_que_morreu_sem_registro_conta_como_falha_depois_do_teto():
    """Request interrompida (timeout, instância reciclada) não grava `last_error`. Passado o
    teto de uma request, a tentativa morreu, e o dia ainda tem conserto."""
    ok, motivo = orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 9, 30), "empty", {})
    assert ok and "2 de 3" in motivo


def test_agenda_sem_inicio_de_tentativa_segue_o_dedup_de_sempre():
    """Agenda gravada antes desta versão não tem `tentativa_em`: sem régua, nada de nova
    tentativa por tempo."""
    sched = _hoje(tentativa_em=None)
    assert not orchestrator._should_run_now(sched, _dt(2026, 7, 1, 11, 0), "empty", {})[0]


def test_barrada_cuja_tentativa_falhou_antes_de_registrar_o_envio_tenta_de_novo():
    """A tentativa levantou antes de chegar ao send_zma (leitura do GCS no `_workdir`): ficou o
    `last_error`, não o `ultima_tentativa_em`."""
    sched = _hoje(tentativa_em="2026-07-01T09:15:00-03:00")
    estado = {**_barrada(), **_falhou(at="2026-07-01T09:15:04-03:00")}
    assert orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 30), "ready", estado)[0]


def test_barrada_cuja_tentativa_morreu_sem_registro_tenta_de_novo_depois_do_teto():
    sched = _hoje(tentativa_em="2026-07-01T09:15:00-03:00")
    assert not orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 30), "ready", _barrada())[0]
    assert orchestrator._should_run_now(sched, _dt(2026, 7, 1, 9, 45), "ready", _barrada())[0]


def test_edicao_enviada_nao_e_tentada_mesmo_com_erro_velho():
    assert not orchestrator._should_run_now(_hoje(), _dt(2026, 7, 1, 9, 15), "sent", _falhou())[0]
