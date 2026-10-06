"""
Enrich all products in Supabase with price, category, brand.
Uses Playwright + saved session (reliable) with parallel scraping.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from config import SUPABASE_URL, SUPABASE_KEY

API = f"{SUPABASE_URL}/rest/v1/products"
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gajab_session.json")
PINCODE = "400001"

CATEGORY_KEYWORDS = [
    "Home & Garden", "Toys & Games", "Fashion Accessories", "Stationery",
    "Luggage & Bags", "Sporting Goods", "Electronics", "Automobile Accessories",
]


def api_request(url, method="GET", data=None):
    req = Request(url, data=json.dumps(data).encode() if data else None, headers=HEADERS, method=method)
    resp = urlopen(req, timeout=30)
    raw = resp.read().decode()
    if not raw or method in ("PATCH", "POST", "DELETE"):
        return {}
    return json.loads(raw)


def fetch_products_missing():
    all_products = []
    offset = 0
    PAGE = 1000
    while True:
        url = f"{API}?select=id,name,url,price,category,brand&limit={PAGE}&offset={offset}"
        page = api_request(url)
        if not page:
            break
        all_products.extend(page)
        offset += PAGE
        if len(page) < PAGE:
            break
    return all_products


def scrape_product_playwright(url, session_state):
    """Scrape price, brand (SOLD BY), category from product page."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=session_state, viewport={"width": 430, "height": 932}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        time.sleep(1.5)
        html = page.content()
        title = page.title()
        body = ""
        try:
            body = page.inner_text("body")
        except Exception:
            pass
        ctx.close()
        browser.close()

    result = {}

    # Inactive product check
    if "Product | Gajab" in title or title == "Product | Gajab":
        return {"_inactive": True}

    # Price from body: ₹695, ₹464 (MRP, asking)
    prices = re.findall(r"₹\s*([\d,]+)", body)
    if prices:
        nums = [float(p.replace(",", "")) for p in prices]
        if nums:
            max_price = max(nums)
            min_price = min(nums)
            result["mrp_price"] = f"₹{max_price:,.0f}"
            result["price"] = f"₹{min_price:,.0f}"

    # Brand from SOLD BY
    sold_by = re.findall(r"SOLD BY\s*\n?\s*([A-Za-z0-9 &._-]+)", body)
    if sold_by:
        result["brand"] = sold_by[0].strip()

    # Category from body
    for cat in CATEGORY_KEYWORDS:
        if cat in body:
            result["category"] = cat
            break

    # Fallback: category from JSON-LD breadcrumb
    if "category" not in result:
        jsonld = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        for j in jsonld:
            for cat in CATEGORY_KEYWORDS:
                if cat in j:
                    result["category"] = cat
                    break
            if "category" in result:
                break

    return result


def enrich_product(product, session_state):
    """Enrich a single product."""
    updates = {}
    url = product.get("url", "")

    # Skip if no URL
    if not url:
        return None, "no_url"

    scraped = scrape_product_playwright(url, session_state)
    if not scraped:
        return None, "scrape_failed"

    if scraped.get("_inactive"):
        return None, "inactive_product"

    for key in ["price", "mrp_price", "category", "brand"]:
        if key in scraped and not product.get(key):
            updates[key] = scraped[key]

    return updates if updates else None, "no_new_data"


def main():
    print("Fetching products missing data...")
    products = fetch_products_missing()
    total = len(products)
    missing_price = [p for p in products if not p.get("price")]
    missing_cat = [p for p in products if not p.get("category")]
    missing_brand = [p for p in products if not p.get("brand")]
    print(f"Total: {total} | missing price: {len(missing_price)} | missing category: {len(missing_cat)} | missing brand: {len(missing_brand)}")

    to_enrich = [p for p in products if not p.get("price") or not p.get("category") or not p.get("brand")]
    print(f"To enrich: {len(to_enrich)}")

    # Load session
    if not os.path.exists(SESSION_FILE):
        print("ERROR: No session file found. Run setup_login.py first.")
        return
    with open(SESSION_FILE) as f:
        session_state = json.load(f)
    print("Session loaded")

    # Limit for testing — set to len(to_enrich) for full run
    LIMIT = int(os.getenv("ENRICH_LIMIT", "50"))
    to_enrich = to_enrich[:LIMIT]
    print(f"Processing {len(to_enrich)} products (ENRICH_LIMIT={LIMIT})")

    enriched_count = 0
    inactive_count = 0
    failed_count = 0
    updated_count = 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(enrich_product, p, session_state): p for p in to_enrich}
        for i, future in enumerate(as_completed(futures)):
            p = futures[future]
            try:
                updates, reason = future.result()
                if updates:
                    api_request(f"{API}?id=eq.{p['id']}", method="PATCH", data=updates)
                    updated_count += 1
                    print(f"[{i+1}/{len(to_enrich)}] ✅ {p['id']}: +{list(updates.keys())}")
                elif reason == "inactive_product":
                    inactive_count += 1
                    print(f"[{i+1}/{len(to_enrich)}] ⛔ {p['id']}: INACTIVE (delisted)")
                elif reason == "no_new_data":
                    pass
                else:
                    failed_count += 1
                    print(f"[{i+1}/{len(to_enrich)}] ⚠️ {p['id']}: {reason}")
            except Exception as e:
                failed_count += 1
                print(f"[{i+1}/{len(to_enrich)}] ❌ {p['id']}: {str(e)[:80]}")

    print(f"\nDone. Updated: {updated_count} | Inactive: {inactive_count} | Failed: {failed_count}")


if __name__ == "__main__":
    main()
