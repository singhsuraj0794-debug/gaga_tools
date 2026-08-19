#!/bin/bash
# start-chrome-cdp.sh — Start real Chrome with remote debugging so the
# Meesho scraper's _try_playwright() can use it to pass Akamai bot detection.
# Chrome routes through the Webshare rotating residential proxy so rapid
# product scraping doesn't get your local IP blocked.
#
# Usage: ./start-chrome-cdp.sh
# Then run: ./start-scraper-tunnel.sh   (keep BOTH terminals open)

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="/tmp/chrome-scraper-profile"

# Webshare rotating residential proxy (override with WEBSHARE_PROXY if needed)
PROXY="${WEBSHARE_PROXY:-http://uvuqatrj-in-rotate:fd9sp5s4yg8q@p.webshare.io:80}"

echo "=== Starting Chrome with remote debugging on port 9222 ==="
if curl -s --max-time 2 "http://localhost:9222/json/version" >/dev/null 2>&1; then
  echo "Chrome CDP already running on 9222"
  echo "NOTE: If it was started WITHOUT the proxy, stop it (Ctrl+C / pkill Chrome with this profile) and rerun to pick up the proxy."
else
  echo "Using proxy: ${PROXY}"
  "$CHROME" --remote-debugging-port=9222 --user-data-dir="$PROFILE" \
    --proxy-server="$PROXY" \
    --no-first-run --no-default-browser-check >/dev/null 2>&1 &
  sleep 4
  echo "Chrome started. Verifying..."
fi

curl -s --max-time 5 "http://localhost:9222/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print('CDP OK —', d.get('Browser',''))" 2>/dev/null || echo "FAILED — Chrome CDP not reachable"
echo ""
echo "Now run in a SECOND terminal: ./start-scraper-tunnel.sh"
