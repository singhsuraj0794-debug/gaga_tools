#!/usr/bin/env python3
"""Probe one CDP Chrome port and print exactly "ok" or "fail".

Used by windows-common.ps1's Test-CdpHealthy. A wedged Chrome still accepts TCP
and still answers /json/version, so the only faithful check is to do what the
scrapers do: attach with Playwright. Run one process per port so a driver crash
on a bad browser cannot poison the verdict for the healthy ones.

Deliberately prints nothing else: PowerShell treats anything that is not "ok" or
"fail" as "unknown" and leaves the browser alone.
"""
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("fail")
        return 1
    port = sys.argv[1].strip()
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("fail")
        return 1
    pw = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}", timeout=8000)
        browser.close()
        print("ok")
        return 0
    except Exception:
        print("fail")
        return 1
    finally:
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
