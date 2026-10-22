#!/usr/bin/env python3
"""Backfill products.sku from a Gajab ProductListing export.

The Price Mapper's product rows never carried the seller's SKU: the only code
they had was the last segment of the product URL (e.g. `AV-C697185030`), which is
a LINK code - not the `Sku Number` in the marketplace sheet
(`AV-C3-AP1-BN1-YK1-100G`). This script adds the column and fills it by joining
on the URL tail, which is what `products.id` is built from.

Usage:
    python3 scripts/backfill_product_sku.py --xlsx C:\\gajab\\ProductListing.xlsx
    python3 scripts/backfill_product_sku.py --xlsx sheet.xlsx --dry-run

Idempotent: re-running with a newer export updates changed SKUs and fills new
products. Rows whose URL is not in the sheet keep whatever they had.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from openpyxl import load_workbook

SECRETS = Path(os.environ.get("PM_SYNC_SECRETS", r"C:\gajab\.db-secrets.json"))


def default_dsn() -> str:
    if os.environ.get("PM_SYNC_DSN"):
        return os.environ["PM_SYNC_DSN"]
    pw = __import__("json").loads(SECRETS.read_text(encoding="utf-8"))["superuser"]
    return f"postgresql://postgres:{pw}@127.0.0.1:5432/gajab"


def url_tail(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if "/product-detail/" in u:
        u = u.split("/product-detail/", 1)[1]
    return re.sub(r"^[_\-\s]+", "", u.rsplit("/", 1)[-1]).strip().upper()


def norm_title(t: str) -> str:
    t = html.unescape(t or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def norm_title_strict(t: str) -> str:
    """Like norm_title but KEEPS bracketed variant text ('(Black)' vs '(Red)')."""
    t = html.unescape(t or "").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def load_sheet(path: Path) -> dict[str, str]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Product Detail Sheet"] if "Product Detail Sheet" in wb.sheetnames else wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [" ".join(str(h).split()) for h in next(it)]
    ix = {h: i for i, h in enumerate(hdr)}
    for col in ("Sku Number", "Product URL"):
        if col not in ix:
            raise SystemExit(f"sheet has no '{col}' column")
    out: dict[str, str] = {}
    for r in it:
        sku = str(r[ix["Sku Number"]] or "").strip()
        if not sku:
            continue
        t = url_tail(r[ix["Product URL"]])
        if t:
            out[t] = sku
    return out


def load_sheet_titles(path: Path) -> dict[str, str]:
    """normalised title -> SKU, only where that title has exactly ONE SKU.

    Some products are in the DB but their URL is not in the sheet (renamed
    listings). Their title still matches exactly, which recovers ~800 rows;
    ambiguous titles are skipped so two different products cannot be given the
    same SKU on a hunch.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Product Detail Sheet"] if "Product Detail Sheet" in wb.sheetnames else wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [" ".join(str(h).split()) for h in next(it)]
    ix = {h: i for i, h in enumerate(hdr)}
    seen: dict[str, set] = defaultdict(set)
    for r in it:
        sku = str(r[ix["Sku Number"]] or "").strip()
        k = norm_title(str(r[ix["Product Name"]] or ""))
        if sku and k:
            seen[k].add(sku)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def load_sheet_titles_strict(path: Path) -> dict[str, str]:
    """Same, but bracketed variant text is kept, which separates products that
    only differ by '(Pack of 2, Black)' / '(Pack of 2, Red)'."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Product Detail Sheet"] if "Product Detail Sheet" in wb.sheetnames else wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [" ".join(str(h).split()) for h in next(it)]
    ix = {h: i for i, h in enumerate(hdr)}
    seen: dict[str, set] = defaultdict(set)
    for r in it:
        sku = str(r[ix["Sku Number"]] or "").strip()
        k = norm_title_strict(str(r[ix["Product Name"]] or ""))
        if sku and k:
            seen[k].add(sku)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--dsn", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sheet = load_sheet(Path(args.xlsx).expanduser())
    print(f"sheet SKUs: {len(sheet)}")

    conn = psycopg2.connect(args.dsn or default_dsn())
    try:
        cur = conn.cursor()
        cur.execute("select id, url from products")
        pairs = [(pid, sheet.get(url_tail(url) or re.sub(r"^gajab-", "", pid).lstrip("_").upper()))
                 for pid, url in cur.fetchall()]
        matched = [(pid, sku) for pid, sku in pairs if sku]
        # pass 2: exact title, only when that title maps to a single SKU
        titles = load_sheet_titles(Path(args.xlsx).expanduser())
        cur.execute("select id, name from products where sku is null")
        by_title = [(pid, titles[norm_title(name)]) for pid, name in cur.fetchall()
                    if norm_title(name) in titles]
        matched += by_title
        print(f"  recovered by exact title: {len(by_title)}")

        # pass 3: strict title (keeps the bracketed variant), still single-SKU
        strict = load_sheet_titles_strict(Path(args.xlsx).expanduser())
        cur.execute("select id, name from products where sku is null")
        by_strict = [(pid, strict[norm_title_strict(name)]) for pid, name in cur.fetchall()
                     if norm_title_strict(name) in strict]
        matched += by_strict
        print(f"  recovered by strict (variant-aware) title: {len(by_strict)}")
        print(f"products: {len(pairs)}  matched to a sheet SKU: {len(matched)}"
              f"  unmatched: {len(pairs) - len(matched)}")

        if args.dry_run:
            for pid, sku in matched[:5]:
                print("  ", pid, "->", sku)
            print("DRY RUN - nothing written")
            return 0

        cur.execute("alter table products add column if not exists sku text")
        cur.execute("create temp table _sku (id text primary key, sku text) on commit drop")
        execute_values(cur, "insert into _sku (id, sku) values %s", matched, page_size=2000)
        cur.execute("""
            update products p set sku = s.sku
              from _sku s
             where p.id = s.id
               and (p.sku is null or p.sku <> s.sku)""")
        changed = cur.rowcount
        cur.execute("create index if not exists products_sku_idx on products (sku)")
        conn.commit()
        print(f"updated {changed} rows")
        cur.execute("select count(*) from products where sku is not null")
        print(f"products with a SKU: {cur.fetchone()[0]}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
