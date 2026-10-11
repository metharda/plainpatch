#!/bin/bash
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[TEST]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; cleanup; exit 1; }

cleanup() {
    warn "Stopping containers..."
    docker compose down --timeout 10 2>/dev/null || true
}

trap cleanup EXIT

wait_for_health() {
    local url=$1
    local name=$2
    local max_attempts=30
    
    for i in $(seq 1 $max_attempts); do
        if curl -sf "$url" > /dev/null 2>&1; then
            log "$name is healthy"
            return 0
        fi
        sleep 2
    done
    fail "$name failed health check"
}

log "Starting integration tests..."

log "Building and starting services..."
docker compose up -d --build

log "Waiting for services to be healthy..."
wait_for_health "http://localhost:11434/api/version" "Ollama"
wait_for_health "http://localhost:7001/health" "Schema Service"
wait_for_health "http://localhost:7002/health" "Values Service"
wait_for_health "http://localhost:7000/health" "Bot Service"

log "All services healthy!"

log "Testing Schema Service..."
curl -sf http://localhost:7001/storefront | jq -e '.type' > /dev/null || fail "Schema Service /storefront failed"
curl -sf http://localhost:7001/ledger | jq -e '.type' > /dev/null || fail "Schema Service /ledger failed"
curl -sf http://localhost:7001/orders | jq -e '.type' > /dev/null || fail "Schema Service /orders failed"
log "Schema Service: OK"

log "Testing Values Service..."
curl -sf http://localhost:7002/storefront | jq -e '.workload' > /dev/null || fail "Values Service /storefront failed"
curl -sf http://localhost:7002/ledger | jq -e '.workload' > /dev/null || fail "Values Service /ledger failed"
curl -sf http://localhost:7002/orders | jq -e '.workload' > /dev/null || fail "Values Service /orders failed"
log "Values Service: OK"

log "Testing Bot Service - Example 1: Ledger memory..."
RESULT=$(curl -sf -X POST http://localhost:7000/edits \
    -H "Content-Type: application/json" \
    -d '{"text": "set ledger service memory to 1024mb"}')
echo "$RESULT" | jq -e '.config.workload and (.changes | length > 0)' > /dev/null || fail "Bot Service example 1 failed"
log "Example 1: OK"

log "Testing Bot Service - Example 2: Orders env..."
RESULT=$(curl -sf -X POST http://localhost:7000/edits \
    -H "Content-Type: application/json" \
    -d '{"text": "set TENANT env to globex for orders service"}')
echo "$RESULT" | jq -e '.config.workload and (.changes | length > 0)' > /dev/null || fail "Bot Service example 2 failed"
log "Example 2: OK"

log "Testing Bot Service - Example 3: Storefront CPU..."
RESULT=$(curl -sf -X POST http://localhost:7000/edits \
    -H "Content-Type: application/json" \
    -d '{"text": "lower cpu limit of storefront service to 80%"}')
echo "$RESULT" | jq -e '.config.workload and (.changes | length > 0)' > /dev/null || fail "Bot Service example 3 failed"
log "Example 3: OK"

log "Testing apply, history and undo..."
BEFORE=$(curl -sf http://localhost:7002/storefront | jq -S .)
RESULT=$(curl -sf -X POST http://localhost:7000/edits \
    -H "Content-Type: application/json" \
    -d '{"text": "set storefront service memory to 3072mb", "apply": true, "author": "integration-test"}')
echo "$RESULT" | jq -e '.applied == true and .revision != null' > /dev/null || fail "Apply failed"
[ "$(curl -sf http://localhost:7002/storefront | jq -S .)" != "$BEFORE" ] || fail "Apply did not change the stored config"
curl -sf http://localhost:7000/history/storefront | jq -e '.[0].author == "integration-test"' > /dev/null || fail "History missing applied change"
curl -sf -X POST "http://localhost:7000/undo/storefront?author=integration-test" | jq -e '.reverts != null' > /dev/null || fail "Undo failed"
[ "$(curl -sf http://localhost:7002/storefront | jq -S .)" = "$BEFORE" ] || fail "Undo did not restore the original config"
log "Apply / history / undo: OK"

log "Testing edge cases..."

log "Edge case: Empty input..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:7000/edits \
    -H "Content-Type: application/json" \
    -d '{"text": ""}')
[ "$STATUS" = "400" ] || fail "Empty input should return 400, got $STATUS"
log "Empty input: OK (400)"

log "Edge case: Invalid app name..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:7001/nonexistent)
[ "$STATUS" = "404" ] || fail "Nonexistent app should return 404, got $STATUS"
log "Invalid app: OK (404)"

log "Edge case: Invalid JSON..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:7000/edits \
    -H "Content-Type: application/json" \
    -d 'not json')
[ "$STATUS" = "422" ] || [ "$STATUS" = "400" ] || fail "Invalid JSON should return 4xx, got $STATUS"
log "Invalid JSON: OK ($STATUS)"

log "====================================="
log "ALL INTEGRATION TESTS PASSED!"
log "====================================="
