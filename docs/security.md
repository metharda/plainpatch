# Security

## Reporting

Please report vulnerabilities privately through GitHub's **Report a vulnerability** button (Security → Advisories), not in a public issue.

## Threat model

plainpatch is meant to run on a trusted network, next to the configs it edits.

- **Prompt injection.** User text reaches the model, so treat model output as untrusted. Nothing it emits is written without passing [`compile_intents()`](../bot-server/intents.py):
  - paths limited to existing ones
  - values grounded in the request
  - units and arithmetic done in code
  - full JSON Schema validation

  An injected instruction can at most propose a change the user could have requested directly, and it's shown as a preview first.
- **Direct writes.** The values service doesn't validate against schemas. Compose binds it to `127.0.0.1`, and the nginx gateway allows only `GET` on `/values/`.
- **No authentication.** The assistant has no auth. `author` is self-reported and is an audit hint, not an identity. Put the gateway behind your own auth if more than one person can reach it.
- **Resource use.** Inputs are capped at 2000 characters, and the gateway rate-limits to 10 requests/s per IP.
