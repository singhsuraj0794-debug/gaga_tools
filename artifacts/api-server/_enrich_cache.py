#!/usr/bin/env python3
"""Persistent per-product enrichment cache (category + attributes).

Both agent passes are expensive (category: one GPU decision per product;
attributes: one more). Their answers are deterministic for the same product and
taxonomy, so they are cached here and reused on every later export - which is
what makes a re-run, or a run after a restart, cheap instead of starting over.

Keyed by (platform, product_id). Invalidated by bumping CACHE_VERSION whenever the
taxonomy, the alias map or the agent prompts change in a way that should force a
fresh decision.

    from _enrich_cache import get_many, put_many
    cached = get_many("amazon", [ids...])    # {product_id: {l4_path, attributes, method, confidence}}
    put_many([{platform, product_id, l4_path, attributes, method, confidence}, ...])
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(os.environ.get("ENRICH_CACHE_DB", r"C:\gajab\templates\product_cache.db"))
# Bump when a change should invalidate old answers (taxonomy paths, aliases,
# agent prompts). Stored in the row so stale rows are simply ignored.
CACHE_VERSION = "v1"

SCHEMA = """
create table if not exists product_enrichment (
    platform    text not null,
    product_id  text not null,
    l4_path     text,
    attributes  text,          -- {attribute: value}
    method      text,
    confidence  real,
    version     text not null,
    updated_at  text not null,
    primary key (platform, product_id)
);
create index if not exists product_enrichment_version on product_enrichment (version);
"""


def connect(db: Path = DB_PATH) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.executescript(SCHEMA)
    return conn


def get_many(platform: str, product_ids: list[str], db: Path = DB_PATH) -> dict[str, dict]:
    ids = [str(i) for i in product_ids if i]
    if not ids:
        return {}
    out: dict[str, dict] = {}
    try:
        conn = connect(db)
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""select product_id, l4_path, attributes, method, confidence
                      from product_enrichment
                     where platform = ? and version = ? and product_id in ({marks})""",
                [platform, CACHE_VERSION, *chunk],
            ).fetchall()
            for pid, path, attrs, method, conf in rows:
                try:
                    attrs_d = json.loads(attrs or "{}")
                except Exception:
                    attrs_d = {}
                out[pid] = {"l4_path": path or "", "attributes": attrs_d,
                            "method": method or "", "confidence": conf or 0.0}
        conn.close()
    except Exception as exc:  # noqa: BLE001 - cache must never break an export
        print(f"enrich-cache: {type(exc).__name__}: {exc}", file=sys.stderr)
    return out


def put_many(records: list[dict], db: Path = DB_PATH) -> int:
    rows = [r for r in records if r.get("product_id")]
    if not rows:
        return 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        conn = connect(db)
        conn.executemany(
            """insert into product_enrichment
               (platform, product_id, l4_path, attributes, method, confidence, version, updated_at)
               values (?,?,?,?,?,?,?,?)
               on conflict(platform, product_id) do update set
                 l4_path=excluded.l4_path, attributes=excluded.attributes,
                 method=excluded.method, confidence=excluded.confidence,
                 version=excluded.version, updated_at=excluded.updated_at""",
            [(r.get("platform", ""), str(r["product_id"]), r.get("l4_path") or "",
              json.dumps(r.get("attributes") or {}, ensure_ascii=False),
              r.get("method", ""), float(r.get("confidence") or 0.0),
              CACHE_VERSION, now) for r in rows],
        )
        conn.commit()
        conn.close()
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        print(f"enrich-cache: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


def stats(db: Path = DB_PATH) -> dict:
    try:
        conn = connect(db)
        q = lambda s: conn.execute(s).fetchone()[0]
        out = {
            "rows": q("select count(*) from product_enrichment"),
            "current_version": q(f"select count(*) from product_enrichment where version = '{CACHE_VERSION}'"),
            "by_platform": dict(conn.execute(
                "select platform, count(*) from product_enrichment group by 1").fetchall()),
            "newest": q("select max(updated_at) from product_enrichment"),
        }
        conn.close()
        return out
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
