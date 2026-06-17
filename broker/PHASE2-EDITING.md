# Fase 2 — Edição no painel (WooW News, MAR-133)

Documentado, **não implementado**. Habilita editar título e data de publicação direto do
dashboard `woow-news`. É escrita: hoje o painel é read-only e o espelho Firebase é `write:false`,
então a edição passa obrigatoriamente pelo broker (que escreve via SA).

## Decisões já tomadas
- "Renomear" = editar o **subject** (título da edição). NÃO re-chavear o ID da edição
  (re-chavear move o `editions/<ed>.state.json` no GCS — raro/arriscado, fora de escopo).
- "Mudar data" = setar o campo `date`. **`date` é só rótulo informativo. NÃO existe scheduler:**
  quem dispara o envio é o cron/skill, não uma data por edição. A UI tem que deixar isso explícito
  ("data = rótulo, não reagenda o envio").

## Backend (broker)
Novo endpoint autenticado em `main.py` + `orchestrator.py`:

```
POST /edition/update   (papel: operador — mesma authorize() do /run, /queue)
body: { "edition": "2026-06-17", "subject"?: "...", "date"?: "YYYY-MM-DD" }
```
- Valida `edition` existe (`get_state`); `date` casa `YYYY-MM-DD` se enviado.
- Aplica `sm.upsert_edition(edition, patch)` só com os campos enviados (subject/date).
  `upsert_edition` já faz merge raso sem rebaixar stage — reusar.
- Chama `sm.sync_to_firebase()` ao final para o painel refletir na hora.
- Retorna o state atualizado.

Sem novos segredos. Redeploy: `broker/provision.sh` (David).

## Front-end (painel)
- **Login Google mK**: reusar `GET /oauth-config` do broker (client Desktop) + obter ID token;
  enviar `Authorization: Bearer <id_token>` no POST. O broker já valida domínio + papel.
  O painel só mostra os controles de edição quando logado; leitura segue sem login.
- **UI**: edição inline na tabela do **Histórico** (e/ou na Gaveta) — lápis no subject e no
  campo data. Salvar → `POST /edition/update` → re-fetch do espelho.
- Aviso fixo perto do campo data: "data = rótulo de publicação, não reagenda o envio".

## Verificação
- pytest do endpoint (`upsert` só os campos enviados; rejeita date inválida; exige papel).
- E2E manual: logar no painel, renomear uma edição, ver refletir no espelho/Gaveta.

## Fora de escopo (continua)
- Re-chavear o ID da edição.
- Auto-rodar pipeline (generate+send) diário automático — dispara email real, decisão à parte.
