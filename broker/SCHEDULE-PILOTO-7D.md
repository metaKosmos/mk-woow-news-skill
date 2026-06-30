# WooW! Daily Drops — agendamento do piloto de 7 dias (time interno mK)

Registro do agendamento criado para rodar a News diariamente no piloto interno.
Triggers vivem fora do repo (Claude Code Remote), então este arquivo é a fonte de
verdade do que foi programado e como mexer.

## O que foi programado

- **Janela:** 01/07 a 07/07/2026 (7 edições).
- **Horário:** 10h BRT (= 13:00 UTC). Cron: `0 13 1-7 7 *`.
- **Destinatário:** lista ZMA **"Time mK Daily Drops"** (alvo já configurado em
  `config/newsletter.yaml`; confira sempre com `woow.py list-lists`, o `→` marca o alvo).
- **Modo:** **gerar e avisar para aprovação**. O run automático faz pesquisa +
  geração e posta o preview para um humano aprovar. **O disparo NUNCA é automático**
  (regra dura da skill — `SKILL.md`).
- **Trigger diário:** `trig_01TnGU94T4ZjRt3j4tBbzxZm` (cria sessão nova a cada disparo;
  notificação push ligada).

## O que o run automático faz a cada 10h

1. Checa login do broker (`scripts/auth.py --status`). Sessão headless não loga
   sozinha; se não houver login válido, ele só avisa os operadores no Slack e para.
2. Se logado: `woow.py run --edition <hoje> --stage research` e depois `--stage generate`.
   A IA faz a curadoria fresca do dia (immersive commerce, IA, FOOH, ecossistema mK),
   sem pauta fixa.
3. Posta no Slack (david@, joao@) o preview pronto + assunto + custo estimado.
4. Deixa a edição em `ready` aguardando aprovação. **Não dispara.**

## Como disparar (humano, após conferir o preview)

```bash
python3 scripts/woow.py run --edition <YYYY-MM-DD> --stage send
```

Antes, confira o alvo: `python3 scripts/woow.py list-lists`.

## Limitação conhecida (auth)

O broker autentica por login Google mK interativo (loopback) com teto de sessão.
Uma sessão agendada/headless **não** consegue logar sozinha. Na prática, para o run
das 10h gerar a edição, precisa haver um login mK válido em cache na máquina onde a
sessão roda. Sem isso, o run só notifica os operadores para rodarem manualmente.

> Solução robusta "100% automático para o time" (não implementada): job server-side
> (Cloud Scheduler -> endpoint protegido por cron-token que faz research->generate->send),
> espelhando o cron `/sync` existente. É deploy de admin (David) e bypassa o checkpoint
> de aprovação — fica para uma decisão à parte.

## Encerrar / mexer no agendamento

- O cron `1-7 7` para sozinho após 07/07 (só voltaria em julho/2027).
- Para apagar de vez: `delete_trigger` com `trigger_id="trig_01TnGU94T4ZjRt3j4tBbzxZm"`.
- Para mudar horário/janela: recriar o trigger com outro `cron_expression`
  (lembre: BRT = UTC-3, então 10h BRT = `13` na hora do cron).
