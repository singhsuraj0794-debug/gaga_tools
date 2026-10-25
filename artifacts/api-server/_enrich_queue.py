#!/usr/bin/env python3
"""Background enrichment worker: category + attributes, off the export path.

Why: the agent passes are GPU work (~2-5s per product) while scraping is network
/CPU work. Running enrichment afterwards made exports slow; running it here, on a
single queue, means:

  * scraping is never slowed (2 worker threads, `ENRICH_WORKERS`)
  * several scrape jobs share ONE agent queue instead of competing for the GPU
  * exports read the result from `product_cache.db` and just write files
  * the queue is persisted, so a restart resumes where it stopped

Producers: the scrape routes enqueue a completed job's products; the export
enqueues anything it still has to compute.

    python3 _enrich_queue.py enqueue --platform flipkart --job <jobId>
    python3 _enrich_queue.py enqueue --platform amazon --products job.json
    python3 _enrich_queue.py worker            # long-running, started by the stack
    python3 _enrich_queue.py status [--json]
    python3 _enrich_queue.py purge             # drop finished rows

Queue state lives in C:\\gajab\\templates\\product_specs.db (same file as the spec
store), progress in C:\\gajab\\enrich_progress.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DB_PATH = Path(os.environ.get("SPEC_STORE_DB", r"C:\gajab\templates\product_specs.db"))
PROGRESS = Path(os.environ.get("ENRICH_PROGRESS", r"C:\gajab\enrich_progress.json"))
# 1 worker by default: the box is a 6-core CPU and Chrome needs the cores while
# scraping. The queue is a background nicety, not a race.
WORKERS = max(1, int(os.environ.get("ENRICH_WORKERS", "1")))
BATCH = max(1, int(os.environ.get("ENRICH_BATCH", "8")))
# The API drops {"platform","products"} files here as products are scraped; the
# worker ingests them into the queue. A spool file is cheaper and more reliable
# than spawning a python process per flush.
SPOOL_DIR = Path(os.environ.get("ENRICH_SPOOL_DIR", r"C:\gajab\enrich_spool"))
# Enrichment is meant to run WHILE scraping (GPU idle, products arrive). Set
# ENRICH_PAUSE_ON_SCRAPE=1 to make it yield completely instead.
PAUSE_ON_SCRAPE = os.environ.get("ENRICH_PAUSE_ON_SCRAPE", "0") == "1"

QUEUE_SCHEMA = """
create table if not exists enrich_queue (
    id          integer primary key autoincrement,
    platform    text not null,
    product_id  text not null,
    payload     text not null,          -- the scraper's product object
    state       text not null default 'pending',   -- pending|running|done|failed
    tries       integer not null default 0,
    error       text,
    added_at    text not null,
    updated_at  text not null,
    unique (platform, product_id, state) on conflict ignore
);
create index if not exists enrich_queue_state on enrich_queue (state, id);
"""


def connect(db: Path = DB_PATH) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.executescript(QUEUE_SCHEMA)
    return conn


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _pid(p: dict) -> str:
    return str(p.get("id") or p.get("sku") or p.get("url") or "")


def enqueue(platform: str, products: list[dict], db: Path = DB_PATH) -> int:
    """Queue products that do not already have a cached decision."""
    try:
        import _enrich_cache
    except Exception:
        _enrich_cache = None
    ids = [_pid(p) for p in products if _pid(p)]
    cached = _enrich_cache.get_many(platform, ids) if _enrich_cache else {}
    rows = []
    ts = now()
    for p in products:
        pid = _pid(p)
        if not pid or pid in cached:
            continue
        rows.append((platform, pid, json.dumps(p, ensure_ascii=False)[:200000], "pending", ts, ts))
    if not rows:
        return 0
    conn = connect(db)
    conn.executemany(
        """insert into enrich_queue (platform, product_id, payload, state, added_at, updated_at)
           values (?,?,?,?,?,?)
           on conflict(platform, product_id, state) do nothing""", rows)
    conn.commit()
    conn.close()
    return len(rows)


def ingest_spool(db: Path = DB_PATH) -> int:
    """Move spooled products into the queue. Returns how many were added."""
    added = 0
    try:
        if not SPOOL_DIR.is_dir():
            return 0
        for f in sorted(SPOOL_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                added += enqueue(str(data.get("platform") or ""), data.get("products") or [], db)
            except Exception:
                pass
            finally:
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    return added


def reap_stale(db: Path = DB_PATH, minutes: int = 5, max_tries: int = 3) -> int:
    """Requeue rows a killed worker left in 'running' (and retryable failures).

    Without this, a deploy/restart mid-batch strands those products forever: they
    are neither pending nor done, so nothing ever picks them up again.
    """
    cutoff = (dt.datetime.now() - dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")
    try:
        conn = connect(db)
        n = conn.execute(
            """update enrich_queue set state='pending', updated_at=?
                where (state='running' and updated_at < ?)
                   or (state='failed' and tries < ? and coalesce(error,'') not like '%no category%')""",
            (now(), cutoff, max_tries)).rowcount
        conn.commit(); conn.close()
        return n
    except Exception:
        return 0


def scrape_busy() -> bool:
    """True while any scrape job is running or pending.

    Enrichment must yield to scraping: both compete for the same 6 cores, and
    running them together made scraping ~3.5x slower (8.2s vs 2.3s per product).
    """
    try:
        for plat in ("flipkart", "meesho", "amazon"):
            d = HERE / f"{plat}_jobs"
            if not d.is_dir():
                continue
            files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:4]
            for f in files:
                try:
                    j = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if j.get("status") in ("running", "pending"):
                    return True
    except Exception:
        pass
    return False


def _write_progress(**kw) -> None:
    try:
        PROGRESS.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROGRESS.with_suffix(".tmp")
        tmp.write_text(json.dumps(kw), encoding="utf-8")
        tmp.replace(PROGRESS)
    except Exception:
        pass


def counts(db: Path = DB_PATH) -> dict:
    conn = connect(db)
    q = lambda s: conn.execute(s).fetchone()[0]
    out = {
        "pending": q("select count(*) from enrich_queue where state='pending'"),
        "running": q("select count(*) from enrich_queue where state='running'"),
        "done": q("select count(*) from enrich_queue where state='done'"),
        "failed": q("select count(*) from enrich_queue where state='failed'"),
        "skipped": q("select count(*) from enrich_queue where state='skipped'"),
    }
    conn.close()
    return out


def enrich_one(product: dict, platform: str) -> dict | None:
    """Resolve the L4 (agent first) then map its attributes. Returns a cache record."""
    import _category_agent as ca
    import _category_map as cmap
    import _prepare_gajab_export as ex

    mapper = cmap.CategoryMapper()
    src = product.get("source_category_path") or product.get("sourceCategoryPath") or ""
    title = ex._clean(product.get("title"))

    path, method, conf = "", "rules", 0.0
    try:
        if ca.available():
            paths = ca.load_targets()
            emb = ca.load_index(paths)
            res = ca.classify_one(product, paths, emb)
            if res.get("path"):
                path, method, conf = res["path"], "agent", res.get("confidence", 0.0)
    except Exception:
        pass
    if not path:
        r = mapper.resolve(platform, src, title=title)
        if r:
            path, method, conf = r["full"], r.get("method", "rules"), r.get("confidence", 0.0)
    if not path:
        return None

    attributes: dict[str, str] = {}
    try:
        am = ex.SpecAttributeMapper(ex.load_catalog())
        mapped = am.map_specs(ex._normalise_specs(product.get("specifications")), path,
                              platform=platform)
        attributes = dict(mapped.get("attributes") or {})
        if mapped.get("unmapped") and ca.available():
            extra = ca.map_attributes(mapped["unmapped"],
                                      [a["attribute"] for a in am.attrs_for(path)], title)
            for k, v in extra.items():
                attributes.setdefault(k, v)
    except Exception:
        pass

    return {"platform": platform, "product_id": _pid(product), "l4_path": path,
            "attributes": attributes, "method": method, "confidence": conf}


def acquire_singleton():
    """Only ONE worker may consume the queue (two doubles the CPU taken from Chrome)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.CreateMutexW(None, False, "Global\\gajab_enrich_worker")
        if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:
        return True                              # non-Windows: no guard


def worker(db: Path = DB_PATH, once: bool = False, wait: bool = False,
           idle_sleep: int = 20) -> int:
    """Consume the queue until empty. One process, WORKERS threads."""
    from concurrent.futures import ThreadPoolExecutor
    import _enrich_cache

    if not once and acquire_singleton() is None:
        print("enrich worker: another instance is already running", flush=True)
        return 0

    ingest_spool(db)
    requeued = reap_stale(db)
    if requeued:
        print(f"enrich worker: requeued {requeued} stale row(s)", flush=True)
    totals = counts(db)
    total = totals["pending"] + totals["running"]
    done = 0
    print(f"enrich worker: {total} pending, {WORKERS} workers", flush=True)
    _write_progress(state="running", queued=total, done=0, total=total,
                    updated_at=now(), detail="starting")

    while True:
        ingest_spool(db)
        reap_stale(db)
        if wait and not once and PAUSE_ON_SCRAPE and scrape_busy():
            _write_progress(state="paused", queued=counts(db)["pending"], done=done,
                            total=total, updated_at=now(),
                            detail="paused - scraping in progress")
            time.sleep(idle_sleep)
            continue
        conn = connect(db)
        rows = conn.execute(
            "select id, platform, payload from enrich_queue where state='pending' order by id limit ?",
            (BATCH,)).fetchall()
        if not rows:
            conn.close()
            if wait and not once:
                time.sleep(idle_sleep)
                c = counts(db)
                _write_progress(state="idle" if c["pending"] == 0 else "running",
                                queued=c["pending"] + c["running"], done=c["done"],
                                total=c["done"] + c["failed"], updated_at=now(),
                                detail="waiting for work", failed=c["failed"])
                continue
            break
        ids = [r[0] for r in rows]
        conn.executemany("update enrich_queue set state='running', updated_at=? where id=?",
                         [(now(), i) for i in ids])
        conn.commit()
        conn.close()

        def _do(row):
            rid, platform, payload = row
            try:
                prod = json.loads(payload)
                rec = enrich_one(prod, platform)
                if rec:
                    _enrich_cache.put_many([rec])
                    return rid, None
                return rid, "no category"
            except Exception as exc:  # noqa: BLE001
                return rid, f"{type(exc).__name__}: {exc}"

        results = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for rid, err in pool.map(_do, rows):
                if err is None:
                    results.append((rid, "done", None))
                elif err == "no category":
                    # deterministic: this product has no mappable category, so a
                    # retry would spin forever (and keep the bar non-zero)
                    results.append((rid, "skipped", err))
                else:
                    results.append((rid, "failed", err))
        conn = connect(db)
        conn.executemany(
            "update enrich_queue set state=?, error=?, tries=tries+1, updated_at=? where id=?",
            [(state, err, now(), rid) for rid, state, err in results])
        conn.commit()
        conn.close()

        done += len(results)
        _write_progress(state="running", queued=total, done=done, total=total,
                        updated_at=now(),
                        detail=f"{sum(1 for _, s, _ in results if s == 'done')} ok, "
                               f"{sum(1 for _, s, _ in results if s == 'failed')} failed")
        print(f"  {done}/{total}", flush=True)
        if once:
            break

    c = counts(db)
    _write_progress(state="idle" if not c["pending"] else "running",
                    queued=c["pending"] + c["running"], done=c["done"], total=c["done"] + c["failed"],
                    updated_at=now(), detail="queue drained",
                    failed=c["failed"])
    print(f"enrich worker done: {c}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="background enrichment queue")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enqueue")
    p.add_argument("--platform", required=True)
    p.add_argument("--job", help="scrape job id (reads its persisted products)")
    p.add_argument("--products", help="path to a JSON file with a products array")

    pw = sub.add_parser("worker")
    pw.add_argument("--once", action="store_true")
    pw.add_argument("--wait", action="store_true", help="keep running, polling for work")
    pw.add_argument("--idle-sleep", type=int, default=20)
    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    sub.add_parser("purge")
    args = ap.parse_args()

    if args.cmd == "enqueue":
        prods = []
        if args.job:
            f = HERE / f"{args.platform}_jobs" / f"{args.job}.json"
            job = json.loads(f.read_text(encoding="utf-8"))
            prods = job.get("products") or []
        elif args.products:
            raw = json.loads(Path(args.products).read_text(encoding="utf-8"))
            prods = raw.get("products") if isinstance(raw, dict) else raw
        n = enqueue(args.platform, prods or [])
        print(f"queued {n} product(s) for {args.platform}")
        return 0

    if args.cmd == "worker":
        return worker(once=args.once, wait=args.wait, idle_sleep=args.idle_sleep)

    if args.cmd == "status":
        c = counts()
        try:
            prog = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            prog = {}
        out = {**c, "progress": prog}
        print(json.dumps(out, indent=2) if args.json else
              f"pending={c['pending']} running={c['running']} done={c['done']} failed={c['failed']}")
        return 0

    if args.cmd == "purge":
        conn = connect()
        n = conn.execute("delete from enrich_queue where state in ('done','failed')").rowcount
        conn.commit(); conn.close()
        print(f"purged {n} finished row(s)")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
