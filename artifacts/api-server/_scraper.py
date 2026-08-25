#!/usr/bin/env python3
from __future__ import annotations
"""
Flipkart product scraper — called as subprocess by Node.js server.

Uses curl_cffi with Chrome TLS impersonation, falls back to ScraperAPI.
Env vars (passed from Node.js):
  SCRAPER_PROXY        — HTTP/HTTPS proxy URL
  SCRAPING_SERVICE_URL — ScraperAPI base URL
"""
import json
import os
import re
import sys
import traceback
import warnings
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

PROXY = os.environ.get("SCRAPER_PROXY", "")
SCRAPING_SERVICE_URL = os.environ.get("SCRAPING_SERVICE_URL", "")
# Chrome CDP instance used for scraping (runs through the Webshare proxy).
SCRAPE_CDP_URL = os.environ.get("SCRAPE_CDP_URL", "http://localhost:9223")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]


def scrape(url: str, attempt: int = 1, max_attempts: int = 3) -> dict:
    ua = USER_AGENTS[(attempt - 1) % len(USER_AGENTS)]

    html = _try_direct(url, ua)
    if html:
        return _parse_html(html, url)
    html = _try_playwright(url, ua)
    if html:
        return _parse_html(html, url)

    if attempt < max_attempts:
        delay = attempt * 5
        import time
        time.sleep(delay)
        return scrape(url, attempt=attempt + 1, max_attempts=max_attempts)

    return _blocked("All fetch methods failed")


def _is_bot_page(html: str) -> bool:
    if len(html) < 5000:
        return True
    checks = [
        "sec-if-cpt-container" in html,
        "_abck" in html[:2000],
        # "Access Denied" is intentionally NOT here — Flipkart's normal pages embed
        # the string "Access Denied" inside a JSON error-widget, causing false positives.
        "cf-browser-verification" in html,
        "/cdn-cgi/" in html[:2000],
    ]
    return any(checks)


def _try_playwright(url: str, ua: str = "") -> str:
    """Fetch page HTML via the proxy-enabled Chrome CDP (real browser, passes bot detection)."""
    try:
        import urllib.request
        from playwright.sync_api import sync_playwright

        urllib.request.urlopen(f"{SCRAPE_CDP_URL}/json/version", timeout=5)

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(SCRAPE_CDP_URL)
            ctx = browser.new_context(
                user_agent=ua or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            html = page.content()
            ctx.close()
            if not _is_bot_page(html):
                return html
    except Exception:
        pass
    return ""


def _try_direct(url: str, ua: str = "") -> str:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return ""
    headers = {
        "User-Agent": ua or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        kwargs: dict = {"headers": headers, "impersonate": "chrome", "timeout": 30}
        if PROXY:
            kwargs["proxies"] = {"https": PROXY, "http": PROXY}
        resp = curl_requests.get(url, **kwargs)
        if resp.status_code == 200 and not _is_bot_page(resp.text):
            return resp.text
    except Exception:
        pass
    return ""


def _via_scraping_service(url: str) -> dict:
    import requests

    try:
        base = SCRAPING_SERVICE_URL.rstrip("/?&")
        if "?url=" in base or "&url=" in base or "url=" in base:
            target = f"{base}{url}"
        elif "?" in base:
            target = f"{base}&url={url}"
        else:
            target = f"{base}?url={url}"

        resp = requests.get(target, timeout=45)
        if resp.status_code == 200 and not _is_bot_page(resp.text) and len(resp.text) > 10000:
            return _parse_html(resp.text, url)
        return _blocked("Scraping service returned non-product page")
    except Exception as e:
        return _blocked(f"Scraping service error: {e}")


def _parse_html(html: str, url: str) -> dict:
    result = {"status": "failed", "title": None, "description": None, "meta_description": None, "images": [], "price": None, "dimensions": None, "weight": None, "gst": None, "hsn": None, "specifications": None, "url": url}

    data = _try_next_data(html)
    if data:
        result.update(data)

    if not result["title"]:
        data = _try_jsonld(html)
        if data:
            result.update(data)

    if result["status"] == "failed" or result["status"] == "sentinel":
        fallback = _regex_fallback(html)
        if fallback["title"]:
            result.update(fallback)

    specs = _try_specs(html) or _try_specs_from_html(html) or _try_specs_from_dom(url)
    if specs:
        if not result.get("specifications"):
            result["specifications"] = specs["specifications"]
        if not result.get("dimensions") and specs.get("dimensions"):
            result["dimensions"] = specs["dimensions"]
        if not result.get("weight") and specs.get("weight"):
            result["weight"] = specs["weight"]
        if not result.get("gst") and specs.get("gst"):
            result["gst"] = specs["gst"]
        if not result.get("hsn") and specs.get("hsn"):
            result["hsn"] = specs["hsn"]

    actual_desc = _try_description(html)
    if actual_desc:
        result["description"] = actual_desc
    long_texts = _try_long_texts(html)
    if long_texts:
        real_texts = [t for t in long_texts if not re.search(r'color of the product may vary|picture displayed|product\'s dimensions|fit through the entrance|civil work|drilling holes|wiping the surface|assembly is required', t, re.IGNORECASE)]
        if real_texts:
            combined = " ".join(real_texts)
            if len(combined) > len(result.get("description", "")):
                result["description"] = combined
    if (not result.get("description") or len(result.get("description", "")) < 100) and re.search(r'>\s*Description\s*<', html, re.IGNORECASE | re.DOTALL):
        dom_desc = _try_description_from_dom(url)
        if dom_desc:
            result["description"] = dom_desc
    if not result.get("description") and result.get("meta_description"):
        result["description"] = result["meta_description"]

    if not result["title"]:
        return _blocked("Could not extract product data")
    result["status"] = "success"
    return result


def _is_blocked(html: str) -> bool:
    return False


def _blocked(msg: str) -> dict:
    return {
        "status": "blocked",
        "error": msg,
        "title": None,
        "description": None,
        "meta_description": None,
        "images": [],
        "price": None,
        "dimensions": None,
        "weight": None,
        "gst": None,
        "hsn": None,
        "specifications": None,
    }


def _try_next_data(html: str) -> dict | None:
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    props = data.get("props", {}).get("pageProps", {})
    product_data = (
        props.get("productData")
        or props.get("product")
        or props.get("data")
        or {}
    )
    if not product_data:
        return None

    title = product_data.get("title") or product_data.get("name") or ""
    description = product_data.get("description") or ""
    imgs = _extract_images(product_data, html)
    price = product_data.get("price") or ""
    specs = _get_specs(product_data)

    return {
        "status": "success",
        "title": _clean(title[:500]),
        "description": _clean(description[:2000]) if description else None,
        "meta_description": _clean(description[:2000]) if description else None,
        "images": imgs[:10],
        "price": str(price) if price else None,
        "dimensions": _find_spec(specs, ["dimension", "size"]),
        "weight": _find_spec(specs, ["weight"]),
        "gst": _find_spec(specs, ["gst", "tax"]),
        "hsn": _find_spec(specs, ["hsn"]),
    }


def _get_specs(product_data: dict) -> list:
    specs = product_data.get("specifications") or product_data.get("specs") or []
    if isinstance(specs, dict):
        flat = []
        for v in specs.values():
            if isinstance(v, list):
                flat.extend(v)
            elif isinstance(v, dict):
                flat.append(v)
        return flat
    return specs if isinstance(specs, list) else []


def _find_spec(specs: list, keys: list[str]) -> str | None:
    for s in specs:
        if isinstance(s, dict):
            k = str(s.get("key", s.get("label", ""))).lower()
            v = str(s.get("value", ""))
            for key in keys:
                if key in k and v:
                    return v
            name = str(s.get("name", "")).lower()
            for key in keys:
                if key in name:
                    return str(s.get("text", s.get("value", ""))) or None
    return None


def _try_jsonld(html: str) -> dict | None:
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue

        if isinstance(data, list):
            if not data:
                continue
            data = data[0]

        name = data.get("name", "")
        if not name:
            continue

        desc = data.get("description", "")
        offers = data.get("offers", {}) or {}
        price = offers.get("price", "") if isinstance(offers, dict) else ""
        img = data.get("image", "")
        imgs = [img] if isinstance(img, str) and img else (img if isinstance(img, list) else [])

        return {
            "status": "success",
            "title": _clean(name[:500]),
            "description": _clean(desc[:2000]) if desc else None,
            "meta_description": _clean(desc[:2000]) if desc else None,
            "images": imgs[:10] if imgs else _extract_images_from_html(html),
            "price": str(price) if price else None,
            "dimensions": None,
            "weight": None,
            "gst": None,
            "hsn": None,
        }
    return None


def _regex_fallback(html: str) -> dict:
    title = None
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    # Flipkart now renders client-side: the real title lives in JSON (ogTitle).
    if not title:
        m = re.search(r'"ogTitle"\s*:\s*"([^"]{5,200})"', html)
        if m:
            title = m.group(1).strip()
    if not title:
        m = re.search(r'"seoData"\s*:\s*\{.*?"title"\s*:\s*"([^"]{5,200})"', html, re.DOTALL)
        if m:
            title = m.group(1).strip()

    # Strip Flipkart's SEO suffixes so the title is the clean product name
    if title:
        title = re.sub(r"\s*Online at Best Prices?.*$", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s*\|\s*Flipkart.*$", "", title, flags=re.IGNORECASE).strip()

    imgs = _extract_images_from_html(html)
    price = None
    m = re.search(r"[₹]\s*([\d,]+(?:\.\d{2})?)", html)
    if m:
        price = f"₹{m.group(1)}"

    return {
        "status": "success" if title else "failed",
        "title": _clean(title[:500]) if title else None,
        "description": None,
        "images": imgs[:10],
        "price": price,
        "dimensions": None,
        "weight": None,
        "gst": None,
        "hsn": None,
    }


def _try_specs_from_dom(url: str) -> dict | None:
    """Extract specifications from the visible DOM via Playwright."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            for btn_text in ["All details", "More about", "Features", "Specifications", "View All Details"]:
                try:
                    btn = page.query_selector(f"text={btn_text}")
                    if btn:
                        btn.click()
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    continue
            pairs = page.evaluate("""() => {
                const result = [];
                const isGood = (s) => s && s.length > 0 && s.length < 60 && !/^[₹]/.test(s);

                // Try finding a specs table grid — look for adjacent label-value divs
                const allDivs = document.querySelectorAll('div');
                for (let i = 0; i < allDivs.length - 1; i++) {
                    const label = (allDivs[i].textContent || '').trim();
                    const value = (allDivs[i + 1].textContent || '').trim();
                    if (!label || !value || label === value) continue;
                    if (label.length > 60 || value.length > 300) continue;
                    if (/^[₹]/.test(label) || /^[₹]/.test(value)) continue;
                    // Label should be short, value should be longer or both reasonable
                    if (label.length < value.length && label.length > 1) {
                        const siblings = allDivs[i].parentElement === allDivs[i + 1].parentElement;
                        const adjacent = allDivs[i].nextElementSibling === allDivs[i + 1];
                        if (siblings && adjacent && !result.some(p => p[0] === label)) {
                            result.push([label, value]);
                            i++;
                        }
                    }
                }
                if (result.length > 0) return result;

                // Fallback: find a "Specifications" heading and look for the grid after it
                const specWords = ['specifications', 'product details', 'features', 'general', 'in the box'];
                for (const el of document.querySelectorAll('h1,h2,h3,h4,h5,h6,strong,span[class*=\"title\"],div[class*=\"heading\"]')) {
                    const t = (el.textContent || '').trim().toLowerCase();
                    if (!t || !specWords.some(w => t.includes(w))) continue;
                    let current = el.parentElement.nextElementSibling || el.nextElementSibling;
                    for (let s = 0; s < 6 && current; s++) {
                        const children = current.querySelectorAll(':scope > div, :scope > span, :scope > li');
                        if (children.length >= 4) {
                            for (let i = 0; i < children.length - 1; i++) {
                                const a = (children[i].textContent || '').trim();
                                const b = (children[i + 1].textContent || '').trim();
                                if (isGood(a) && b && b.length < 300 && a !== b && !/^[₹]/.test(b) && !result.some(p => p[0] === a)) {
                                    result.push([a, b]);
                                    i++;
                                }
                            }
                            if (result.length > 0) return result;
                        }
                        current = current.nextElementSibling;
                    }
                }
                return result;
            }""")
            specs = {label: value for label, value in pairs}
            context.close()
            browser.close()
            if specs:
                result = {"specifications": specs}
                for label, value in specs.items():
                    ll = label.lower()
                    if "weight" in ll:
                        result["weight"] = value
                    if "dimension" in ll or ll in ("size", "product dimensions"):
                        result["dimensions"] = value
                    if "gst" in ll:
                        result["gst"] = value
                    if "hsn" in ll:
                        result["hsn"] = value
                return result
    except Exception:
        pass
    return None


def _try_long_texts(html: str) -> list[str]:
    """Extract long descriptive text blocks from expanded HTML."""
    import html as _html
    texts = []
    for m in re.finditer(
        r'>([^<]{100,5000})</div>',
        html,
    ):
        raw = m.group(1)
        text = _html.unescape(raw.strip())
        if 100 < len(text) < 5000 and not re.match(r'^[₹\d]', text):
            texts.append(text)
    if texts:
        seen = set()
        unique = []
        for t in texts:
            if t[:50] not in seen:
                seen.add(t[:50])
                unique.append(t)
        return unique
    return []


def _try_description_from_dom(url: str) -> str | None:
    """Extract description from the expanded DOM via Playwright."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            for btn_text in ["All details", "Features, description and more", "More about"]:
                try:
                    btn = page.query_selector(f"text={btn_text}")
                    if btn:
                        btn.click()
                        page.wait_for_timeout(1000)
                except Exception:
                    continue
            tabs = page.query_selector_all('[tabindex="0"]')
            for t in tabs:
                try:
                    t.click()
                    page.wait_for_timeout(500)
                except Exception:
                    continue
            desc = page.evaluate("""() => {
                // Look for a "Description" or "About this item" heading followed by paragraph text
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    if ((/^description$/i.test(t) || /^about this item/i.test(t)) && el.children.length === 0) {
                        let next = el.parentElement?.nextElementSibling;
                        if (!next) next = el.closest('div,section')?.querySelector('p,div[class*="text"],span[class*="text"]');
                        if (next) {
                            const text = next.textContent.trim();
                            if (text.length > 50 && text.length < 1000) return text;
                        }
                    }
                }
                return null;
            }""")
            context.close()
            browser.close()
            if desc and len(desc) > 80:
                return _clean(desc)
    except Exception:
        pass
    return None


def _try_specs_from_html(html: str) -> dict | None:
    """Extract specs from the rendered HTML after 'All details' expansion."""
    import html as _html
    specs = {}

    # Look for any table-like structure: adjacent <td>, <th>, or <div> pairs in a grid
    for m in re.finditer(
        r'<div[^>]*>([^<]{1,60})</div>\s*<div[^>]*>([^<]{1,300})</div>',
        html,
    ):
        label = _html.unescape(m.group(1).strip())
        value = _html.unescape(m.group(2).strip())
        if label and value and label != value and len(label) > 1 and len(label) < 60:
            if not re.match(r'^[₹]', label) and not re.match(r'^[₹]', value):
                specs[label] = value

    if not specs:
        # Try li-based or dt/dd patterns
        for m in re.finditer(
            r'<(?:dt|th|li)[^>]*>([^<]{1,60})</(?:dt|th|li)>\s*<(?:dd|td)[^>]*>([^<]{1,300})</(?:dd|td)>',
            html,
        ):
            label = _html.unescape(m.group(1).strip())
            value = _html.unescape(m.group(2).strip())
            if label and value and label != value and len(label) > 1 and len(label) < 60:
                if not re.match(r'^[₹]', label) and not re.match(r'^[₹]', value):
                    specs[label] = value

    if specs:
        result = {"specifications": specs}
        for label, value in specs.items():
            ll = label.lower()
            if "weight" in ll:
                result["weight"] = value
            if "dimension" in ll or ll in ("size", "product dimensions"):
                result["dimensions"] = value
            if "gst" in ll:
                result["gst"] = value
            if "hsn" in ll:
                result["hsn"] = value
        return result
    return None


def _try_specs(html: str) -> dict | None:
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    slots = state.get("multiWidgetState", {}).get("widgetsData", {}).get("slots", [])
    all_specs: dict[str, str] = {}

    def find_spec_grid_in_value(obj, depth=0):
        if depth > 25 or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            if "specification" in k.lower() and isinstance(v, dict):
                inner = v.get("value", {})
                if isinstance(inner, dict):
                    grid = inner.get("gridData_0", {}).get("value", [])
                    if not isinstance(grid, list):
                        continue
                    for cat_item in grid:
                        if not isinstance(cat_item, dict):
                            continue
                        civ = cat_item.get("value", {})
                        for ci_k, ci_v in civ.items():
                            if isinstance(ci_v, dict):
                                inner_gd = ci_v.get("value", {}).get("gridData_0", {}).get("value", [])
                                if not isinstance(inner_gd, list):
                                    continue
                                for spec_item in inner_gd:
                                    if not isinstance(spec_item, dict):
                                        continue
                                    sv = spec_item.get("value", {})
                                    if not isinstance(sv, dict):
                                        continue
                                    label = _get_text(sv.get("label_0", {}))
                                    if not label:
                                        continue
                                    val1 = _get_text(sv.get("label_1", {}))
                                    val2 = _get_text(sv.get("label_2", {}))
                                    val = val2 or val1
                                    if val and val != label:
                                        all_specs[label] = val
            else:
                find_spec_grid_in_value(v, depth + 1)

    for slot in slots:
        dls = slot.get("slotData", {}).get("widget", {}).get("data", {}).get("dlsData", {})
        find_spec_grid_in_value(dls)

    if not all_specs:
        return None

    result: dict = {"specifications": all_specs}
    for label, value in all_specs.items():
        label_lower = label.lower()
        if "weight" in label_lower:
            result["weight"] = value
        if "dimension" in label_lower or label_lower in ("size", "product dimensions"):
            result["dimensions"] = value
        if "gst" in label_lower:
            result["gst"] = value
        if "hsn" in label_lower:
            result["hsn"] = value

    return result


def _try_description(html: str) -> str | None:
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    slots = state.get("multiWidgetState", {}).get("widgetsData", {}).get("slots", [])

    def find_desc_text(obj, depth=0):
        if depth > 20 or not isinstance(obj, dict):
            return None
        for k, v in obj.items():
            if "description" in k.lower() and isinstance(v, dict):
                inner = v.get("value", {})
                if isinstance(inner, dict):
                    label = inner.get("label_0", {})
                    if isinstance(label, dict):
                        text = label.get("value", {}).get("text", "")
                        if isinstance(text, str) and len(text) > 50:
                            return text
            result = find_desc_text(v, depth + 1)
            if result:
                return result
        return None

    for slot in slots:
        dls = slot.get("slotData", {}).get("widget", {}).get("data", {}).get("dlsData", {})
        text = find_desc_text(dls)
        if text:
            return _clean(text)
    return None


def _get_text(node: dict) -> str:
    raw = node.get("value", {}).get("text", "")
    if isinstance(raw, list):
        parts = []
        for r in raw:
            if isinstance(r, str):
                parts.append(r.strip("[]'\" "))
        return " ".join(parts).strip()
    if isinstance(raw, str):
        return raw.strip("[]'\" ").strip()
    return ""


def _extract_images(product_data: dict, html: str) -> list[str]:
    imgs = product_data.get("images", [])
    if isinstance(imgs, list) and imgs:
        result = []
        for i in imgs:
            if isinstance(i, str):
                result.append(i if i.startswith("http") else f"https:{i}")
            elif isinstance(i, dict):
                u = i.get("url", i.get("src", ""))
                if u:
                    result.append(u if u.startswith("http") else f"https:{u}")
        return result
    return _extract_images_from_html(html)


def _extract_images_from_html(html: str) -> list[str]:
    urls = re.findall(r'https://rukminim[^"\'\\\s]+(?:\.(?:jpg|jpeg|png|webp))?', html)
    seen = set()
    unique = []
    for u in urls:
        u_clean = u.split("?")[0]
        if u_clean not in seen:
            seen.add(u_clean)
            unique.append(u)
    return unique[:10]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print(json.dumps({"status": "failed", "error": "No URL provided"}))
        sys.exit(1)

    try:
        result = scrape(url)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
        sys.exit(1)
