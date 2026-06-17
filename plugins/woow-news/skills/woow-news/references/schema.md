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
