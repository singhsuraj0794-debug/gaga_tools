#!/usr/bin/env python3
"""
Authenticated user-journey load test for gajab staging.

Each concurrent "user" loops this journey until the duration ends:
  1. Land on the product page
  2. Complete the Bargain Flow (Start Bargaining -> slide price -> Offer Your
     Price -> wait for counter-offer -> Accept)
  3. Navigate back to the Homepage
  4. Open "View All" inside the Trending section/widget

Accepts the same CLI surface as concurrency_test.py (so ramp_test.py / the
Windows worker can drive it) plus a few flow-specific options.

Usage:
    python3 user_flow_test.py --sessions 2 --duration 60 --auth
    python3 user_flow_test.py --sessions 10 --duration 300 --auth --tabs --block-heavy
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

# ---- live progress (posted to monitor_server.py when --monitor-url is set) ----
_MON = {"sessions": 0, "running": 0, "journeys": 0, "completed": 0, "landed": 0, "offered": 0, "accepted": 0}
_MON_LOCK = threading.Lock()
_MON_STOP = threading.Event()


def _bump(**kw) -> None:
    with _MON_LOCK:
        for k, v in kw.items():
            _MON[k] = _MON.get(k, 0) + v


def _monitor_reporter(url: str, host: str, interval: float = 3.0) -> None:
    while not _MON_STOP.is_set():
        with _MON_LOCK:
            payload = dict(_MON)
        payload["host"] = host
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass
        _MON_STOP.wait(interval)

_SCREENSHOT_DIR = Path(__file__).parent / "flow_screenshots"
_DEFAULT_AUTH_STATE = Path(__file__).parent / ".gajab_session.json"

DEFAULT_PRODUCT = (
    "https://gajab.com/product-detail/"
    "prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914"
)
DEFAULT_HOME = "https://gajab.com/"
DEFAULT_TRENDING = "https://gajab.com/product-list/all?widgetId=11&position=WP2"

STAGING_PRODUCT = (
    "https://stg.gajab.com/product-detail/"
    "prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914"
)
STAGING_HOME = "https://stg.gajab.com/"
STAGING_TRENDING = "https://stg.gajab.com/product-list/all?widgetId=11&position=WP2"

_INIT_SCRIPT = "window.__lcp=0;"

# ---------- shared JS snippets (ported from happy_flow.py) ----------

_JS_CLICK_TEXT = """
(needles) => {
  for (const el of document.querySelectorAll('button, a, [role="button"], div, span')) {
    const t = (el.textContent || '').trim().toLowerCase();
    if (!t || t.length > 40) continue;
    if (!needles.some(n => t.includes(n))) continue;
    el.removeAttribute('disabled');
    el.scrollIntoView({behavior:'instant', block:'center'});
    const r = el.getBoundingClientRect();
    el.dispatchEvent(new MouseEvent('click', {
      view: window, bubbles: true, cancelable: true,
      clientX: r.left + r.width/2, clientY: r.top + r.height/2,
    }));
    return true;
  }
  return false;
}
"""

_JS_SET_SLIDER = """
() => {
  const ranges = document.querySelectorAll('input[type="range"]');
  for (const r of ranges) {
    const box = r.getBoundingClientRect();
    if (box.width > 20) {
      const max = parseFloat(r.max);
      const min = parseFloat(r.min) || 0;
      const target = isNaN(max) ? r.value : String(Math.round(min + (max - min) * 0.4));
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(r, target);
      const propsKey = Object.keys(r).find(k => k.startsWith('__reactProps$'));
      if (propsKey && r[propsKey] && r[propsKey].onChange) {
        try { r[propsKey].onChange({target: {value: target}}); } catch(e) {}
      }
      r.dispatchEvent(new Event('input', {bubbles: true}));
      r.dispatchEvent(new Event('change', {bubbles: true}));
      return {found: true, min: r.min, max: r.max, value: target, via: 'range input'};
    }
  }
  const nodes = document.querySelectorAll(
    '[data-testid*="preset"], [class*="preset"], [class*="chip"], [class*="price-marker"], [class*="PriceMarker"]');
  for (const el of nodes) {
    const box = el.getBoundingClientRect();
    if (box.width < 10 || box.height < 10) continue;
    const t = (el.textContent || '').trim();
    if (!['₹','preset','offer price'].some(n => t.includes(n) || n === '₹')) continue;
    el.scrollIntoView({behavior:'instant', block:'center'});
    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
    return {found: true, min: null, max: null, via: 'preset chip'};
  }
  return {found: false};
}
"""

_JS_TRENDING_VIEW_ALL = """
() => {
  for (const el of document.querySelectorAll('*')) {
    const t = (el.textContent || '').trim();
    if (t.length > 60) continue;
    if (!t.toLowerCase().includes('trending')) continue;
    let node = el;
    for (let d = 0; d < 6 && node; d++, node = node.parentElement) {
      for (const link of node.querySelectorAll('a')) {
        if ((link.textContent || '').toLowerCase().includes('view all')) return link.href;
      }
    }
  }
  const a = document.querySelector('a[href*="widgetId=11"]');
  return a ? a.href : null;
}
"""


def log(msg: str) -> None:
    print(f"[FLOW] {msg}", flush=True)


async def _wait_text(page, needles: list[str], timeout_ms: int) -> bool:
    js = """(needles) => {
        for (const el of document.querySelectorAll('button, a, [role="button"], div, span')) {
            const t = (el.textContent || '').trim().toLowerCase();
            if (t && t.length <= 40 && needles.some(n => t.includes(n))) return true;
        }
        return false;
    }"""
    try:
        await page.wait_for_function(js, arg=needles, timeout=timeout_ms)
        return True
    except Exception:
        return False


_JS_VISIBLE_BOX = """
(needles) => {
  for (const el of document.querySelectorAll('button, a, [role="button"]')) {
    const t = (el.textContent || '').trim().toLowerCase();
    if (!t || t.length > 40) continue;
    if (!needles.some(n => t.includes(n))) continue;
    el.scrollIntoView({behavior:'instant', block:'center'});
    const r = el.getBoundingClientRect();
    if (r.width > 5 && r.height > 5) return {x: r.left + r.width / 2, y: r.top + r.height / 2};
  }
  return null;
}
"""


async def _visible_text_box(page, needles: list[str]):
    try:
        return await page.evaluate(_JS_VISIBLE_BOX, needles)
    except Exception:
        return None


_PRODUCT_CURSOR = 0
_PRODUCT_LOCK = asyncio.Lock()


class _Barrier:
    """Simple asyncio barrier (works on Python 3.8+)."""

    def __init__(self, parties: int):
        self.parties = parties
        self.count = 0
        self.event = asyncio.Event()

    async def wait(self):
        self.count += 1
        if self.count >= self.parties:
            self.event.set()
        await self.event.wait()


async def _next_product(products: list[str]) -> str:
    """Rotate through the product pool so concurrent journeys avoid the same product."""
    global _PRODUCT_CURSOR
    if len(products) == 1:
        return products[0]
    async with _PRODUCT_LOCK:
        p = products[_PRODUCT_CURSOR % len(products)]
        _PRODUCT_CURSOR += 1
        return p


async def _click_text_real(page, needles: list[str]) -> bool:
    """Real mouse click on the first *visible* matching button/link."""
    box = await _visible_text_box(page, needles)
    if not box:
        return False
    try:
        await page.mouse.click(box["x"], box["y"])
        return True
    except Exception:
        return False


async def _detect_existing_offer(page) -> bool:
    """True when the product already has a bargained offer (no Start Bargaining)."""
    try:
        text = (await page.evaluate("() => document.body.innerText")).lower()
    except Exception:
        return False
    patterns = ["offer price expires", "bargains already", "bargain price", "your offer"]
    if any(p in text for p in patterns):
        return True
    return ("exclusive offer" in text or "buy now" in text) and "₹" in text and "asking price" in text


async def _dismiss_overlays(page) -> None:
    for _ in range(3):
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
        except Exception:
            pass


async def _set_pincode(page) -> None:
    """Best-effort delivery pincode so the bargain CTA renders."""
    try:
        btn = page.locator("button:has-text('Enter pincode'), button:has-text('Pincode'), button:has-text('city')")
        if await btn.count() > 0 and await btn.first.is_visible(timeout=1500):
            await btn.first.click()
            await asyncio.sleep(1.5)
            inp = page.locator("input[type='tel'], input[type='text'], input").last
            if await inp.is_visible():
                await inp.fill("400001")
                await asyncio.sleep(0.4)
                await page.locator(
                    "button:has-text('Submit'), button:has-text('Apply'), button:has-text('Done'), button[type='submit']"
                ).first.click(timeout=3000)
                await asyncio.sleep(0.6)
    except Exception:
        pass


async def _bargain_full(page, args) -> dict:
    """Start Bargaining -> slide -> Offer -> wait counter -> Accept. Returns step result."""
    r = {"start_ok": False, "slider_ok": False, "offer_ok": False, "accept_ok": False,
         "counter_seen": False, "existing_offer": False, "ms": None}
    t0 = time.perf_counter()
    try:
        await page.locator("#varient-price").scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass
    if not args.no_pincode:
        await _set_pincode(page)
    await _dismiss_overlays(page)

    # 1. Start Bargaining — only if a *visible* button exists; otherwise an
    #    existing bargained offer is showing (common on page reuse / repeat runs).
    start_needles = ["start bargaining", "bargain now", "negotiate"]
    start_loc = page.locator("button:has-text('Start Bargaining'):visible, button:has-text('Bargain Now'):visible").first
    try:
        if await start_loc.count() > 0:
            await start_loc.scroll_into_view_if_needed(timeout=3000)
            await start_loc.click(timeout=5000)
            r["start_ok"] = True
    except Exception:
        r["start_ok"] = False
    if not r["start_ok"]:
        box = await _visible_text_box(page, start_needles)
        if box:
            try:
                await page.mouse.click(box["x"], box["y"])
                r["start_ok"] = True
            except Exception:
                r["start_ok"] = False
    if not r["start_ok"]:
        deadline = time.time() + args.bargain_wait_ms / 1000.0
        while time.time() < deadline:
            if await _detect_existing_offer(page):
                r["existing_offer"] = True
                r["offer_ok"] = True
                r["ms"] = round((time.perf_counter() - t0) * 1000)
                return r
            box = await _visible_text_box(page, start_needles)
            if box:
                try:
                    await page.mouse.click(box["x"], box["y"])
                    r["start_ok"] = True
                    break
                except Exception:
                    pass
            await asyncio.sleep(1)
    if not r["start_ok"]:
        if await _detect_existing_offer(page):
            r["existing_offer"] = True
            r["offer_ok"] = True
        else:
            r["error"] = "start_bargaining_not_found"
        r["ms"] = round((time.perf_counter() - t0) * 1000)
        return r
    try:
        await page.wait_for_selector('input[type="range"]', timeout=args.modal_wait_ms)
    except Exception:
        await asyncio.sleep(args.step_delay_ms / 1000.0)
    await _dismiss_overlays(page)

    # 2. Slider / preset price
    for _ in range(args.slider_retries):
        try:
            res = await page.evaluate(_JS_SET_SLIDER)
        except Exception:
            res = {"found": False}
        if res.get("found"):
            r["slider_ok"] = True
            r["slider_via"] = res.get("via")
            break
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
    if not r["slider_ok"] and await _detect_existing_offer(page):
        # A slider never appeared because this account already has a bargain here.
        r["existing_offer"] = True
        r["offer_ok"] = True
        r["ms"] = round((time.perf_counter() - t0) * 1000)
        return r
    if not r["slider_ok"] and args.screenshots:
        try:
            _SCREENSHOT_DIR.mkdir(exist_ok=True)
            await page.screenshot(path=str(_SCREENSHOT_DIR / f"slider_fail_{int(time.time())}.png"))
        except Exception:
            pass
    await asyncio.sleep(0.5)

    # 3. Offer Your Price
    if not r.get("existing_offer"):
        for sel in ["button:has-text('Offer Your Price')", "button:has-text('Submit Offer')", "button:has-text('Make Offer')"]:
            loc = page.locator(sel)
            try:
                if await loc.count() > 0 and await loc.first.is_visible(timeout=1500):
                    await loc.first.click()
                    r["offer_ok"] = True
                    break
            except Exception:
                continue
        if not r["offer_ok"]:
            r["offer_ok"] = await _click_text_real(
                page, ["offer your price", "submit offer", "make an offer", "make offer", "send offer"])
        if not r["offer_ok"]:
            r["error"] = "offer_button_not_found"
            r["ms"] = round((time.perf_counter() - t0) * 1000)
            return r
        await asyncio.sleep(args.step_delay_ms / 1000.0)

    # 4. Wait for counter-offer and Accept
    deadline = time.time() + args.accept_wait_s
    while time.time() < deadline:
        accept = page.locator("button:has-text('Accept Offer'), button:has-text('Accept the offer'), button:has-text('Accept')").first
        bargain_more = page.locator("button:has-text('Bargain More'), button:has-text('Bargain more')").first
        try:
            if await accept.count() > 0 and await accept.is_visible(timeout=800):
                await accept.click(force=True)
                r["accept_ok"] = True
                r["counter_seen"] = True
                await asyncio.sleep(1.5)
                break
        except Exception:
            pass
        try:
            if await bargain_more.count() > 0 and await bargain_more.is_visible(timeout=500):
                r["counter_seen"] = True
        except Exception:
            pass
        await asyncio.sleep(1.5)

    r["ms"] = round((time.perf_counter() - t0) * 1000)
    return r


async def _journey(page, context, args, product: str) -> dict:
    """One full user journey. Returns per-step timings + success flags."""
    j = {"ok": False, "product": product, "land_ms": None, "land_ok": False, "home_ms": None, "home_ok": False,
         "trending_ms": None, "trending_ok": False, "bargain": None, "journey_ms": None}
    t0 = time.perf_counter()

    # 1. Land on product page
    try:
        resp = await page.goto(product, wait_until="domcontentloaded", timeout=args.timeout)
        j["land_status"] = resp.status if resp else None
        j["land_ms"] = round((time.perf_counter() - t0) * 1000)
        j["land_ok"] = await _wait_text(page, ["start bargaining", "bargain", "add to cart", "buy now"], args.timeout)
        j["final_url"] = page.url
    except Exception as exc:
        j["error"] = f"land: {type(exc).__name__}: {str(exc).splitlines()[0]}"

    # 2. Bargain flow (full completion) — optionally sync all sessions first so
    #    the PDP load happens for everyone, then bargaining starts together.
    if j["land_ok"] and getattr(args, "_barrier", None) is not None:
        try:
            await asyncio.wait_for(args._barrier.wait(), timeout=args.timeout / 1000)
        except Exception:
            pass
    if j["land_ok"]:
        j["bargain"] = await _bargain_full(page, args)

    # 3. Back to homepage
    if not args.bargain_only:
        t1 = time.perf_counter()
        try:
            resp = await page.goto(args.home, wait_until="domcontentloaded", timeout=args.timeout)
            j["home_status"] = resp.status if resp else None
            j["home_ok"] = await _wait_text(page, ["trending"], args.home_wait_ms)
        except Exception as exc:
            j["error"] = f"home: {type(exc).__name__}: {str(exc).splitlines()[0]}"
        j["home_ms"] = round((time.perf_counter() - t1) * 1000)

        # 4. Open View All in Trending
        t2 = time.perf_counter()
        if j["home_ok"]:
            try:
                href = await page.evaluate(_JS_TRENDING_VIEW_ALL)
                if not href:
                    href = args.trending_url
                if href:
                    resp = await page.goto(href, wait_until="domcontentloaded", timeout=args.timeout)
                    j["trending_url"] = href
                    j["trending_status"] = resp.status if resp else None
                    j["trending_ok"] = bool(resp and resp.status < 400)
                else:
                    j["trending_ok"] = False
                    j["error"] = "trending_view_all_not_found"
            except Exception as exc:
                j["error"] = f"trending: {type(exc).__name__}: {str(exc).splitlines()[0]}"
        j["trending_ms"] = round((time.perf_counter() - t2) * 1000)

    b = j.get("bargain") or {}
    bargain_done = bool(b.get("accept_ok") or b.get("existing_offer"))
    if args.bargain_only:
        j["ok"] = bool(j["land_ok"] and bargain_done)
    else:
        j["ok"] = bool(j["land_ok"] and j["home_ok"] and j["trending_ok"] and bargain_done)
    j["journey_ms"] = round((time.perf_counter() - t0) * 1000)
    return j


async def _bounded_close(obj) -> None:
    if obj is None:
        return
    try:
        await asyncio.wait_for(obj.close(), timeout=5)
    except BaseException:  # noqa: BLE001
        pass


async def run_session(pw, args, idx: int, shared_browser=None, auth_file=None, products=None) -> dict:
    browser = context = page = None
    own_browser = shared_browser is None
    errors: list[str] = []
    failed: list[str] = []
    journeys: list[dict] = []
    iter_errors: list[str] = []
    result = {"session": idx, "journeys": journeys, "iter_errors": iter_errors}
    _bump(running=1)
    try:
        if own_browser:
            browser = await pw.chromium.launch(
                channel="chrome" if args.chrome else None,
                headless=not args.headed,
                args=["--disable-dev-shm-usage", "--no-default-browser-check"],
            )
        else:
            browser = shared_browser
        ck = dict(
            viewport={"width": 430, "height": 932} if args.mobile else {"width": 1440, "height": 900},
            locale="en-IN", timezone_id="Asia/Kolkata", ignore_https_errors=True,
            is_mobile=args.mobile, has_touch=args.mobile,
            geolocation={"latitude": 19.4560, "longitude": 72.8054},
            permissions=["geolocation"] if args.mobile else [],
        )
        if auth_file:
            ck["storage_state"] = str(auth_file)
        context = await browser.new_context(**ck)
        context.set_default_timeout(min(args.timeout, 30000))
        context.set_default_navigation_timeout(args.timeout)
        await context.add_init_script(_INIT_SCRIPT)

        blocked = set()
        if args.block_heavy:
            blocked = {"image", "media", "font"}
        elif args.block_media:
            blocked = {"media", "font"}  # keep images (bargain needs them), cut video/fonts
        if blocked:
            async def _route(route):
                if route.request.resource_type in blocked:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", _route)
        blocked_types = blocked

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

        shot_done = False
        while True:
            try:
                prod = await _next_product(products or [args.product])
                j = await _journey(page, context, args, prod)
                journeys.append(j)
                _bump(journeys=1, completed=int(j["ok"]),
                      landed=int(j["land_ok"]),
                      offered=int((j.get("bargain") or {}).get("offer_ok")),
                      accepted=int((j.get("bargain") or {}).get("accept_ok")))
                if args.screenshots and not shot_done:
                    shot_done = True
                    _SCREENSHOT_DIR.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                    try:
                        await page.screenshot(path=str(_SCREENSHOT_DIR / f"{stamp}_s{idx:02d}.png"))
                    except Exception:
                        pass
            except Exception as exc:  # noqa: BLE001
                iter_errors.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")

            if args.max_iterations > 0 and len(journeys) >= args.max_iterations:
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
        _bump(running=-1)
        await _bounded_close(page)
        await _bounded_close(context)
        if own_browser:
            await _bounded_close(browser)

    def avg(key, src=None):
        vals = [(j if src is None else j).get(key) for j in (journeys if src is None else src)]
        vals = [v for v in vals if v is not None]
        return round(statistics.mean(vals), 1) if vals else None

    result["iterations"] = len(journeys)
    result["ok_iterations"] = sum(1 for j in journeys if j["ok"])
    result["ok"] = any(j["ok"] for j in journeys)
    result["journey_ms"] = avg("journey_ms")
    result["land_ms"] = avg("land_ms")
    result["home_ms"] = avg("home_ms")
    result["trending_ms"] = avg("trending_ms")
    result["bargain_ms"] = round(statistics.mean(
        [j["bargain"]["ms"] for j in journeys if j.get("bargain") and j["bargain"].get("ms") is not None]), 1) \
        if any(j.get("bargain") and j["bargain"].get("ms") is not None for j in journeys) else None
    result["accept_ok"] = any((j.get("bargain") or {}).get("accept_ok") for j in journeys)
    result["offer_ok"] = any((j.get("bargain") or {}).get("offer_ok") for j in journeys)
    if not journeys:
        result["error"] = iter_errors[0] if iter_errors else "no journeys"
    elif args.require_auth and not result["ok"]:
        result["error"] = "journey did not complete (check auth/session)"
    return result


def _ms(v) -> str:
    return "-" if v is None else f"{v:.0f}ms"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Authenticated user-journey load test")
    ap.add_argument("--url", dest="product", default=DEFAULT_PRODUCT, help="Product page URL")
    ap.add_argument("--product", dest="product", help="Product page URL (alias of --url)")
    ap.add_argument("--products", default="", help="Comma-separated product URLs, rotated across sessions (one account, many products)")
    ap.add_argument("--product-file", type=Path, default=None, help="File with one product URL per line")
    ap.add_argument("--product-offset", type=int, default=0, help="Start index into the product list (give each PC a distinct range)")
    ap.add_argument("--home", default=DEFAULT_HOME, help="Homepage URL")
    ap.add_argument("--trending-url", default=DEFAULT_TRENDING, help="Trending View All URL (fallback)")
    ap.add_argument("--staging", action="store_true", help="Target stg.gajab.com instead of live gajab.com")
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--peak", type=int, default=0)
    ap.add_argument("--duration", type=int, default=0)
    ap.add_argument("--think-time", type=int, default=0)
    ap.add_argument("--max-iterations", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=60000)
    ap.add_argument("--stagger", type=int, default=0)
    ap.add_argument("--sync", action="store_true", help="All sessions land on the PDP first (barrier), then bargain together")
    ap.add_argument("--tabs", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--chrome", action="store_true")
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--block-heavy", action="store_true")
    ap.add_argument("--block-media", action="store_true", help="Block video/fonts only (keep images) to cut RAM")
    ap.add_argument("--screenshots", action="store_true")
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--auth-state", type=Path, default=_DEFAULT_AUTH_STATE)
    ap.add_argument("--auth-dir", type=Path, default=None, help="Directory of storage_state *.json files (session pool, rotated across users)")
    ap.add_argument("--require-auth", action="store_true")
    ap.add_argument("--bargain-wait-ms", type=int, default=15000, help="Wait for Start Bargaining button")
    ap.add_argument("--accept-wait-s", type=int, default=15, help="Wait for counter-offer Accept button")
    ap.add_argument("--home-wait-ms", type=int, default=10000, help="Wait for Trending section on home")
    ap.add_argument("--step-delay-ms", type=int, default=2000, help="Fixed pause after open/offer steps")
    ap.add_argument("--modal-wait-ms", type=int, default=6000, help="Wait for the bargain slider modal")
    ap.add_argument("--slider-retries", type=int, default=8, help="Slider detection retries")
    ap.add_argument("--no-pincode", action="store_true", help="Skip the pincode modal step")
    ap.add_argument("--pdp-bargain", "--bargain-only", dest="bargain_only", action="store_true",
                    help="Flow = visit PDP then run the bargain flow (skip home + Trending)")
    ap.add_argument("--fast", action="store_true", help="Low-cost preset: quicker waits, no pincode, fewer retries")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--monitor-url", default="", help="POST live progress to monitor_server.py (/report)")
    ap.add_argument("--host-name", default="", help="Name shown in the live monitor")
    args = ap.parse_args()

    if args.staging:
        if args.product == DEFAULT_PRODUCT:
            args.product = STAGING_PRODUCT
        if args.home == DEFAULT_HOME:
            args.home = STAGING_HOME
        if args.trending_url == DEFAULT_TRENDING:
            args.trending_url = STAGING_TRENDING

    product_list: list[str] = []
    if args.product_file:
        product_list = [l.strip() for l in args.product_file.read_text().splitlines()
                        if l.strip() and not l.startswith("#")]
    if args.products:
        product_list += [p.strip() for p in args.products.split(",") if p.strip()]
    if not product_list:
        product_list = [args.product]
    args._products = product_list
    globals()["_PRODUCT_CURSOR"] = max(0, args.product_offset)

    auth_pool: list[Path] = []
    if args.auth:
        if args.auth_dir:
            auth_pool = sorted(args.auth_dir.glob("*.json"))
            if not auth_pool:
                ap.error(f"no *.json session files found in {args.auth_dir}")
        elif args.auth_state.exists():
            auth_pool = [args.auth_state]
        else:
            ap.error(f"auth state not found: {args.auth_state} (run setup_login.py, or use --auth-dir)")
    args._auth_pool = auth_pool
    if args.block_heavy:
        print("[note] --block-heavy ignored: the bargain journey needs the full page to render")
        args.block_heavy = False
    if args.fast:
        args.accept_wait_s = min(args.accept_wait_s, 8)
        args.home_wait_ms = min(args.home_wait_ms, 6000)
        args.step_delay_ms = min(args.step_delay_ms, 800)
        args.modal_wait_ms = min(args.modal_wait_ms, 5000)
        args.slider_retries = min(args.slider_retries, 5)
        args.no_pincode = True
    args._deadline = time.monotonic() + args.duration if args.duration > 0 else None
    args._barrier = _Barrier(args.sessions) if args.sync else None

    print("=" * 68)
    print(f"User-journey concurrency — {args.sessions} users")
    print(f"Product : {args.product if len(product_list) == 1 else str(len(product_list)) + ' products (rotated per session)'}")
    print(f"Home    : {args.home}")
    print(f"Browser : {'Google Chrome' if args.chrome else 'bundled Chromium'} | {'headed' if args.headed else 'headless'}")
    if auth_pool:
        print(f"Auth    : yes — {len(auth_pool)} session(s) rotated across {args.sessions} users")
    else:
        print("Auth    : no (guest)")
    print(f"Duration: {args.duration}s | think-time {args.think_time}ms | peak {args.peak or 'unlimited'}")
    if args.monitor_url:
        with _MON_LOCK:
            _MON["sessions"] = args.sessions
        host_name = args.host_name or socket.gethostname()
        threading.Thread(target=_monitor_reporter, args=(args.monitor_url, host_name), daemon=True).start()
        print(f"Monitor : reporting live to {args.monitor_url} as '{host_name}'")
    print("=" * 68, flush=True)

    def session_file(i: int):
        return auth_pool[i % len(auth_pool)] if auth_pool else None

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

        async def guarded(i):
            af = session_file(i)
            if sem is None:
                return await run_session(pw, args, i + 1, shared_browser, af, product_list)
            async with sem:
                return await run_session(pw, args, i + 1, shared_browser, af, product_list)

        async def delayed(i):
            if args.stagger > 0 and i > 0:
                await asyncio.sleep(args.stagger * i / 1000)
            return await guarded(i)

        tasks = [asyncio.create_task(delayed(i)) for i in range(args.sessions)]
        hard_cap = (args.duration + args.timeout / 1000 + 60) if args.duration > 0 else (args.timeout / 1000 + 120)
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=hard_cap)
        except asyncio.TimeoutError:
            print(f"\n[!] hard cap {hard_cap:.0f}s reached — cancelling", flush=True)
            for t in tasks:
                t.cancel()
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            results = [r for r in gathered if isinstance(r, dict)]
        if shared_browser is not None:
            await _bounded_close(shared_browser)
    total_wall = time.perf_counter() - wall_start

    results.sort(key=lambda r: r["session"])
    all_journeys = [j for r in results for j in r["journeys"]]

    print("\nPer-session results (averages across each user's journeys):")
    print(f"{'#':>3} {'ok':>3} {'jnys':>5} {'okj':>4} {'journey':>8} {'land':>7} {'bargain':>8} {'home':>7} {'trend':>7} {'acc':>4} {'err':>4}")
    for r in results:
        acc = sum(1 for j in r["journeys"] if (j.get("bargain") or {}).get("accept_ok"))
        print(
            f"{r['session']:>3} {('Y' if r['ok'] else 'N'):>3} {r['iterations']:>5} {r['ok_iterations']:>4} "
            f"{_ms(r['journey_ms']):>8} {_ms(r['land_ms']):>7} {_ms(r['bargain_ms']):>8} "
            f"{_ms(r['home_ms']):>7} {_ms(r['trending_ms']):>7} {acc:>4} "
            f"{str(r['console_errors'] + r['failed_requests']):>4}"
        )

    ok_sessions = sum(1 for r in results if r["ok"])
    total_journeys = len(all_journeys)
    ok_journeys = sum(1 for j in all_journeys if j["ok"])
    accepted = sum(1 for j in all_journeys if (j.get("bargain") or {}).get("accept_ok"))
    offered = sum(1 for j in all_journeys if (j.get("bargain") or {}).get("offer_ok"))
    landed = sum(1 for j in all_journeys if j["land_ok"])
    homed = sum(1 for j in all_journeys if j["home_ok"])
    trended = sum(1 for j in all_journeys if j["trending_ok"])

    def summ(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return "-"
        return f"min {min(vals):.0f}ms / avg {statistics.mean(vals):.0f}ms / p95 {sorted(vals)[min(len(vals)-1,int(0.95*(len(vals)-1)))]:.0f}ms / max {max(vals):.0f}ms"

    print("\nSummary:")
    print(f"  sessions           : {ok_sessions}/{len(results)} fully successful")
    print(f"  journeys           : {total_journeys} total, {ok_journeys} fully completed")
    print(f"  step success       : land {landed}/{total_journeys} | offer {offered} | accepted {accepted} | home {homed} | trending {trended}")
    if total_journeys and total_wall > 0:
        print(f"  throughput         : {total_journeys / total_wall:.3f} journeys/s ({total_journeys / total_wall * 60:.1f}/min)")
    print(f"  total wall time    : {total_wall:.1f}s")
    print(f"  journey time       : {summ([j['journey_ms'] for j in all_journeys])}")
    print(f"  land product       : {summ([j['land_ms'] for j in all_journeys])}")
    print(f"  bargain flow       : {summ([(j.get('bargain') or {}).get('ms') for j in all_journeys])}")
    print(f"  home load          : {summ([j['home_ms'] for j in all_journeys])}")
    print(f"  trending view all  : {summ([j['trending_ms'] for j in all_journeys])}")
    print(f"  console+net errors : {sum(r['console_errors'] + r['failed_requests'] for r in results)}")

    if args.json:
        journey_vals = [j["journey_ms"] for j in all_journeys if j.get("journey_ms") is not None]
        payload = {
            "product": args.product, "home": args.home, "sessions": args.sessions,
            "total_wall_s": round(total_wall, 2), "journeys": total_journeys,
            "ok_journeys": ok_journeys, "accepted": accepted, "offered": offered,
            "landed": landed, "homed": homed, "trended": trended,
            # keys ramp_test.py reads for its summary/stop logic
            "ok_sessions": ok_sessions,
            "ok_loads": ok_journeys,
            "cancelled": sum(1 for r in results if r.get("cancelled")),
            "wall_ms_avg": round(statistics.mean(journey_vals), 1) if journey_vals else None,
            "cpu_task_ms_avg": None,
            "heap_mb_avg": None,
            "results": results,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"[json] wrote {args.json}")
    print("=" * 68)
    _MON_STOP.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
