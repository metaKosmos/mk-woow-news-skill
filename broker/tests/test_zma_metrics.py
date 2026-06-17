# broker/tests/test_zma_metrics.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from zma_metrics import parse_report

def test_parse_rates_from_report():
    raw = {"sent": 200, "opened": 62, "clicked": 10, "bounced": 2}
    r = parse_report(raw)
    assert round(r["open_rate"], 4) == 0.31
    assert round(r["click_rate"], 4) == 0.05
    assert round(r["bounce_rate"], 4) == 0.01

def test_parse_handles_missing():
    assert parse_report({}) == {"open_rate": None, "click_rate": None, "bounce_rate": None}

def test_parse_accepts_percent_strings():
    raw = {"sent": 100, "open_percent": "28%", "click_percent": "4%"}
    r = parse_report(raw)
    assert round(r["open_rate"], 4) == 0.28
    assert round(r["click_rate"], 4) == 0.04


def test_parse_includes_absolute_counts():
    # Painel mostra o nº absoluto de cliques quando o ZMA reporta as contagens.
    raw = {"sent": 200, "delivered": 198, "opened": 62, "clicked": 10, "bounced": 2}
    r = parse_report(raw)
    assert r["sent"] == 200
    assert r["delivered"] == 198
    assert r["opened"] == 62
    assert r["clicked"] == 10
    assert r["bounced"] == 2


def test_parse_absolute_count_coercion():
    # ZMA às vezes devolve contagens como string.
    r = parse_report({"sent": "300", "clicked": "182"})
    assert r["sent"] == 300
    assert r["clicked"] == 182


def test_parse_omits_absent_counts():
    # Só taxas (percent): nenhuma contagem absoluta deve ser inventada.
    r = parse_report({"sent": 100, "open_percent": "28%"})
    assert "clicked" not in r
    assert "opened" not in r
    assert r["sent"] == 100  # 'sent' presente é refletido
