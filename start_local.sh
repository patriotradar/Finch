#!/usr/bin/env bash
# Start Finch local brain + PWA web UI
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export LD_LIBRARY_PATH="$ROOT/bin:${LD_LIBRARY_PATH:-}"

# 1) Brain
"$ROOT/bin/start_brain.sh" >> /tmp/llama_server.log 2>&1 &
# start_brain execs when starting fresh; when already up it returns 0 without backgrounding.
# If backgrounded with exec replaced: check health
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "Brain online"
    break
  fi
  sleep 1
done

# 2) Web
if ! curl -sf http://127.0.0.1:8100/admin >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
  nohup python web/chat_server.py >> /tmp/finch_web.log 2>&1 &
  echo "Web PID $!"
  sleep 2
fi
curl -sf http://127.0.0.1:8100/api/admin/dashboard | python3 -m json.tool | head -20
echo "Prospect: http://127.0.0.1:8100/  Admin: http://127.0.0.1:8100/admin"

# 3) Email status
if [ -n "${FINCH_EMAIL:-}" ] && [ -n "${FINCH_EMAIL_PASSWORD:-}" ]; then
  echo "Email: $FINCH_EMAIL (configured)"
else
  echo "Email: NOT configured. Set FINCH_EMAIL + FINCH_EMAIL_PASSWORD."
  echo "       See SETUP_EMAIL.txt — 2-minute Gmail setup."
fi
