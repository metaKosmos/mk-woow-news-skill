#!/usr/bin/env python3
"""broker_client.py — cliente fino do broker woow-news (Bearer ID token mK)."""
import json, sys, urllib.error, urllib.parse, urllib.request
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
        if e.code == 400:  # entrada inválida: a mensagem do broker já é a explicação
            raise BrokerError(err.get("error") or "requisição inválida")
        raise BrokerError(f"Broker {e.code}: {json.dumps(err)[:400]}")
    except urllib.error.URLError as e:
        raise BrokerError(f"Não contatei o broker em {BROKER_URL}: {e}")


def _qs(**pares):
    """Query string só com o que veio preenchido.

    Campo ausente e campo vazio não são a mesma coisa para o broker: sem `campanha` ele
    resolve a campanha padrão sozinho, e `campanha=` seria um slug vazio a validar. Mesma
    razão do `_sem_nulos` no lado do POST."""
    itens = {k: str(v) for k, v in pares.items() if v is not None and v != ""}
    return ("?" + urllib.parse.urlencode(itens)) if itens else ""


def _sem_nulos(d):
    return {k: v for k, v in d.items() if v is not None}


def run(edition, stage, extra=None):  return _req("POST", "/run", {"edition": edition, "stage": stage, **(extra or {})})
def add_pauta(edition, pauta):        return _req("POST", "/add-pauta", {"edition": edition, "pauta": pauta})
def queue():                          return _req("GET", "/queue")
def metrics(campanha=None):           return _req("GET", "/metrics" + _qs(campanha=campanha))
def sync():                           return _req("GET", "/sync")
def list_lists():                     return _req("GET", "/lists")
def create_list(name, emails, description=None):
    return _req("POST", "/lists/create", {"name": name, "emails": emails, "description": description})
def set_active_list(list_key, list_name=None, campanha=None):
    return _req("POST", "/lists/set-active",
                _sem_nulos({"list_key": list_key, "list_name": list_name, "campanha": campanha}))
def get_schedule(campanha=None):      return _req("GET", "/schedule" + _qs(campanha=campanha))
def set_schedule(cfg):                return _req("POST", "/schedule/set", cfg)
def create_campaign(edition, type, extra=None):
    return _req("POST", "/campaigns/create", {"edition": edition, "type": type, **(extra or {})})
def set_html(edition, html):          return _req("POST", "/campaigns/set-html", {"edition": edition, "html": html})
def get_sources(campanha=None):       return _req("GET", "/sources" + _qs(campanha=campanha))
# `/campanhas` é o nome da v1.8.0; `/curadoria` continua respondendo o mesmo no broker,
# porque a v1.7.0 o publicou. O cliente fala o nome novo: skill velha contra broker novo
# segue funcionando pelo alias, e skill nova contra broker velho é justamente o caso que o
# aviso de versão existe para pegar.
def get_campanhas():                  return _req("GET", "/campanhas")
def set_campanha(op, **kw):           return _req("POST", "/campanhas/set", {"op": op, **_sem_nulos(kw)})
def get_campanha_status(campanha=None):
    return _req("GET", "/campanhas/status" + _qs(campanha=campanha))
def get_curadoria():                  return get_campanhas()
def set_curadoria(op, **kw):          return set_campanha(op, **kw)
def get_publicados(campanha=None, dias=None):
    return _req("GET", "/publicados" + _qs(campanha=campanha, dias=dias))
def rebuild_publicados():             return _req("POST", "/admin/publicados/rebuild")
def set_sources(op, **kw):            return _req("POST", "/sources/set", {"op": op, **kw})
def test_sources(**kw):               return _req("POST", "/sources/test", kw)
def get_clients():                    return _req("GET", "/clients")
def set_release(notes):               return _req("POST", "/admin/release", {"notes": notes})
def get_senders():                    return _req("GET", "/senders")
def set_sender(from_email, from_name=None, campanha=None):
    return _req("POST", "/senders/set-active",
                _sem_nulos({"from_email": from_email, "from_name": from_name,
                            "campanha": campanha}))


def version():
    try:
        with urllib.request.urlopen(f"{BROKER_URL}/version", timeout=10, context=ssl_context()) as r:
            return json.loads(r.read()).get("version")
    except Exception:
        return None
