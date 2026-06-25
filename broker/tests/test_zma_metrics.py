# broker/tests/test_zma_metrics.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from zma_metrics import parse_report, _unwrap

def test_parse_rates_from_report():
    raw = {"sent": 200, "opened": 62, "clicked": 10, "bounced": 2}
    r = parse_report(raw)
    assert round(r["open_rate"], 4) == 0.31
    assert round(r["click_rate"], 4) == 0.05
    assert round(r["bounce_rate"], 4) == 0.01

def test_parse_handles_missing():
    assert parse_report({}) == {"open_rate": None, "click_rate": None, "bounce_rate": None}

def test_parse_accepts_percent_strings():
    # Campos percentuais reais do ZMA (open_percent + unique_clicked_percent).
    raw = {"emails_sent_count": 100, "open_percent": "28%", "unique_clicked_percent": "4%"}
    r = parse_report(raw)
    assert round(r["open_rate"], 4) == 0.28
    assert round(r["click_rate"], 4) == 0.04


def test_parse_real_zma_field_names():
    # Nomes reais do campaignreports -> chaves estáveis do painel.
    raw = {"emails_sent_count": "200", "delivered_count": "198", "opens_count": "62",
           "unique_clicks_count": "10", "bounces_count": "2"}
    r = parse_report(raw)
    assert r["sent"] == 200 and r["delivered"] == 198 and r["opened"] == 62
    assert r["clicked"] == 10 and r["bounced"] == 2


def test_parse_bare_number_percent_is_divided_by_100():
    # open_percent vem como número puro "100.0" (= 100%), não "100%". Deve virar 1.0.
    r = parse_report({"emails_sent_count": 1, "open_percent": "100.0"})
    assert r["open_rate"] == 1.0


def test_click_rate_uses_unique_clicked_percent_not_ctor():
    # clicksperopenrate é CTOR (clique÷abertura) — NÃO deve ser usado p/ click_rate.
    raw = {"emails_sent_count": 100, "unique_clicked_percent": "5.0", "clicksperopenrate": "50.0"}
    r = parse_report(raw)
    assert r["click_rate"] == 0.05


def test_bounce_fallback_hard_plus_soft():
    # bounces_count ausente -> soma hardbounce + softbounce.
    r = parse_report({"emails_sent_count": 100, "hardbounce_count": "3", "softbounce_count": "1"})
    assert r["bounced"] == 4
    assert round(r["bounce_rate"], 4) == 0.04


def test_unwrap_campaign_reports_list():
    # ZMA embrulha o relatório sob `campaign-reports` (lista).
    raw = {"campaign-reports": [{"emails_sent_count": "200", "opens_count": "62"}]}
    node = _unwrap(raw)
    assert node["emails_sent_count"] == "200"
    r = parse_report(node)
    assert r["sent"] == 200 and r["opened"] == 62


def test_unwrap_empty_list_is_safe():
    assert _unwrap({"campaign-reports": []}) == {}
    assert parse_report(_unwrap({"campaign-reports": []})) == {
        "open_rate": None, "click_rate": None, "bounce_rate": None}


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
