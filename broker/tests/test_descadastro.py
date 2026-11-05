# broker/tests/test_descadastro.py
"""O link de descadastro do Daily Drops sai com a merge tag que o ZMA reconhece.

Até 05/11/2026 o render usava $[UNSUBSCRIBE]$. O ZMA não reconhece essa grafia: trata como
link comum, passa pelo rastreador de cliques e redireciona para https://$[UNSUBSCRIBE]$, e o
clique termina em ERR_INVALID_REDIRECT.
"""
import json
import pathlib
import sys

BROKER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))

import generate_content as gc  # noqa: E402
import render_newsletter  # noqa: E402


def test_render_usa_a_merge_tag_do_zma(tmp_path, monkeypatch):
    content = {"cabecalho": "[WDD]", "titulo_edicao": "T", "sumario": ["i0"]}
    for campo in gc.BLOCK_FIELDS:
        content[campo] = {"headline": f"H {campo}", "corpo": f"<p>corpo {campo}</p>"}
    (tmp_path / "2026-11-05.json").write_text(json.dumps({"content": content}), encoding="utf-8")
    monkeypatch.setattr(render_newsletter, "TEMPLATES", BROKER / "templates")
    monkeypatch.setattr(render_newsletter, "CONFIG", BROKER / "config")
    monkeypatch.setattr(render_newsletter, "CONTENT", tmp_path)
    monkeypatch.setattr(render_newsletter, "RENDERS", tmp_path / "renders")

    saida = render_newsletter.render_woow("2026-11-05", image_url="https://example.com/i.png")

    html = saida.read_text(encoding="utf-8")
    assert 'href="$[LI:UNSUBSCRIBE]$"' in html
    assert "$[UNSUBSCRIBE]$" not in html
