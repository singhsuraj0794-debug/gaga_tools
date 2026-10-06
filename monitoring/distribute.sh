#!/usr/bin/env bash
#
# Roll out the gajab load-test worker to many macOS/Linux machines over SSH and
# start them together. e.g. 10 Macs x 10 sessions = 100 concurrent users.
#
# Requires passwordless SSH (key auth) to each host.
#
# Usage:
#   ./distribute.sh --host-file hosts.txt --flow --sessions 10 --duration 300 --think-time 3000 --sessions-dir ./sessions
#   ./distribute.sh --hosts mac1,mac2 --flow --staging --sessions 8 --duration 300
#   ./distribute.sh --hosts mac1 --copy-only
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE=(worker.sh concurrency_test.py ramp_test.py user_flow_test.py)

HOSTS=""
HOST_FILE=""
SSH_USER="${SSH_USER:-}"
REMOTE_DIR="${GAJAB_REMOTE_DIR:-gajab-loadtest}"
SESSIONS_DIR=""
SESSIONS=10
DURATION=300
THINK=3000
PEAK=0
FLOW=0
STAGING=0
FULL_PAYLOAD=0
FAST=0
ENGINE="chromium"
MIN_FREE=1500
SKIP_INSTALL=0
COPY_ONLY=0
NO_LAUNCH=0

usage() {
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --hosts h1,h2         comma-separated targets
  --host-file FILE      file with one host per line (# comments allowed)
  --user NAME           SSH user (or set SSH_USER)
  --remote-dir DIR      remote work dir (default gajab-loadtest)
  --sessions-dir DIR    local session pool to copy (setup_login_pool.py output)
  --sessions N          sessions per host (default 10)
  --duration S          run seconds (default 300)
  --think-time MS       idle between reloads (default 3000)
  --peak N              cap simultaneous sessions
  --flow                run the bargain journey
  --staging             target stg.gajab.com (with --flow)
  --full-payload        do not block images/media/fonts
  --fast                low-cost flow preset (faster waits, skips pincode)
  --engine chromium|chrome
  --min-free-mb MB      memory guard threshold (default 1500)
  --skip-install        skip pip/playwright install on the remote
  --copy-only           copy bundle, do not start
  --no-launch           alias of --copy-only
  -h, --help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --hosts) HOSTS="$2"; shift 2 ;;
        --host-file) HOST_FILE="$2"; shift 2 ;;
        --user) SSH_USER="$2"; shift 2 ;;
        --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
        --sessions-dir) SESSIONS_DIR="$2"; shift 2 ;;
        --sessions) SESSIONS="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --think-time) THINK="$2"; shift 2 ;;
        --peak) PEAK="$2"; shift 2 ;;
        --flow) FLOW=1; shift ;;
        --staging) STAGING=1; shift ;;
        --full-payload) FULL_PAYLOAD=1; shift ;;
        --fast) FAST=1; shift ;;
        --engine) ENGINE="$2"; shift 2 ;;
        --min-free-mb) MIN_FREE="$2"; shift 2 ;;
        --skip-install) SKIP_INSTALL=1; shift ;;
        --copy-only|--no-launch) COPY_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

if [ -z "$HOSTS" ] && [ -z "$HOST_FILE" ]; then
    echo "no targets: use --hosts or --host-file" >&2
    exit 1
fi

HOSTLIST=()
if [ -n "$HOST_FILE" ]; then
    [ -f "$HOST_FILE" ] || { echo "host file not found: $HOST_FILE" >&2; exit 1; }
    while IFS= read -r line; do
        line="${line%%#*}"; line="$(echo "$line" | xargs)"
        [ -n "$line" ] && HOSTLIST+=("$line")
    done < "$HOST_FILE"
fi
if [ -n "$HOSTS" ]; then
    IFS=',' read -r -a extra <<< "$HOSTS"
    for h in "${extra[@]}"; do h="$(echo "$h" | xargs)"; [ -n "$h" ] && HOSTLIST+=("$h"); done
fi

for f in "${BUNDLE[@]}"; do
    [ -f "$SCRIPT_DIR/$f" ] || { echo "missing bundle file: $SCRIPT_DIR/$f" >&2; exit 1; }
done

copy_sessions=0
if [ -n "$SESSIONS_DIR" ]; then
    if [ -d "$SESSIONS_DIR" ]; then copy_sessions=1; else echo "sessions dir not found: $SESSIONS_DIR (guest)" >&2; fi
fi

has_products=0
if [ "$FLOW" -eq 1 ] && [ -f "$SCRIPT_DIR/products.txt" ]; then has_products=1; fi

# worker.sh args on the remote (auth dir points at the copied pool)
WARGS=(--sessions "$SESSIONS" --duration "$DURATION" --think-time "$THINK" --engine "$ENGINE" --min-free-mb "$MIN_FREE")
[ "$FLOW" -eq 1 ] && WARGS+=(--flow)
[ "$STAGING" -eq 1 ] && WARGS+=(--staging)
[ "$FULL_PAYLOAD" -eq 1 ] && WARGS+=(--full-payload)
[ "$FAST" -eq 1 ] && WARGS+=(--fast)
[ "$PEAK" -gt 0 ] && WARGS+=(--peak "$PEAK")
[ "$SKIP_INSTALL" -eq 1 ] && WARGS+=(--skip-install)
[ "$copy_sessions" -eq 1 ] && WARGS+=(--auth-dir "$REMOTE_DIR/sessions")
[ "$has_products" -eq 1 ] && WARGS+=(--product-file "$REMOTE_DIR/products.txt")

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

echo "============================================================"
echo " gajab load-test rollout (SSH)"
echo " targets : ${#HOSTLIST[@]}  (${HOSTLIST[*]})"
echo " remote  : $REMOTE_DIR"
echo " mode    : $([ "$FLOW" -eq 1 ] && echo 'bargain journey' || echo 'page load')$([ "$STAGING" -eq 1 ] && echo ' on STAGING')"
echo " sessions: $SESSIONS per host   duration: ${DURATION}s"
echo " pool    : $([ "$copy_sessions" -eq 1 ] && echo "$SESSIONS_DIR" || echo 'none (guest)')"
echo "============================================================"

STARTED=()
FAILED=()
HI=0

for host in "${HOSTLIST[@]}"; do
    target="${SSH_USER:+$SSH_USER@}$host"
    echo ""
    echo "==> $host : copying"
    if ! ssh "${SSH_OPTS[@]}" "$target" "mkdir -p '$REMOTE_DIR/sessions'"; then
        echo "    $host : SSH/mkdir failed"; FAILED+=("$host"); continue
    fi
    scp_files=()
    for f in "${BUNDLE[@]}"; do scp_files+=("$SCRIPT_DIR/$f"); done
    [ "$has_products" -eq 1 ] && scp_files+=("$SCRIPT_DIR/products.txt")
    if ! scp "${SSH_OPTS[@]}" "${scp_files[@]}" "$target:$REMOTE_DIR/"; then
        echo "    $host : bundle copy failed"; FAILED+=("$host"); continue
    fi
    if [ "$copy_sessions" -eq 1 ]; then
        if ! scp "${SSH_OPTS[@]}" "$SESSIONS_DIR"/*.json "$target:$REMOTE_DIR/sessions/" 2>/dev/null; then
            echo "    $host : sessions copy failed (continuing guest)"
        fi
    fi
    ssh "${SSH_OPTS[@]}" "$target" "chmod +x '$REMOTE_DIR/worker.sh'" || true
    echo "    $host : bundle copied"

    if [ "$COPY_ONLY" -eq 1 ]; then HI=$((HI + 1)); continue; fi

    HOST_ARGS=("${WARGS[@]}")
    [ "$has_products" -eq 1 ] && HOST_ARGS+=(--product-offset $((HI * 5)))
    echo "    $host : launching"
    if ssh "${SSH_OPTS[@]}" "$target" "cd '$REMOTE_DIR' && nohup ./worker.sh ${HOST_ARGS[*]} > worker.log 2>&1 & echo \$! > worker.pid; echo started"; then
        STARTED+=("$host")
    else
        echo "    $host : launch failed"; FAILED+=("$host")
    fi
    HI=$((HI + 1))
done

echo ""
echo "============================================================"
echo " Rollout complete"
echo " started : ${#STARTED[@]} host(s)  ${STARTED[*]:-}"
if [ "${#FAILED[@]}" -gt 0 ]; then echo " failed  : ${FAILED[*]}"; fi
echo ""
echo " Each host logs to ~/$REMOTE_DIR/worker.log"
echo " Tail all : for h in ${HOSTLIST[*]}; do echo \"== \$h ==\"; ssh \$h 'tail -5 ~/$REMOTE_DIR/worker.log'; done"
echo " Stop all : for h in ${HOSTLIST[*]}; do ssh \$h 'pkill -f user_flow_test.py; pkill -f concurrency_test.py; pkill -f ramp_test.py'; done"
echo "============================================================"
