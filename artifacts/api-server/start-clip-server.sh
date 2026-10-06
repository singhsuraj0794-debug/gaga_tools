#!/usr/bin/env bash
# Start the persistent CLIP/EasyOCR verification server.
# Keeps CLIP + EasyOCR + MiniLM resident in memory so large catalogues
# (100s/1000s of products) don't reload models per batch.
#
# Run from the api-server directory (or this script's directory).
# Safe to re-run: if a server is already listening on the port, it just prints
# that and exits without error.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PORT="${CLIP_VERIFY_PORT:-8001}"
WORKERS="${CLIP_VERIFY_WORKERS:-6}"

# Already running? Then there is nothing to do.
if curl -s --max-time 2 "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
  echo "clip-verify server is already running on 127.0.0.1:${PORT}"
  exit 0
fi

echo "Starting clip-verify server on 127.0.0.1:${PORT} (${WORKERS} workers)..."
CLIP_VERIFY_PORT="$PORT" CLIP_VERIFY_WORKERS="$WORKERS" python3 _clip_verify_server.py
