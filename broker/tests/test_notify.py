# broker/tests/test_notify.py — MAR-484: aviso de edição pronta ou falha, antes do envio.
import pathlib
import sys
from datetime import datetime, timezone

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
                             erro="Só 2 item(ns) com fonte confirmada, e o piso é 3.",
                             contexto={"nova_tentativa": False, "tentativa": 3})
    assert "NÃO foi gerada" in txt
    assert "piso é 3" in txt
    assert "não tenta de novo" in txt
    assert "run --edition 2026-09-03" in txt


def test_falha_vence_o_estagio():
    """Erro no send de uma edição que ficou 'ready' precisa avisar do erro, não da revisão."""
    txt = notify.monta_aviso("2026-09-03", PRONTA, erro="ZMA 6610")
    assert "NÃO foi enviada" in txt and "6610" in txt


def test_falha_no_envio_nao_diz_que_a_edicao_nao_foi_gerada():
    """De 22 a 25/10/2026 o aviso dizia "NÃO foi gerada" com a edição pronta e o preview no
    ar: só o envio tinha falhado. Quem lia ia procurar defeito na geração."""
    txt = notify.monta_aviso("2026-09-03", PRONTA, erro="sendcampaign recusado pelo ZMA")
    assert "NÃO foi gerada" not in txt
    assert "gerada, mas NÃO foi enviada" in txt


def test_falha_com_nova_tentativa_diz_ate_quando():
    txt = notify.monta_aviso("2026-09-03", {"stage": "researched"}, erro="Gemini 503",
                             contexto={"nova_tentativa": True, "tentativa": 1,
                                       "tentativas_max": 3, "prazo": "12:00"})
    assert "tenta de novo" in txt and "não tenta de novo" not in txt
    assert "12:00" in txt and "1 de 3" in txt


# ------------------------------------------------------- edição barrada pela revisão do ZMA
BARRADA = {"stage": "ready", "envio_zma": {
    "campaign_key": "K1", "campaign_name": "mK Newsletter 2026-10-26",
    "status": "barrado_revisao", "codigo": "6608",
    "significado": "os destinatários mudaram depois da revisão"}}


def test_aviso_de_revisao_marca_quem_libera_e_diz_ate_quando(monkeypatch):
    monkeypatch.setenv("ALERT_REVIEW_MENTION", "U04JLC4HPFT")
    txt = notify.monta_aviso("2026-10-26", BARRADA, evento="revisao",
                             contexto={"prazo": "12:00"})
    assert txt.startswith("<@U04JLC4HPFT>")
    assert "6608" in txt and "os destinatários mudaram depois da revisão" in txt
    assert "mK Newsletter 2026-10-26" in txt
    assert "12:00" in txt
    assert "não crie outra campanha" in txt.lower()


def test_sem_mencao_configurada_o_aviso_sai_sem_marcar_ninguem(monkeypatch):
    monkeypatch.delenv("ALERT_REVIEW_MENTION", raising=False)
    txt = notify.monta_aviso("2026-10-26", BARRADA, evento="revisao", contexto={"prazo": "12:00"})
    assert "<@" not in txt and "6608" in txt


def test_mencao_que_nao_e_id_do_slack_nao_entra_no_aviso(monkeypatch):
    """A variável vira texto do Slack: `<!channel>` ali chamaria o canal inteiro."""
    monkeypatch.setenv("ALERT_REVIEW_MENTION", "<!channel>")
    txt = notify.monta_aviso("2026-10-26", BARRADA, evento="revisao", contexto={"prazo": "12:00"})
    assert "channel" not in txt and "<@" not in txt


def test_aviso_de_que_saiu_traz_o_horario():
    estado = {"stage": "sent", "envio_zma": {**BARRADA["envio_zma"], "status": "enviada",
                                             "enviada_em": "2026-10-26T10:31:00-03:00"}}
    txt = notify.monta_aviso("2026-10-26", estado, evento="saiu")
    assert "saiu às 10:31" in txt


def test_aviso_de_que_saiu_com_horario_do_zma_mostra_o_que_veio():
    estado = {"stage": "sent", "envio_zma": {**BARRADA["envio_zma"], "status": "enviada",
                                             "enviada_em": "26 Oct 2026, 10:20 AM"}}
    assert "26 Oct 2026, 10:20 AM" in notify.monta_aviso("2026-10-26", estado, evento="saiu")


def test_aviso_de_prazo_diz_que_o_dia_foi_pulado():
    txt = notify.monta_aviso("2026-10-26", {**BARRADA, "envio_zma": {
        **BARRADA["envio_zma"], "status": "prazo_vencido"}}, evento="prazo",
        contexto={"prazo": "12:00"})
    assert "não saiu até 12:00" in txt
    assert "mK Newsletter 2026-10-26" in txt and "6608" in txt


def test_aviso_de_prazo_sem_conferencia_no_zma_diz_isso():
    txt = notify.monta_aviso("2026-10-26", BARRADA, evento="prazo",
                             contexto={"prazo": "12:00", "conferido": False})
    assert "conferir no painel" in txt


def test_aviso_de_revisao_diz_onde_clicar_e_qual_das_campanhas_liberar():
    """Rodar a edição de novo deixa duas campanhas com o mesmo nome no painel (24/10/2026
    teve duas). O horário de criação é o que o painel mostra ao lado do nome. Caminho e botão
    conferidos no painel em 25/10/2026."""
    estado = {"stage": "ready", "envio_zma": {**BARRADA["envio_zma"],
                                              "criada_em": "2026-10-26T09:01:12-03:00"}}
    txt = notify.monta_aviso("2026-10-26", estado, evento="revisao", contexto={"prazo": "12:00"})
    assert "Marketing Campaigns" in txt and "Review and launch" in txt
    assert "criada às 09:01" in txt


def test_aviso_de_revisao_aponta_a_campanha_descartada_por_conteudo_antigo():
    estado = {"stage": "ready", "envio_zma": {
        **BARRADA["envio_zma"], "criada_em": "2026-10-26T10:32:00-03:00",
        "descartadas": [{"campaign_key": "K0", "criada_em": "2026-10-26T09:01:12-03:00"}]}}
    txt = notify.monta_aviso("2026-10-26", estado, evento="revisao", contexto={"prazo": "12:00"})
    assert "criada às 10:32" in txt
    assert "09:01" in txt and "conteúdo antigo" in txt


def test_aviso_de_edicao_que_ja_tinha_saido():
    estado = {"stage": "sent", "envio_zma": {
        "campaign_key": "K9", "campaign_name": "mK Newsletter 2026-10-26", "status": "enviada",
        "enviada_em": "26 Oct 2026, 08:40 AM", "ja_tinha_saido": True}}
    txt = notify.monta_aviso("2026-10-26", estado, evento="ja_saiu")
    assert "já tinha saído" in txt and "mK Newsletter 2026-10-26" in txt
    assert "26 Oct 2026, 08:40 AM" in txt


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
class _Relogio(datetime):
    """Relógio do orchestrator congelado numa segunda ao meio-dia (BRT), dentro da agenda."""

    @classmethod
    def now(cls, tz=None):
        instante = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
        return instante.astimezone(tz) if tz else instante.replace(tzinfo=None)


def _tick(tmp_path, monkeypatch, estado_final, erro=None):
    """Roda o cron_tick de verdade, com agenda por campanha, só o pipeline e o Slack falsos."""
    import orchestrator
    from state_manager import CAMPANHA_PADRAO, StateManager, LocalStore
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "datetime", _Relogio)
    monkeypatch.setattr(StateManager, "sync_to_firebase", lambda self: {"synced": 0})
    orchestrator.set_schedule({"campanha": CAMPANHA_PADRAO, "enabled": True, "auto_send": False,
                               "_email": "david@metakosmos.com.br"})

    def _run_daily(ed, auto):
        sm.upsert_edition(ed, estado_final)
        if erro:
            raise RuntimeError(erro)
        return {"edition": ed, "stage": estado_final["stage"]}
    monkeypatch.setattr(orchestrator, "run_daily", _run_daily)
    recebidos = []
    monkeypatch.setattr(orchestrator.notify, "avisa",
                        lambda st, ed, err=None, **kw: recebidos.append((ed, st.get("stage"), err))
                        or "enviado")
    return orchestrator.cron_tick(), recebidos


def test_tick_avisa_quando_a_edicao_fica_pronta(tmp_path, monkeypatch):
    out, recebidos = _tick(tmp_path, monkeypatch, {"stage": "ready"})
    assert out["aviso"] == "enviado"
    assert out["ran"] is True
    assert [(st, err) for _ed, st, err in recebidos] == [("ready", None)]


def test_tick_avisa_com_o_erro_quando_o_estagio_falha(tmp_path, monkeypatch):
    """O dia já foi claimado e não há nova tentativa: sem este aviso, o único sinal de que
    a edição não saiu é a ausência do e-mail."""
    out, recebidos = _tick(tmp_path, monkeypatch, {"stage": "researched"},
                           erro="generate_content.py falhou: piso é 3")
    assert "piso é 3" in recebidos[0][2]
    assert out["aviso"] == "enviado"


def test_tick_que_nao_roda_nao_avisa(tmp_path, monkeypatch):
    """Controle positivo: agendamento desligado não pode gerar aviso diário."""
    import orchestrator
    from state_manager import StateManager, LocalStore
    sm = StateManager(LocalStore(tmp_path))
    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "datetime", _Relogio)
    # nenhuma agenda ligada: a padrão nasce desligada
    def _nao(*a, **k):
        raise AssertionError("avisou sem ter rodado")
    monkeypatch.setattr(orchestrator.notify, "avisa", _nao)
    assert orchestrator.cron_tick()["ran"] is False


@pytest.mark.parametrize("estado,erro,evento", [
    (PRONTA, None, None), ({"stage": "researched"}, "falhou", None), (PRONTA, "falhou", None),
    (BARRADA, None, "revisao"), (BARRADA, None, "saiu"), (BARRADA, None, "prazo"),
    (BARRADA, None, "ja_saiu")])
def test_aviso_nao_usa_travessao_longo(estado, erro, evento):
    """A regra editorial da mK proíbe travessão longo, e o próprio prompt da newsletter
    abre com "ZERO TRAVESSÃO LONGO". O aviso é texto da mK como qualquer outro."""
    txt = notify.monta_aviso("2026-09-03", estado, erro, evento=evento,
                             contexto={"prazo": "12:00", "nova_tentativa": True,
                                       "tentativa": 1, "tentativas_max": 3})
    assert "—" not in txt and "–" not in txt
