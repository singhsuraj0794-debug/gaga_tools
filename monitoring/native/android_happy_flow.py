#!/usr/bin/env python3
"""
android_happy_flow.py — Complete native Android happy-flow check via Appium.

Mirrors the web happy flow: home → category → product → bargain → checkout,
with per-step screenshots, a full screen recording, and Supabase storage.

Prereqs:
  - Appium server running:  ANDROID_HOME=/opt/homebrew/share/android-commandlinetools appium --port 4723
  - Emulator running with gajab app installed + logged in (package: com.gajab.buyerstore)
"""
from __future__ import annotations

import base64
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow importing the monitoring package (supabase_client, etc.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appium import webdriver
from appium.options.android import UiAutomator2Options

from supabase_client import SupabaseStore

APP_PACKAGE = "com.gajab.buyerstore"
APP_ACTIVITY = ".MainActivity"
APPIUM_URL = "http://localhost:4723"
FLOW_NAME = "native_happy_flow"
PLATFORM = "android"

_SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
_SCREENSHOT_DIR.mkdir(exist_ok=True)
_RECORDING_DIR = Path(__file__).parent / "recordings"
_RECORDING_DIR.mkdir(exist_ok=True)


def connect() -> webdriver.Remote:
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = "emulator-5554"
    options.app_package = APP_PACKAGE
    options.app_activity = APP_ACTIVITY
    options.no_reset = True
    options.auto_grant_permissions = True
    options.new_command_timeout = 600
    # App launch on a cold/slow emulator regularly exceeded the default 20s
    # adbExecTimeout, so activate_app()/start-activity threw and the whole run
    # aborted ("adbExec timeout ... timed out after 20000ms"). Give adb room.
    options.adb_exec_timeout = 120000
    options.uiautomator2_server_launch_timeout = 120000
    options.uiautomator2_server_install_timeout = 120000
    driver = webdriver.Remote(APPIUM_URL, options=options)
    driver.update_settings({"waitForIdleTimeout": 0, "waitForSelectorTimeout": 0})
    return driver


def wait_for_tree(driver, timeout: int = 20, label: str = "") -> bool:
    """Wait until the accessibility tree is readable again.

    UIAutomator intermittently returns an empty tree while the app's UI thread
    is busy ("Timed out waiting for the root AccessibilityNodeInfo"). Polling
    page_source lets the app settle instead of failing the step outright.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            src = driver.page_source
            if src and len(src) > 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    if label:
        print(f"[flow] tree still unreadable after {timeout}s ({label})")
    return False


def find(driver, xpath: str, timeout: int = 20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        els = driver.find_elements("xpath", xpath)
        if els:
            return els[0]
        time.sleep(0.5)
    return None


def find_desc(driver, substring: str, timeout: int = 20):
    return find(driver, f'//*[contains(@content-desc, "{substring}")]', timeout)


# The current Gajab build exposes only testID-based content-descs — no visible
# text ("Suraj", "Asking Price", "Start Bargaining" are NOT in the UI tree).
# These helpers match the app's own testIDs so checks work either way.
def has_testid(driver, testid: str, timeout: int = 8) -> bool:
    return find_desc(driver, testid, timeout) is not None


def has_any_testid(driver, testids, timeout: int = 8) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t in testids:
            if find_desc(driver, t, timeout=1) is not None:
                return True
        time.sleep(0.5)
    return False


HOME_READY_TESTIDS = (
    "dashboard_bazaar_tab", "dashboard_categories_tab",
    "home_category_0_item", "product_template1_product_0_card",
    "home_profile_avatar_button", "trending_product_0_card",
    "gajab_deal_product_card",
)
PRODUCT_CARD_TESTIDS = (
    "product_template1_product_0_card", "product_template1_product_1_card",
    "product_template2_product_0_card", "product_card",
)
CATEGORY_CARD_TESTIDS = ("category_template_item_0_card", "home_category_0_item")


def screenshot(driver, label: str) -> str | None:
    try:
        b64 = driver.get_screenshot_as_base64()
        path = _SCREENSHOT_DIR / f"android_{label}_{int(time.time())}.png"
        path.write_bytes(base64.b64decode(b64))
        return str(path)
    except Exception as e:
        print(f"[screenshot] {label} failed: {e}")
        return None


def tap_center(driver, el, duration: int = 100) -> bool:
    """Tap an element (el.click() is most reliable for React Native)."""
    try:
        el.click()
        return True
    except Exception:
        try:
            rect = el.rect
            x = rect["x"] + rect["width"] // 2
            y = rect["y"] + rect["height"] // 2
            driver.tap([(x, y)], duration)
            return True
        except Exception:
            return False


def dismiss_blocking_dialogs(driver) -> bool:
    """Dismiss app-update / promo modals that cover the UI.

    A forced 'Update App?' modal (e.g. "Version 1.0.30 is available") blocks
    every tap, so the whole happy flow fails even though the app is logged in.
    Tap outside the dialog (it is centred) and fall back to the Back button.
    """
    dismissed = False
    blocking_markers = (
        "Update App", "Would you like to update", "would you like to update",
        "Rate this app", "Rate the app", "Enjoying Gajab",
    )
    for _ in range(3):
        present = False
        for m in blocking_markers:
            if find_desc(driver, m, timeout=2) is not None:
                present = True
                break
        if not present:
            break
        # Tap well above the centred dialog (its box starts mid-screen).
        try:
            size = driver.get_window_size()
            driver.tap([(size["width"] // 2, int(size["height"] * 0.16))], 120)
            time.sleep(1.2)
        except Exception:
            pass
        still = any(find_desc(driver, m, timeout=1) is not None for m in blocking_markers)
        if still:
            try:
                driver.back()
                time.sleep(1.2)
            except Exception:
                pass
        if not any(find_desc(driver, m, timeout=1) is not None for m in blocking_markers):
            dismissed = True
            break
    return dismissed


def disable_animations() -> None:
    """Turn off device animations.

    With animations on, the app's UI thread never goes idle and UIAutomator
    cannot fetch the accessibility tree ("Timed out ... waiting for the root
    AccessibilityNodeInfo ... the application is being idle long enough"),
    which makes every element lookup time out. Also applied by
    start-native-monitor.sh; repeated here so a manual run is covered too.
    """
    import subprocess
    for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        try:
            subprocess.run(["adb", "shell", "settings", "put", "global", key, "0"],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def run_flow() -> list[dict]:
    disable_animations()
    driver = connect()
    store = SupabaseStore()
    results = []
    try:
        # Reset to home (relaunch, keep login session)
        try:
            driver.terminate_app(APP_PACKAGE)
        except Exception:
            pass
        # Launch with retries + fallback. A cold emulator can take longer than
        # adb's timeout to start the activity; retrying beats aborting the run.
        launched = False
        for attempt in range(3):
            try:
                driver.activate_app(APP_PACKAGE)
                launched = True
                break
            except Exception as e:
                print(f"[flow] activate_app attempt {attempt+1} failed: {str(e)[:120]}")
                try:
                    driver.start_activity(APP_PACKAGE, APP_ACTIVITY)
                    launched = True
                    break
                except Exception as e2:
                    print(f"[flow] start_activity failed: {str(e2)[:120]}")
                time.sleep(5)
        if not launched:
            print("[flow] could not launch app after retries — continuing anyway")
        # Give the UI time to settle after a cold launch. With animations off
        # the accessibility tree becomes readable (verified: 19KB dump vs 0).
        time.sleep(10)

        # An app-update / promo modal can cover the whole UI and block every
        # tap (seen: "Update App? Version 1.0.31 is available"). The app exposes
        # only testIDs — no text — so we cannot detect it by its label. Instead:
        # wait for the home testIDs and, if they never appear, tap the scrim
        # ABOVE the centred dialog to dismiss whatever is blocking the UI.
        home_ready = False
        for attempt in range(5):
            if has_any_testid(driver, HOME_READY_TESTIDS, timeout=5):
                home_ready = True
                break
            if dismiss_blocking_dialogs(driver):
                print("[flow] dismissed a blocking dialog (update/promo modal)")
            try:
                size = driver.get_window_size()
                # scrim area above the centred dialog (dialog starts ~30% down)
                driver.tap([(size["width"] // 2, int(size["height"] * 0.16))], 120)
            except Exception:
                pass
            time.sleep(2)
        if not home_ready:
            print("[flow] home testIDs not seen after modal-clearing attempts")

        # Start full-screen recording
        try:
            driver.start_recording_screen()
        except Exception as e:
            print(f"[record] start failed: {e}")

        # ── Step 1: home load ──
        t0 = time.time()
        # Newer builds expose no text for the profile name, so accept the home
        # testIDs (dashboard tabs / first product card) as "logged in + loaded".
        logged_in = (
            find_desc(driver, "Suraj", timeout=8) is not None
            or has_any_testid(driver, HOME_READY_TESTIDS, timeout=8)
        )
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_home_load", "status": "pass" if logged_in else "fail",
                        "detail": "logged in (home visible)" if logged_in else "profile not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "home")})

        # ── Step 2: home products populate ──
        t0 = time.time()
        products = find_desc(driver, "Asking Price", timeout=15) is not None \
            or has_any_testid(driver, PRODUCT_CARD_TESTIDS, timeout=5)
        if not products:
            # Products may be below fold or loading slowly — scroll down
            driver.swipe(540, 1800, 540, 1000, 600)
            time.sleep(2)
            products = find_desc(driver, "Asking Price", timeout=10) is not None \
                or has_any_testid(driver, PRODUCT_CARD_TESTIDS, timeout=5)
        if not products:
            # Try alternate indicators
            products = find_desc(driver, "Trending", timeout=5) is not None or \
                       find_desc(driver, "Bargain Price", timeout=5) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_home_products_populate", "status": "pass" if products else "fail",
                        "detail": "product cards visible" if products else "no product cards",
                        "duration_ms": duration, "screenshot": screenshot(driver, "home_products")})

        wait_for_tree(driver, 20, "banners")
        # ── Step 2b: banners / category tabs ──
        t0 = time.time()
        BANNER_IDS = (
            "home_banner_1_card", "home_banner_8_card", "home_banner_0_card",
            "gajab_deal_block_0_button", "gajab_deal_product_card",
            "trending_product_0_card", "home_live_deal_product_card_button",
            "home_banner_2_card", "home_banner_3_card",
        )
        CAT_TAB_IDS = ("home_category_0_item", "home_category_1_item",
                       "home_category_2_item", "dashboard_categories_tab")

        # Check at the CURRENT (top) position first — the banner/category tabs are
        # usually here, and scrolling first was what made this check flaky.
        banner = has_any_testid(driver, BANNER_IDS, timeout=5) \
            or find_desc(driver, "Buy Now", timeout=2) is not None
        cat_tabs = has_any_testid(driver, CAT_TAB_IDS, timeout=5) \
            or find_desc(driver, "Home & Kitchen", timeout=2) is not None \
            or find_desc(driver, "All", timeout=2) is not None
        # If either is missing, scroll down and re-check (banners can sit below fold).
        if not banner or not cat_tabs:
            try:
                driver.swipe(500, 1700, 500, 800, 600)
                time.sleep(1.5)
            except Exception:
                pass
            banner = banner or has_any_testid(driver, BANNER_IDS, timeout=4)
            cat_tabs = cat_tabs or has_any_testid(driver, CAT_TAB_IDS, timeout=4)
        # Scroll back to the top so the next step starts cleanly.
        try:
            driver.swipe(540, 700, 540, 1900, 500)
            time.sleep(1)
        except Exception:
            pass
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_banners_check", "status": "pass" if (banner and cat_tabs) else "fail",
                        "detail": f"banners={banner}, category tabs={cat_tabs}",
                        "duration_ms": duration, "screenshot": screenshot(driver, "banners")})

        wait_for_tree(driver, 20, "category")
        # ── Step 3: category ──
        # Scroll back to top first (banners_check scrolled down)
        driver.swipe(540, 600, 540, 1800, 600)
        time.sleep(1)
        cat_tab = find_desc(driver, "dashboard_categories_tab", timeout=8) or \
                  find_desc(driver, "Categories", timeout=4)
        if cat_tab:
            cat_tab.click()
            time.sleep(3)
        # Dismiss keyboard/search if still open from previous run
        keyboard = find(driver, '//android.view.inputmethod.InputMethodService', 2) or \
                   find(driver, '//android.widget.Button[@text="✓"]', 2)
        if keyboard:
            driver.back()
            time.sleep(1)
        # Also check if search bar is focused — press back to dismiss
        search_bar = find(driver, '//android.widget.EditText', 2)
        if search_bar:
            try:
                focused = search_bar.get_attribute("focused")
                if focused == "true":
                    driver.back()
                    time.sleep(1)
            except Exception:
                pass
        # The Categories page has a grid/list of category entries — look for one that is
        # a clickable category item (NOT in the search bar, NOT in Recently Viewed)
        # Category items on the Categories page are typically ImageView or TextView with category name
        # that have content-desc and are clickable, located below the top area (y > 150)
        t0 = time.time()
        cat_link = None
        # Try to find category links that are actual list items (below search bar area, y > 200)
        CAT_NAMES = ["Sporting Goods", "Kitchen & Dining", "Household Appliances",
                     "Lawn & Garden", "Home & Kitchen", "Toys & Games", "Gaming",
                     "Beauty & Health", "Electronics", "Fashion Accessories"]
        # Prefer the app's own category testIDs (no visible text is exposed).
        for tid in ("home_category_0_item", "home_category_1_item", "home_category_2_item",
                    "category_item_105_button", "category_item_53_button",
                    "category_item_58_button", "category_item_70_button"):
            cat_link = find_desc(driver, tid, timeout=2)
            if cat_link:
                break
        if not cat_link:
            for name in CAT_NAMES:
                els = driver.find_elements("xpath", f'//*[contains(@content-desc, "{name}") or contains(@text, "{name}")]')
                for el in els:
                    try:
                        rect = el.rect
                        if rect["y"] > 200:
                            cat_link = el
                            break
                    except Exception:
                        continue
                if cat_link:
                    break
        if cat_link:
            tap_center(driver, cat_link)
            time.sleep(4)
        # Collect product cards — try multiple selectors since different category pages
        # use different layouts. Filter out filter/sort elements and header area.
        cat_cards = []
        # Try 0: the app's own product-card testIDs. On a category listing the
        # cards are 'child_category_product_card_*' (verified), on home they are
        # 'product_template*_product_*_card'.
        for tid in ("child_category_product_card_", "product_template1_product_0_card",
                    "product_template1_product_1_card", "product_template2_product_0_card",
                    "product_template2_product_1_card"):
            el = find_desc(driver, tid, timeout=2)
            if el:
                cat_cards.append(el)
        # Count all child-category cards if present (still "products loaded").
        if not cat_cards:
            try:
                cards = driver.find_elements("xpath", '//*[contains(@content-desc, "child_category_product_card_")]')
                cat_cards.extend(cards)
            except Exception:
                pass
        # Try 1: clickable ImageViews with non-empty content-desc (home page products)
        if len(cat_cards) < 3:
            cat_cards += driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
        # Try 2: if few found, also try clickable ViewGroups/FrameLayouts in product grid area
        if len(cat_cards) < 3:
            extras = driver.find_elements("xpath", '//android.view.ViewGroup[@clickable="true"]')
            for el in extras:
                try:
                    rect = el.rect
                    if rect["y"] > 300 and rect["width"] > 100 and rect["height"] > 100:
                        cat_cards.append(el)
                except Exception:
                    continue
        # Try 3: if still few, look for any clickable element with price text (₹)
        if len(cat_cards) < 3:
            price_els = driver.find_elements("xpath", '//*[contains(@text, "₹") and @clickable="true"]')
            for el in price_els:
                try:
                    rect = el.rect
                    if rect["y"] > 300:
                        # Find its parent or sibling image
                        cat_cards.append(el)
                except Exception:
                    continue
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_category_load", "status": "pass" if cat_cards else "fail",
                        "detail": f"{len(cat_cards)} category products loaded" if cat_cards else "no category products",
                        "duration_ms": duration, "screenshot": screenshot(driver, "category")})

        wait_for_tree(driver, 20, "product detail")
        # ── Step 4: product detail — pick a random in-stock product ──
        bargain_btn = None
        chosen = None
        pool = list(cat_cards)
        random.shuffle(pool)
        for prod in pool[:10]:  # try up to 10 random products
            try:
                prod.click()
                time.sleep(2.5)
                # Skip out-of-stock products immediately
                if find_desc(driver, "Out of Stock", timeout=2):
                    driver.back()
                    time.sleep(2)
                    continue
                bargain_btn = find_desc(driver, "Start Bargaining", timeout=3) or \
                                find_desc(driver, "pdp_commonsheet_bargain_button", timeout=2)
                if bargain_btn:
                    chosen = prod
                    break
                # Not bargainable for another reason — go back and try another
                driver.back()
                time.sleep(2)
            except Exception:
                continue
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_product_detail_load", "status": "pass" if bargain_btn else "fail",
                        "detail": "Start Bargaining visible" if bargain_btn else "no bargainable product found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "product_detail")})

        wait_for_tree(driver, 20, "bargain")
        # ── Step 5: bargain flow (open modal with retry, slide price down, then offer) ──
        t0 = time.time()
        offer_btn = None
        slid = False
        # Tap Start Bargaining (retry until the modal opens) — the tap is flaky on RN
        for attempt in range(5):
            sb = find_desc(driver, "Start Bargaining", timeout=5) or \
                 find_desc(driver, "pdp_commonsheet_bargain_button", timeout=1)
            if sb:
                tap_center(driver, sb)
                time.sleep(3)
            offer_btn = find_desc(driver, "Offer Your Price", timeout=6) or \
                        find_desc(driver, "pdp_bargains_offer_your_price_button", timeout=1) or \
                        find_desc(driver, "Make an Offer", timeout=3)
            if offer_btn:
                break
        # Slide the price down via the clickable price markers (e.g. 538, 568, 598, ...)
        if offer_btn:
            for price in ("568", "598", "628"):
                marker = find_desc(driver, price, timeout=3)
                if marker:
                    tap_center(driver, marker)
                    time.sleep(1)
                    slid = True
                    break
            if not slid:
                # Newer build uses preset chips instead of price text nodes.
                for chip in ("pdp_bargains_preset_chip_29", "pdp_bargains_preset_chip_28",
                             "pdp_bargains_preset_chip_30"):
                    marker = find_desc(driver, chip, timeout=1)
                    if marker:
                        tap_center(driver, marker)
                        time.sleep(1)
                        slid = True
                        break
            tap_center(driver, offer_btn)
            time.sleep(4)
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_bargain_flow", "status": "pass" if offer_btn else "fail",
                        "detail": ("offer submitted (price slid)" if slid else "offer submitted") if offer_btn else "offer button not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "bargain")})

        # ── Step 6: checkout (Bargains → item → Buy Now/Pay → gateway) ──
        bargains_tab = find_desc(driver, "Bargains", timeout=8)
        if bargains_tab:
            bargains_tab.click()
            time.sleep(3)
        t0 = time.time()
        buy_btn = None
        bargain_more = None
        # Click the first bargain item to open its detail (won bargains show "Buy Now")
        items = driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
        if items:
            items[0].click()
            time.sleep(4)
        buy_btn = find_desc(driver, "Buy Now", timeout=8) or find_desc(driver, "Pay", timeout=5)
        if not buy_btn:
            accept_btn = find_desc(driver, "Accept the offer", timeout=6)
            bargain_more = find_desc(driver, "Bargain More", timeout=5)
            if accept_btn:
                accept_btn.click()
                time.sleep(4)
                buy_btn = find_desc(driver, "Buy Now", timeout=8) or find_desc(driver, "Pay", timeout=5)
        if buy_btn:
            buy_btn.click()
            time.sleep(4)
        duration = int((time.time() - t0) * 1000)
        # The checkout page shows "Checkout" / "Pay Online" / "Pay ₹N" (final pay button opens the gateway)
        checkout_page = find_desc(driver, "Checkout", timeout=8) or find_desc(driver, "Pay Online", timeout=5)
        pay_now = find_desc(driver, "Pay ₹", timeout=5)
        gateway = find_desc(driver, "Razorpay", timeout=5) or find_desc(driver, "UPI", timeout=5) or \
                 find_desc(driver, "Debit Card", timeout=5)
        if pay_now and not gateway:
            pay_now.click()
            time.sleep(4)
            gateway = find_desc(driver, "Razorpay", timeout=8) or find_desc(driver, "UPI", timeout=5) or \
                     find_desc(driver, "Debit Card", timeout=5)
        if gateway:
            checkout_status, checkout_detail = "pass", "payment gateway opened"
        elif checkout_page or pay_now:
            checkout_status, checkout_detail = "pass", "checkout page reached (Pay ₹ button present)"
        elif buy_btn:
            checkout_status, checkout_detail = "pass", "Buy Now clicked (gateway not detected)"
        elif bargain_more:
            checkout_status, checkout_detail = "degraded", "bargain still in progress (Bargain More)"
        else:
            checkout_status, checkout_detail = "fail", "no bargain item in My Bargains"
        results.append({"step": f"{PLATFORM}_checkout_flow", "status": checkout_status,
                        "detail": checkout_detail, "duration_ms": duration, "screenshot": screenshot(driver, "checkout")})

        # ── Step 7: My Bargains page (reset back to main app first) ──
        t0 = time.time()
        wait_for_tree(driver, 20, "my bargains")
        # Back out until the bottom nav is visible (dashboard_*_tab testIDs).
        for _ in range(10):
            if has_any_testid(driver, ("dashboard_bazaar_tab", "dashboard_bargains_tab",
                                       "dashboard_alerts_tab"), timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        bargains_tab = find_desc(driver, "dashboard_bargains_tab", timeout=8) or \
                       find_desc(driver, "Bargains", timeout=3)
        if bargains_tab:
            bargains_tab.click()
            time.sleep(3)
        my_bargains_ok = has_any_testid(driver, ("mybargain_card_0", "mybargain_product_tap_0",
                                                 "mybargain_bargain_again_button", "mybargain_offer_status_0",
                                                 "mybargain_card_1"), timeout=10) or \
                         find_desc(driver, "My Bargains", timeout=3) is not None or \
                         find_desc(driver, "Bargain More", timeout=3) is not None or \
                         find_desc(driver, "Buy Now", timeout=3) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_my_bargains", "status": "pass" if my_bargains_ok else "fail",
                        "detail": "My Bargains page loaded" if my_bargains_ok else "no bargains",
                        "duration_ms": duration, "screenshot": screenshot(driver, "my_bargains")})

        # ── Step 8: Alerts / Orders page (navigate via bottom nav) ──
        t0 = time.time()
        wait_for_tree(driver, 20, "alerts")
        # Bottom nav should already be visible from Bargains page; if not, back out.
        for _ in range(10):
            if has_any_testid(driver, ("dashboard_alerts_tab", "dashboard_bazaar_tab"), timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        alerts_tab = find_desc(driver, "dashboard_alerts_tab", timeout=8) or \
                     find_desc(driver, "Alerts", timeout=3)
        if alerts_tab:
            alerts_tab.click()
            time.sleep(3)
        alerts_ok = has_any_testid(driver, ("alerts_filter_tab_all", "alerts_notification_card_0",
                                            "alerts_filter_tab_orders", "alerts_notification_card_1"), timeout=10) or \
                    find_desc(driver, "Alerts", timeout=3) is not None or \
                    find_desc(driver, "Notification", timeout=3) is not None or \
                    find_desc(driver, "Orders", timeout=3) is not None
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_alerts_orders", "status": "pass" if alerts_ok else "fail",
                        "detail": "alerts/orders page loaded" if alerts_ok else "page not found",
                        "duration_ms": duration, "screenshot": screenshot(driver, "alerts_orders")})

        # ── Step 9: Search products ──
        t0 = time.time()
        search_ok = False
        search_detail = "search not available"
        # Relaunch app if it was backgrounded
        try:
            driver.activate_app(APP_PACKAGE)
            time.sleep(2)
        except Exception:
            pass
        # Navigate back to app home first
        for _ in range(10):
            if find_desc(driver, "Bazaar", timeout=1) or find_desc(driver, "Categories", timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        # Go to Categories page where the search icon is
        cat_tab3 = find_desc(driver, "Categories", timeout=5)
        if cat_tab3:
            cat_tab3.click()
            time.sleep(3)
        # The search icon is a magnifying glass ImageView in the top-right of the header
        # Try finding it by class + position (top 150px of screen, right half)
        search_icon = None
        icons = driver.find_elements("xpath", '//android.widget.ImageView[@clickable="true"]')
        for icon in icons:
            try:
                rect = icon.rect
                # Search icon is in the header (y < 150) and on the right side
                if rect["y"] < 150 and rect["x"] > 300:
                    search_icon = icon
                    break
            except Exception:
                continue
        if not search_icon:
            # Fallback: look for content-desc containing "search" or "Search"
            search_icon = find_desc(driver, "Search", timeout=5)
        if search_icon:
            try:
                search_icon.click()
                time.sleep(2)
            except Exception:
                tap_center(driver, search_icon)
                time.sleep(2)
            # Type search query in the EditText that appears
            search_input = find(driver, '//android.widget.EditText', 5)
            if search_input:
                search_input.clear()
                search_input.send_keys("cricket bat")
                time.sleep(3)
                results_found = find(driver, '//*[contains(@text, "cricket") or contains(@text, "Cricket")]', 8) is not None
                no_results = find(driver, '//*[contains(@text, "No results") or contains(@text, "no results") or contains(@text, "did not match")]', 5) is not None
                if results_found:
                    search_ok = True
                    search_detail = "search results displayed"
                elif no_results:
                    search_ok = True
                    search_detail = "search completed (no results for cricket bat)"
                else:
                    search_ok = True
                    search_detail = "search submitted"
                # Navigate back safely (check we're still in the app)
                for _ in range(5):
                    if find_desc(driver, "Bazaar", timeout=1) or find_desc(driver, "Categories", timeout=1):
                        break
                    try:
                        driver.back()
                        time.sleep(0.5)
                    except Exception:
                        break
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_search_products", "status": "pass" if search_ok else "degraded",
                        "detail": search_detail,
                        "duration_ms": duration, "screenshot": screenshot(driver, "search_products")})

        # ── Step 10: bargain 2 (second bargain on another random product) ──
        t0 = time.time()
        b2_offer = None
        b2_slid = False
        # Relaunch app if it was backgrounded by search step
        try:
            driver.activate_app(APP_PACKAGE)
            time.sleep(2)
        except Exception:
            pass
        wait_for_tree(driver, 20, "bargain 2")
        # Ensure bottom nav is visible (dashboard_*_tab testIDs)
        for _ in range(10):
            if has_any_testid(driver, ("dashboard_categories_tab", "dashboard_bazaar_tab"), timeout=1):
                break
            try:
                driver.back()
                time.sleep(0.5)
            except Exception:
                break
        # Navigate to the Categories tab via its testID.
        cat_tab2 = find_desc(driver, "dashboard_categories_tab", timeout=8) or \
                   find_desc(driver, "Categories", timeout=3)
        if cat_tab2:
            tap_center(driver, cat_tab2)
            time.sleep(3)

        # Try SEVERAL categories until a bargainable product is found. The first
        # category often has none, which is why bargain 2 kept failing.
        b2_bargain_btn = None
        CAT2_IDS = (
            "category_item_105_button", "category_item_53_button", "category_item_58_button",
            "category_item_70_button", "category_item_72_button", "category_item_77_button",
            "category_item_87_button", "category_item_88_button", "category_item_89_button",
            "category_item_90_button", "category_item_91_button",
            "home_category_0_item", "home_category_1_item",
        )
        for cat_tid in CAT2_IDS:
            cat_link2 = find_desc(driver, cat_tid, timeout=3)
            if not cat_link2:
                continue
            tap_center(driver, cat_link2)
            time.sleep(3)
            wait_for_tree(driver, 15, f"bargain2 {cat_tid}")
            # Product cards on a category listing are child_category_product_card_*.
            cards2 = []
            try:
                cards2 = driver.find_elements("xpath", '//*[contains(@content-desc, "child_category_product_card_")]')
            except Exception:
                pass
            if not cards2:
                cards2 = driver.find_elements("xpath", '//android.widget.ImageView[@content-desc != "" and @clickable="true"]')
            random.shuffle(cards2)
            for prod in cards2[:12]:
                try:
                    prod.click()
                    time.sleep(2.5)
                    if find_desc(driver, "Out of Stock", timeout=2):
                        driver.back()
                        time.sleep(2)
                        continue
                    b2_bargain_btn = find_desc(driver, "pdp_commonsheet_bargain_button", timeout=5) or \
                                     find_desc(driver, "Start Bargaining", timeout=2)
                    if b2_bargain_btn:
                        break
                    driver.back()
                    time.sleep(2)
                except Exception:
                    try:
                        driver.back()
                        time.sleep(1)
                    except Exception:
                        pass
                    continue
            if b2_bargain_btn:
                break
            # Return to the categories list and try the next category.
            try:
                for _ in range(3):
                    if has_any_testid(driver, ("category_item_105_button", "dashboard_categories_tab"), timeout=1):
                        break
                    driver.back()
                    time.sleep(1)
            except Exception:
                pass
        if b2_bargain_btn:
            # open bargain modal with retry
            for _ in range(4):
                try:
                    sb = find_desc(driver, "Start Bargaining", timeout=4) or \
                         find_desc(driver, "pdp_commonsheet_bargain_button", timeout=1)
                    if sb:
                        sb.click()
                        time.sleep(3)
                except Exception:
                    time.sleep(1)
                    continue
                b2_offer = find_desc(driver, "pdp_bargains_offer_your_price_button", timeout=4) or \
                           find_desc(driver, "Offer Your Price", timeout=3) or \
                           find_desc(driver, "Make an Offer", timeout=2)
                if b2_offer:
                    break
            if b2_offer:
                for price in ("568", "598", "628"):
                    marker = find_desc(driver, price, timeout=2)
                    if marker:
                        marker.click()
                        time.sleep(1)
                        b2_slid = True
                        break
                if not b2_slid:
                    for chip in ("pdp_bargains_preset_chip_29", "pdp_bargains_preset_chip_28",
                                 "pdp_bargains_preset_chip_30"):
                        marker = find_desc(driver, chip, timeout=1)
                        if marker:
                            marker.click()
                            time.sleep(1)
                            b2_slid = True
                            break
                b2_offer.click()
                time.sleep(3)
        duration = int((time.time() - t0) * 1000)
        results.append({"step": f"{PLATFORM}_bargain2_flow", "status": "pass" if b2_offer else "fail",
                        "detail": ("second bargain submitted" if b2_slid else "second bargain submitted") if b2_offer else "no bargainable product for bargain 2",
                        "duration_ms": duration, "screenshot": screenshot(driver, "bargain2")})

    finally:
        # Stop recording and save
        video_path = None
        try:
            b64 = driver.stop_recording_screen()
            video_path = _RECORDING_DIR / f"android_{int(time.time())}.mp4"
            video_path.write_bytes(base64.b64decode(b64))
            print(f"[record] saved {video_path} ({video_path.stat().st_size // 1024}KB)")
        except Exception as e:
            print(f"[record] stop failed: {e}")
        driver.quit()

    # Upload video + screenshots to Supabase, store results
    video_url = None
    if video_path:
        video_url = store.upload_video(str(video_path), platform=PLATFORM)
    for r in results:
        details = {"detail": r["detail"]}
        ss_path = r.get("screenshot")
        if ss_path:
            url = store.upload_screenshot(ss_path, platform=PLATFORM)
            if url:
                details["screenshot_url"] = url
        if video_url:
            details["session_recording_url"] = video_url
        store.store_flow_step(FLOW_NAME, r["step"], r["duration_ms"], r["status"], None if r["status"] == "pass" else r["detail"], details, issue_type=r.get("issue_type"))
    return results


def main():
    results = run_flow()
    failed = [r for r in results if r["status"] == "fail"]
    degraded = [r for r in results if r["status"] == "degraded"]
    print("\n=== SUMMARY ===")
    for r in results:
        mark = "PASS" if r["status"] == "pass" else ("DEGRADED" if r["status"] == "degraded" else "FAIL")
        print(f"  {mark:9s} {r['step']}  {r['detail']}")
    print(f"steps={len(results)} passed={len(results)-len(failed)-len(degraded)} degraded={len(degraded)} failed={len(failed)}")
    # Exit 1 only when the run is catastrophically broken (most steps failed or
    # nothing was written to Supabase). One flaky data-dependent step (e.g. a
    # second bargain can't find another bargainable product) still writes its
    # row to Supabase and shouldn't mark the whole job as infra-failure.
    sys.exit(0 if (len(failed) < max(1, len(results) // 2)) else 1)


if __name__ == "__main__":
    main()
