# Usage

- [Walkthrough](#walkthrough)
- [CLI reference](#cli-reference)
- [HTTP](#http)

## Walkthrough

In about ten minutes you'll start the stack, preview a change, apply it, inspect the audit log and undo it. You need Docker with Compose v2 and about 4 GB of free RAM. An NVIDIA GPU is optional but makes requests about 10× faster.

### 1. Start the stack

```bash
cp .env.example .env
docker compose up -d
```

The first start pulls `qwen2.5:3b` (about 2 GB). Wait until the assistant is healthy:

```bash
curl -s localhost:7000/health
# {"status":"healthy","circuit_breaker":"closed"}
```

### 2. Preview a change

```bash
alias configbot="python3 $(pwd)/cli/configbot.py"
configbot "lower the orders cpu limit to 80%"
```

```
app: orders
  ~ /workload/container/resources/cpu/limitMilliCPU: 500 → 400
preview only, re-run with --apply to save
```

Nothing is saved yet. The model returned `"80%"` and code computed 80% of 500.

### 3. Apply it

```bash
configbot "lower the orders cpu limit to 80%" --apply
```

The output is the same diff, followed by `saved as revision #1`.

### 4. Read the audit log

```bash
configbot history orders
```

```
#1  2026-11-23T10:15:02+00:00  you  lower the orders cpu limit to 80%
  ~ /workload/container/resources/cpu/limitMilliCPU = 400
```

### 5. Undo it

```bash
configbot undo orders
```

```
reverted #1 as revision #2
  ~ /workload/container/resources/cpu/limitMilliCPU = 500
```

### 6. Try something the system should refuse

```bash
configbot "make orders faster"
```

The command exits with code 1 and a one-line reason. The request names no field and no value, so there's nothing grounded to change. That refusal is deliberate: see [why values must come from the request](intents.md#grounding).

## CLI reference

`cli/configbot.py` is a single file that uses only the Python standard library.

```bash
alias configbot="python3 $(pwd)/cli/configbot.py"
```

| Command | Effect |
|---|---|
| `configbot "<request>"` | Preview: print the diff, save nothing. |
| `configbot "<request>" --apply` | Save the change and print its revision. |
| `configbot history APP [--limit N]` | Audit log, newest first. `N` is 1–500, default 20. |
| `configbot undo APP` | Revert the latest change. |

`--author NAME` works with every command and may come before or after it. The default is your OS user name, or `anonymous` if there isn't one.

**Output.** `~` marks a replaced value (`old → new`), `+` an added one, `-` a removed one, and `= path (already set)` a field that already had the requested value. Colors appear only on a TTY.

**Environment.** `CONFIGBOT_URL` sets the assistant's base URL (default `http://localhost:7000`). A trailing slash is fine.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | The server answered with an error, couldn't be reached, or returned something unexpected. One-line message, no traceback. |
| 2 | Usage error (argparse). |

## HTTP

Everything the CLI does is one HTTP call to the assistant:

```bash
curl -s localhost:7000/edits -H 'Content-Type: application/json' \
  -d '{"text": "lower the orders cpu limit to 80%"}'                         # preview
curl -s localhost:7000/edits -H 'Content-Type: application/json' \
  -d '{"text": "lower the orders cpu limit to 80%", "apply": true, "author": "you"}'
curl -s localhost:7000/history/orders
curl -s -X POST "localhost:7000/undo/orders?author=you"
```

Every field, response and status code is listed in [api.md](api.md).
