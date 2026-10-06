#!/usr/bin/env python3
"""
Playwright automation: login → create categories on admin.gajab.com
Handles all required fields including Status, Return Policy, Brand Document.
"""
from __future__ import annotations
import json, os, sys, time, traceback
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")

ADMIN_BASE = os.getenv("ADMIN_BASE_URL", "https://admin.gajab.com")
CATEGORIES_FILE = SCRIPT_DIR / "categories.json"
SESSION_FILE = SCRIPT_DIR / ".admin_session.json"
DELAY = float(os.getenv("DELAY", "3"))
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

def log(level: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def screenshot(page, name: str):
    path = SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"
    page.screenshot(path=str(path))
    log("SCREENSHOT", str(path))

def load_categories():
    with open(CATEGORIES_FILE) as f:
        return json.load(f)

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
    log("INFO", "")
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
    log("INFO", "Session saved")

def load_session():
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None

def fill_input(page, placeholder: str, value: str):
    """Fill an input by its placeholder text."""
    inp = page.locator(f"input[placeholder='{placeholder}']").first
    if inp.is_visible(timeout=3000):
        inp.click(force=True)
        inp.fill(str(value))
        log("INFO", f"    {placeholder} = {value}")
        return True
    return False

def select_ng_combobox(page, label_text: str, search_term: str) -> bool:
    """Select from an ng-select combobox by its label using keyboard."""
    log("INFO", f"    Selecting '{search_term}' in {label_text}...")
    try:
        label = page.locator(f"label:has-text('{label_text}')").first
        if not label.is_visible(timeout=3000):
            log("WARN", f"    Label '{label_text}' not found")
            return False

        container = label.locator("xpath=ancestor::div[contains(@class,'col')]").first
        combobox = container.locator("[role='combobox']").first

        if not combobox.is_visible(timeout=3000):
            log("WARN", f"    Combobox for '{label_text}' not found")
            return False

        # Click to open
        combobox.click(force=True)
        time.sleep(1.5)

        # Try keyboard: type to filter, then Enter
        page.keyboard.type(search_term, delay=50)
        time.sleep(1.5)
        page.keyboard.press("Enter")
        time.sleep(0.5)

        # Check if selection took effect
        val = combobox.text_content().strip()
        if search_term in val:
            log("INFO", f"    ✓ Selected '{search_term}' (keyboard)")
            return True

        # Fallback: try clicking option in the dropdown panel
        panel = page.locator(".ng-dropdown-panel, .cdk-overlay-pane").first
        if panel.is_visible(timeout=2000):
            options = panel.locator("[role='option']")
            count = options.count()
            for i in range(count):
                opt = options.nth(i)
                if search_term in (opt.text_content() or ""):
                    opt.click(force=True)
                    log("INFO", f"    ✓ Selected '{search_term}' (panel click)")
                    time.sleep(0.5)
                    return True

        # Fallback: try page-wide option search
        for sel in [
            f"[role='option']:has-text('{search_term}')",
            f"div[class*='option']:has-text('{search_term}')",
        ]:
            try:
                opt = page.locator(sel).first
                if opt.is_visible(timeout=2000):
                    opt.click(force=True)
                    log("INFO", f"    ✓ Selected '{search_term}' (global click)")
                    time.sleep(0.5)
                    return True
            except:
                continue

        log("WARN", f"    Could not find option '{search_term}'")
        return False
    except Exception as e:
        log("WARN", f"    Combobox error: {e}")
        return False

def select_ng_combobox_by_index(page, label_text: str, index: int) -> bool:
    """Select from an ng-select combobox by clicking the option at the given index."""
    log("INFO", f"    Selecting option #{index} in {label_text}...")
    try:
        label = page.locator(f"label:has-text('{label_text}')").first
        container = label.locator("xpath=ancestor::div[contains(@class,'col')]").first
        combobox = container.locator("[role='combobox']").first
        combobox.click(force=True)
        time.sleep(1)
        opts = page.locator("[role='option']").all()
        if index < len(opts):
            opts[index].click()
            txt = opts[index].text_content().strip()
            log("INFO", f"    ✓ Selected '{txt}'")
            time.sleep(0.5)
            return True
    except Exception as e:
        log("WARN", f"    Index select error: {e}")
    return False

def select_native_select(page, label_text: str, option_text: str) -> bool:
    """Select from a native <select> dropdown."""
    try:
        label = page.locator(f"label:has-text('{label_text}')").first
        container = label.locator("xpath=ancestor::div[contains(@class,'col')]").first
        sel = container.locator("select").first
        if sel.is_visible(timeout=3000):
            sel.select_option(label=option_text)
            log("INFO", f"    ✓ Selected '{option_text}' in {label_text}")
            return True
    except:
        pass
    return False

def create_category(page, cat: dict) -> bool:
    name = cat["name"]
    log("INFO", f"--- Creating: {name} ---")

    page.goto(f"{ADMIN_BASE}/#/vendors/product-config/request-categories/add",
              wait_until="networkidle", timeout=30000)
    time.sleep(3)

    if is_on_login_page(page):
        log("ERROR", "Session expired")
        return False

    screenshot(page, f"add_{name.replace(' ', '_')}")

    # 1. Category Name
    log("INFO", f"  Name: {name}")
    if not fill_input(page, "Category Name", name):
        log("ERROR", "  Category Name input not found")
        screenshot(page, f"no_name_{name.replace(' ', '_')}")
        return False
    time.sleep(0.5)

    # 2. Description
    desc = cat.get("description", f"{name} - L{cat.get('level', '')}")
    log("INFO", f"  Description: {desc}")
    try:
        page.locator("textarea[placeholder='Enter the Description']").first.fill(desc)
    except:
        pass
    time.sleep(0.3)

    # 3. Image upload
    image_path = cat.get("image", "")
    if image_path:
        full_image = SCRIPT_DIR / image_path
        if full_image.exists():
            log("INFO", f"  Uploading image: {full_image.name}")
            try:
                page.locator("a:has-text('Image'), span:has-text('Image')").first.click()
                time.sleep(2)
                page.locator("input[type='file']").first.set_input_files(str(full_image))
                time.sleep(3)
                log("INFO", "    Image uploaded")
                page.locator("a:has-text('General'), span:has-text('General')").first.click()
                time.sleep(1)
            except Exception as e:
                log("WARN", f"    Image upload failed: {e}")

    # 4. Parent dropdown
    parent_path = cat.get("parent_path", "")
    if parent_path:
        parent_name = parent_path.split(">")[-1].strip()
        log("INFO", f"  Parent: {parent_name}")
        select_ng_combobox(page, "Parent", parent_name)
        time.sleep(1)

    # 5. Sort Order
    sort_order = cat.get("sort_order", 1)
    fill_input(page, " Sort Order", str(sort_order))
    time.sleep(0.3)

    # 6. Sales Commission
    commission = cat.get("sales_commission", 30)
    fill_input(page, "Sales Commission", str(commission))
    time.sleep(0.3)

    # 7. Status (ng-select combobox) — use keyboard approach
    status = cat.get("status", "Enabled")
    log("INFO", f"  Status: {status}")
    # Scroll to Status field first
    try:
        status_label = page.locator("label:has-text('Status')").first
        status_label.scroll_into_view_if_needed()
        time.sleep(0.5)
    except:
        pass
    # Use same keyboard-based selection as other ng-selects
    status_selected = select_ng_combobox(page, "Status", status)
    if not status_selected:
        log("WARN", "    Could not select Status via keyboard, trying JS approach...")
        try:
            # Fallback: use Angular's internal API
            result = page.evaluate("""(statusValue) => {
                // Find the Status ng-select element
                const selects = document.querySelectorAll('ng-select');
                for (const sel of selects) {
                    const container = sel.closest('.col, .form-group, [class*="col"]');
                    if (!container) continue;
                    const text = container.textContent || '';
                    if (text.includes('Status') && !text.includes('Shipping')) {
                        // Use Angular's component API
                        const ngEl = sel.__ngContext__ ? sel : null;
                        if (ngEl) {
                            // Try dispatching input events
                            const control = sel.querySelector('.ng-select-container');
                            if (control) control.click();
                        }
                        return 'attempted';
                    }
                }
                return 'not_found';
            }""")
            log("INFO", f"    Status JS fallback: {result}")
            if result == 'attempted':
                time.sleep(1)
                # Now try to find and click option with proper events
                page.evaluate("""(statusValue) => {
                    const options = document.querySelectorAll('[role="option"]');
                    for (const opt of options) {
                        if (opt.textContent && opt.textContent.trim() === statusValue) {
                            // Dispatch mousedown, mouseup, click sequence
                            opt.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            opt.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            opt.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return 'dispatched';
                        }
                    }
                    return 'no_option';
                }""", status)
                time.sleep(0.5)
                status_selected = True  # Assume it worked with event dispatch
        except Exception as e:
            log("WARN", f"    Status JS fallback failed: {e}")
    if not status_selected:
        log("WARN", "    Could not select Status")
    time.sleep(0.5)

    # 8. Bargain Percentage
    bargain = cat.get("bargain_percentage", 25)
    fill_input(page, "Bargain Percentage", str(bargain))
    time.sleep(0.3)

    # 9. Shipping Policy (may already have default)
    shipping = cat.get("shipping_policy", "")
    if shipping:
        log("INFO", f"  Shipping Policy: {shipping}")
        select_ng_combobox(page, "Shipping Policy", shipping)
        time.sleep(0.5)

    # 10. Return Policy (required)
    return_policy = cat.get("return_policy", "")
    if return_policy:
        log("INFO", f"  Return Policy: {return_policy}")
        if not select_ng_combobox(page, "Return Policy", return_policy):
            # Try native select fallback
            select_native_select(page, "Return Policy", return_policy)
        time.sleep(0.5)

    # 11. Brand Document (multi-select, required)
    brand_docs = cat.get("brand_documents", [])
    if brand_docs:
        log("INFO", f"  Brand Documents: {brand_docs}")
        for doc in brand_docs:
            select_ng_combobox(page, "Brand Document", doc)
            time.sleep(0.5)
            # Press Escape to close dropdown without clearing selections
            page.keyboard.press("Escape")
            time.sleep(0.3)

    screenshot(page, f"prefill_{name.replace(' ', '_')}")

    # 12. Scroll down and click Save
    log("INFO", "  Saving...")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)

    save_btn = page.locator("button:has-text('Save')").last
    if save_btn.is_visible(timeout=5000):
        save_btn.click()
        log("INFO", "    Clicked Save")
    else:
        log("ERROR", "  Save button not found")
        screenshot(page, f"no_save_{name.replace(' ', '_')}")
        return False

    time.sleep(5)
    screenshot(page, f"after_save_{name.replace(' ', '_')}")

    # Check for validation errors
    errors = page.locator(".text-danger, .error-text")
    error_texts = []
    for i in range(errors.count()):
        try:
            txt = errors.nth(i).text_content().strip()
            if txt and "required" in txt.lower():
                error_texts.append(txt)
        except:
            pass

    if error_texts:
        log("ERROR", f"  Validation errors: {error_texts}")
        return False

    # Check URL
    current_url = page.url
    if "category-list" in current_url:
        log("INFO", f"  ✓ '{name}' created! (redirected to list)")
        return True

    # Check for success toast
    try:
        toast = page.locator(".toast-success")
        if toast.count() > 0 and toast.first.is_visible(timeout=2000):
            log("INFO", f"  ✓ '{name}' created! (toast)")
            return True
    except:
        pass

    if "add" in current_url:
        log("WARN", "  Still on add page — save may have failed")
        return False

    log("INFO", f"  '{name}' save completed (verify in admin)")
    return True

def main():
    categories = load_categories()
    log("INFO", f"Loaded {len(categories)} categories to create")
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        session = load_session()
        if session:
            try:
                context = browser.new_context(storage_state=session, viewport={"width": 1440, "height": 900})
            except:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
        else:
            context = browser.new_context(viewport={"width": 1440, "height": 900})

        page = context.new_page()
        page.set_default_timeout(15000)

        page.goto(ADMIN_BASE, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        if is_on_login_page(page):
            if not manual_login(page):
                browser.close()
                sys.exit(1)
            save_session(context)
        else:
            log("INFO", "Already logged in")
            save_session(context)

        for cat in categories:
            # Skip parent existence check — form will show error if parent missing
            parent_path = cat.get("parent_path", "")
            if parent_path:
                parent_name = parent_path.split(">")[-1].strip()
                # Check if parent was created in this run
                parent_created = any(
                    r["success"] and r["name"] == parent_name
                    for r in results
                )
                if not parent_created and cat.get("level", 0) > 2:
                    log("WARN", f"  Parent '{parent_name}' not created in this run — proceeding anyway (will fail in form if missing)")

            try:
                ok = create_category(page, cat)
                results.append({"name": cat["name"], "success": ok})
                if ok:
                    log("INFO", f"Waiting {DELAY}s...")
                    time.sleep(DELAY)
                else:
                    log("ERROR", f"  ✗ {cat['name']} creation failed — aborting dependent categories")
                    # If this is a parent category, skip remaining categories that depend on it
                    if cat.get("level", 0) < 4:
                        log("WARN", f"  Skipping remaining L4+ categories (parent failed)")
                        break
            except Exception as e:
                log("ERROR", f"Failed: {cat['name']}: {e}")
                traceback.print_exc()
                screenshot(page, f"error_{cat['name'].replace(' ', '_')}")
                results.append({"name": cat["name"], "success": False})
                if cat.get("level", 0) < 4:
                    log("WARN", f"  Skipping remaining L4+ categories (parent failed)")
                    break

        screenshot(page, "final")
        browser.close()

    log("INFO", "=" * 50)
    created = sum(1 for r in results if r["success"])
    for r in results:
        s = "PASS" if r["success"] else "FAIL"
        log("INFO", f"  [{s}] {r['name']}")
    log("INFO", f"  {created}/{len(results)} created")

if __name__ == "__main__":
    main()
