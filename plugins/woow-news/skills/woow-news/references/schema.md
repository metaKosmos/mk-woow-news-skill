# Schema dos arquivos de estado (woow-news)

Daily Drops: `edition` = data de publicação `YYYY-MM-DD` (uma edição por dia). O campo
`date` espelha essa data (o broker preenche em `run_stage`).

## queue.json (no GCS, espelhado pro Firebase)
```json
{
  "updated_at": "2026-06-17T08:00:00-03:00",
  "editions": [
    {"edition": "2026-06-17", "type": "news_auto", "date": "2026-06-17", "stage": "sent",
     "subject": "Assunto", "image_ready": true, "open_rate": 0.31, "html_versions": 2}
  ]
}
```
`html_versions` = nº de versões de HTML publicadas da edição (badge do histórico no painel).
`stage` ∈ `empty | researched | generated | ready | sent` (progresso da gaveta).
`type` ∈ `news_auto | manual_html` (tipo da campanha; campo, não estágio). Edições legadas
sem `type` são lidas como `news_auto` (retrocompat).

## editions/<ed>.state.json (no GCS)
```json
{
  "edition": "2026-06-17", "type": "news_auto", "date": "2026-06-17", "stage": "sent",
  "subject": "Assunto", "image_ready": true, "campaign_key": "...", "preview_url": "...",
  "preheader": "", "list_key": "3z...",
  "timestamps": {"researched_at": "...", "generated_at": "...", "ready_at": "...", "sent_at": "..."},
  "tokens": {"classify": {"input": 0, "output": 0}, "score": {}, "write": {}, "art_director": {}, "image": {}},
  "cost": {"per_step_brl": {}, "total_usd": 0.0, "total_brl": 0.0},
  "metrics": {"open_rate": 0.31, "click_rate": 0.05, "bounce_rate": 0.01,
              "sent": 200, "delivered": 198, "opened": 62, "clicked": 10, "bounced": 2,
              "fetched_at": "..."}
}
```
As contagens absolutas em `metrics` (`sent/delivered/opened/clicked/bounced`) só aparecem
quando o relatório do ZMA as fornece; o painel usa `clicked` para o total de cliques.

Campos por campanha (usados em `manual_html`): `type`, `preheader` (preview text) e `list_key`
(lista ZMA por campanha, override do alvo global no `send`).

**Histórico de HTML** (`html_history`): cada publicação de HTML (news_auto, manual_html ou
`set-html`) grava um snapshot **imutável** em `nl/hist/<ed>/<stamp>.html` no bucket público e
anexa uma entrada aqui. O `preview_url` sempre aponta para o "latest" estável (`nl/<ed>.html`);
o histórico guarda as versões anteriores para o painel listar. Cortado nas últimas 20.
```json
  "preview_url": "https://storage.googleapis.com/mk-woow-news-public/nl/2026-06-17.html",
  "html_history": [
    {"url": ".../nl/hist/2026-06-17/20260617T101500.html", "at": "2026-06-17T10:15:00-03:00",
     "source": "manual_html", "by": "patrick@metakosmos.com.br", "stamp": "20260617T101500"},
    {"url": ".../nl/hist/2026-06-17/20260617T143000.html", "at": "2026-06-17T14:30:00-03:00",
     "source": "set_html", "by": "patrick@metakosmos.com.br", "stamp": "20260617T143000"}
  ]
```
`source` ∈ `news_auto | manual_html | set_html` (de onde veio aquela versão).

## settings.json (no GCS) — config mutável de envio
```json
{
  "active_list_key": "3z...", "active_list_name": "Time mK Daily Drops",
  "set_by": "joao@metakosmos.com.br", "set_at": "2026-06-30T10:00:00-03:00",
  "active_from_email": "patrick@metakosmos.com.br", "active_from_name": "WooW! Daily Drops",
  "sender_set_by": "joao@metakosmos.com.br", "sender_set_at": "2026-07-01T10:00:00-03:00"
}
```
Lista-alvo do envio diário (`active_list_*`, editado por `set-list`) e remetente ativo global
(`active_from_*`, editado por `set-sender`). Ambos têm precedência sobre `newsletter.yaml`.

## schedule.json (no GCS) — agendamento do envio diário
```json
{
  "enabled": false,
  "send_time": "10:00",
  "weekdays": [0, 1, 2, 3, 4, 5, 6],
  "auto_send": false,
  "until": null,
  "last_run_date": null,
  "set_by": "david@metakosmos.com.br", "set_at": "2026-06-30T10:00:00-03:00"
}
```
- `send_time`: HH:MM em **BRT**.
- `weekdays`: dias em que roda; `0=seg .. 6=dom` (`datetime.weekday()`).
- `auto_send`: `false` = modo revisão (gera e para em `ready`); `true` = dispara sozinho.
- `until`: data limite opcional `YYYY-MM-DD` (janela; ex.: piloto de 7 dias). `null` = sem fim.
- `last_run_date`: dedup — o tick "claima" o dia antes de rodar; não roda 2x no mesmo dia.

O `POST /cron/tick` (Cloud Scheduler, a cada ~15 min) lê este arquivo e roda a edição de
hoje quando dá o horário. Editado por `schedule set/on/off/auto-send`.

## sources.json (no GCS) — fontes RSS da pesquisa
```json
{
  "feeds": [
    {
      "source": "Fast Company",
      "url": "https://www.fastcompany.com/latest/rss",
      "enabled": true,
      "added_by": "patrick@metakosmos.com.br",
      "added_at": "2026-08-24T20:00:00-03:00",
      "note": "",
      "last_test": {"status": "ok", "found": 20, "kept": 6, "error": null,
                    "at": "2026-08-24T20:00:00-03:00"}
    }
  ],
  "set_by": "patrick@metakosmos.com.br", "set_at": "2026-08-24T20:00:00-03:00",
  "tested_by": "joao@metakosmos.com.br", "tested_at": "2026-08-24T21:00:00-03:00"
}
```
Lista viva das fontes, editada por `sources add|set-url|enable|disable|remove`. Tem
precedência sobre `broker/config/feeds.yaml`, que é só o seed: sem este arquivo, vale o YAML
do container. Antes de cada pesquisa, o broker escreve as fontes com `enabled: true` no
`config/feeds.yaml` do workdir, então mudança aqui vale na pesquisa seguinte, sem redeploy.

- `enabled`: `false` mantém a fonte cadastrada e fora da pesquisa.
- `last_test`: resultado do último `sources test` **daquela** fonte. `found` = itens no feed,
  `kept` = itens dentro da janela de recência, `error` = o erro real (403/404/timeout).
- `set_by`/`set_at` são de quem editou a lista; `tested_by`/`tested_at`, de quem rodou o
  último teste. São coisas diferentes e não se sobrescrevem.
