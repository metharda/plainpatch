#!/usr/bin/env bash
# Start schema, values and the assistant from this checkout on ports 7001, 7002 and 7000, against a
# scratch copy of data/, so a recording starts from the committed configs. Run from the repository root.
# Needs Ollama on $OLLAMA_URL (default http://127.0.0.1:11434) with qwen2.5:3b, and the services'
# Python dependencies ($PYTHON, default python3).
set -euo pipefail

PY=${PYTHON:-python3}
OLLAMA_URL=${OLLAMA_URL:-http://127.0.0.1:11434}
work=$(mktemp -d)
cp -r data "$work/"

(cd schema-server && nohup "$PY" app.py --schema-dir="$work/data/schemas" --listen=127.0.0.1:7001 >"$work/schema.log" 2>&1 &)
(cd values-server && nohup "$PY" app.py --values-dir="$work/data/values" --listen=127.0.0.1:7002 >"$work/values.log" 2>&1 &)
(cd bot-server && nohup "$PY" app.py --listen=127.0.0.1:7000 --ollama-url="$OLLAMA_URL" \
  --schema-url=http://127.0.0.1:7001 --values-url=http://127.0.0.1:7002 >"$work/bot.log" 2>&1 &)

for _ in $(seq 60); do
  curl -sf 127.0.0.1:7000/health >/dev/null && break
  sleep 1
done
until grep -q "Model warmed up" "$work/bot.log"; do sleep 1; done
echo "stack ready; logs and data in $work"
