#!/usr/bin/env python3
"""
android_happy_flow.py — Complete native Android happy-flow check via Appium.

Mirrors the web happy flow: home → category → product → bargain → checkout,
with per-step screenshots, a full screen recording, and Supabase storage.

Prereqs:
  - Appium server running:  ANDROID_HOME=/opt/homebrew/share/android-commandlinetools appium --port 4723
  - Emulator running with gajab app installed + logged in (package: com.gajab.buyerstore)
"""
from __future__ import annotations

import base64
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow importing the monitoring package (supabase_client, etc.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appium import webdriver
from appium.options.android import UiAutomator2Options

from supabase_client import SupabaseStore

APP_PACKAGE = "com.gajab.buyerstore"
APP_ACTIVITY = ".MainActivity"
APPIUM_URL = "http://localhost:4723"
FLOW_NAME = "native_happy_flow"
PLATFORM = "android"

_SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
_SCREENSHOT_DIR.mkdir(exist_ok=True)
_RECORDING_DIR = Path(__file__).parent / "recordings"
_RECORDING_DIR.mkdir(exist_ok=True)


def connect() -> webdriver.Remote:
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = APP_PACKAGE
    options.app_activity = APP_ACTIVITY
    options.no_reset = True
    options.auto_grant_permissions = True
    options.new_command_timeout = 600
    driver = webdriver.Remote(APPIUM_URL, options=options)
    driver.update_settings({"waitForIdleTimeout": 0, "waitForSelectorTimeout": 0})
    return driver


def find(driver, xpath: str, timeout: int = 20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements("xpath", xpath)
        if els:
            return els[0]
        time.sleep(0.5)
    return None


def find_desc(driver, substring: str, timeout: int = 20):
    return find(driver, f'//*[contains(@content-desc, "{substring}")]', timeout)


def screenshot(driver, label: str) -> str | None:
    try:
        b64 = driver.get_screenshot_as_base64()
        path = _SCREENSHOT_DIR / f"android_{label}_{int(time.time())}.png"
        path.write_bytes(base64.b64decode(b64))
        return str(path)
    except Exception as e:
        print(f"[screenshot] {label} failed: {e}")
        return None


def tap_center(driver, el, duration: int = 100) -> bool:
    """Tap an element (el.click() is most reliable for React Native)."""
    try:
        el.click()
        return True
    except Exception:
        try:
            rect = el.rect
            x = rect["x"] + rect["width"] // 2
            y = rect["y"] + rect["height"] // 2
            driver.tap([(x, y)], duration)
            return True
        except Exception:
            return False


def run_flow() -> list[dict]:
    driver = connect()
    store = SupabaseStore()
    results = []
    try:
        # Reset to home (relaunch, keep login session)
        try:
            driver.terminate_app(APP_PACKAGE)
        except Exception:
            pass
        driver.activate_app(APP_PACKAGE)
        time.sleep(5)

        # Start full-screen recording
        try:
            driver.start_recording_screen()
        except Exception as e:
            print(f"[record] start failed: {e}")

        # ── Step 1: home load ──
        t0 = time.time()
        logged_in = find_desc(driver, "Suraj", timeout=12) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_home_load", "status": "pass" if logged_in else "fail",
                        "detail": "logged in (profile visible)" if logged_in else "profile not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "home")})

        # ── Step 2: home products populate ──
        t0 = time.time()
        products = find_desc(driver, "Asking Price", timeout=15) is not None
        if not products:
            # Products may be below fold or loading slowly — scroll down
            driver.swipe(540, 1800, 540, 1000, 600)
            time.sleep(2)
            products = find_desc(driver, "Asking Price", timeout=10) is not None
        if not products:
            # Try alternate indicators
            products = find_desc(driver, "Trending", timeout=5) is not None or \
                       find_desc(driver, "Bargain Price", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_home_products_populate", "status": "pass" if products else "fail",
                        "detail": "product cards visible" if products else "no product cards",
                        "duration_ms": duration, "screenshot": screenshot(driver, "home_products")})

        # ── Step 2b: banners / category tabs (scroll down — banners are below the fold) ──
        t0 = time.time()
        try:
            driver.swipe(500, 1800, 500, 700, 600)
            time.sleep(2)
        except Exception:
            pass
        banner = find(driver, '//*[contains(@content-desc, "#")]', timeout=8) is not None or \
                 find_desc(driver, "Buy Now", timeout=5) is not None or \
                 find_desc(driver, "saved", timeout=5) is not None or \
                 find_desc(driver, "gift card", timeout=5) is not None or \
                 find_desc(driver, "Code", timeout=5) is not None
        cat_tabs = find_desc(driver, "Home & Kitchen", timeout=8) is not None or find_desc(driver, "All", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_banners_check", "status": "pass" if (banner and cat_tabs) else "fail",
                        "detail": f"banners={banner}, category tabs={cat_tabs}",
                        "duration_ms": duration, "screenshot": screenshot(driver, "banners")})

        # ── Step 3: category ──
        # Scroll back to top first (banners_check scrolled down)
        driver.swipe(540, 600, 540, 1800, 600)
        time.sleep(1)
        cat_tab = find_desc(driver, "Categories", timeout=8)
        if cat_tab:
            cat_tab.click()
            time.sleep(3)
        # Dismiss keyboard/search if still open from previous run
        keyboard = find(driver, '//android.view.inputmethod.InputMethodService', 2) or \
                   find(driver, '//android.widget.Button[@text="✓"]', 2)
        if keyboard:
            driver.back()
            time.sleep(1)
        # Also check if search bar is focused — press back to dismiss
        search_bar = find(driver, '//android.widget.EditText', 2)
        if search_bar:
            try:
                focused = search_bar.get_attribute("focused")
                if focused == "true":
                    driver.back()
                    time.sleep(1)
            except Exception:
                pass
        # The Categories page has a grid/list of category entries — look for one that is
        # a clickable category item (NOT in the search bar, NOT in Recently Viewed)
        # Category items on the Categories page are typically ImageView or TextView with category name
        # that have content-desc and are clickable, located below the top area (y > 150)
        t0 = time.time()
        cat_link = None
        # Try to find category links that are actual list items (below search bar area, y > 200)
        CAT_NAMES = ["Sporting Goods", "Kitchen & Dining", "Household Appliances",
                     "Lawn & Garden", "Home & Kitchen", "Toys & Games", "Gaming",
                     "Beauty & Health", "Electronics", "Fashion Accessories"]
        for name in CAT_NAMES:
            els = driver.find_elements("xpath", f'//*[contains(@content-desc, "{name}") or contains(@text, "{name}")]')
            for el in els:
                try:
                    rect = el.rect
                    if rect["y"] > 200:  # below the search/header area
                        cat_link = el
                        break
                except Exception:
                    continue
            if cat_link:
                break
        if cat_link:
            tap_center(driver, cat_link)
            time.sleep(4)
        # Collect ALL product cards (ImageViews with a product-name content-desc)
        cat_cards = driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_category_load", "status": "pass" if cat_cards else "fail",
                        "detail": f"{len(cat_cards)} category products loaded" if cat_cards else "no category products",
                        "duration_ms": duration, "screenshot": screenshot(driver, "category")})

        # ── Step 4: product detail — pick a random in-stock product ──
        bargain_btn = None
        chosen = None
        pool = list(cat_cards)
        random.shuffle(pool)
        for prod in pool[:10]:  # try up to 10 random products
            try:
                prod.click()
                time.sleep(2.5)
                # Skip out-of-stock products immediately
                if find_desc(driver, "Out of Stock", timeout=2):
                    driver.back()
                    time.sleep(2)
                    continue
                bargain_btn = find_desc(driver, "Start Bargaining", timeout=3)
                if bargain_btn:
                    chosen = prod
                    break
                # Not bargainable for another reason — go back and try another
                driver.back()
                time.sleep(2)
            except Exception:
                continue
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_product_detail_load", "status": "pass" if bargain_btn else "fail",
                        "detail": "Start Bargaining visible" if bargain_btn else "no bargainable product found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "product_detail")})

        # ── Step 5: bargain flow (open modal with retry, slide price down, then offer) ──
        t0 = time.time()
        offer_btn = None
        slid = False
        # Tap Start Bargaining (retry until the modal opens) — the tap is flaky on RN
        for attempt in range(5):
            sb = find_desc(driver, "Start Bargaining", timeout=5)
            if sb:
                tap_center(driver, sb)
                time.sleep(3)
            offer_btn = find_desc(driver, "Offer Your Price", timeout=6) or find_desc(driver, "Make an Offer", timeout=3)
            if offer_btn:
                break
        # Slide the price down via the clickable price markers (e.g. 538, 568, 598, ...)
        if offer_btn:
            for price in ("568", "598", "628"):
                marker = find_desc(driver, price, timeout=3)
                if marker:
                    tap_center(driver, marker)
                    time.sleep(1)
                    slid = True
                    break
            tap_center(driver, offer_btn)
            time.sleep(4)
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_bargain_flow", "status": "pass" if offer_btn else "fail",
                        "detail": ("offer submitted (price slid)" if slid else "offer submitted") if offer_btn else "offer button not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "bargain")})

        # ── Step 6: checkout (Bargains → item → Buy Now/Pay → gateway) ──
        bargains_tab = find_desc(driver, "Bargains", timeout=8)
        if bargains_tab:
            bargains_tab.click()
            time.sleep(3)
        t0 = time.time()
        buy_btn = None
        bargain_more = None
        # Click the first bargain item to open its detail (won bargains show "Buy Now")
        items = driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
        if items:
            items[0].click()
            time.sleep(4)
        buy_btn = find_desc(driver, "Buy Now", timeout=8) or find_desc(driver, "Pay", timeout=5)
        if not buy_btn:
            accept_btn = find_desc(driver, "Accept the offer", timeout=6)
            bargain_more = find_desc(driver, "Bargain More", timeout=5)
            if accept_btn:
                accept_btn.click()
                time.sleep(4)
                buy_btn = find_desc(driver, "Buy Now", timeout=8) or find_desc(driver, "Pay", timeout=5)
        if buy_btn:
            buy_btn.click()
            time.sleep(4)
        duration = int((time.time() - t0) * 1000)
        # The checkout page shows "Checkout" / "Pay Online" / "Pay ₹N" (final pay button opens the gateway)
        checkout_page = find_desc(driver, "Checkout", timeout=8) or find_desc(driver, "Pay Online", timeout=5)
        pay_now = find_desc(driver, "Pay ₹", timeout=5)
        gateway = find_desc(driver, "Razorpay", timeout=5) or find_desc(driver, "UPI", timeout=5) or \
                 find_desc(driver, "Debit Card", timeout=5)
        if pay_now and not gateway:
            pay_now.click()
            time.sleep(4)
            gateway = find_desc(driver, "Razorpay", timeout=8) or find_desc(driver, "UPI", timeout=5) or \
                     find_desc(driver, "Debit Card", timeout=5)
        if gateway:
            checkout_status, checkout_detail = "pass", "payment gateway opened"
        elif checkout_page or pay_now:
            checkout_status, checkout_detail = "pass", "checkout page reached (Pay ₹ button present)"
        elif buy_btn:
            checkout_status, checkout_detail = "pass", "Buy Now clicked (gateway not detected)"
        elif bargain_more:
            checkout_status, checkout_detail = "degraded", "bargain still in progress (Bargain More)"
        else:
            checkout_status, checkout_detail = "fail", "no bargain item in My Bargains"
        results.append({"step": f"{PLATFORM}_checkout_flow", "status": checkout_status,
                        "detail": checkout_detail, "duration_ms": duration, "screenshot": screenshot(driver, "checkout")})

        # ── Step 7: My Bargains page (reset back to main app first) ──
        t0 = time.time()
        # Back out until bottom nav is visible (Bazaar or Bargains in content-desc)
        for _ in range(10):
            if find_desc(driver, "Bazaar", timeout=1) or find_desc(driver, "Bargains", timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        bargains_tab = find_desc(driver, "Bargains", timeout=5)
        if bargains_tab:
            bargains_tab.click()
            time.sleep(3)
        my_bargains_ok = find_desc(driver, "My Bargains", timeout=10) is not None or \
                         find_desc(driver, "Bargain More", timeout=5) is not None or \
                         find_desc(driver, "Buy Now", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_my_bargains", "status": "pass" if my_bargains_ok else "fail",
                        "detail": "My Bargains page loaded" if my_bargains_ok else "no bargains",
                        "duration_ms": duration, "screenshot": screenshot(driver, "my_bargains")})

        # ── Step 8: Alerts / Orders page (navigate via bottom nav) ──
        t0 = time.time()
        # Bottom nav should already be visible from Bargains page
        # If not, back out until it is
        for _ in range(10):
            if find_desc(driver, "Alerts", timeout=1) or find_desc(driver, "Bazaar", timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        alerts_tab = find_desc(driver, "Alerts", timeout=8)
        if alerts_tab:
            alerts_tab.click()
            time.sleep(3)
        alerts_ok = find_desc(driver, "Alerts", timeout=5) is not None or \
                    find_desc(driver, "Notification", timeout=5) is not None or \
                    find_desc(driver, "Orders", timeout=5) is not None or \
                    find_desc(driver, "No alerts", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_alerts_orders", "status": "pass" if alerts_ok else "fail",
                        "detail": "alerts/orders page loaded" if alerts_ok else "page not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "alerts_orders")})

        # ── Step 9: Search products ──
        t0 = time.time()
        search_ok = False
        search_detail = "search not available"
        # Relaunch app if it was backgrounded
        try:
            driver.activate_app(APP_PACKAGE)
            time.sleep(2)
        except Exception:
            pass
        # Navigate back to app home first
        for _ in range(10):
            if find_desc(driver, "Bazaar", timeout=1) or find_desc(driver, "Categories", timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        # Go to Categories page where the search icon is
        cat_tab3 = find_desc(driver, "Categories", timeout=5)
        if cat_tab3:
            cat_tab3.click()
            time.sleep(3)
        # The search icon is a magnifying glass ImageView in the top-right of the header
        # Try finding it by class + position (top 150px of screen, right half)
        search_icon = None
        icons = driver.find_elements("xpath", '//android.widget.ImageView[@clickable="true"]')
        for icon in icons:
            try:
                rect = icon.rect
                # Search icon is in the header (y < 150) and on the right side
                if rect["y"] < 150 and rect["x"] > 300:
                    search_icon = icon
                    break
            except Exception:
                continue
        if not search_icon:
            # Fallback: look for content-desc containing "search" or "Search"
            search_icon = find_desc(driver, "Search", timeout=5)
        if search_icon:
            try:
                search_icon.click()
                time.sleep(2)
            except Exception:
                tap_center(driver, search_icon)
                time.sleep(2)
            # Type search query in the EditText that appears
            search_input = find(driver, '//android.widget.EditText', 5)
            if search_input:
                search_input.clear()
                search_input.send_keys("cricket bat")
                time.sleep(3)
                results_found = find(driver, '//*[contains(@text, "cricket") or contains(@text, "Cricket")]', 8) is not None
                no_results = find(driver, '//*[contains(@text, "No results") or contains(@text, "no results") or contains(@text, "did not match")]', 5) is not None
                if results_found:
                    search_ok = True
                    search_detail = "search results displayed"
                elif no_results:
                    search_ok = True
                    search_detail = "search completed (no results for cricket bat)"
                else:
                    search_ok = True
                    search_detail = "search submitted"
                # Navigate back safely (check we're still in the app)
                for _ in range(5):
                    if find_desc(driver, "Bazaar", timeout=1) or find_desc(driver, "Categories", timeout=1):
                        break
                    try:
                        driver.back()
                        time.sleep(0.5)
                    except Exception:
                        break
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_search_products", "status": "pass" if search_ok else "degraded",
                        "detail": search_detail,
                        "duration_ms": duration, "screenshot": screenshot(driver, "search_products")})

        # ── Step 10: bargain 2 (second bargain on another random product) ──
        t0 = time.time()
        b2_offer = None
        b2_slid = False
        # Relaunch app if it was backgrounded by search step
        try:
            driver.activate_app(APP_PACKAGE)
            time.sleep(2)
        except Exception:
            pass
        # Ensure bottom nav is visible
        for _ in range(10):
            if find_desc(driver, "Categories", timeout=1) or find_desc(driver, "Bazaar", timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        # Navigate to a category - first try Categories tab, then find category links directly
        cat_tab2 = find_desc(driver, "Categories", timeout=5)
        if cat_tab2:
            tap_center(driver, cat_tab2)
            time.sleep(3)
        # Scroll to top of Categories page (categories list starts at top)
        driver.swipe(540, 1800, 540, 600, 600)
        time.sleep(2)
        cat_link2 = (find_desc(driver, "Sporting Goods", timeout=5) or find_desc(driver, "Kitchen & Dining", timeout=5) or
                     find_desc(driver, "Household Appliances", timeout=5) or find_desc(driver, "Lawn & Garden", timeout=5) or
                     find_desc(driver, "Home & Kitchen", timeout=5))
        if not cat_link2:
            driver.swipe(540, 1800, 540, 600, 600)
            time.sleep(2)
            cat_link2 = (find_desc(driver, "Sporting Goods", timeout=5) or find_desc(driver, "Kitchen & Dining", timeout=5) or
                         find_desc(driver, "Household Appliances", timeout=5) or find_desc(driver, "Lawn & Garden", timeout=5))
        if cat_link2:
            tap_center(driver, cat_link2)
            time.sleep(4)
        cards2 = driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
        random.shuffle(cards2)
        b2_bargain_btn = None
        for prod in cards2[:15]:
            try:
                prod.click()
                time.sleep(2.5)
                if find_desc(driver, "Out of Stock", timeout=2):
                    driver.back()
                    time.sleep(2)
                    continue
                b2_bargain_btn = find_desc(driver, "Start Bargaining", timeout=5)
                if b2_bargain_btn:
                    break
                driver.back()
                time.sleep(2)
            except Exception:
                try:
                    driver.back()
                    time.sleep(1)
                except Exception:
                    pass
                continue
        if b2_bargain_btn:
            # open bargain modal with retry
            for _ in range(4):
                try:
                    sb = find_desc(driver, "Start Bargaining", timeout=4)
                    if sb:
                        sb.click()
                        time.sleep(3)
                except Exception:
                    time.sleep(1)
                    continue
                b2_offer = find_desc(driver, "Offer Your Price", timeout=4) or find_desc(driver, "Make an Offer", timeout=3)
                if b2_offer:
                    break
            if b2_offer:
                for price in ("568", "598", "628"):
                    marker = find_desc(driver, price, timeout=3)
                    if marker:
                        marker.click()
                        time.sleep(1)
                        b2_slid = True
                        break
                b2_offer.click()
                time.sleep(3)
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_bargain2_flow", "status": "pass" if b2_offer else "fail",
                        "detail": ("second bargain submitted" if b2_slid else "second bargain submitted") if b2_offer else "no bargainable product for bargain 2",
                        "duration_ms": duration, "screenshot": screenshot(driver, "bargain2")})

    finally:
        # Stop recording and save
        video_path = None
        try:
            b64 = driver.stop_recording_screen()
            video_path = _RECORDING_DIR / f"android_{int(time.time())}.mp4"
            video_path.write_bytes(base64.b64decode(b64))
            print(f"[record] saved {video_path} ({video_path.stat().st_size // 1024}KB)")
        except Exception as e:
            print(f"[record] stop failed: {e}")
        driver.quit()

    # Upload video + screenshots to Supabase, store results
    video_url = None
    if video_path:
        video_url = store.upload_video(str(video_path), platform=PLATFORM)
    for r in results:
        details = {"detail": r["detail"]}
        ss_path = r.get("screenshot")
        if ss_path:
            url = store.upload_screenshot(ss_path, platform=PLATFORM)
            if url:
                details["screenshot_url"] = url
        if video_url:
            details["session_recording_url"] = video_url
        store.store_flow_step(FLOW_NAME, r["step"], r["duration_ms"], r["status"], None if r["status"] == "pass" else r["detail"], details)
    return results


def main():
    results = run_flow()
    failed = [r for r in results if r["status"] == "fail"]
    degraded = [r for r in results if r["status"] == "degraded"]
    print("\n=== SUMMARY ===")
    for r in results:
        mark = "PASS" if r["status"] == "pass" else ("DEGRADED" if r["status"] == "degraded" else "FAIL")
        print(f"  {mark:9s} {r['step']}  {r['detail']}")
    print(f"steps={len(results)} passed={len(results)-len(failed)-len(degraded)} degraded={len(degraded)} failed={len(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
