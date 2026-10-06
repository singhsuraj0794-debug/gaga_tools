#!/usr/bin/env bash
#
# Plug-and-play macOS / Linux load-test worker for gajab staging.
# Installs Playwright + Chromium (if needed), then runs N concurrent headless sessions
# with a memory guard, via ramp_test.py (which handles the guard + summary).
#
# Usage:
#   ./worker.sh --sessions 10 --duration 300 --think-time 2000
#   ./worker.sh --flow --sessions 10 --duration 300 --think-time 2000
#   ./worker.sh --sessions 20 --peak 10 --duration 300
#   ./worker.sh --sessions 10 --full-payload --engine chrome
#   ./worker.sh --sessions 2 --duration 15 --skip-install
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAMP="$SCRIPT_DIR/ramp_test.py"
WORKDIR="${GAJAB_WORKDIR:-$HOME/.gajab-loadtest}"
VENV="$WORKDIR/venv"
VENV_PY="$VENV/bin/python"

FLOW=0
RUNNER="concurrency_test.py"
URL_SET=0
FLOW_PRODUCT="https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914"
URL="https://stg.gajab.com/product-list/all?widgetId=13&position=WP4"
SESSIONS=10
DURATION=0
THINK=0
PEAK=0
ENGINE="chromium"
MIN_FREE=1500
TIMEOUT=30000
AUTH_STATE="$SCRIPT_DIR/.gajab_session.json"
AUTH_DIR=""
NO_AUTH=0
STAGING=0
PRODUCT_FILE=""
PRODUCTS=""
PRODUCT_OFFSET=0
FAST=0
BAO=0
SYNC=0
MONITOR_URL=""
HOST_NAME=""
FULL_PAYLOAD=0
ROUNDS=1
SKIP_INSTALL=0

usage() {
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --url URL             target page (default staging widget URL)
  --flow                run the authenticated bargain journey on live gajab.com
  --product URL         product page for --flow (implies --flow)
  --product-file FILE   file of product URLs (one per line) rotated across sessions
  --products LIST       comma-separated product URLs rotated across sessions
  --product-offset N    start index into the product list (distinct per PC)
  --fast                low-cost flow preset (faster waits, skips pincode)
  --bargain-only        flow = visit PDP then bargain (skip home + Trending)
  --sync                land all sessions on the PDP first, then bargain together
  --monitor-url URL     POST live progress to monitor_server.py (/report)
  --host-name NAME      name shown in the live monitor
  --sessions N          concurrent sessions (default 10)
  --duration S          run for S seconds (default 0 = single load each)
  --think-time MS       idle ms between reloads (default 0 = max churn)
  --peak N              cap simultaneous sessions (0 = all concurrent)
  --engine chromium|chrome   browser engine (default chromium = lightest)
  --full-payload        do NOT block images/media/fonts
  --staging             target stg.gajab.com (with --flow)
  --auth-state PATH     saved Playwright session (default ./.gajab_session.json)
  --auth-dir DIR        directory of session *.json files (pool rotated across users)
  --no-auth             run as guest
  --min-free-mb MB      memory guard threshold (default 1500)
  --rounds N            repeat the whole batch N times
  --skip-install        skip pip/playwright install (fast re-runs)
  -h, --help            show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --url) URL="$2"; URL_SET=1; shift 2 ;;
        --flow) FLOW=1; shift ;;
        --product) URL="$2"; URL_SET=1; FLOW=1; shift 2 ;;
        --product-file) PRODUCT_FILE="$2"; FLOW=1; shift 2 ;;
        --products) PRODUCTS="$2"; FLOW=1; shift 2 ;;
        --product-offset) PRODUCT_OFFSET="$2"; shift 2 ;;
        --fast) FAST=1; shift ;;
        --bargain-only|--pdp-bargain) BAO=1; FLOW=1; shift ;;
        --sync) SYNC=1; shift ;;
        --monitor-url) MONITOR_URL="$2"; shift 2 ;;
        --host-name) HOST_NAME="$2"; shift 2 ;;
        --sessions) SESSIONS="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --think-time) THINK="$2"; shift 2 ;;
        --peak) PEAK="$2"; shift 2 ;;
        --engine) ENGINE="$2"; shift 2 ;;
        --min-free-mb) MIN_FREE="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --auth-state) AUTH_STATE="$2"; shift 2 ;;
        --auth-dir) AUTH_DIR="$2"; shift 2 ;;
        --no-auth) NO_AUTH=1; shift ;;
        --staging) STAGING=1; shift ;;
        --full-payload) FULL_PAYLOAD=1; shift ;;
        --rounds) ROUNDS="$2"; shift 2 ;;
        --skip-install) SKIP_INSTALL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

if [ ! -f "$RAMP" ]; then
    echo "ramp_test.py not found beside worker.sh ($RAMP)" >&2
    exit 1
fi

if [ "$FLOW" -eq 1 ]; then
    RUNNER="user_flow_test.py"
    [ "$URL_SET" -eq 0 ] && URL="$FLOW_PRODUCT"
fi
if [ ! -f "$SCRIPT_DIR/$RUNNER" ]; then
    echo "runner not found: $SCRIPT_DIR/$RUNNER" >&2
    exit 1
fi

echo "============================================================"
echo " gajab load-test worker (macOS/Linux)"
echo " host    : $(hostname)   sessions: $SESSIONS   peak: $([ "$PEAK" -gt 0 ] && echo "$PEAK" || echo unlimited)"
echo " target  : $URL"
echo " runner  : $RUNNER ($([ "$FLOW" -eq 1 ] && echo bargain journey || echo page load))"
echo " engine  : $ENGINE   payload: $([ "$FULL_PAYLOAD" -eq 1 ] && echo full || echo blocked)"
echo " guard   : kill below ${MIN_FREE}MB available RAM"
echo "============================================================"

if [ "$SKIP_INSTALL" -eq 0 ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        if command -v brew >/dev/null 2>&1; then
            echo "==> python3 missing, installing via Homebrew"
            brew install python
        else
            echo "python3 not found. Install Python 3 (https://www.python.org/downloads/) and re-run." >&2
            exit 1
        fi
    fi
    mkdir -p "$WORKDIR"
    if [ ! -x "$VENV_PY" ]; then
        echo "==> creating virtualenv at $VENV"
        python3 -m venv "$VENV"
    fi
    echo "==> installing Playwright"
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet "playwright>=1.48,<2.0"
    echo "==> ensuring Chromium + headless-shell"
    "$VENV_PY" -m playwright install chromium
else
    if [ ! -x "$VENV_PY" ]; then
        echo "--skip-install set but no venv at $VENV — run once without --skip-install" >&2
        exit 1
    fi
fi

# Build ramp_test.py args (start == max == sessions runs a single level with the guard)
ARGS=(--url "$URL" --start "$SESSIONS" --step 1 --max-sessions "$SESSIONS" --runner "$RUNNER"
      --min-free-mb "$MIN_FREE" --cooldown 1 --engine "$ENGINE" --timeout "$TIMEOUT")
if [ "$DURATION" -gt 0 ]; then
    ARGS+=(--duration "$DURATION" --max-iterations 0 --level-timeout "$(( DURATION + TIMEOUT / 1000 + 90 ))")
else
    ARGS+=(--max-iterations 1 --level-timeout 180)
fi
[ "$THINK" -gt 0 ] && ARGS+=(--think-time "$THINK")
[ "$PEAK" -gt 0 ] && ARGS+=(--peak "$PEAK")
[ "$FULL_PAYLOAD" -eq 1 ] && ARGS+=(--full-payload)
[ "$STAGING" -eq 1 ] && ARGS+=(--staging)
[ -n "$PRODUCT_FILE" ] && ARGS+=(--product-file "$PRODUCT_FILE")
[ -n "$PRODUCTS" ] && ARGS+=(--products "$PRODUCTS")
[ "$PRODUCT_OFFSET" -gt 0 ] && ARGS+=(--product-offset "$PRODUCT_OFFSET")
[ "$FAST" -eq 1 ] && ARGS+=(--fast)
[ "$BAO" -eq 1 ] && ARGS+=(--bargain-only)
[ "$SYNC" -eq 1 ] && ARGS+=(--sync)
[ -n "$MONITOR_URL" ] && ARGS+=(--monitor-url "$MONITOR_URL")
[ -n "$HOST_NAME" ] && ARGS+=(--host-name "$HOST_NAME")
if [ "$NO_AUTH" -eq 1 ] || { [ -z "$AUTH_DIR" ] && [ ! -f "$AUTH_STATE" ]; }; then
    ARGS+=(--no-auth)
else
    ARGS+=(--auth-state "$AUTH_STATE")
    [ -n "$AUTH_DIR" ] && ARGS+=(--auth-dir "$AUTH_DIR")
fi

for r in $(seq 1 "$ROUNDS"); do
    [ "$ROUNDS" -gt 1 ] && echo "===== Round $r / $ROUNDS ====="
    "$VENV_PY" "$RAMP" "${ARGS[@]}"
done
