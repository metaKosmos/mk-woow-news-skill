#!/usr/bin/env python3
"""Disparo da newsletter mK via API Zoho Marketing Automation (ZMA).

Adaptação da automação MAR-118 (era Zoho Campaigns v1.1) para Marketing
Automation v1. Até 16/10/2026 o sendcampaign passava sem revisão; desde 17/10 a conta está no
Marketing Automation 2.0, toda campanha nasce com revisão de conteúdo obrigatória e o
sendcampaign da v1 volta 6611 sem conseguir mandá-la para revisão. O disparo passou para o
`reviewandlaunch` da API v2.1 (doc enviada pelo suporte do Zoho em 30/10, não publicada): ele
manda a campanha para a revisão do Zoho, e ela sai sozinha quando a revisão passa. A criação
continua na v1. Recusa que ainda dependa de alguém no painel sai com EXIT_BARRADO_REVISAO.

Fluxo:
    refresh OAuth  ->  recentcampaigns (a edição já saiu?)   [só com --send]
                   ->  getmailinglists (resolve listkey)
                   ->  createCampaign (content_url + list_details + topicId)
                       ou --campaign-key (reusa a campanha de uma tentativa anterior)
                   ->  reviewandlaunch v2.1 (campaign_id numérico)   [só com --send]

O resultado sai no stdout em linhas JSON (`tipo`, `campaign_key`, `code`...), e o orquestrador
lê a última: `criada` logo depois do createCampaign, o resultado do envio no fim. Recusa da
revisão do ZMA sai com EXIT_BARRADO_REVISAO.

Uso:
    # cria a campanha (Draft) a partir de um HTML já público, sem enviar:
    python3 send_zma.py content/2026-w24.md --content-url https://.../newsletter-2026-w24.html

    # cria E dispara:
    python3 send_zma.py content/2026-w24.md --content-url https://.../x.html --send

    # reenvia a campanha de uma tentativa barrada, sem criar outra:
    python3 send_zma.py content/2026-w24.md --content-url https://.../x.html --send --campaign-key K --campaign-id N

    # só confere se a campanha (ou uma cópia dela) já saiu:
    python3 send_zma.py --check --campaign-key K --campaign-name "mK Newsletter 2026-w24"

Pré-requisito: ZOHO_MA_CLIENT_ID, ZOHO_MA_CLIENT_SECRET, ZOHO_MA_REFRESH_TOKEN
no .envmk (ver README — gerar Self Client em api-console.zoho.com com scope
ZohoMarketingAutomation.campaign.ALL). from_email precisa estar em Senders.
"""
import argparse
import json
import os
import sys
import urllib.error
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
# A v2.1 só responde neste host: no marketingautomation.zoho.com ela devolve 1004.
ZMA_V21_BASE = "https://ma.zoho.com/api/v2.1"
# Teto de cada chamada HTTP. Sem ele, uma chamada travada só cai nos 600 s do subprocess, e
# a conferência do prazo, que roda antes de o tick escolher a campanha, seguraria o tick.
TIMEOUT_HTTP = 60

DEFAULT_FROM_EMAIL = "david@metakosmos.com.br"
DEFAULT_FROM_NAME = "metaKosmos"

# Saída própria da recusa da revisão: o orquestrador tenta de novo até o prazo só nela.
EXIT_BARRADO_REVISAO = 3

# Recusas do sendcampaign, com o texto da doc do ZMA (páginas Schedule Campaign e Error
# Codes). Código fora daqui sai cru, sem significado inventado.
SIGNIFICADO_ZMA = {
    "6600": "a campanha já foi enviada",
    "6601": "sem permissão para esta ação",
    "6605": "o status da campanha mudou",
    "6606": "nenhuma lista selecionada na campanha",
    "6607": "a campanha está sem conteúdo",
    "6608": "os destinatários mudaram depois da revisão",
    "6609": "o conteúdo mudou depois da revisão",
    "6611": "o conteúdo não foi revisado",
    "6301": "campaignKey inválido",
    "6503": "a campanha foi apagada",
    "3006": "o conteúdo mudou depois da revisão",
    "3007": "os destinatários mudaram depois da revisão",
    # Do reviewandlaunch, lido ao vivo em 30/10/2026 numa campanha que já estava em revisão.
    "5014": "a campanha não pode ir para revisão (já está em revisão ou saiu)",
}
# Recusa que só passa quando alguém libera a revisão no painel do ZMA.
CODIGOS_REVISAO = frozenset({"6608", "6609", "6611", "3006", "3007"})
# `campaign_status` de campanha que já saiu ou está saindo, sem espaço e em minúsculas.
# A campanha em revisão entra: depois do reviewandlaunch ela sai sozinha quando a revisão do
# Zoho passa, e mandar outra com o mesmo nome faria a lista receber duas vezes. O
# recentcampaigns da v1 a mostra como "To Be Reviewed" (lido em 30/10/2026); a v2.1, como
# ContentToBeReviewed.
STATUS_SAIU = frozenset({"sent", "inprogress", "sending", "scheduled",
                         "scheduledafterreviewed", "tobereviewed", "contenttobereviewed",
                         "sendingshortly"})
# Status de sucesso do reviewandlaunch, no mesmo formato.
STATUS_LANCADA = frozenset({"contenttobereviewed", "scheduled", "sendingshortly", "sending",
                            "sent"})


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
    with urllib.request.urlopen(req, timeout=TIMEOUT_HTTP) as resp:
        return json.loads(resp.read().decode("utf-8"))


def put(url, headers=None):
    """PUT sem corpo. A recusa do ZMA vem como HTTP 4xx com JSON no corpo, e é ela que diz o
    código: o corpo volta como resposta, não como exceção."""
    req = urllib.request.Request(url, data=b"", headers=headers or {}, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_HTTP) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")
        try:
            return json.loads(corpo)
        except ValueError:
            raise e from None


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT_HTTP) as resp:
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


def _id_numerico(c):
    """O campaign_id numérico de uma campanha ou resposta do ZMA, ou "". O createCampaign e o
    recentcampaigns não usam o mesmo nome de campo (o recentcampaigns usa `campaignId`)."""
    corpo = _corpo(c)
    for campo in ("campaign_id", "campaignId", "campaignid"):
        if corpo.get(campo):
            return str(corpo[campo])
    return ""


def campaign_id_da_chave(token, campaign_key):
    """O campaign_id da campanha pela chave, procurado no recentcampaigns, ou "". O
    getcampaigndetails não traz o id, e o recentcampaigns pode deixar rascunho de fora (29/10):
    por isso o id é guardado na criação e esta busca é só a rede de segurança."""
    for c in _campanhas(recent_campaigns(token, 200)):
        if c.get("campaign_key") == campaign_key:
            return _id_numerico(c)
    return ""


def launch_campaign(token, campaign_id):
    """reviewandlaunch da v2.1: manda a campanha para a revisão do Zoho e dispara quando ela
    passa. Recebe o campaign_id numérico, não o campaignKey."""
    url = (f"{ZMA_V21_BASE}/campaigns/{campaign_id}/reviewandlaunch"
           "?send_mode=sendnow&schedule_type=sendnow")
    return put(url, headers=auth_headers(token))


def recent_campaigns(token, quantas=30):
    """recentcampaigns: as últimas campanhas da conta, com `campaign_status`."""
    url = f"{ZMA_BASE}/recentcampaigns?resfmt=JSON&range={quantas}"
    return get(url, headers=auth_headers(token))


def _corpo(resp):
    """O ZMA responde solto ou embrulhado em `response`: a doc mostra o sucesso embrulhado, a
    recusa de 24/10/2026 veio solta. Lê os dois."""
    if isinstance(resp, dict) and isinstance(resp.get("response"), dict):
        return resp["response"]
    return resp if isinstance(resp, dict) else {}


def _campanhas(resp):
    """Lista do recentcampaigns. A página da ZMA v1 devolve 404; o formato é o da doc da
    Zoho Campaigns v1.1 (`recent_campaigns`)."""
    corpo = _corpo(resp)
    for chave in ("recent_campaigns", "recentcampaigns", "campaigns"):
        if isinstance(corpo.get(chave), list):
            return corpo[chave]
    return []


def campanha_que_saiu(resp, campaign_key=None, nome=None):
    """A campanha da edição que já saiu, ou None. Função pura.

    Casa pela chave ou pelo nome EXATO. O nome cobre a edição rodada de novo, que cria outra
    campanha com o mesmo nome (24/10/2026 teve duas). Assunto e nome contido ficam de fora de
    propósito: no mesmo dia houve um teste para o time ("TESTE 6608 Daily Drops ... (time)")
    com o assunto da edição, e um teste assim, enviado, faria a lista real ficar sem ela. Nos
    envios à mão de 21 e 23/10 saiu a própria campanha do broker, e é a chave que pega isso."""
    for c in _campanhas(resp):
        status = str(c.get("campaign_status", "")).replace(" ", "").replace("_", "").lower()
        if status not in STATUS_SAIU:
            continue
        if campaign_key and c.get("campaign_key") == campaign_key:
            return c
        if nome and c.get("campaign_name") == nome:
            return c
    return None


def status_lancamento(resp):
    """Status da campanha na resposta do reviewandlaunch, sem espaço e em minúsculas, ou "".
    Na resposta real (30/10/2026) ele vem em `campaign_details.status`."""
    corpo = _corpo(resp)
    detalhes = corpo.get("campaign_details")
    for fonte in (detalhes if isinstance(detalhes, dict) else {}, corpo):
        for campo in ("status", "campaign_status"):
            if fonte.get(campo):
                return str(fonte[campo]).replace(" ", "").replace("_", "").lower()
    return ""


def classifica_envio(send_resp):
    """(tipo, code) da resposta do disparo. tipo: enviada | ja_enviada | revisao | erro.

    `enviada` no reviewandlaunch é status_code 200 com a campanha num dos STATUS_LANCADA:
    em ContentToBeReviewed ela já está com o Zoho e sai sozinha."""
    corpo = _corpo(send_resp)
    status_code = str(corpo.get("status_code", ""))
    if status_code:
        if status_code == "200" and status_lancamento(send_resp) in STATUS_LANCADA:
            return "enviada", status_code
        code = str(corpo.get("code") or corpo.get("error_code") or status_code)
        if code == "6600":
            return "ja_enviada", code
        return ("revisao" if code in CODIGOS_REVISAO else "erro"), code
    code = str(corpo.get("code", ""))
    if code in ("0", "200"):
        return "enviada", code
    if code == "6600":
        return "ja_enviada", code
    if code in CODIGOS_REVISAO:
        return "revisao", code
    return "erro", code


def _resultado(**campos):
    """Última linha do stdout: o que o orquestrador lê para decidir a próxima tentativa."""
    print(json.dumps(campos, ensure_ascii=False))


# Endpoint de listagem de Senders do ZMA. A API v1 do Marketing Automation NÃO documenta
# publicamente esse método (pesquisa jul/2026); este path é um CANDIDATO a confirmar por
# probe ao vivo numa conta ZMA real (patrick@ como controle positivo). Sobrescreva por env
# ZMA_SENDERS_PATH quando o probe revelar o path correto — sem redeploy do código. Se o
# endpoint não existir/responder, quem chama (orchestrator) cai na allowlist configurada.
SENDERS_PATH = os.environ.get("ZMA_SENDERS_PATH", "getfromaddress")


def _parse_senders(resp):
    """Normaliza a resposta de Senders do ZMA para [{email, verified}]. Tolerante a variações
    de nome de campo; devolve None se não reconhecer nada parecido com senders (aí o probe
    inspeciona a resposta crua)."""
    if not isinstance(resp, dict):
        return None
    rows = (resp.get("from_address") or resp.get("fromaddress") or resp.get("senders")
            or resp.get("from_addresses") or resp.get("list_of_details"))
    if not isinstance(rows, list):
        return None
    out = []
    for it in rows:
        if isinstance(it, str):
            out.append({"email": it, "verified": None})
        elif isinstance(it, dict):
            email = (it.get("email") or it.get("fromemail") or it.get("from_email")
                     or it.get("emailid") or it.get("fromAddress"))
            if "verified" in it:
                verified = bool(it.get("verified"))
            else:
                verified = str(it.get("status") or it.get("verifystatus") or "").lower() \
                    in ("verified", "1", "true", "active")
            if email:
                out.append({"email": email, "verified": verified})
    return out or None


def list_senders(token):
    """Best-effort: lista os Senders (from-addresses) do ZMA + status de verificação.
    Devolve {"senders": [{email, verified}, ...]} quando reconhece o formato; senão
    {"senders": None, "raw": <resposta crua>} para o probe ao vivo inspecionar."""
    try:
        resp = get(f"{ZMA_BASE}/{SENDERS_PATH}?resfmt=JSON", headers=auth_headers(token))
    except Exception as e:  # noqa: BLE001 — endpoint pode não existir
        return {"senders": None, "error": str(e), "path": SENDERS_PATH}
    senders = _parse_senders(resp)
    return {"senders": senders} if senders is not None else {"senders": None, "raw": resp,
                                                             "path": SENDERS_PATH}


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Disparo newsletter mK via Zoho Marketing Automation")
    ap.add_argument("content", nargs="?", help="caminho do content/*.md (subject, list_name, etc.)")
    ap.add_argument("--content-url", help="URL pública do HTML renderizado (content_url)")
    ap.add_argument("--from-email", default=DEFAULT_FROM_EMAIL)
    ap.add_argument("--from-name", default=DEFAULT_FROM_NAME)
    ap.add_argument("--topic-id", default=None, help="topicId (se Topic Management estiver ativo)")
    ap.add_argument("--list-name", default=None, help="sobrescreve o list_name do front-matter")
    ap.add_argument("--list-key", default=None, help="listkey direto do ZMA (pula a resolução por nome)")
    ap.add_argument("--campaign-name", default=None,
                    help="nome da campanha no ZMA (default: 'mK Newsletter <stem>')")
    ap.add_argument("--list-senders", action="store_true",
                    help="lista os Senders do ZMA (JSON) e sai — não cria campanha")
    ap.add_argument("--send", action="store_true", help="dispara (default: só cria Draft)")
    ap.add_argument("--campaign-key", default=None,
                    help="reenvia esta campanha, de uma tentativa anterior, em vez de criar outra")
    ap.add_argument("--campaign-id", default=None,
                    help="campaign_id numérico da --campaign-key (sem ele, procura no recentcampaigns)")
    ap.add_argument("--check", action="store_true",
                    help="só confere se a campanha (--campaign-key/--campaign-name) já saiu")
    args = ap.parse_args()

    env = load_env()
    token = get_access_token(env)
    print("OK access_token obtido")

    if args.list_senders:
        print(json.dumps(list_senders(token), ensure_ascii=False))
        return

    if args.check:
        _confere(token, args.campaign_key, args.campaign_name)
        return

    if not args.content or not args.content_url:
        sys.exit("Uso: send_zma.py <content.md> --content-url <URL> [--send]  (ou --list-senders)")
    content_path = Path(args.content)
    if not content_path.is_absolute():
        content_path = BASE / content_path
    meta = read_meta(content_path)

    subject = meta.get("subject") or sys.exit("front-matter sem 'subject'")
    list_name = args.list_name or meta.get("list_name")
    if not args.campaign_key and not args.list_key and not list_name:
        sys.exit("Defina --list-key, ou list_name no front-matter / --list-name")
    campaignname = args.campaign_name or f"mK Newsletter {content_path.stem}"

    if args.send:
        # Antes de criar ou reenviar: a edição pode ter saído pelo painel enquanto a revisão
        # do ZMA segurava o envio pela API.
        saiu = _ja_saiu(token, args.campaign_key, campaignname)
        if saiu:
            print(f"OK a edição já saiu: {saiu.get('campaign_name')} ({saiu.get('campaign_key')})")
            _resultado(tipo="ja_enviada", campaign_key=saiu.get("campaign_key") or "",
                       campaign_id=_id_numerico(saiu),
                       campaign_name=saiu.get("campaign_name") or campaignname,
                       enviada_em=saiu.get("sent_date_string") or "")
            return

    if args.campaign_key:
        key = args.campaign_key
        campaign_id = args.campaign_id or ""
        print(f"OK campanha de tentativa anterior, campaignKey: {key}")
    else:
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
        campaign_id = _id_numerico(resp)
        print(f"OK createCampaign, campaignKey: {key}, campaign_id: {campaign_id or '?'}")
        print(f"    status: {resp.get('campaign_status') or resp.get('status')}")
        # A chave sai já, antes do envio: processo morto no disparo (o teto do subprocess,
        # instância reciclada) não chega ao resultado final, e sem ela a tentativa seguinte
        # criaria uma segunda campanha com o mesmo nome.
        _resultado(tipo="criada", campaign_key=key, campaign_id=campaign_id,
                   campaign_name=campaignname)

    if not args.send:
        print("Modo Draft (sem --send). Campanha criada, não enviada.")
        return

    try:
        if not campaign_id:
            campaign_id = campaign_id_da_chave(token, key)
        if not campaign_id:
            raise RuntimeError("campaign_id não encontrado (nem na criação, nem no recentcampaigns)")
        send_resp = launch_campaign(token, campaign_id)
    except Exception as e:  # noqa: BLE001: a falha de rede não pode levar a chave junto
        _resultado(tipo="erro", campaign_key=key, campaign_id=campaign_id,
                   campaign_name=campaignname, code="", significado="", erro=str(e)[:200])
        sys.exit(f"reviewandlaunch falhou (campaignKey {key}): {e}")
    print(f"reviewandlaunch -> {json.dumps(send_resp)}")
    tipo, code = classifica_envio(send_resp)
    falha = falha_sendcampaign(send_resp, key)
    if not falha:
        print(f"OK campanha lançada ({status_lancamento(send_resp)}): sai quando a revisão "
              "do Zoho passar")
    _resultado(tipo=tipo, campaign_key=key, campaign_id=campaign_id,
               campaign_name=campaignname, code=code,
               significado=SIGNIFICADO_ZMA.get(code, ""),
               status_zma=status_lancamento(send_resp))
    if tipo == "revisao":
        print(falha, file=sys.stderr)
        sys.exit(EXIT_BARRADO_REVISAO)
    if falha:
        sys.exit(falha)


def _ja_saiu(token, campaign_key, nome):
    """A campanha da edição que já saiu, ou None. Best-effort: a conferência fora do ar não
    impede o envio, porque o próprio sendcampaign recusa campanha já enviada (6600)."""
    try:
        return campanha_que_saiu(recent_campaigns(token), campaign_key, nome)
    except Exception as e:  # noqa: BLE001: conferência é proteção extra, não pré-requisito
        print(f"recentcampaigns falhou ({e}); seguindo sem a conferência", file=sys.stderr)
        return None


def _confere(token, campaign_key, nome):
    """--check: diz se a campanha já saiu, sem enviar nada."""
    try:
        resp = recent_campaigns(token)
    except Exception as e:  # noqa: BLE001: quem chama distingue "não saiu" de "não sei"
        print(f"recentcampaigns falhou: {e}", file=sys.stderr)
        _resultado(tipo="nao_conferido", campaign_key=campaign_key or "", erro=str(e)[:200])
        return
    saiu = campanha_que_saiu(resp, campaign_key, nome)
    if saiu:
        _resultado(tipo="ja_enviada", campaign_key=saiu.get("campaign_key") or "",
                   campaign_name=saiu.get("campaign_name") or nome or "",
                   enviada_em=saiu.get("sent_date_string") or "")
    else:
        _resultado(tipo="pendente", campaign_key=campaign_key or "", campaign_name=nome or "")


def falha_sendcampaign(send_resp, campaignkey):
    """Mensagem de falha do disparo, ou None quando o ZMA aceitou o lançamento (ou a campanha
    já tinha saído).

    A resposta crua vai na mensagem: o orquestrador só guarda o stderr, e sem o código
    do ZMA a falha não tem diagnóstico (o Daily Drops ficou parado dias por isso). O
    significado vem antes da resposta crua porque `health.last_error` corta em 500."""
    tipo, code = classifica_envio(send_resp)
    if tipo in ("enviada", "ja_enviada"):
        return None
    crua = json.dumps(send_resp, ensure_ascii=False)[:300]
    significado = SIGNIFICADO_ZMA.get(code)
    rotulo = f"code {code or 'ausente'}" + (f": {significado}" if significado else "")
    if tipo == "revisao":
        return (f"reviewandlaunch barrado pela revisão do ZMA ({rotulo}, campaignKey {campaignkey}): "
                f"liberar a revisão desta campanha no painel do ZMA. {crua}")
    return f"reviewandlaunch recusado pelo ZMA ({rotulo}, campaignKey {campaignkey}): {crua}"


if __name__ == "__main__":
    main()
