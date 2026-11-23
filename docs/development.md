# Development

- [Repository layout](#repository-layout)
- [Working on it](#working-on-it)
- [Adding an app](#adding-an-app)
- [Recording the demos](#recording-the-demos)

## Repository layout

The code map, with what each directory owns, is in [architecture.md](architecture.md#code-map).

## Working on it

### Set up

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r bot-server/requirements.txt -r values-server/requirements.txt -r schema-server/requirements.txt -r cli/requirements.txt
```

### Check your change

```bash
make lint          # ruff on every service, the CLI and the evals
make test          # unit tests (the LLM is mocked)
make eval          # real-model evals; needs Ollama with qwen2.5:3b
```

- Logic belongs in `bot-server/intents.py` when it can be a pure function. That's where it's easiest to test.
- If a change affects what the model sees or how its output is handled, run `make eval` before and after, and put both numbers in the pull request.
- Keep the [invariants](architecture.md#invariants). If a change needs to break one, say why in the pull request and update [architecture.md](architecture.md#design-decisions).

## Adding an app

1. **Write the schema** as `data/schemas/<app>.schema.json` (JSON Schema 2020-12). Use `additionalProperties: false` so a typo can't add a field, and set real bounds (`minimum`, `maximum`, `enum`, `pattern`). The validator is the last line of defense.
2. **Write the current values** as `data/values/<app>.value.json`. They must validate against the schema.
3. **Name fields by unit.** Field names decide how quantities convert:

   | Suffix | Unit | Accepts |
   |---|---|---|
   | `…MiB` | mebibytes | `512mb`, `1.5gb`, `512Mi`, `2 GB`, `75%`, `+256mb` |
   | `…MilliCPU` | millicores | `750m`, `1.5 cores`, `2` (≤ 64 means cores), `80%`, `-250m` |
   | `…Seconds` | seconds | `90s`, `2 minutes`, `+30s` |
   | anything else | plain number | `5`, `+1`, `200%` |

   Pair limits and requests as `limitX` / `requestX` in the same object. The limit ≥ request rule and request clamping then work automatically.
4. **Put environment variables in an `envs` object.** That's the only map the model may add keys to.
5. **Register the app name** in `APPS` in `bot-server/prompts.py`, and add a line to `EXTRACT_APP_SYSTEM` describing how users refer to it.
6. **Restart** the assistant and schema service, then add a few labeled requests for the new app to `evals/cases.py` and [run the evals](evals.md#running-the-evals).

## Recording the demos

The GIF in the README is recorded from a real session with [VHS](https://github.com/charmbracelet/vhs) v0.12.1. `docs/demo/hero.tape` types real commands into the CLI against a running stack with a real model; nothing is mocked. `_style.tape` holds the shared look (JetBrains Mono 16, 1200×720, Catppuccin Mocha). `prepare.sh` starts the three services on a fresh copy of `data/`, and `shrink.sh` re-encodes the GIF at 20 fps with a 64-colour palette.

```bash
docs/demo/prepare.sh                     # needs Ollama with qwen2.5:3b
vhs docs/demo/hero.tape && docs/demo/shrink.sh
```

**Check the GIF** before committing it: look at the last frame and a contact sheet for wrapped lines, leaked setup commands or errors.

```bash
ffmpeg -v error -sseof -1 -i docs/demo/hero.gif -update 1 last.png
ffmpeg -v error -i docs/demo/hero.gif -vf "fps=1/2,scale=600:-1,tile=4x4" -frames:v 1 sheet.png
```
