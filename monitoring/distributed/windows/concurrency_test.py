#!/usr/bin/env python3
"""
Concurrency load test: open the same page in N isolated browser sessions at once.

Each session gets its own browser context (separate cookies / storage / cache),
so it behaves like N independent first-time visitors hitting the app together.

Usage:
    python3 concurrency_test.py --url https://stg.gajab.com/ --sessions 10
    python3 concurrency_test.py --url https://stg.gajab.com/ --sessions 10 --headed
    python3 concurrency_test.py --mobile --screenshots

Authenticated + real Chrome (non-headless):
    python3 concurrency_test.py --url "https://stg.gajab.com/product-list/all?widgetId=13&position=WP4" \\
        --sessions 10 --auth --chrome --headed --screenshots
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

_SCREENSHOT_DIR = Path(__file__).parent / "concurrency_screenshots"
_DEFAULT_AUTH_STATE = Path(__file__).parent / ".gajab_session.json"

# Installed before any page script runs: capture LCP + resource weight.
_INIT_SCRIPT = """
window.__lcp = 0;
try {
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__lcp = e.startTime;
  }).observe({ type: 'largest-contentful-paint', buffered: true });
} catch (e) {}
"""


def _ms(v) -> str:
    return "-" if v is None else f"{v:.0f}ms"


async def _bounded_close(obj) -> None:
    """Close a Playwright object without letting a stuck close hang the run."""
    if obj is None:
        return
    try:
        await asyncio.wait_for(obj.close(), timeout=5)
    except BaseException:  # noqa: BLE001
        pass


_AUTH_COOKIES = ("access_token", "access_token_guest")
_AUTH_URL_MARKERS = ("signin", "login", "otp", "verify")


async def _is_authenticated(context, page) -> bool:
    """Best-effort auth check: final URL not on a login route + token cookie/localStorage present."""
    try:
        if any(m in page.url.lower() for m in _AUTH_URL_MARKERS):
            return False
    except Exception:
        pass
    try:
        for c in await context.cookies():
            if c.get("name") in _AUTH_COOKIES and c.get("value"):
                return True
    except Exception:
        pass
    try:
        token = await page.evaluate(
            "() => window.localStorage.getItem('access_token') || window.localStorage.getItem('token') || ''"
        )
        if token:
            return True
    except Exception:
        pass
    return False


async def _perf_counters(cdp) -> dict:
    """Cumulative CDP Performance counters (seconds / bytes)."""
    try:
        res = await cdp.send("Performance.getMetrics")
        return {m["name"]: m["value"] for m in res["metrics"]}
    except Exception:
        return {}


async def _load_once(page, context, args, cdp=None) -> dict:
    """Navigate once and return the Web Vitals + CPU sample for this page load."""
    before = await _perf_counters(cdp) if cdp else {}
    start = time.perf_counter()
    response = await page.goto(args.url, wait_until="load", timeout=args.timeout)
    wall_ms = (time.perf_counter() - start) * 1000
    metrics = await page.evaluate(
        """() => {
            const nav = performance.getEntriesByType('navigation')[0] || {};
            const paint = performance.getEntriesByType('paint');
            const fcp = paint.find(p => p.name === 'first-contentful-paint');
            const resources = performance.getEntriesByType('resource');
            const transfer = resources.reduce((s, r) => s + (r.transferSize || 0), 0);
            return {
                ttfb: nav.responseStart ?? null,
                dcl: nav.domContentLoadedEventEnd ?? null,
                load: nav.loadEventEnd ?? null,
                fcp: fcp ? fcp.startTime : null,
                lcp: window.__lcp || null,
                transfer: transfer,
                requests: resources.length,
            };
        }"""
    )
    after = await _perf_counters(cdp) if cdp else {}

    def _delta(name):
        if name in before and name in after:
            return round((after[name] - before[name]) * 1000, 1)
        return None

    return {
        "status": response.status if response else None,
        "wall_ms": wall_ms,
        "ttfb_ms": metrics["ttfb"] or None,
        "dcl_ms": metrics["dcl"] or None,
        "load_ms": metrics["load"] or None,
        "fcp_ms": metrics["fcp"] or None,
        "lcp_ms": metrics["lcp"] or None,
        "transfer_kb": round(metrics["transfer"] / 1024, 1),
        "requests": metrics["requests"],
        "cpu_task_ms": _delta("TaskDuration"),
        "cpu_script_ms": _delta("ScriptDuration"),
        "cpu_layout_ms": _delta("LayoutDuration"),
        "cpu_style_ms": _delta("RecalcStyleDuration"),
        "heap_mb": round(after.get("JSHeapUsedSize", 0) / 1048576, 1) if after else None,
        "nodes": after.get("Nodes"),
        "auth": await _is_authenticated(context, page),
        "title": await page.title(),
        "final_url": page.url,
    }


def _avg(samples: list[dict], key: str):
    vals = [s[key] for s in samples if s.get(key) is not None]
    return round(statistics.mean(vals), 1) if vals else None


async def run_session(pw, args, idx: int, shared_browser=None) -> dict:
    """One isolated session. Reloads until the global deadline when --duration is set.

    Never raises — always returns a result dict carrying every sample it measured.
    """
    browser = context = page = None
    own_browser = shared_browser is None
    errors: list[str] = []
    failed: list[str] = []
    samples: list[dict] = []
    iter_errors: list[str] = []
    result = {"session": idx, "samples": samples, "iter_errors": iter_errors}
    try:
        if own_browser:
            browser = await pw.chromium.launch(
                channel="chrome" if args.chrome else None,
                headless=not args.headed,
                args=["--disable-dev-shm-usage", "--no-default-browser-check"],
            )
        else:
            browser = shared_browser
        context_kwargs = dict(
            viewport={"width": 430, "height": 932} if args.mobile else {"width": 1440, "height": 900},
            device_scale_factor=2 if args.mobile else 1,
            is_mobile=args.mobile,
            has_touch=args.mobile,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation={"latitude": 19.4560, "longitude": 72.8054},
            permissions=["geolocation"] if args.mobile else [],
            ignore_https_errors=True,
        )
        if args.auth:
            context_kwargs["storage_state"] = str(args.auth_state)
        context = await browser.new_context(**context_kwargs)
        context.set_default_timeout(min(args.timeout, 30000))
        context.set_default_navigation_timeout(args.timeout)
        await context.add_init_script(_INIT_SCRIPT)

        if args.block_heavy:
            blocked = {"image", "media", "font"}

            async def _route(route):
                if route.request.resource_type in blocked:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", _route)

        blocked_types = {"image", "media", "font"} if args.block_heavy else set()

        def _on_failed(request):
            if request.resource_type in blocked_types:
                return
            failed.append(f"{request.method} {request.url}")

        def _on_console(msg):
            if msg.type != "error":
                return
            if args.block_heavy and ("Failed to load resource" in msg.text or "net::ERR_" in msg.text):
                return
            errors.append(msg.text)

        page = await context.new_page()
        page.on("console", _on_console)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", _on_failed)

        cdp = None
        try:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Performance.enable")
        except Exception:
            cdp = None

        shot_done = False
        while True:
            try:
                sample = await _load_once(page, context, args, cdp=cdp)
                samples.append(sample)
                if args.screenshots and not shot_done:
                    shot_done = True
                    _SCREENSHOT_DIR.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                    await page.screenshot(path=str(_SCREENSHOT_DIR / f"{stamp}_s{idx:02d}.png"))
            except Exception as exc:  # noqa: BLE001
                iter_errors.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")

            if args.max_iterations > 0 and len(samples) >= args.max_iterations:
                break
            if args.duration <= 0 or time.monotonic() >= args._deadline:
                break
            if args.think_time > 0:
                remaining = args._deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(args.think_time / 1000.0, remaining))
    except asyncio.CancelledError:
        result["cancelled"] = True
    except Exception as exc:  # noqa: BLE001
        iter_errors.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        result["console_errors"] = len(errors)
        result["failed_requests"] = len(failed)
        await _bounded_close(page)
        await _bounded_close(context)
        if own_browser:
            await _bounded_close(browser)

    ok_samples = [s for s in samples if s["status"] is not None and s["status"] < 400]
    result["iterations"] = len(samples)
    result["ok_iterations"] = len(ok_samples)
    result["auth"] = any(s["auth"] for s in samples) if samples else False
    result["wall_ms"] = _avg(samples, "wall_ms")
    result["ttfb_ms"] = _avg(samples, "ttfb_ms")
    result["dcl_ms"] = _avg(samples, "dcl_ms")
    result["load_ms"] = _avg(samples, "load_ms")
    result["fcp_ms"] = _avg(samples, "fcp_ms")
    result["lcp_ms"] = _avg(samples, "lcp_ms")
    result["transfer_kb"] = _avg(samples, "transfer_kb")
    result["requests"] = _avg(samples, "requests")
    result["cpu_task_ms"] = _avg(samples, "cpu_task_ms")
    result["cpu_script_ms"] = _avg(samples, "cpu_script_ms")
    result["cpu_layout_ms"] = _avg(samples, "cpu_layout_ms")
    result["cpu_style_ms"] = _avg(samples, "cpu_style_ms")
    result["heap_mb"] = _avg(samples, "heap_mb")
    result["nodes"] = _avg(samples, "nodes")
    result["status"] = samples[-1]["status"] if samples else None
    result["title"] = samples[-1]["title"] if samples else None
    result["final_url"] = samples[-1]["final_url"] if samples else None

    result["ok"] = bool(ok_samples)
    result["error"] = None
    if not samples:
        result["error"] = iter_errors[0] if iter_errors else "no samples"
    if result["ok"] and args.require_auth and not result["auth"]:
        result["ok"] = False
        result["error"] = "not authenticated (session missing/expired)"
    return result


def _summary(results: list[dict], key: str, unit: str = "ms") -> str:
    def fmt(v):
        return "-" if v is None else f"{v:.0f}{unit}"

    vals = [r[key] for r in results if r.get(key) is not None]
    if not vals:
        return "-"
    if len(vals) == 1:
        return fmt(vals[0])
    p95 = sorted(vals)[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
    return (
        f"min {fmt(min(vals))} / avg {fmt(statistics.mean(vals))} / "
        f"med {fmt(statistics.median(vals))} / p95 {fmt(p95)} / max {fmt(max(vals))}"
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description="Concurrency load test with N isolated Playwright sessions")
    ap.add_argument("--url", default="https://stg.gajab.com/", help="Page URL to load")
    ap.add_argument("--sessions", type=int, default=10, help="Number of concurrent sessions")
    ap.add_argument("--timeout", type=int, default=60000, help="Per-session navigation timeout (ms)")
    ap.add_argument("--headed", action="store_true", help="Run browsers visible (default: headless)")
    ap.add_argument("--mobile", action="store_true", help="Emulate mobile viewport (430x932)")
    ap.add_argument("--screenshots", action="store_true", help="Save one screenshot per session")
    ap.add_argument("--stagger", type=int, default=0, help="Delay between session starts (ms); 0 = all at once")
    ap.add_argument("--chrome", action="store_true", help="Use installed Google Chrome instead of bundled Chromium")
    ap.add_argument("--auth", action="store_true", help="Load a saved login session (storage_state) into every context")
    ap.add_argument("--auth-state", type=Path, default=_DEFAULT_AUTH_STATE, help="Path to storage_state JSON")
    ap.add_argument("--require-auth", action="store_true", help="Fail a session if it is not logged in")
    ap.add_argument("--tabs", action="store_true", help="One browser window, N context tabs (recommended for --headed Chrome)")
    ap.add_argument("--duration", type=int, default=0, help="Keep reloading for this many seconds (0 = single load per session)")
    ap.add_argument("--think-time", type=int, default=0, help="Idle ms between reloads in duration mode (0 = reload immediately)")
    ap.add_argument("--max-iterations", type=int, default=0, help="Cap reloads per session (0 = only limited by --duration)")
    ap.add_argument("--block-heavy", action="store_true", help="Block images/media/fonts to cut client RAM/CPU (lighter local runs)")
    ap.add_argument("--json", type=Path, default=None, help="Write machine-readable results to this path")
    ap.add_argument("--peak", type=int, default=0, help="Max sessions active at once (0 = all concurrent). Use to cap local peak.")
    args = ap.parse_args()

    if args.auth and not args.auth_state.exists():
        ap.error(f"auth state not found: {args.auth_state} (run setup_login.py first)")

    args._deadline = time.monotonic() + args.duration if args.duration > 0 else None

    print("=" * 68)
    print(f"Concurrency test — {args.sessions} sessions")
    print(f"URL        : {args.url}")
    print(f"Viewport   : {'mobile 430x932' if args.mobile else 'desktop 1440x900'}")
    print(f"Browser    : {'Google Chrome' if args.chrome else 'bundled Chromium'} | {'headed' if args.headed else 'headless'}")
    print(f"Auth       : {'yes — ' + str(args.auth_state) if args.auth else 'no (guest)'}")
    print(f"Mode       : {'one browser, N tabs' if args.tabs else 'one browser per session'}")
    if args.duration > 0:
        print(f"Duration   : {args.duration}s sustained | think-time {args.think_time}ms | max-iters {args.max_iterations or 'inf'}")
    else:
        print("Duration   : single load per session")
    print(f"Resources  : {'images/media/fonts BLOCKED' if args.block_heavy else 'full payload'}")
    peak_label = f" — peak {args.peak} concurrent" if args.peak > 0 else ""
    print(f"Stagger    : {args.stagger}ms{peak_label}")
    print("=" * 68, flush=True)

    sem = asyncio.Semaphore(args.peak) if args.peak > 0 else None

    wall_start = time.perf_counter()
    async with async_playwright() as pw:
        shared_browser = None
        if args.tabs:
            shared_browser = await pw.chromium.launch(
                channel="chrome" if args.chrome else None,
                headless=not args.headed,
                args=["--disable-dev-shm-usage", "--no-default-browser-check"],
            )

        async def guarded(i: int):
            if sem is None:
                return await run_session(pw, args, i + 1, shared_browser=shared_browser)
            async with sem:
                return await run_session(pw, args, i + 1, shared_browser=shared_browser)

        def make(i: int):
            return asyncio.create_task(guarded(i))

        if args.stagger <= 0:
            tasks = [make(i) for i in range(args.sessions)]
        else:
            async def delayed(i: int):
                await asyncio.sleep(args.stagger * i / 1000)
                return await guarded(i)

            tasks = [asyncio.create_task(delayed(i)) for i in range(args.sessions)]

        hard_cap = (args.duration + args.timeout / 1000 + 30) if args.duration > 0 else (args.timeout / 1000 + 60)
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=hard_cap)
        except asyncio.TimeoutError:
            print(f"\n[!] hard cap {hard_cap:.0f}s reached — cancelling remaining sessions", flush=True)
            for t in tasks:
                t.cancel()
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            results = [r for r in gathered if isinstance(r, dict)]

        if shared_browser is not None:
            try:
                await shared_browser.close()
            except Exception:
                pass
    total_wall = time.perf_counter() - wall_start

    results.sort(key=lambda r: r["session"])
    all_samples = [s for r in results for s in r["samples"]]

    print("\nPer-session results (averages over each session's reloads):")
    auth_enabled = any(r["auth"] is not None for r in results)
    header = f"{'#':>3} {'ok':>3} {'it':>4} {'status':>6}"
    if auth_enabled:
        header += f" {'auth':>4}"
    header += f" {'wall':>8} {'ttfb':>7} {'dcl':>7} {'load':>7} {'fcp':>7} {'lcp':>7} {'KB':>7} {'req':>4} {'cpu':>7} {'script':>7} {'heap':>7} {'err':>4}"
    print(header)
    for r in results:
        auth_col = f" {('Y' if r['auth'] else 'N'):>4}" if auth_enabled else ""
        print(
            f"{r['session']:>3} {('Y' if r['ok'] else 'N'):>3} {r['iterations']:>4} "
            f"{str(r['status'] or '-'):>6}{auth_col} "
            f"{_ms(r['wall_ms']):>8} {_ms(r['ttfb_ms']):>7} {_ms(r['dcl_ms']):>7} "
            f"{_ms(r['load_ms']):>7} {_ms(r['fcp_ms']):>7} {_ms(r['lcp_ms']):>7} "
            f"{str(r['transfer_kb'] or '-'):>7} {str(r['requests'] or '-'):>4} "
            f"{_ms(r['cpu_task_ms']):>7} {_ms(r['cpu_script_ms']):>7} {str(r['heap_mb'] or '-'):>7} "
            f"{str(r['console_errors'] + r['failed_requests']):>4}"
        )

    ok_count = sum(1 for r in results if r["ok"])
    failures = [r for r in results if not r["ok"]]
    total_loads = len(all_samples)
    ok_loads = sum(1 for s in all_samples if s["status"] is not None and s["status"] < 400)
    print("\nSummary:")
    print(f"  success        : {ok_count}/{len(results)} sessions")
    if auth_enabled:
        auth_count = sum(1 for r in results if r["auth"])
        print(f"  authenticated  : {auth_count}/{len(results)} sessions")
    print(f"  total loads    : {ok_loads}/{total_loads} page loads OK")
    cancelled_count = sum(1 for r in results if r.get("cancelled"))
    if cancelled_count:
        print(f"  cancelled      : {cancelled_count} session(s) hit the hard cap mid-load")
    if total_loads and total_wall > 0:
        print(f"  throughput     : {ok_loads / total_wall:.2f} loads/s")
    print(f"  total wall time: {total_wall:.2f}s")
    print(f"  wall (per load): {_summary(all_samples, 'wall_ms')}")
    print(f"  ttfb           : {_summary(all_samples, 'ttfb_ms')}")
    print(f"  domContentLoaded: {_summary(all_samples, 'dcl_ms')}")
    print(f"  load event     : {_summary(all_samples, 'load_ms')}")
    print(f"  FCP            : {_summary(all_samples, 'fcp_ms')}")
    print(f"  LCP            : {_summary(all_samples, 'lcp_ms')}")
    cpu_task_vals = [s["cpu_task_ms"] for s in all_samples if s.get("cpu_task_ms") is not None]
    if cpu_task_vals:
        total_cpu_ms = sum(cpu_task_vals)
        print(f"  CPU task/load  : {_summary(all_samples, 'cpu_task_ms')}")
        print(f"  CPU script/load: {_summary(all_samples, 'cpu_script_ms')}")
        print(f"  JS heap/load   : {_summary(all_samples, 'heap_mb', unit='MB')}")
        if total_wall > 0 and results:
            per_core = total_cpu_ms / (total_wall * 1000 * args.sessions) * 100
            print(f"  CPU load       : {total_cpu_ms / 1000:.1f}s total task time "
                  f"(≈{per_core:.0f}% of one core per session)")
    print(f"  console+net err: {sum(r['console_errors'] + r['failed_requests'] for r in results)}")
    print(f"  load errors    : {sum(len(r['iter_errors']) for r in results)}")

    if failures:
        print("\nFailures:")
        for r in failures:
            reason = r["error"] or f"HTTP {r['status']}"
            if r.get("iter_errors"):
                reason += f" | {len(r['iter_errors'])} load error(s): {r['iter_errors'][0]}"
            print(f"  session {r['session']}: {reason}")
    print("=" * 68)

    if args.json:
        payload = {
            "url": args.url,
            "sessions": args.sessions,
            "duration": args.duration,
            "block_heavy": args.block_heavy,
            "total_wall_s": round(total_wall, 2),
            "total_loads": total_loads,
            "ok_loads": ok_loads,
            "ok_sessions": ok_count,
            "result_sessions": len(results),
            "cpu_task_ms_avg": round(statistics.mean(cpu_task_vals), 1) if cpu_task_vals else None,
            "heap_mb_avg": _avg(all_samples, "heap_mb"),
            "wall_ms_avg": _avg(all_samples, "wall_ms"),
            "load_ms_avg": _avg(all_samples, "load_ms"),
            "results": results,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"[json] wrote {args.json}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
