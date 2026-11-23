# Intents, quantities and grounding

How the assistant turns what the model says into a change it can trust, and why each check exists. The components are described in [architecture.md](architecture.md), the measurements in [evals.md](evals.md), and the background reading in [research/](research/).

## The core split

A 3B model running locally reads requests well and computes badly. Asked for final values, it wrote "5gb" as 5242 and "50% of 500" as 750, and it occasionally invented a JSON Pointer that looked plausible. Prompting harder didn't fix that. What fixed it was moving each job to whatever does it reliably:

| Job | Done by | Why |
|---|---|---|
| Understand the request, pick fields | the model, under a grammar | Language understanding is the only part that needs a model. |
| Restrict paths and value types | Ollama's `format` (JSON Schema → grammar) | The model can't produce a path that doesn't exist. |
| Convert units, do arithmetic | `intents.py` | Exact and testable. |
| Check that values came from the user | `intents.py` grounding | Turns vague or invented edits into a refusal with a reason. |
| Check the result is valid | JSON Schema + `check_limits` | The app's own contract, plus rules the schema can't express. |

## The grammar

`intent_format(values)` builds the edit call's `format` schema from the *current* config:

```
{"operations": [ ≤8 of:
   {"op": "replace", "path": enum(text fields),   "value": string}
   {"op": "replace", "path": enum(flag fields),   "value": boolean}
   {"op": "replace", "path": enum(number fields), "value": number | quantity string}
   {"op": "add",     "path": enum(env maps), "key": string, "value": string}   # all required
   {"op": "remove",  "path": enum(existing leaves)} ]}
```

Each detail came from a measured failure:

- **Path enums.** With free-text paths, the model invented pointers.
- **Value types pinned per path.** With one loose `value`, it wrote `"8080"` into ports and `"none"` into numbers.
- **JSON Patch op names.** Custom names (`set_text`, `set_number`) made it pick the wrong op, for example adding an env var called `IMAGE` instead of replacing the image.
- **`add` as its own shape with `key` required.** When `key` was optional, the model left it out and overwrote the whole `envs` map.
- **`maxItems: 8`.** This bounds the rare degenerate loop inside the grammar itself.

Because the paths are in the grammar, **the app's JSON Schema isn't in the prompt**. It's used only for validation, which keeps the edit prompt at about 800 tokens instead of 2,500.

## Quantities

Number fields accept what the user wrote. The field's suffix decides the conversion:

| Suffix | Examples |
|---|---|
| `…MiB` | `1.5gb` → 1536, `512Mi` → 512, `75%` of current, `+256mb` |
| `…MilliCPU` | `750m` → 750, `1.5 cores` → 1500, `2` → 2000 (bare ≤ 64 means cores, ≥ 100 means millicores, 65–99 is refused as ambiguous) |
| `…Seconds` | `90s`, `2 minutes` → 120, `+30s` |
| other | `5`, `+1`, `200%` |

- **Signs.** A sign means "relative" only when the request is worded that way ("down by 1"). "Set replicas to -1" stays an absolute −1, which the schema then refuses.
- **Rounding.** Integer fields round to the nearest whole number, with ties going away from the current value, so a requested decrease can never round back into a no-op.
- **Fractions.** An absolute fraction on a whole-number field is refused.

## Grounding

Everything the model writes must trace back to the request:

- **Text** values must appear in it.
- **Numbers** must follow from its quantities applied to the current value: the amount itself, ± it, a percentage of or by it, or a doubling or halving. So `"1875m"` for "+25%" of 1500 passes, while `"750m"` for "75%" of 4096 is caught.
- **New env vars** need an env/variable mention, a key named as a whole word, and a non-empty value that isn't the key.
- **Unrequested edits to `…request…` fields** are dropped, and so is padding that sets an unrelated field to its current value.

When a check fails, the exact message goes back to the model as feedback, and at temperature 0 a plain resend would repeat the mistake. After three attempts the request gets a 422 carrying the last reason.

## Kubernetes rules the schema can't express

A limit below its request is invalid. When a lowered limit crosses a request the user didn't mention, the request is clamped to the limit, and the clamp appears in `changes`. An explicit request above the limit is refused.

## Routing

The app is chosen by a regex when the request names exactly one app as a target. Names glued to a value don't count (`storefront:2.0`, `orders.example.com`, `storefront-lobby`, `=orders`), and neither does "to <app>" after a setting verb ("set TENANT to orders"). Otherwise the model picks from `{"app": enum[storefront, orders, ledger, none]}`. The `none` option exists so an unrelated request isn't forced onto an app.

## LLM settings

- **`temperature: 0`**, which Ollama recommends for structured output.
- **One `num_ctx` (16384) on every call and the warm-up.** On GPUs under 24 GB, Ollama otherwise defaults to 4096 and silently drops the start of longer prompts, and a different value per call makes it reload the model.
- **The request goes last** in the prompt, so Ollama's prompt cache reuses the static prefix. In the evals, about 70% of the edit prompt is served from the cache.
- **`num_predict: 256`** for edits. A reply cut off at the limit counts as a failed attempt.
- **Ollama's `prediction aborted` errors** (a token-repeat loop) count as a bad answer and are retried with feedback, not treated as an outage.

## What it costs

Each request makes at most one edit call in the common case, plus an app call only when the regex can't decide. An exact repeat of a request against the same config and schema is served from an in-process cache with no LLM call. On an RTX 4070 the median request takes about 0.4 s. See [evals.md](evals.md).
