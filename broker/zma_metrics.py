"""zma_metrics.py — relatórios de campanha ZMA (open/click/bounce).

DISCOVERY (D4): o endpoint exato de relatório do ZMA precisa ser validado contra a
org ao vivo. `fetch` tenta o endpoint conhecido; `parse_report` normaliza a resposta
para taxas. Ajustar os nomes de campo no passo de validação E2E.
"""
import json
import urllib.parse
import urllib.request

ZMA_BASE = "https://marketingautomation.zoho.com/api/v1"
ACCOUNTS_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"


def _pct(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip().endswith("%"):
        try:
            return float(v.strip().rstrip("%")) / 100.0
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    """Coerção tolerante para contagem inteira (aceita '182', 182, 182.0)."""
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# Contagens absolutas: chave de saída -> candidatos na resposta crua do ZMA.
_COUNT_FIELDS = (
    ("sent", ("sent", "emailssent")),
    ("delivered", ("delivered",)),
    ("opened", ("opened",)),
    ("clicked", ("clicked",)),
    ("bounced", ("bounced",)),
)


def parse_report(raw):
    """Normaliza a resposta crua do ZMA: taxas (open/click/bounce_rate) + contagens
    absolutas presentes (sent/delivered/opened/clicked/bounced). O painel mostra o
    absoluto (ex.: total de cliques) quando a chave existe; senão só a taxa."""
    out = {"open_rate": None, "click_rate": None, "bounce_rate": None}
    if not raw:
        return out
    sent = _int(raw.get("sent") or raw.get("emailssent") or raw.get("delivered")) or 0
    pairs = [("open_rate", "opened", "open_percent"),
             ("click_rate", "clicked", "click_percent"),
             ("bounce_rate", "bounced", "bounce_percent")]
    for rate_key, count_key, pct_key in pairs:
        if pct_key in raw:
            out[rate_key] = _pct(raw.get(pct_key))
        elif count_key in raw and sent:
            cnt = _int(raw.get(count_key))
            if cnt is not None:
                out[rate_key] = cnt / sent
    # Inclui só as contagens presentes (mantém o payload enxuto).
    for out_key, candidates in _COUNT_FIELDS:
        for c in candidates:
            v = _int(raw.get(c))
            if v is not None:
                out[out_key] = v
                break
    return out


def _access_token(env):
    data = urllib.parse.urlencode({
        "refresh_token": env["ZOHO_MA_REFRESH_TOKEN"],
        "client_id": env["ZOHO_MA_CLIENT_ID"],
        "client_secret": env["ZOHO_MA_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(ACCOUNTS_TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("access_token")


def fetch(env, campaign_key):
    """Puxa o relatório de uma campanha. Endpoint a validar (D4)."""
    if not campaign_key:
        return {}
    token = _access_token(env)
    url = (f"{ZMA_BASE}/getcampaignreports?resfmt=JSON"
           f"&campaignkey={urllib.parse.quote(campaign_key)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Zoho-oauthtoken {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())
    except Exception:  # noqa: BLE001 — endpoint a validar (D4)
        return {}
    report = raw.get("campaign_report") or raw.get("report") or raw
    return parse_report(report)
