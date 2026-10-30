# broker/tests/test_send_revisao.py — o send_zma diante da revisão do ZMA.
#
# Desde 17/10/2026 a conta do ZMA passou a revisar as campanhas, e o `sendcampaign` da API
# volta 6608 ("Recipient changed after review"). O broker tenta de novo até o prazo, e isso
# só é seguro com três garantias que este arquivo prende:
#   1. a nova tentativa reenvia a MESMA campanha, nunca cria outra;
#   2. campanha que já saiu (pelo painel, inclusive como cópia) não é enviada de novo;
#   3. a recusa da revisão sai com código próprio e resultado legível pela máquina.
#
# Formato do `recentcampaigns` lido ao vivo em 25/10/2026 (a página da ZMA v1 devolve 404):
# lista sob `recent_campaigns`, data em texto ("23 Oct 2026, 10:44 AM"), `sent_date_string`
# só na campanha enviada. A recusa do `sendcampaign` é a resposta crua de 24/10/2026, copiada
# do #woow-news-alerts.
#
# Desde 30/10/2026 o disparo é o `reviewandlaunch` da v2.1 (PUT com o campaign_id numérico), e
# o sucesso dele é status_code 200 com a campanha em ContentToBeReviewed (ou adiante). As
# recusas no formato da v1 continuam aqui porque o classificador ainda as lê.
import json
import pathlib
import sys

import pytest

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER / "pipeline"))
import send_zma  # noqa: E402

RECUSA_6608 = {"code": "6608", "campaign_status": "Draft",
               "message": "zc.api.campaign.contactcount.changed", "uri": "/sendcampaign",
               "version": "1"}
# Resposta real do reviewandlaunch em 30/10/2026 (campanha de teste para a lista de 1 contato).
SUCESSO = {"status_message": "Success", "status_code": 200,
           "campaign_details": {"updated_time_ms": 1793402040000,
                                "name": "ZZ TESTE reviewandlaunch David 2026-10-30",
                                "id": "1487553000017736875",
                                "updated_date": "2026-10-30T13:14:00-03:00",
                                "status": "ContentToBeReviewed"}}
SUCESSO_V1 = {"code": "200", "message": "", "campaign_status": "inprogress",
              "uri": "/api/v1/sendcampaign", "version": "1"}
NOME = "mK Newsletter 2026-10-26"


def _campanha(key, nome, status, enviada="", assunto="Drops do dia"):
    c = {"campaign_key": key, "campaign_name": nome, "campaign_status": status,
         "campaignId": f"ID-{key}", "campaigntype": "normal", "is_hybrid": "false",
         "created_date_string": "26 Oct 2026, 09:01 AM", "created_time": "1793016060000",
         "from_email": "news@metakosmos.com.br", "reply_to": "news@metakosmos.com.br",
         "subject": assunto, "zuid": "892598387", "folder_id": ""}
    if enviada:
        c["sent_date_string"] = enviada
    return c


class _Zma:
    """ZMA de mentira no nível do HTTP: o que o send_zma decide fica real."""

    def __init__(self, recentes=(), envio=SUCESSO, recentes_falha=False, envio_falha=None,
                 ao_enviar=None):
        self.recentes = list(recentes)
        self.envio = envio
        self.recentes_falha = recentes_falha
        self.envio_falha = envio_falha
        self.ao_enviar = ao_enviar
        self.chamadas = []

    def get(self, url, headers=None):
        op = url.split("?")[0].rsplit("/", 1)[-1]
        self.chamadas.append(op)
        if op == "recentcampaigns":
            if self.recentes_falha:
                raise OSError("ZMA fora do ar")
            return {"recent_campaigns": self.recentes, "code": "0", "status": "success"}
        raise AssertionError(f"GET inesperado: {url}")

    def post_form(self, url, data, headers=None):
        op = url.rsplit("/", 1)[-1]
        self.chamadas.append((op, data.get("campaignkey")))
        if op == "createCampaign":
            return {"message": "Campaign created successfully", "campaignKey": "NOVA",
                    "campaign_id": "ID-NOVA", "code": "200", "uri": "/api/v1/createCampaign"}
        raise AssertionError(f"POST inesperado: {url}")

    def put(self, url, headers=None):
        assert url.startswith(send_zma.ZMA_V21_BASE + "/campaigns/"), url
        caminho = url.split("?")[0].split("/")
        self.chamadas.append((caminho[-1], caminho[-2]))
        if caminho[-1] == "reviewandlaunch":
            if self.ao_enviar:
                self.ao_enviar()
            if self.envio_falha:
                raise self.envio_falha
            return self.envio
        raise AssertionError(f"PUT inesperado: {url}")


def _roda(monkeypatch, capsys, tmp_path, zma, *extra):
    md = tmp_path / "2026-10-26.md"
    md.write_text("---\nsubject: Drops do dia\nlist_name: Time mK\n---\n", encoding="utf-8")
    monkeypatch.setattr(send_zma, "load_env", lambda: {})
    monkeypatch.setattr(send_zma, "get_access_token", lambda env: "T")
    monkeypatch.setattr(send_zma, "get", zma.get)
    monkeypatch.setattr(send_zma, "post_form", zma.post_form)
    monkeypatch.setattr(send_zma, "put", zma.put)
    argv = ["send_zma.py"]
    if "--check" not in extra:
        argv += [str(md), "--content-url", "https://pub/x.html", "--send", "--list-key", "LK"]
    monkeypatch.setattr(sys, "argv", argv + list(extra))
    codigo = 0
    try:
        send_zma.main()
    except SystemExit as e:
        codigo = e.code
    out, err = capsys.readouterr()
    linhas = [l for l in out.splitlines() if l.strip().startswith("{")]
    return codigo, (json.loads(linhas[-1]) if linhas else None), err


def test_tentativa_seguinte_reenvia_a_mesma_campanha_sem_criar_outra(monkeypatch, capsys, tmp_path):
    zma = _Zma(recentes=[_campanha("K1", NOME, "Draft")])
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0
    assert ("reviewandlaunch", "ID-K1") in zma.chamadas
    assert not any(c[0] == "createCampaign" for c in zma.chamadas if isinstance(c, tuple))
    assert res["tipo"] == "enviada" and res["campaign_key"] == "K1"


def test_mesma_edicao_ja_enviada_com_outra_chave_impede_o_reenvio(monkeypatch, capsys, tmp_path):
    """Rodar a edição de novo cria outra campanha com o mesmo nome (24/10 teve duas). Se uma
    delas já saiu, mandar a outra é a lista inteira recebendo duas vezes."""
    zma = _Zma(recentes=[_campanha("K1", NOME, "Draft"),
                         _campanha("K2", NOME, "Sent", "26 Oct 2026, 10:20 AM")])
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0
    assert not any(isinstance(c, tuple) and c[0] == "reviewandlaunch" for c in zma.chamadas)
    assert res["tipo"] == "ja_enviada"
    assert res["campaign_key"] == "K2"
    assert res["enviada_em"] == "26 Oct 2026, 10:20 AM"


def test_teste_enviado_ao_time_com_o_mesmo_assunto_nao_barra_a_edicao(monkeypatch, capsys,
                                                                     tmp_path):
    """Em 24/10 houve uma campanha "TESTE 6608 Daily Drops 2026-10-24 (time)" com o assunto
    da edição. Se um teste assim tivesse saído, casar por assunto ou por nome contido daria a
    edição por enviada e a lista real ficaria sem ela."""
    teste = _campanha("K9", "TESTE " + NOME + " (time)", "Sent", "26 Oct 2026, 09:30 AM")
    zma = _Zma(recentes=[_campanha("K1", NOME, "Draft"), teste])
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0
    assert ("reviewandlaunch", "ID-K1") in zma.chamadas
    assert res["tipo"] == "enviada" and res["campaign_key"] == "K1"


def test_rascunho_de_outra_edicao_nao_conta_como_enviada(monkeypatch, capsys, tmp_path):
    """Controle do teste acima: o que impede o reenvio é ter SAÍDO, não ter o mesmo nome."""
    zma = _Zma(recentes=[_campanha("K0", "mK Newsletter 2026-10-25", "Sent"),
                         _campanha("K1", NOME, "Draft")])
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0 and res["tipo"] == "enviada" and res["campaign_key"] == "K1"


def test_recusa_da_revisao_sai_com_codigo_proprio_e_resultado_legivel(monkeypatch, capsys, tmp_path):
    zma = _Zma(recentes=[_campanha("K1", NOME, "Draft")], envio=RECUSA_6608)
    codigo, res, err = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == send_zma.EXIT_BARRADO_REVISAO
    assert res["tipo"] == "revisao"
    assert res["code"] == "6608" and res["campaign_key"] == "K1"
    assert res["campaign_name"] == NOME
    assert "os destinatários mudaram depois da revisão" in err


def test_primeiro_envio_cria_a_campanha_e_devolve_a_chave(monkeypatch, capsys, tmp_path):
    zma = _Zma()
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma)
    assert codigo == 0
    criadas = [c for c in zma.chamadas if isinstance(c, tuple) and c[0] == "createCampaign"]
    assert len(criadas) == 1
    assert ("reviewandlaunch", "ID-NOVA") in zma.chamadas
    assert res == {**res, "tipo": "enviada", "campaign_key": "NOVA", "campaign_id": "ID-NOVA",
                   "campaign_name": NOME, "status_zma": "contenttobereviewed"}


def test_primeira_recusa_ja_devolve_a_chave_criada(monkeypatch, capsys, tmp_path):
    """Sem a chave no resultado, a tentativa seguinte criaria uma segunda campanha."""
    zma = _Zma(envio=RECUSA_6608)
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma)
    assert codigo == send_zma.EXIT_BARRADO_REVISAO
    assert res["campaign_key"] == "NOVA"


def test_chave_criada_sai_no_stdout_antes_do_envio(monkeypatch, capsys, tmp_path):
    """Processo morto durante o sendcampaign (o teto de 600 s do subprocess, instância
    reciclada) não chega ao resultado final. Sem a chave já impressa, a tentativa seguinte
    criaria uma segunda campanha com o mesmo nome."""
    antes_do_envio = []
    zma = _Zma(ao_enviar=lambda: antes_do_envio.append(capsys.readouterr().out))
    _roda(monkeypatch, capsys, tmp_path, zma)
    linhas = [json.loads(l) for l in antes_do_envio[0].splitlines() if l.strip().startswith("{")]
    assert linhas and linhas[-1]["campaign_key"] == "NOVA"
    assert linhas[-1]["campaign_id"] == "ID-NOVA"


def test_envio_que_cai_depois_de_criar_devolve_a_chave(monkeypatch, capsys, tmp_path):
    zma = _Zma(envio_falha=OSError("conexão recusada"))
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma)
    assert codigo not in (0, None, send_zma.EXIT_BARRADO_REVISAO)
    assert "conexão recusada" in str(codigo)
    assert res["tipo"] == "erro" and res["campaign_key"] == "NOVA"


def test_chamadas_ao_zma_tem_teto_de_tempo(monkeypatch):
    """Sem teto, uma chamada travada segura o tick até os 600 s do subprocess, inclusive a
    conferência do prazo, que roda antes de escolher a campanha do tick."""
    tetos = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def _urlopen(req, timeout=None):
        tetos.append(timeout)
        return _Resp()

    monkeypatch.setattr(send_zma.urllib.request, "urlopen", _urlopen)
    send_zma.get("https://zma/recentcampaigns")
    send_zma.post_form("https://zma/createCampaign", {"campaignname": "x"})
    send_zma.put("https://zma/campaigns/1/reviewandlaunch")
    assert len(tetos) == 3 and all(tetos)


def test_recusa_que_nao_e_da_revisao_continua_sendo_falha_comum(monkeypatch, capsys, tmp_path):
    zma = _Zma(envio={"code": "6601", "message": "Insufficient privileges"})
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma)
    assert codigo not in (0, send_zma.EXIT_BARRADO_REVISAO)
    assert "6601" in str(codigo)
    assert res["tipo"] == "erro" and res["code"] == "6601"


def test_conferencia_fora_do_ar_nao_impede_o_envio(monkeypatch, capsys, tmp_path):
    zma = _Zma(recentes_falha=True)
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma,
                           "--campaign-key", "K1", "--campaign-id", "ID-K1")
    assert codigo == 0
    assert ("reviewandlaunch", "ID-K1") in zma.chamadas
    assert res["tipo"] == "enviada"


def test_tentativa_sem_id_acha_o_id_pela_chave(monkeypatch, capsys, tmp_path):
    """Edição barrada antes do conserto tem só a chave guardada: o id vem do recentcampaigns."""
    zma = _Zma(recentes=[_campanha("K1", NOME, "Draft")])
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0 and res["campaign_id"] == "ID-K1"
    assert ("reviewandlaunch", "ID-K1") in zma.chamadas


def test_sem_id_e_sem_conferencia_falha_sem_perder_a_chave(monkeypatch, capsys, tmp_path):
    zma = _Zma(recentes_falha=True)
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo not in (0, None, send_zma.EXIT_BARRADO_REVISAO)
    assert res["tipo"] == "erro" and res["campaign_key"] == "K1"
    assert not any(isinstance(c, tuple) and c[0] == "reviewandlaunch" for c in zma.chamadas)


def test_campanha_ja_em_revisao_do_zoho_nao_e_lancada_de_novo(monkeypatch, capsys, tmp_path):
    """Depois do reviewandlaunch a campanha fica em ContentToBeReviewed e sai sozinha: lançar
    outra com o mesmo nome faria a lista receber duas vezes."""
    zma = _Zma(recentes=[_campanha("K1", NOME, "To Be Reviewed")])  # como veio em 30/10
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0 and res["tipo"] == "ja_enviada"
    assert not any(isinstance(c, tuple) and c[0] == "reviewandlaunch" for c in zma.chamadas)


@pytest.mark.parametrize("status", ["ContentToBeReviewed", "Scheduled", "SendingShortly",
                                    "Sending", "Sent"])
def test_status_de_lancamento_e_sucesso(status):
    resp = {"status_code": 200, "campaign_details": {"status": status}}
    assert send_zma.classifica_envio(resp) == ("enviada", "200")


def test_status_code_200_com_campanha_parada_em_draft_e_erro():
    """200 sozinho não basta: a campanha precisa ter saído do Draft."""
    resp = {"status_code": 200, "campaign_details": {"status": "Draft"}}
    assert send_zma.classifica_envio(resp)[0] == "erro"


def test_recusa_http_do_reviewandlaunch_volta_como_resposta(monkeypatch):
    """O ZMA recusa com HTTP 4xx e JSON no corpo; o código tem de chegar ao classificador."""
    import io
    import urllib.error

    def _urlopen(req, timeout=None):
        assert req.get_method() == "PUT"
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b'{"status_code": 400, "code": "6601"}'))

    monkeypatch.setattr(send_zma.urllib.request, "urlopen", _urlopen)
    resp = send_zma.put("https://ma.zoho.com/api/v2.1/campaigns/1/reviewandlaunch")
    assert send_zma.classifica_envio(resp) == ("erro", "6601")


def test_6600_e_campanha_que_ja_saiu(monkeypatch, capsys, tmp_path):
    """6600 = "Campaign is sent": a campanha saiu entre a conferência e o envio."""
    zma = _Zma(recentes=[_campanha("K1", NOME, "Draft")],
               envio={"code": "6600", "message": "Campaign is sent"})
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma, "--campaign-key", "K1")
    assert codigo == 0 and res["tipo"] == "ja_enviada" and res["campaign_key"] == "K1"


def test_sucesso_embrulhado_em_response_e_sucesso(monkeypatch, capsys, tmp_path):
    """A doc do ZMA mostra o sucesso dentro de `response`; a recusa de 24/10 veio solta."""
    zma = _Zma(envio={"response": SUCESSO_V1})
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma)
    assert codigo == 0 and res["tipo"] == "enviada"


@pytest.mark.parametrize("status,tipo", [("Sent", "ja_enviada"), ("Draft", "pendente")])
def test_check_so_confere_e_nunca_envia(monkeypatch, capsys, tmp_path, status, tipo):
    zma = _Zma(recentes=[_campanha("K1", NOME, status)])
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma,
                           "--check", "--campaign-key", "K1", "--campaign-name", NOME)
    assert codigo == 0
    assert res["tipo"] == tipo and res["campaign_key"] == "K1"
    assert all(not isinstance(c, tuple) for c in zma.chamadas), "o --check fez POST"


def test_check_que_nao_consegue_conferir_diz_isso(monkeypatch, capsys, tmp_path):
    zma = _Zma(recentes_falha=True)
    codigo, res, _ = _roda(monkeypatch, capsys, tmp_path, zma,
                           "--check", "--campaign-key", "K1", "--campaign-name", NOME)
    assert res["tipo"] == "nao_conferido"
