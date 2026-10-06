#!/bin/bash
# run-prelisting-service.sh
# Wrapper used by the LaunchAgent (com.gajab.prelisting) so the whole
# pre-listing compute stack starts on login and is kept alive by launchd:
#   API (8090) + CLIP (8001) + analysis (8003) + cloudflared tunnel
# The tunnel URL is published to Supabase by start-prelisting-tunnel.sh, and
# the deployed validator auto-discovers it.
#
# NOTE: this replaces the old com.gajab.tunnel agent, which pointed at port
# 8080 (wrong port) and fought with this stack, causing repeated outages.
set -uo pipefail

REPO_DIR="/Users/gajabmarketing/Library/CloudStorage/GoogleDrive-gajab@aeliyamarine.com/My Drive/Apps/Product-Video-Scraper"
cd "$REPO_DIR" || exit 1

export ANALYSIS_WORKERS=1

# Keep the Amazon scraper's Chrome + ngrok alive too (used by the dashboard).
if ! curl -s -o /dev/null --max-time 3 http://127.0.0.1:9223/json/version; then
  nohup bash start-chrome-scraper.sh >/tmp/chrome-scraper.log 2>&1 &
fi

exec bash start-prelisting-tunnel.sh
