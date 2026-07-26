#!/bin/bash
# start-scraper-tunnel.sh
# Run this on your laptop to offload scraping from Render.
# Keep this terminal window open while using the scraper.
#
# 1. Starts the local scraper server (Python) on port 9000
# 2. Creates a public tunnel via localhost.run
# 3. Prints the tunnel URL — paste it into LOCAL_SCRAPER_URL on Render

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Starting local scraper server on port 9000 ==="
cd "$SCRIPT_DIR/artifacts/api-server"
python3 local_scraper_server.py &
SCRAPER_PID=$!
trap "kill $SCRAPER_PID 2>/dev/null; exit" EXIT INT TERM
sleep 2

echo ""
echo "=== Creating public tunnel via localhost.run ==="
echo "    (keep this terminal open)"
echo ""

ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
    -R 80:localhost:9000 nokey@localhost.run 2>&1 | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" =~ https://[a-z0-9]+\.lhr\.life ]]; then
    URL="${BASH_REMATCH[0]}"
    echo ""
    echo "============================================"
    echo " TUNNEL URL: $URL"
    echo "============================================"
    echo ""
    echo "Copy this URL and set it as LOCAL_SCRAPER_URL"
    echo "in your Render dashboard environment variables."
    echo ""
  fi
done
