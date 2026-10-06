#!/usr/bin/env python3
"""
Create a pool of saved login sessions for the load test.

For each account it opens gajab.com sign-in, requests an OTP, asks you for the
code, and saves the resulting browser session to <out-dir>/sNN.json. Those files
are what `user_flow_test.py --auth-dir <out-dir>` rotates across concurrent users.

Usage:
    # log in 5 accounts (prompts for each phone number + OTP)
    python3 setup_login_pool.py --out-dir sessions --count 5

    # or supply the phone numbers up front
    python3 setup_login_pool.py --out-dir sessions --phones "9876543210,9123456789"

Notes:
  - Runs a visible browser; you type the OTP from each phone.
  - Works for production gajab.com. For staging use --base-url https://stg.gajab.com
  - Re-running skips accounts whose session file already exists.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_BASE = "https://gajab.com"


def login_one(browser, phone: str, out_path: Path, base_url: str) -> bool:
    print("\n" + "-" * 60)
    print(f"Account: {phone}")
    print("-" * 60)
    context = browser.new_context(
        viewport={"width": 430, "height": 932},
        is_mobile=True, has_touch=True,
        geolocation={"latitude": 19.4560, "longitude": 72.8054},
        locale="en-IN", timezone_id="Asia/Kolkata",
    )
    try:
        page = context.new_page()
        print("1. opening sign-in page...")
        page.goto(f"{base_url}/auth/signin", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector('input[type="tel"]', timeout=30000)
        time.sleep(1)

        print("2. entering phone...")
        page.locator('input[type="tel"]').fill(phone)
        time.sleep(0.5)

        print("3. accepting terms...")
        cb = page.locator('input[type="checkbox"]')
        if cb.count() > 0:
            try:
                cb.first.click()
            except Exception:
                pass
        time.sleep(0.5)

        print("4. requesting OTP...")
        page.locator('button:has-text("Request OTP")').first.click()
        otp = input(f"   enter OTP for {phone}: ").strip()

        print("5. entering OTP...")
        fields = page.locator("input[maxlength='1'][inputmode='numeric']")
        if fields.count() > 0 and fields.first.is_visible():
            fields.first.click()
            time.sleep(0.3)
            page.keyboard.type(otp, delay=0.15)
        time.sleep(2)
        submit = page.locator("button:has-text('Submit'), button:has-text('Verify'), button:has-text('Login')")
        if submit.count() > 0 and submit.first.is_enabled():
            submit.first.click()
        time.sleep(3)

        logged_in = "/auth/signin" not in page.url
        print(f"   url: {page.url}  (logged_in={logged_in})")
        if not logged_in:
            print("   login did not complete — skipping this account")
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(out_path))
        print(f"   saved -> {out_path}")
        return True
    finally:
        try:
            context.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a pool of gajab login sessions")
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "sessions")
    ap.add_argument("--count", type=int, default=0, help="How many accounts to log in (prompts for phones)")
    ap.add_argument("--phones", default="", help="Comma-separated phone numbers")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help="gajab.com or https://stg.gajab.com")
    args = ap.parse_args()

    phones = [p.strip() for p in args.phones.split(",") if p.strip()]
    if not phones and args.count:
        for i in range(args.count):
            p = input(f"phone number for account {i + 1}/{args.count}: ").strip()
            if p:
                phones.append(p)
    if not phones:
        ap.error("provide --phones or --count")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            for i, phone in enumerate(phones, start=1):
                out = args.out_dir / f"s{i:02d}.json"
                if out.exists():
                    print(f"[{i}/{len(phones)}] {out.name} exists — skipping ({phone})")
                    saved += 1
                    continue
                try:
                    if login_one(browser, phone, out, args.base_url):
                        saved += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"   failed for {phone}: {type(exc).__name__}: {str(exc).splitlines()[0]}")
        finally:
            browser.close()

    print("\n" + "=" * 60)
    print(f"Sessions saved to {args.out_dir}: {saved}/{len(phones)}")
    print(f"Run the flow with:  --auth-dir {args.out_dir}")
    print("=" * 60)
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
