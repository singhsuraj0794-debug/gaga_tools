#!/usr/bin/env python3
"""
Parallel bulk-create of missing categories via the Gajab backend API.

Processes one level at a time (L2 -> L3 -> L4) so every parent exists before
its children are created. Within a level, requests run concurrently with a
per-parent unique sort-order allocator.

Usage:
  python3 bulk_create_parallel.py --workers 6
  python3 bulk_create_parallel.py --workers 6 --level 3
"""
from __future__ import annotations
import base64, io, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from PIL import Image
import bulk_create_api as B

SORT_BASE = 5000  # start new sort orders here to skip soft-deleted slots (max allowed is 9999)


def main():
    workers = 6
    only_level = None
    limit = None
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    if "--level" in sys.argv:
        only_level = int(sys.argv[sys.argv.index("--level") + 1])
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    tok = B.token()
    cats = B.parse_sheet()
    image_b64 = "data:image/jpeg;base64," + base64.b64encode(B.IMAGE_FILE.read_bytes()).decode()

    sort_lock = threading.Lock()
    id_lock = threading.Lock()

    levels = [1, 2, 3, 4] if only_level is None else [only_level]
    total_created = total_failed = 0
    failures = []

    for level in levels:
        # Refresh state so parents created in the previous level are visible
        items = B.fetch_items(tok)
        path_to_id = {i.get("levels", ""): i.get("categoryId") for i in items}
        sort_next = {}
        for i in items:
            pid = i.get("parentInt") or 0
            sort_next[pid] = max(sort_next.get(pid, 0), i.get("sortOrder") or 0)
        # Start new sort orders high to skip slots held by soft-deleted rows.
        for pid in list(sort_next):
            sort_next[pid] = max(sort_next[pid], SORT_BASE - 1)

        todo = sorted([p for p in cats if cats[p]["level"] == level and p not in path_to_id])
        if limit is not None:
            todo = todo[:limit]
        if not todo:
            B.log(f"L{level}: nothing missing")
            continue
        B.log(f"L{level}: {len(todo)} to create, {workers} workers")

        def create_one(path):
            cat = cats[path]
            parent_path = cat["parent_path"]
            parent_id = path_to_id.get(parent_path) if parent_path else None
            if parent_path and parent_id is None:
                return path, "parent missing", None

            pid_key = parent_id or 0
            with sort_lock:
                so = sort_next.get(pid_key, SORT_BASE - 1) + 1
                sort_next[pid_key] = so

            payload = B.build_payload(cat, parent_id, so, image_b64)
            status, resp = B.api_post("/product/api/category", payload, tok)
            msg = resp.get("message") or resp.get("error") or ""

            # Retry transient errors (5xx / network) and sort-order collisions
            transient = 0
            tries = 0
            while True:
                low = msg.lower()
                if status in (200, 201) and resp.get("status") in (1, "1", True):
                    break
                if "sort order" in low and tries < 40:
                    tries += 1
                    with sort_lock:
                        if tries > 15:
                            sort_next[pid_key] = sort_next.get(pid_key, SORT_BASE) + 100
                        so = sort_next.get(pid_key, SORT_BASE - 1) + 1
                        sort_next[pid_key] = so
                    payload["sortOrder"] = so
                elif (status >= 500 or status == 0 or "timed out" in low) and transient < 3:
                    transient += 1
                else:
                    break
                status, resp = B.api_post("/product/api/category", payload, tok)
                msg = resp.get("message") or resp.get("error") or ""

            ok = status in (200, 201) and (resp.get("status") in (1, "1", True) or "data" in resp)
            if not ok:
                return path, f"HTTP {status}: {str(msg)[:160]}", None

            new_id = None
            data = resp.get("data")
            if isinstance(data, dict):
                new_id = data.get("categoryId") or data.get("id")
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                new_id = data[0].get("categoryId") or data[0].get("id")
            with id_lock:
                if new_id is not None:
                    path_to_id[path] = new_id
            return path, None, new_id

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(create_one, p): p for p in todo}
            for fut in as_completed(futures):
                path, err, new_id = fut.result()
                done += 1
                if err:
                    total_failed += 1
                    failures.append((path, err))
                    B.log(f"[L{level} {done}/{len(todo)}] FAIL {path}: {err}")
                else:
                    total_created += 1
                    if done % 25 == 0 or done == len(todo):
                        B.log(f"[L{level} {done}/{len(todo)}] created (last {path} id={new_id})")

        # Invalidate the cached id-lookup for next level
        path_to_id = None

    B.log("=" * 60)
    B.log(f"Created {total_created}, failed {total_failed}")
    for p, e in failures[:50]:
        B.log(f"  FAIL {p}: {e}")


if __name__ == "__main__":
    main()
