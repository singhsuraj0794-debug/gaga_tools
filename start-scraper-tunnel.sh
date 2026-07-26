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
# Then set LOCAL_SCRAPER_URL on Render to https://YOUR_DOMAIN.ngrok-free.app
# This URL will NEVER change — update it once and forget it.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/artifacts/api-server"

NGROK_DOMAIN="gajabscraper.ngrok-free.app"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill $SCRAPER_PID 2>/dev/null
  kill $NGROK_PID 2>/dev/null
  exit
}
trap cleanup EXIT INT TERM

echo "=== Starting local scraper server on port 9000 ==="
python3 local_scraper_server.py &
SCRAPER_PID=$!
sleep 2

echo "=== Creating ngrok tunnel ==="
echo "    URL: https://${NGROK_DOMAIN}"
echo "    Set LOCAL_SCRAPER_URL=https://${NGROK_DOMAIN} on Render"
echo "    Keep this terminal open while scraping."
echo ""

while true; do
  ~/bin/ngrok http --domain="${NGROK_DOMAIN}" 9000 2>&1 &
  NGROK_PID=$!
  wait $NGROK_PID 2>/dev/null
  echo "[$(date '+%H:%M:%S')] ngrok dropped — reconnecting in 5s..."
  sleep 5
done
