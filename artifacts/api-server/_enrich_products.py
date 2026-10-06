#!/usr/bin/env python3
"""Enrich Gajab.com products in Supabase with precise data.

Fetches products missing price/category/brand/mrp_price from the Gajab
gateway product-detail endpoint (fast, ~0.25s) which returns:
  - price, mrpPrice
  - Category[0].categoryName
  - brandName
  - companyDetail.companyName (seller)

Usage: python3 _enrich_products.py [--limit N] [--only-null COLUMN]
"""
import json
import os
import sys
import time
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WORKERS = 12
TIMEOUT = 20

HEADERS = {
    "Content-type": "application/json",
    "Origin": "https://gajab.com",
    "Referer": "https://gajab.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def get_supabase():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_missing_products(sb, limit=None):
    """Fetch products missing any of price/category/brand/mrp_price."""
    all_products = []
    offset = 0
    PAGE = 1000
    while True:
        if limit and len(all_products) >= limit:
            break
        page_size = PAGE
        if limit:
            page_size = min(PAGE, limit - len(all_products))
        q = (
            sb.table("products")
            .select("id, url, price, category, brand, mrp_price")
            .or_("price.is.null,category.is.null,brand.is.null,mrp_price.is.null")
            .range(offset, offset + page_size - 1)
        )
        r = q.execute()
        data = r.data or []
        if not data:
            break
        all_products.extend(data)
        offset += PAGE
        if limit and len(all_products) >= limit:
            break
        if len(data) < page_size:
            break
    return all_products


def fetch_product_data(slug: str, item_id: str) -> dict:
    url = (
        f"https://gatewayservice.gajab.com/product/api/product-store/"
        f"product-detail/{slug}/{item_id}?pincode="
    )
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 400:
                return {}  # invalid product, skip
            resp.raise_for_status()
            d = resp.json()
            data = d.get("data") or {}
            cat = data.get("Category") or []
            category = cat[0].get("categoryName") if cat else None
            seller = (data.get("companyDetail") or {}).get("companyName")
            return {
                "price": data.get("price"),
                "mrpPrice": data.get("mrpPrice"),
                "category": category,
                "brandName": data.get("brandName"),
                "seller": seller,
            }
        except requests.exceptions.Timeout:
            if attempt == 2:
                raise
            time.sleep(1)
        except requests.exceptions.ConnectionError:
            if attempt == 2:
                raise
            time.sleep(1)
    return {}


def enrich_one(p: dict) -> dict:
    """Enrich a single product; returns update dict or None."""
    url = p.get("url")
    if not url:
        return None
    prefix = "https://gajab.com/product-detail/"
    if not url.startswith(prefix):
        return None
    parts = url[len(prefix):].split("/")
    if len(parts) < 2:
        return None
    slug, item_id = parts[0], parts[1]
    try:
        data = fetch_product_data(slug, item_id)
    except Exception as e:
        return {"id": p["id"], "error": str(e)}

    updates = {}
    if data.get("price"):
        try:
            price_num = float(data["price"])
            if price_num > 0:
                updates["price"] = f"₹{int(round(price_num)):,}"
        except (ValueError, TypeError):
            pass
    if data.get("mrpPrice"):
        try:
            mrp_num = float(data["mrpPrice"])
            if mrp_num > 0:
                updates["mrp_price"] = f"₹{int(round(mrp_num)):,}"
        except (ValueError, TypeError):
            pass
    if data.get("category"):
        updates["category"] = data["category"]
    if data.get("brandName"):
        updates["brand"] = data["brandName"]

    return {"id": p["id"], "updates": updates} if updates else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sb = get_supabase()
    products = fetch_missing_products(sb, limit=args.limit)
    total = len(products)
    print(f"Products to enrich: {total}", file=sys.stderr)

    if total == 0:
        print("Nothing to do.", file=sys.stderr)
        return

    updated = 0
    errors = 0

    def process(p):
        return enrich_one(p)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, p): p for p in products}
        done = 0
        for fut in as_completed(futures):
            done += 1
            p = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                errors += 1
                result = None
            if result:
                if "error" in result:
                    errors += 1
                else:
                    updates = result["updates"]
                    if args.dry_run:
                        print(f"  [dry-run] {result['id']}: {updates}", file=sys.stderr)
                        updated += 1
                    else:
                        try:
                            sb.table("products").update(updates).eq("id", result["id"]).execute()
                            updated += 1
                        except Exception as e:
                            errors += 1
                            print(f"  [err] {result['id']}: {e}", file=sys.stderr)
            if done % 100 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  Progress {done}/{total} ({rate:.1f}/s), updated={updated}, errors={errors}", file=sys.stderr)

    print(f"\nDone: {updated} updated, {errors} errors, {time.time()-t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()