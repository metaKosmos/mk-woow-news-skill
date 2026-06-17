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
