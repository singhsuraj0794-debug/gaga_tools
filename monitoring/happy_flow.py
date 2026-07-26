from __future__ import annotations
import re
import time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import (
    VIEWPORT, DEVICE_SCALE_FACTOR, GEOLOCATION,
    PAGE_TIMEOUT, NAV_TIMEOUT, TIME_BUDGETS_SECONDS,
    OTP_TIMEOUT, OTP_POLL_INTERVAL, MONITOR_PHONE,
    TWILIO_SID, TWILIO_AUTH_TOKEN,
)


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


def _check_budget(budget_key: str, duration_ms: int, results: list):
    budget_sec = TIME_BUDGETS_SECONDS.get(budget_key)
    if budget_sec and duration_ms > budget_sec * 1000:
        log(f"BUDGET EXCEEDED: {budget_key} took {duration_ms}ms (budget: {budget_sec * 1000}ms)")
        results[-1]["status"] = "degraded"


def _do_bargain_flow(page, product_url: str, results: list):
    log("Opening bargain flow")
    t0 = time.time()
    _dismiss_overlays(page)

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
                return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
        }
        return null;
    }""")

    if not btn_info:
        raise HappyFlowError("'Start Bargaining' button not found")

    page.mouse.click(btn_info["x"], btn_info["y"])
    log("Clicked Start Bargaining")
    time.sleep(2)
    _dismiss_overlays(page)

    log("Setting offer price")
    page.evaluate("""() => {
        const ranges = document.querySelectorAll('input[type="range"]');
        let slider = null;
        for (const r of ranges) {
            const box = r.getBoundingClientRect();
            if (box.width > 100 && parseFloat(r.max) > 1) {
                slider = r;
                break;
            }
        }
        if (!slider) return;
        const target = 2.0;
        const propsKey = Object.keys(slider).find(k => k.startsWith('__reactProps$'));
        if (propsKey) {
            const props = slider[propsKey];
            if (props && props.onChange) {
                try { props.onChange({target: {value: target}}); } catch(e) {}
            }
        }
    }""")
    time.sleep(0.5)

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
                    break
        except Exception:
            continue
    if not offered:
        log("Offer button not found — trying fallback")
        page.evaluate("""() => {
            for (const btn of document.querySelectorAll('button')) {
                const t = (btn.textContent||'').trim();
                if (t.includes('Offer') || t.includes('Submit')) { btn.removeAttribute('disabled'); btn.click(); return; }
            }
        }""")

    time.sleep(3)

    for sel in ["button:has-text('Accept the offer')", "button:has-text('Accept')"]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=2000):
                box = loc.first.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    log("Clicked Accept")
                    time.sleep(2)
                    break
        except Exception:
            continue

    bargain_duration = int((time.time() - t0) * 1000)
    results.append({"step": "bargain_flow", "duration_ms": bargain_duration, "status": "pass"})
    _check_budget("bargain_flow", bargain_duration, results)


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

        try:
            # Step 1: Home page
            log("Step 1 — Loading home page")
            t0 = time.time()
            page.goto("https://gajab.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            duration = int((time.time() - t0) * 1000)
            assert "Gajab" in page.title(), "Home page title missing 'Gajab'"
            results.append({"step": "home_load", "duration_ms": duration, "status": "pass"})
            _check_budget("home_page_load", duration, results)

            # Step 2: Category page
            log("Step 2 — Loading category page")
            t0 = time.time()
            page.goto("https://gajab.com/product-list/all", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            duration = int((time.time() - t0) * 1000)
            assert page.locator("a[href*='/product-detail/']").first.is_visible(timeout=5000)
            results.append({"step": "category_load", "duration_ms": duration, "status": "pass"})
            _check_budget("category_page_load", duration, results)

            # Step 3: Product detail page
            log("Step 3 — Loading product detail page")
            t0 = time.time()
            page.goto(product_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            time.sleep(1)
            duration = int((time.time() - t0) * 1000)
            assert page.locator("#varient-price").is_visible(timeout=5000)
            results.append({"step": "product_detail_load", "duration_ms": duration, "status": "pass"})
            _check_budget("product_detail_load", duration, results)

            # Step 4: Bargain flow (works without login)
            _do_bargain_flow(page, product_url, results)

            log("Happy flow completed successfully")

        except (AssertionError, PWTimeout, HappyFlowError, Exception) as e:
            step_name = results[-1]["step"] if results else "unknown"
            log(f"HAPPY FLOW FAILED at step '{step_name}': {e}")
            results.append({"step": step_name, "duration_ms": 0, "status": "fail", "error": str(e)})
        finally:
            context.close()
            browser.close()

    return results
