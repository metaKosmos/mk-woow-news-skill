# O Zoho Campaigns deixa src vazio, sem erro, na imagem que não ingere. Caso real de
# 02/11/2026 (What's New): as 6 URLs com espaço ou acento sumiram, as 4 simples chegaram.
import pathlib
import sys
import types

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_fake = types.ModuleType("broker_client")
_fake.BrokerError = type("BrokerError", (Exception,), {})
sys.modules.setdefault("broker_client", _fake)

import woow  # noqa: E402

BASE = "https://site.metakosmoslab.com/api/media/file/"


def _img(src):
    return f'<img src="{src}" width="10" height="10" alt="x">'


def test_nomes_simples_passam():
    html = "".join(_img(BASE + n + "?prefix=stage%2Fsite") for n in
                   ["header.png", "stu.png", "favoritos.gif", "bx_share-alt.png", "mk-fashion.png"])
    assert woow.auditar_imagens(html) == []


def test_espaco_e_acento_no_nome_sao_pegos():
    for n in ["Hero%20Asset.png", "mK%20Fashion.png", "compara%C3%A7%C3%A3o%20gif.gif",
              "bot%C3%A3o%20de%20destaque%20gif.gif", "Purple%20Heart.png"]:
        achados = woow.auditar_imagens(_img(BASE + n + "?prefix=stage%2Fsite"))
        assert len(achados) == 1 and "espaço ou acento" in achados[0][1], n


def test_pasta_com_espaco_nao_acusa_se_o_arquivo_e_simples():
    assert woow.auditar_imagens(_img("https://x.com/minha%20pasta/logo.png")) == []


def test_src_vazio_data_relativo_http_e_svg():
    casos = {
        '<img src alt="a">': "sem src",
        '<img src="" alt="a">': "vazio",
        _img("data:image/png;base64,AAAA"): "data:",
        _img("imagens/logo.png"): "https absoluto",
        _img("http://x.com/logo.png"): "https absoluto",
        _img("https://x.com/logo.svg"): "SVG",
    }
    for html, trecho in casos.items():
        achados = woow.auditar_imagens(html)
        assert len(achados) == 1 and trecho in achados[0][1], html


def test_aviso_devolve_se_ha_problema(capsys):
    assert woow._avisar_imagens(_img(BASE + "a%20b.png"), "x.html") is True
    assert "descartar" in capsys.readouterr().out
    assert woow._avisar_imagens(_img(BASE + "ab.png"), "x.html") is False
