#!/usr/bin/env python3
"""Persist every product's scraped specs and how they mapped to Gajab attributes.

Why: the export used to throw away any scraped key it could not match, so there
was no record of what a source page actually said. This keeps, per product:

    raw specs          - exactly what the page reported (key -> value)
    mapped             - which Gajab attribute each value landed in
    unmapped           - the keys that found no attribute, with the reason

Two uses:

  * audit - "why is this column blank?" is answerable from the store
  * growth - `top-unmapped` ranks the keys that are losing the most data, and
    `alias` writes a permanent spec_attribute_map.json entry so the next run maps
    them (the kind of thing Amazon's "Lid Material" / "Item Volume" needed).

CLI
---
    python3 _spec_store.py top-unmapped [--limit 40] [--platform amazon]
    python3 _spec_store.py show --id B08LT7ZW96 [--platform amazon]
    python3 _spec_store.py alias --key "container shape" --attribute shape [--write]
    python3 _spec_store.py stats

Storage: C:\\gajab\\templates\\product_specs.db (SQLite, WAL).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DB_PATH = Path(os.environ.get("SPEC_STORE_DB", r"C:\gajab\templates\product_specs.db"))
ALIAS_FILE = Path(__file__).with_name("spec_attribute_map.json")

SCHEMA = """
create table if not exists product_specs (
    platform    text not null,
    product_id  text not null,
    url         text,
    title       text,
    l4_path     text,
    specs       text,          -- raw scraped specs, JSON
    mapped      text,          -- attribute -> value, JSON
    unmapped    text,          -- [{key, value, reason}], JSON
    updated_at  text not null,
    primary key (platform, product_id)
);
create index if not exists product_specs_l4 on product_specs (l4_path);
create index if not exists product_specs_updated on product_specs (updated_at desc);
"""


def connect(db: Path = DB_PATH) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.executescript(SCHEMA)
    return conn


def record_many(records: list[dict], db: Path = DB_PATH) -> int:
    """Upsert a batch of {platform, product_id, url, title, l4_path, specs, mapped, unmapped}."""
    if not records:
        return 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        conn = connect(db)
        conn.executemany(
            """insert into product_specs
               (platform, product_id, url, title, l4_path, specs, mapped, unmapped, updated_at)
               values (?,?,?,?,?,?,?,?,?)
               on conflict(platform, product_id) do update set
                 url=excluded.url, title=excluded.title, l4_path=excluded.l4_path,
                 specs=excluded.specs, mapped=excluded.mapped, unmapped=excluded.unmapped,
                 updated_at=excluded.updated_at""",
            [(r.get("platform", ""), r.get("product_id", ""), r.get("url", ""),
              (r.get("title") or "")[:400], r.get("l4_path", ""),
              json.dumps(r.get("specs") or {}, ensure_ascii=False),
              json.dumps(r.get("mapped") or {}, ensure_ascii=False),
              json.dumps(r.get("unmapped") or [], ensure_ascii=False),
              now) for r in records],
        )
        conn.commit()
        conn.close()
        return len(records)
    except Exception as exc:  # noqa: BLE001 - never fail an export over the store
        print(f"spec-store: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


def top_unmapped(limit: int = 40, platform: str = "", db: Path = DB_PATH) -> list[dict]:
    conn = connect(db)
    q = "select unmapped from product_specs"
    params: tuple = ()
    if platform:
        q += " where platform = ?"
        params = (platform,)
    counts: Counter = Counter()
    samples: dict[str, str] = {}
    for (raw,) in conn.execute(q, params):
        try:
            for item in json.loads(raw or "[]"):
                k = (item.get("key") or "").strip()
                if not k:
                    continue
                counts[k] += 1
                samples.setdefault(k, str(item.get("value"))[:80])
        except Exception:
            continue
    conn.close()
    return [{"key": k, "count": n, "sample": samples[k]} for k, n in counts.most_common(limit)]


def suggest_attribute(key: str, declared: list[str]) -> str:
    """Cheap suggestion: share a token with a declared attribute name."""
    kt = set(key.lower().replace("?", " ").split())
    best, score = "", 0
    for a in declared:
        at = set(a.lower().split())
        s = len(kt & at)
        if s > score:
            best, score = a, s
    return best


def write_alias(key: str, attribute: str, alias_file: Path = ALIAS_FILE) -> None:
    data = json.loads(alias_file.read_text(encoding="utf-8"))
    data.setdefault("aliases", {})[key.strip().lower()] = attribute.strip()
    data["version"] = round(float(data.get("version", 1.0)) + 0.01, 2)
    alias_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="scraped spec store")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("top-unmapped")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--platform", default="")

    p = sub.add_parser("show")
    p.add_argument("--id", required=True)
    p.add_argument("--platform", default="")

    p = sub.add_parser("alias")
    p.add_argument("--key", required=True)
    p.add_argument("--attribute", required=True,
                   help='Gajab attribute name, or "" to drop the key')
    p.add_argument("--write", action="store_true")

    sub.add_parser("stats")
    args = ap.parse_args()

    if args.cmd == "stats":
        conn = connect()
        q = lambda s: conn.execute(s).fetchone()[0]
        print("products stored:", q("select count(*) from product_specs"))
        print("by platform:", dict(conn.execute(
            "select platform, count(*) from product_specs group by 1").fetchall()))
        print("newest:", q("select max(updated_at) from product_specs"))
        conn.close()
        return 0

    if args.cmd == "top-unmapped":
        rows = top_unmapped(args.limit, args.platform)
        if not rows:
            print("no unmapped keys stored yet")
            return 0
        print(f"{'count':>6}  {'key':44} sample value")
        for r in rows:
            print(f"{r['count']:6}  {r['key'][:44]:44} {r['sample'][:46]}")
        return 0

    if args.cmd == "show":
        conn = connect()
        conn.row_factory = sqlite3.Row
        q = "select * from product_specs where product_id = ?"
        params = [args.id]
        if args.platform:
            q += " and platform = ?"
            params.append(args.platform)
        row = conn.execute(q, params).fetchone()
        conn.close()
        if not row:
            print("not found")
            return 1
        print(json.dumps(dict(row), indent=2, ensure_ascii=False)[:4000])
        return 0

    if args.cmd == "alias":
        if args.write:
            write_alias(args.key, args.attribute)
            print(f"alias written: {args.key!r} -> {args.attribute!r}")
        else:
            print(f"would write: {args.key!r} -> {args.attribute!r} (re-run with --write)")
        return 0
    return 2



if __name__ == "__main__":
    raise SystemExit(main())
