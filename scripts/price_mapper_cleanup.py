#!/usr/bin/env python3
"""Archive Price Mapper products that no longer exist, plus stale mappings.

Classification (the ProductListing export is the source of truth):

  * KEEP            - the product URL tail is in the export (it is a live
                      listing), OR its exact title matches an ACTIVE row
  * ARCHIVE delisted - no URL tail and no title match anywhere in the export
                      (verified against the live storefront: the PDP serves no
                      product data for these)
  * ARCHIVE non-active - exact title match, but every matching row is Blocked /
                      Rejected / Pending

Also archives "orphan" price_mappings: rows whose gajab_product_id has no
products row (they inflate the mapped count and can never be displayed).

Everything is copied to products_archive / price_mappings_archive first, so a
mistake is recoverable with a single INSERT ... SELECT back.

Usage:
    python3 scripts/price_mapper_cleanup.py --xlsx C:\\gajab\\ProductListing.xlsx           # dry run
    python3 scripts/price_mapper_cleanup.py --xlsx C:\\gajab\\ProductListing.xlsx --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from openpyxl import Workbook, load_workbook

SECRETS = Path(os.environ.get("PM_SYNC_SECRETS", r"C:\gajab\.db-secrets.json"))
DEFAULT_PG_BIN = r"C:\gajab\pg\pgsql\bin"
DEFAULT_BACKUP_DIR = r"C:\gajab\backups"


def default_dsn() -> str:
    if os.environ.get("PM_SYNC_DSN"):
        return os.environ["PM_SYNC_DSN"]
    pw = json.loads(SECRETS.read_text(encoding="utf-8"))["superuser"]
    return f"postgresql://postgres:{pw}@127.0.0.1:5432/gajab"


def url_tail(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if "/product-detail/" in u:
        u = u.split("/product-detail/", 1)[1]
    return re.sub(r"^[_\-\s]+", "", u.rsplit("/", 1)[-1]).strip().upper()


def title_sig(t: str) -> frozenset:
    t = html.unescape(t or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    return frozenset(w for w in re.sub(r"[^a-z0-9]+", " ", t).split() if len(w) > 1)


def load_sheet(path: Path):
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Product Detail Sheet"] if "Product Detail Sheet" in wb.sheetnames else wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [" ".join(str(h).split()) for h in next(it)]
    ix = {h: i for i, h in enumerate(hdr)}
    tails: set[str] = set()
    by_sig: dict[frozenset, list[tuple[str, str, str]]] = defaultdict(list)
    for r in it:
        tails.add(url_tail(str(r[ix["Product URL"]] or "")))
        sig = title_sig(str(r[ix["Product Name"]] or ""))
        if sig:
            by_sig[sig].append((
                str(r[ix["Sku Number"]] or "").strip(),
                str(r[ix["Product Status"]] or "").strip(),
                str(r[ix["Product URL"]] or ""),
            ))
    return tails, by_sig


def backup(pg_bin: str, backup_dir: str, password: str) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(backup_dir) / f"price_mapper_cleanup_{ts}.dump"
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PGPASSWORD=password)
    proc = subprocess.run(
        [str(Path(pg_bin) / "pg_dump.exe"), "-h", "127.0.0.1", "-U", "postgres",
         "-d", "gajab", "-Fc", "--data-only", "-w", "-t", "public.products",
         "-t", "public.price_mappings", "-f", str(out)],
        env=env, capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise SystemExit(f"backup FAILED ({proc.returncode}): {proc.stderr.strip()[:300]}")
    print(f"  backup -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--dsn", default="")
    ap.add_argument("--pg-bin", default=DEFAULT_PG_BIN)
    ap.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    ap.add_argument("--review", default=r"C:\gajab\price_mapper_ambiguous_sku.xlsx")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tails, by_sig = load_sheet(Path(args.xlsx).expanduser())
    conn = psycopg2.connect(args.dsn or default_dsn())
    cur = conn.cursor()
    cur.execute("select id, name, url, price from products")
    products = cur.fetchall()
    print(f"products: {len(products)}  sheet tails: {len(tails)}")

    keep = archive_delisted = archive_nonactive = 0
    delisted, nonactive, ambiguous = [], [], []
    for pid, name, url, price in products:
        if (url_tail(url) or re.sub(r"^gajab-", "", pid).lstrip("_").upper()) in tails:
            keep += 1
            continue
        recs = by_sig.get(title_sig(name))
        if not recs:
            archive_delisted += 1
            delisted.append((pid, name, url))
            continue
        if all(s.lower() != "active" for _, s, _ in recs):
            archive_nonactive += 1
            nonactive.append((pid, name, url, ", ".join(sorted({s for _, s, _ in recs}))))
            continue
        keep += 1
        skus = sorted({s for s, _, _ in recs if s})
        if len(skus) > 1:
            ambiguous.append((pid, name, price, url, skus, [u for _, _, u in recs][:5]))

    cur.execute("""select count(*) from price_mappings m
                    left join products p on p.id = m.gajab_product_id
                   where p.id is null""")
    orphans = cur.fetchone()[0]

    print("\nCLASSIFICATION")
    print(f"  keep                    {keep}")
    print(f"  archive - delisted      {archive_delisted}")
    print(f"  archive - non-active    {archive_nonactive}")
    print(f"  orphan mappings         {orphans}")
    print(f"  ambiguous (kept, no SKU){len(ambiguous)}")

    wb = Workbook()
    ws = wb.active
    ws.title = "Ambiguous SKU"
    ws.append(["Gajab Product ID", "Product Title", "Gajab Price", "Gajab URL",
               "Candidate SKUs", "Sheet Product URLs"])
    for pid, name, price, url, skus, urls in ambiguous:
        ws.append([pid, name, price, url, " | ".join(skus), "\n".join(urls)])
    for col, width in (("A", 24), ("B", 70), ("C", 14), ("D", 70), ("E", 44), ("F", 70)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(args.review)
    print(f"  review sheet -> {args.review}")

    if not args.apply:
        print("\nDRY RUN - nothing changed. re-run with --apply to commit.")
        return 0

    pw = json.loads(SECRETS.read_text(encoding="utf-8"))["superuser"] if SECRETS.exists() else ""
    backup(args.pg_bin, args.backup_dir, pw or os.environ.get("PGPASSWORD", ""))

    ids = [(pid, "delisted", "") for pid, _, _ in delisted]
    ids += [(pid, "non-active", status) for pid, _, _, status in nonactive]
    try:
        for live, arch in (("products", "products_archive"),
                           ("price_mappings", "price_mappings_archive")):
            cur.execute(f"create table if not exists {arch} (like {live} including defaults)")
            cur.execute(f"alter table {arch} add column if not exists archived_at timestamptz default now()")
            cur.execute(f"alter table {arch} add column if not exists archive_reason text")
            cur.execute(f"alter table {arch} add column if not exists source_status text")

        cur.execute("create temp table _clean (id text primary key, reason text, status text) on commit drop")
        if ids:
            execute_values(cur, "insert into _clean (id, reason, status) values %s", ids, page_size=2000)
            cur.execute("""insert into price_mappings_archive
                           select m.*, now(), c.reason, c.status
                             from price_mappings m join _clean c on c.id = m.gajab_product_id""")
            cur.execute("""delete from price_mappings m using _clean c where c.id = m.gajab_product_id""")
            cur.execute("""insert into products_archive
                           select p.*, now(), c.reason, c.status
                             from products p join _clean c on c.id = p.id""")
            cur.execute("""delete from products p using _clean c where c.id = p.id""")

        cur.execute("""insert into price_mappings_archive
                       select m.*, now(), 'orphan', null
                         from price_mappings m
                         left join products p on p.id = m.gajab_product_id
                        where p.id is null""")
        cur.execute("""delete from price_mappings m
                        where not exists (select 1 from products p where p.id = m.gajab_product_id)""")
        conn.commit()
    except Exception:
        conn.rollback()
        print("ROLLED BACK - nothing changed", file=sys.stderr)
        raise

    cur.execute("select count(*) from products")
    print(f"\ncommitted. products now: {cur.fetchone()[0]}")
    cur.execute("select count(*) from price_mappings")
    print(f"price_mappings now: {cur.fetchone()[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
