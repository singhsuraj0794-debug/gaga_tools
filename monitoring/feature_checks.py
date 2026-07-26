from __future__ import annotations
import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import VIEWPORT, PAGE_TIMEOUT, NAV_TIMEOUT


def log(msg):
    print(f"[FEATURES] {msg}", flush=True)


def check_elements(page, checks: list[dict]) -> list[dict]:
    results = []
    for check in checks:
        t0 = time.time()
        status = "pass"
        error = None
        try:
            if check.get("type") == "visible":
                loc = page.locator(check["selector"])
                found = loc.count() > 0
                visible = found and loc.first.is_visible(timeout=3000) if found else False
                if not visible:
                    status = "fail"
                    error = f"Element not visible (count={loc.count()})"
            elif check.get("type") == "count":
                loc = page.locator(check["selector"])
                count = loc.count()
                min_count = check.get("min", 1)
                if count < min_count:
                    status = "fail"
                    error = f"Found {count}, expected >= {min_count}"
            elif check.get("type") == "text":
                content = page.text_content(check["selector"])
                expected = check.get("contains", "")
                if expected and (not content or expected not in content):
                    status = "fail"
                    error = f"Text '{expected}' not found in '{content}'"
            elif check.get("type") == "attribute":
                attr = page.locator(check["selector"]).get_attribute(check["attribute"])
                expected = check.get("value")
                if expected and attr != expected:
                    status = "fail"
                    error = f"Attribute {check['attribute']}='{attr}', expected '{expected}'"
            elif check.get("type") == "response":
                status = "pass"
        except PWTimeout:
            status = "fail"
            error = "Timeout waiting for element"
        except Exception as e:
            status = "fail"
            error = str(e)[:100]

        duration = int((time.time() - t0) * 1000)
        results.append({
            "check": check.get("name", check.get("selector", "unknown")),
            "status": status,
            "duration_ms": duration,
            "error": error,
        })
    return results


def run_feature_checks() -> list[dict]:
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT, is_mobile=True, has_touch=True, locale="en-IN", timezone_id="Asia/Kolkata")
        page = context.new_page()

        try:
            # ── Home page features ──
            log("Loading home page for feature checks")
            page.goto("https://gajab.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            time.sleep(2)

            home_checks = [
                {"name": "site_logo", "type": "visible", "selector": "img[src*='logo'], a[href='/'] img"},
                {"name": "search_icon", "type": "visible", "selector": "img[src*='search'], [placeholder*='Search'], input[type='search']"},
                {"name": "category_nav", "type": "count", "selector": "nav a, [class*='category'], a[href*='/product-list/']", "min": 3},
                {"name": "hero_banner", "type": "visible", "selector": "[class*='banner'] img, [class*='carousel'] img, section img[src*='banner']"},
                {"name": "product_grid", "type": "count", "selector": "a[href*='/product-detail/']", "min": 4},
                {"name": "most_bargained_section", "type": "visible", "selector": "[class*='bargain'], [class*='Bargain'], section:has-text('Bargained')"},
                {"name": "trending_section", "type": "visible", "selector": "section:has-text('Trending'), [class*='trending']"},
                {"name": "footer_links", "type": "count", "selector": "footer a", "min": 3},
                {"name": "login_button", "type": "visible", "selector": "a[href*='signin'], a[href*='login'], button:has-text('Log in')"},
            ]
            log(f"Running {len(home_checks)} home page feature checks")
            for r in check_elements(page, home_checks):
                results.append({**r, "page": "home", "category": "feature"})
                icon = "✅" if r["status"] == "pass" else "❌"
                log(f"  {icon} home/{r['check']}: {r['status']} ({r['duration_ms']}ms) {r.get('error', '')}")

            # ── Category page features ──
            log("Loading category page for feature checks")
            page.goto("https://gajab.com/product-list/all", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            time.sleep(1)

            category_checks = [
                {"name": "product_cards", "type": "count", "selector": "a[href*='/product-detail/']", "min": 6},
                {"name": "filter_panel", "type": "visible", "selector": "[class*='filter'], button:has-text('Filter'), [class*='Filter']"},
                {"name": "price_filter", "type": "visible", "selector": "text=Price, [class*='price'], input[type='range']"},
                {"name": "sort_dropdown", "type": "visible", "selector": "select, button:has-text('Relevance'), [class*='sort']"},
                {"name": "pagination", "type": "visible", "selector": "a[href*='offset'], [class*='pagination'], button:has-text('Load More')"},
            ]
            log(f"Running {len(category_checks)} category page feature checks")
            for r in check_elements(page, category_checks):
                results.append({**r, "page": "category", "category": "feature"})
                icon = "✅" if r["status"] == "pass" else "❌"
                log(f"  {icon} category/{r['check']}: {r['status']} ({r['duration_ms']}ms) {r.get('error', '')}")

            # ── Product detail features ──
            product_url = "https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914"
            log("Loading product detail page for feature checks")
            page.goto(product_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
            time.sleep(1)

            pdp_checks = [
                {"name": "product_title", "type": "visible", "selector": "h1, [class*='title'], [class*='product-name']"},
                {"name": "product_image", "type": "visible", "selector": "img[src*='resize.gajab.com'], img[alt*='product'], [class*='gallery'] img"},
                {"name": "price_display", "type": "visible", "selector": "#varient-price, [class*='price'], [class*='Price']"},
                {"name": "start_bargaining_btn", "type": "visible", "selector": "button:has-text('Start Bargaining'), #varient-price button"},
                {"name": "asking_price", "type": "visible", "selector": "text=Asking price, text=₹, [class*='asking']"},
                {"name": "ratings_section", "type": "visible", "selector": "[class*='rating'], img[alt*='rating'], text=ratings"},
            ]
            log(f"Running {len(pdp_checks)} product detail feature checks")
            for r in check_elements(page, pdp_checks):
                results.append({**r, "page": "product_detail", "category": "feature"})
                icon = "✅" if r["status"] == "pass" else "❌"
                log(f"  {icon} pdp/{r['check']}: {r['status']} ({r['duration_ms']}ms) {r.get('error', '')}")

            log("All feature checks completed")

        except Exception as e:
            log(f"Feature check error: {e}")
            results.append({"page": "feature_checks", "check": "overall", "status": "fail", "duration_ms": 0, "error": str(e)[:100], "category": "feature"})
        finally:
            context.close()
            browser.close()

    return results
