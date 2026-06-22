#!/usr/bin/env bash
# Smoke-test the brain chain end to end.
#   ./tools/test_brain.sh proxy   # test anthropic_proxy directly (proxy -> OpenModel)
#   ./tools/test_brain.sh shim    # test odysseus_shim (shim -> Odysseus -> proxy -> OpenModel)
set -euo pipefail

TARGET="${1:-shim}"
case "$TARGET" in
  proxy) URL="http://127.0.0.1:9100/v1/chat/completions" ;;
  shim)  URL="http://127.0.0.1:9200/v1/chat/completions" ;;
  *) echo "usage: $0 [proxy|shim]"; exit 1 ;;
esac

echo "== health =="
curl -s "${URL%/chat/completions}/models" | head -c 400; echo

echo "== non-stream =="
curl -s "$URL" -H 'content-type: application/json' -d '{
  "model":"deepseek-v4-flash",
  "session_id":"smoketest",
  "messages":[
    {"role":"system","content":"Ты Гермес. Отвечай одним коротким предложением по-русски."},
    {"role":"user","content":"Представься в двух словах."}
  ]}' -w '\n[HTTP %{http_code}]\n'

echo "== stream (content deltas only) =="
curl -s -N "$URL" -H 'content-type: application/json' -d '{
  "model":"deepseek-v4-flash","stream":true,"session_id":"smoketest",
  "messages":[{"role":"user","content":"Скажи привет и пожелай хорошего дня."}]
}' | grep -o '"content": *"[^"]*"' | head -40
