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

MODIFY_CONFIG_SYSTEM = """You modify JSON configurations based on user requests.

Rules:
- Return ONLY valid JSON, no explanations or markdown
- Only change what the user asks for
- Keep all other fields unchanged
- Memory is in MiB (1024mb = 1024)
- CPU is in milliCPU (80% of 1500 = 1200)

Key paths:
- Memory: workload.container.resources.memory.limitMiB
- CPU: workload.container.resources.cpu.limitMilliCPU
- Env vars: workload.container.envs.[VAR_NAME]"""

MODIFY_CONFIG_USER = """User request: "{input}"

JSON Schema:
{schema}

Current config:
{values}

Return the modified JSON:"""
