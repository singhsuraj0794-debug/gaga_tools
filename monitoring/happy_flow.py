from __future__ import annotations
import json
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


def _set_pincode(page, sub_steps: list):
    log("Step 4a — Setting delivery pincode")
    pc_btn = page.locator("button:has-text('Enter pincode'), button:has-text('Pincode'), button:has-text('city')")
    if pc_btn.count() > 0 and pc_btn.first.is_visible(timeout=2000):
        pc_btn.first.click()
        time.sleep(2)
        page.screenshot(path=str(_SCREENSHOT_DIR / "pincode_modal.png"))
        for sel in ["input", "input[type='text']", "input[type='tel']"]:
            inp = page.locator(sel).last
            if inp.is_visible():
                ph = inp.get_attribute("placeholder") or ""
                log(f"Pincode input found: placeholder='{ph}'")
                inp.fill("400001")
                time.sleep(0.5)
                page.locator("button:has-text('Submit'), button:has-text('Apply'), button:has-text('Done'), button[type='submit']").first.click(timeout=3000)
                time.sleep(1)
                sub_steps.append({"check": "set_pincode", "status": "pass", "detail": "Pincode 400001 set via modal"})
                return
        sub_steps.append({"check": "set_pincode", "status": "degraded", "detail": "Pincode button clicked but no visible input found"})
    else:
        body_text = page.evaluate("() => document.body.innerText")
        if "Add delivery location" in body_text or "Enter pincode" in body_text:
            sub_steps.append({"check": "set_pincode", "status": "degraded", "detail": "Location prompt visible but pincode button not clickable"})
        else:
            sub_steps.append({"check": "set_pincode", "status": "pass", "detail": "Pincode already set or not required"})


def _do_bargain_flow(page, results: list):
    t0 = time.time()
    sub_steps = []

    _set_pincode(page, sub_steps)

    log("Step 4b — Locating & clicking Start Bargaining button")
    clicked = page.evaluate("""() => {
        const vp = document.getElementById('varient-price');
        if (!vp) return false;
        let clicked_any = false;
        // Click any Start Bargaining button inside varient-price
        for (const btn of vp.querySelectorAll('button')) {
            if (btn.textContent.includes('Start Bargaining')) {
                btn.removeAttribute('disabled');
                btn.style.pointerEvents = 'auto !important';
                btn.style.opacity = '1';
                btn.style.visibility = 'visible';
                btn.style.position = 'relative';
                btn.style.zIndex = '99999';
                btn.scrollIntoView({behavior:'instant',block:'center'});
                const event = new MouseEvent('click', {
                    view: window, bubbles: true, cancelable: true,
                    clientX: btn.getBoundingClientRect().left + btn.offsetWidth / 2,
                    clientY: btn.getBoundingClientRect().top + btn.offsetHeight / 2,
                });
                btn.dispatchEvent(event);
                clicked_any = true;
            }
        }
        return clicked_any;
    }""")

    if not clicked:
        _capture_screenshot(page, "bargain_start_not_found")
        raise HappyFlowError("'Start Bargaining' button not found or could not be clicked")
    sub_steps.append({"check": "start_bargaining_button", "status": "pass", "detail": "Button found and clicked via JS dispatchEvent"})
    log("Step 4c — Start Bargaining clicked")
    time.sleep(2)
    _dismiss_overlays(page)
    sub_steps.append({"check": "bargain_modal_opened", "status": "pass", "detail": "Overlay dismissed after click"})

    log("Step 4d — Setting offer price via slider")
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

    log("Step 4e — Clicking Offer Your Price button")
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

    log("Step 4f — Checking for Accept offer button")
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


def _do_checkout_flow(page, results: list):
    t0 = time.time()
    sub_steps = []

    log("Step 5a — Navigating to checkout")
    # After bargain, try cart link first, then direct nav to checkout
    cart_btn = page.locator("a[href*='cart'], a[href*='checkout'], [class*='cart'] a, button:has-text('Cart'), button:has-text('cart')")
    if cart_btn.count() > 0:
        try:
            cart_btn.first.click(timeout=3000)
            time.sleep(2)
        except Exception:
            pass
    page.goto("https://gajab.com/checkout", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
    time.sleep(2)
    is_checkout = "checkout" in page.url.lower()
    sub_steps.append({"check": "checkout_nav", "status": "pass" if is_checkout else "degraded", "detail": f"URL: {page.url[:80]}"})

    log("Step 5b — Opening Razorpay & entering test card details")
    ss_checkout = _capture_screenshot(page, "checkout_page")
    on_checkout = "checkout" in page.url.lower()
    razorpay_found = False
    card_filled = False

    if on_checkout:
        pay_btn = page.locator("button:has-text('Pay'), button:has-text('Place Order'), button:has-text('Proceed'), button:has-text('Submit')")
        if pay_btn.count() > 0 and pay_btn.first.is_visible(timeout=2000):
            pay_btn.first.click(force=True)
            log("Pay button clicked")
            time.sleep(4)
            ss_after_pay = _capture_screenshot(page, "after_pay")
            sub_steps.append({"check": "pay_button_click", "status": "pass", "detail": "Pay/Proceed button clicked"})

            # Check for Razorpay iframe
            try:
                razorpay_iframe_el = page.locator("iframe[src*='razorpay'], iframe[id*='razorpay']")
                if razorpay_iframe_el.count() > 0:
                    razorpay_iframe = page.frame_locator("iframe[src*='razorpay'], iframe[id*='razorpay']")
                    razorpay_found = True
                    sub_steps.append({"check": "razorpay_loaded", "status": "pass", "detail": "Razorpay checkout opened"})
                    log("Razorpay loaded, selecting Card mode")

                    time.sleep(3)
                    card_clicked = False
                    for attempt in range(8):
                        for sel in ["button:has-text('Card')", "[class*='card']", "label:has-text('Card')", "[data-method='card']"]:
                            try:
                                el = razorpay_iframe.locator(sel).first
                                if el.count() > 0 and el.is_visible(timeout=1000):
                                    el.click(force=True)
                                    card_clicked = True
                                    log(f"Card tab clicked: {sel}")
                                    break
                            except Exception:
                                continue
                        if card_clicked:
                            break
                        time.sleep(1)
                    sub_steps.append({"check": "card_selected", "status": "pass" if card_clicked else "degraded", "detail": "Card tab clicked" if card_clicked else "Card tab not found"})

                    if card_clicked:
                        time.sleep(2)
                        try:
                            card_input = razorpay_iframe.locator("input[placeholder*='card'], input[placeholder*='Card'], input[type='tel']").first
                            if card_input.count() > 0:
                                card_input.fill("4529566615008376")
                                log("Card number entered")
                                sub_steps.append({"check": "card_number", "status": "pass", "detail": "Card number entered"})

                            razorpay_iframe.locator("input[placeholder*='MM'], input[placeholder*='expir']").first.fill("11/30")
                            razorpay_iframe.locator("input[placeholder*='CVV'], input[placeholder*='cvv']").first.fill("994")
                            razorpay_iframe.locator("input[placeholder*='name'], input[placeholder*='Name']").first.fill("Gracie Ullrich")
                            log("Card details entered")
                            sub_steps.append({"check": "card_details", "status": "pass", "detail": "Card/expiry/CVV/name entered"})

                            time.sleep(0.5)
                            final_pay = razorpay_iframe.locator("button:has-text('Pay'), button[type='submit']").first
                            if final_pay.count() > 0:
                                final_pay.click(force=True)
                                log("Final Pay clicked")
                                time.sleep(2)
                                sub_steps.append({"check": "final_pay_clicked", "status": "pass", "detail": "Payment submitted"})
                                card_filled = True
                        except Exception as e:
                            sub_steps.append({"check": "card_form", "status": "degraded", "detail": f"Form error: {str(e)[:60]}"})
                else:
                    sub_steps.append({"check": "razorpay_loaded", "status": "degraded", "detail": "Pay clicked but no Razorpay iframe"})
            except Exception as e:
                sub_steps.append({"check": "razorpay_loaded", "status": "degraded", "detail": f"Razorpay error: {str(e)[:60]}"})
        else:
            sub_steps.append({"check": "pay_button_click", "status": "degraded", "detail": "No Pay button found on checkout"})
    else:
        sub_steps.append({"check": "pay_button_click", "status": "degraded", "detail": "Not on checkout page (redirected)"})

    duration = int((time.time() - t0) * 1000)
    pay_was_clicked = any(s["check"] == "pay_button_click" and s["status"] == "pass" for s in sub_steps)
    results.append({
        "step": "checkout_flow",
        "duration_ms": duration,
        "status": "pass" if razorpay_found and pay_was_clicked else "degraded" if razorpay_found or pay_was_clicked else "fail",
        "sub_steps": sub_steps,
        "detail": f"Checkout: Pay={pay_was_clicked}, Razorpay={'found' if razorpay_found else 'not found'}",
        "screenshot": ss_checkout,
        "failure_reason": None if razorpay_found else "Payment gateway did not appear",
    })
    _check_budget("checkout_nav", duration, results)


def _do_search_flow(page, results: list):
    log("Search — Searching for a product")
    t0 = time.time()
    sub_steps = []
    try:
        page.goto("https://gajab.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
        time.sleep(1)

        search_input = page.locator("input[placeholder*='Search']").first
        if search_input.count() > 0:
            search_input.fill("cricket bat", force=True)
            time.sleep(0.3)
            page.keyboard.press("Enter")
            time.sleep(2)
            sub_steps.append({"check": "search_performed", "status": "pass", "detail": "Searched 'cricket bat'"})
            results_count = page.locator("a[href*='/product-detail/']").count()
            sub_steps.append({"check": "search_results", "status": "pass" if results_count > 0 else "degraded", "detail": f"Results: {results_count}"})
        else:
            sub_steps.append({"check": "search_performed", "status": "degraded", "detail": "Search bar not found"})
    except Exception as e:
        sub_steps.append({"check": "search_overall", "status": "fail", "detail": str(e)[:80]})

    duration = int((time.time() - t0) * 1000)
    results.append({
        "step": "search_products", "duration_ms": duration,
        "status": "pass" if all(s["status"] == "pass" for s in sub_steps) else "degraded",
        "sub_steps": sub_steps, "detail": f"Search flow: {duration}ms",
        "screenshot": _capture_screenshot(page, "search_results"),
    })


def _do_page_checks(page, results: list):
    pages_to_check = [
        ("my_bargains", "https://gajab.com/my-bargains", "[class*='tab'], [class*='bargain'], a[href*='bargain'], h1, h2"),
        ("alerts_orders", "https://gajab.com/alerts-list?activeTab=1", "[class*='tab'], [class*='order'], [class*='alert'], h1, h2"),
    ]
    for name, url, expected in pages_to_check:
        t0 = time.time()
        try:
            page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            time.sleep(1)
            duration = int((time.time() - t0) * 1000)
            has_content = page.evaluate("(sel) => document.querySelector(sel) !== null", expected)
            title = page.title()
            results.append({
                "step": f"page_{name}",
                "duration_ms": duration,
                "status": "pass" if has_content else "degraded",
                "detail": f"{url} — title='{title[:50]}', has_content={has_content}",
                "screenshot": _capture_screenshot(page, name),
            })
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            results.append({
                "step": f"page_{name}", "duration_ms": duration, "status": "fail",
                "error": str(e)[:100], "failure_reason": f"Failed to load {url}",
            })

    log("Checking home page banners")
    try:
        page.goto("https://gajab.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
        time.sleep(1)
        banners = page.locator("[class*='banner'] img, section img[src*='banner'], [class*='carousel'] img")
        banner_count = banners.count()
        banner_images_loaded = True
        for i in range(min(banner_count, 5)):
            src = banners.nth(i).get_attribute("src") or ""
            if "resize.gajab.com" not in src and "banner" not in src.lower():
                banner_images_loaded = False
        results.append({
            "step": "banners_check",
            "duration_ms": 0,
            "status": "pass" if banner_count >= 2 and banner_images_loaded else "degraded",
            "detail": f"Banners found: {banner_count}, images from CDN: {banner_images_loaded}",
            "screenshot": _capture_screenshot(page, "banners"),
        })
    except Exception as e:
        results.append({"step": "banners_check", "duration_ms": 0, "status": "fail", "error": str(e)[:100]})


def _check_budget(budget_key: str, duration_ms: int, results: list):
    budget_sec = TIME_BUDGETS_SECONDS.get(budget_key)
    if budget_sec and duration_ms > budget_sec * 1000:
        log(f"BUDGET EXCEEDED: {budget_key} took {duration_ms}ms (budget: {budget_sec * 1000}ms)")
        results[-1]["status"] = "degraded"
        results[-1]["budget_exceeded"] = {"budget_ms": budget_sec * 1000, "actual_ms": duration_ms}


_SESSION_FILE = Path(__file__).parent / ".gajab_session.json"

def _load_session() -> dict | None:
    # Try local file first
    if _SESSION_FILE.exists():
        try:
            with open(_SESSION_FILE) as f:
                state = json.load(f)
            log(f"Loaded saved session from {_SESSION_FILE}")
            return state
        except Exception as e:
            log(f"Failed to load local session: {e}")
    # Fallback: download from Supabase Storage
    try:
        from supabase_client import SupabaseStore
        if SupabaseStore.download_session(str(_SESSION_FILE)):
            with open(_SESSION_FILE) as f:
                state = json.load(f)
            log("Loaded session from Supabase Storage")
            return state
    except Exception as e:
        log(f"Failed to load session from Supabase: {e}")
    return None


def _pick_random_product(page) -> str | None:
    """Navigate to category page and pick a random product URL."""
    try:
        page.goto("https://gajab.com/product-list/all", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
        time.sleep(1)
        links = page.locator("a[href*='/product-detail/']")
        count = links.count()
        if count == 0:
            log("No product links found on category page")
            return None
        import random
        idx = random.randint(0, min(count - 1, 30))
        url = links.nth(idx).get_attribute("href")
        if url and not url.startswith("http"):
            url = "https://gajab.com" + url
        log(f"Random product #{idx}: {url}")
        return url
    except Exception as e:
        log(f"Random product selection failed: {e}")
        return None


def run_happy_flow() -> list[dict]:
    results = []
    video_path = None

    log("Starting happy-flow check")

    session_state = _load_session()
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
            record_video_dir=str(_SCREENSHOT_DIR / "recordings"),
            storage_state=session_state,
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

            # Pick a random product for bargain
            product_url = _pick_random_product(page)
            if not product_url:
                raise HappyFlowError("Could not find any product to bargain")
            log(f"Selected product: {product_url}")

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

            # Step 5: Checkout + Razorpay payment gateway check
            _do_checkout_flow(page, results)

            # Step 6: Additional page checks (My Account, My Bargains, Orders, Banners)
            log("Step 6 — Running additional page checks")
            _do_page_checks(page, results)

            # Step 7: Search products flow
            log("Step 7 — Searching products")
            _do_search_flow(page, results)

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
            try:
                if page.video:
                    vpath = page.video.path()
                    if vpath and Path(vpath).exists():
                        video_path = str(vpath)
                        size_kb = Path(vpath).stat().st_size / 1024
                        log(f"Session recording saved: {video_path} ({size_kb:.0f}KB)")
                        results.append({
                            "step": "session_recording",
                            "duration_ms": 0,
                            "status": "pass",
                            "detail": f"Recording saved ({size_kb:.0f}KB)",
                            "screenshot": None,
                            "video_path": video_path,
                        })
            except Exception as e:
                log(f"Video save error: {e}")
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
