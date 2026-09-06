#!/bin/bash
# start-prelisting-tunnel.sh
# Exposes the local pre-listing validator compute API (HSN / CLIP / text
# correction) to the Cloudflare-hosted frontend via a Cloudflare Tunnel.
#
# What it runs (all local):
#   1. API server          — port 8080 (Express, runs the Python ML scripts)
#   2. CLIP verify server  — port 8001 (persistent CLIP/EasyOCR/MiniLM models)
#   3. cloudflared tunnel  — exposes port 8080 at https://<random>.trycloudflare.com
#
# Then copy the tunnel URL into the frontend and deploy:
#   VITE_PRELISTING_API_URL=https://<random>.trycloudflare.com npx vite build
#
# ONE-TIME SETUP:
#   1. Install cloudflared:  brew install cloudflared
#   2. (Optional) For a STABLE hostname instead of a random one each run,
#      create a named tunnel and update TUNNEL_URL below.
#
# Requires the API server to be built (dist/index.mjs). If not, run:
#   (cd artifacts/api-server && node build.mjs)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_DIR="$SCRIPT_DIR/artifacts/api-server"
cd "$API_DIR"

PORT="${API_PORT:-8080}"
CLIP_PORT="${CLIP_VERIFY_PORT:-8001}"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill "$API_PID" 2>/dev/null || true
  kill "$CLIP_PID" 2>/dev/null || true
  kill "$TUNNEL_PID" 2>/dev/null || true
  exit 0
}
trap cleanup EXIT INT TERM

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared not found. Install with: brew install cloudflared"
  exit 1
fi

echo "=== 1/3: Starting API server on 127.0.0.1:${PORT} ==="
if [ ! -f "dist/index.mjs" ]; then
  echo "    dist/index.mjs not found — building API server..."
  node build.mjs
fi
PORT="$PORT" node --enable-source-maps ./dist/index.mjs &
API_PID=$!
sleep 2

echo "=== 2/3: Starting CLIP verify server on 127.0.0.1:${CLIP_PORT} ==="
if curl -s --max-time 2 "http://127.0.0.1:${CLIP_PORT}/health" >/dev/null 2>&1; then
  echo "    already running"
else
  CLIP_VERIFY_PORT="$CLIP_PORT" python3 _clip_verify_server.py &
  CLIP_PID=$!
  sleep 2
fi

echo "=== 3/3: Starting Cloudflare Tunnel -> 127.0.0.1:${PORT} ==="
CLOUDFLARED_LOG="$(mktemp -t cloudflared.XXXXXX.log)"
cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate >"$CLOUDFLARED_LOG" 2>&1 &
TUNNEL_PID=$!

# Wait for the trycloudflare.com URL to appear in cloudflared's log.
TUNNEL_URL=""
for _ in $(seq 1 30); do
  sleep 1
  TUNNEL_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | head -1 || true)"
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
done

echo ""
echo "    ╔════════════════════════════════════════════════════════════════╗"
echo "    ║  PRE-LISTING VALIDATOR TUNNEL IS LIVE                          ║"
echo "    ║                                                               ║"
echo "    ║  $TUNNEL_URL"
echo "    ║                                                               ║"
echo "    ║  Build + deploy the frontend with:                             ║"
echo "    ║    VITE_PRELISTING_API_URL=$TUNNEL_URL npx vite build"
echo "    ╚════════════════════════════════════════════════════════════════╝"
echo ""

wait "$TUNNEL_PID"
