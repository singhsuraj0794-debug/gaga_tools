#!/usr/bin/env python3
"""Open the admin panel for manual login and save the session."""
import json, time, sys
from pathlib import Path
from dotenv import load_dotenv
import os
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")
ADMIN_BASE = os.getenv("ADMIN_BASE_URL", "https://admin.gajab.com")
SESSION_FILE = SCRIPT_DIR / ".admin_session.json"
TOKEN_FILE = SCRIPT_DIR / ".admin_token"


def is_on_login_page(page) -> bool:
    try:
        return page.locator("input[placeholder='User Name']").is_visible(timeout=2000)
    except Exception:
        return False


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    token = {"v": None}

    def on_request(request):
        auth = request.headers.get("authorization", "")
        if auth and "Bearer" in auth:
            token["v"] = auth

    page.on("request", on_request)
    page.goto(ADMIN_BASE, wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    if is_on_login_page(page):
        print("=" * 60)
        print("  Please log in in the opened browser window...")
        print("=" * 60)
        for i in range(300):
            time.sleep(2)
            if not is_on_login_page(page):
                print(f"Login detected after {i * 2}s")
                time.sleep(3)
                break
        else:
            print("Login timeout")
            browser.close()
            sys.exit(1)

    # Visit an admin page so an authenticated API request fires and we capture the token
    page.goto(f"{ADMIN_BASE}/#/vendors/product-config/request-categories/add",
              wait_until="networkidle", timeout=30000)
    time.sleep(3)

    with open(SESSION_FILE, "w") as f:
        json.dump(ctx.storage_state(), f)
    print(f"Session saved to {SESSION_FILE}")

    if token["v"]:
        TOKEN_FILE.write_text(token["v"])
        print(f"Token saved to {TOKEN_FILE}")
    else:
        print("WARNING: no bearer token captured")

    print(f"Current URL: {page.url}")
    browser.close()
