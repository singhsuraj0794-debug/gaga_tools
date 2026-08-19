#!/bin/bash
# start-chrome-extract.sh — Dedicated Chrome instance for Meesho LINK EXTRACTION.
# Runs on port 9225 with NO proxy (uses your local IP — fast & reliable).
#
# Ports:
#   9222 = monitoring (no proxy)
#   9223 = product scraping (Webshare proxy)
#   9225 = link extraction (no proxy, local IP)
#
# Usage: ./start-chrome-extract.sh

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="/tmp/chrome-extract-profile-9225"
PORT=9225

echo "=== Starting extraction Chrome on port $PORT (no proxy, local IP) ==="
if curl -s --max-time 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "Extraction Chrome already running on $PORT"
else
  "$CHROME" --remote-debugging-port=$PORT --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check >/dev/null 2>&1 &
  sleep 4
  echo "Chrome started. Verifying..."
fi

curl -s --max-time 5 "http://localhost:$PORT/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Extraction Chrome CDP OK —', d.get('Browser',''))" 2>/dev/null || echo "FAILED — Chrome CDP not reachable on port $PORT"
