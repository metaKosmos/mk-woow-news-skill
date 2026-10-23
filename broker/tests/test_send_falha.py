# broker/tests/test_send_falha.py — falha do sendcampaign carrega a resposta crua do ZMA
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER / "pipeline"))
import send_zma


def test_sucesso_nao_e_falha():
    assert send_zma.falha_sendcampaign({"code": "0"}, "K") is None
    assert send_zma.falha_sendcampaign({"code": 200}, "K") is None

def test_falha_leva_codigo_e_resposta_crua():
    msg = send_zma.falha_sendcampaign({"code": "2502", "message": "Campaign under review"}, "K1")
    assert "code 2502" in msg and "K1" in msg and "Campaign under review" in msg
    assert "6611" not in msg  # a dica do 6611 só aparece quando o código é 6611

def test_6611_ganha_a_dica():
    msg = send_zma.falha_sendcampaign({"code": "6611", "message": "Content not reviewed"}, "K")
    assert "Content not reviewed" in msg and "fluxo de revisão" in msg

def test_resposta_sem_code():
    msg = send_zma.falha_sendcampaign({"status": "error"}, "K")
    assert "code ausente" in msg and '"status": "error"' in msg

def test_mensagem_cabe_no_last_error():
    msg = send_zma.falha_sendcampaign({"code": "6611", "message": "x" * 2000}, "K")
    assert len(msg) <= 500
