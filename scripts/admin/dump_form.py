#!/usr/bin/env python3
"""Quick dump of form elements on the add-category page."""
from __future__ import annotations
import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
SESSION_FILE = SCRIPT_DIR / ".admin_session.json"
ADMIN_BASE = "https://admin.gajab.com"

with open(SESSION_FILE) as f:
    session = json.load(f)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(storage_state=session, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(15000)

    page.goto(f"{ADMIN_BASE}/#/vendors/product-config/request-categories/add",
              wait_until="networkidle", timeout=30000)
    time.sleep(5)

    # Check if login page
    if page.locator("input[placeholder='User Name']").count() > 0:
        print("ERROR: On login page - session expired")
        browser.close()
        exit(1)

    print("=== VISIBLE INPUTS ===")
    for i, inp in enumerate(page.locator("input").all()):
        try:
            if not inp.is_visible(): continue
            attrs = {}
            for attr in ["type","name","placeholder","id","class","role"]:
                v = inp.get_attribute(attr)
                if v: attrs[attr] = v[:80]
            print(f"  [{i}] {attrs}")
        except: pass

    print("\n=== VISIBLE SELECTS ===")
    for i, sel in enumerate(page.locator("select").all()):
        try:
            if not sel.is_visible(): continue
            attrs = {}
            for attr in ["name","id","class"]:
                v = sel.get_attribute(attr)
                if v: attrs[attr] = v[:80]
            opts = [o.text_content().strip() for o in sel.locator("option").all()[:5]]
            print(f"  [{i}] {attrs} options={opts}")
        except: pass

    print("\n=== VISIBLE TEXTAREAS ===")
    for i, ta in enumerate(page.locator("textarea").all()):
        try:
            if not ta.is_visible(): continue
            attrs = {}
            for attr in ["name","placeholder","id","class"]:
                v = ta.get_attribute(attr)
                if v: attrs[attr] = v[:80]
            print(f"  [{i}] {attrs}")
        except: pass

    print("\n=== COMBOBOX (ng-select) ===")
    for i, cb in enumerate(page.locator("[role='combobox']").all()):
        try:
            if not cb.is_visible(): continue
            # Find nearby label
            label_text = ""
            try:
                parent = cb.locator("xpath=ancestor::div[contains(@class,'col')]").first
                lbl = parent.locator("label").first
                label_text = lbl.text_content().strip()
            except: pass
            # Find inner input
            inner = cb.locator("input").first
            inner_type = inner.get_attribute("type") or ""
            inner_role = inner.get_attribute("role") or ""
            print(f"  [{i}] label='{label_text}' inner_type={inner_type} inner_role={inner_role}")
        except: pass

    print("\n=== VISIBLE LABELS ===")
    for i, lbl in enumerate(page.locator("label").all()):
        try:
            if not lbl.is_visible(): continue
            txt = lbl.text_content().strip()
            if txt: print(f"  [{i}] '{txt}'")
        except: pass

    print("\n=== VISIBLE BUTTONS ===")
    for i, btn in enumerate(page.locator("button").all()):
        try:
            if not btn.is_visible(): continue
            txt = btn.text_content().strip()
            if txt: print(f"  [{i}] '{txt}'")
        except: pass

    print("\n=== DIVS with % prefix (for commission/bargain) ===")
    for i, div in enumerate(page.locator("div:has-text('%')").all()):
        try:
            if not div.is_visible(): continue
            txt = div.text_content().strip()[:60]
            if "%" in txt and len(txt) < 20: print(f"  [{i}] '{txt}'")
        except: pass

    browser.close()
