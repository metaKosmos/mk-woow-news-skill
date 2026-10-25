# broker/tests/test_envio_revisao.py — edição barrada pela revisão do ZMA.
#
# Desde 17/10/2026 o ZMA revisa as campanhas, e o envio pela API volta 6608. A regra decidida
# pelo David em 25/10: o aviso no #woow-news-alerts marca quem libera a revisão no painel, e
# o broker tenta de novo a cada tick até o prazo (12:00 para o envio das 09:00). Depois do
# prazo, o dia é pulado.
#
# Só o send_zma.py (rede) e o Slack são falsos aqui. Estado, agenda, tick e texto do aviso
# são os de verdade.
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

import pytest

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))

import notify  # noqa: E402
import orchestrator  # noqa: E402
from state_manager import CAMPANHA_PADRAO, LocalStore, StateManager  # noqa: E402

ED = "2026-10-26"  # segunda-feira
NOME = "mK Newsletter 2026-10-26"
PATRICK = "U04JLC4HPFT"

BARRADO = (3, {"tipo": "revisao", "campaign_key": "K1", "campaign_name": NOME, "code": "6608",
               "significado": "os destinatários mudaram depois da revisão"},
           "sendcampaign barrado pela revisão do ZMA (code 6608: os destinatários mudaram "
           "depois da revisão, campaignKey K1): liberar a revisão desta campanha no painel do ZMA.")
ENVIADA = (0, {"tipo": "enviada", "campaign_key": "K1", "campaign_name": NOME, "code": "200",
               "significado": ""}, "")
JA_SAIU_OUTRA_CHAVE = (0, {"tipo": "ja_enviada", "campaign_key": "K2", "campaign_name": NOME,
                           "enviada_em": "26 Oct 2026, 10:20 AM"}, "")
CHECK_PENDENTE = (0, {"tipo": "pendente", "campaign_key": "K1", "campaign_name": NOME}, "")
CHECK_SAIU = (0, {"tipo": "ja_enviada", "campaign_key": "K1", "campaign_name": NOME,
                  "enviada_em": "26 Oct 2026, 11:52 AM"}, "")


class _Relogio(datetime):
    instante = None

    @classmethod
    def now(cls, tz=None):
        return cls.instante.astimezone(tz) if tz else cls.instante.replace(tzinfo=None)


def _brt(hh, mm):
    return datetime(2026, 10, 26, hh + 3, mm, tzinfo=timezone.utc)


class _Ambiente:
    def __init__(self, tmp_path, monkeypatch):
        self.sm = StateManager(LocalStore(tmp_path / "estado"))
        self.raiz = tmp_path
        self.n = 0
        self.envios = []      # args de cada chamada ao send_zma.py
        self.respostas = []   # (código de saída, resultado ou None, stderr)
        self.slack = []
        monkeypatch.setattr(orchestrator, "_sm", lambda: self.sm)
        monkeypatch.setattr(orchestrator, "datetime", _Relogio)
        monkeypatch.setattr(orchestrator, "_restore_content", lambda *a, **k: None)
        monkeypatch.setattr(orchestrator, "_workdir", lambda ed: self._wd())
        monkeypatch.setattr(orchestrator, "_envmk_workdir", self._wd)
        monkeypatch.setattr(orchestrator, "_run_script_proc", self._proc)
        monkeypatch.setattr(StateManager, "sync_to_firebase", lambda s: {"synced": 0})
        monkeypatch.setattr(notify.secrets_store, "get_slack_webhook", lambda: "https://hooks/x")
        monkeypatch.setattr(notify, "envia", lambda url, texto: self.slack.append(texto) or True)
        monkeypatch.setenv("ALERT_REVIEW_MENTION", PATRICK)

    def _wd(self):
        self.n += 1
        d = self.raiz / f"wd{self.n}"
        d.mkdir(parents=True)
        return d

    def _proc(self, wd, script, args):
        assert script == "send_zma.py"
        self.envios.append(list(args))
        rc, res, err = self.respostas.pop(0)
        out = "OK access_token obtido\n" + (json.dumps(res, ensure_ascii=False) if res else "")
        return subprocess.CompletedProcess(args, rc, out, err)

    def pronta(self, **extra):
        self.sm.upsert_edition(ED, {"stage": "ready", "subject": "Drops do dia", "date": ED,
                                    "preview_url": "https://pub/2026-10-26.html", **extra})

    def agenda(self, **over):
        orchestrator.set_schedule({"campanha": CAMPANHA_PADRAO, "enabled": True,
                                   "auto_send": True, "send_time": "09:00",
                                   "_email": "david@metakosmos.com.br", **over})

    def tick(self, hh, mm):
        _Relogio.instante = _brt(hh, mm)
        return orchestrator.cron_tick()

    def envio(self):
        return self.sm.get_state(ED).get("envio_zma") or {}


@pytest.fixture
def amb(tmp_path, monkeypatch):
    a = _Ambiente(tmp_path, monkeypatch)
    _Relogio.instante = _brt(9, 0)
    return a


def _tem_par(args, flag, valor):
    return any(args[i] == flag and args[i + 1] == valor for i in range(len(args) - 1))


# ------------------------------------------------------------------ estágio send

def test_recusa_da_revisao_guarda_a_campanha_e_nao_marca_enviada(amb):
    amb.pronta()
    amb.respostas = [BARRADO]
    with pytest.raises(orchestrator.EnvioBarradoPelaRevisao):
        orchestrator.run_stage(ED, "send", {})
    st = amb.sm.get_state(ED)
    assert st["stage"] == "ready"
    assert st["envio_zma"]["status"] == "barrado_revisao"
    assert st["envio_zma"]["campaign_key"] == "K1"
    assert st["envio_zma"]["codigo"] == "6608"
    assert "--campaign-key" not in amb.envios[0], "a primeira tentativa cria a campanha"


def test_tentativa_seguinte_reenvia_a_mesma_campanha(amb):
    amb.pronta()
    amb.respostas = [BARRADO, ENVIADA]
    with pytest.raises(orchestrator.EnvioBarradoPelaRevisao):
        orchestrator.run_stage(ED, "send", {})
    out = orchestrator.run_stage(ED, "send", {})
    assert _tem_par(amb.envios[1], "--campaign-key", "K1")
    assert out["stage"] == "sent"
    st = amb.sm.get_state(ED)
    assert st["stage"] == "sent" and st["campaign_key"] == "K1"
    assert st["envio_zma"]["status"] == "enviada"
    assert "last_error" not in (st.get("health") or {})


def test_edicao_que_saiu_por_outra_campanha_fica_com_a_chave_dela(amb):
    """As métricas leem `campaign_key`: precisa ser a da campanha que de fato saiu."""
    amb.pronta()
    amb.respostas = [BARRADO, JA_SAIU_OUTRA_CHAVE]
    with pytest.raises(orchestrator.EnvioBarradoPelaRevisao):
        orchestrator.run_stage(ED, "send", {})
    orchestrator.run_stage(ED, "send", {})
    st = amb.sm.get_state(ED)
    assert st["stage"] == "sent" and st["campaign_key"] == "K2"


def test_recusa_comum_segue_falha_comum_mas_guarda_a_chave_criada(amb):
    amb.pronta()
    amb.respostas = [(1, {"tipo": "erro", "campaign_key": "K1", "campaign_name": NOME,
                          "code": "6601", "significado": "sem permissão para esta ação"},
                      "sendcampaign recusado pelo ZMA (code 6601: sem permissão para esta ação)")]
    with pytest.raises(RuntimeError) as exc:
        orchestrator.run_stage(ED, "send", {})
    assert not isinstance(exc.value, orchestrator.EnvioBarradoPelaRevisao)
    st = amb.sm.get_state(ED)
    assert st["envio_zma"]["campaign_key"] == "K1", "a tentativa seguinte criaria outra"
    assert "6601" in st["health"]["last_error"]["message"]


def test_recusa_qualquer_durante_a_revisao_mantem_a_edicao_barrada(amb):
    """Com a revisão aberta no painel, o ZMA pode responder outro código (6605, status
    mudou). A edição segue barrada e o prazo continua valendo."""
    amb.pronta()
    amb.respostas = [BARRADO, (1, {"tipo": "erro", "campaign_key": "K1", "campaign_name": NOME,
                                   "code": "6605", "significado": "o status da campanha mudou"},
                               "sendcampaign recusado pelo ZMA (code 6605)")]
    with pytest.raises(orchestrator.EnvioBarradoPelaRevisao):
        orchestrator.run_stage(ED, "send", {})
    with pytest.raises(orchestrator.EnvioBarradoPelaRevisao):
        orchestrator.run_stage(ED, "send", {})
    envio = amb.envio()
    assert envio["status"] == "barrado_revisao" and envio["codigo"] == "6605"
    assert envio["tentativas_envio"] == 2


def test_falha_sem_resultado_legivel_segue_falha_comum(amb):
    amb.pronta()
    amb.respostas = [(1, None, "Falha ao obter access_token: invalid_code")]
    with pytest.raises(RuntimeError) as exc:
        orchestrator.run_stage(ED, "send", {})
    assert not isinstance(exc.value, orchestrator.EnvioBarradoPelaRevisao)
    assert "invalid_code" in str(exc.value)
    assert not amb.envio()


# ------------------------------------------------------------------ o tick

def test_barrada_marca_quem_libera_uma_vez_e_diz_ate_quando(amb):
    amb.agenda()
    amb.pronta()
    amb.respostas = [BARRADO, BARRADO]
    amb.tick(9, 0)
    assert len(amb.slack) == 1
    msg = amb.slack[0]
    assert f"<@{PATRICK}>" in msg
    assert "6608" in msg and "os destinatários mudaram depois da revisão" in msg
    assert NOME in msg and "12:00" in msg
    amb.tick(9, 15)
    assert _tem_par(amb.envios[1], "--campaign-key", "K1")
    assert len(amb.slack) == 1, "a nova tentativa barrada não pode repetir a menção"


def test_liberada_no_painel_vira_enviada_e_avisa_que_saiu(amb):
    amb.agenda()
    amb.pronta()
    amb.respostas = [BARRADO, JA_SAIU_OUTRA_CHAVE]
    amb.tick(9, 0)
    amb.tick(10, 30)
    assert amb.sm.get_state(ED)["stage"] == "sent"
    assert len(amb.slack) == 2 and "saiu" in amb.slack[1]
    amb.tick(10, 45)
    assert len(amb.envios) == 2 and len(amb.slack) == 2, "edição enviada voltou a ser tentada"


def test_para_de_tentar_no_prazo_e_avisa_uma_vez(amb):
    amb.agenda()
    amb.pronta()
    amb.respostas = [BARRADO, BARRADO, CHECK_PENDENTE]
    amb.tick(9, 0)
    amb.tick(11, 45)
    assert len(amb.envios) == 2
    amb.tick(12, 0)
    assert "--check" in amb.envios[2], "no prazo o broker só confere, não envia"
    assert amb.envio()["status"] == "prazo_vencido"
    assert "não saiu até 12:00" in amb.slack[-1]
    total = len(amb.slack)
    amb.tick(12, 15)
    assert len(amb.envios) == 3 and len(amb.slack) == total


def test_no_prazo_confere_no_zma_antes_de_dizer_que_nao_saiu(amb):
    """Liberada e enviada pelo painel entre o último tick e o prazo: não pode virar
    "não saiu"."""
    amb.agenda()
    amb.pronta()
    amb.respostas = [BARRADO, CHECK_SAIU]
    amb.tick(9, 0)
    amb.tick(12, 0)
    assert amb.sm.get_state(ED)["stage"] == "sent"
    assert "saiu" in amb.slack[-1] and "não saiu" not in amb.slack[-1]


def test_tentativa_em_andamento_nao_ganha_outra_em_paralelo(amb):
    """O tick seguinte chega com a tentativa anterior ainda rodando: reenviar ali é corrida
    contra o próprio broker."""
    amb.agenda()
    amb.pronta(envio_zma={"campaign_key": "K1", "campaign_name": NOME,
                          "status": "barrado_revisao", "codigo": "6608",
                          "ultima_tentativa_em": "2026-10-26T09:00:05-03:00",
                          "avisos": ["revisao"]})
    orchestrator._mark_schedule_run(amb.sm, ED, CAMPANHA_PADRAO, _brt(9, 15))
    r = amb.tick(9, 30)
    assert amb.envios == [] and r["ran"] is False


def test_falha_comum_tenta_de_novo_ate_tres_vezes(amb, monkeypatch):
    """PROD-3167: instabilidade curta não mata mais o dia; o teto é 3 por dia."""
    amb.agenda()
    rodadas = []

    def _run_daily(edition, auto_send=False):
        rodadas.append(edition)
        orchestrator._record_stage_error(amb.sm, edition, "generate", RuntimeError("Gemini 503"))
        raise RuntimeError("generate_content.py falhou: Gemini 503")

    monkeypatch.setattr(orchestrator, "run_daily", _run_daily)
    for hh, mm in ((9, 0), (9, 15), (9, 30), (9, 45)):
        amb.tick(hh, mm)
    assert rodadas == [ED, ED, ED]
    assert len(amb.slack) == 3
    for msg in amb.slack[:2]:
        assert "tenta de novo" in msg and "não tenta de novo" not in msg
    assert "não tenta de novo" in amb.slack[2]
