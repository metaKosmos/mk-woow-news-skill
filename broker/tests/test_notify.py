# broker/tests/test_notify.py — MAR-484: aviso de edição pronta ou falha, antes do envio.
import pathlib
import sys

import pytest

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))

import notify  # noqa: E402

PRONTA = {
    "stage": "ready", "subject": "Kohl's converte mais com busca por IA",
    "preview_url": "https://storage.googleapis.com/mk-woow-news-public/nl/2026-09-03.html",
    "provenance": {"publicados": 4, "descartados": [
        {"campo": "sinal_2", "motivo": "source_id_fora_do_pool", "headline": "H"}]},
    "link_check": {"suspeitos": [{"campo": "manchete", "motivo": "raiz_de_dominio"}]},
}


def test_edicao_pronta_traz_o_que_o_revisor_precisa():
    txt = notify.monta_aviso("2026-09-03", PRONTA)
    assert "pronta para revisão" in txt
    assert PRONTA["subject"] in txt
    assert PRONTA["preview_url"] in txt
    assert "4 itens" in txt
    assert "1 descartado(s): source_id_fora_do_pool" in txt
    assert "1 link(s) suspeito(s)" in txt
    assert "--stage send" in txt


def test_edicao_enviada_nao_avisa():
    """O e-mail já chegou a quem seria avisado. Avisar depois é ruído."""
    assert notify.monta_aviso("2026-09-03", {**PRONTA, "stage": "sent"}) is None


@pytest.mark.parametrize("stage", ["empty", "researched", "generated"])
def test_estagio_intermediario_nao_avisa(stage):
    assert notify.monta_aviso("2026-09-03", {"stage": stage}) is None


def test_falha_avisa_com_o_erro_e_o_comando_de_recuperacao():
    txt = notify.monta_aviso("2026-09-03", {"stage": "researched"},
                             erro="Só 2 item(ns) com fonte confirmada, e o piso é 3.")
    assert "NÃO foi gerada" in txt
    assert "piso é 3" in txt
    assert "não tenta de novo" in txt
    assert "run --edition 2026-09-03" in txt


def test_falha_vence_o_estagio():
    """Erro no send de uma edição que ficou 'ready' precisa avisar do erro, não da revisão."""
    txt = notify.monta_aviso("2026-09-03", PRONTA, erro="ZMA 6610")
    assert "NÃO foi gerada" in txt and "6610" in txt


def test_edicao_pronta_sem_procedencia_nao_quebra():
    """Edição legada ou manual_html não tem provenance nenhum."""
    txt = notify.monta_aviso("2026-09-03", {"stage": "ready", "preview_url": "https://x"})
    assert "pronta para revisão" in txt and "https://x" in txt


def test_sem_webhook_nao_envia_e_nao_levanta(monkeypatch):
    monkeypatch.setattr(notify.secrets_store, "get_slack_webhook", lambda: "")
    enviou = []
    monkeypatch.setattr(notify, "envia", lambda u, t: enviou.append(t))
    assert notify.avisa(PRONTA, "2026-09-03") == "sem_webhook"
    assert enviou == []


def test_slack_fora_do_ar_nao_derruba_a_edicao(monkeypatch):
    """Controle do requisito central: aviso é observabilidade, não parte da entrega."""
    monkeypatch.setattr(notify.secrets_store, "get_slack_webhook", lambda: "https://hooks/x")
    def _boom(url, texto):
        raise OSError("connection refused")
    monkeypatch.setattr(notify, "envia", _boom)
    r = notify.avisa(PRONTA, "2026-09-03")
    assert r.startswith("falhou: OSError")


def test_caminho_feliz_envia_uma_vez(monkeypatch):
    monkeypatch.setattr(notify.secrets_store, "get_slack_webhook", lambda: "https://hooks/x")
    enviados = []
    monkeypatch.setattr(notify, "envia", lambda u, t: enviados.append((u, t)) or True)
    assert notify.avisa(PRONTA, "2026-09-03") == "enviado"
    assert len(enviados) == 1
    assert "pronta para revisão" in enviados[0][1]


def test_edicao_enviada_nem_chega_a_ler_o_segredo(monkeypatch):
    """Ler o Secret Manager custa chamada de rede; sem nada a avisar, nem tenta."""
    def _nao_deveria():
        raise AssertionError("leu o segredo sem ter o que avisar")
    monkeypatch.setattr(notify.secrets_store, "get_slack_webhook", _nao_deveria)
    assert notify.avisa({**PRONTA, "stage": "sent"}, "2026-09-03") == "nada_a_avisar"


# ------------------------------------------------------- integração com o tick diário
def _tick(tmp_path, monkeypatch, sched, estado_final, erro=None):
    import orchestrator
    from state_manager import StateManager, LocalStore
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-09-03", estado_final)
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "get_schedule", lambda s=None: sched)
    monkeypatch.setattr(orchestrator, "_mark_schedule_run", lambda s, d: None)
    monkeypatch.setattr(orchestrator, "_resolve_edition_date", lambda e: "2026-09-03")
    monkeypatch.setattr(orchestrator, "_should_run_now", lambda *a: (True, "ok"))
    monkeypatch.setattr(sm, "sync_to_firebase", lambda: {})
    def _run_daily(ed, auto):
        if erro:
            raise RuntimeError(erro)
        return {"edition": ed, "stage": estado_final["stage"]}
    monkeypatch.setattr(orchestrator, "run_daily", _run_daily)
    recebidos = []
    monkeypatch.setattr(orchestrator.notify, "avisa",
                        lambda st, ed, err=None: recebidos.append((ed, st.get("stage"), err)) or "enviado")
    return orchestrator.cron_tick(), recebidos


SCHED = {"enabled": True, "send_time": "07:00", "weekdays": [0, 1, 2, 3, 4, 5, 6],
         "auto_send": False, "until": None, "last_run_date": None}


def test_tick_avisa_quando_a_edicao_fica_pronta(tmp_path, monkeypatch):
    out, recebidos = _tick(tmp_path, monkeypatch, SCHED, {"stage": "ready"})
    assert out["aviso"] == "enviado"
    assert recebidos == [("2026-09-03", "ready", None)]


def test_tick_avisa_com_o_erro_quando_o_estagio_falha(tmp_path, monkeypatch):
    """O dia já foi claimado e não há nova tentativa: sem este aviso, o único sinal de que
    a edição não saiu é a ausência do e-mail."""
    out, recebidos = _tick(tmp_path, monkeypatch, SCHED, {"stage": "researched"},
                           erro="generate_content.py falhou: piso é 3")
    assert "piso é 3" in recebidos[0][2]
    assert out["aviso"] == "enviado"


def test_tick_que_nao_roda_nao_avisa(tmp_path, monkeypatch):
    """Controle positivo: agendamento desligado não pode gerar aviso diário."""
    import orchestrator
    from state_manager import StateManager, LocalStore
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "get_schedule", lambda s=None: {**SCHED, "enabled": False})
    def _nao(*a, **k):
        raise AssertionError("avisou sem ter rodado")
    monkeypatch.setattr(orchestrator.notify, "avisa", _nao)
    assert orchestrator.cron_tick()["ran"] is False
