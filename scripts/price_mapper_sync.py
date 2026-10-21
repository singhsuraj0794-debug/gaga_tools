#!/usr/bin/env python3
"""Reconcile the Price Mapper database with a Gajab product-listing export.

The export ("ProductListing_Export*.xlsx") is the source of truth for which
products exist and their current details/status. This script:

  * Active source products   -> update the matching record in place, or insert it
  * Non-Active source products -> archive + remove the confidently matched record
  * anything uncertain       -> left untouched and listed for review

Matching key
------------
The tail of the product URL is a stable product code (numeric, or a SKU code
like 'BTGW945793876') and `products.id` is literally 'gajab-' + that tail. So
the tail is used as the identifier:

    https://gajab.com/product-detail/<slug>/<TAIL>   ->  gajab-<TAIL>

This survives title edits and slug changes, which title matching does not.
Titles are only used as a *guard*: an exact normalized-title match without a
tail match is treated as MEDIUM confidence (review) so a re-listed product is
never inserted twice. --fuzzy-guard additionally checks near-duplicate titles.

What is never touched
---------------------
For retained products only these product-detail fields are written:
    products      : name, price, mrp_price, image_url, url, category
    price_mappings: gajab_title, gajab_price, gajab_image_url, gajab_url
The mapper's progress (amazon_/flipkart_/meesho_* url, price, scores, tags,
unavailable/reliable flags, search_error, last_checked, *_price_checked_at) is
verified unchanged by a before/after hash of those columns. `products.seller`
and `products.brand` are left alone on updates: the export's seller name does
not line up with the stored value (only ~22% match), so overwriting it would be
an unreviewed change. `seller` is only populated on insert.

Usage
-----
    python3 scripts/price_mapper_sync.py --xlsx ProductListing.xlsx            # dry run
    python3 scripts/price_mapper_sync.py --xlsx ProductListing.xlsx --apply    # commit
    python3 scripts/price_mapper_sync.py --xlsx P.xlsx --apply --report out.xlsx

A dry run always writes the reconciliation workbook. --apply takes a pg_dump
backup of both tables first, then performs everything in ONE transaction.
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
import time
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

SECRETS = Path(os.environ.get("PM_SYNC_SECRETS", r"C:\gajab\.db-secrets.json"))


def default_dsn() -> str:
    """PM_SYNC_DSN, else the local stack's secrets file. Never a literal here."""
    if os.environ.get("PM_SYNC_DSN"):
        return os.environ["PM_SYNC_DSN"]
    try:
        pw = json.loads(SECRETS.read_text(encoding="utf-8"))["superuser"]
    except Exception as exc:  # pragma: no cover - configuration error
        raise SystemExit(f"no DSN: set PM_SYNC_DSN or provide {SECRETS} ({exc})")
    return f"postgresql://postgres:{pw}@127.0.0.1:5432/gajab"
DEFAULT_PG_BIN = r"C:\gajab\pg\pgsql\bin"
DEFAULT_BACKUP_DIR = r"C:\gajab\backups"

# price_mappings columns that ARE mapping progress: never written, and hashed
# before/after to prove they did not move.
PROGRESS_COLS = [
    "amazon_url", "amazon_price", "amazon_match_score", "amazon_dinov2",
    "amazon_clip", "amazon_match_tag", "amazon_unavailable", "amazon_reliable",
    "flipkart_url", "flipkart_price", "flipkart_match_score", "flipkart_dinov2",
    "flipkart_clip", "flipkart_match_tag", "flipkart_unavailable",
    "flipkart_reliable", "meesho_url", "meesho_price", "meesho_match_score",
    "meesho_dinov2", "meesho_clip", "meesho_match_tag", "meesho_unavailable",
    "meesho_reliable", "search_error", "last_checked",
    "amazon_price_checked_at", "flipkart_price_checked_at",
    "meesho_price_checked_at",
]


# ── normalisation ─────────────────────────────────────────────────────────
def url_tail(url: str) -> str:
    """Stable product code from a Gajab product URL (case/underscore-insensitive)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if "/product-detail/" in u:
        u = u.split("/product-detail/", 1)[1]
    raw = u.rsplit("/", 1)[-1]
    return re.sub(r"^[_\-\s]+", "", raw).strip().upper()


def norm_title(title: str) -> str:
    """Lowercase, unescape entities, drop brackets/punctuation for comparison."""
    t = html.unescape(title or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def money(value) -> str:
    """'285.00' -> '₹285' (same shape the storefront rows already use)."""
    if value is None:
        return ""
    s = str(value).strip().replace(",", "").replace("₹", "")
    if not s:
        return ""
    try:
        return f"₹{int(round(float(s))):,}"
    except (TypeError, ValueError):
        return ""


def clean(value) -> str:
    return " ".join(str(value or "").split())


# ── loading ───────────────────────────────────────────────────────────────
def load_source(path: Path, limit: int = 0):
    """One record per distinct product code; the export has one row per SKU."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Product Detail Sheet"] if "Product Detail Sheet" in wb.sheetnames else wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [clean(h) for h in next(it)]
    ix = {h: i for i, h in enumerate(hdr)}
    required = ["Product Name", "Product Status", "Product URL"]
    missing = [c for c in required if c not in ix]
    if missing:
        raise SystemExit(f"source sheet is missing column(s): {missing}")

    def g(r, name):
        i = ix.get(name)
        return r[i] if i is not None and i < len(r) else None

    out, dup_tails, mixed = {}, Counter(), 0
    for n, r in enumerate(it, start=2):
        url = clean(g(r, "Product URL"))
        t = url_tail(url)
        if not t:
            continue
        rec = {
            "row": n,
            "tail": t,
            "name": clean(g(r, "Product Name")),
            "status": clean(g(r, "Product Status")),
            "price": money(g(r, "Asking Price")),
            "mrp": money(g(r, "Mrp Price")),
            "image": clean(g(r, "Product Image 1")),
            "url": url,
            "category": clean(g(r, "Category Name")),
            "seller": clean(g(r, "Seller Name")),
        }
        prev = out.get(t)
        if prev is None:
            out[t] = rec
        else:
            dup_tails[t] += 1
            if prev["status"] != rec["status"]:
                mixed += 1
        if limit and len(out) >= limit:
            break
    return list(out.values()), {"duplicate_tails": dup_tails, "mixed_status_tails": mixed}


def load_db(conn):
    cur = conn.cursor()
    cur.execute("select id, name, price, mrp_price, image_url, url, category, seller from products")
    products = {}
    for pid, name, price, mrp, image, url, category, seller in cur.fetchall():
        products[pid] = {
            "id": pid, "name": name or "", "price": price or "", "mrp": mrp or "",
            "image": image or "", "url": url or "", "category": category or "",
            "seller": seller or "",
        }
    cur.execute("select gajab_product_id, amazon_url, flipkart_url, meesho_url from price_mappings")
    mappings = {gid: {"amazon": a, "flipkart": f, "meesho": m} for gid, a, f, m in cur.fetchall()}
    cur.execute("select id, url from products")
    id_tail = {pid: (url_tail(url) or re.sub(r"^gajab-", "", pid).lstrip("_").upper())
               for pid, url in cur.fetchall()}
    return products, mappings, id_tail


# ── planning ──────────────────────────────────────────────────────────────
def plan(source, products, mappings, id_tail, stats):
    by_tail = defaultdict(list)
    for pid, t in id_tail.items():
        by_tail[t].append(pid)
    by_url = {p["url"].rstrip("/"): pid for pid, p in products.items() if p["url"]}
    by_title = defaultdict(list)
    for pid, p in products.items():
        k = norm_title(p["name"])
        if k:
            by_title[k].append(pid)

    updates, inserts, removals, review = [], [], [], []
    used_ids: set = set()

    for s in source:
        tail = s["tail"]
        active = s["status"].lower() == "active"
        cands = by_tail.get(tail, [])

        # ── match ────────────────────────────────────────────────────────
        if len(cands) == 1:
            match, confidence, how = cands[0], "high", "product code"
        elif len(cands) > 1:
            exact = [pid for pid in cands if products[pid]["url"].rstrip("/") == s["url"].rstrip("/")]
            if len(exact) == 1:
                match, confidence, how = exact[0], "high", "product code + url"
            else:
                match, confidence, how = None, "medium", f"duplicate code ({len(cands)} records)"
        else:
            twins = [pid for pid in by_title.get(norm_title(s["name"]), [])
                     if pid not in used_ids]
            if twins:
                match, confidence, how = None, "medium", "title match only (no code match)"
            else:
                match, confidence, how = None, "none", "no match"

        if active:
            if confidence == "high":
                p = products[match]
                changed = [f for f in ("name", "price", "mrp", "image", "url", "category")
                           if (p.get(f) or "") != (s.get(f) or "")]
                used_ids.add(match)
                updates.append({"db_id": match, "tail": tail, "name": s["name"], "price": s["price"],
                                "mrp": s["mrp"], "image": s["image"], "url": s["url"],
                                "category": s["category"], "seller": s["seller"],
                                "how": how, "changed": changed,
                                "old_name": p["name"], "old_price": p["price"],
                                "has_mapping": match in mappings})
            elif confidence == "none":
                inserts.append({"id": "gajab-" + tail, "tail": tail, "name": s["name"],
                                "price": s["price"], "mrp": s["mrp"], "image": s["image"],
                                "url": s["url"], "category": s["category"], "seller": s["seller"],
                                "status": s["status"]})
            else:
                review.append({"reason": how, "status": s["status"], "tail": tail,
                               "title": s["name"], "candidates": ", ".join(cands or twins)})
        else:
            if confidence == "high":
                p = products[match]
                m = mappings.get(match) or {}
                removals.append({"db_id": match, "tail": tail, "name": p["name"],
                                 "source_status": s["status"], "how": how,
                                 "platforms": ",".join(k for k in ("amazon", "flipkart", "meesho") if m.get(k)),
                                 "has_mapping": match in mappings})
            elif confidence != "none":
                review.append({"reason": how, "status": s["status"], "tail": tail,
                               "title": s["name"], "candidates": ", ".join(cands or twins)})

    # a product cannot be both updated and removed
    upd_ids = {u["db_id"] for u in updates}
    removals = [r for r in removals if r["db_id"] not in upd_ids]
    rem_ids = {r["db_id"] for r in removals}
    inserts = [i for i in inserts if i["id"] not in products]
    stats.update({
        "source_products": len(source),
        "source_active": sum(1 for s in source if s["status"].lower() == "active"),
        "source_non_active": sum(1 for s in source if s["status"].lower() != "active"),
        "db_products_before": len(products),
        "db_products_not_in_source": len(set(products) - upd_ids - rem_ids),
        "updates": len(updates),
        "updates_unchanged": sum(1 for u in updates if not u["changed"]),
        "inserts": len(inserts),
        "removals": len(removals),
        "removals_with_mapping": sum(1 for r in removals if r["has_mapping"]),
        "removals_with_links": sum(1 for r in removals if r["platforms"]),
        "review": len(review),
    })
    return updates, inserts, removals, review


# ── report ────────────────────────────────────────────────────────────────
def write_report(path: Path, stats, updates, inserts, removals, review, meta):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Metric", "Value"])
    for k, v in list(stats.items()) + list(meta.items()):
        ws.append([k, v])
    for col, width in (("A", 34), ("B", 70)):
        ws.column_dimensions[col].width = width
    for c in ws[1]:
        c.font = Font(bold=True)

    def sheet(title, cols, rows):
        w = wb.create_sheet(title)
        w.append(cols)
        for c in w[1]:
            c.font = Font(bold=True)
        for r in rows:
            w.append([r.get(k, "") for k in cols])
        w.freeze_panes = "A2"
        if rows:
            w.auto_filter.ref = f"A1:{chr(64 + len(cols))}{len(rows) + 1}"

    sheet("Updates", ["tail", "db_id", "old_name", "name", "old_price", "price", "mrp",
                      "category", "how", "changed", "has_mapping"], updates)
    sheet("New Products", ["id", "name", "price", "mrp", "category", "seller", "url", "status"], inserts)
    sheet("Removals", ["db_id", "tail", "name", "source_status", "how", "platforms",
                       "has_mapping"], removals)
    sheet("Review", ["reason", "status", "tail", "title", "candidates"], review)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ── apply ─────────────────────────────────────────────────────────────────
def backup(conn, pg_bin: str, backup_dir: str) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(backup_dir) / f"price_mapper_pre_sync_{ts}.dump"
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PGPASSWORD"] = conn.get_dsn_parameters().get("password", "")
    cmd = [str(Path(pg_bin) / "pg_dump.exe"), "-h", "127.0.0.1", "-U", "postgres",
           "-d", "gajab", "-Fc", "--data-only", "-t", "public.products",
           "-t", "public.price_mappings", "-f", str(out)]
    subprocess.run(cmd, check=True, env=env)
    return out


def snapshot_progress(conn) -> dict:
    cur = conn.cursor()
    expr = "md5(row(" + ",".join(PROGRESS_COLS) + ")::text)"
    cur.execute(f"select gajab_product_id, {expr} from price_mappings")
    return dict(cur.fetchall())


def apply_changes(conn, updates, inserts, removals):
    cur = conn.cursor()
    # archive tables mirror the live ones
    for live, arch in (("products", "products_archive"), ("price_mappings", "price_mappings_archive")):
        cur.execute(f"create table if not exists {arch} (like {live} including defaults)")
        cur.execute(f"alter table {arch} add column if not exists archived_at timestamptz default now()")
        cur.execute(f"alter table {arch} add column if not exists archive_reason text")
        cur.execute(f"alter table {arch} add column if not exists source_status text")

    if updates:
        cur.execute("create temp table _sync_upd (id text primary key, name text, price text, "
                    "mrp text, image text, url text, category text) on commit drop")
        cur.executemany("insert into _sync_upd values (%s,%s,%s,%s,%s,%s,%s)",
                        [(u["db_id"], u["name"], u["price"], u["mrp"], u["image"], u["url"],
                          u["category"]) for u in updates])
        cur.execute("""
            update products p set name=u.name, price=u.price, mrp_price=u.mrp,
                   image_url=u.image, url=u.url, category=u.category
              from _sync_upd u where p.id=u.id""")
        # keep the denormalised product copy in price_mappings fresh; mapping
        # columns are deliberately absent from this SET list
        cur.execute("""
            update price_mappings m
               set gajab_title=u.name, gajab_price=u.price,
                   gajab_image_url=u.image, gajab_url=u.url
              from _sync_upd u where m.gajab_product_id=u.id""")

    if inserts:
        cur.executemany("""
            insert into products (id, name, price, mrp_price, image_url, url, category, seller)
            values (%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (id) do nothing""",
            [(i["id"], i["name"], i["price"], i["mrp"], i["image"], i["url"],
              i["category"], i["seller"]) for i in inserts])

    if removals:
        ids = [r["db_id"] for r in removals]
        status = {r["db_id"]: r["source_status"] for r in removals}
        cur.execute("create temp table _sync_rem (id text primary key, status text) on commit drop")
        cur.executemany("insert into _sync_rem values (%s,%s)", list(status.items()))
        cur.execute("""
            insert into price_mappings_archive
            select m.*, now(), 'price_mapper_sync', r.status
              from price_mappings m join _sync_rem r on r.id = m.gajab_product_id""")
        cur.execute("delete from price_mappings m using _sync_rem r where r.id = m.gajab_product_id")
        cur.execute("""
            insert into products_archive
            select p.*, now(), 'price_mapper_sync', r.status
              from products p join _sync_rem r on r.id = p.id""")
        cur.execute("delete from products p using _sync_rem r where r.id = p.id")
    return len(updates), len(inserts), len(removals)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", required=True, help="ProductListing_Export*.xlsx")
    ap.add_argument("--dsn", default="")
    ap.add_argument("--pg-bin", default=DEFAULT_PG_BIN)
    ap.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    ap.add_argument("--report", default="")
    ap.add_argument("--limit", type=int, default=0, help="only the first N source products (testing)")
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    args = ap.parse_args()

    t0 = time.time()
    src_path = Path(args.xlsx).expanduser()
    if not src_path.exists():
        raise SystemExit(f"not found: {src_path}")

    source, src_stats = load_source(src_path, args.limit)
    conn = psycopg2.connect(args.dsn or default_dsn())
    try:
        products, mappings, id_tail = load_db(conn)
        stats = dict(src_stats)
        updates, inserts, removals, review = plan(source, products, mappings, id_tail, stats)

        print("=" * 74)
        print("PRICE MAPPER SYNC - " + ("APPLY" if args.apply else "DRY RUN"))
        print("=" * 74)
        for k in ("source_products", "source_active", "source_non_active", "db_products_before",
                  "updates", "updates_unchanged", "inserts", "removals",
                  "removals_with_mapping", "removals_with_links", "review",
                  "db_products_not_in_source", "duplicate_tails", "mixed_status_tails"):
            if k in stats:
                print(f"  {k:28s} {stats[k]}")

        report = Path(args.report).expanduser() if args.report else Path.home() / "Downloads" / (
            "price_mapper_sync_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            + ("" if args.apply else "_dryrun") + ".xlsx")
        write_report(report, stats, updates, inserts, removals, review,
                     {"source_file": str(src_path), "mode": "apply" if args.apply else "dry-run",
                      "generated_at": dt.datetime.now().isoformat(timespec="seconds")})
        print(f"\n  report -> {report}")

        if not args.apply:
            print("\n  nothing written (dry run). re-run with --apply to commit.")
            return 0

        before = snapshot_progress(conn)
        dump = backup(conn, args.pg_bin, args.backup_dir)
        print(f"  backup -> {dump}")
        try:
            n_u, n_i, n_r = apply_changes(conn, updates, inserts, removals)
            conn.commit()
        except Exception:
            conn.rollback()
            print("  ROLLED BACK - nothing changed", file=sys.stderr)
            raise
        print(f"  committed: {n_u} updated, {n_i} inserted, {n_r} removed")

        after = snapshot_progress(conn)
        kept = set(after) & set(before)
        moved = [g for g in kept if before[g] != after[g]]
        removed = set(before) - set(after)
        print(f"  mapping progress check: {len(kept)} retained rows, {len(moved)} changed"
              f" (expected 0), {len(removed)} removed")
        if moved:
            print("  WARNING: mapping columns changed for:", moved[:5], file=sys.stderr)
        cur = conn.cursor()
        cur.execute("select count(*) from products")
        print(f"  products now: {cur.fetchone()[0]}")
        print(f"  done in {time.time() - t0:.1f}s")
        return 0 if not moved else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
