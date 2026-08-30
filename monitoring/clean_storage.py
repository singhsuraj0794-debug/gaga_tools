#!/usr/bin/env python3
"""
clean_storage.py — Delete ALL recordings (and optionally screenshots) from the
Supabase 'monitoring' storage bucket to free the exceeded storage quota.

Usage:
  SUPABASE_SERVICE_KEY=<service_role_key> python3 clean_storage.py [--screenshots]

Get the service_role key from:
  Supabase Dashboard → Project Settings → API → service_role secret
"""
import json
import os
import sys
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://okxyskmjsmtykblrtmyi.supabase.co")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

BUCKET = "monitoring"
PREFIXES = ["recordings"]
if "--screenshots" in sys.argv:
    PREFIXES.append("screenshots")


def _req(method: str, path: str, body: bytes = None) -> dict:
    url = f"{SUPABASE_URL}{path}"
    req = urllib.request.Request(url, data=body, method=method, headers={
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main():
    if not SERVICE_KEY:
        print("ERROR: set SUPABASE_SERVICE_KEY (the service_role key) first.")
        sys.exit(1)

    total_deleted = 0
    for prefix in PREFIXES:
        # List files
        data = _req("GET", f"/storage/v1/object/list/{BUCKET}?prefix={prefix}&limit=200")
        items = data if isinstance(data, list) else data.get("data", [])
        names = [f.get("name") for f in items if f.get("name")]
        print(f"[{prefix}] found {len(names)} files")

        # Delete in batches of 1000 (Supabase remove endpoint accepts a list)
        for i in range(0, len(names), 100):
            batch = [f"{prefix}/{n}" for n in names[i:i+100]]
            try:
                _req("DELETE", f"/storage/v1/object/{BUCKET}", json.dumps({"prefixes": batch}).encode())
                total_deleted += len(batch)
                print(f"  deleted {len(batch)} files")
            except Exception as e:
                print(f"  delete error: {e}")

    print(f"\nDone. Total files deleted: {total_deleted}")


if __name__ == "__main__":
    main()
