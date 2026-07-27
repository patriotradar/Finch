#!/usr/bin/env bash
# Free public URL for Finch — no Cloudflare account, no payment.
# Uses localtunnel (random *.loca.lt). Ephemeral — dies when this process stops.
set -euo pipefail
PORT="${1:-8100}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${ROOT}/bin:${PATH}"

if ! curl -sf "http://127.0.0.1:${PORT}/admin" >/dev/null 2>&1; then
  echo "Finch web is not up on :${PORT}. Start it first:"
  echo "  ${ROOT}/start_local.sh"
  exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "npx/node required for free tunnel"
  exit 1
fi

echo "Opening free public tunnel (no signup, no card)..."
echo "If the phone shows a tunnel password page, enter this server IP:"
curl -s -m 8 https://api.ipify.org 2>/dev/null || curl -s -m 8 https://ifconfig.me 2>/dev/null || true
echo
echo

exec npx --yes localtunnel --port "$PORT"
