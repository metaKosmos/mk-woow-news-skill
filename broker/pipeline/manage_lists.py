#!/usr/bin/env python3
"""manage_lists.py — gerência de listas ZMA (Zoho Marketing Automation) da WooW News.

Cria listas de envio e adiciona contatos via API ZMA. Standalone (espelha os
helpers de send_zma.py) para o orchestrator copiar e rodar isolado no workdir.

Endpoints ZMA (scope ZohoMarketingAutomation.lead.CREATE, coberto por lead.ALL):
    getmailinglists                   -> lista as mailing lists (listname/listkey)
    addlistandleads (mode=newlist)    -> cria lista + até 10 emails
    addleadsinbulk  (listkey)         -> adiciona até 10 emails a uma lista existente

A API aceita no máximo 10 emails por chamada — o create/add fazem batching de 10.

Uso:
    python3 manage_lists.py list
    python3 manage_lists.py create --name "Time mK" --emails-file team.txt
    python3 manage_lists.py create --name "Time mK" --emails "a@x.com,b@x.com"
    python3 manage_lists.py add --list-key <KEY> --emails "c@x.com"

A ÚLTIMA linha do stdout é um JSON parseável pelo orchestrator, ex.:
    {"ok": true, "op": "create", "listkey": "...", "listname": "Time mK", "count": 29}
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent

ACCOUNTS_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZMA_BASE = "https://marketingautomation.zoho.com/api/v1"
EMAILS_PER_CALL = 10  # limite da API ZMA (addlistandleads / addleadsinbulk)


# ---------------------------------------------------------------- env / config
def find_envmk():
    """Sobe na árvore de diretórios procurando .envmk (fonte de verdade mK).

    Local: acha o .envmk da raiz do workspace. No broker: o orchestrator grava
    um .envmk com os segredos do Secret Manager na raiz do workdir."""
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
        sys.exit(f"Falta {key} no .envmk (Self Client OAuth de Marketing Automation).")
    return val


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


def _ok(resp):
    """ZMA devolve code '0' (string) e/ou status 'success' no sucesso."""
    return str(resp.get("code", "")) in ("0", "200") or resp.get("status") == "success"


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def get_lists(token):
    """getmailinglists -> [{listname, listkey, ...}]. Exige scope lead.ALL."""
    resp = get(f"{ZMA_BASE}/getmailinglists?resfmt=JSON", headers=auth_headers(token))
    rows = resp.get("list_of_details") or resp.get("listdetails") or []
    out = []
    for item in rows:
        out.append({"listname": item.get("listname") or item.get("listName"),
                    "listkey": item.get("listkey") or item.get("listKey"),
                    "count": item.get("noofcontacts")})
    return out


def add_leads(token, listkey, emails):
    """addleadsinbulk -> adiciona até 10 emails a uma lista existente."""
    resp = post_form(f"{ZMA_BASE}/addleadsinbulk",
                     {"resfmt": "JSON", "listkey": listkey, "emailids": ",".join(emails)},
                     headers=auth_headers(token))
    if not _ok(resp):
        sys.exit(f"addleadsinbulk falhou ({len(emails)} emails): {json.dumps(resp)}")
    return resp


def create_list(token, listname, emails, description=None):
    """addlistandleads (mode=newlist) com o 1º lote; addleadsinbulk com o resto.

    Devolve o listkey da lista criada."""
    if not emails:
        sys.exit("Lista de emails vazia.")
    first, rest = emails[:EMAILS_PER_CALL], emails[EMAILS_PER_CALL:]
    data = {
        "resfmt": "JSON",
        "listname": listname,
        "signupform": "public",
        "mode": "newlist",
        "emailids": ",".join(first),
    }
    if description:
        data["listdescription"] = description
    resp = post_form(f"{ZMA_BASE}/addlistandleads", data, headers=auth_headers(token))
    listkey = resp.get("listkey") or resp.get("listKey")
    if not listkey or not _ok(resp):
        sys.exit(f"addlistandleads não criou a lista: {json.dumps(resp)}")
    for batch in _chunks(rest, EMAILS_PER_CALL):
        add_leads(token, listkey, batch)
    return listkey


# ------------------------------------------------------------------ email util
def parse_emails(raw_csv=None, file_path=None):
    """Aceita CSV (--emails) ou arquivo (--emails-file, 1 por linha ou CSV).
    Dedupe preservando a ordem; valida presença de '@'."""
    raw = ""
    if file_path:
        raw = Path(file_path).read_text(encoding="utf-8")
    elif raw_csv:
        raw = raw_csv
    tokens = []
    for line in raw.replace(",", "\n").splitlines():
        e = line.strip().strip(",").strip()
        if e:
            tokens.append(e)
    seen, out, bad = set(), [], []
    for e in tokens:
        if "@" not in e:
            bad.append(e); continue
        low = e.lower()
        if low not in seen:
            seen.add(low); out.append(e)
    if bad:
        sys.exit(f"Emails inválidos (sem '@'): {bad}")
    if not out:
        sys.exit("Nenhum email válido fornecido.")
    return out


# ------------------------------------------------------------------------ main
def _emit(payload):
    """Última linha do stdout = JSON que o orchestrator parseia."""
    print(json.dumps(payload, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="Gerência de listas ZMA (WooW News)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--emails", default=None, help="CSV de emails")
    c.add_argument("--emails-file", default=None, help="arquivo com emails (1 por linha ou CSV)")
    c.add_argument("--description", default=None)

    a = sub.add_parser("add")
    a.add_argument("--list-key", required=True)
    a.add_argument("--emails", default=None)
    a.add_argument("--emails-file", default=None)

    args = ap.parse_args()
    env = load_env()
    token = get_access_token(env)

    if args.cmd == "list":
        lists = get_lists(token)
        for it in lists:
            print(f"  {it['listname']!r:40}  {it['listkey']}", file=sys.stderr)
        _emit({"ok": True, "op": "list", "lists": lists, "count": len(lists)})
        return

    if args.cmd == "create":
        emails = parse_emails(args.emails, args.emails_file)
        print(f"Criando lista {args.name!r} com {len(emails)} contatos "
              f"({(len(emails) + EMAILS_PER_CALL - 1) // EMAILS_PER_CALL} chamada(s))…",
              file=sys.stderr)
        listkey = create_list(token, args.name, emails, args.description)
        _emit({"ok": True, "op": "create", "listkey": listkey,
               "listname": args.name, "count": len(emails)})
        return

    if args.cmd == "add":
        emails = parse_emails(args.emails, args.emails_file)
        for batch in _chunks(emails, EMAILS_PER_CALL):
            add_leads(token, args.list_key, batch)
        _emit({"ok": True, "op": "add", "listkey": args.list_key, "count": len(emails)})
        return


if __name__ == "__main__":
    main()
