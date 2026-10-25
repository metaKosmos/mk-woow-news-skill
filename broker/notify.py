"""notify.py: aviso de edição pronta ou falha, sempre ANTES do envio.

Existe porque o tick diário roda sozinho e não fala com ninguém. Com `auto-send off` a
edição para em `ready` esperando revisão, e nada avisa que ela está lá: a revisão dependia
de alguém lembrar de rodar `woow status`. Quando o estágio falha é pior: o `cron_tick`
engole a exceção e devolve 200 ao Cloud Scheduler, e a nova tentativa (até 3 por dia) só
sai no tick seguinte.

Aviso DEPOIS do envio não serve: a essa altura o time já recebeu o e-mail. As exceções são a
edição barrada pela revisão do ZMA, em que o aviso marca quem libera no painel e o "saiu"
fecha o assunto quando ela sai depois disso, e a edição que o broker não enviou porque já
constava como enviada.

Falha de aviso nunca derruba a edição. Toda a superfície pública é try/except.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime

import secrets_store

TIMEOUT = 10
# Quem libera a revisão no painel do ZMA, pelo id do Slack (hoje o Patrick). Variável de
# ambiente para trocar de pessoa sem mexer no código.
MENCAO_ENV = "ALERT_REVIEW_MENTION"
_ID_SLACK_RE = re.compile(r"[UW][A-Z0-9]{2,20}")


def _linha_de_procedencia(estado):
    """Resumo do que a guarda de procedência fez, para o revisor saber onde olhar."""
    prov = estado.get("provenance") or {}
    bits = []
    if prov.get("publicados") is not None:
        bits.append(f"{prov['publicados']} itens")
    descartados = prov.get("descartados") or []
    if descartados:
        motivos = ", ".join(sorted({d.get("motivo", "?") for d in descartados}))
        bits.append(f"{len(descartados)} descartado(s): {motivos}")
    suspeitos = (estado.get("link_check") or {}).get("suspeitos") or []
    if suspeitos:
        bits.append(f"{len(suspeitos)} link(s) suspeito(s)")
    return " · ".join(bits)


def _mencao():
    """`<@ID> ` de quem libera a revisão, ou vazio. Só id de usuário passa: o valor vira
    texto do Slack, e um `<!channel>` ali chamaria o canal inteiro."""
    bruto = (os.environ.get(MENCAO_ENV) or "").strip()
    return f"<@{bruto}> " if _ID_SLACK_RE.fullmatch(bruto) else ""


def _quando(valor):
    """"às HH:MM" de um ISO do broker; o horário em texto do ZMA passa como veio."""
    if not valor:
        return ""
    try:
        return f"às {datetime.fromisoformat(str(valor)).strftime('%H:%M')}"
    except ValueError:
        return f"(Zoho: {valor})"


def _aviso_revisao(edition, envio, contexto):
    codigo, significado = envio.get("codigo") or "?", envio.get("significado")
    nome = envio.get("campaign_name") or edition
    criada = _quando(envio.get("criada_em"))
    prazo = contexto.get("prazo") or "o prazo"
    # Caminho e botão conferidos no painel em 25/10/2026. O horário de criação é o que o painel
    # mostra ao lado do nome, e separa as campanhas de mesmo nome (24/10 teve duas).
    linhas = [
        f"{_mencao()}*WooW! Daily Drops {edition}: envio barrado pela revisão do Zoho*",
        f"Código {codigo}" + (f": {significado}." if significado else "."),
        f"Para liberar: Zoho Marketing Automation, Marketing Campaigns › Email, abrir \"{nome}\""
        + (f" (criada {criada})" if criada else "") + " e seguir em \"Review and launch\". "
        "Não crie outra campanha: o broker reenvia esta mesma."]
    antigas = [_quando(d.get("criada_em")) for d in envio.get("descartadas") or []
               if d.get("criada_em")]
    if antigas:
        linhas.append(f"Não libere a de mesmo nome criada {', '.join(antigas)}: ficou com o "
                      f"conteúdo antigo.")
    linhas.append(f"O broker tenta enviar de novo a cada 15 min até {prazo}. Se não sair até "
                  f"lá, o dia é pulado e aviso aqui.")
    return "\n".join(linhas)


def _aviso_saiu(edition, envio):
    quando = _quando(envio.get("enviada_em"))
    return f"*WooW! Daily Drops {edition}: saiu{' ' + quando if quando else ''}*, depois da revisão do Zoho."


def _aviso_ja_saiu(edition, envio):
    nome = envio.get("campaign_name") or edition
    quando = _quando(envio.get("enviada_em"))
    return "\n".join([
        f"*WooW! Daily Drops {edition}: já tinha saído, o broker não enviou de novo*",
        f"A campanha \"{nome}\" já constava como enviada no Zoho{' ' + quando if quando else ''}. "
        f"Se esta edição devia sair também, confira no painel: duas campanhas com o mesmo nome "
        f"no mesmo dia contam como uma."])


def _aviso_prazo(edition, envio, contexto):
    prazo = contexto.get("prazo") or "o prazo"
    nome = envio.get("campaign_name") or edition
    significado = envio.get("significado")
    linhas = [f"*WooW! Daily Drops {edition}: não saiu até {prazo}*",
              f"A campanha \"{nome}\" seguiu barrada pela revisão do Zoho (código "
              f"{envio.get('codigo') or '?'}" + (f": {significado}" if significado else "")
              + "). O dia foi pulado e o broker parou de tentar."]
    if envio.get("ultimo_erro"):
        linhas.append(f"A última tentativa falhou por outro motivo: {envio['ultimo_erro']}")
    if contexto.get("conferido") is False:
        linhas.append("Não consegui confirmar com o Zoho se ela saiu pelo painel: vale "
                      "conferir no painel.")
    return "\n".join(linhas)


def _aviso_falha(edition, estado, erro, contexto):
    # Edição em `ready` já foi gerada, com preview no ar: só o envio falhou. De 22 a 25/10 o
    # aviso dizia "NÃO foi gerada" nesse caso, e mandava procurar defeito no lugar errado.
    titulo = ("gerada, mas NÃO foi enviada" if estado.get("stage") == "ready"
              else "NÃO foi gerada")
    linhas = [f"*WooW! Daily Drops {edition}: {titulo}*", f"```{str(erro)[:600]}```"]
    if contexto.get("nova_tentativa") is True:
        linhas.append(f"O broker tenta de novo no próximo tick, até {contexto.get('prazo')} "
                      f"(tentativa {contexto.get('tentativa')} de "
                      f"{contexto.get('tentativas_max')}).")
    elif contexto.get("nova_tentativa") is False:
        feitas = contexto.get("tentativa")
        linhas.append(f"Foram {feitas} tentativa(s) hoje, e o cron não tenta de novo." if feitas
                      else "O cron não tenta de novo hoje.")
    linhas.append(f"Para rodar na mão antes do horário de entrega:\n"
                  f"`python3 scripts/woow.py run --edition {edition}`")
    return "\n".join(linhas)


def monta_aviso(edition, estado, erro=None, evento=None, contexto=None):
    """Texto do aviso, ou None quando não há o que avisar. Função pura, testável sem rede.

    `evento` é o da edição barrada pela revisão do ZMA (revisao | saiu | prazo) ou o da que
    já constava como enviada (ja_saiu); sem ele, o aviso de sempre: falha, ou edição pronta
    esperando revisão. `contexto` traz o prazo e as tentativas do dia, que só o tick conhece.

    `sent` não gera aviso de propósito: o e-mail já chegou a quem seria avisado."""
    estado = estado or {}
    contexto = contexto or {}
    envio = estado.get("envio_zma") or {}
    if evento == "revisao":
        return _aviso_revisao(edition, envio, contexto)
    if evento == "saiu":
        return _aviso_saiu(edition, envio)
    if evento == "prazo":
        return _aviso_prazo(edition, envio, contexto)
    if evento == "ja_saiu":
        return _aviso_ja_saiu(edition, envio)
    if erro:
        return _aviso_falha(edition, estado, erro, contexto)
    if estado.get("stage") != "ready":
        return None
    linhas = [f"*WooW! Daily Drops {edition}: pronta para revisão*"]
    if estado.get("subject"):
        linhas.append(f"Assunto: {estado['subject']}")
    resumo = _linha_de_procedencia(estado)
    if resumo:
        linhas.append(resumo)
    if estado.get("preview_url"):
        linhas.append(f"Preview (abre sem login): {estado['preview_url']}")
    linhas.append(f"Para disparar: `python3 scripts/woow.py run --edition {edition} --stage send`")
    return "\n".join(linhas)


def envia(url, texto):
    """POST no webhook. Devolve True se o Slack aceitou."""
    req = urllib.request.Request(
        url, data=json.dumps({"text": texto}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return 200 <= resp.status < 300


def avisa(estado, edition, erro=None, evento=None, contexto=None):
    """Monta e envia. NUNCA levanta: aviso é observabilidade, não parte da entrega.

    Devolve o que aconteceu, para o log do Cloud Run e para o teste: 'enviado',
    'sem_webhook', 'nada_a_avisar' ou 'falhou: ...'."""
    try:
        texto = monta_aviso(edition, estado, erro, evento=evento, contexto=contexto)
        if not texto:
            return "nada_a_avisar"
        url = secrets_store.get_slack_webhook()
        if not url:
            return "sem_webhook"
        envia(url, texto)
        return "enviado"
    except Exception as exc:  # noqa: BLE001 — falha de aviso não pode derrubar a edição
        return f"falhou: {type(exc).__name__}: {exc}"[:200]
