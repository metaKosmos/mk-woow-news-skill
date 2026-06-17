# Schema dos arquivos de estado (woow-news)

## queue.json (no GCS, espelhado pro Firebase)
```json
{
  "updated_at": "2026-06-16T08:00:00-03:00",
  "editions": [
    {"edition": "2026-w25", "date": "2026-06-16", "stage": "sent",
     "subject": "Assunto", "image_ready": true, "open_rate": 0.31}
  ]
}
```
`stage` ∈ `empty | researched | generated | ready | sent` (progresso da gaveta).

## editions/<ed>.state.json (no GCS)
```json
{
  "edition": "2026-w25", "date": "2026-06-16", "stage": "sent",
  "subject": "Assunto", "image_ready": true, "campaign_key": "...",
  "timestamps": {"researched_at": "...", "generated_at": "...", "ready_at": "...", "sent_at": "..."},
  "tokens": {"classify": {"input": 0, "output": 0}, "score": {}, "write": {}, "art_director": {}, "image": {}},
  "cost": {"per_step_brl": {}, "total_usd": 0.0, "total_brl": 0.0},
  "metrics": {"open_rate": 0.31, "click_rate": 0.05, "bounce_rate": 0.01, "fetched_at": "..."}
}
```
