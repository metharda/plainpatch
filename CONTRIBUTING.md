# Contributing

Thanks for taking the time. Setup, checks and conventions are in [docs/development.md](docs/development.md):

```bash
make lint && make test     # ruff + unit tests (the LLM is mocked)
make eval                  # real-model evals; needs Ollama with qwen2.5:3b
```

If a change affects what the model sees or how its output is handled, put the `make eval` numbers from before and after in the pull request.

Report security issues privately; see [docs/security.md](docs/security.md).
