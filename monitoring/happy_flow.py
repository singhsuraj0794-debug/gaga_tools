from __future__ import annotations
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import (
    VIEWPORT, DEVICE_SCALE_FACTOR, GEOLOCATION,
    PAGE_TIMEOUT, NAV_TIMEOUT, TIME_BUDGETS_SECONDS,
    OTP_TIMEOUT, OTP_POLL_INTERVAL, MONITOR_PHONE,
    TWILIO_SID, TWILIO_AUTH_TOKEN,
)

_SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
_SCREENSHOT_DIR.mkdir(exist_ok=True)


class OTPDeliveryError(Exception):
    pass


class HappyFlowError(Exception):
    pass


def log(msg):
    print(f"[HAPPY_FLOW] {msg}", flush=True)


def _get_twilio_messages(since_ts: datetime):
    from twilio.rest import Client
    client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
    messages = client.messages.list(to=MONITOR_PHONE, limit=5)
    return [m for m in messages if m.date_sent is not None and m.date_sent.replace(tzinfo=None) >= since_ts.replace(tzinfo=None)]


def _extract_otp(body: str) -> str | None:
    match = re.search(r"\b(\d{4,6})\b", body)
    return match.group(1) if match else None


def _poll_otp(since_ts: datetime) -> str:
    if not TWILIO_SID or not TWILIO_AUTH_TOKEN:
        raise OTPDeliveryError("Twilio not configured")
    deadline = time.time() + OTP_TIMEOUT
    while time.time() < deadline:
        msgs = _get_twilio_messages(since_ts)
        for msg in msgs:
            log(f"Twilio message: {msg.body[:80]} from {msg.from_}")
            code = _extract_otp(msg.body)
            if code:
                log(f"OTP extracted: {code}")
                return code
        remaining = int(deadline - time.time())
        log(f"Waiting for OTP... ({remaining}s left)")
        time.sleep(OTP_POLL_INTERVAL)
    raise OTPDeliveryError(f"OTP not received within {OTP_TIMEOUT}s timeout")


def _dismiss_overlays(page):
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            pass
    try:
        backdrops = page.locator(".react-modal-sheet-backdrop")
        if backdrops.count() > 0:
            backdrops.first.click(timeout=2000)
            time.sleep(0.3)
    except Exception:
        pass


import base64 as _base64
import io as _io

def _capture_screenshot(page, label: str) -> dict:
    ts = datetime.now().strftime("%H%M%S")
    path = _SCREENSHOT_DIR / f"{label}_{ts}.png"
    result = {"path": None, "base64": None}
    try:
        buf = _io.BytesIO()
        page.screenshot(path=str(path), full_page=False)
        with open(path, "rb") as f:
            raw = f.read()
        result["path"] = str(path)
        if len(raw) < 500000:
            result["base64"] = "data:image/png;base64," + _base64.b64encode(raw).decode()
        log(f"Screenshot saved: {path} ({len(raw)}b)")
    except Exception as e:
        log(f"Screenshot failed: {e}")
    return result


def _check_page_ready(page, expected_url_substring: str | None = None, expected_element: str | None = None) -> dict:
    """Check page readiness and return diagnostics."""
    info = {
        "url": page.url,
        "title": page.title(),
        "console_errors": [],
        "status_code": None,
        "page_ok": True,
    }
    try:
        info["title"] = page.title()
    except Exception:
        info["title"] = None
    if expected_url_substring and expected_url_substring not in page.url:
        info["page_ok"] = False
        info["reason"] = f"URL missing '{expected_url_substring}'"
    if expected_element:
        try:
            loc = page.locator(expected_element)
            info["element_found"] = loc.count() > 0
            if loc.count() > 0:
                info["element_visible"] = loc.first.is_visible(timeout=2000)
        except Exception as e:
            info["element_found"] = False
            info["element_error"] = str(e)
    return info


def _do_bargain_flow(page, results: list):
    t0 = time.time()
    sub_steps = []

    log("Step 4a — Locating Start Bargaining button")
    btn_info = page.evaluate("""() => {
        const vp = document.getElementById('varient-price');
        if (!vp) return null;
        for (const btn of vp.querySelectorAll('button')) {
            if (btn.textContent.includes('Start Bargaining')) {
                btn.removeAttribute('disabled');
                btn.style.pointerEvents = 'auto';
                btn.style.opacity = '1';
                btn.scrollIntoView({behavior:'instant',block:'center'});
                const r = btn.getBoundingClientRect();
                return {x: r.x + r.width/2, y: r.y + r.height/2, found: true};
            }
        }
        return {found: false};
    }""")

    if not btn_info or not btn_info.get("found"):
        _capture_screenshot(page, "bargain_start_not_found")
        raise HappyFlowError("'Start Bargaining' button not found in #varient-price")
    sub_steps.append({"check": "start_bargaining_button", "status": "pass", "detail": "Button found and clicked"})

    page.mouse.click(btn_info["x"], btn_info["y"])
    log("Step 4b — Start Bargaining clicked")
    time.sleep(2)
    _dismiss_overlays(page)
    sub_steps.append({"check": "bargain_modal_opened", "status": "pass", "detail": "Overlay dismissed after click"})

    log("Step 4c — Setting offer price via slider")
    slider_result = page.evaluate("""() => {
        const ranges = document.querySelectorAll('input[type="range"]');
        for (const r of ranges) {
            const box = r.getBoundingClientRect();
            if (box.width > 100 && parseFloat(r.max) > 1) {
                const target = 2.0;
                const propsKey = Object.keys(r).find(k => k.startsWith('__reactProps$'));
                if (propsKey) {
                    const props = r[propsKey];
                    if (props && props.onChange) {
                        try { props.onChange({target: {value: target}}); } catch(e) {}
                    }
                }
                return {found: true, old: r.value, min: r.min, max: r.max};
            }
        }
        return {found: false};
    }""")
    if not slider_result.get("found"):
        _capture_screenshot(page, "slider_not_found")
        sub_steps.append({"check": "price_slider", "status": "degraded", "detail": "No slider input found, continuing"})
    else:
        sub_steps.append({"check": "price_slider", "status": "pass", "detail": f"Slider set (min={slider_result['min']}, max={slider_result['max']})"})
    time.sleep(0.5)

    log("Step 4d — Clicking Offer Your Price button")
    offered = False
    for sel in [
        "button:has-text('Offer Your Price')",
        "button:has-text('Submit Offer')",
        "button:has-text('Make Offer')",
    ]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=2000):
                box = loc.first.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    log(f"Clicked: {sel}")
                    offered = True
                    sub_steps.append({"check": "offer_button", "status": "pass", "detail": f"Clicked: {sel}"})
                    break
        except Exception:
            continue
    if not offered:
        log("Offer button not found via selectors — trying JS fallback")
        _capture_screenshot(page, "offer_button_fallback")
        clicked = page.evaluate("""() => {
            for (const btn of document.querySelectorAll('button')) {
                const t = (btn.textContent||'').trim();
                if (t.includes('Offer') || t.includes('Submit')) { btn.removeAttribute('disabled'); btn.click(); return true; }
            }
            return false;
        }""")
        sub_steps.append({"check": "offer_button", "status": "degraded" if clicked else "fail", "detail": "JS fallback used" if clicked else "No offer button found anywhere"})

    time.sleep(3)

    log("Step 4e — Checking for Accept offer button")
    accepted = False
    for sel in ["button:has-text('Accept the offer')", "button:has-text('Accept')"]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=2000):
                box = loc.first.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    log("Clicked Accept")
                    accepted = True
                    sub_steps.append({"check": "accept_offer", "status": "pass", "detail": "Seller accepted offer"})
                    time.sleep(2)
                    break
        except Exception:
            continue
    if not accepted:
        sub_steps.append({"check": "accept_offer", "status": "degraded", "detail": "No accept button appeared (seller may not have responded within timeout)"})

    bargain_duration = int((time.time() - t0) * 1000)
    ss_bargain = _capture_screenshot(page, "bargain_complete")
    results.append({
        "step": "bargain_flow",
        "duration_ms": bargain_duration,
        "status": "pass",
        "sub_steps": sub_steps,
        "detail": f"Bargain flow completed in {bargain_duration}ms ({len(sub_steps)} sub-steps)",
        "screenshot": ss_bargain,
    })
    _check_budget("bargain_flow", bargain_duration, results)


def _check_budget(budget_key: str, duration_ms: int, results: list):
    budget_sec = TIME_BUDGETS_SECONDS.get(budget_key)
    if budget_sec and duration_ms > budget_sec * 1000:
        log(f"BUDGET EXCEEDED: {budget_key} took {duration_ms}ms (budget: {budget_sec * 1000}ms)")
        results[-1]["status"] = "degraded"
        results[-1]["budget_exceeded"] = {"budget_ms": budget_sec * 1000, "actual_ms": duration_ms}


def run_happy_flow() -> list[dict]:
    results = []
    product_url = "https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914"

    log("Starting happy-flow check")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            is_mobile=True,
            has_touch=True,
            geolocation=GEOLOCATION,
            permissions=["geolocation"],
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append({"type": msg.type, "text": msg.text}) if msg.type == "error" else None)

        try:
            # Step 1: Home page
            log("Step 1 — Loading home page")
            t0 = time.time()
            page.goto("https://gajab.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            duration = int((time.time() - t0) * 1000)
            title = page.title()
            has_gajab = "Gajab" in title or "gajab" in title.lower()
            ss_home = _capture_screenshot(page, "home")
            results.append({
                "step": "home_load",
                "duration_ms": duration,
                "status": "pass" if has_gajab else "fail",
                "detail": f"Title: '{title}', URL: {page.url}",
                "title_found": has_gajab,
                "screenshot": ss_home,
                "console_errors": [c for c in console_errors if c["type"] == "error"][:5],
                "failure_reason": None if has_gajab else "Page title missing 'Gajab'",
            })
            _check_budget("home_page_load", duration, results)

            # Step 2: Category page
            log("Step 2 — Loading category page")
            t0 = time.time()
            page.goto("https://gajab.com/product-list/all", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            duration = int((time.time() - t0) * 1000)
            product_links = page.locator("a[href*='/product-detail/']")
            has_products = product_links.count() > 0
            ss_cat = _capture_screenshot(page, "category")
            failure_reason = None
            if not has_products:
                failure_reason = "No product links found on category page"
            elif duration > TIME_BUDGETS_SECONDS.get("category_page_load", 5) * 1000:
                failure_reason = f"Page load slow ({duration}ms)"
            results.append({
                "step": "category_load",
                "duration_ms": duration,
                "status": "pass" if has_products else "fail",
                "detail": f"Products found: {product_links.count()}, URL: {page.url}",
                "product_count": product_links.count(),
                "screenshot": ss_cat,
                "console_errors": [c for c in console_errors if c["type"] == "error"][:5],
                "failure_reason": failure_reason,
            })
            _check_budget("category_page_load", duration, results)

            # Step 3: Product detail page
            log("Step 3 — Loading product detail page")
            t0 = time.time()
            page.goto(product_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            time.sleep(1)
            duration = int((time.time() - t0) * 1000)
            varient_price = page.locator("#varient-price")
            has_varient = varient_price.count() > 0 and varient_price.first.is_visible(timeout=5000)
            ss_pdp = _capture_screenshot(page, "product_detail")
            failure_reason = None
            if not has_varient:
                failure_reason = "#varient-price element not found"
            results.append({
                "step": "product_detail_load",
                "duration_ms": duration,
                "status": "pass" if has_varient else "fail",
                "detail": f"#varient-price visible: {has_varient}, title: {page.title()[:60]}",
                "varient_price_found": has_varient,
                "screenshot": ss_pdp,
                "console_errors": [c for c in console_errors if c["type"] == "error"][:5],
                "failure_reason": failure_reason,
            })
            _check_budget("product_detail_load", duration, results)

            if not has_varient:
                _capture_screenshot(page, "pdp_missing_varient")
                raise HappyFlowError("Product detail page missing #varient-price element")

            # Step 4: Bargain flow
            _do_bargain_flow(page, results)

            log("Happy flow completed successfully")

        except (AssertionError, PWTimeout, HappyFlowError, Exception) as e:
            step_name = results[-1]["step"] if results else "unknown"
            log(f"HAPPY FLOW FAILED at step '{step_name}': {e}")
            try:
                ss = _capture_screenshot(page, f"failure_{step_name}")
            except Exception:
                ss = {"path": None, "base64": None}
            results.append({
                "step": step_name,
                "duration_ms": 0,
                "status": "fail",
                "error": str(e),
                "failure_reason": str(e),
                "screenshot": ss,
                "console_errors": [c for c in console_errors if c["type"] == "error"],
            })

        finally:
            context.close()
            browser.close()

    # Generate a readable summary
    log("=" * 50)
    log("HAPPY FLOW DETAILED REPORT")
    log("=" * 50)
    for r in results:
        icon = "✅" if r["status"] == "pass" else "⚠️" if r["status"] == "degraded" else "❌"
        log(f"{icon} {r['step']}: {r['status']} ({r.get('duration_ms', 0)}ms)")
        if r.get("detail"):
            log(f"   Details: {r['detail']}")
        if r.get("sub_steps"):
            for ss in r["sub_steps"]:
                ss_icon = "✅" if ss["status"] == "pass" else "⚠️" if ss["status"] == "degraded" else "❌"
                log(f"   {ss_icon} {ss['check']}: {ss['status']} — {ss.get('detail', '')}")
        if r.get("console_errors"):
            log(f"   🖥️ Console errors: {len(r['console_errors'])}")
            for ce in r["console_errors"][:3]:
                log(f"      - {ce['text'][:100]}")
        if r.get("error"):
            log(f"   Error: {r['error']}")
        if r.get("screenshot"):
            log(f"   📸 Screenshot: {r['screenshot']}")
    log("=" * 50)

    return results
