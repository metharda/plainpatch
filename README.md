# AI-Assisted Application Configuration Tool

This project provides a **local, AI-driven configuration management system** that allows users to modify application configuration values using **natural language**.

Instead of manually editing JSON files, users can send plain-English requests such as:

- "set ledger service memory to 1024mb"
- "set TENANT env to globex for orders service"
- "lower cpu limit of storefront service to 80%"

The system understands the request, determines **which application is being referenced**, validates changes against the **application's JSON Schema**, updates the **current values JSON**, and returns the modified configuration.

### Highlights

- **Surgical edits with JSON Patch.** The LLM returns only the operations it wants to make (RFC 6902) instead of rewriting the whole config. That is faster, more reliable on a small local model, and nothing outside the patch can change by accident.
- **Diff-first.** Every response includes a readable list of `changes` (`path: old → new`), so you see exactly what will change before anything is saved.
- **Apply, audit, undo.** Add `"apply": true` to save a change. Every write is logged with author, message and patch, and any change can be rolled back with one call.
- **Terminal client.** `configbot "lower storefront cpu to 80%" --apply` gives you a colored diff in your shell. It uses only the Python standard library.

---

## High-Level Architecture

The system is composed of **three independent services**, each with a single responsibility:

### 1. Schema Service (`:7001`)
- Serves **JSON Schemas** for applications
- Schemas define the **allowed structure, fields, and constraints**
- Each application is identified by a `app_name`

### 2. Values Service (`:7002`)
- Serves the **current configuration values** for applications
- Values are stored separately from schemas
- Uses the same `app_name` to link values to schemas

### 3. Bot Service (`:7000`)
- Accepts **natural language user input**
- Uses a **local LLM (via Ollama)** to:
  - Identify which application the user wants to modify
  - Understand the intended change
  - Apply the change **safely and structurally** to the values JSON
- Returns the **updated values JSON** to the caller

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** (v2+)
- **Python 3.12+** (for local testing without Docker)
- **~4GB RAM** available for Ollama LLM (qwen2.5:3b)

### Environment Setup

```bash
# Copy the example env file
cp .env.example .env

# Start core services (builds + starts Ollama, schema-server, values-server, bot-server)
docker compose up -d

# Verify all services are healthy
docker compose ps
```

### Test Commands

Every response looks like this (shortened):

```json
{
  "app": "ledger",
  "applied": false,
  "revision": null,
  "changes": [
    {"op": "replace", "path": "/workload/container/resources/memory/limitMiB", "old": 2048, "new": 1024}
  ],
  "patch":  [{"op": "replace", "path": "/workload/.../memory/limitMiB", "value": 1024}],
  "config": { "...": "full updated values, schema-validated" }
}
```

```bash
# Memory change
curl -X POST http://localhost:7000/edits \
  -H "Content-Type: application/json" \
  -d '{"text": "set ledger service memory to 1024mb"}'

# Environment variable
curl -X POST http://localhost:7000/edits \
  -H "Content-Type: application/json" \
  -d '{"text": "set TENANT env to globex for orders service"}'

# CPU limit
curl -X POST http://localhost:7000/edits \
  -H "Content-Type: application/json" \
  -d '{"text": "lower cpu limit of storefront service to 80%"}'
```

### Apply, History & Undo

Requests are **preview-only** by default. To save a change:

```bash
curl -X POST http://localhost:7000/edits \
  -H "Content-Type: application/json" \
  -d '{"text": "set ledger service memory to 1024mb", "apply": true, "author": "arda"}'
# → "applied": true, "revision": 1

curl http://localhost:7000/history/ledger          # newest first: author, timestamp, message, patch
curl -X POST "http://localhost:7000/undo/ledger?author=arda"   # roll back the latest change
```

Writes are atomic: the new file is written to a temp file and then renamed over the old one. The audit log lives in `data/values/.history/<app>.jsonl`. Undo walks backwards one change at a time and is logged too, so the history is never rewritten.

### CLI

`cli/configbot.py` needs no install and uses only the standard library:

```bash
alias configbot="python3 $(pwd)/cli/configbot.py"

configbot "lower cpu limit of storefront service to 80%"            # preview
configbot "lower cpu limit of storefront service to 80%" --apply    # save it
configbot history storefront
configbot undo storefront
```

```
app: storefront
  ~ /workload/container/resources/cpu/limitMilliCPU: 1500 → 1200
saved as revision #4
```

It talks to the bot at `CONFIGBOT_URL` (default `http://localhost:7000`) and records your OS username as the author (override with `--author`).

---

## Local Development & Testing

### Prerequisites for Local Testing

```bash
# Create and activate virtual environment
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate

# Install all service dependencies
pip install -r schema-server/requirements.txt
pip install -r values-server/requirements.txt
pip install -r bot-server/requirements.txt
```

### Running Tests

The project uses a **layered test strategy** (test pyramid):

#### 1. Lint / Static Analysis (fastest)

```bash
# Lint all services with ruff
ruff check schema-server/ values-server/ bot-server/ cli/
```

**What it guarantees:** Code style, import order, common Python bugs, naming conventions.

#### 2. Unit Tests (fast — ~2s per service)

```bash
# Run all unit tests
cd schema-server && python -m pytest tests/ -v
cd values-server && python -m pytest tests/ -v
cd bot-server && python -m pytest tests/ -v
cd cli && python -m pytest tests/ -v

# Or use Makefile shortcut
make test
```

**What it guarantees:**
- Schema/Values services: health endpoints, file serving, 404/500 error handling, input validation, path traversal protection
- Values service: atomic writes, audit log, multi-step undo, nothing-to-undo handling
- Bot service: input validation, circuit breaker logic, exponential backoff, JSON extraction, full `/edits` flow (with mocked LLM), JSON Patch parsing/fallbacks, apply flow, history/undo proxying, prompt injection handling
- CLI: diff rendering, argument routing

#### 3. Unit Tests with Coverage

```bash
make test-coverage
```

**What it guarantees:** Same as unit tests, plus visibility into untested code paths. Coverage reports show line-by-line miss information.

#### 4. Integration Tests (requires Docker — ~2-5 min)

```bash
# Start services first
docker compose up -d --build

# Run integration test suite
bash test_integration.sh
# Or: make test-integration
```

**What it guarantees:** End-to-end service communication, health checks, schema/values serving with real data files, LLM-powered configuration modification, error handling for edge cases.

### Interpreting Test Reports

- **JUnit XML**: Generated as `test-results.xml` in each service directory (via `--junitxml` flag). CI uploads these as artifacts.
- **Coverage XML**: Generated as `coverage.xml`. Shows which lines are tested.
- **Terminal output**: `--cov-report=term-missing` shows untested line numbers directly in console.

---

## CI/CD Pipeline

The GitHub Actions pipeline (`.github/workflows/ci.yml`) runs on every push/PR to `main`:

| Stage | What | Parallelized? |
|-------|------|--------------|
| **Lint** | `ruff check` on all services + CLI | No (fast enough) |
| **Test** | `pytest` with coverage + JUnit reports (matrix: 3 services + CLI) | Yes |
| **Docker Build** | `docker compose build` + config validation | No (after lint+test) |
| **Security Scan** | Trivy vulnerability scan on all images | No (after build) |

### Artifacts

Each CI run produces:
- `test-results-{service}/test-results.xml` — JUnit test report
- `test-results-{service}/coverage.xml` — Coverage data

### Flaky Test Policy

- All tests are deterministic: randomness is seeded, time-dependent tests use controlled sleeps, LLM calls are mocked.
- If a test becomes flaky, it must be **quarantined** (marked `@pytest.mark.skip(reason="flaky: <ticket>")`) and a root-cause analysis ticket created.
- No `@pytest.mark.flaky` retries without an accompanying investigation.

---

## Common Failure Modes & Debugging

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `502 LLM unavailable` | Ollama not ready or model not pulled | Wait for `ollama-init` container to complete; check `docker logs ollama` |
| `503 Circuit breaker open` | Multiple consecutive LLM failures | Wait 30s for automatic reset; check Ollama health |
| `404 Schema/Values not found` | Wrong `app_name` (must be: storefront, ledger, orders) | Verify app name in request |
| `409 Nothing to undo` | Every change for that app has already been reverted | Check `GET /history/<app>` |
| `500 Failed to generate valid configuration` | LLM output doesn't match JSON Schema | Check bot-server logs for validation errors; may need model upgrade |
| Tests fail locally | Missing dependencies | Run `pip install -r <service>/requirements.txt` |
| Docker build fails | Stale cache | Run `docker compose build --no-cache` |

### Checking Logs

```bash
# All service logs
docker compose logs -f

# Specific service
docker compose logs -f bot-server

# Health check
curl http://localhost:7000/health
```

---

## Environment Variables

All configurable via `.env` file (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `qwen2.5:3b` | LLM model for Ollama |
| `SCHEMA_PORT` | `7001` | Schema service port |
| `VALUES_PORT` | `7002` | Values service port |
| `BOT_PORT` | `7000` | Bot service port |
| `OLLAMA_PORT` | `11434` | Ollama API port |
| `GRAFANA_PORT` | `3000` | Grafana UI port |
| `PROMETHEUS_PORT` | `9090` | Prometheus UI port |

---

## Docker Compose Profiles

| Profile | Services | Command |
|---------|----------|---------|
| *(default)* | ollama, schema-server, values-server, bot-server | `docker compose up -d` |
| `monitoring` | + Prometheus, Grafana | `docker compose --profile monitoring up -d` |
| `gateway` | + Nginx reverse proxy | `docker compose --profile gateway up -d` |
| All | Everything | `docker compose --profile monitoring --profile gateway up -d` |

---

## Makefile Commands

```bash
make help              # Show all commands
make build             # Build Docker images
make up                # Start core services
make down              # Stop all services
make test              # Run all unit tests
make test-coverage     # Run tests with coverage + JUnit reports
make test-integration  # Run integration test suite
make lint              # Run code linting (ruff)
make clean             # Remove containers, images, and caches
```

---

## License

Licensed under the [Apache License 2.0](LICENSE). Copyright 2026 Ahmet Arda ORAK. Redistributions must keep the [NOTICE](NOTICE) file.
