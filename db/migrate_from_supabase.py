#!/usr/bin/env python3
"""
migrate_from_supabase.py - copy the four Supabase tables into the local Postgres.

Reads through the Supabase REST API (we have the anon/publishable key, not the
database password), pages in 1000-row chunks with a stable ORDER BY, and writes
one CSV per table. Load them with db/load.sh on the target host:

    python3 db/migrate_from_supabase.py --out /tmp/pgexport
    # then on the box:
    psql -U postgres -h 127.0.0.1 -d gajab -f db/schema.sql
    psql -U postgres -h 127.0.0.1 -d gajab -c "\\copy ..."

Column order is pinned per table so the CSV matches schema.sql exactly.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PAGE = 1000

# table -> (order-by for stable paging, column order for the CSV)
TABLES: dict[str, tuple[str, list[str]]] = {
    "products": ("id", [
        "id", "name", "price", "mrp_price", "image_url", "url",
        "category", "brand", "seller", "created_at",
    ]),
    "price_mappings": ("gajab_product_id", [
        "id", "gajab_product_id", "gajab_title", "gajab_image_url", "gajab_price", "gajab_url",
        "amazon_url", "amazon_price", "amazon_match_score", "amazon_dinov2", "amazon_clip",
        "amazon_match_tag", "amazon_unavailable", "amazon_reliable",
        "flipkart_url", "flipkart_price", "flipkart_match_score", "flipkart_dinov2", "flipkart_clip",
        "flipkart_match_tag", "flipkart_unavailable", "flipkart_reliable",
        "meesho_url", "meesho_price", "meesho_match_score", "meesho_dinov2", "meesho_clip",
        "meesho_match_tag", "meesho_unavailable", "meesho_reliable",
        "search_error", "last_checked", "created_at",
    ]),
    "product_duplicates": ("id", [
        "id", "product_id", "duplicate_of", "dinov2_score", "created_at",
    ]),
    "monitoring_runs": ("run_at,id", [
        "id", "run_at", "run_id", "page", "metric", "value", "status",
        "step_failed", "duration_ms", "details", "issue_type", "created_at",
    ]),
}


def load_env(path: str) -> dict:
    d: dict[str, str] = {}
    if not os.path.exists(path):
        return d
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def cell(v):
    """Render a JSON value for a Postgres CSV load."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float):
        return repr(v)
    return v


def fetch_page(url: str, key: str, table: str, order: str, offset: int) -> list[dict]:
    path = (f"/rest/v1/{table}?select=*&order={order}&limit={PAGE}&offset={offset}")
    req = urllib.request.Request(
        f"{url}{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "db", "export"),
                    help="directory to write the CSVs into")
    ap.add_argument("--env", default=os.path.join(REPO, "monitoring", ".env"))
    ap.add_argument("--table", action="append", default=None)
    args = ap.parse_args()

    env = {**load_env(args.env), **os.environ}
    url = env.get("SUPABASE_URL", "")
    key = env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_KEY") or ""
    if not url or not key:
        print("ERROR: SUPABASE_URL and a key are required (monitoring/.env).")
        return 1

    os.makedirs(args.out, exist_ok=True)
    tables = args.table or list(TABLES)
    grand = 0
    for table in tables:
        order, cols = TABLES[table]
        path = os.path.join(args.out, f"{table}.csv")
        n = 0
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            offset = 0
            while True:
                try:
                    rows = fetch_page(url, key, table, order, offset)
                except urllib.error.HTTPError as e:
                    print(f"  {table}: HTTP {e.code} at offset {offset}: {e.read()[:160]}")
                    return 2
                if not rows:
                    break
                for r in rows:
                    w.writerow([cell(r.get(c)) for c in cols])
                n += len(rows)
                offset += len(rows)
                if len(rows) < PAGE:
                    break
        size = os.path.getsize(path) / 1024 / 1024
        grand += n
        print(f"  {table}: {n} rows -> {path} ({size:.1f} MB)")

    print(f"\ntotal rows exported: {grand}")
    print("\nLoad on the target host:")
    for table in tables:
        order, cols = TABLES[table]
        print(f"  psql -U postgres -h 127.0.0.1 -d gajab -c \"\\copy {table}({','.join(cols)}) "
              f"from '{args.out}/{table}.csv' with (format csv, null '')\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
