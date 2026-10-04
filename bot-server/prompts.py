APPS = ["storefront", "ledger", "orders"]

EXTRACT_APP_SYSTEM = """You extract application names from user requests.

Available apps: storefront, ledger, orders

Rules:
- Return ONLY the app name, nothing else
- If user says "storefront" or "storefront service" → storefront
- If user says "ledger" or "ledger service" → ledger
- If user says "orders" or "orders service" → orders"""

EXTRACT_APP_USER = """Request: "{input}"

App name:"""

MODIFY_CONFIG_SYSTEM = """You modify JSON configurations by returning a JSON Patch (RFC 6902).

Rules:
- Return ONLY a JSON array of patch operations, no explanations or markdown
- "replace" changes an existing field, "add" creates a new one (e.g. a new env var), "remove" deletes one
- Paths are JSON Pointers into the current config
- Only change what the user asks for
- Memory is in MiB (1024mb = 1024)
- CPU is in milliCPU (80% of 1500 = 1200)

Key paths:
- Memory: /workload/container/resources/memory/limitMiB
- CPU: /workload/container/resources/cpu/limitMilliCPU
- Env vars: /workload/container/envs/[VAR_NAME]

Example:
[{"op": "replace", "path": "/workload/container/resources/memory/limitMiB", "value": 1024}]"""

MODIFY_CONFIG_USER = """User request: "{input}"

JSON Schema:
{schema}

Current config:
{values}

Return the JSON Patch:"""
