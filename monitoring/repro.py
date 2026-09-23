"""Explainable, replicable failure details for the synthetic monitor.

Every check that can fail or degrade has a registry entry describing:
  expected  — what a healthy app/site must do
  repro     — numbered manual steps a human can follow to reproduce/verify
  owner     — who can act on it (app / api / infra / data)

`explain()` turns a monitor result into a self-contained block that answers
"what broke, what did we see, and how do I see it myself" — so an alert never
leaves the reader guessing what to click.

Usage:
    from repro import explain
    results.append({
        "step": "android_checkout_flow",
        "status": "degraded",
        "detail": "bargain still in progress",
        **explain("android_checkout_flow", "degraded", "Bargain More shown"),
    })
"""

from __future__ import annotations

import os
from typing import Iterable

# ---------------------------------------------------------------------------
# Registry: step name -> expected behaviour + manual reproduction steps
# ---------------------------------------------------------------------------

REPRO: dict[str, dict] = {
    # ── Android native app ─────────────────────────────────────────────────
    "android_home_load": {
        "owner": "app",
        "expected": "App opens past splash to the Home screen and the user session is restored (logged in).",
        "repro": [
            "Cold-launch the Gajab app (force-stop first: `adb shell am force-stop com.gajab.app`).",
            "Wait ~5s for the splash video to finish.",
            "Confirm the bottom navigation (Bazaar / Bargains / Alerts / Account) is visible.",
            "If it stays on splash or shows Login, the session restore failed.",
        ],
    },
    "android_home_products_populate": {
        "owner": "app",
        "expected": "Home renders product cards from the API within the step budget.",
        "repro": [
            "Open the app on Home.",
            "Scroll the first two carousels.",
            "Product cards should show an image, title and price — an empty grid means the feed API returned nothing.",
        ],
    },
    "android_banners_check": {
        "owner": "app",
        "expected": "Home banner carousel and the category tab strip are both present and populated.",
        "repro": [
            "Open Home.",
            "Check the top banner carousel auto-rotates and shows an image.",
            "Check the category tabs under it scroll horizontally and have at least 3 items.",
        ],
    },
    "android_category_load": {
        "owner": "app",
        "expected": "Tapping a home category opens a child-category/product grid that populates.",
        "repro": [
            "Open Home and tap any category tile (e.g. the first one).",
            "Wait for the listing page.",
            "A product grid should render; an empty page or endless spinner is a failure.",
        ],
    },
    "android_product_detail_load": {
        "owner": "app",
        "expected": "Product detail page (PDP) opens with media, price and a tappable bargain button.",
        "repro": [
            "From a category grid tap the first product card.",
            "Confirm the PDP shows an image, title, price and the 'Start Bargaining' button.",
            "If the button is missing the PDP did not finish loading.",
        ],
    },
    "android_bargain_flow": {
        "owner": "app",
        "expected": "Bargain modal opens, a lower price can be selected, and 'Offer Your Price' submits.",
        "repro": [
            "Open any PDP and tap 'Start Bargaining'.",
            "Move the price slider (or tap a preset chip) to a lower value.",
            "Tap 'Offer Your Price'.",
            "Confirm an offer confirmation appears.",
        ],
    },
    "android_checkout_flow": {
        "owner": "app",
        "expected": (
            "The Bargains tab opens, a bargain item is tappable, and either the checkout page "
            "or the payment gateway appears."
        ),
        "repro": [
            "Open the app and tap the 'Bargains' tab in the bottom navigation.",
            "Tap the first bargain card.",
            "An ACCEPTED bargain shows 'Buy Now' → tap it → the checkout page with 'Pay ₹N' should appear.",
            "A bargain still awaiting seller acceptance shows 'Bargain More' — this is expected, NOT a failure; "
            "checkout is only reachable once the offer is accepted.",
            "If no item is tappable, the bargains list failed to load.",
        ],
    },
    "android_my_bargains": {
        "owner": "app",
        "expected": "My Bargains page lists the user's bargains with a status filter.",
        "repro": [
            "Open the app, tap 'Bargains'.",
            "Confirm cards render and the status filter tabs (All / Pending / Accepted ...) are present.",
        ],
    },
    "android_alerts_orders": {
        "owner": "app",
        "expected": "Alerts page opens and its filter tabs render.",
        "repro": [
            "Open the app, tap 'Alerts'.",
            "Confirm the filter tabs (e.g. 'All') are present and the list area renders.",
        ],
    },
    "android_search_products": {
        "owner": "app",
        "expected": "Search returns product results for a common keyword.",
        "repro": [
            "Open the app, tap the search bar.",
            "Type a common term (e.g. 'shirt') and submit.",
            "A result grid should render; no results for a broad keyword means the search API failed.",
        ],
    },
    "android_bargain2_flow": {
        "owner": "data",
        "expected": "A second, still-bargainable product is found and an offer can be submitted.",
        "repro": [
            "Walk the category tabs on Home and open a few products.",
            "Find one whose PDP shows 'Start Bargaining' (not 'Buy Now').",
            "Submit an offer.",
            "This step degrades when every product browsed is already bargained/accepted — a data condition, not an app bug.",
        ],
    },

    # ── Web / mweb ─────────────────────────────────────────────────────────
    "home_load": {
        "owner": "web",
        "expected": "https://gajab.com/ loads with the banner, category strip and a populated product grid.",
        "repro": [
            "Open https://gajab.com/ in a fresh incognito window.",
            "Confirm the home banner, category tiles and product cards all render.",
            "Check the browser console for failed API calls.",
        ],
    },
    "category_load": {
        "owner": "web",
        "expected": "https://gajab.com/product-list/all lists products and supports filters.",
        "repro": [
            "Open https://gajab.com/product-list/all.",
            "Confirm products render and the filter/sort controls are usable.",
        ],
    },
    "random_product": {
        "owner": "web",
        "expected": "A product can be opened from a category listing.",
        "repro": [
            "Open any category, click a product card.",
            "Confirm the PDP loads with media and a bargain entry point.",
        ],
    },
    "bargain_flow": {
        "owner": "web",
        "expected": "The bargain slider moves and an offer can be submitted from the PDP.",
        "repro": [
            "Open a product detail page.",
            "Click the bargain / 'Offer your price' control.",
            "Drag the price slider down and submit the offer.",
            "Confirm the offer is accepted by the UI (confirmation toast / modal).",
        ],
    },
    "bargain2": {
        "owner": "data",
        "expected": "A second bargainable product is found across the category listings.",
        "repro": [
            "Visit several categories (the flow walks 5 categories x 30 products).",
            "Find a product without an existing accepted bargain.",
            "Degrades when all scanned products already have bargains — a data condition.",
        ],
    },
    "checkout_nav": {
        "owner": "web",
        "expected": "Checkout is reachable: 'Pay' opens Razorpay and the UPI option is offered.",
        "repro": [
            "From an accepted bargain click 'Buy Now'.",
            "On checkout click 'Pay'.",
            "Confirm the Razorpay iframe/modal opens and lists UPI.",
        ],
    },
    "my_bargains": {
        "owner": "web",
        "expected": "The My Bargains page lists the user's bargains.",
        "repro": [
            "Open https://gajab.com/my-bargains (logged in).",
            "Confirm bargain cards render with statuses.",
        ],
    },
    "alerts_orders": {
        "owner": "web",
        "expected": "Alerts and Orders pages render their lists.",
        "repro": [
            "Open the Alerts page and the Orders page while logged in.",
            "Confirm both lists render.",
        ],
    },
    "banners": {
        "owner": "web",
        "expected": "Home banners render and link to content.",
        "repro": [
            "Open https://gajab.com/.",
            "Confirm the banner carousel shows images and clicking one navigates.",
        ],
    },
    "search_products": {
        "owner": "web",
        "expected": "Search returns results for a keyword.",
        "repro": [
            "Use the search bar on https://gajab.com/ with a common keyword.",
            "Confirm results render.",
        ],
    },

    # ── API / infrastructure ───────────────────────────────────────────────
    "healthz": {
        "owner": "api",
        "expected": "The API server answers its health check.",
        "repro": [
            "curl -sS https://gajab.com/api/healthz",
            "A non-200 or timeout means the API/edge is down.",
        ],
    },
    # Server-health endpoints (server_health.HEALTH_ENDPOINTS)
    "gajab.com (main)": {
        "owner": "web",
        "expected": "The gajab.com homepage returns HTTP 200 within the latency budget.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code} %{time_total}s\\n' https://gajab.com/",
            "Open https://gajab.com/ in an incognito window and confirm it renders.",
            "If slow, check the origin/CDN (Cloudflare) and the backend logs.",
        ],
    },
    "gajab.com (category)": {
        "owner": "web",
        "expected": "The category listing returns HTTP 200 within the latency budget.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code} %{time_total}s\\n' https://gajab.com/product-list/all",
            "Open https://gajab.com/product-list/all and confirm products render.",
        ],
    },
    "gatewayservice.gajab.com": {
        "owner": "api",
        "expected": "The gateway service product API returns HTTP 200 within the latency budget.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code} %{time_total}s\\n' 'https://gatewayservice.gajab.com/product/api/product-store/product/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914?pincode=400001'",
            "A 5xx means the product-store backend is failing; check its logs.",
        ],
    },
    "resize.gajab.com (CDN)": {
        "owner": "infra",
        "expected": "The image CDN serves images with HTTP 200 within the latency budget.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code} %{time_total}s\\n' https://resize.gajab.com/storeLogo/Gajab_og_banner_1770188098122.jpeg",
            "If it 5xx's, images across the site break — check the resize/CDN service.",
        ],
    },
    # API monitor endpoints (api_monitor.API_ENDPOINTS)
    "Home Page (gajab.com": {
        "owner": "web",
        "expected": "The homepage responds 200 with the expected page content.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code}\\n' https://gajab.com/",
            "Open https://gajab.com/ and confirm the page renders for a logged-out user.",
        ],
    },
    "Category Page (gajab.com": {
        "owner": "web",
        "expected": "The category page responds 200 with the expected page content.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code}\\n' https://gajab.com/product-list/all",
            "Open the category page and confirm the product grid loads.",
        ],
    },
    "Gateway Service (gatewayservice": {
        "owner": "api",
        "expected": "The gateway OTP/product endpoints respond successfully.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code}\\n' https://gatewayservice.gajab.com/customer/api/customer/mobile-send-otp-new",
            "A 5xx means the gateway backend is down — check its logs and the upstream DB.",
        ],
    },
    "Product Store API (gateway)": {
        "owner": "api",
        "expected": "The product-store API returns product JSON for a valid product + pincode.",
        "repro": [
            "curl -sS 'https://gatewayservice.gajab.com/product/api/product-store/product/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914?pincode=400001'",
            "An empty body or 5xx means the product-store service or its DB is failing.",
        ],
    },
    "Image CDN (resize.gajab.com)": {
        "owner": "infra",
        "expected": "The image CDN returns the image with HTTP 200.",
        "repro": [
            "curl -sS -o /dev/null -w '%{http_code}\\n' https://resize.gajab.com/storeLogo/Gajab_og_banner_1770188098122.jpeg",
            "A 5xx here breaks every product image on the site.",
        ],
    },
    "healthz": {
        "owner": "api",
        "expected": "The API server answers its health check.",
        "repro": [
            "curl -sS https://gajab.com/api/healthz",
            "A non-200 or timeout means the API/edge is down.",
        ],
    },
    "products_status": {
        "owner": "api",
        "expected": "The products status endpoint reports a healthy catalogue.",
        "repro": [
            "curl -sS https://gajab.com/api/products/status",
            "Compare the reported counts/staleness with the expected range.",
        ],
    },
    # ── Feature element checks (monitoring/feature_checks.py) ──────────────
    "site_logo": {
        "owner": "web",
        "expected": "The Gajab logo is visible in the header on every page.",
        "repro": ["Open https://gajab.com/", "Confirm the logo shows in the top-left header."],
    },
    "search_icon": {
        "owner": "web",
        "expected": "A search control (icon or input) is visible in the header.",
        "repro": ["Open https://gajab.com/", "Confirm the search icon/box is in the header and opens when clicked."],
    },
    "category_nav": {
        "owner": "web",
        "expected": "At least 3 category navigation entries are present.",
        "repro": ["Open https://gajab.com/", "Count the category tiles/links in the category strip (expect >= 3)."],
    },
    "hero_banner": {
        "owner": "web",
        "expected": "The hero banner carousel renders with images.",
        "repro": ["Open https://gajab.com/", "Confirm the top banner shows an image and auto-rotates."],
    },
    "product_grid": {
        "owner": "web",
        "expected": "At least 4 product cards render on the home page.",
        "repro": ["Open https://gajab.com/", "Count product cards in the first grid (expect >= 4).", "An empty grid means the feed API returned nothing."],
    },
    "most_bargained_section": {
        "owner": "web",
        "expected": "The 'Most Bargained' section is present on the home page.",
        "repro": ["Open https://gajab.com/", "Scroll to the 'Most Bargained' section and confirm cards render."],
    },
    "trending_section": {
        "owner": "web",
        "expected": "The 'Trending' section is present on the home page.",
        "repro": ["Open https://gajab.com/", "Scroll to the 'Trending' section and confirm cards render."],
    },
    "footer_links": {
        "owner": "web",
        "expected": "The footer contains at least 3 links.",
        "repro": ["Open https://gajab.com/", "Scroll to the footer and count the links (expect >= 3)."],
    },
    "login_button": {
        "owner": "web",
        "expected": "A login/sign-in entry point is visible when logged out.",
        "repro": ["Open https://gajab.com/ in an incognito window.", "Confirm the Login / Account control is visible."],
    },
    "product_cards": {
        "owner": "web",
        "expected": "The category listing renders at least 6 product cards.",
        "repro": ["Open https://gajab.com/product-list/all.", "Count the product cards (expect >= 6).", "Fewer means the listing API failed or paging broke."],
    },
    "filter_panel": {
        "owner": "web",
        "expected": "A Filters control is available on the category listing.",
        "repro": ["Open https://gajab.com/product-list/all.", "Confirm a 'Filters' button is visible and opens a panel."],
    },
    "price_filter": {
        "owner": "web",
        "expected": "The filter panel offers price filtering.",
        "repro": ["Open https://gajab.com/product-list/all.", "Click 'Filters' and confirm a Price control/range appears."],
    },
    "sort_dropdown": {
        "owner": "web",
        "expected": "A sort control (e.g. 'Relevance') is available on the category listing.",
        "repro": ["Open https://gajab.com/product-list/all.", "Confirm the sort control shows 'Relevance' and opens a menu."],
    },
    "pagination": {
        "owner": "web",
        "expected": "The listing supports paging (pagination control or Load More).",
        "repro": ["Open https://gajab.com/product-list/all.", "Scroll to the bottom and confirm pagination / Load More is present."],
    },
    "product_title": {
        "owner": "web",
        "expected": "The PDP shows the product title.",
        "repro": ["Open any product detail page.", "Confirm the H1/title is present."],
    },
    "product_image": {
        "owner": "web",
        "expected": "The PDP shows the product gallery image from the CDN.",
        "repro": ["Open any product detail page.", "Confirm the gallery image loads (resize.gajab.com)."],
    },
    "price_display": {
        "owner": "web",
        "expected": "The PDP shows the price.",
        "repro": ["Open any product detail page.", "Confirm the price element (#varient-price) is visible."],
    },
    "start_bargaining_btn": {
        "owner": "web",
        "expected": "The PDP shows the 'Start Bargaining' call to action.",
        "repro": ["Open any product detail page.", "Confirm the 'Start Bargaining' button is visible and clickable."],
    },
    "asking_price": {
        "owner": "web",
        "expected": "The PDP shows both MRP and the Asking price.",
        "repro": ["Open any product detail page.", "Confirm 'MRP: ₹X' and 'Asking: ₹Y' are both shown."],
    },
    "ratings_section": {
        "owner": "web",
        "expected": "The PDP shows the ratings/reviews area (stars and/or a ratings count).",
        "repro": ["Open any product detail page.", "Confirm the star row / ratings count renders.", "Note: the count text lives in a hidden container, so the check also accepts the star images."],
    },

    # ── Lighthouse / performance metrics (monitoring/lighthouse_audit.py) ──
    # Pages audited: home https://gajab.com/, category
    # https://gajab.com/product-list/all, product_detail (the Prestige veggie
    # cutter PDP). Thresholds come from monitoring/config.py THRESHOLDS.
    "lighthouse_performance_score": {
        "owner": "web",
        "expected": "Lighthouse performance score >= 50 (config THRESHOLDS.performance_score).",
        "repro": [
            "Identify the URL from the 'observed' line (home / category / product_detail).",
            "Run: npx lighthouse <URL> --only-categories=performance --form-factor=mobile --view",
            "Or open the URL in Chrome DevTools > Lighthouse > Mobile > Analyze page load.",
            "Compare the score with 50; then check the Opportunities list for the biggest wins.",
            "Common causes: large unoptimized hero images, render-blocking JS/CSS, slow TTFB.",
        ],
    },
    "lighthouse_lcp_ms": {
        "owner": "web",
        "expected": "Largest Contentful Paint <= 2500 ms (config THRESHOLDS.lcp_ms).",
        "repro": [
            "Note the URL from the 'observed' line.",
            "Run: npx lighthouse <URL> --only-audits=largest-contentful-paint --form-factor=mobile",
            "Or DevTools > Lighthouse > analyze, then read the LCP element in the report.",
            "The LCP element is usually the hero banner/product image — check its size and format (WebP).",
        ],
    },
    "lighthouse_cls": {
        "owner": "web",
        "expected": "Cumulative Layout Shift <= 0.1 (config THRESHOLDS.cls).",
        "repro": [
            "Note the URL from the 'observed' line.",
            "Run: npx lighthouse <URL> --only-audits=cumulative-layout-shift --form-factor=mobile",
            "Or load the URL in DevTools > Performance > Experience and watch for layout shifts.",
            "Usual causes: images without width/height, late-loading banners or fonts shifting content.",
        ],
    },
    "lighthouse_tbt_ms": {
        "owner": "web",
        "expected": "Total Blocking Time <= 300 ms (config THRESHOLDS.tbt_ms).",
        "repro": [
            "Note the URL from the 'observed' line.",
            "Run: npx lighthouse <URL> --only-audits=total-blocking-time --form-factor=mobile",
            "DevTools > Performance recording shows the long tasks that block the main thread.",
            "Usual causes: heavy JS bundles, long tasks during hydration.",
        ],
    },
    "lighthouse_si_ms": {
        "owner": "web",
        "expected": "Speed Index <= 4000 ms (config THRESHOLDS.si_ms).",
        "repro": [
            "Note the URL from the 'observed' line.",
            "Run: npx lighthouse <URL> --only-audits=speed-index --form-factor=mobile",
            "DevTools > Performance > Filmstrip shows how quickly content becomes visible.",
        ],
    },

    # ── Web/mweb step names as emitted by run_monitor (platform-prefixed) ──
    "checkout_flow": {
        "owner": "web",
        "expected": (
            "From My Bargains an accepted bargain exposes Pay, and Pay opens the "
            "Razorpay gateway. A bargain still awaiting seller acceptance is "
            "correctly gated (no Pay) and still counts as PASS."
        ),
        "repro": [
            "Log in on https://gajab.com/ and open https://gajab.com/my-bargains.",
            "Find a bargain whose status is ACCEPTED (a pending one has no Pay by design).",
            "Click Pay and confirm the Razorpay modal opens with UPI.",
            "If every bargain is still pending, Pay will legitimately be absent — that is expected, not a bug.",
        ],
    },
    "product_detail_load": {
        "owner": "web",
        "expected": "A product detail page opens with #varient-price and a tappable 'Start Bargaining' CTA.",
        "repro": [
            "Open any category on https://gajab.com/ and click a product.",
            "Confirm the PDP shows the price block and the 'Start Bargaining' button.",
            "Some listings have no bargain CTA (not bargainable) — the flow retries with another product.",
        ],
    },
    "home_products_populate": {
        "owner": "web",
        "expected": "The home page product grid populates with cards.",
        "repro": [
            "Open https://gajab.com/ in a fresh incognito window.",
            "Confirm product cards render in the first grid.",
            "An empty grid means the feed API returned nothing.",
        ],
    },
    "category_all_load": {
        "owner": "web",
        "expected": "https://gajab.com/product-list/all lists products within the time budget.",
        "repro": [
            "Open https://gajab.com/product-list/all.",
            "Confirm products render; note how long the grid takes to appear.",
            "Slow loads here are usually the heavy background SVGs / blocking JS.",
        ],
    },
    "category_load": {
        "owner": "web",
        "expected": "A category listing loads and its product grid populates within the time budget.",
        "repro": [
            "Open any category, e.g. https://gajab.com/product-list/toys-games.",
            "Confirm the grid renders and note the load time.",
        ],
    },
    "page_my_bargains": {
        "owner": "web",
        "expected": "https://gajab.com/my-bargains loads the user's bargains while logged in.",
        "repro": [
            "Log in and open https://gajab.com/my-bargains.",
            "Confirm bargain cards render.",
            "A redirect to login means the stored session expired.",
        ],
    },
    "page_alerts_orders": {
        "owner": "web",
        "expected": "The alerts and orders pages load their lists while logged in.",
        "repro": [
            "Log in and open https://gajab.com/alerts-list, then the orders page.",
            "Confirm both lists render.",
        ],
    },

    # ── Monitor meta ───────────────────────────────────────────────────────
    "monitor_total_duration_ms": {
        "owner": "infra",
        "expected": "The whole monitoring cycle finishes within its time budget, with no failing checks.",
        "repro": [
            "Open the latest run in this dashboard and scan every checkpoint for FAIL/DEGRADED.",
            "The 'observed' line lists how many issues this cycle found.",
            "A long duration with all checks passing is normal on Lighthouse/feature runs.",
        ],
    },
    "price_mappings": {
        "owner": "api",
        "expected": "Price mappings endpoint returns data.",
        "repro": [
            "curl -sS 'https://gajab.com/api/price-mappings'",
            "An empty/errored body means the mapping store or API is broken.",
        ],
    },
}

DEFAULT_OWNER = "unknown"

# Generic fallback so EVERY failure carries reproduction steps, even for checks
# that have no bespoke registry entry yet.
GENERIC_REPRO = [
    "Open the surface named in this check (see the observed value / URL below).",
    "Repeat the action this check performs, at roughly the same time of day.",
    "Watch the browser Network tab or app logs while it runs and note any error status.",
    "Compare what you see against the 'expected' text above; if it differs, the failure is real.",
    "If it passes manually, the check is flaky — note the run time and re-run the monitor.",
]
GENERIC_EXPECTED = "The monitored surface should respond normally and within its time budget."


def _repro_text(steps: Iterable[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


_PREFIXES = ("mweb_", "web_", "android_", "happy_flow_", "native_", "server_", "api_")


def lookup(step: str) -> dict:
    """Find the registry entry for a step, tolerating platform/flow prefixes.

    Step names arrive as `home_load`, `mweb_home_load`, `android_home_load`,
    `happy_flow_checkout_nav`, `api_Home Page` ... so try the exact key first,
    then strip known prefixes, then fall back to the longest registry key
    contained in the name.
    """
    if not step:
        return {}
    if step in REPRO:
        return REPRO[step]
    low = step.lower()
    for prefix in _PREFIXES:
        if low.startswith(prefix):
            candidate = low[len(prefix):]
            if candidate in REPRO:
                return REPRO[candidate]
    # strip every prefix combination (e.g. "mweb_checkout_nav")
    for prefix in _PREFIXES:
        low2 = low[len(prefix):] if low.startswith(prefix) else low
        if low2 in REPRO:
            return REPRO[low2]
    matches = [k for k in REPRO if k.lower() in low]
    if matches:
        return REPRO[max(matches, key=len)]
    # Dynamic step names, e.g. "mweb_category_toys-games_load" — the slug is
    # per-run, so collapse "category_<slug>_load" to the registry key
    # "category_load".
    import re as _re
    collapsed = _re.sub(r"category_[a-z0-9\-]+_load", "category_load", low)
    if collapsed in REPRO:
        return REPRO[collapsed]
    collapsed2 = _re.sub(r"_toys-games_", "_", low)
    if collapsed2 in REPRO:
        return REPRO[collapsed2]
    return {}


def template(step: str) -> dict:
    """The checkpoint template — what it verifies and how to reproduce it.

    Always returned (generic fallback for unknown steps) so the dashboard can
    show a plan of action for EVERY checkpoint, not just the failing ones.
    """
    meta = lookup(step)
    steps = meta.get("repro") or GENERIC_REPRO
    return {
        "expected": meta.get("expected") or GENERIC_EXPECTED,
        "owner": meta.get("owner", DEFAULT_OWNER),
        "repro": _repro_text(steps),
    }


def explain(step: str, status: str, observed: str = "") -> dict:
    """Build the explainable fields for a monitor result.

    Returns `expected`, `observed`, `owner` and `repro` (a numbered list), and
    `explainable` only when the step did not pass. Unknown steps fall back to
    generic steps so every checkpoint has a plan of action.
    """
    payload = template(step)
    payload["observed"] = observed or "(not captured)"
    if status not in ("pass",):
        payload["explainable"] = True
    return payload


def to_slack_block(result: dict, url: str | None = None) -> str:
    """Render one failed/degraded result as a Slack-friendly message block."""
    lines = [
        f"*{result.get('step', '?')}* — {result.get('status', '?').upper()}",
        f"• observed: {result.get('observed') or result.get('detail') or '(n/a)'}",
        f"• expected: {result.get('expected', '(not documented)')}",
        f"• owner: {result.get('owner', DEFAULT_OWNER)}",
    ]
    if result.get("repro"):
        lines.append("*reproduce:*")
        lines.append(result["repro"])
    if url:
        lines.append(f"• evidence: {url}")
    return "\n".join(lines)
