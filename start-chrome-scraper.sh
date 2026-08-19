#!/bin/bash
# start-chrome-scraper.sh — SEPARATE Chrome instance for Meesho scraping only.
# Runs on port 9223 and routes through the Webshare rotating residential proxy
# so rapid product scraping doesn't block the local IP.
#
# Monitoring keeps its own Chrome on port 9222 (no proxy) — these never conflict.
#
# Usage: ./start-chrome-scraper.sh
# Then run: ./start-scraper-tunnel.sh   (keep BOTH terminals open)

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="/tmp/chrome-scraper-profile-9223"
PORT=9223

# Webshare rotating residential proxy (override with WEBSHARE_PROXY if needed)
PROXY="${WEBSHARE_PROXY:-http://uvuqatrj-in-rotate:fd9sp5s4yg8q@p.webshare.io:80}"

echo "=== Starting scraping Chrome on port $PORT with Webshare proxy ==="
if curl -s --max-time 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "Scraping Chrome already running on $PORT"
  echo "NOTE: if it was started WITHOUT the proxy, stop it and rerun to pick up the proxy."
else
  echo "Using proxy: ${PROXY}"
  "$CHROME" --remote-debugging-port=$PORT --user-data-dir="$PROFILE" \
    --proxy-server="$PROXY" \
    --no-first-run --no-default-browser-check >/dev/null 2>&1 &
  sleep 4
  echo "Chrome started. Verifying..."
fi

curl -s --max-time 5 "http://localhost:$PORT/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Scraping Chrome CDP OK —', d.get('Browser',''))" 2>/dev/null || echo "FAILED — Chrome CDP not reachable on port $PORT"
echo ""
echo "Now run in a SECOND terminal: ./start-scraper-tunnel.sh"
