from __future__ import annotations
import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import VIEWPORT, PAGE_TIMEOUT, NAV_TIMEOUT


def log(msg):
    print(f"[FEATURES] {msg}", flush=True)


def _poll_element(page, selector: str, check_type: str, timeout_ms: int = 8000, poll_interval_ms: int = 500) -> dict:
    """
    Poll for an element with retry. Returns:
      { found: bool, visible: bool, in_dom: bool, count: int, error: str|None }
    Distinguishes "not in DOM" from "in DOM but not visible".
    """
    deadline = time.time() + timeout_ms / 1000
    last_count = 0
    last_error = None
    found_visible = False
    found_in_dom = False

    while time.time() < deadline:
        try:
            loc = page.locator(selector)
            count = loc.count()
            last_count = count

            if count > 0:
                found_in_dom = True
                # Scroll the first match into view before checking visibility
                try:
                    loc.first.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass

                if check_type == "visible":
                    # Check each match for visibility
                    for i in range(min(count, 10)):
                        try:
                            if loc.nth(i).is_visible(timeout=500):
                                found_visible = True
                                break
                        except Exception:
                            continue
                elif check_type == "count":
                    min_count = 1  # caller validates count externally
                    found_visible = count > 0
                else:
                    found_visible = count > 0

                if found_visible:
                    return {
                        "found": True, "visible": True, "in_dom": True,
                        "count": count, "error": None,
                    }
        except Exception as e:
            last_error = str(e)[:100]

        time.sleep(poll_interval_ms / 1000)

    # Timed out — report what we know
    return {
        "found": found_in_dom,
        "visible": False,
        "in_dom": found_in_dom,
        "count": last_count,
        "error": last_error or ("Element not visible" if found_in_dom else "Element not found in DOM"),
    }


def check_elements(page, checks: list[dict]) -> list[dict]:
    results = []
    for check in checks:
        t0 = time.time()
        status = "pass"
        error = None
        issue_type = "product"  # default: a real product/UI issue

        try:
            if check.get("type") == "visible":
                poll_timeout = check.get("poll_timeout_ms", 8000)
                result = _poll_element(page, check["selector"], "visible", timeout_ms=poll_timeout)
                if not result["visible"]:
                    status = "fail"
                    if not result["in_dom"]:
                        error = f"Element not in DOM (count=0) — selector may be wrong"
                        issue_type = "product"
                    else:
                        error = f"Element in DOM (count={result['count']}) but not visible — may be lazy-loaded or off-screen"
                        issue_type = "infra"  # timing/lazy-load issue, not a real bug
                match_count = result["count"]
            elif check.get("type") == "count":
                poll_timeout = check.get("poll_timeout_ms", 8000)
                result = _poll_element(page, check["selector"], "count", timeout_ms=poll_timeout)
                count = result["count"]
                min_count = check.get("min", 1)
                if count < min_count:
                    status = "fail"
                    error = f"Found {count}, expected >= {min_count}"
                    issue_type = "product" if count == 0 else "infra"
                match_count = count
            elif check.get("type") == "text_visible":
                # Robust alternative to brittle CSS classes: pass when ANY
                # visible element contains one of the given terms. The app's
                # class names are hashed/minified, so selectors like
                # [class*='asking'] or button:has-text('Relevance') matched
                # nothing even though the feature was clearly on screen.
                terms = check.get("terms") or [check.get("contains", "")]
                poll_timeout = check.get("poll_timeout_ms", 8000)
                # Optional: open a panel/menu before looking (e.g. Filters).
                if check.get("click_before"):
                    try:
                        cb = page.get_by_text(check["click_before"], exact=False).first
                        if cb.is_visible():
                            cb.click()
                            time.sleep(1.5)
                    except Exception:
                        pass
                found_term = None
                deadline = time.time() + poll_timeout / 1000
                # Optional CSS alternatives — some features render as images
                # (e.g. star glyphs) while their text lives in a display:none
                # container, so a text search alone reports a false failure.
                sel_alts = check.get("selectors") or []
                while time.time() < deadline and not found_term:
                    for sel in sel_alts:
                        try:
                            loc = page.locator(sel)
                            for i in range(min(loc.count(), 8)):
                                if loc.nth(i).is_visible():
                                    found_term = sel
                                    break
                        except Exception:
                            pass
                        if found_term:
                            break
                    if found_term:
                        break
                    for term in terms:
                        if not term:
                            continue
                        try:
                            loc = page.get_by_text(term, exact=False)
                            for i in range(min(loc.count(), 6)):
                                if loc.nth(i).is_visible():
                                    found_term = term
                                    break
                        except Exception:
                            pass
                        if found_term:
                            break
                    if not found_term:
                        time.sleep(0.5)
                if found_term:
                    match_count = 1
                else:
                    status = "fail"
                    error = f"No visible element containing any of {terms}" + (
                        f" or matching {sel_alts}" if sel_alts else "")
                    issue_type = "product"
            elif check.get("type") == "text":
                # Poll for text content
                deadline = time.time() + 8000 / 1000
                content = None
                while time.time() < deadline:
                    try:
                        content = page.text_content(check["selector"])
                        if content:
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)
                expected = check.get("contains", "")
                if expected and (not content or expected not in content):
                    status = "fail"
                    error = f"Text '{expected}' not found in '{(content or '')[:80]}'"
                    issue_type = "product"
                match_count = None
            elif check.get("type") == "attribute":
                attr = None
                deadline = time.time() + 5000 / 1000
                while time.time() < deadline:
                    try:
                        attr = page.locator(check["selector"]).get_attribute(check["attribute"])
                        if attr is not None:
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)
                expected = check.get("value")
                if expected and attr != expected:
                    status = "fail"
                    error = f"Attribute {check['attribute']}='{attr}', expected '{expected}'"
                    issue_type = "product"
                match_count = None
            elif check.get("type") == "response":
                status = "pass"
                match_count = None
            else:
                match_count = None
        except PWTimeout:
            status = "fail"
            error = "Timeout waiting for element"
            issue_type = "infra"
            match_count = None
        except Exception as e:
            status = "fail"
            error = str(e)[:100]
            issue_type = "infra"
            match_count = None

        duration = int((time.time() - t0) * 1000)

        # If match_count wasn't set by the check type above, try to get it
        if match_count is None:
            try:
                match_count = page.locator(check.get("selector", "")).count()
            except Exception:
                match_count = None

        results.append({
            "check": check.get("name", check.get("selector", "unknown")),
            "check_type": check.get("type", "visible"),
            "status": status,
            "duration_ms": duration,
            "error": error,
            "match_count": match_count,
            "min": check.get("min"),
            "issue_type": issue_type,  # "product" = real bug, "infra" = timing/lazy-load
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
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            # Extra wait for client-side hydration
            time.sleep(3)

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
                log(f"  {icon} home/{r['check']}: {r['status']} ({r['duration_ms']}ms) {r.get('error', '') or ''} [{r.get('issue_type','')}]")

            # ── Category page features ──
            log("Loading category page for feature checks")
            page.goto("https://gajab.com/product-list/all", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            # Wait for product cards to render (client-side)
            try:
                page.wait_for_selector("a[href*='/product-detail/']", timeout=10000)
            except PWTimeout:
                pass
            time.sleep(2)

            category_checks = [
                {"name": "product_cards", "type": "count", "selector": "a[href*='/product-detail/']", "min": 6, "poll_timeout_ms": 10000},
                {"name": "filter_panel", "type": "text_visible", "terms": ["Filters", "Filter"]},
                {"name": "price_filter", "type": "text_visible", "terms": ["Price", "₹"], "click_before": "Filters"},
                {"name": "sort_dropdown", "type": "text_visible", "terms": ["Relevance", "Sort by", "Popularity"]},
                {"name": "pagination", "type": "visible", "selector": "a[href*='offset'], [class*='pagination'], button:has-text('Load More')"},
            ]
            log(f"Running {len(category_checks)} category page feature checks")
            for r in check_elements(page, category_checks):
                results.append({**r, "page": "category", "category": "feature"})
                icon = "✅" if r["status"] == "pass" else "❌"
                log(f"  {icon} category/{r['check']}: {r['status']} ({r['duration_ms']}ms) {r.get('error', '') or ''} [{r.get('issue_type','')}]")

            # ── Product detail features ──
            product_url = "https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914"
            log("Loading product detail page for feature checks")
            page.goto(product_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            # Wait for PDP to hydrate
            try:
                page.wait_for_selector("#varient-price, h1", timeout=10000)
            except PWTimeout:
                pass
            time.sleep(2)

            pdp_checks = [
                {"name": "product_title", "type": "visible", "selector": "h1, [class*='title'], [class*='product-name']"},
                {"name": "product_image", "type": "visible", "selector": "img[src*='resize.gajab.com'], img[alt*='product'], [class*='gallery'] img"},
                {"name": "price_display", "type": "visible", "selector": "#varient-price, [class*='price'], [class*='Price']"},
                {"name": "start_bargaining_btn", "type": "text_visible", "terms": ["Start Bargaining", "Your best price"]},
                {"name": "asking_price", "type": "text_visible", "terms": ["Asking", "MRP"]},
                {"name": "ratings_section", "type": "text_visible", "terms": ["Rating", "ratings", "Reviews"],
                 "selectors": ["img[alt*='star']", "[class*='star']"]},
            ]
            log(f"Running {len(pdp_checks)} product detail feature checks")
            for r in check_elements(page, pdp_checks):
                results.append({**r, "page": "product_detail", "category": "feature"})
                icon = "✅" if r["status"] == "pass" else "❌"
                log(f"  {icon} pdp/{r['check']}: {r['status']} ({r['duration_ms']}ms) {r.get('error', '') or ''} [{r.get('issue_type','')}]")

            log("All feature checks completed")

        except Exception as e:
            log(f"Feature check error: {e}")
            results.append({"page": "feature_checks", "check": "overall", "status": "fail", "duration_ms": 0, "error": str(e)[:100], "category": "feature", "issue_type": "infra"})
        finally:
            context.close()
            browser.close()

    return results
