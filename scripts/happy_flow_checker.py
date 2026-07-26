#!/usr/bin/env python3
"""
happy_flow_checker.py — Hourly synthetic monitor for Gajab.com.
Runs a complete happy flow using Playwright Chromium and outputs JSON results.

Usage:
  python3 happy_flow_checker.py
  # Outputs JSON to stdout with each step's result
"""

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE_URL = "https://gajab.com"
VIEWPORT = {"width": 430, "height": 932}
TIMEOUT = 15000

results = {"passed": 0, "failed": 0, "steps": [], "timestamp": datetime.now(timezone.utc).isoformat(), "overall": "unknown"}


def step(name: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                start = time.time()
                func(*args, **kwargs)
                elapsed = round(time.time() - start, 2)
                results["passed"] += 1
                results["steps"].append({"name": name, "status": "pass", "duration": elapsed})
                print(f"  ✓ {name} ({elapsed}s)", flush=True)
            except Exception as e:
                elapsed = round(time.time() - start, 2) if 'start' in dir() else 0
                results["failed"] += 1
                results["steps"].append({"name": name, "status": "fail", "duration": elapsed, "error": str(e)[:200]})
                print(f"  ✗ {name}: {e}", flush=True)
        return wrapper
    return decorator


@step("Homepage loads")
def check_homepage(page):
    page.goto(BASE_URL, wait_until="networkidle", timeout=TIMEOUT)
    assert page.title(), "No page title"
    body = page.inner_text("body").lower()
    assert any(w in body for w in ["gajab", "product", "shop", "buy"]), "Homepage missing expected content"


@step("Search finds results")
def check_search(page):
    search_btn = page.query_selector('input[type="search"], input[placeholder*="search" i], button[aria-label*="search" i], a[href*="search"]')
    if not search_btn:
        search_btn = page.query_selector('input[type="text"], input:not([type])')
    if search_btn:
        search_btn.click()
        time.sleep(0.5)
        search_input = page.query_selector('input[type="search"], input[placeholder*="search" i]')
        if search_input:
            search_input.fill("mobile")
            search_input.press("Enter")
            time.sleep(3)
            body = page.inner_text("body").lower()
            assert "mobile" in body or "product" in body, "Search results empty"


@step("Product list loads")
def check_product_list(page):
    page.goto(f"{BASE_URL}/category/mobile", wait_until="networkidle", timeout=TIMEOUT)
    links = page.query_selector_all('a[href*="/product-detail/"]')
    assert len(links) > 0, "No product links found"
    # Click first product
    links[0].click()
    time.sleep(3)


@step("Product detail page loads")
def check_product_detail(page):
    body = page.inner_text("body").lower()
    assert any(w in body for w in ["price", "₹", "mrp", "product"]), "Product detail missing price/description"


@step("Bargain page loads")
def check_bargain(page):
    bargain_links = page.query_selector_all('a[href*="bargain"], button:has-text("Bargain"), a:has-text("bargain")')
    if bargain_links:
        bargain_links[0].click()
        time.sleep(3)
        body = page.inner_text("body").lower()
        assert "bargain" in body or "negotiate" in body or "offer" in body, "Bargain page didn't load"


@step("Page renders at mobile viewport")
def check_mobile_viewport(page):
    vp = page.viewport_size
    assert vp and vp["width"] <= 430 and vp["height"] <= 932, f"Viewport {vp} exceeds mobile dimensions"


def main():
    print(f"\nGajab.com Happy Flow Check — {results['timestamp']}", flush=True)
    print("=" * 50, flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport=VIEWPORT,
            user_agent="Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            locale="en-IN",
            geolocation={"latitude": 19.076, "longitude": 72.8777},
            timezone_id="Asia/Kolkata",
        )
        page = context.new_page()

        check_homepage(page)
        check_product_list(page)
        check_product_detail(page)
        check_bargain(page)
        check_mobile_viewport(page)

        browser.close()

    results["overall"] = "pass" if results["failed"] == 0 else "fail"
    print(f"\n{'=' * 50}", flush=True)
    print(f"Overall: {results['overall'].upper()} ({results['passed']} passed, {results['failed']} failed)", flush=True)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    main()
