# broker/tests/test_send_falha.py — falha do sendcampaign carrega a resposta crua do ZMA
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER / "pipeline"))
import send_zma


import pytest


def test_sucesso_nao_e_falha():
    assert send_zma.falha_sendcampaign({"code": "0"}, "K") is None
    assert send_zma.falha_sendcampaign({"code": 200}, "K") is None
    assert send_zma.falha_sendcampaign({"response": {"code": "200"}}, "K") is None

def test_falha_leva_codigo_e_resposta_crua():
    msg = send_zma.falha_sendcampaign({"code": "2502", "message": "Campaign under review"}, "K1")
    assert "code 2502" in msg and "K1" in msg and "Campaign under review" in msg
    assert "6611" not in msg  # código fora da tabela não ganha significado inventado

@pytest.mark.parametrize("code,significado", [
    ("6608", "os destinatários mudaram depois da revisão"),
    ("6609", "o conteúdo mudou depois da revisão"),
    ("6611", "o conteúdo não foi revisado"),
])
def test_recusa_da_revisao_diz_o_significado_e_nao_manda_mexer_no_dns(code, significado):
    """A dica fixa de 6611 levou o time a pedir mudança de DNS em 23/10, antes de alguém saber
    que o código real era 6608. O texto agora sai da tabela da doc, código por código."""
    msg = send_zma.falha_sendcampaign({"code": code, "message": "x"}, "K")
    assert significado in msg
    assert "liberar a revisão" in msg
    assert "domínio" not in msg

def test_6600_nao_e_falha():
    """6600 = "Campaign is sent": a campanha já tinha saído, não há o que reenviar."""
    assert send_zma.falha_sendcampaign({"code": "6600"}, "K") is None

def test_significado_vem_antes_da_resposta_crua():
    """`health.last_error` corta em 500. Com a chave real (66 caracteres) e a resposta crua
    no meio, o significado no fim era justamente o que sumia."""
    chave = "3zbe6a9a4cc5c38076aec96871c36422408cb7f987613b41cf82e5a59defe29ed6"
    msg = send_zma.falha_sendcampaign({"code": "6608", "message": "y" * 2000}, chave)
    assert "os destinatários mudaram depois da revisão" in msg[:500]

def test_resposta_sem_code():
    msg = send_zma.falha_sendcampaign({"status": "error"}, "K")
    assert "code ausente" in msg and '"status": "error"' in msg

def test_mensagem_cabe_no_last_error():
    msg = send_zma.falha_sendcampaign({"code": "6611", "message": "x" * 2000}, "K")
    assert len(msg) <= 500
