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
# The current tunnel URL is always written to:  /tmp/prelisting-tunnel-url.txt
# (and printed to the terminal). Paste it into the validator's "Compute API"
# field. The tunnel auto-reconnects if cloudflared drops, and the URL file is
# updated on every reconnect.
#
# ONE-TIME SETUP:
#   1. Install cloudflared:  brew install cloudflared
#
# Requires the API server to be built (dist/index.mjs). If not, run:
#   (cd artifacts/api-server && node build.mjs)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_DIR="$SCRIPT_DIR/artifacts/api-server"
cd "$API_DIR"

PORT="${API_PORT:-8080}"
CLIP_PORT="${CLIP_VERIFY_PORT:-8001}"
URL_FILE="/tmp/prelisting-tunnel-url.txt"
API_PID=""
CLIP_PID=""
ANALYSIS_PID=""
TUNNEL_PID=""

cleanup() {
  echo ""
  echo "Shutting down..."
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$CLIP_PID" ] && kill "$CLIP_PID" 2>/dev/null || true
  [ -n "$ANALYSIS_PID" ] && kill "$ANALYSIS_PID" 2>/dev/null || true
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  exit 0
}
trap cleanup EXIT INT TERM

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared not found. Install with: brew install cloudflared"
  exit 1
fi

echo "=== 1/3: API server on 127.0.0.1:${PORT} ==="
if ! curl -s --max-time 2 "http://127.0.0.1:${PORT}/api/products/status" >/dev/null 2>&1; then
  if [ ! -f "dist/index.mjs" ]; then
    echo "    dist/index.mjs not found — building API server..."
    node build.mjs
  fi
  PORT="$PORT" node --enable-source-maps ./dist/index.mjs &
  API_PID=$!
  sleep 3
  echo "    started (pid $API_PID)"
else
  echo "    already running (reusing it)"
fi

echo "=== 2/3: CLIP verify server on 127.0.0.1:${CLIP_PORT} ==="
if curl -s --max-time 2 "http://127.0.0.1:${CLIP_PORT}/health" >/dev/null 2>&1; then
  echo "    already running"
else
  CLIP_VERIFY_PORT="$CLIP_PORT" python3 _clip_verify_server.py &
  CLIP_PID=$!
  sleep 2
  echo "    started (pid $CLIP_PID)"
fi

echo "=== 2b/3: Analysis server (HSN + Qwen text) on 127.0.0.1:${ANALYSIS_PORT:-8003} ==="
if curl -s --max-time 2 "http://127.0.0.1:${ANALYSIS_PORT:-8003}/health" >/dev/null 2>&1; then
  echo "    already running"
else
  ANALYSIS_PORT="${ANALYSIS_PORT:-8003}" ANALYSIS_WORKERS="${ANALYSIS_WORKERS:-1}" python3 _analysis_server.py &
  ANALYSIS_PID=$!
  sleep 2
  echo "    started (pid $ANALYSIS_PID) — Qwen loads on first use"
fi

echo "=== 3/3: Cloudflare Tunnel -> 127.0.0.1:${PORT} ==="

start_tunnel() {
  CLOUDFLARED_LOG="$(mktemp -t cloudflared.XXXXXX.log)"
  cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate >"$CLOUDFLARED_LOG" 2>&1 &
  TUNNEL_PID=$!
  # Wait for the trycloudflare.com URL
  local url=""
  for _ in $(seq 1 30); do
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | head -1 || true)"
    if [ -n "$url" ]; then
      echo "$url" > "$URL_FILE"
      publish_url "$url"
      echo "    tunnel live: $url"
      echo "    (URL saved to $URL_FILE)"
      return 0
    fi
    sleep 1
  done
  return 1
}

# Publish the current tunnel URL to Supabase Storage so the deployed frontend
# can auto-fill the Compute API field from any device (no manual pasting).
# Uses a timestamped filename because the anon key can INSERT new files but
# cannot overwrite a fixed one (RLS). The frontend lists the bucket and reads
# the newest prelisting-api-url-<ts>.txt.
publish_url() {
  local url="$1"
  local supabase_url supabase_key remote req
  supabase_url="$(grep -E '^SUPABASE_URL=' "$API_DIR/.env" | head -1 | cut -d= -f2-)"
  supabase_key="$(grep -E '^SUPABASE_KEY=' "$API_DIR/.env" | head -1 | cut -d= -f2-)"
  if [ -z "$supabase_url" ] || [ -z "$supabase_key" ]; then
    return 0
  fi
  remote="prelisting-api-url-$(date +%s).txt"
  curl -s -o /dev/null --max-time 10 -X PUT \
    -H "apikey: $supabase_key" \
    -H "Authorization: Bearer $supabase_key" \
    -H "Content-Type: text/plain" \
    --data "$url" \
    "$supabase_url/storage/v1/object/monitoring/$remote" || true
  echo "    published to Supabase for auto-discovery ($remote)"
}

start_tunnel

echo ""
echo "    ╔════════════════════════════════════════════════════════════════╗"
echo "    ║  PRE-LISTING VALIDATOR TUNNEL IS LIVE                          ║"
echo "    ║                                                               ║"
echo "    ║  $(cat "$URL_FILE")"
echo "    ║                                                               ║"
echo "    ║  Paste that URL into the app's Compute API field → Test        ║"
echo "    ║  (also saved at $URL_FILE)"
echo "    ╚════════════════════════════════════════════════════════════════╝"
echo ""

# Keep the tunnel alive. New trycloudflare.com hostnames can take a few minutes
# for DNS to propagate, so before reconnecting we poll the URL for up to
# DNS_WAIT seconds (default 240). Only restart if it's still unreachable after
# that — this prevents the loop from churning new (also-unpropagated) URLs.
DNS_WAIT="${DNS_WAIT:-240}"
while true; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "[$(date '+%H:%M:%S')] tunnel process exited — reconnecting..."
    start_tunnel || echo "    reconnect failed, retrying..."
    continue
  fi
  url="$(cat "$URL_FILE" 2>/dev/null || true)"
  if [ -n "$url" ] && ! curl -s -o /dev/null --max-time 8 "$url/api/products/status"; then
    echo "[$(date '+%H:%M:%S')] tunnel unreachable — waiting up to ${DNS_WAIT}s for DNS/reachability before reconnecting..."
    waited=0
    while [ "$waited" -lt "$DNS_WAIT" ]; do
      if kill -0 "$TUNNEL_PID" 2>/dev/null && curl -s -o /dev/null --max-time 8 "$url/api/products/status"; then
        echo "[$(date '+%H:%M:%S')] tunnel is reachable again"
        break
      fi
      sleep 10
      waited=$((waited + 10))
    done
    # If still unreachable after the wait, restart with a fresh URL.
    if [ "$waited" -ge "$DNS_WAIT" ]; then
      echo "[$(date '+%H:%M:%S')] still unreachable after ${waited}s — reconnecting..."
      kill "$TUNNEL_PID" 2>/dev/null || true
      sleep 2
      start_tunnel || echo "    reconnect failed, retrying..."
    fi
    continue
  fi
  sleep 10
done