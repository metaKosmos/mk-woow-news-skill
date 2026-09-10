"""Agenda por campanha (`schedules/<campanha>.json`) e um tick que roda UMA por vez.

Três coisas em jogo, e cada uma tem o vizinho que continua passando:

1. A migração. Enquanto `schedules/daily-drops.json` não existir, a padrão lê o
   `schedule.json` legado INTEIRO, `last_run_date` incluso. O claim grava o documento
   resolvido inteiro no caminho novo — é isso que impede o tick de rodar duas vezes no dia
   em que a migração acontece.
2. O claim por campanha. Com um `last_run_date` global, a primeira campanha do dia claima o
   dia e todas as outras respondem "já rodou hoje": 200, `ran: false`, nenhuma newsletter, e
   nenhum erro em lugar nenhum.
3. O isolamento do laço. Uma campanha que estoura não pode levar as seguintes junto, e o
   estado de recuperação depende de ter claimado ou não.
"""
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import orchestrator  # noqa: E402
from state_manager import CAMPANHA_PADRAO, LocalStore, StateManager  # noqa: E402


def _local_sm(tmp_path, monkeypatch):
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    return sm


def _campanha(slug, **regra):
    orchestrator.set_curadoria({"op": "criar", "campanha": slug,
                                "_email": "david@metakosmos.com.br", **regra})


class _RelogioCongelado(datetime):
    _instante_utc = None

    @classmethod
    def now(cls, tz=None):
        instante = cls._instante_utc
        return instante.astimezone(tz) if tz else instante.replace(tzinfo=None)


def _congela(monkeypatch, instante_utc):
    """Congela o relógio DO ORCHESTRATOR.

    O `cron_tick` chama `datetime.now(BRT)` direto, e sem congelar um teste de tick passa
    hoje e fica vermelho amanhã — ou, pior, passa por acaso porque a data real coincidiu.
    """
    _RelogioCongelado._instante_utc = instante_utc
    monkeypatch.setattr(orchestrator, "datetime", _RelogioCongelado)


# 2026-09-15 é uma segunda-feira; 15:00 UTC = 12:00 BRT, depois do send_time default 10:00.
SEGUNDA_MEIO_DIA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)


def _liga(slug, **over):
    orchestrator.set_schedule({"campanha": slug, "enabled": True,
                               "_email": "david@metakosmos.com.br", **over})


# ------------------------------------------------------------------ o blob por campanha

def test_agenda_de_campanha_nova_nasce_desligada_e_sem_auto_send(tmp_path, monkeypatch):
    """Campanha nova não manda e-mail sozinha. É o SCHEDULE_DEFAULTS de hoje, e continua."""
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    s = orchestrator.get_schedule(campanha="woow-beauty")
    assert s["enabled"] is False
    assert s["auto_send"] is False
    assert s["last_run_date"] is None


def test_agenda_grava_no_caminho_por_campanha(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    _liga("woow-beauty", send_time="09:00")
    assert sm.store.read("schedules/woow-beauty.json") is not None
    assert json.loads(sm.store.read("schedules/woow-beauty.json"))["send_time"] == "09:00"


def test_agenda_de_uma_campanha_nao_vaza_na_outra(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    _campanha("woow-food")
    _liga("woow-beauty", send_time="09:00")
    assert orchestrator.get_schedule(campanha="woow-food")["send_time"] == "10:00"
    assert orchestrator.get_schedule(campanha="woow-food")["enabled"] is False


def test_set_schedule_sem_campanha_grava_na_padrao(tmp_path, monkeypatch):
    """Retrocompat do comando: `schedule set` sem `--campanha` continua valendo, e passa a
    dizer em qual campanha gravou."""
    sm = _local_sm(tmp_path, monkeypatch)
    r = orchestrator.set_schedule({"enabled": True, "_email": "d@x"})
    assert r["campanha"] == CAMPANHA_PADRAO
    assert sm.store.read(f"schedules/{CAMPANHA_PADRAO}.json") is not None


def test_set_schedule_preserva_last_run_date_por_campanha(tmp_path, monkeypatch):
    """O vizinho do `_SCHEDULE_SET_FIELDS`: o claim não pode ser apagado por um `set`."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator._mark_schedule_run(sm, "2026-09-15", "woow-beauty")
    _liga("woow-beauty", send_time="08:00")
    s = orchestrator.get_schedule(campanha="woow-beauty")
    assert s["last_run_date"] == "2026-09-15"
    assert s["send_time"] == "08:00"


def test_agenda_de_campanha_inexistente_e_recusada(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    with pytest.raises(orchestrator.EntradaInvalida):
        _liga("nao-existe")
    # vizinho: a padrão sempre existe, mesmo sem `curadoria criar`
    assert orchestrator.set_schedule({"enabled": True})["campanha"] == CAMPANHA_PADRAO


# ------------------------------------------------------------------ migração do legado

def test_padrao_le_o_schedule_json_legado_inteiro(tmp_path, monkeypatch):
    """Inclusive o `last_run_date`: ler só os campos de configuração e perder o claim faria
    o tick rodar de novo no dia da migração, com auto_send disparando o segundo envio."""
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("schedule.json", json.dumps({
        "enabled": True, "send_time": "07:30", "weekdays": [0, 1, 2],
        "auto_send": True, "until": None, "last_run_date": "2026-09-15",
        "set_by": "david@metakosmos.com.br", "set_at": "2026-06-30T10:00:00-03:00"}))
    s = orchestrator.get_schedule(campanha=CAMPANHA_PADRAO)
    assert s["send_time"] == "07:30"
    assert s["weekdays"] == [0, 1, 2]
    assert s["auto_send"] is True
    assert s["last_run_date"] == "2026-09-15"


def test_legado_nao_e_lido_por_campanha_nao_padrao(tmp_path, monkeypatch):
    """O vizinho da migração: o `schedule.json` legado é da padrão e de mais ninguém.

    Sem isto, uma campanha nova nasceria com `enabled: True` herdado do Daily Drops e
    mandaria e-mail sozinha no primeiro tick.
    """
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    orchestrator._sm().store.write("schedule.json", json.dumps(
        {"enabled": True, "auto_send": True, "send_time": "07:30"}))
    s = orchestrator.get_schedule(campanha="woow-beauty")
    assert s["enabled"] is False
    assert s["auto_send"] is False
    assert s["send_time"] == "10:00"


def test_o_claim_da_migracao_grava_o_documento_resolvido_inteiro(tmp_path, monkeypatch):
    """Não só o campo: o blob novo tem de nascer com a configuração do legado dentro.

    Gravar só `{"last_run_date": ...}` faria a leitura seguinte encontrar o arquivo novo,
    parar de cair no legado, e a agenda voltar aos defaults — `enabled: False`, e a
    newsletter simplesmente não sai mais.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("schedule.json", json.dumps(
        {"enabled": True, "send_time": "07:30", "weekdays": [0, 1, 2, 3, 4],
         "auto_send": True, "last_run_date": None}))
    orchestrator._mark_schedule_run(sm, "2026-09-15", CAMPANHA_PADRAO)
    novo = json.loads(sm.store.read(f"schedules/{CAMPANHA_PADRAO}.json"))
    assert novo["last_run_date"] == "2026-09-15"
    assert novo["enabled"] is True
    assert novo["send_time"] == "07:30"
    assert novo["auto_send"] is True
    # e a leitura seguinte já vem do caminho novo, com tudo preservado
    assert orchestrator.get_schedule(campanha=CAMPANHA_PADRAO)["send_time"] == "07:30"


def test_schedules_novo_tem_precedencia_sobre_o_legado(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    sm.store.write("schedule.json", json.dumps({"enabled": True, "send_time": "07:30"}))
    sm.store.write(f"schedules/{CAMPANHA_PADRAO}.json",
                   json.dumps({"enabled": True, "send_time": "11:00"}))
    assert orchestrator.get_schedule(campanha=CAMPANHA_PADRAO)["send_time"] == "11:00"


# ------------------------------------------------------------------ o tick

def _tick(monkeypatch, rodadas, instante=SEGUNDA_MEIO_DIA, erro_em=None):
    """Roda o cron_tick com run_daily instrumentado. `rodadas` recebe cada edição rodada."""
    _congela(monkeypatch, instante)

    def _fake_run_daily(edition, auto_send=False):
        rodadas.append(edition)
        if erro_em and erro_em in edition:
            raise RuntimeError(f"estourou em {edition}")
        return {"edition": edition, "auto_send": auto_send, "stage": "ready"}

    monkeypatch.setattr(orchestrator, "run_daily", _fake_run_daily)
    monkeypatch.setattr(StateManager, "sync_to_firebase", lambda self: {"synced": 0})
    return orchestrator.cron_tick()


def test_tick_roda_uma_campanha_e_lista_as_pendentes(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    for slug in ("woow-beauty", "woow-food"):
        _campanha(slug)
        _liga(slug)
    _liga(CAMPANHA_PADRAO)

    rodadas = []
    r = _tick(monkeypatch, rodadas)

    assert r["ran"] is True
    assert len(rodadas) == 1, f"o tick rodou mais de uma campanha: {rodadas}"
    assert r["campanha"] in (CAMPANHA_PADRAO, "woow-beauty", "woow-food")
    assert len(r["pendentes"]) == 2
    assert r["campanha"] not in r["pendentes"]


def test_tres_ticks_seguidos_rodam_as_tres_campanhas(tmp_path, monkeypatch):
    """O claim é por campanha. Com um `last_run_date` global, a primeira campanha do dia
    bloquearia as outras duas e a saída seria `ran: false, "já rodou hoje"` — indistinguível
    do funcionamento correto para quem lê o retorno."""
    _local_sm(tmp_path, monkeypatch)
    for slug in ("woow-beauty", "woow-food"):
        _campanha(slug)
        _liga(slug)
    _liga(CAMPANHA_PADRAO)

    rodadas = []
    for _ in range(3):
        _tick(monkeypatch, rodadas)
    assert sorted(rodadas) == sorted([
        "2026-09-15", "woow-beauty--2026-09-15", "woow-food--2026-09-15"])

    # e o quarto tick não roda nada: todas já claimaram o dia
    quarto = _tick(monkeypatch, rodadas)
    assert quarto["ran"] is False
    assert len(rodadas) == 3


def test_tick_monta_o_id_composto_da_campanha(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    _liga("woow-beauty")
    rodadas = []
    _tick(monkeypatch, rodadas)
    assert rodadas == ["woow-beauty--2026-09-15"]


def test_tick_grava_a_campanha_no_state_da_edicao(tmp_path, monkeypatch):
    """`run_daily` nunca gravou o campo `campanha`, e `create_campaign` é o único escritor.

    Sem isto TODA edição nascida do cron — de qualquer campanha — cai em
    `publicados/daily-drops.json` no rebuild, barrando a pauta da campanha errada.
    """
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    _liga("woow-beauty")
    _tick(monkeypatch, [])
    assert sm.get_state("woow-beauty--2026-09-15")["campanha"] == "woow-beauty"


def test_tick_escolhe_a_campanha_vencida_ha_mais_tempo(tmp_path, monkeypatch):
    sm = _local_sm(tmp_path, monkeypatch)
    for slug in ("woow-beauty", "woow-food"):
        _campanha(slug)
        _liga(slug)
    _liga(CAMPANHA_PADRAO)
    # a padrão rodou ontem, a beauty há uma semana, a food hoje ainda não rodou nunca
    orchestrator._mark_schedule_run(sm, "2026-09-14", CAMPANHA_PADRAO)
    orchestrator._mark_schedule_run(sm, "2026-09-08", "woow-beauty")

    rodadas = []
    _tick(monkeypatch, rodadas)
    # nunca rodou < rodou há uma semana < rodou ontem
    assert rodadas == ["woow-food--2026-09-15"]


def test_campanha_desligada_nao_entra_no_tick(tmp_path, monkeypatch):
    _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")  # nasce enabled: False
    _liga(CAMPANHA_PADRAO)
    rodadas = []
    r = _tick(monkeypatch, rodadas)
    assert rodadas == ["2026-09-15"]
    assert r["pendentes"] == []


def test_excecao_numa_campanha_nao_impede_a_seguinte(tmp_path, monkeypatch):
    """Cada campanha no seu try, no padrão do `_refresh_metrics`. Sem isso, uma falha na
    campanha 2 derruba silenciosamente as campanhas 3..N do mesmo tick."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    _liga("woow-beauty")
    _liga(CAMPANHA_PADRAO)
    # agenda ilegível na padrão: o tick não pode morrer por causa dela
    sm.store.write(f"schedules/{CAMPANHA_PADRAO}.json", "{ isto não é json")

    rodadas = []
    r = _tick(monkeypatch, rodadas)
    assert rodadas == ["woow-beauty--2026-09-15"]
    assert r["ran"] is True
    assert any(CAMPANHA_PADRAO in str(e) for e in r.get("erros", [])), \
        f"a falha da padrão não apareceu no retorno: {r}"


def test_erro_do_run_daily_nao_derruba_o_tick_e_a_campanha_fica_claimada(tmp_path,
                                                                        monkeypatch):
    """O claim vem ANTES do pipeline de propósito: dia já claimado, erro no health, sem 500
    e sem retry do Scheduler. Isso não muda com o laço por campanha."""
    sm = _local_sm(tmp_path, monkeypatch)
    _liga(CAMPANHA_PADRAO)
    r = _tick(monkeypatch, [], erro_em="2026-09-15")
    assert r["ran"] is True
    assert "error" in r
    assert orchestrator.get_schedule(sm, CAMPANHA_PADRAO)["last_run_date"] == "2026-09-15"


def test_tick_sem_nenhuma_campanha_agendada(tmp_path, monkeypatch):
    """O vizinho de tudo: com nada ligado, o tick continua respondendo o que respondia."""
    _local_sm(tmp_path, monkeypatch)
    r = _tick(monkeypatch, [])
    assert r["ran"] is False
    assert r["pendentes"] == []


def test_campanha_manual_html_nao_e_rodada_pelo_tick(tmp_path, monkeypatch):
    """`run_daily` não conhece `type`: uma edição manual_html rodada pelo cron cai em
    `_generate_manual_html` sem payload e morre em 'manual_html exige html e subject'."""
    sm = _local_sm(tmp_path, monkeypatch)
    _campanha("woow-beauty")
    _liga("woow-beauty")
    sm.upsert_edition("woow-beauty--2026-09-15", {"type": "manual_html",
                                                  "campanha": "woow-beauty"})
    rodadas = []
    r = _tick(monkeypatch, rodadas)
    assert rodadas == []
    assert r["ran"] is False


def test_should_run_now_continua_puro_e_intacto():
    """A função que decide não mudou: mudou quem a chama. Se esta assinatura mudar, os 6
    testes de test_schedule.py estão medindo outra coisa."""
    sched = {**orchestrator.SCHEDULE_DEFAULTS, "enabled": True}
    ok, motivo = orchestrator._should_run_now(sched, datetime(2026, 9, 15, 12, 0), "empty")
    assert ok is True and motivo == "ok"
