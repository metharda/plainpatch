"""Labeled requests for evals/run.py: each is scored on the exact config after applying the patch."""

C = M = T = "/workload/container"  # every app uses the same layout
REMOVE = object()

# (request, expected app or None when the request must be refused, {pointer: expected new value | REMOVE})
# The design was iterated on TUNED; HELDOUT was written later to check that it generalizes.
TUNED = [
    ("set ledger service memory to 1024mb", "ledger", {f"{T}/resources/memory/limitMiB": 1024}),
    ("set TENANT env to globex for orders service", "orders", {f"{M}/envs/TENANT": "globex"}),
    ("lower cpu limit of storefront service to 80%", "storefront", {f"{C}/resources/cpu/limitMilliCPU": 1200}),
    ("set storefront memory limit to 4gb", "storefront", {f"{C}/resources/memory/limitMiB": 4096}),
    ("increase orders memory limit to 2048 MiB", "orders", {f"{M}/resources/memory/limitMiB": 2048}),
    ("set the cpu limit of ledger to 3000 millicores", "ledger", {f"{T}/resources/cpu/limitMilliCPU": 3000}),
    ("set storefront cpu limit to 2 cores", "storefront", {f"{C}/resources/cpu/limitMilliCPU": 2000}),
    ("set storefront memory request to 1gb", "storefront", {f"{C}/resources/memory/requestMiB": 1024}),
    ("set orders cpu request to 250 millicpu", "orders", {f"{M}/resources/cpu/requestMilliCPU": 250}),
    ("scale storefront to 5 replicas", "storefront", {"/workload/replicas": 5}),
    ("set replicas of the ledger statefulset to 3", "ledger", {"/workload/replicas": 3}),
    ("add env LOG_LEVEL=debug to the storefront service", "storefront", {f"{C}/envs/LOG_LEVEL": "debug"}),
    ("set REGION env var to eu-west-1 for ledger", "ledger", {f"{T}/envs/REGION": "eu-west-1"}),
    ("remove the TENANT env from orders", None, {}),  # schema requires TENANT: must be refused
    ("update the storefront image to storefront:1.1.0", "storefront", {f"{C}/image": "storefront:1.1.0"}),
    ("change orders ingress hostname to mm.example.com", "orders",
     {"/ingress/hostname": "mm.example.com"}),
    ("set orders ingress read timeout to 120 seconds", "orders",
     {"/ingress/proxy/timeouts/readSeconds": 120}),
    ("set max connections of storefront to 50", "storefront", {"/workload/maxConnections": 50}),
    ("double the memory limit of orders", "orders", {f"{M}/resources/memory/limitMiB": 2048}),
    ("halve the ledger cpu limit", "ledger", {f"{T}/resources/cpu/limitMilliCPU": 1250}),
    ("set imagePullPolicy to Always for ledger", "ledger", {f"{T}/imagePullPolicy": "Always"}),
    ("raise storefront memory limit by 512mb", "storefront", {f"{C}/resources/memory/limitMiB": 2560}),
    ("set ledger memory limit and request to 8gb", "ledger",
     {f"{T}/resources/memory/limitMiB": 8192, f"{T}/resources/memory/requestMiB": 8192}),
    ("set the orders proxy body size to 10", "orders", {"/ingress/proxy/bodySize": 10}),
    ("lower cpu limit of orders to 50%", "orders", {f"{M}/resources/cpu/limitMilliCPU": 250}),
    ("set storefront liveness probe port to 7777", "storefront", {f"{C}/probes/liveness/port": 7777}),
    ("set the storefront cpu limit to 2.5 cores", "storefront", {f"{C}/resources/cpu/limitMilliCPU": 2500}),
    ("set memory of the orders service to 3gb", "orders", {f"{M}/resources/memory/limitMiB": 3072}),
    ("give the ledger service 6gb of memory", "ledger", {f"{T}/resources/memory/limitMiB": 6144}),
    ("make the printer faster", None, {}),
]

# written after tuning on TUNED
HELDOUT = [
    ("bump storefront replicas to 4", "storefront", {"/workload/replicas": 4}),
    ("set storefront memory request to 768mb", "storefront", {f"{C}/resources/memory/requestMiB": 768}),
    ("change the orders image tag to orders:1.3.0", "orders", {f"{M}/image": "orders:1.3.0"}),
    ("set FEATURE_FLAGS env to beta on storefront", "storefront", {f"{C}/envs/FEATURE_FLAGS": "beta"}),
    ("update TENANT on orders to initech", "orders", {f"{M}/envs/TENANT": "initech"}),
    ("set connect timeout of orders ingress to 30 seconds", "orders",
     {"/ingress/proxy/timeouts/connectSeconds": 30}),
    ("set ledger cpu request to 1500 millicores", "ledger", {f"{T}/resources/cpu/requestMilliCPU": 1500}),
    ("increase ledger memory limit to 5gb", "ledger", {f"{T}/resources/memory/limitMiB": 5120}),
    ("set storefront readiness probe port to 8080", "storefront", {f"{C}/probes/readiness/port": 8080}),
    ("set orders replicas to 1", "orders", {"/workload/replicas": 1}),
    ("set send timeout for orders proxy to 90s", "orders",
     {"/ingress/proxy/timeouts/sendSeconds": 90}),
    ("set storefront max connection count to 25", "storefront", {"/workload/maxConnections": 25}),
    ("set storefront imagePullPolicy to Always", "storefront", {f"{C}/imagePullPolicy": "Always"}),
    ("set the ledger memory request to 2gb", "ledger", {f"{T}/resources/memory/requestMiB": 2048}),
    ("add env DEBUG=true to orders", "orders", {f"{M}/envs/DEBUG": "true"}),
    ("reduce storefront cpu request to 500 millicores", "storefront", {f"{C}/resources/cpu/requestMilliCPU": 500}),
    ("set ledger replicas to 4", "ledger", {"/workload/replicas": 4}),
    ("turn the oven on", None, {}),
]

SETS = {"tuned": TUNED, "heldout": HELDOUT}
