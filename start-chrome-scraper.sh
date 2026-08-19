#!/bin/bash
# start-chrome-scraper.sh — SEPARATE Chrome instance for Meesho scraping only.
# Runs on port 9223 and routes through a local proxy (port 9224) that forwards
# to the Webshare rotating residential proxy, so rapid scraping doesn't block
# the local IP. Chrome can't take proxy credentials directly, so we use a local
# auth-forwarding proxy.
#
# Monitoring keeps its own Chrome on port 9222 (no proxy) — these never conflict.
#
# Usage: ./start-chrome-scraper.sh
# Then run: ./start-scraper-tunnel.sh   (keep BOTH terminals open)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="/tmp/chrome-scraper-profile-9223"
PORT=9223
LOCAL_PROXY_PORT=9224

# Start the local auth-forwarding proxy to Webshare
echo "=== Starting local Webshare proxy forwarder on port $LOCAL_PROXY_PORT ==="
if curl -s --max-time 2 -x "http://localhost:$LOCAL_PROXY_PORT" "http://api.ipify.org" >/dev/null 2>&1; then
  echo "Local proxy already running on $LOCAL_PROXY_PORT"
else
  python3 "$SCRIPT_DIR/local_proxy.py" $LOCAL_PROXY_PORT >/tmp/local_proxy.log 2>&1 &
  sleep 2
  echo "Local proxy started. Verifying..."
fi
curl -s --max-time 15 -x "http://localhost:$LOCAL_PROXY_PORT" "http://api.ipify.org" | xargs echo "Local proxy egress IP:"

echo ""
echo "=== Starting scraping Chrome on port $PORT (via local proxy) ==="
if curl -s --max-time 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "Scraping Chrome already running on $PORT"
  echo "NOTE: if it was started without the proxy, stop it and rerun to pick up the proxy."
else
  "$CHROME" --remote-debugging-port=$PORT --user-data-dir="$PROFILE" \
    --proxy-server="http://localhost:$LOCAL_PROXY_PORT" \
    --no-first-run --no-default-browser-check >/dev/null 2>&1 &
  sleep 4
  echo "Chrome started. Verifying..."
fi

curl -s --max-time 5 "http://localhost:$PORT/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Scraping Chrome CDP OK —', d.get('Browser',''))" 2>/dev/null || echo "FAILED — Chrome CDP not reachable on port $PORT"
echo ""
echo "Now run in a SECOND terminal: ./start-scraper-tunnel.sh"
