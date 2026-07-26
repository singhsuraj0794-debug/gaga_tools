#!/usr/bin/env python3
"""
Batch scrapes Gajab product pages via Playwright to extract brand & category
from JSON-LD structured data. Updates Supabase in-place.
Usage: python3 _scrape_brand_category.py <supabase_url> <supabase_key>
"""
import json
import os
import sys
import time
import traceback
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright

BATCH_SIZE = 10
REQUEST_TIMEOUT = 25000


def get_products_needing_enrichment(supabase_url: str, supabase_key: str,
                                    only_missing: str = "brand"):
    """Fetch products missing the specified field."""
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    all_products = []
    offset = 0
    field = "brand" if only_missing == "brand" else "category"
    while True:
        resp = requests.get(
            f"{supabase_url}/rest/v1/products?select=id,url,{field}&{field}=is.null&limit=1000&offset={offset}",
            headers=headers, timeout=15
        )
        rows = resp.json()
        if not rows:
            break
        all_products.extend(rows)
        offset += 1000
        if len(rows) < 1000:
            break
    return all_products


def extract_brand_category(page, product_id: str, slug: str, item_id: str) -> dict:
    """Visit product page and extract brand/category from JSON-LD."""
    url = f"https://gajab.com/product-detail/{slug}/{item_id}"
    page.goto(url, timeout=REQUEST_TIMEOUT)
    page.wait_for_timeout(1500)

    raw_scripts = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
            .map(s => s.textContent);
    }""")

    for raw in raw_scripts:
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(raw):
            try:
                obj, end = decoder.raw_decode(raw, idx)
                if isinstance(obj, dict):
                    graph = obj.get("@graph", [obj])
                    for item in graph:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            brand = item.get("brand", {})
                            bn = brand.get("name") if isinstance(brand, dict) else None
                            cat = item.get("category")
                            if bn or cat:
                                return {"brand": bn, "category": cat}
                idx = end + 1
                while idx < len(raw) and raw[idx] in " \n\r\t":
                    idx += 1
            except json.JSONDecodeError:
                idx += 1
    return {}


def update_product(supabase_url: str, supabase_key: str, product_id: str,
                   brand: str = None, category: str = None):
    """Update brand/category in Supabase."""
    updates = {}
    if brand:
        updates["brand"] = brand
    if category:
        updates["category"] = category
    if not updates:
        return False

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    resp = requests.patch(
        f"{supabase_url}/rest/v1/products?id=eq.{product_id}",
        json=updates, headers=headers, timeout=10
    )
    return resp.status_code in (200, 204)


def main():
    if len(sys.argv) < 3:
        supabase_url = os.environ.get("SUPABASE_URL", "https://okxyskmjsmtykblrtmyi.supabase.co")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or \
                       os.environ.get("VITE_SUPABASE_ANON_KEY", "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp")
    else:
        supabase_url = sys.argv[1]
        supabase_key = sys.argv[2]

    mode = sys.argv[3] if len(sys.argv) > 3 else "brand"
    # mode: "brand" = only missing brand; "both" = missing brand OR category

    if mode == "both":
        brand_products = get_products_needing_enrichment(supabase_url, supabase_key, "brand")
        cat_products = get_products_needing_enrichment(supabase_url, supabase_key, "category")
        seen_ids = set()
        products = []
        for p in brand_products + cat_products:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                products.append(p)
    else:
        products = get_products_needing_enrichment(supabase_url, supabase_key, mode)

    total = len(products)
    print(f"Products to process: {total}")

    if total == 0:
        print("Nothing to do.")
        return

    updated_brand = 0
    updated_category = 0
    errors = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1440, "height": 900},
        )

        for i in range(0, total, BATCH_SIZE):
            batch = products[i:i + BATCH_SIZE]
            pages = []
            for p in batch:
                pg = ctx.new_page()
                pages.append((p, pg))

            for p, pg in pages:
                try:
                    url = p.get("url", "")
                    parts = url.replace("https://gajab.com/product-detail/", "").split("/")
                    if len(parts) < 2:
                        errors += 1
                        continue
                    slug = parts[0]
                    item_id = parts[1]
                    result = extract_brand_category(pg, p["id"], slug, item_id)
                    if result.get("brand") or result.get("category"):
                        ok = update_product(
                            supabase_url, supabase_key, p["id"],
                            result.get("brand"), result.get("category")
                        )
                        if ok:
                            if result.get("brand"):
                                updated_brand += 1
                            if result.get("category"):
                                updated_category += 1
                except Exception:
                    errors += 1
                finally:
                    pg.close()

            progress = i + len(batch)
            print(f"Progress: {progress}/{total}  "
                  f"brand={updated_brand} category={updated_category} errors={errors}")

        browser.close()

    print(f"\nDone. Total: {total}, brand updated: {updated_brand}, "
          f"category updated: {updated_category}, errors: {errors}")


if __name__ == "__main__":
    main()
