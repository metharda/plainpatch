# Ollama structured outputs and the context window

Research notes behind the structured-outputs change: what Ollama's `format` field does, which JSON Schema features its grammar supports, and why the default context window silently truncated this service's prompts.

## A. Ollama Structured Outputs (`format` with JSON Schema)

### Version, Endpoints, and Mechanism
* **Introduced in:** Ollama **v0.5.0** (December 6, 2024) ([Ollama Blog: Structured Outputs](https://ollama.com/blog/structured-outputs), [Ollama v0.5.0 Release](https://github.com/ollama/ollama/releases/tag/v0.5.0)).
* **Supported endpoints:**
  * Native: `POST /api/chat` and `POST /api/generate` via top-level `"format": { ... }` (or `"format": "json"` for unconstrained valid JSON) ([docs.ollama.com/capabilities/structured-outputs](https://docs.ollama.com/capabilities/structured-outputs), [docs/api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)).
  * OpenAI-compatible: `POST /v1/chat/completions` (and `/v1/responses`) via `response_format: {"type": "json_schema", ...}` ([docs.ollama.com/api/openai-compatibility](https://docs.ollama.com/api/openai-compatibility)).
* **How it works:** **Grammar-constrained decoding.** Ollama's Go layer (`SchemaToGrammar` in `llama/llama.go`) calls `llama.cpp`'s C++ converter (`common/json-schema-to-grammar.cpp`) to compile the JSON Schema into a **GBNF** (GGML BNF) grammar. During token sampling, logits for any token that would violate the grammar are masked out, making syntactically invalid tokens impossible to sample ([Ollama Blog](https://ollama.com/blog/structured-outputs), [llama.cpp `json-schema-to-grammar.cpp`](https://github.com/ggml-org/llama.cpp/blob/master/common/json-schema-to-grammar.cpp)).

### JSON Schema Keyword Support (`llama.cpp` GBNF + Ollama)
Support is governed by `llama.cpp`'s `json-schema-to-grammar.cpp` ([llama.cpp `grammars/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)) plus Ollama's Go unmarshaling layer:

| Keyword(s) | Status | Behavior & Caveats |
|---|---|---|
| `type` (`object`, `array`, `string`, `number`, `integer`, `boolean`, `null`), `properties`, `required`, `items` | **Supported** | Core primitives; reliable across versions. Avoid multi-type arrays like `"type": ["string", "null"]` on older/tool paths where Go struct unmarshaling has failed ([ollama/ollama issues](https://github.com/ollama/ollama/issues)). |
| `enum`, `const` | **Supported** | Compiled to GBNF literal alternations. Large enums slow grammar compilation; pairing `enum`/`const` with sibling constraints (e.g., `pattern`) can cause sibling constraints to be ignored. |
| `minItems`, `maxItems` | **Supported** | Compiled to GBNF repetition rules (fixed in Ollama/llama.cpp patches). |
| `pattern` | **Supported (subset)** | Must generally be anchored (`^...$`). Complex regexes fail/fallback; combining `pattern` with `minLength`/`maxLength` on the same string can silently drop length bounds. |
| `additionalProperties` | **Supported (strict default)** | `llama.cpp` defaults to `additionalProperties: false` for tighter grammars; `true` is supported but has had edge cases with unescaped newlines in keys. |
| `oneOf`, `anyOf` | **Partial / Caveats** | Compiled to GBNF alternations (`\|`) in `llama.cpp`, **but cannot be mixed with sibling `properties` at the same schema level** (`llama.cpp` [#7703](https://github.com/ggml-org/llama.cpp/issues/7703)). Rejected on some Ollama cloud/tool paths and can bloat grammars. |
| `allOf` | **Partial / Fragile** | Limited merging; overlapping constraints can explode grammar size or hit repetition caps. |
| `$ref` / `$defs` | **Partial / Fragile** | Local `#/$defs/...` works in `llama.cpp` up to `MAX_REPETITION_THRESHOLD`; remote `$ref` URLs are **not** supported. Deep/recursive refs or third-party wrappers stripping `$defs` frequently break — **flatten schemas** when possible. |
| Empty schema `{}` ("any JSON value") | **Version-dependent** | Intended to match any JSON value, but triggered 400/500 errors in some older `llama.cpp` builds — **verify on your installed Ollama version** (fallback union shown in §D). |
| `uniqueItems`, `not`, `if`/`then`/`else`, `dependentSchemas`, `multipleOf` | **Unsupported** | Silently ignored or rejected because stateless GBNF grammars cannot enforce them ([llama.cpp `grammars/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)). |

### Known Limitations & Bugs
* **Root schema should be `"type": "object"`:** Root-level `"type": "array"` or bare `"type": "string"` schemas have caused parsing failures, empty outputs (`[]`), or termination bugs across Ollama/backend versions. Always wrap arrays and enums in a top-level object.
* **Key ordering is not preserved:** Go map unmarshaling alphabetically sorts object keys before grammar generation, so property order in your schema may be reordered in output ([ollama/ollama](https://github.com/ollama/ollama/issues)).
* **Truncation on `num_predict` / `num_ctx` still produces invalid JSON:** Grammar masking does *not* prevent hitting `num_predict` mid-object; when that happens, `done_reason` is `"length"` and the JSON string is truncated/unparseable.
* **Infinite whitespace loops & backend gaps:** MLX backend builds have had issues ignoring `format` ([#16563](https://github.com/ollama/ollama/issues/16563), [#17183](https://github.com/ollama/ollama/issues/17183)) or emitting infinite whitespace until `num_predict` ([#18567](https://github.com/ollama/ollama/issues/18567)). Standard GGUF/`llama.cpp` (default for `qwen2.5:3b`) is much more reliable, though bounding `num_predict` is still good hygiene.
* **Large-schema buffer bug (historical):** Older Ollama builds had a fixed buffer limit in `SchemaToGrammar` ([#10646](https://github.com/ollama/ollama/issues/10646), later fixed to allocate dynamically).
* **Thinking-mode bugs:** Combining `think: false` with `format` broke structured output on some hybrid reasoning models ([#14850](https://github.com/ollama/ollama/issues/14850), [#15260](https://github.com/ollama/ollama/issues/15260)) — not directly applicable to `qwen2.5:3b`, which is non-thinking, but relevant if you upgrade models later.

### Official Best Practices
From [ollama.com/blog/structured-outputs](https://ollama.com/blog/structured-outputs) and [docs.ollama.com/capabilities/structured-outputs](https://docs.ollama.com/capabilities/structured-outputs):
1. **Still describe the expected JSON / fields in the prompt** — grammar constrains *valid tokens*, not semantic intent; telling the model what each field means prevents it from filling valid syntax with nonsense.
2. **Set `"temperature": 0`** for more deterministic, consistent outputs.
3. **Generate schemas from Pydantic (`Model.model_json_schema()`)** and parse/validate back with `Model.model_validate_json(...)`.

### Exact Request Example (`POST /api/chat`)
```json
{
  "model": "qwen2.5:3b",
  "stream": false,
  "keep_alive": "30m",
  "messages": [
    {
      "role": "system",
      "content": "Classify which app the user request refers to. Return JSON with an \"app\" field set to one of: storefront, ledger, orders."
    },
    {
      "role": "user",
      "content": "Increase the max lobby wait time before expanding skill rating range."
    }
  ],
  "format": {
    "type": "object",
    "properties": {
      "app": {
        "type": "string",
        "enum": ["storefront", "ledger", "orders"]
      }
    },
    "required": ["app"],
    "additionalProperties": false
  },
  "options": {
    "temperature": 0,
    "num_ctx": 32768,
    "num_predict": 32
  }
}
```

---

## B. Context Window (`num_ctx`) & Risk to Your ~5k-Token Prompt

### Historical vs. Current Defaults
* **Historically:** Ollama defaulted to a static **`2048`** tokens, later raised to a static **`4096`** tokens. *(Unverified detail: I could not directly verify from primary release notes the exact minor release tag that shifted the static default from `2048` to `4096`.)*
* **Today (since ~v0.15.5):** Ollama dynamically selects the default `num_ctx` at server startup based on available VRAM ([docs.ollama.com/context-length](https://docs.ollama.com/context-length), [Ollama v0.15.5](https://github.com/ollama/ollama/releases/tag/v0.15.5)):
  * **`< 24 GiB` VRAM** (reported as `< 23 GiB` usable after GPU overhead in code): **`4,096`** tokens (4k)
  * **`24–48 GiB` VRAM:** **`32,768`** tokens (32k)
  * **`≥ 48 GiB` VRAM:** **`262,144`** tokens (256k)
* **Server-wide override:** `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (note: `OLLAMA_NUM_CTX` is **ignored**).
* **Verify active runtime context:** Run `ollama ps` and check the **`CONTEXT`** column ([docs.ollama.com/context-length](https://docs.ollama.com/context-length)).

### What Happens When a Prompt Exceeds `num_ctx`
* **Silent truncation — NO HTTP error is returned.** Ollama returns `200 OK`, logs `msg="truncating input prompt"` on the server, and drops tokens from the **beginning** of the prompt while keeping the tail ([ollama/ollama issues #4764 / #11283](https://github.com/ollama/ollama/issues/11283)). This can silently slice off your system instructions or the first part of your JSON Schema.
* **Client-side detection:** Check `prompt_eval_count` in the response JSON ([docs/api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)). If `prompt_eval_count` is capped at or very near your configured `num_ctx`, truncation occurred.

### Setting `num_ctx` Per Request — and the Reload Trap
* Pass `"options": {"num_ctx": 32768}` in `POST /api/chat` (or bake `PARAMETER num_ctx 32768` into a custom Modelfile, or set `OLLAMA_CONTEXT_LENGTH=32768` on the server).
* **Critical trap for your 2-call pipeline:** In Ollama's scheduler (`server/sched.go`), `needsReload` compares the loaded runner's options against each incoming request ([ollama/ollama #9749](https://github.com/ollama/ollama/issues/9749), [#6148](https://github.com/ollama/ollama/issues/6148), [#18950](https://github.com/ollama/ollama/issues/18950)). **If Call 1 omits `num_ctx` (e.g., defaults to 4096) and Call 2 sends `num_ctx: 32768`, Ollama will unload/reload the model and reallocate the KV cache on every single user request.** Always send the **exact same `num_ctx`** on both Call 1 and Call 2 (or configure it globally via `OLLAMA_CONTEXT_LENGTH` / Modelfile).

### `qwen2.5:3b` Limits & Your ~5k-Token Prompt Risk
* **`qwen2.5:3b` native maximum context:** **32,768 tokens** (3.09B params, 36 layers, GQA with 2 KV heads — relatively light KV cache) ([ollama.com/library/qwen2.5](https://ollama.com/library/qwen2.5), [Qwen2.5-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)).
* **Is your ~5k-token prompt at risk? YES — critically so:**
  1. **Immediate truncation risk:** On any host with `< 24 GiB` VRAM (typical for running a 3B model locally) or older Ollama, the default `num_ctx` is **4,096** (or 2,048). Without explicitly raising `num_ctx`, your ~4.5k-token indented schema + ~0.5k-token config is being **silently truncated right now**.
  2. **Ceiling pressure even at 32k:** With a `~4.5k` schema + `~0.5k` config + system/user prompt (`~0.5k`) + `num_predict: 8192`, total context demand is **~13.5k tokens**, and `num_predict` alone is most of it. Any schema growth or retry message history eats into the rest. Minifying the JSON Schema (no whitespace indentation) and capping `num_predict` to `2048–4096` buys substantial headroom.

---

## C. Other Relevant Ollama Features

| Feature | Behavior & Verdict for Your Service |
|---|---|
| **Tool calling (`tools`) vs. `format`** | **Use `format`, not `tools`.** In Ollama, `qwen2.5:3b` tool calling relies on chat-template prompting + output parsing (not grammar-constrained decoding), so a 3B model can skip invoking the tool, reply in prose, or hallucinate parameter types. Your pipeline never needs dynamic tool choice—both calls always expect a fixed response shape. |
| **`keep_alive`** | Top-level param (default `"5m"`). Set `"keep_alive": "30m"` or `-1` (never unload) so `qwen2.5:3b` stays resident in memory for your FastAPI service ([docs/api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)). Combine with identical `num_ctx` on Call 1 and Call 2 to avoid thrashing. |
| **`seed`** | Inside `options` (e.g., `"seed": 42`) for reproducible sampling ([docs/api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)). **Caveat:** Do **not** blindly retry with the same `seed` + `temperature: 0` + identical prompt—you will reproduce the exact same bad patch. On retries, append the validation error to `messages` (and optionally omit/increment `seed` or raise `temperature` slightly, e.g., `0.1–0.2`). |
| **`think`** | Top-level param (`true`/`false`/effort string) for reasoning models ([docs/api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)). **Do NOT pass `think` (not even `"think": false`) to `qwen2.5:3b`:** Ollama rejects non-thinking models with **HTTP 400** (`"qwen2.5:3b" does not support thinking`) ([ollama/ollama issues](https://github.com/ollama/ollama/issues)). |
| **OpenAI-compatible `response_format`** | `POST /v1/chat/completions` supports `response_format: {"type": "json_schema", "json_schema": {"name": "...", "strict": true, "schema": {...}}}` ([docs.ollama.com/api/openai-compatibility](https://docs.ollama.com/api/openai-compatibility)). **Stick with native `/api/chat`:** `/v1/chat/completions` historically drops or does not expose per-request `num_ctx` and `keep_alive` ([ollama/ollama #4028](https://github.com/ollama/ollama/issues/4028)), forcing server-level/Modelfile workarounds. |
| **Official `ollama` Python SDK vs. raw `httpx`** | `ollama.AsyncClient` wraps `httpx.AsyncClient` underneath, accepts `timeout=httpx.Timeout(...)`, and integrates cleanly with `format=Model.model_json_schema()` ([ollama/ollama-python](https://github.com/ollama/ollama-python)). However, `format` is just a plain JSON dict on `/api/chat`. **If you already have a clean `httpx.AsyncClient` managed via FastAPI `lifespan`** (with connection limits, timeouts, and observability), keeping raw `httpx` avoids an extra wrapper layer; if you want typed responses and less boilerplate, `ollama.AsyncClient` is a thin, well-supported choice. |

---

## D. Concrete Recommendations

### Call 1 — App Classification
* **Schema (`format`):** Wrap the enum in a top-level `"type": "object"` (avoid bare `"type": "string"` at root):
  ```json
  {
    "type": "object",
    "properties": {
      "app": {
        "type": "string",
        "enum": ["storefront", "ledger", "orders"]
      }
    },
    "required": ["app"],
    "additionalProperties": false
  }
  ```
* **Options & top-level params:**
  * `keep_alive`: `"30m"` (or `-1`)
  * `options`: `{"temperature": 0, "num_ctx": 32768, "num_predict": 32}`
  * Why `num_ctx: 32768` on a tiny prompt? **To match Call 2** so Ollama never reloads the model between Call 1 and Call 2.
  * Why `num_predict: 32`? `{"app":"orders"}` is ~7 tokens; a tight cap prevents runaway whitespace generation.

### Call 2 — RFC 6902 JSON Patch Generation
* **Top-level array or object wrapper?** **Wrap in a top-level object** (`{"operations": [...]}`). Top-level `"type": "array"` has documented parsing/termination issues across Ollama/backend versions.
* **Recommended `format` schema (flat & maximum `qwen2.5:3b` / `llama.cpp` compatibility):**
  ```json
  {
    "type": "object",
    "properties": {
      "operations": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "properties": {
            "op": {
              "type": "string",
              "enum": ["add", "remove", "replace", "move", "copy", "test"]
            },
            "path": {
              "type": "string"
            },
            "value": {},
            "from": {
              "type": "string"
            }
          },
          "required": ["op", "path"],
          "additionalProperties": false
        }
      }
    },
    "required": ["operations"],
    "additionalProperties": false
  }
  ```
  * **Verify `"value": {}` on your Ollama version:** In RFC 6902, `value` can be any JSON type (string, number, boolean, null, object, array). Empty schema `{}` matches any JSON value in recent `llama.cpp`, but errored in older versions. If your Ollama build rejects `{}`, replace `"value": {}` with:
    ```json
    "value": {
      "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {"type": "object", "additionalProperties": true},
        {"type": "array"}
      ]
    }
    ```
  * **Why flat over `oneOf` per op?** While you *could* make `items` a pure `oneOf` of 3 sub-schemas (`add`/`replace`/`test` requiring `value`; `move`/`copy` requiring `from`; `remove` requiring neither — never mixing `oneOf` with sibling `properties`), `oneOf` is more fragile across Ollama versions and can hurt 3B model quality. Enforcing per-op field presence in Python/Pydantic is trivial and rock-solid.
  * **Why omit `pattern: "^/.*"` on `path`?** RFC 6902 allows `""` (empty string) for the root document, and keeping `path` unconstrained in GBNF avoids regex-to-GBNF quirks; validate JSON Pointer syntax in Python.
  * **Prompt tip:** Still instruct the model to return `{"operations": [...]}` in RFC 6902 format, and minify the 15–18 KB schema JSON before embedding it to save tokens.
* **Options & top-level params:**
  * `keep_alive`: `"30m"` (or `-1`)
  * `options`: `{"temperature": 0, "num_ctx": 32768, "num_predict": 2048}` (or `4096` if large patches occur — lower than `8192` to preserve headroom inside `qwen2.5:3b`'s 32k context ceiling and cap worst-case latency).
  * Also set `OLLAMA_CONTEXT_LENGTH=32768` on the Ollama server (or bake `PARAMETER num_ctx 32768` into a custom Modelfile) as defense in depth.

### Safeguards: What to Remove vs. Keep (and Add)

| Safeguard | Recommendation | Reason |
|---|---|---|
| **Regex extraction / markdown fence stripping** | **Remove** (just `json.loads`) | Grammar-constrained decoding prevents prose and ```` ```json ```` wrappers. |
| **Call 1 enum membership retry loop** | **Remove** (keep simple check on `done_reason == "stop"`) | `enum` grammar forces one of the 3 exact strings; only retry on transport error or `done_reason != "stop"`. |
| **`done_reason == "stop"` check** | **ADD (required)** | If `done_reason == "length"`, output hit `num_predict`/`num_ctx` and is truncated invalid JSON. |
| **`prompt_eval_count` truncation check** | **ADD (required)** | Assert/log warning if `prompt_eval_count >= num_ctx - 512` to catch silent prompt truncation immediately. |
| **`json.loads` + op-level Pydantic validation** | **Keep** | Catches truncation (`done_reason == "length"`), backend bugs, and per-op RFC 6902 rules (`add`/`replace`/`test` need `value`; `move`/`copy` need `from`; valid JSON Pointer `path`). |
| **`jsonpatch` apply check** | **Keep** | Grammar cannot verify that `path` / `from` exist in the current ~1.5 KB config. |
| **Target 15–18 KB JSON Schema validation** | **Keep** | `format` only constrains the *patch structure*, not that the post-patch config satisfies the app's domain schema. |
| **Call 2 retries (up to 3x)** | **Keep — with error feedback** | Still needed for semantic errors (bad paths, schema violations). Pass the specific `jsonpatch`/`jsonschema` error back in a follow-up user turn on retry (and don't lock the same `seed` with `temperature: 0` without changing the prompt). |
