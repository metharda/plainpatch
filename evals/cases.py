"""Labeled requests for evals/run.py: each is scored on the exact config after applying the patch."""

C = M = T = "/workload/container"  # every app uses the same layout
REMOVE = object()

# (request, expected app or None when the request must be refused, {pointer: expected new value | REMOVE})
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

SETS = {"tuned": TUNED}
