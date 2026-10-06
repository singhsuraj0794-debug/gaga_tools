#!/usr/bin/env python3
"""
Find the safe local concurrency ceiling.

Ramps headless-Chrome sessions (2, 4, 6, ...) and aborts before the machine runs
out of memory. Each level runs in its own process group so all child Chrome
processes are killed if the memory guard trips.

Usage:
    python3 ramp_test.py --url "https://stg.gajab.com/product-list/all?widgetId=13&position=WP4"
    python3 ramp_test.py --max-sessions 20 --step 2 --min-free-mb 2500
    python3 ramp_test.py --full-payload          # don't block images/media/fonts
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def _page_size() -> int:
    """Memory page size, cross-platform (macOS/Windows/Linux)."""
    try:
        import psutil
        return psutil.virtual_memory().page_size or 4096
    except Exception:
        try:
            return os.sysconf("SC_PAGE_SIZE")
        except Exception:
            return 4096


_PAGE_SIZE = _page_size()


def available_mb() -> float:
    """Reclaimable memory (MB), cross-platform via psutil; macOS vm_stat fallback."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1048576
    except Exception:
        pass
    try:
        out = subprocess.check_output(["vm_stat"]).decode()
    except Exception:
        return float("inf")
    fields = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip().rstrip(".")
        if val.isdigit():
            fields[key.strip()] = int(val)
    pages = sum(fields.get(k, 0) for k in (
        "Pages free", "Pages inactive", "Pages speculative", "Pages purgeable",
    ))
    return pages * _PAGE_SIZE / 1048576


def kill_group(proc: subprocess.Popen):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def run_level(args, sessions: int) -> dict:
    level_json = Path(f"/tmp/ramp_level_{sessions}.json")
    if level_json.exists():
        level_json.unlink()

    runner = Path(args.runner)
    if not runner.is_absolute():
        runner = HERE / runner
    if not runner.exists():
        raise SystemExit(f"runner not found: {runner}")

    cmd = [
        sys.executable, str(runner),
        "--url", args.url,
        "--sessions", str(sessions),
        "--tabs", "--timeout", str(args.timeout),
        "--json", str(level_json),
    ]
    if args.engine == "chrome":
        cmd.append("--chrome")
    if args.staging and "user_flow" in args.runner:
        cmd.append("--staging")
    if "user_flow" in args.runner:
        if args.product_file:
            cmd += ["--product-file", str(args.product_file)]
        if args.products:
            cmd += ["--products", args.products]
        if args.product_offset:
            cmd += ["--product-offset", str(args.product_offset)]
        if args.fast:
            cmd.append("--fast")
        if args.bargain_only:
            cmd.append("--pdp-bargain")
        if args.mobile:
            cmd.append("--mobile")
        if args.block_media:
            cmd.append("--block-media")
        if args.stagger:
            cmd += ["--stagger", str(args.stagger)]
        if args.sync:
            cmd.append("--sync")
        if args.monitor_url:
            cmd += ["--monitor-url", args.monitor_url]
            if args.host_name:
                cmd += ["--host-name", args.host_name]
    if not args.no_auth:
        cmd += ["--auth", "--auth-state", str(args.auth_state)]
        if args.auth_dir:
            cmd += ["--auth-dir", str(args.auth_dir)]
    if not args.full_payload:
        cmd.append("--block-heavy")
    if args.duration:
        cmd += ["--duration", str(args.duration)]
    if args.max_iterations:
        cmd += ["--max-iterations", str(args.max_iterations)]
    if args.peak:
        cmd += ["--peak", str(args.peak)]
    if args.think_time:
        cmd += ["--think-time", str(args.think_time)]

    log_path = Path(f"/tmp/ramp_level_{sessions}.log")
    log = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=HERE, start_new_session=True)

    min_avail = float("inf")
    aborted = False
    timed_out = False
    start = time.monotonic()
    while proc.poll() is None:
        avail = available_mb()
        min_avail = min(min_avail, avail)
        if avail < args.min_free_mb:
            aborted = True
            print(f"    !! memory guard: available {avail:.0f} MB < {args.min_free_mb} MB — killing level")
            kill_group(proc)
            break
        if time.monotonic() - start > args.level_timeout:
            timed_out = True
            print(f"    !! level timeout {args.level_timeout}s — killing level")
            kill_group(proc)
            break
        time.sleep(0.5)
    log.close()

    data = {}
    if level_json.exists():
        try:
            data = json.loads(level_json.read_text())
        except Exception:
            data = {}

    # Persist every run so reports never lose step-level detail.
    if data and getattr(args, "results_dir", None):
        try:
            results_dir = Path(args.results_dir)
            results_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            runner_name = Path(args.runner).stem
            tag = "pdpbargain" if getattr(args, "bargain_only", False) else "fullflow"
            out = results_dir / f"{stamp}_{tag}_{sessions}s_{runner_name}.json"
            if "data" not in data:
                out.write_text(json.dumps(data, indent=2))
            print(f"[results] saved {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"[results] save failed: {exc}")

    return {
        "sessions": sessions,
        "min_avail_mb": round(min_avail, 0) if min_avail != float("inf") else None,
        "aborted": aborted,
        "timed_out": timed_out,
        "data": data,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Ramp concurrency until the machine or server says stop")
    ap.add_argument("--url", default="https://stg.gajab.com/product-list/all?widgetId=13&position=WP4")
    ap.add_argument("--results-dir", type=Path, default=HERE / "load_test_results",
                    help="Directory to persist per-run result JSON (timestamped)")
    ap.add_argument("--runner", default="concurrency_test.py",
                    help="Runner script to drive: concurrency_test.py (page load) or user_flow_test.py (bargain journey)")
    ap.add_argument("--start", type=int, default=2, help="First level (sessions)")
    ap.add_argument("--step", type=int, default=2, help="Sessions added per level")
    ap.add_argument("--max-sessions", type=int, default=20, help="Highest level to attempt")
    ap.add_argument("--min-free-mb", type=int, default=2500, help="Abort a level below this available RAM")
    ap.add_argument("--cooldown", type=int, default=15, help="Seconds to rest between levels")
    ap.add_argument("--level-timeout", type=int, default=180, help="Hard cap per level (s)")
    ap.add_argument("--timeout", type=int, default=30000, help="Per-page navigation timeout (ms)")
    ap.add_argument("--duration", type=int, default=0, help="Hold each level for N seconds")
    ap.add_argument("--max-iterations", type=int, default=1, help="Reloads per session per level")
    ap.add_argument("--peak", type=int, default=0, help="Max sessions active at once (0 = all concurrent)")
    ap.add_argument("--think-time", type=int, default=0, help="Idle ms between reloads in duration mode")
    ap.add_argument("--full-payload", action="store_true", help="Do NOT block images/media/fonts")
    ap.add_argument("--engine", choices=("chrome", "chromium"), default="chrome",
                    help="chrome = installed Google Chrome; chromium = lighter bundled headless-shell")
    ap.add_argument("--no-auth", action="store_true", help="Run as guest")
    ap.add_argument("--auth-state", type=Path, default=HERE / ".gajab_session.json")
    ap.add_argument("--auth-dir", type=Path, default=None, help="Session pool directory (user_flow_test only)")
    ap.add_argument("--staging", action="store_true", help="Run the flow against stg.gajab.com")
    ap.add_argument("--product-file", type=Path, default=None, help="Product list for the flow (one URL per line)")
    ap.add_argument("--products", default="", help="Comma-separated product URLs for the flow")
    ap.add_argument("--product-offset", type=int, default=0, help="Start index into the product list")
    ap.add_argument("--fast", action="store_true", help="Low-cost flow preset (user_flow_test only)")
    ap.add_argument("--bargain-only", dest="bargain_only", action="store_true", help="Flow: visit PDP then bargain only (skip home + trending)")
    ap.add_argument("--mobile", action="store_true", help="Mobile viewport (lighter render, flow only)")
    ap.add_argument("--block-media", action="store_true", help="Block video/fonts only (keep images)")
    ap.add_argument("--stagger", type=int, default=0, help="Delay each session start by N ms (avoid simultaneous burst)")
    ap.add_argument("--sync", action="store_true", help="Flow: land all sessions on the PDP first, then bargain together")
    ap.add_argument("--monitor-url", default="", help="Live progress monitor URL (user_flow_test)")
    ap.add_argument("--host-name", default="", help="Name shown in the live monitor")
    ap.add_argument("--continue-on-fail", action="store_true", help="Keep ramping after a level has failures")
    args = ap.parse_args()

    print("=" * 78)
    print(f"Concurrency ramp — {args.start}..{args.max_sessions} sessions (step {args.step})")
    print(f"URL       : {args.url}")
    print(f"Engine    : {'Google Chrome' if args.engine == 'chrome' else 'bundled Chromium headless-shell'}")
    print(f"Payload   : {'full' if args.full_payload else 'images/media/fonts BLOCKED'}")
    print(f"Auth      : {'no (guest)' if args.no_auth else args.auth_state}")
    print(f"Guard     : abort below {args.min_free_mb} MB available RAM, {args.cooldown}s cooldown")
    if args.peak:
        print(f"Peak      : {args.peak} concurrent (sessions run in waves)")
    print(f"Start RAM : {available_mb():.0f} MB available")
    print("=" * 78, flush=True)

    levels = []
    max_stable = 0
    best_level = 0
    best_ok = 0
    n = args.start
    while n <= args.max_sessions:
        print(f"\n>>> Level {n} sessions ...", flush=True)
        res = run_level(args, n)
        d = res["data"]
        ok = d.get("ok_sessions", 0)
        loads_ok = d.get("ok_loads", 0)
        wall = d.get("wall_ms_avg")
        cpu = d.get("cpu_task_ms_avg")
        heap = d.get("heap_mb_avg")
        wall_s = f"{wall:.0f}ms" if wall is not None else "-"
        cpu_s = f"{cpu:.0f}ms" if cpu is not None else "-"
        heap_s = f"{heap}MB" if heap is not None else "-"
        if d:
            pct = (ok / n * 100) if n else 0
            print(
                f"    ok {ok}/{n} sessions ({pct:.0f}%) | loads {loads_ok} | "
                f"avg load/journey {wall_s} | cpu/tab {cpu_s} | heap {heap_s} | "
                f"min RAM {res['min_avail_mb']}MB"
            )
            if ok > best_ok:
                best_ok, best_level = ok, n
        else:
            print(f"    no data (aborted={res['aborted']} timeout={res['timed_out']})")
        levels.append(res)

        if res["aborted"]:
            break
        if ok < n or (loads_ok == 0 and n > 0):
            if not args.continue_on_fail:
                break
        else:
            max_stable = n
        n += args.step
        time.sleep(args.cooldown)

    print("\n" + "=" * 78)
    print("RAMP SUMMARY")
    print(f"{'sessions':>8} {'ok':>5} {'avg load':>10} {'cpu/tab':>9} {'heap MB':>8} {'minRAM':>8}  status")
    for res in levels:
        d = res["data"]
        n = res["sessions"]
        wall = d.get("wall_ms_avg")
        cpu = d.get("cpu_task_ms_avg")
        heap = d.get("heap_mb_avg")
        if res["aborted"]:
            status = "ABORTED (memory guard)"
        elif res["timed_out"]:
            status = "TIMEOUT"
        elif d.get("ok_sessions", 0) == n and n > 0:
            status = "ok"
        else:
            status = f"degraded ({d.get('ok_sessions', 0)}/{n})"
        print(
            f"{n:>8} {str(d.get('ok_sessions', '-')):>5} "
            f"{(f'{wall:.0f}ms' if wall is not None else '-'):>10} "
            f"{(f'{cpu:.0f}ms' if cpu is not None else '-'):>9} "
            f"{str(heap if heap is not None else '-'):>8} "
            f"{str(res['min_avail_mb']):>8}  {status}"
        )
    print("=" * 78)
    if best_level:
        pct = best_ok / best_level * 100
        print(f"Best level: {best_level} sessions — {best_ok}/{best_level} completed ({pct:.0f}%)")
    if max_stable:
        print(f"Highest level with ALL sessions completed: {max_stable}")
    else:
        print("No level had every session complete — see the per-level completion % above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
