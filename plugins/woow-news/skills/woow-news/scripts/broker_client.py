#!/usr/bin/env python3
"""broker_client.py — cliente fino do broker woow-news (Bearer ID token mK)."""
import json, sys, urllib.error, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth import get_id_token            # noqa: E402
from config import BROKER_URL, ssl_context, local_version  # noqa: E402


class BrokerError(Exception):
    pass


_aviso_mostrado = False


def _mostra_aviso(body):
    """Imprime o aviso de update que o broker manda no corpo, uma vez por processo.

    Vai em stderr para nao sujar a saida de comandos que emitem JSON (queue). Fica aqui,
    e nao em cada cmd_*, porque este e o unico ponto por onde toda resposta passa: assim
    o aviso aparece em qualquer comando, sem depender de ninguem lembrar de checar."""
    global _aviso_mostrado
    aviso = body.get("_aviso") if isinstance(body, dict) else None
    if not aviso or _aviso_mostrado:
        return
    _aviso_mostrado = True
    print(f"[!] {aviso.get('message', '')}", file=sys.stderr)
    if aviso.get("notes"):
        print(f"    o que mudou: {aviso['notes']}", file=sys.stderr)


def _req(method, path, payload=None):
    url = f"{BROKER_URL}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    versao = local_version()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {get_id_token()}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        # O broker usa a versao para decidir se manda aviso de update e para registrar
        # quem opera em qual versao. O User-Agent era fixo em "1.0", que era mentira.
        "X-Skill-Version": versao,
        "User-Agent": f"woow-news-skill/{versao or 'desconhecida'}"})
    try:
        with urllib.request.urlopen(req, timeout=600, context=ssl_context()) as r:
            body = r.read().decode("utf-8")
            out = json.loads(body) if body else {}
            _mostra_aviso(out)
            out.pop("_aviso", None)  # o aviso é do stderr; stdout de `queue` sai JSON limpo
            return out
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
        except Exception:
            err = {"error": "resposta não-JSON"}
        if e.code in (401, 403):
            raise BrokerError(f"Acesso negado ({e.code}): {err.get('error')}. "
                              f"Rode: python scripts/auth.py --status")
        raise BrokerError(f"Broker {e.code}: {json.dumps(err)[:400]}")
    except urllib.error.URLError as e:
        raise BrokerError(f"Não contatei o broker em {BROKER_URL}: {e}")


def run(edition, stage, extra=None):  return _req("POST", "/run", {"edition": edition, "stage": stage, **(extra or {})})
def add_pauta(edition, pauta):        return _req("POST", "/add-pauta", {"edition": edition, "pauta": pauta})
def queue():                          return _req("GET", "/queue")
def metrics():                        return _req("GET", "/metrics")
def sync():                           return _req("GET", "/sync")
def list_lists():                     return _req("GET", "/lists")
def create_list(name, emails, description=None):
    return _req("POST", "/lists/create", {"name": name, "emails": emails, "description": description})
def set_active_list(list_key, list_name=None):
    return _req("POST", "/lists/set-active", {"list_key": list_key, "list_name": list_name})
def get_schedule():                   return _req("GET", "/schedule")
def set_schedule(cfg):                return _req("POST", "/schedule/set", cfg)
def create_campaign(edition, type, extra=None):
    return _req("POST", "/campaigns/create", {"edition": edition, "type": type, **(extra or {})})
def set_html(edition, html):          return _req("POST", "/campaigns/set-html", {"edition": edition, "html": html})
def get_sources():                    return _req("GET", "/sources")
def set_sources(op, **kw):            return _req("POST", "/sources/set", {"op": op, **kw})
def test_sources(**kw):               return _req("POST", "/sources/test", kw)
def get_clients():                    return _req("GET", "/clients")
def set_release(notes):               return _req("POST", "/admin/release", {"notes": notes})
def get_senders():                    return _req("GET", "/senders")
def set_sender(from_email, from_name=None):
    return _req("POST", "/senders/set-active", {"from_email": from_email, "from_name": from_name})


def version():
    try:
        with urllib.request.urlopen(f"{BROKER_URL}/version", timeout=10, context=ssl_context()) as r:
            return json.loads(r.read()).get("version")
    except Exception:
        return None
