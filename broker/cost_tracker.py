"""cost_tracker.py — custo por edição a partir do uso de tokens + rates Gemini.

usage: {etapa: {"input": int, "output": int}}. rates: dict do cost_rates.yaml.
Imagem cobra taxa fixa por imagem (per_image_usd) além dos tokens de texto.
"""
import os


def compute_cost(usage, rates):
    brl_rate = float(os.environ.get("BRL_RATE", rates.get("brl_rate", 5.70)))
    models = rates.get("models", {})
    step_model = rates.get("steps", {})
    per_step_usd = {}
    for step, u in usage.items():
        model = step_model.get(step, step)
        m = models.get(model, {})
        inp = u.get("input", 0) / 1_000_000 * m.get("input_per_m", 0.0)
        out = u.get("output", 0) / 1_000_000 * m.get("output_per_m", 0.0)
        flat = m.get("per_image_usd", 0.0) if step == "image" else 0.0
        per_step_usd[step] = inp + out + flat
    total_usd = sum(per_step_usd.values())
    return {
        "per_step_usd": per_step_usd,
        "per_step_brl": {k: v * brl_rate for k, v in per_step_usd.items()},
        "total_usd": total_usd,
        "total_brl": total_usd * brl_rate,
        "brl_rate": brl_rate,
    }
