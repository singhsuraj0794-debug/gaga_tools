#!/usr/bin/env python3
"""
Memory probe: park N headless tabs and measure the real per-tab cost.

Answers "why can't we run 20 sessions?" with actual numbers instead of guesses.

Usage:
    python3 mem_probe.py --sessions 8
    python3 mem_probe.py --sessions 8 --full-payload
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import time

from playwright.async_api import async_playwright

URL = "https://stg.gajab.com/product-list/all?widgetId=13&position=WP4"


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
_BLOCKED = {"image", "media", "font"}
_INIT = "window.__lcp=0;"


def vm_stats() -> dict:
    """Reclaimable memory (MB) + swap used (MB), cross-platform via psutil."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {"avail_mb": vm.available / 1048576, "swap_used_mb": sw.used / 1048576}
    except Exception:
        pass
    # macOS fallback
    out = subprocess.check_output(["vm_stat"]).decode()
    f = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            v = v.strip().rstrip(".")
            if v.isdigit():
                f[k.strip()] = int(v)
    avail_mb = sum(f.get(k, 0) for k in (
        "Pages free", "Pages inactive", "Pages speculative", "Pages purgeable",
    )) * _PAGE_SIZE / 1048576
    swap = subprocess.check_output(["sysctl", "-n", "vm.swapusage"]).decode()
    m = re.search(r"used = ([\d.]+)M", swap)
    return {"avail_mb": avail_mb, "swap_used_mb": float(m.group(1)) if m else 0.0}


def browser_rss_mb() -> float:
    """Sum RSS of Playwright-managed browsers only (ms-playwright in the path)."""
    try:
        import psutil
    except Exception:
        psutil = None
    if psutil is not None:
        total = 0
        for proc in psutil.process_iter(["cmdline", "memory_info"]):
            try:
                cmd = " ".join(proc.info["cmdline"] or [])
            except Exception:
                continue
            if "ms-playwright" not in cmd:
                continue
            if "--type=" not in cmd and "headless_shell" not in cmd:
                continue
            mi = proc.info.get("memory_info")
            if mi:
                total += mi.rss
        return total / 1048576
    # unix fallback
    out = subprocess.check_output(["ps", "-Ao", "rss,command"]).decode(errors="ignore")
    total = 0
    for line in out.splitlines()[1:]:
        if ("ms-playwright" in line and "--type=" in line) or ("ms-playwright" in line and "headless_shell" in line):
            try:
                total += int(line.split(None, 1)[0])
            except ValueError:
                pass
    return total / 1024


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=8)
    ap.add_argument("--full-payload", action="store_true")
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()

    before = vm_stats()
    print(f"BEFORE  available={before['avail_mb']:.0f} MB  swap={before['swap_used_mb']:.0f} MB  "
          f"browser_rss={browser_rss_mb():.0f} MB")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        contexts, pages = [], []
        for i in range(args.sessions):
            ctx = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-IN", timezone_id="Asia/Kolkata",
            )
            if not args.full_payload:
                async def _route(route):
                    if route.request.resource_type in _BLOCKED:
                        await route.abort()
                    else:
                        await route.continue_()
                await ctx.route("**/*", _route)
            await ctx.add_init_script(_INIT)
            pg = await ctx.new_page()
            contexts.append(ctx)
            pages.append(pg)

        print(f"opening {args.sessions} tabs in parallel ...", flush=True)
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[pg.goto(args.url, wait_until="load", timeout=45000) for pg in pages],
            return_exceptions=True,
        )
        ok = sum(1 for r in results if not isinstance(r, Exception))
        print(f"loaded {ok}/{args.sessions} in {time.perf_counter() - t0:.1f}s; parking tabs 6s ...", flush=True)
        await asyncio.sleep(6)

        after = vm_stats()
        parked_rss = browser_rss_mb()
        print(f"AFTER   available={after['avail_mb']:.0f} MB  swap={after['swap_used_mb']:.0f} MB  "
              f"browser_rss={parked_rss:.0f} MB")

        delta_avail = before["avail_mb"] - after["avail_mb"]
        delta_swap = after["swap_used_mb"] - before["swap_used_mb"]
        print("\n----- per-tab cost -----")
        print(f"system RAM drop    : {delta_avail:7.0f} MB  -> {delta_avail / args.sessions:6.0f} MB/tab")
        print(f"swap growth        : {delta_swap:7.0f} MB")
        print(f"browser RSS total  : {parked_rss:7.0f} MB  -> {parked_rss / args.sessions:6.0f} MB/tab")
        if delta_avail > 0:
            headroom = after["avail_mb"]
            print(f"\nextrapolation at {delta_avail / args.sessions:.0f} MB/tab and {headroom:.0f} MB free:")
            print(f"  ~{int(headroom // (delta_avail / args.sessions))} more tabs would exhaust currently-free RAM")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
