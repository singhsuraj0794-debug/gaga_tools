"""
Import products from gajab.com sitemaps that are not yet in Supabase.
"""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from config import SUPABASE_URL, SUPABASE_KEY

API = f"{SUPABASE_URL}/rest/v1/products"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def get_supabase_ids():
    ids = set()
    offset = 0
    while True:
        url = f"{API}?select=id&limit=1000&offset={offset}"
        req = urllib.request.Request(url, headers=HEADERS)
        page = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not page:
            break
        ids.update(p["id"] for p in page)
        offset += 1000
        if len(page) < 1000:
            break
    return ids


def get_gajab_products():
    products = {}
    resp = urllib.request.urlopen(urllib.request.Request("https://gajab.com/sitemap.xml", headers={"User-Agent": "Mozilla/5.0"}), timeout=30)
    index = resp.read().decode()
    sitemap_urls = [u.replace("&amp;", "&") for u in re.findall(r"<loc>(.*?)</loc>", index) if "products" in u]

    for su in sitemap_urls:
        try:
            r = urllib.request.urlopen(urllib.request.Request(su, headers={"User-Agent": "Mozilla/5.0"}), timeout=30)
            x = r.read().decode()
            urls = re.findall(r"<loc>(.*?)</loc>", x)
            for u in urls:
                u = u.strip()
                if "product-detail" in u:
                    parts = u.rstrip("/").split("/")
                    pid = parts[-1]
                    slug = parts[-2]
                    products[pid] = {"id": pid, "slug": slug, "url": u}
        except Exception as e:
            print(f"Sitemap error: {e}")
    return products


def import_products(missing_products):
    records = []
    for pid, data in missing_products.items():
        # Generate a readable name from slug
        slug = data["slug"]
        name = slug.replace("-", " ").replace("_", " ").strip().title()[:200]
        records.append({
            "id": f"gajab-{pid}",
            "name": name,
            "url": data["url"],
            "image_url": None,
            "price": None,
            "category": None,
        })

    # Upsert in batches of 100
    imported = 0
    for i in range(0, len(records), 100):
        batch = records[i:i + 100]
        req = urllib.request.Request(
            f"{API}?on_conflict=id",
            data=json.dumps(batch).encode(),
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        resp.read()
        imported += len(batch)
        print(f"  Imported {imported}/{len(records)}")
    return imported


def main():
    print("Fetching Supabase product IDs...")
    supabase_ids = get_supabase_ids()
    print(f"  Supabase: {len(supabase_ids)} products")

    print("Fetching gajab.com sitemap products...")
    gajab_products = get_gajab_products()
    print(f"  Gajab: {len(gajab_products)} products")

    missing = {pid: data for pid, data in gajab_products.items() if f"gajab-{pid}" not in supabase_ids}
    print(f"\nMissing from Supabase: {len(missing)}")

    if missing:
        imported = import_products(missing)
        print(f"\nImported {imported} new products")
    else:
        print("Nothing to import")


if __name__ == "__main__":
    main()
