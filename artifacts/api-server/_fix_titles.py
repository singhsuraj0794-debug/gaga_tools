#!/usr/bin/env python3
"""
Scrape Gajab product pages to fix truncated/wrong product names in Supabase.
Resumable — saves progress to fix_titles_progress.json.

Usage: python3 _fix_titles.py [--limit N] [--force] [--resume]
"""
import argparse
import json
import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BATCH_SIZE = 5
REQUEST_TIMEOUT = 30000
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fix_titles_progress.json")


def get_all_products(supabase_url, supabase_key):
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    all_products = []
    offset = 0
    while True:
        try:
            resp = requests.get(
                f"{supabase_url}/rest/v1/products?select=id,name,url&order=id.asc&limit=1000&offset={offset}",
                headers=headers, timeout=30
            )
            rows = resp.json()
            if not rows:
                break
            all_products.extend(rows)
            offset += 1000
            if len(rows) < 1000:
                break
        except Exception as e:
            print(f"Error fetching at offset {offset}: {e}", flush=True)
            break
    return all_products


def extract_title(page, slug, item_id):
    url = f"https://gajab.com/product-detail/{slug}/{item_id}"
    try:
        page.goto(url, timeout=REQUEST_TIMEOUT)
        page.wait_for_timeout(2000)
    except Exception:
        return None
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
                            name = item.get("name")
                            if name:
                                return name.strip()
                idx = end + 1
                while idx < len(raw) and raw[idx] in " \n\r\t":
                    idx += 1
            except json.JSONDecodeError:
                idx += 1
    html_title = page.title()
    if html_title and html_title != "Gajab":
        return html_title.strip()
    return None


def update_name(supabase_url, supabase_key, product_id, name):
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        resp = requests.patch(
            f"{supabase_url}/rest/v1/products?id=eq.{product_id}",
            json={"name": name}, headers=headers, timeout=10
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed": set(), "updated": 0, "same": 0, "errors": 0, "no_page": 0}


def save_progress(progress):
    progress["processed"] = list(progress["processed"])
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)
    progress["processed"] = set(progress["processed"])


def main():
    parser = argparse.ArgumentParser(description="Fix Gajab product titles")
    parser.add_argument("--limit", type=int, default=0, help="Max products to process (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if name matches")
    parser.add_argument("--resume", action="store_true", help="Resume from saved progress")
    args = parser.parse_args()

    supabase_url = os.environ.get("SUPABASE_URL", "https://okxyskmjsmtykblrtmyi.supabase.co")
    supabase_key = os.environ.get("SUPABASE_KEY",
                                   "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp")

    print("Fetching products from Supabase...", flush=True)
    products = get_all_products(supabase_url, supabase_key)
    total = len(products)
    print(f"Total products: {total}", flush=True)

    progress = load_progress()
    if not args.resume:
        progress = {"processed": set(), "updated": 0, "same": 0, "errors": 0, "no_page": 0}

    # Filter already processed
    todo = [p for p in products if p["id"] not in progress["processed"]]
    skipped_progress = total - len(todo)
    print(f"Already processed: {skipped_progress}, remaining: {len(todo)}", flush=True)

    if args.limit and args.limit < len(todo):
        todo = todo[:args.limit]

    if not todo:
        print("Nothing to do.", flush=True)
        return

    updated = progress["updated"]
    skipped_same = progress["same"]
    errors = progress["errors"]
    no_page = progress["no_page"]
    processed_in_run = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1440, "height": 900},
        )

        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            pages = []
            for p in batch:
                try:
                    pg = ctx.new_page()
                    pages.append((p, pg))
                except Exception:
                    errors += 1
                    progress["processed"].add(p["id"])

            for p, pg in pages:
                try:
                    pid = p["id"]
                    old_name = p.get("name", "") or ""
                    url = p.get("url", "")
                    if not url:
                        no_page += 1
                        progress["processed"].add(pid)
                        pg.close()
                        continue
                    parts = url.replace("https://gajab.com/product-detail/", "").split("/")
                    if len(parts) < 2:
                        no_page += 1
                        progress["processed"].add(pid)
                        pg.close()
                        continue
                    slug, item_id = parts[0], parts[1]

                    actual_title = extract_title(pg, slug, item_id)
                    if not actual_title:
                        no_page += 1
                        progress["processed"].add(pid)
                        pg.close()
                        continue

                    if actual_title == old_name and not args.force:
                        skipped_same += 1
                    else:
                        if actual_title != old_name:
                            print(f"  UPDATED {pid}: '{old_name[:60]}...' -> '{actual_title[:60]}...'", flush=True)
                        ok = update_name(supabase_url, supabase_key, pid, actual_title)
                        if ok:
                            updated += 1
                        else:
                            errors += 1
                except Exception:
                    errors += 1
                finally:
                    progress["processed"].add(p["id"])
                    processed_in_run += 1
                    try:
                        pg.close()
                    except Exception:
                        pass

            # Save progress every batch
            progress["updated"] = updated
            progress["same"] = skipped_same
            progress["errors"] = errors
            progress["no_page"] = no_page
            save_progress(progress)

            progress_pct = i + len(batch)
            print(f"Progress: {progress_pct}/{len(todo)} ({skipped_progress + progress_pct}/{total})  "
                  f"updated={updated} same={skipped_same} errors={errors} no_page={no_page}",
                  flush=True)

        browser.close()

    print(f"\nDone. Updated: {updated}, already correct: {skipped_same}, "
          f"errors: {errors}, no page: {no_page}", flush=True)


if __name__ == "__main__":
    main()
