#!/usr/bin/env python3
"""
prune_storage.py - keep only the last N hours of monitor artefacts in the
Supabase 'monitoring' storage bucket.

The happy-flow run uploads a recording and a screenshot per step. The old
retention had three bugs that let the bucket reach ~20 GB (699 recordings +
17,270 screenshots) and hit the Supabase storage quota:

  1. it deleted with the anon key while the bucket had no DELETE policy, so every
     remove() was a silent no-op that still printed "Deleted old recording";
  2. it used storage.list() with supabase-py's default page of 100, so it never
     saw the full list;
  3. it never pruned screenshots at all.

This module pages the listing, parses the timestamp out of each object name,
deletes everything older than the window, and VERIFIES the delete actually
removed rows (a 200 with [] means RLS filtered them - reported, not ignored).
`prune()` is imported by supabase_client.SupabaseStore after each run.

Usage:
  python3 prune_storage.py                     # dry run
  python3 prune_storage.py --apply             # delete
  python3 prune_storage.py --apply --hours 24 --prefix recordings
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = "monitoring"
DEFAULT_PREFIXES = ("recordings", "screenshots")

# e.g. 2026-08-30T13-49-08-784501+00-00_android.webm
_NAME_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")


def _load_env() -> None:
    """Populate os.environ from monitoring/.env for keys that are not already set."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _resolve(url: str | None, key: str | None) -> tuple[str, str]:
    _load_env()
    url = url or os.environ.get("SUPABASE_URL", "")
    if not key:
        for name in ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"):
            if os.environ.get(name):
                key = os.environ[name]
                break
    return url, key or ""


def _req(url: str, key: str, method: str, path: str, body: bytes | None = None) -> bytes:
    req = urllib.request.Request(
        f"{url}{path}", data=body, method=method,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _list(url: str, key: str, prefix: str) -> list[dict]:
    """Page the storage list endpoint - it caps a page, so walk offsets."""
    out: list[dict] = []
    offset = 0
    while True:
        body = json.dumps({"prefix": prefix, "limit": 1000, "offset": offset}).encode()
        items = json.loads(_req(url, key, "POST", f"/storage/v1/object/list/{BUCKET}", body))
        if not isinstance(items, list) or not items:
            break
        out.extend(items)
        offset += len(items)
        if len(items) < 1000 or offset > 500000:
            break
    return out


def _ts_of(name: str) -> datetime | None:
    m = _NAME_TS.match(name or "")
    if not m:
        return None
    try:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None


def prune(hours: float = 24.0, prefixes=DEFAULT_PREFIXES, apply: bool = True,
          url: str | None = None, key: str | None = None,
          verbose: bool = True) -> dict:
    """Delete monitor artefacts older than `hours`. Returns a summary dict."""
    url, key = _resolve(url, key)
    summary: dict = {"deleted": 0, "kept": 0, "undated": 0, "error": "", "per_prefix": {}}
    if not url or not key:
        summary["error"] = "missing SUPABASE_URL or key"
        if verbose:
            print(f"[PRUNE] {summary['error']}")
        return summary

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for prefix in prefixes:
        try:
            items = _list(url, key, f"{prefix}/")
        except Exception as e:
            summary["error"] = f"list {prefix} failed: {e}"
            if verbose:
                print(f"[PRUNE] {summary['error']}")
            continue

        old, kept, undated = [], 0, 0
        for it in items:
            name = it.get("name") or ""
            ts = _ts_of(name)
            if ts is None:
                undated += 1
            elif ts < cutoff:
                old.append(f"{prefix}/{name}")
            else:
                kept += 1
        summary["kept"] += kept
        summary["undated"] += undated
        summary["per_prefix"][prefix] = {"total": len(items), "old": len(old), "kept": kept}

        if verbose:
            print(f"[PRUNE] [{prefix}] {len(items)} objects -> delete {len(old)}, keep {kept}"
                  f"{f', {undated} undated (kept)' if undated else ''}")
        if not (apply and old):
            continue

        deleted = 0
        for i in range(0, len(old), 100):
            batch = old[i:i + 100]
            try:
                raw = _req(url, key, "DELETE", f"/storage/v1/object/{BUCKET}",
                           json.dumps({"prefixes": batch}).encode())
            except urllib.error.HTTPError as e:
                summary["error"] = f"delete HTTP {e.code}: {e.read()[:160].decode('utf-8','replace')}"
                if verbose:
                    print(f"[PRUNE] {summary['error']}")
                break
            except Exception as e:
                summary["error"] = f"delete error: {e}"
                if verbose:
                    print(f"[PRUNE] {summary['error']}")
                break
            # 200 with [] means RLS filtered every row: the key can view/upload
            # but not delete. Never count that as success.
            try:
                removed = json.loads(raw) if raw else []
            except Exception:
                removed = []
            if not removed:
                summary["error"] = ("delete returned 200 but removed 0 objects - "
                                    "the key has no DELETE permission on this bucket")
                if verbose:
                    print(f"[PRUNE] {summary['error']}")
                break
            deleted += len(removed)
        summary["deleted"] += deleted
        if verbose and deleted:
            print(f"[PRUNE] [{prefix}] deleted {deleted}/{len(old)}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0, help="retention window (default 24)")
    ap.add_argument("--prefix", action="append", default=None,
                    help="storage prefix to prune (repeatable); default recordings+screenshots")
    ap.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    args = ap.parse_args()

    _load_env()
    if not os.environ.get("SUPABASE_URL"):
        print("ERROR: SUPABASE_URL is required (monitoring/.env or env vars).")
        return 1
    print(f"retention: keep objects newer than "
          f"{(datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()} ({args.hours}h)")
    print(f"mode: {'APPLY (deleting)' if args.apply else 'DRY RUN (nothing deleted)'}\n")

    summary = prune(hours=args.hours, prefixes=args.prefix or DEFAULT_PREFIXES, apply=args.apply)
    if summary.get("error"):
        print(f"\nERROR: {summary['error']}")
        return 2
    verb = "Deleted" if args.apply else "Would delete"
    would = sum(v["old"] for v in summary["per_prefix"].values())
    undated_note = f" (+{summary['undated']} undated)" if summary["undated"] else ""
    print(f"\n{verb}: {summary['deleted'] if args.apply else would} objects; "
          f"keeping {summary['kept']}{undated_note}")
    if not args.apply:
        print("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
