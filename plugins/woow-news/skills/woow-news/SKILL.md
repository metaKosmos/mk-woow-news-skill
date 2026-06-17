---
name: woow-news
description: Opera a newsletter WooW! Daily Drops da metaKosmos — gaveta editorial, pesquisa/curadoria/disparo e métricas. Use quando o usuário disser "/woow-news", "newsletter WooW", "Daily Drops", "gaveta da newsletter", "disparar newsletter mK", "rodar a WooW" ou "métricas da newsletter".
---

# WooW! Daily Drops — Skill operacional

Opera a newsletter diária da mK via broker autenticado por email mK. Os segredos (Gemini, Zoho) ficam no GCP, nunca na máquina. Você (João/Patrick/David) só precisa estar logado com a conta @metakosmos.com.br.

## No início de toda sessão
Rode a checagem de versão e mostre o aviso de 1 linha se houver update:
`python3 scripts/version_check.py`

## Login (uma vez)
`bash scripts/setup.sh` instala deps + faz login Google mK (loopback). Depois, `python3 scripts/auth.py --status` mostra quem está logado.

## Comandos (sempre via scripts/woow.py)
- `python3 scripts/woow.py status` — a gaveta: enviado, pronto, gerado, pesquisado, vazio + cobertura.
- `python3 scripts/woow.py run --edition 2026-wXX` — pipeline completo COM checkpoints: pesquisa, mostra pauta, pergunta pauta manual, gera conteúdo+imagem+HTML, mostra preview + custo, pergunta se dispara. O disparo NUNCA é automático.
- `python3 scripts/woow.py run --edition 2026-wXX --stage research|generate|send` — roda um estágio isolado.
- `python3 scripts/woow.py add-pauta --edition 2026-wXX --title "..." --content "..." --link "..."` — injeta pauta manual no próximo research.
- `python3 scripts/woow.py queue` — fila detalhada (JSON).
- `python3 scripts/woow.py metrics` — métricas ZMA (open/click/bounce) + custo das últimas edições.
- `python3 scripts/woow.py sync` — força o espelho do estado pro Firebase (o painel mkaifirst.web.app/#newsletter lê de lá).

## Checkpoint obrigatório
O disparo (`send`) SEMPRE pede confirmação humana explícita. Nunca dispare sem o operador conferir o preview. Em validação, garanta que a lista aponta para a lista de teste antes do envio real.

## Papéis
- Admin (david@): muda lógica, allowlist, deploy.
- Operadores (joão@, patrick@): run, add-pauta, queue, metrics, sync.
Erro 403 significa conta não autorizada ou rota de admin. Confira `python3 scripts/auth.py --status`.

## Onde as coisas moram
Estado autoritativo: bucket GCS (via broker). Painel: espelho Firebase. Schema dos arquivos em `references/schema.md`.
