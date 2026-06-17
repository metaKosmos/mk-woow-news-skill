# broker/tests/test_cost_tracker.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from cost_tracker import compute_cost

RATES = {
    "brl_rate": 5.0,
    "models": {
        "gemini-3.5-flash": {"input_per_m": 1.0, "output_per_m": 2.0},
        "gemini-3-pro-image": {"input_per_m": 1.0, "per_image_usd": 0.06},
    },
    "steps": {"classify": "gemini-3.5-flash", "write": "gemini-3.5-flash", "image": "gemini-3-pro-image"},
}

def test_token_step_cost():
    usage = {"classify": {"input": 1_000_000, "output": 500_000}}
    c = compute_cost(usage, RATES)
    # 1.0 USD (input) + 1.0 USD (0.5M * 2.0) = 2.0 USD -> 10.0 BRL
    assert round(c["per_step_brl"]["classify"], 4) == 10.0
    assert round(c["total_usd"], 4) == 2.0
    assert round(c["total_brl"], 4) == 10.0

def test_image_flat_fee():
    usage = {"image": {"input": 1000, "output": 0}}
    c = compute_cost(usage, RATES)
    # 1000/1e6 * 1.0 = 0.001 USD + 0.06 flat = 0.061 USD
    assert round(c["total_usd"], 4) == 0.061

def test_brl_rate_env_override(monkeypatch):
    monkeypatch.setenv("BRL_RATE", "6.0")
    usage = {"classify": {"input": 1_000_000, "output": 0}}
    c = compute_cost(usage, RATES)
    assert round(c["total_brl"], 4) == 6.0  # 1 USD * 6.0
