"""zma_metrics.py — relatórios de campanha ZMA (open/click/bounce + contagens).

Endpoint Zoho Marketing Automation v1 `campaignreports` (só precisa de `campaignkey`).
O relatório vem embrulhado em `campaign-reports` (lista). parse_report normaliza os
nomes reais do ZMA para chaves estáveis que o painel já consome.
"""
import json
import urllib.parse
import urllib.request

ZMA_BASE = "https://marketingautomation.zoho.com/api/v1"
ACCOUNTS_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"


def _zpct(v):
    """Percentual do ZMA -> taxa 0–1. Os campos vêm como número 0–100 (ex.: '100.0',
    '28.5'); também tolera string com '%' ('28%'). SEMPRE divide por 100."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().rstrip("%").strip()
        if not v:
            return None
    try:
        return float(v) / 100.0
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


# Contagens absolutas: chave de saída -> candidatos na resposta crua do ZMA (nomes
# reais do campaignreports + aliases legados como fallback).
_COUNT_FIELDS = (
    ("sent", ("emails_sent_count", "sent", "emailssent")),
    ("delivered", ("delivered_count", "delivered")),
    ("opened", ("opens_count", "opened")),
    ("clicked", ("unique_clicks_count", "clicked")),
    ("bounced", ("bounces_count", "bounced")),
)

# Taxas (0–1): chave de saída -> (campo percentual ZMA, chave de contagem p/ fallback÷sent).
# click_rate usa unique_clicked_percent (NÃO clicksperopenrate, que é CTOR — clique÷abertura).
_RATE_FIELDS = (
    ("open_rate", "open_percent", "opened"),
    ("click_rate", "unique_clicked_percent", "clicked"),
    ("bounce_rate", "bounce_percent", "bounced"),
)


def parse_report(raw):
    """Normaliza o relatório cru do ZMA: taxas (open/click/bounce_rate, 0–1) +
    contagens absolutas presentes (sent/delivered/opened/clicked/bounced). O painel
    consome essas chaves estáveis, não os nomes crus do ZMA."""
    out = {"open_rate": None, "click_rate": None, "bounce_rate": None}
    if not raw:
        return out

    # Contagens primeiro (a taxa pode precisar de 'sent' p/ o fallback÷sent).
    for out_key, candidates in _COUNT_FIELDS:
        for c in candidates:
            v = _int(raw.get(c))
            if v is not None:
                out[out_key] = v
                break
    # bounced ausente, mas hard+soft presentes -> soma.
    if out.get("bounced") is None:
        hard, soft = _int(raw.get("hardbounce_count")), _int(raw.get("softbounce_count"))
        if hard is not None or soft is not None:
            out["bounced"] = (hard or 0) + (soft or 0)

    sent = out.get("sent") or out.get("delivered") or 0
    for rate_key, pct_field, count_key in _RATE_FIELDS:
        rate = _zpct(raw.get(pct_field))
        if rate is None and out.get(count_key) is not None and sent:
            rate = out[count_key] / sent
        out[rate_key] = rate
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


def _unwrap(raw):
    """Desembrulha o relatório: o ZMA devolve sob `campaign-reports` (lista). Checa a
    presença da chave (não a verdade), p/ tratar lista vazia como relatório ausente."""
    node = raw
    if isinstance(raw, dict):
        for key in ("campaign-reports", "campaign_report", "report"):
            if key in raw:
                node = raw[key]
                break
    if isinstance(node, list):
        node = node[0] if node else {}
    return node if isinstance(node, dict) else {}


def fetch(env, campaign_key):
    """Puxa o relatório de uma campanha enviada (endpoint campaignreports).

    Pode levantar em erro de rede/auth — quem chama (do_sync) trata por edição p/ não
    derrubar o espelho; a rota /metrics propaga (502) e a validação ao vivo vê o erro.
    """
    if not campaign_key:
        return {}
    token = _access_token(env)
    url = (f"{ZMA_BASE}/campaignreports?resfmt=JSON"
           f"&campaignkey={urllib.parse.quote(campaign_key)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Zoho-oauthtoken {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = json.loads(r.read())
    return parse_report(_unwrap(raw))
