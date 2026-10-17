#!/usr/bin/env python3
"""
migrate_storage.py - copy the Supabase 'monitoring' bucket to a local folder.

Downloads every object through its public URL and writes it to
<out>/monitoring/<key>, which is the layout storage-shim.mjs serves. The bucket is
public, so no key is needed for the download - only for the listing.

Usage:
  python3 db/migrate_storage.py --out db/storage-export
  # then: scp -r db/storage-export/monitoring  gajab-win:C:/gajab/storage/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUCKET = "monitoring"


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


def list_prefix(url: str, key: str, prefix: str) -> list[dict]:
    out, offset = [], 0
    while True:
        body = json.dumps({"prefix": prefix, "limit": 1000, "offset": offset}).encode()
        req = urllib.request.Request(
            f"{url}/storage/v1/object/list/{BUCKET}", data=body,
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        items = json.loads(urllib.request.urlopen(req, timeout=120).read())
        if not isinstance(items, list) or not items:
            break
        out.extend(items)
        offset += len(items)
        if len(items) < 1000:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "db", "storage-export"))
    ap.add_argument("--env", default=os.path.join(REPO, "monitoring", ".env"))
    ap.add_argument("--prefix", action="append", default=None)
    args = ap.parse_args()

    env = {**load_env(args.env), **os.environ}
    url = env.get("SUPABASE_URL", "")
    key = env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_KEY") or ""
    if not url or not key:
        print("ERROR: SUPABASE_URL and a key are required.")
        return 1

    prefixes = args.prefix or ["recordings/", "screenshots/", ""]
    dest_root = os.path.join(args.out, BUCKET)
    os.makedirs(dest_root, exist_ok=True)

    total = bytes_ = skipped = 0
    for prefix in prefixes:
        items = list_prefix(url, key, prefix)
        print(f"[{prefix or '<root>'}] {len(items)} objects")
        for it in items:
            name = it.get("name")
            if not name:
                continue
            keypath = f"{prefix}{name}"
            dest = os.path.join(dest_root, keypath.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest) and os.path.getsize(dest) == ((it.get("metadata") or {}).get("size") or -1):
                skipped += 1
                continue
            src = f"{url}/storage/v1/object/public/{BUCKET}/{urllib.parse.quote(keypath)}"
            try:
                with urllib.request.urlopen(src, timeout=180) as r, open(dest, "wb") as f:
                    data = r.read()
                    f.write(data)
                total += 1
                bytes_ += len(data)
            except urllib.error.HTTPError as e:
                print(f"  FAIL {keypath}: HTTP {e.code}")
            except Exception as e:
                print(f"  FAIL {keypath}: {e}")
        print(f"  -> {total} downloaded so far ({bytes_/1024/1024:.1f} MB), {skipped} already present")

    print(f"\ndone: {total} objects, {bytes_/1024/1024:.1f} MB -> {dest_root}")
    print(f"\nCopy to the box:\n  scp -r {dest_root} gajab-win:C:/gajab/storage/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
