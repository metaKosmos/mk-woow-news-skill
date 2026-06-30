# Schema dos arquivos de estado (woow-news)

Daily Drops: `edition` = data de publicação `YYYY-MM-DD` (uma edição por dia). O campo
`date` espelha essa data (o broker preenche em `run_stage`).

## queue.json (no GCS, espelhado pro Firebase)
```json
{
  "updated_at": "2026-06-17T08:00:00-03:00",
  "editions": [
    {"edition": "2026-06-17", "date": "2026-06-17", "stage": "sent",
     "subject": "Assunto", "image_ready": true, "open_rate": 0.31}
  ]
}
```
`stage` ∈ `empty | researched | generated | ready | sent` (progresso da gaveta).

## editions/<ed>.state.json (no GCS)
```json
{
  "edition": "2026-06-17", "date": "2026-06-17", "stage": "sent",
  "subject": "Assunto", "image_ready": true, "campaign_key": "...", "preview_url": "...",
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

## settings.json (no GCS) — config mutável de envio
```json
{
  "active_list_key": "3z...", "active_list_name": "Time mK Daily Drops",
  "set_by": "joao@metakosmos.com.br", "set_at": "2026-06-30T10:00:00-03:00"
}
```
Lista-alvo do envio diário (tem precedência sobre `newsletter.yaml`). Editado por `set-list`.

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
