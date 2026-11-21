APPS = ["storefront", "ledger", "orders"]

EXTRACT_APP_SYSTEM = """You extract application names from user requests.

Available apps: storefront, ledger, orders

Return JSON: {"app": "<name>"}
- If user says "storefront" or "storefront service" → storefront
- If user says "ledger" or "ledger service" → ledger
- If user says "orders" or "orders service" → orders
- If the request is not clearly about one of these apps → none"""

EXTRACT_APP_USER = """Request: "{input}"

App name:"""

MODIFY_CONFIG_SYSTEM = """You turn a configuration change request into JSON Patch style operations. Code applies them.

Return {"operations": [...]} using:
- Change an existing field: {"op": "replace", "path": P, "value": V}
  For memory, CPU and time fields, and for relative changes, write the quantity as a string exactly as
  the user said it. Do NOT convert units or do arithmetic; code does that.
  "to 2gb" -> "2gb" | "750m" -> "750m" | "1.5 cores" -> "1.5 cores" | "90 seconds" -> "90s"
  "to 80%" -> "80%" | "up by 10%" -> "+10%" | "by 512mb" -> "+512mb" | "down by 1" -> "-1"
  "double" -> "200%" | "halve" -> "50%"
- A new env var: {"op": "add", "path": "<the envs path>", "key": "NAME", "value": "text"}
- Delete a field: {"op": "remove", "path": P}

Where fields live (every app uses the same layout):
- CPU and memory: /workload/container/resources/cpu|memory/limit...|request...
- Env vars: /workload/container/envs/NAME
- Health probes (liveness, readiness, startup): /workload/container/probes/...
- Public hostname and the reverse proxy (body size; connect, send, read timeouts): /ingress/...
- The cluster-internal Service port: /service/...
- Replicas, rollout strategy, grace period: /workload/...

Rules:
- Only change what the user asks for
- Paths are JSON Pointers into the current config

Example: "set storefront memory to 2gb" ->
{"operations": [{"op": "replace", "path": "/workload/container/resources/memory/limitMiB", "value": "2gb"}]}"""

# static parts first, request last: Ollama reuses the cached prompt prefix across requests
MODIFY_CONFIG_USER = """Current config:
{values}

User request: "{input}"

Return the JSON Patch:"""

RETRY_FEEDBACK = """

Your previous answer was rejected: {error}
Return corrected operations."""
