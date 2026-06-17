#!/usr/bin/env python3
"""Disparo da newsletter mK via API Zoho Marketing Automation (ZMA).

Adaptação da automação MAR-118 (era Zoho Campaigns v1.1) para Marketing
Automation v1. O domínio metakosmos.com.br está autenticado em ZMA, então o
sendcampaign deve passar sem o clique manual que o Campaigns exigia (erro 6611).

Fluxo:
    refresh OAuth  ->  getmailinglists (resolve listkey)
                   ->  createCampaign (content_url + list_details + topicId)
                   ->  sendcampaign (campaignkey)   [só com --send]

Uso:
    # cria a campanha (Draft) a partir de um HTML já público, sem enviar:
    python3 send_zma.py content/2026-w24.md --content-url https://.../newsletter-2026-w24.html

    # cria E dispara:
    python3 send_zma.py content/2026-w24.md --content-url https://.../x.html --send

Pré-requisito: ZOHO_MA_CLIENT_ID, ZOHO_MA_CLIENT_SECRET, ZOHO_MA_REFRESH_TOKEN
no .envmk (ver README — gerar Self Client em api-console.zoho.com com scope
ZohoMarketingAutomation.campaign.ALL). from_email precisa estar em Senders.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Faltam dependências. Rode: pip3 install pyyaml")

BASE = Path(__file__).resolve().parent

ACCOUNTS_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZMA_BASE = "https://marketingautomation.zoho.com/api/v1"

DEFAULT_FROM_EMAIL = "david@metakosmos.com.br"
DEFAULT_FROM_NAME = "metaKosmos"


# ---------------------------------------------------------------- env / config
def find_envmk():
    """Sobe na árvore de diretórios procurando .envmk (fonte de verdade mK)."""
    for parent in [BASE, *BASE.parents]:
        candidate = parent / ".envmk"
        if candidate.exists():
            return candidate
    return None


def load_env():
    env = {}
    path = find_envmk()
    if not path:
        sys.exit("Não encontrei .envmk subindo a partir de " + str(BASE))
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require(env, key):
    val = env.get(key)
    if not val:
        sys.exit(
            f"Falta {key} no .envmk. Gere o Self Client OAuth de Marketing "
            f"Automation (ver README) e adicione a credencial."
        )
    return val


def read_meta(content_path: Path):
    raw = content_path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        sys.exit("Arquivo sem front-matter YAML.")
    _, fm, _ = raw.split("---", 2)
    return yaml.safe_load(fm) or {}


# ------------------------------------------------------------------- http util
def post_form(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------- zma api
def get_access_token(env):
    """Troca o refresh token por um access token (válido 1h)."""
    data = {
        "refresh_token": require(env, "ZOHO_MA_REFRESH_TOKEN"),
        "client_id": require(env, "ZOHO_MA_CLIENT_ID"),
        "client_secret": require(env, "ZOHO_MA_CLIENT_SECRET"),
        "grant_type": "refresh_token",
    }
    resp = post_form(ACCOUNTS_TOKEN_URL, data)
    token = resp.get("access_token")
    if not token:
        sys.exit(f"Falha ao obter access_token: {resp}")
    return token


def auth_headers(token):
    # Zoho usa o prefixo "Zoho-oauthtoken", NÃO "Bearer".
    return {"Authorization": f"Zoho-oauthtoken {token}"}


def resolve_listkey(token, list_name):
    """getmailinglists -> acha o listkey pelo nome da lista."""
    url = f"{ZMA_BASE}/getmailinglists?resfmt=JSON"
    resp = get(url, headers=auth_headers(token))
    lists = resp.get("list_of_details") or resp.get("listdetails") or []
    for item in lists:
        name = item.get("listname") or item.get("listName")
        if name == list_name:
            return item.get("listkey") or item.get("listKey")
    sys.exit(
        f"Lista '{list_name}' não encontrada em getmailinglists. "
        f"Resposta crua: {json.dumps(resp)[:600]}"
    )


def create_campaign(token, *, campaignname, from_email, from_name, subject,
                    content_url, listkey, topic_id=None):
    data = {
        "resfmt": "JSON",
        "campaignname": campaignname,
        "from_email": from_email,
        "from_name": from_name,
        "subject": subject,
        "content_url": content_url,
        "list_details": json.dumps({listkey: []}),
    }
    if topic_id:
        data["topicId"] = topic_id
    resp = post_form(f"{ZMA_BASE}/createCampaign", data, headers=auth_headers(token))
    key = resp.get("campaignKey") or resp.get("campaign_key")
    if not key:
        sys.exit(f"createCampaign não retornou campaignKey: {json.dumps(resp)}")
    return key, resp


def send_campaign(token, campaignkey):
    data = {"resfmt": "JSON", "campaignkey": campaignkey}
    return post_form(f"{ZMA_BASE}/sendcampaign", data, headers=auth_headers(token))


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Disparo newsletter mK via Zoho Marketing Automation")
    ap.add_argument("content", help="caminho do content/*.md (subject, list_name, etc.)")
    ap.add_argument("--content-url", required=True, help="URL pública do HTML renderizado (content_url)")
    ap.add_argument("--from-email", default=DEFAULT_FROM_EMAIL)
    ap.add_argument("--from-name", default=DEFAULT_FROM_NAME)
    ap.add_argument("--topic-id", default=None, help="topicId (se Topic Management estiver ativo)")
    ap.add_argument("--list-name", default=None, help="sobrescreve o list_name do front-matter")
    ap.add_argument("--list-key", default=None, help="listkey direto do ZMA (pula a resolução por nome)")
    ap.add_argument("--send", action="store_true", help="dispara (default: só cria Draft)")
    args = ap.parse_args()

    content_path = Path(args.content)
    if not content_path.is_absolute():
        content_path = BASE / content_path
    meta = read_meta(content_path)

    subject = meta.get("subject") or sys.exit("front-matter sem 'subject'")
    list_name = args.list_name or meta.get("list_name")
    if not args.list_key and not list_name:
        sys.exit("Defina --list-key, ou list_name no front-matter / --list-name")
    campaignname = f"mK Newsletter {content_path.stem}"

    env = load_env()
    token = get_access_token(env)
    print("OK access_token obtido")

    if args.list_key:
        listkey = args.list_key
        print(f"OK listkey (config): {listkey}")
    else:
        listkey = resolve_listkey(token, list_name)
        print(f"OK listkey de '{list_name}': {listkey}")

    key, resp = create_campaign(
        token,
        campaignname=campaignname,
        from_email=args.from_email,
        from_name=args.from_name,
        subject=subject,
        content_url=args.content_url,
        listkey=listkey,
        topic_id=args.topic_id,
    )
    print(f"OK createCampaign — campaignKey: {key}")
    print(f"    status: {resp.get('campaign_status') or resp.get('status')}")

    if not args.send:
        print("Modo Draft (sem --send). Campanha criada, não enviada.")
        return

    send_resp = send_campaign(token, key)
    print(f"sendcampaign -> {json.dumps(send_resp)}")
    code = str(send_resp.get("code", ""))
    if code not in ("0", "200"):
        sys.exit(
            "sendcampaign não confirmou sucesso. Se for 6611 (Content not "
            "reviewed), validar autenticação de domínio / fluxo de revisão em ZMA."
        )
    print("OK envio confirmado")


if __name__ == "__main__":
    main()
