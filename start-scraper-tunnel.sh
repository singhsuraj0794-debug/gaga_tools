#!/bin/bash
# start-scraper-tunnel.sh
# Run this on your laptop to offload Playwright scraping from Render.
# Uses ngrok free tier for a PERMANENT URL that never changes.
#
# ONE-TIME SETUP (do this first):
#   1. Sign up at https://dashboard.ngrok.com/signup (GitHub login, no credit card)
#   2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
#   3. Run: ngrok config add-authtoken YOUR_TOKEN
#   4. Reserve a domain at https://dashboard.ngrok.com/cloud-edge/domains
#      (e.g. "gajabscraper.ngrok-free.app" — pick any available name)
#   5. Update NGROK_DOMAIN below with your reserved domain
#
# For Indian e-commerce sites (Meesho, Flipkart), set an Indian residential proxy:
#   export SCRAPER_PROXY="http://user:pass@proxy:port"
#   ./start-scraper-tunnel.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/artifacts/api-server"

NGROK_DOMAIN="headphone-shudder-lavender.ngrok-free.dev"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill $SCRAPER_PID 2>/dev/null
  kill $NGROK_PID 2>/dev/null
  exit
}
trap cleanup EXIT INT TERM

echo "=== Local scraper server on port 9000 ==="
if curl -s -o /dev/null --max-time 3 -X POST http://127.0.0.1:9000/scrape \
     -H "Content-Type: application/json" -d '{}'; then
  echo "    already running — reusing it (not starting a second copy)"
  SCRAPER_PID=""
else
  python3 local_scraper_server.py &
  SCRAPER_PID=$!
  sleep 2
  echo "    started (pid $SCRAPER_PID)"
fi

echo ""
echo "=== ngrok tunnel ==="
echo "    URL: https://${NGROK_DOMAIN}"
echo "    Set LOCAL_SCRAPER_URL=https://${NGROK_DOMAIN} on Render"
echo ""

# If ngrok is already serving this permanent domain, don't start a second one
# (that fails with ERR_NGROK_334 — "endpoint is already online").
if ps aux | grep -q "[n]grok http .*${NGROK_DOMAIN}"; then
  echo "    ngrok already running for ${NGROK_DOMAIN} — reusing it."
  echo "    Tunnel is live: https://${NGROK_DOMAIN}"
  echo ""
  echo "    Nothing to do. Press Ctrl+C to stop watching."
  # keep the script alive without owning the tunnel
  while true; do sleep 3600; done
fi

echo "    starting ngrok (keep this terminal open)..."
echo ""
while true; do
  ~/bin/ngrok http --url="${NGROK_DOMAIN}" 9000 2>&1 &
  NGROK_PID=$!
  wait $NGROK_PID 2>/dev/null
  # If the port got taken by an already-running endpoint, don't spin forever.
  if ps aux | grep -q "[n]grok http .*${NGROK_DOMAIN}"; then
    echo "[$(date '+%H:%M:%S')] another ngrok owns ${NGROK_DOMAIN} — reusing it."
    while true; do sleep 3600; done
  fi
  echo "[$(date '+%H:%M:%S')] ngrok dropped — reconnecting in 5s..."
  sleep 5
done
