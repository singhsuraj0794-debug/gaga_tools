"""
Root Cause Analysis (RCA) generator for monitoring failures and degradations.
Provides actionable insights for the tech team.
"""

from __future__ import annotations

RCA_TEMPLATES: dict[str, dict] = {
    "home_load": {
        "slow": {
            "summary": "Home page took >10s to load (502 error or slow server response)",
            "probable_causes": [
                "Backend server returned HTTP 502 Bad Gateway",
                "CDN or origin server latency",
                "Large unoptimized assets (hero banners, images)",
                "Server-side rendering (SSR) taking too long",
            ],
            "actions": [
                "Check server logs for 502 errors at the time of failure",
                "Verify CDN (resize.gajab.com) is healthy — currently returning HTTP 500",
                "Optimize hero banner images — consider WebP format and lazy loading",
                "Review SSR performance / database query times",
            ],
            "severity": "high",
        },
        "title_missing": {
            "summary": "Page loaded but title tag was empty — likely a server error or redirect loop",
            "probable_causes": [
                "Backend returned a status code error (502/503) causing empty response",
                "JavaScript failed to execute, leaving title unfilled",
                "Redirect chain broken or looped",
            ],
            "actions": [
                "Check HTTP status code for the request — if 502, investigate upstream server",
                "Verify the page renders correctly in a browser with the same URL",
                "Check browser console for JS errors preventing hydration",
            ],
            "severity": "high",
        },
    },
    "category_load": {
        "slow": {
            "summary": "Category page took >5s to load",
            "probable_causes": [
                "API calls for product listing are slow",
                "Large number of product images loading without optimization",
                "Client-side filtering/sorting causing re-renders",
            ],
            "actions": [
                "Profile product listing API response times",
                "Implement image lazy loading and placeholder blur",
                "Consider server-side pagination instead of client-side",
            ],
            "severity": "medium",
        },
    },
    "product_detail_load": {
        "slow": {
            "summary": "Product detail page took >5s to load",
            "probable_causes": [
                "Product data API (gateway) response slow",
                "Large product images not optimized",
                "Bargain flow JavaScript bundle loading slowly",
                "Multiple variant/price calculations on render",
            ],
            "actions": [
                "Profile product store API response times",
                "Optimize and lazy-load product gallery images",
                "Review bargain flow React component performance",
                "Consider code splitting for bargain UI bundle",
            ],
            "severity": "medium",
        },
    },
    "bargain_flow": {
        "slider_not_found": {
            "summary": "Bargain slider UI element not detected on the page",
            "probable_causes": [
                "React modal sheet failed to render slider component",
                "Pincode/location dialog blocked the bargain modal",
                "JavaScript error prevented bargain UI from loading",
                "Race condition in React component mounting",
            ],
            "actions": [
                "Check console for React rendering errors during bargain modal open",
                "Verify pincode/location dialog is dismissed before bargain starts",
                "Increase bargain modal wait timeout if page is slow",
                "Test manually: open Start Bargaining and verify slider appears",
            ],
            "severity": "medium",
        },
        "offer_not_found": {
            "summary": "Offer Your Price button not found in bargain modal",
            "probable_causes": [
                "Bargain modal rendered without offer step (slider not set)",
                "Slider value change didn't trigger React state update",
                "UI differs for this product category",
            ],
            "actions": [
                "Verify slider onChange event fires correctly",
                "Check if different product categories have different bargain UI",
                "Ensure React props are being accessed via __reactProps$ key",
            ],
            "severity": "medium",
        },
    },
    "lighthouse": {
        "si_ms": {
            "summary": "Speed Index exceeds threshold — page content paints too slowly",
            "probable_causes": [
                "Large hero images or above-fold assets not loaded fast enough",
                "CSS/JS render-blocking resources",
                "Server response time slow (TTFB high)",
                "Third-party scripts (Google Maps, analytics) blocking render",
            ],
            "actions": [
                "Optimize above-fold images — use responsive sizes and WebP",
                "Inline critical CSS and defer non-critical JS",
                "Reduce TTFB by optimizing backend queries and CDN caching",
                "Lazy-load Google Maps and non-essential third-party scripts",
            ],
            "severity": "high",
        },
        "lcp_ms": {
            "summary": "Largest Contentful Paint exceeds 2.5s — main content loads slowly",
            "probable_causes": [
                "Hero image or main title not prioritized in loading",
                "Slow server response time",
                "Render-blocking resources delaying paint",
                "Image not properly sized or next-gen format not used",
            ],
            "actions": [
                "Add fetchpriority='high' to hero image",
                "Preload LCP image with <link rel='preload'>",
                "Optimize image with WebP/AVIF and proper dimensions",
                "Reduce server response time (TTFB)",
            ],
            "severity": "high",
        },
        "cls": {
            "summary": "Cumulative Layout Shift exceeds 0.1 — page content moves during load",
            "probable_causes": [
                "Images or ads without explicit width/height attributes",
                "Dynamic content injected after layout (banners, recommendations)",
                "Web fonts causing FOIT/FOUT",
                "Late-loading embeds pushing content down",
            ],
            "actions": [
                "Add explicit width/height attributes to all images and banners",
                "Reserve space for dynamic content (ads, recommendations)",
                "Use font-display: swap for web fonts",
                "Set min-height on lazy-loaded sections",
            ],
            "severity": "high",
        },
        "tbt_ms": {
            "summary": "Total Blocking Time exceeds 300ms — main thread blocked by long tasks",
            "probable_causes": [
                "Heavy JavaScript bundles executing on load",
                "Third-party scripts blocking main thread",
                "Long-running API calls on main thread",
                "Inefficient React re-renders or state updates",
            ],
            "actions": [
                "Code-split JavaScript bundles — defer non-critical scripts",
                "Move third-party scripts to web workers or async load",
                "Optimize React components — use React.memo and useMemo",
                "Move long computations to setTimeout or requestIdleCallback",
            ],
            "severity": "high",
        },
    },
    "checkout_flow": {
        "no_pay_button": {
            "summary": "No Pay button found on checkout page — cart likely empty",
            "probable_causes": [
                "Bargained item was not automatically added to cart",
                "Checkout page requires cart items from a previous session",
                "Cart API endpoint returned empty",
            ],
            "actions": [
                "Verify if bargained items appear in cart after offer submission",
                "Check if cart requires seller acceptance before adding items",
                "Review cart/checkout flow on gajab.com for bargained items",
            ],
            "severity": "low",
        },
    },
    "bargain2_flow": {
        "counter_offer_missing": {
            "summary": "No counter-offer appeared within 30s of submitting extreme low offer",
            "probable_causes": [
                "Seller automated response system didn't trigger",
                "Product may not have automated counter-offer enabled",
                "Pincode/location dialog blocked bargain modal from rendering",
                "Server processing delay >30s for counter-offer generation",
            ],
            "actions": [
                "Verify product has automated counter-offer enabled on seller side",
                "Check if pincode/location dialog was properly dismissed",
                "Consider increasing counter-offer wait timeout to 45s",
                "Manually test with this product to confirm counter-offer behavior",
            ],
            "severity": "medium",
        },
        "slider_not_found": {
            "summary": "Bargain slider not found in second bargain flow",
            "probable_causes": [
                "Pincode/location dialog blocked the bargain modal from opening",
                "Start Bargaining click didn't register due to overlay",
                "React modal render delayed beyond retry timeout",
            ],
            "actions": [
                "Ensure pincode dialog is dismissed before clicking Start Bargaining",
                "Increase retry count for slider detection in Bargain 2",
                "Verify Start Bargaining button is clickable after dialog dismiss",
            ],
            "severity": "medium",
        },
    },
    "server": {
        "gatewayservice.gajab.com_404": {
            "summary": "Gateway service endpoint returns HTTP 404 — endpoint may have changed",
            "probable_causes": [
                "API endpoint path changed on gateway service",
                "Endpoint requires specific parameters that are missing",
                "Service migration changed the URL structure",
            ],
            "actions": [
                "Verify correct API endpoint for OTP/mobile-send service",
                "Check gateway service documentation for updated endpoints",
                "Update monitoring URL if endpoint path has changed",
            ],
            "severity": "medium",
        },
        "resize.gajab.com_500": {
            "summary": "Image CDN returning HTTP 500 — image serving degraded",
            "probable_causes": [
                "CDN origin server error",
                "Image resizing service crashed or overloaded",
                "Invalid request to CDN root (may need specific image path)",
            ],
            "actions": [
                "Check CDN provider health dashboard",
                "Verify image resizing service is operational",
                "Test with a specific image URL to isolate the issue",
            ],
            "severity": "high",
        },
    },
}


def generate_rca(check_name: str, failure_detail: str, console_errors: list | None = None) -> dict:
    """Generate root cause analysis for a given failure/degradation."""
    rca = {
        "summary": failure_detail,
        "probable_causes": [],
        "actions": [],
        "console_errors": console_errors or [],
        "severity": "medium",
    }

    # Match against known templates
    for key, templates in RCA_TEMPLATES.items():
        if key in check_name.lower():
            for template_key, template in templates.items():
                if template_key in failure_detail.lower() or template_key in check_name.lower():
                    rca["probable_causes"].extend(template.get("probable_causes", []))
                    rca["actions"].extend(template.get("actions", []))
                    if template.get("severity") == "high":
                        rca["severity"] = "high"
                    break
            break

    # Add fallback for unmatched
    if not rca["probable_causes"]:
        rca["probable_causes"] = ["Automated check detected a failure — manual investigation recommended"]
        rca["actions"] = ["Check the monitoring dashboard for screenshots and session recording",
                          "Review application logs for errors at the time of failure",
                          "Manually reproduce the issue in a browser"]

    # Deduplicate
    rca["probable_causes"] = list(dict.fromkeys(rca["probable_causes"]))
    rca["actions"] = list(dict.fromkeys(rca["actions"]))

    return rca


def format_rca_for_slack(rca: dict, check_name: str) -> str:
    """Format RCA for Slack alert message."""
    lines = [
        f"🔍 *RCA: {check_name}*",
        f"📋 *Summary:* {rca['summary']}",
        f"⚠️ *Severity:* {rca['severity'].upper()}",
    ]
    if rca["console_errors"]:
        lines.append(f"🖥️ *Console Errors ({len(rca['console_errors'])}):*")
        for ce in rca["console_errors"][:3]:
            lines.append(f"   `{ce['text'][:100]}`")

    lines.append(f"\n*🔧 Probable Causes:*")
    for c in rca["probable_causes"]:
        lines.append(f"• {c}")

    lines.append(f"\n*✅ Action Items:*")
    for a in rca["actions"]:
        lines.append(f"• {a}")

    return "\n".join(lines)
