#!/bin/bash
# start-scraper-tunnel.sh
# Run this on your laptop to offload Playwright scraping from Render.
# Keep this terminal window open while using the scraper.
# Auto-reconnects if the tunnel drops.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/artifacts/api-server"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill $SCRAPER_PID 2>/dev/null
  exit
}
trap cleanup EXIT INT TERM

echo "=== Starting local scraper server on port 9000 ==="
python3 local_scraper_server.py &
SCRAPER_PID=$!
sleep 2

echo "=== Creating public tunnel (auto-reconnect) ==="
echo "    Keep this terminal open while scraping."
echo ""

while true; do
  echo "[$(date '+%H:%M:%S')] Connecting tunnel..."
  ssh -o StrictHostKeyChecking=no \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o ConnectTimeout=10 \
      -R 80:localhost:9000 nokey@localhost.run 2>&1 | while IFS= read -r line; do
    echo "$line"
    if [[ "$line" =~ https://[a-z0-9]+\.lhr\.life ]]; then
      URL="${BASH_REMATCH[0]}"
      echo ""
      echo "============================================"
      echo " TUNNEL: $URL"
      echo " Set LOCAL_SCRAPER_URL=$URL on Render"
      echo "============================================"
      echo ""
    fi
  done
  echo "[$(date '+%H:%M:%S')] Tunnel dropped — reconnecting in 5s..."
  sleep 5
done
