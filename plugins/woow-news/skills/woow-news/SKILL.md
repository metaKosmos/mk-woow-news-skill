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

## Convenção da edição (diária)
WooW! **Daily Drops** = uma edição por dia. O identificador da edição é a **data de
publicação no formato `YYYY-MM-DD`** (ex.: `2026-06-17`). O broker já preenche o campo
`date` a partir dessa chave. (Edições legadas com chave semanal `2026-wNN` ainda funcionam,
mas use o formato de data para as novas.)

## Comandos (sempre via scripts/woow.py)
- `python3 scripts/woow.py status` — a gaveta: enviado, pronto, gerado, pesquisado, vazio + cobertura.
- `python3 scripts/woow.py run --edition 2026-06-17` — pipeline completo COM checkpoints: pesquisa, mostra pauta, pergunta pauta manual, gera conteúdo+imagem+HTML, mostra preview + custo, pergunta se dispara. O disparo NUNCA é automático.
- `python3 scripts/woow.py run --edition 2026-06-17 --stage research|generate|send` — roda um estágio isolado.
- `python3 scripts/woow.py add-pauta --edition 2026-06-17 --title "..." --content "..." --link "..."` — injeta pauta manual no próximo research.
- `python3 scripts/woow.py queue` — fila detalhada (JSON).
- `python3 scripts/woow.py metrics` — métricas ZMA (open/click/bounce) + custo das últimas edições.
- `python3 scripts/woow.py sync` — força o espelho do estado pro Firebase (o painel mkaifirst.web.app/#newsletter lê de lá).
- `python3 scripts/woow.py list-lists` — lista as listas de envio do ZMA (nome + listkey + contatos) e marca (→) qual é o alvo do envio diário.
- `python3 scripts/woow.py create-list --name "Time mK Daily Drops" --emails-file team.txt` — cria uma lista de envio no ZMA com os contatos (CSV via `--emails` também serve). Confirma antes de criar e devolve o `listkey`.
- `python3 scripts/woow.py set-list --list-key <KEY>` (ou `--name "..."`) — troca a lista-alvo do envio diário. Mostra o alvo atual + o novo (com nº de contatos) e pede confirmação antes de gravar.
- `python3 scripts/woow.py roles list` — **(admin)** mostra admins e operadores atuais + o admin-base de env. `roles add-operator <email>` / `roles remove-operator <email>` / `roles add-admin <email>` / `roles remove-admin <email>` gerenciam acesso sem redeploy (confirma antes). Só admin executa.

## Listas e destinatários (ZMA, NÃO Zoho CRM)
A lista de envio da newsletter vive no **Zoho Marketing Automation (ZMA)**, e a skill cria/lista/troca essas listas via broker (comandos acima). **Esta skill não usa Zoho CRM.** Se o seu ambiente Claude tiver algum conector `ZohoCRM_*` conectado, **ignore-o** — ele não tem nada a ver com a newsletter; criar algo no módulo "Campaigns" do CRM não vira lista de disparo. Para qualquer operação de lista, use sempre `scripts/woow.py` (list-lists / create-list / set-list), nunca ferramentas de CRM.

Criar uma lista e trocar o destinatário do envio são **ações de operador** (você consegue fazer sozinho) — não precisam de admin nem de redeploy. O `set-list` grava o alvo em estado mutável; a `newsletter.yaml` é só o fallback.

## Checkpoint obrigatório
- **Disparo (`send`):** SEMPRE pede confirmação humana explícita. Nunca dispare sem conferir o preview. Antes do envio, confira em `list-lists` qual lista está marcada como alvo (→).
- **Trocar destinatário (`set-list`):** redireciona QUEM recebe a news diária. O comando confirma antes; só responda `s` se for essa a lista certa.

## Papéis
Dois níveis, com uma separação importante: **administrar acesso ≠ atualizar a skill.**
- **Operador** (run, add-pauta, queue, metrics, sync, **list-lists, create-list, set-list**): toca toda a operação e o ZMA, mas não as rotas de admin.
- **Admin**: tudo do operador + **gerência de acesso pela própria skill** (`roles ...`: promover/rebaixar operador e admin, sem redeploy) + `/admin/reset`. Admin **NÃO** atualiza a skill: mexer no template/prompt/lógica/SKILL.md é commit no GitHub + deploy do broker, que exige credencial de GitHub/GCP fora da skill.
- **Admin-base** (`ADMIN_EMAILS` de env, ex.: david@): sempre admin, anti-lockout, não é removível por `roles remove-admin` (só por deploy). É a chave-mestra de break-glass.

Os papéis vivem no `roles.json` (GCS, mutável pela skill); enquanto ninguém usa `roles`, valem as env vars (`ADMIN_EMAILS`/`OPERATOR_EMAILS`). Erro 403 = conta não autorizada ou rota de admin. Confira `python3 scripts/auth.py --status`.

## Onde as coisas moram
Estado autoritativo: bucket GCS (via broker). Painel: espelho Firebase. Schema dos arquivos em `references/schema.md`.
