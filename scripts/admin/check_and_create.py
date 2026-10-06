#!/usr/bin/env python3
"""
Read Excel L1-L4 hierarchy, check admin API for existing categories, create only missing ones.
"""
from __future__ import annotations
import json, os, sys, time, traceback, openpyxl, urllib.request
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")

ADMIN_BASE = os.getenv("ADMIN_BASE_URL", "https://admin.gajab.com")
SESSION_FILE = SCRIPT_DIR / ".admin_session.json"
TOKEN_FILE = SCRIPT_DIR / ".admin_token"
EXCEL_FILE = Path("/Users/gajabmarketing/Downloads/L1 to L4 - 10 Trial (1).xlsx")
DELAY = 2.0
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

def log(level: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def screenshot(page, name: str):
    path = SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path))
        log("SCREENSHOT", str(path))
    except:
        pass

def is_on_login_page(page) -> bool:
    try:
        return page.locator("input[placeholder='User Name']").is_visible(timeout=2000)
    except:
        return False

def manual_login(page) -> bool:
    page.goto(ADMIN_BASE, wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    if not is_on_login_page(page):
        log("INFO", "Already logged in!")
        return True
    log("INFO", "=" * 60)
    log("INFO", "  BROWSER OPENED — Please log in manually")
    log("INFO", "  Script auto-detects when login completes.")
    log("INFO", "=" * 60)
    for i in range(150):
        time.sleep(2)
        if not is_on_login_page(page):
            log("INFO", f"Login detected after {i*2}s!")
            time.sleep(3)
            return True
        if i > 0 and i % 15 == 0:
            log("INFO", f"  Waiting for login... ({i*2}s)")
    log("ERROR", "Login timeout")
    return False

def save_session(context):
    with open(SESSION_FILE, "w") as f:
        json.dump(context.storage_state(), f)

def load_session():
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None

def get_existing_names(token: str) -> set:
    url = "https://gatewayservice.gajab.com/product/api/category?limit=500&offset=0&keyword=&status=&name="
    req = urllib.request.Request(url)
    req.add_header("Authorization", token)
    req.add_header("Origin", "https://admin.gajab.com")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        cats = data.get("data", data.get("results", []))
        return set(c.get("name", "") for c in cats if c.get("name"))

def fetch_category_items(token: str) -> list:
    """Fetch full category objects (categoryId, parentInt, sortOrder, levels, name)."""
    url = "https://gatewayservice.gajab.com/product/api/category?limit=500&offset=0&keyword=&status=&name="
    req = urllib.request.Request(url)
    req.add_header("Authorization", token)
    req.add_header("Origin", "https://admin.gajab.com")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        return data.get("data", data.get("results", []))

def next_sort_order(items: list, parent_path: str) -> int:
    """Sort order must be unique per parent. Returns max sibling sortOrder + 1."""
    if not parent_path:
        # Top-level (L1) categories
        siblings = [i.get("sortOrder", 0) for i in items if not i.get("parentInt")]
        return (max(siblings) + 1) if siblings else 1
    by_path = {i.get("levels", ""): i for i in items}
    parent = by_path.get(parent_path)
    if not parent:
        return 1
    parent_id = parent.get("categoryId")
    siblings = [i.get("sortOrder", 0) for i in items if i.get("parentInt") == parent_id]
    return (max(siblings) + 1) if siblings else 1

def path_exists(items: list, path: str) -> bool:
    return any(i.get("levels", "") == path for i in items)

def refresh_token(page) -> str:
    """Navigate to admin and extract the auth token from requests."""
    token = [None]
    def on_request(request):
        if "gatewayservice" in request.url:
            auth = request.headers.get("authorization", "")
            if auth:
                token[0] = auth
    page.on("request", on_request)
    page.goto(f"{ADMIN_BASE}/#/vendors/product-config/request-categories",
              wait_until="networkidle", timeout=30000)
    time.sleep(5)
    if token[0]:
        with open(TOKEN_FILE, "w") as f:
            f.write(token[0])
        return token[0]
    return None

def fill_input(page, placeholder: str, value: str):
    inp = page.locator(f"input[placeholder='{placeholder}']").first
    if inp.is_visible(timeout=3000):
        inp.click(force=True)
        inp.fill(str(value))
        log("INFO", f"    {placeholder} = {value}")
        return True
    return False

def select_ng_combobox(page, label_text: str, search_term: str) -> bool:
    log("INFO", f"    Selecting '{search_term}' in {label_text}...")
    try:
        label = page.locator(f"label:has-text('{label_text}')").first
        if not label.is_visible(timeout=5000):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            if not label.is_visible(timeout=3000):
                log("WARN", f"    Label '{label_text}' not found even after scroll")
                screenshot(page, f"missing_label_{label_text.replace(' ', '_')}")
                return False
        label.scroll_into_view_if_needed()
        time.sleep(0.5)
        container = label.locator("xpath=ancestor::div[contains(@class,'col')]").first
        combobox = container.locator("[role='combobox']").first
        if not combobox.is_visible(timeout=3000):
            log("WARN", f"    Combobox for '{label_text}' not found")
            return False
        combobox.click(force=True)
        time.sleep(1.5)
        page.keyboard.type(search_term, delay=50)
        time.sleep(1.5)
        # Click the matching option instead of pressing Enter (Enter submits the form)
        option = page.locator(".ng-option").filter(has_text=search_term).first
        if option.is_visible(timeout=3000):
            option.click()
            time.sleep(1)
        else:
            # Fallback: press Escape to close dropdown without submitting
            page.keyboard.press("Escape")
            time.sleep(0.5)
            log("WARN", f"    Option '{search_term}' not found in dropdown")
            return False
        log("INFO", f"    ✓ Selected '{search_term}'")
        return True
    except Exception as e:
        log("WARN", f"    Combobox error: {e}")
        return False

def select_parent(page, parent_path: str) -> bool:
    """Select parent by matching the FULL path exactly.

    Parent options are full paths like 'Beauty & Health Care > Health Care'.
    Filtering by just 'Health Care' also matches 'Beauty & Health Care > Health & Beauty > Bath and Spa'
    (because that string contains 'Health Care'), so we must match the exact full path.
    """
    log("INFO", f"    Selecting parent path '{parent_path}'...")
    try:
        label = page.locator("label:has-text('Parent')").first
        if not label.is_visible(timeout=5000):
            log("WARN", "    Parent label not found")
            return False
        label.scroll_into_view_if_needed()
        time.sleep(0.5)
        container = label.locator("xpath=ancestor::div[contains(@class,'col')]").first
        combobox = container.locator("[role='combobox']").first
        if not combobox.is_visible(timeout=3000):
            log("WARN", "    Parent combobox not found")
            return False
        combobox.click(force=True)
        time.sleep(1.5)
        # Type the FULL path — typing just the last segment (e.g. "Health Care") also matches
        # "Beauty & Health Care > Health & Beauty > Bath and Spa" because it contains "Health Care".
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(parent_path, delay=30)
        time.sleep(1.5)

        # Find the option whose text EXACTLY matches the full parent path
        options = page.locator(".ng-option")
        count = options.count()
        matched = None
        available = []
        for i in range(count):
            opt_text = options.nth(i).text_content().strip()
            available.append(opt_text)
            if opt_text == parent_path:
                matched = options.nth(i)
                break

        if matched is None:
            page.keyboard.press("Escape")
            time.sleep(0.5)
            log("WARN", f"    Exact parent '{parent_path}' not found. Available: {available[:15]}")
            return False

        matched.click()
        time.sleep(1)
        log("INFO", f"    ✓ Selected parent '{parent_path}'")
        return True
    except Exception as e:
        log("WARN", f"    Parent selection error: {e}")
        return False

def map_dropdown_text(field: str, value: str) -> str:
    """Map Excel text to actual admin panel dropdown option text."""
    mappings = {
        "return_policy": {
            "No Return": "No Return Policy",
            "Return & Refund": "Return and Refund",
        },
        "brand_documents": {
            "Trademark Certificate": "Trademark certificate",
            "Brand Approval Letter": "Brand Authorization Letter",
        },
    }
    return mappings.get(field, {}).get(value, value)

def create_category(page, cat: dict) -> bool:
    name = cat["name"]
    log("INFO", f"--- Creating: {name} (L{cat['level']}) ---")

    page.goto(f"{ADMIN_BASE}/#/vendors/product-config/request-categories/add",
              wait_until="networkidle", timeout=30000)
    time.sleep(3)

    if is_on_login_page(page):
        log("ERROR", "Session expired")
        return False

    # Name
    if not fill_input(page, "Category Name", name):
        log("ERROR", "  Category Name input not found")
        screenshot(page, f"no_name_{name.replace(' ', '_')}")
        return False

    # Description
    desc = cat.get("description", f"{name} - L{cat['level']}")
    try:
        page.locator("textarea[placeholder='Enter the Description']").first.fill(desc)
    except:
        pass

    # Parent — MUST be first because Shipping/Return/Brand Doc fields only appear after parent is selected
    parent_path = cat.get("parent_path", "")
    if parent_path:
        log("INFO", f"  Parent: {parent_path}")
        select_parent(page, parent_path)
        time.sleep(2)

    # Sort Order
    fill_input(page, " Sort Order", str(cat.get("sort_order", 1)))

    # Sales Commission
    fill_input(page, "Sales Commission", str(cat.get("sales_commission", 30)))

    # Status
    status = cat.get("status", "Enabled")
    log("INFO", f"  Status: {status}")
    try:
        page.locator("label:has-text('Status')").first.scroll_into_view_if_needed()
        time.sleep(0.5)
    except:
        pass
    select_ng_combobox(page, "Status", status)

    # Bargain Percentage
    fill_input(page, "Bargain Percentage", str(cat.get("bargain_percentage", 25)))

    # Shipping Policy
    shipping = cat.get("shipping_policy", "")
    if shipping:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        log("INFO", f"  Shipping Policy: {shipping}")
        select_ng_combobox(page, "Shipping Policy", shipping)

    # Return Policy — map Excel text to actual dropdown text
    return_policy = cat.get("return_policy", "")
    if return_policy:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        mapped_return = map_dropdown_text("return_policy", return_policy)
        log("INFO", f"  Return Policy: {return_policy} -> {mapped_return}")
        select_ng_combobox(page, "Return Policy", mapped_return)

    # Brand Documents — map Excel text to actual dropdown text
    brand_docs = cat.get("brand_documents", [])
    if brand_docs:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        mapped_docs = [map_dropdown_text("brand_documents", d) for d in brand_docs]
        log("INFO", f"  Brand Documents: {mapped_docs}")
        for doc in mapped_docs:
            select_ng_combobox(page, "Brand Document", doc)
            time.sleep(0.5)

    screenshot(page, f"prefill_{name.replace(' ', '_')}")

    # Image — switch to Image tab using semantic role (robust), upload, switch back
    image_path = cat.get("image", "")
    if image_path:
        full_image = SCRIPT_DIR / image_path
        if full_image.exists():
            try:
                image_tab = page.get_by_role("tab", name="Image")
                image_tab.wait_for(state="visible", timeout=15000)
                image_tab.click()
                time.sleep(2)
                fi = page.locator("input[type='file']")
                fi.first.wait_for(state="attached", timeout=15000)
                fi.first.set_input_files(str(full_image))
                time.sleep(4)
                log("INFO", f"    Image uploaded")
                general_tab = page.get_by_role("tab", name="General")
                general_tab.click()
                time.sleep(1)
            except Exception as e:
                log("WARN", f"    Image upload failed: {e}")

    # Save
    log("INFO", "  Saving...")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)
    save_btn = page.locator("button:has-text('Save')").last
    if save_btn.is_visible(timeout=5000):
        save_btn.click()
    else:
        log("ERROR", "  Save button not found")
        return False

    time.sleep(5)
    screenshot(page, f"after_save_{name.replace(' ', '_')}")

    current_url = page.url
    if "category-list" in current_url:
        log("INFO", f"  ✓ '{name}' created!")
        return True

    errors = page.locator(".text-danger, .error-text")
    for i in range(errors.count()):
        try:
            txt = errors.nth(i).text_content().strip()
            if txt:
                log("ERROR", f"  Validation: {txt}")
        except:
            pass

    if "add" in current_url:
        log("WARN", "  Still on add page — save may have failed")
        return False

    log("INFO", f"  '{name}' save completed")
    return True

def parse_excel():
    wb = openpyxl.load_workbook(EXCEL_FILE)
    ws = wb['Sheet1']
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    categories = {}

    for r in rows:
        l1 = str(r[0]).strip() if r[0] else None
        l2 = str(r[2]).strip() if r[2] else None
        l3 = str(r[4]).strip() if r[4] else None
        l4 = str(r[6]).strip() if r[6] else None
        if not l1: continue

        commission = r[8] or 30
        bargain = r[9] or 25
        ship = str(r[10]).strip() if r[10] else "Shipping Policy"
        ret = str(r[11]).strip() if r[11] else "No Return"
        brand = str(r[12]).strip() if r[12] else ""
        status = str(r[13]).strip() if r[13] else "Enabled"
        brand_docs = [d.strip() for d in brand.split(",") if d.strip()]

        if l1 and l1 not in categories:
            categories[l1] = {"name": l1, "level": 1, "parent_path": "", "sales_commission": commission, "bargain_percentage": bargain, "shipping_policy": ship, "return_policy": ret, "brand_documents": brand_docs, "status": status, "description": f"{l1} - L1", "image": "data/category_image.jpg"}
        if l2 and l2 not in categories:
            categories[l2] = {"name": l2, "level": 2, "parent_path": l1, "sales_commission": commission, "bargain_percentage": bargain, "shipping_policy": ship, "return_policy": ret, "brand_documents": brand_docs, "status": status, "description": f"{l2} - L2", "image": "data/category_image.jpg"}
        l3_path = f"{l1} > {l2}" if l1 and l2 else ""
        if l3 and l3 not in categories:
            categories[l3] = {"name": l3, "level": 3, "parent_path": l3_path, "sales_commission": commission, "bargain_percentage": bargain, "shipping_policy": ship, "return_policy": ret, "brand_documents": brand_docs, "status": status, "description": f"{l3} - L3", "image": "data/category_image.jpg"}
        l4_path = f"{l1} > {l2} > {l3}" if l1 and l2 and l3 else ""
        if l4 and l4 not in categories:
            categories[l4] = {"name": l4, "level": 4, "parent_path": l4_path, "sales_commission": commission, "bargain_percentage": bargain, "shipping_policy": ship, "return_policy": ret, "brand_documents": brand_docs, "status": status, "description": f"{l4} - L4", "image": "data/category_image.jpg"}

    return sorted(categories.values(), key=lambda x: (x["level"], x["name"]))

def main():
    log("INFO", "Parsing Excel...")
    all_cats = parse_excel()
    log("INFO", f"Found {len(all_cats)} unique categories in Excel")

    # Step 1: Get existing categories via API
    token = None
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()

    if token:
        try:
            existing = get_existing_names(token)
            log("INFO", f"Found {len(existing)} existing categories via saved token")
        except:
            log("WARN", "Saved token expired, will refresh...")
            token = None

    if not token:
        log("INFO", "Need to extract token from browser...")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            session = load_session()
            ctx = browser.new_context(storage_state=session, viewport={"width": 1440, "height": 900}) if session else browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(ADMIN_BASE, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            if is_on_login_page(page):
                if not manual_login(page):
                    browser.close()
                    sys.exit(1)
                save_session(ctx)
            token = refresh_token(page)
            browser.close()

        if not token:
            log("ERROR", "Could not get API token")
            sys.exit(1)
        existing = get_existing_names(token)
        log("INFO", f"Found {len(existing)} existing categories via fresh token")

    # Step 2: Determine what to create
    to_create = []
    for cat in all_cats:
        if cat["name"] in existing:
            log("INFO", f"  ✓ L{cat['level']} {cat['name']} — exists")
        else:
            log("INFO", f"  ✗ L{cat['level']} {cat['name']} — MISSING")
            to_create.append(cat)

    if not to_create:
        log("INFO", "\nAll categories already exist! Nothing to create.")
        return

    log("INFO", f"\nNeed to create {len(to_create)} categories")

    # Step 3: Create missing categories via Playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        session = load_session()
        ctx = browser.new_context(storage_state=session, viewport={"width": 1440, "height": 900}) if session else browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        page.goto(ADMIN_BASE, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        if is_on_login_page(page):
            if not manual_login(page):
                browser.close()
                sys.exit(1)
            save_session(ctx)

        results = []
        created_names = set()
        for cat in to_create:
            parent_path = cat.get("parent_path", "")

            # Re-fetch fresh so just-created parents are visible and sort orders are current
            try:
                items = fetch_category_items(token)
            except Exception as e:
                log("WARN", f"  Could not refresh categories: {e}")
                items = []

            # Check parent exists
            if parent_path:
                parent_name = parent_path.split(">")[-1].strip()
                if not path_exists(items, parent_path) and parent_name not in created_names:
                    log("ERROR", f"  Parent '{parent_name}' missing — skipping {cat['name']}")
                    results.append({"name": cat["name"], "success": False, "reason": f"parent '{parent_name}' missing"})
                    continue

            # Sort order must be unique per parent
            cat["sort_order"] = next_sort_order(items, parent_path)
            log("INFO", f"  Sort Order for {cat['name']}: {cat['sort_order']}")

            try:
                ok = create_category(page, cat)
                results.append({"name": cat["name"], "success": ok})
                if ok:
                    created_names.add(cat["name"])
                    time.sleep(DELAY)
                else:
                    log("ERROR", f"  ✗ {cat['name']} failed — continuing with remaining categories")
            except Exception as e:
                log("ERROR", f"Failed: {cat['name']}: {e}")
                traceback.print_exc()
                screenshot(page, f"error_{cat['name'].replace(' ', '_')}")
                results.append({"name": cat["name"], "success": False})

        screenshot(page, "final")
        browser.close()

    log("INFO", "=" * 50)
    created = sum(1 for r in results if r["success"])
    for r in results:
        s = "PASS" if r["success"] else "FAIL"
        reason = f" ({r.get('reason', '')})" if r.get("reason") else ""
        log("INFO", f"  [{s}] {r['name']}{reason}")
    log("INFO", f"  {created}/{len(results)} created")

if __name__ == "__main__":
    main()
