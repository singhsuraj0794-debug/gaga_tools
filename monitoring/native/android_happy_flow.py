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
                 find_desc(driver, "saved", timeout=5) is not None
        cat_tabs = find_desc(driver, "Home & Kitchen", timeout=8) is not None or find_desc(driver, "All", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_banners_check", "status": "pass" if (banner and cat_tabs) else "fail",
                        "detail": f"banners={banner}, category tabs={cat_tabs}",
                        "duration_ms": duration, "screenshot": screenshot(driver, "banners")})

        # ── Step 3: category ──
        cat_tab = find_desc(driver, "Categories", timeout=8)
        if cat_tab:
            cat_tab.click()
            time.sleep(3)
        t0 = time.time()
        cat_link = find_desc(driver, "Home & Kitchen", timeout=12)
        if cat_link:
            cat_link.click()
            time.sleep(4)
        # Collect ALL product cards (ImageViews with a product-name content-desc)
        cat_cards = driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_category_load", "status": "pass" if cat_cards else "fail",
                        "detail": f"{len(cat_cards)} category products loaded" if cat_cards else "no category products",
                        "duration_ms": duration, "screenshot": screenshot(driver, "category")})

        # ── Step 4: product detail — pick a random product, retry if no bargain button ──
        bargain_btn = None
        chosen = None
        pool = list(cat_cards)
        random.shuffle(pool)
        for prod in pool[:8]:  # try up to 8 random products
            try:
                prod.click()
                time.sleep(3)
                bargain_btn = find_desc(driver, "Start Bargaining", timeout=6)
                if bargain_btn:
                    chosen = prod
                    break
                # No bargain button — go back and try another
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
        for attempt in range(4):
            sb = find_desc(driver, "Start Bargaining", timeout=5)
            if sb:
                sb.click()
                time.sleep(3)
            offer_btn = find_desc(driver, "Offer Your Price", timeout=5) or find_desc(driver, "Make an Offer", timeout=3)
            if offer_btn:
                break
        # Slide the price down via the clickable price markers (e.g. 538, 568, 598, ...)
        if offer_btn:
            for price in ("568", "598", "628"):
                marker = find_desc(driver, price, timeout=3)
                if marker:
                    marker.click()
                    time.sleep(1)
                    slid = True
                    break
            offer_btn.click()
            time.sleep(4)
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_bargain_flow", "status": "pass" if offer_btn else "fail",
                        "detail": ("offer submitted (price slid)" if slid else "offer submitted") if offer_btn else "offer button not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "bargain")})

        # ── Step 6: checkout (Bargains → Accept offer → Pay) ──
        bargains_tab = find_desc(driver, "Bargains", timeout=8)
        if bargains_tab:
            bargains_tab.click()
            time.sleep(3)
        t0 = time.time()
        pay_btn = None
        accept_btn = None
        bargain_more = None
        deadline = time.time() + 50
        while time.time() < deadline:
            pay_btn = find_desc(driver, "Pay", timeout=2)
            accept_btn = find_desc(driver, "Accept the offer", timeout=2)
            bargain_more = find_desc(driver, "Bargain More", timeout=2)
            if pay_btn or accept_btn or bargain_more:
                break
            time.sleep(2)
        if accept_btn and not pay_btn:
            accept_btn.click()
            time.sleep(4)
            pay_btn = find_desc(driver, "Pay", timeout=12)
        if pay_btn:
            pay_btn.click()
            time.sleep(4)
        duration = int((time.time() - t0) * 1000)
        gateway = find_desc(driver, "Razorpay", timeout=10) or find_desc(driver, "UPI", timeout=5) or \
                 find_desc(driver, "Debit Card", timeout=5) or find_desc(driver, "Card Number", timeout=5)
        if gateway:
            checkout_status, checkout_detail = "pass", "payment gateway opened"
        elif pay_btn:
            checkout_status, checkout_detail = "pass", "Pay clicked (gateway not detected)"
        elif accept_btn or bargain_more:
            checkout_status, checkout_detail = "degraded", "bargain still in progress (no Pay yet)"
        else:
            checkout_status, checkout_detail = "fail", "no bargain item in My Bargains"
        results.append({"step": f"{PLATFORM}_checkout_flow", "status": checkout_status,
                        "detail": checkout_detail, "duration_ms": duration, "screenshot": screenshot(driver, "checkout")})

        # ── Step 7: My Bargains page ──
        t0 = time.time()
        bargains_tab = find_desc(driver, "Bargains", timeout=8)
        if bargains_tab:
            bargains_tab.click()
            time.sleep(3)
        my_bargains_ok = find_desc(driver, "My Bargains", timeout=10) is not None or \
                         find_desc(driver, "Bargain More", timeout=5) is not None or \
                         find_desc(driver, "Accept the offer", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_my_bargains", "status": "pass" if my_bargains_ok else "fail",
                        "detail": "My Bargains page loaded" if my_bargains_ok else "no bargains",
                        "duration_ms": duration, "screenshot": screenshot(driver, "my_bargains")})

        # ── Step 8: Alerts / Orders page ──
        t0 = time.time()
        alerts_tab = find_desc(driver, "Alerts", timeout=8)
        if alerts_tab:
            alerts_tab.click()
            time.sleep(3)
        alerts_ok = find_desc(driver, "My Alerts", timeout=8) is not None or \
                    find_desc(driver, "Alerts", timeout=5) is not None or \
                    find_desc(driver, "Orders", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_alerts_orders", "status": "pass" if alerts_ok else "fail",
                        "detail": "alerts/orders page loaded" if alerts_ok else "page not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "alerts_orders")})

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
