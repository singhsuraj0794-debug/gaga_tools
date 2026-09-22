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
    "products_status": {
        "owner": "api",
        "expected": "The products status endpoint reports a healthy catalogue.",
        "repro": [
            "curl -sS https://gajab.com/api/products/status",
            "Compare the reported counts/staleness with the expected range.",
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


def _repro_text(steps: Iterable[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def explain(step: str, status: str, observed: str = "") -> dict:
    """Build the explainable fields for a monitor result.

    Returns a dict with `expected`, `observed`, `owner` and `repro` (a
    human-readable numbered list). Safe for unknown steps.
    """
    meta = REPRO.get(step) or {}
    expected = meta.get("expected", "No expectation documented for this step.")
    steps = meta.get("repro") or [
        "Open the monitored surface and repeat the action this step performs.",
        "Compare what you see against the expected behaviour above.",
    ]
    payload = {
        "expected": expected,
        "observed": observed or "(not captured)",
        "owner": meta.get("owner", DEFAULT_OWNER),
        "repro": _repro_text(steps),
    }
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
