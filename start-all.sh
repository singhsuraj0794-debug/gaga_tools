#!/bin/bash
# start-all.sh — start (or restart) the whole local stack in one command.
#
#   ./start-all.sh          start anything that isn't running
#   ./start-all.sh restart  stop everything first, then start
#
# Components:
#   API 8090 + CLIP 8001 + analysis 8003 + cloudflared tunnel   (compute API)
#   scraper :9000 + ngrok permanent domain                      (Flipkart/Amazon/Meesho)
#   Chrome 9223 (+ proxy 9224)                                  (real browser for scrapers)
#
# The permanent ngrok domain serves BOTH:
#   https://headphone-shudder-lavender.ngrok-free.dev/api/*   -> compute API
#   https://headphone-shudder-lavender.ngrok-free.dev/scrape  -> scraper
set -uo pipefail

REPO_DIR="/Users/gajabmarketing/Library/CloudStorage/GoogleDrive-gajab@aeliyamarine.com/My Drive/Apps/Product-Video-Scraper"
NGROK_BIN="/Users/gajabmarketing/bin/ngrok"
NGROK_DOMAIN="headphone-shudder-lavender.ngrok-free.dev"
cd "$REPO_DIR" || exit 1

up() { curl -s -o /dev/null --max-time 3 "$1"; }

if [ "${1:-}" = "restart" ]; then
  echo "=== stopping everything ==="
  pkill -9 -f cloudflared 2>/dev/null
  pkill -9 -f "start-prelisting-tunnel" 2>/dev/null
  pkill -f "_analysis_server" 2>/dev/null
  pkill -f "_clip_verify_server" 2>/dev/null
  pkill -f "dist/index.mjs" 2>/dev/null
  pkill -f "local_scraper_server" 2>/dev/null
  pkill -f "ngrok http" 2>/dev/null
  sleep 4
fi

echo "=== 1. compute stack (API 8090, CLIP 8001, analysis 8003, tunnel) ==="
if up http://127.0.0.1:8090/api/products/status; then
  echo "    already running"
else
  rm -f /tmp/prelisting-tunnel-url.txt
  ANALYSIS_WORKERS=1 nohup bash start-prelisting-tunnel.sh > /tmp/prelisting-stack.log 2>&1 &
  echo "    starting..."
fi

echo "=== 2. local scraper server (:9000) ==="
if curl -s -o /dev/null --max-time 3 -X POST http://127.0.0.1:9000/scrape \
     -H "Content-Type: application/json" -d '{}'; then
  echo "    already running"
else
  (cd artifacts/api-server && nohup python3 local_scraper_server.py > /tmp/local-scraper.log 2>&1 &)
  echo "    starting..."
fi

echo "=== 3. Chrome for scrapers (:9223) ==="
if curl -s -o /dev/null --max-time 3 http://127.0.0.1:9223/json/version; then
  echo "    already running"
else
  nohup bash start-chrome-scraper.sh > /tmp/chrome-scraper.log 2>&1 &
  echo "    starting..."
fi

echo "=== 4. ngrok permanent tunnel ==="
if ps aux | grep -q "[n]grok http .*${NGROK_DOMAIN}"; then
  echo "    already running"
else
  nohup "$NGROK_BIN" http --url="${NGROK_DOMAIN}" 9000 > /tmp/ngrok.log 2>&1 &
  echo "    starting..."
fi

echo ""
echo "waiting for services..."
sleep 30
echo ""
echo "=== STATUS ==="
for p in "8001 CLIP" "8003 ANALYSIS" "8090 API" "9000 SCRAPER" "9223 CHROME"; do
  set -- $p
  if lsof -nP -iTCP:$1 -sTCP:LISTEN >/dev/null 2>&1; then echo "  :$1 $2  up"; else echo "  :$1 $2  DOWN"; fi
done
echo -n "  PERMANENT URL -> "
curl -s -o /dev/null -w "HTTP %{http_code}\n" --max-time 25 \
  -H "ngrok-skip-browser-warning: true" \
  "https://${NGROK_DOMAIN}/api/products/status"
echo ""
echo "Compute API URL:  https://${NGROK_DOMAIN}"
