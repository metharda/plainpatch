# Operations

- [Configuration](#configuration)
- [Monitoring](#monitoring)
- [Metrics](#metrics)
- [Errors and what to do](#errors-and-what-to-do)

## Configuration

### Environment variables (`.env`)

Copy `.env.example` to `.env` and adjust.

| Variable | Default | Used by |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:3b` | Model the assistant calls and `ollama-init` pulls. |
| `LLM_NUM_CTX` | `16384` | Context window sent with every LLM call. It must fit the config plus the reply; edit prompts are about 800 tokens. |
| `BOT_PORT` / `SCHEMA_PORT` / `VALUES_PORT` | `7000` / `7001` / `7002` | Published ports. |
| `OLLAMA_PORT` | `11434` | Ollama API port. |
| `SCHEMA_URL` / `VALUES_URL` / `OLLAMA_URL` | compose service URLs | How the assistant reaches the other services. |
| `APP_UID` / `APP_GID` | `1000` | User the values service writes `data/values` as. On Linux, set these to `id -u` / `id -g`. |
| `OLLAMA_MEMORY_LIMIT` / `OLLAMA_MEMORY_RESERVATION` | `16G` / `8G` | Container memory for Ollama. |
| `PROMETHEUS_PORT` / `GRAFANA_PORT` / `GRAFANA_ADMIN_PASSWORD` | `9090` / `3000` / `admin` | Monitoring profile. |

### Assistant flags (`bot-server/app.py`)

| Flag | Default |
|---|---|
| `--listen` | `0.0.0.0:7000` |
| `--ollama-url` | `http://ollama:11434` |
| `--model` | `qwen2.5:3b` |
| `--schema-url` | `http://schema-server:7001` |
| `--values-url` | `http://values-server:7002` |
| `--num-ctx` | `16384` |

The schema and values services take `--listen` plus `--schema-dir` and `--values-dir`.

### Fixed LLM settings

Every call sends `temperature: 0`, `keep_alive: 30m`, the shared `num_ctx`, and a `format` JSON Schema. The app call uses `num_predict: 32` and the edit call `num_predict: 256`. Why these values: [design](intents.md#llm-settings).

### Compose profiles

| Profile | Adds | Command |
|---|---|---|
| default | ollama, ollama-init, schema-server, values-server, bot-server | `docker compose up -d` |
| `monitoring` | Prometheus, Grafana | `docker compose --profile monitoring up -d` |
| `gateway` | nginx on port 80 | `docker compose --profile gateway up -d` |

## Monitoring

```bash
docker compose --profile monitoring up -d     # Prometheus :9090, Grafana :3000 (admin / admin)
docker compose --profile gateway up -d        # nginx on :80, rate limited, /values/ is read-only
```

Grafana provisions the **plainpatch – Services Dashboard** automatically. The top rows show HTTP traffic for all three services. The **LLM** row shows:

- LLM calls per second and p95 latency, by stage (`app`, `edit`)
- prompt tokens versus tokens served from Ollama's prompt cache
- the share of requests that skipped the LLM, via the regex router and the patch cache
- rejected attempts by reason, and attempts per accepted edit

What to look for:

- **Cached tokens far below prompt tokens:** the prompt prefix isn't being reused. Check that nothing variable was added before the request.
- **A spike in `edit_rejections_total{reason="schema"}`:** users are asking for values the schema forbids. The 422 details say which.
- **High `llm_call_seconds` on the first request only:** that's the model load. The startup warm-up should normally absorb it, so check the assistant log for `Model warmed up`.

All metric names are listed under [Metrics](#metrics).

## Metrics

Every service exposes HTTP metrics (`http_requests_total`, `http_request_duration_seconds`, …) through `prometheus-fastapi-instrumentator`. The assistant adds the following metrics.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `llm_calls_total` | counter | `stage` = `app` \| `edit` | LLM calls made. |
| `llm_call_seconds` | histogram | `stage` | Wall time per LLM call. |
| `llm_tokens_total` | counter | `kind` = `prompt` \| `cached` \| `generated` | Tokens Ollama processed. `cached` came from its prompt cache. |
| `app_routing_total` | counter | `method` = `regex` \| `llm` | How the target app was chosen. |
| `patch_cache_lookups_total` | counter | `result` = `hit` \| `miss` | Exact-match patch cache. |
| `edit_attempts` | histogram | — | LLM attempts needed per accepted edit (1–3). |
| `edit_rejections_total` | counter | `reason` = `invalid_operation` \| `invalid_json` \| `bad_patch` \| `schema` | Why an attempt was rejected and retried. |

Useful queries (all in the Grafana "LLM" row):

```promql
sum by (kind) (rate(llm_tokens_total[5m]))                                   # prompt vs cached tokens
sum(rate(app_routing_total{method="regex"}[5m])) / sum(rate(app_routing_total[5m]))   # calls avoided
sum(rate(edit_attempts_sum[5m])) / sum(rate(edit_attempts_count[5m]))          # attempts per edit
```

## Errors and what to do

| Response | Likely cause | Fix |
|---|---|---|
| `422 Could not produce a valid change …: <reason>` | The request asks for something invalid (below a schema minimum, a fraction of a replica, an ambiguous CPU number, a value that isn't in the request…) or the model couldn't express it in 3 attempts | Read the reason, then rephrase with the exact field and value |
| `409 The config changed since it was read` | Two applies to the same app raced | Retry; the request is recomputed against the new config |
| `502 LLM unavailable` | Ollama not ready or the model isn't pulled | `docker compose logs ollama ollama-init`; wait for the pull to finish |
| `503 … circuit open` | Repeated LLM failures; `/health` says `degraded` | Wait 30 s; one probe request is let through to test recovery |
| `400 Could not identify app` | The request names no app, or names one only inside a value | Name the app: `orders`, `storefront` or `ledger` |
| Writes fail with permission errors (Linux) | `data/values` isn't writable by the container user | Set `APP_UID` / `APP_GID` in `.env` to `id -u` / `id -g` |
