# Research notes

Background research behind the design decisions in [ARCHITECTURE.md](../../ARCHITECTURE.md). Each note cites its sources; claims the notes could not verify are marked as such.

| Note | Question | Led to |
|---|---|---|
| [ollama-structured-outputs.md](ollama-structured-outputs.md) | What can Ollama's `format` field and context settings do for a two-call config editor? | Grammar-constrained output on both calls, an explicit `num_ctx`, `temperature: 0` with error feedback on retries |
| [efficiency.md](efficiency.md) | How do we make a 3B local model fast and accurate when every app has a large JSON Schema? | The eval harness, request-last prompts for Ollama's prefix cache, path enums in the grammar, dropping the schema from the prompt, deterministic routing, and moving units and arithmetic into code |

The measured outcome of each idea, including the ones that didn't work, is in the evals section of ARCHITECTURE.md.
