# HTTP API

Three services, all JSON. In Docker Compose only the assistant is published on all interfaces. The schema and values services are bound to `127.0.0.1`, because a `PUT` to the values service skips the assistant's validation.

## Assistant (`bot-server`, port 7000)

### `POST /edits`

Turn a plain-English request into a validated change.

| Field | Type | Default | Notes |
|---|---|---|---|
| `text` | string, ≤ 2000 chars | required | The request, e.g. `"lower the orders cpu limit to 80%"`. |
| `apply` | strict boolean | `false` | `true` saves the change. `"yes"` or `1` are rejected, not coerced. |
| `author` | string, ≤ 100 chars | `"anonymous"` | Recorded in the audit log. Blank becomes `"anonymous"`. |

**200 response**

```json
{
  "app": "orders",
  "applied": false,
  "revision": null,
  "changes": [{"op": "replace", "path": "/workload/container/resources/cpu/limitMilliCPU", "old": 500, "new": 400}],
  "unchanged": [],
  "intent": [{"op": "replace", "path": "/workload/container/resources/cpu/limitMilliCPU", "value": "80%"}],
  "patch": [{"op": "replace", "path": "/workload/container/resources/cpu/limitMilliCPU", "value": 400}],
  "config": {"...": "the full resulting config, schema-valid"}
}
```

- `changes` lists what differs, as `old` → `new`. It can include a request clamped to a lowered limit.
- `unchanged` lists fields the request names that already have the requested value.
- `intent` is exactly what the model emitted. `patch` is what code compiled from it.
- `revision` is the audit-log id when `applied` is `true`.

**Errors** (always `{"detail": "<one line>"}`)

| Status | When |
|---|---|
| 400 | Empty input, invalid characters, no app identified, or several apps named. |
| 409 | The config changed between read and write (concurrent apply). Retry. |
| 422 | Malformed body, or the request couldn't become a valid change after 3 attempts. `detail` gives the last reason, for example a schema minimum, an ambiguous CPU value, or a value that isn't in the request. |
| 502 | The LLM or an upstream service failed. |
| 503 | Circuit breaker open, or the schema/values service is unreachable. |

### `GET /history/{app}?limit=20`

Newest-first audit entries (`limit` 1–500), each holding `id`, `timestamp`, `author`, `message`, `reverts` and `patch`.

### `POST /undo/{app}?author=NAME`

Reverts the latest change that hasn't been undone, and records the revert as a new entry. Returns 409 when there is nothing to undo.

### `GET|HEAD /health`

`{"status": "healthy" | "degraded", "circuit_breaker": "closed" | "open" | "half-open"}`. The status is `degraded` while the breaker isn't closed.

### `GET /metrics`

Prometheus metrics. See [operations.md](operations.md#metrics).

## Values service (`values-server`, port 7002)

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/{app}` | Current config. |
| `PUT` | `/{app}` | Body `{"values", "author", "message", "expected_hash"?}`. Writes atomically and appends an audit entry. Returns 409 if `expected_hash` ≠ sha256 of the current config, and 422 for `NaN`/`Infinity`. Returns `{"id": null}` when nothing changed. |
| `GET` | `/{app}/history?limit=` | As above, without the stored snapshots. |
| `POST` | `/{app}/undo?author=` | Restores the snapshot taken before the latest unreverted change. |
| `GET` | `/health` | Liveness. |

App names are alphanumeric, at most 64 characters. Anything else is a 404.

## Schema service (`schema-server`, port 7001)

`GET /{app}` returns `data/schemas/<app>.schema.json`. `GET /health` is liveness.
