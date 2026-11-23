# Evals

The unit tests mock the LLM. The evals measure the real thing: the services from this repository, a real model, and a score on the **exact resulting config**.

- [Method](#method)
- [Results](#results)
- [What didn't work](#what-didnt-work)
- [Known misses](#known-misses)
- [Adversarial testing](#adversarial-testing)
- [Running the evals](#running-the-evals)

## Method

`evals/cases.py` holds 69 labeled requests across the three sample apps. Each label is the request, the app it should edit (or `None` if the request must be refused), and the expected value of every field that should change. A request passes only if the config after applying the patch equals the expected config exactly, or, for refusals, if the assistant answers 4xx/5xx. There's no LLM judge.

| Set | Size | Written |
|---|---|---|
| `tuned` | 30 | First. The design was iterated against it. |
| `heldout` | 18 | After the path-enum work, to check that gains generalize. |
| `heldout2` | 21 | Before the intent-operation redesign, and not run until it was finished. Fixes were later informed by its failures, so its current score isn't an unseen score. |

The labels follow Kubernetes' limit ≥ request rule: when a lowered limit crosses the current request, the request is expected to be clamped to the limit. The runner checks every label against the app's schema before it starts.

**Setup:** qwen2.5:3b (Q4_K_M) on Ollama 0.40.17, an RTX 4070 (12 GB) under WSL2, `temperature: 0`, `num_ctx` 16384. Each stage was measured on the current sample configs.

## Results

| Stage | `tuned` | `heldout` | `heldout2` | **Total** | Median | Edit prompt |
|---|---|---|---|---|---|---|
| Structured outputs; full schema in the prompt; free-text paths | 20/30 | 8/18 | 11/21 | **39/69** | 0.49 s | ~2,560 tokens |
| + regex router, request-last prompt, exact-match cache | 21/30 | 17/18 | 15/21 | **53/69** | 0.35 s | ~2,600 |
| + path enums, typed values, schema dropped from the prompt | 26/30 | 15/18 | 17/21 | **58/69** | 0.36 s | ~600 |
| + intent operations: quantity strings converted in code | 23/30 | 16/18 | 15/21 | **54/69** | 0.31 s | ~650 |
| + grounding, limit/request clamp, layout hints (current) | 28/30 | 18/18 | 15/21 | **61/69** | 0.32 s | ~780 |

How to read it:

- **The router and request-last prompt** cut LLM calls from about 2.1 to 1.2 per request. They also let Ollama's prompt cache serve most of the edit prompt (about 560 of 780 tokens in the current stage).
- **Path enums** removed invented pointers and shrank the prompt by 4×, because the schema no longer has to be in it.
- **Intent operations dipped at first.** They fixed arithmetic ("80%", "5gb"), but without grounding the model could still pad replies or pick the wrong field. Grounding and the layout hints recovered that and more.
- **Latency** is dominated by generating the reply, not reading the prompt. On this GPU a cold 2,500-token prompt takes about 0.4 s to read, and a cached one about 0.02 s.

## What didn't work

Each of these was measured and dropped:

- **Prompt rules for units and "one op per field"** (before quantity strings) made results worse. The model followed them inconsistently and broke cases that had passed.
- **Few-shot examples.** A "limit and request" example made the model edit both fields on almost every memory request.
- **Custom op names** (`set_text`, `set_number`, `add_key`). The model kept reaching for JSON Patch's `replace` and picked the wrong shape.
- **A `set_number` op with separate `amount`, `unit` and `mode` fields.** Same problem. Quantity strings inside a plain `replace` worked better.
- **Rejecting `"512m"` for memory as ambiguous.** The model writes it for half a gigabyte no matter what the prompt says, so refusing it only caused failures.

## Known misses

The current stage misses 8 of 69. Seven are **refusals with a reason** (a 422): the grounding check caught a model mistake and the request isn't applied.

- "halve the ledger cpu limit", "cut the ledger memory limit by 1gb", "increase storefront cpu limit by 25%", "lower ledger memory request to 75%": the model computed a wrong value itself (`"999MiB"`, `"750m"`…) and grounding rejected it.
- "set memory of the orders service to 3gb": the model aimed at the Service object, then padded. Refused.
- "set TENANT for orders to umbrella", "make storefront pull its image always": the model tried to add an env var instead of replacing a field. Refused.

One is a **wrong edit**: "set the orders read and send timeouts to 45 seconds" changed the readiness probe's timeout instead of the ingress proxy timeouts. It's a two-field request whose field names overlap with the probes' `timeoutSeconds`, and grounding can't catch it because the value 45 does come from the request. It's the main open problem; a per-field confirmation step or splitting multi-field requests are the next things to try.

## Adversarial testing

Ten parallel testers each took one area and probed a live stack with about 500 requests in total:

- units and quantities
- env vars
- app routing
- prompt injection
- schema boundaries
- compound and vague requests
- apply/undo under concurrency
- the HTTP contract
- the CLI
- caching and failure handling

The bugs they found are fixed and covered by tests:

- routing to an app named only inside a value (`set image to storefront:2.0` edited storefront)
- a cache key that merged `Storefront:1.0` with `storefront:1.0`
- invented env vars
- lost updates under concurrent applies
- `NaN` persisted by the values service
- 500s for requests that asked for the current value

## Running the evals

```bash
pip install -r bot-server/requirements.txt -r values-server/requirements.txt -r schema-server/requirements.txt
ollama pull qwen2.5:3b                        # Ollama on localhost:11434
make eval                                     # all 69 requests
python3 evals/run.py --set heldout2 --label my-change --ollama http://gpu-box:11434
```

The runner starts all three services on spare ports against a temp copy of `data/` (preview only, nothing is written). It puts a recording proxy between the assistant and Ollama and prints:

- accuracy per set, and every failure with the patch or error it produced
- median and max latency, and LLM calls per request
- prompt, cached and generated tokens for each LLM stage

Everything, including the model's raw intents, is written to `evals/results/<label>.json`. The command exits non-zero if any request fails.

**Adding cases:** add `(request, expected_app_or_None, {pointer: expected_value})` to a set in `evals/cases.py`. Keep a held-out set honest: write it before the change it's meant to test, and don't tune against it.
