#!/usr/bin/env python3
"""
android_happy_flow.py — Native Android happy-flow check via Appium (UiAutomator2).

Prereqs:
  - Appium server running (with ANDROID_HOME set):  appium --port 4723
  - Emulator running with gajab app installed + logged in (package: com.gajab.buyerstore)

Flow: launch → home (logged in + products) → category → product detail → bargain.
Results are printed as JSON lines + a summary.
"""
from __future__ import annotations

import json
import sys
import time

from appium import webdriver
from appium.options.android import UiAutomator2Options


APP_PACKAGE = "com.gajab.buyerstore"
APP_ACTIVITY = ".MainActivity"
APPIUM_URL = "http://localhost:4723"


def connect() -> webdriver.Remote:
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = APP_PACKAGE
    options.app_activity = APP_ACTIVITY
    options.no_reset = True
    options.auto_grant_permissions = True
    options.new_command_timeout = 300
    driver = webdriver.Remote(APPIUM_URL, options=options)
    driver.update_settings({"waitForIdleTimeout": 0, "waitForSelectorTimeout": 0})
    return driver


def find(driver, xpath: str, timeout: int = 20):
    """Poll for an element matching the xpath."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements("xpath", xpath)
        if els:
            return els[0]
        time.sleep(0.5)
    return None


def find_by_desc(driver, desc_substring: str, timeout: int = 20):
    return find(driver, f'//*[contains(@content-desc, "{desc_substring}")]', timeout)


def step(name: str, ok: bool, detail: str = "") -> dict:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)
    return {"step": name, "status": "pass" if ok else "fail", "detail": detail}


def run_happy_flow() -> list[dict]:
    results = []
    driver = connect()
    try:
        # Reset to the app's home screen (relaunch without clearing login session)
        try:
            driver.terminate_app(APP_PACKAGE)
        except Exception:
            pass
        driver.activate_app(APP_PACKAGE)
        time.sleep(5)

        # ── Step 1: Home screen — logged in ──
        logged_in = find_by_desc(driver, "Suraj", timeout=10) is not None
        results.append(step("home_logged_in", logged_in, "profile header visible" if logged_in else "profile not found"))

        # ── Step 2: Product cards visible ──
        product_card = find_by_desc(driver, "Asking Price", timeout=15)
        results.append(step("home_products", product_card is not None,
                            "product cards with asking price" if product_card else "no product cards"))

        # ── Step 3: Bottom nav present ──
        nav_ok = all(find_by_desc(driver, t, timeout=5) for t in ["Bazaar", "Categories", "Bargains", "Alerts"])
        results.append(step("bottom_nav", nav_ok, "Bazaar/Categories/Bargains/Alerts"))

        # ── Step 4: Navigate to Categories ──
        cat_tab = find_by_desc(driver, "Categories", timeout=5)
        if cat_tab:
            cat_tab.click()
            time.sleep(3)
        cat_screen = find_by_desc(driver, "Home & Kitchen", timeout=10) is not None or \
                     find_by_desc(driver, "All", timeout=5) is not None
        results.append(step("category_nav", cat_screen, "category list visible" if cat_screen else "category screen not found"))

        # ── Step 5: Open a category ──
        cat_link = find_by_desc(driver, "Home & Kitchen", timeout=5)
        if cat_link:
            cat_link.click()
            time.sleep(4)
        # Category product cards are ImageViews with a product-name content-desc
        cat_products = find(driver, '//android.widget.ImageView[@content-desc != "" and @clickable="true"]', timeout=15)
        results.append(step("category_products", cat_products is not None,
                            "products in category" if cat_products else "no products in category"))

        # ── Step 6: Open product detail ──
        prod = cat_products if cat_products else find_by_desc(driver, "Asking Price", timeout=10)
        if prod:
            prod.click()
            time.sleep(4)
        bargain_btn = find_by_desc(driver, "Start Bargaining", timeout=15)
        price_visible = bargain_btn is not None or find_by_desc(driver, "Asking Price", timeout=5) is not None
        results.append(step("product_detail", price_visible,
                            "Start Bargaining visible" if bargain_btn else "product detail loaded"))

        # ── Step 7: Start Bargaining ──
        if bargain_btn:
            bargain_btn.click()
            time.sleep(3)
        bargain_open = find_by_desc(driver, "Offer Your Price", timeout=10) is not None or \
                       find_by_desc(driver, "Make an Offer", timeout=5) is not None
        results.append(step("bargain_open", bargain_open, "bargain modal opened" if bargain_open else "bargain modal not detected"))
    finally:
        driver.quit()
    return results


def main():
    results = run_happy_flow()
    failed = [r for r in results if r["status"] == "fail"]
    print("\n=== SUMMARY ===", flush=True)
    print(f"steps: {len(results)}, failed: {len(failed)}", flush=True)
    if failed:
        for r in failed:
            print(f"  FAIL {r['step']}: {r['detail']}", flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
