"""notify.py — aviso de edição pronta ou falha, sempre ANTES do envio.

Existe porque o tick diário roda sozinho e não fala com ninguém. Com `auto-send off` a
edição para em `ready` esperando revisão, e nada avisa que ela está lá: a revisão dependia
de alguém lembrar de rodar `woow status`. Quando o estágio falha é pior — o `cron_tick`
marca o dia como rodado ANTES de executar, engole a exceção e devolve 200 ao Cloud
Scheduler, então não há nem nova tentativa nem sinal.

Aviso DEPOIS do envio não serve: a essa altura o time já recebeu o e-mail.

Falha de aviso nunca derruba a edição. Toda a superfície pública é try/except.
"""
import json
import urllib.error
import urllib.request

import secrets_store

TIMEOUT = 10


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


def monta_aviso(edition, estado, erro=None):
    """Texto do aviso, ou None quando não há o que avisar. Função pura, testável sem rede.

    `sent` não gera aviso de propósito: o e-mail já chegou a quem seria avisado."""
    estado = estado or {}
    if erro:
        return (f"*WooW! Daily Drops {edition} — NÃO foi gerada*\n"
                f"```{str(erro)[:600]}```\n"
                f"O dia já foi marcado como rodado, então o cron não tenta de novo. "
                f"Para rodar na mão antes do horário de entrega:\n"
                f"`python3 scripts/woow.py run --edition {edition}`")
    if estado.get("stage") != "ready":
        return None
    linhas = [f"*WooW! Daily Drops {edition} — pronta para revisão*"]
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


def avisa(estado, edition, erro=None):
    """Monta e envia. NUNCA levanta: aviso é observabilidade, não parte da entrega.

    Devolve o que aconteceu, para o log do Cloud Run e para o teste: 'enviado',
    'sem_webhook', 'nada_a_avisar' ou 'falhou: ...'."""
    try:
        texto = monta_aviso(edition, estado, erro)
        if not texto:
            return "nada_a_avisar"
        url = secrets_store.get_slack_webhook()
        if not url:
            return "sem_webhook"
        envia(url, texto)
        return "enviado"
    except Exception as exc:  # noqa: BLE001 — falha de aviso não pode derrubar a edição
        return f"falhou: {type(exc).__name__}: {exc}"[:200]
