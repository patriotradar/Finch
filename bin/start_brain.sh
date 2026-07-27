#!/usr/bin/env bash
# Start local llama.cpp brain for Finch (SmolLM2-360M)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export LD_LIBRARY_PATH="$ROOT/bin:${LD_LIBRARY_PATH:-}"
MODEL="${FINCH_MODEL:-$ROOT/data/models/SmolLM2-360M-Instruct-Q4_K_M.gguf}"
HOST="${FINCH_LLM_BIND:-127.0.0.1}"
PORT="${FINCH_LLM_PORT:-8080}"
BIN="$ROOT/bin/llama-server"

if [[ ! -x "$BIN" ]]; then
  echo "Missing $BIN" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "Missing model $MODEL" >&2
  exit 1
fi

# Already healthy?
if curl -sf "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "Brain already running on ${HOST}:${PORT}"
  exit 0
fi

exec "$BIN" -m "$MODEL" --host "$HOST" --port "$PORT" \
  -c "${FINCH_LLM_CTX:-1024}" -t "${FINCH_LLM_THREADS:-2}" \
  --alias finch-brain \
  --load-mode mmap
