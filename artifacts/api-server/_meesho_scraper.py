#!/usr/bin/env python3
from __future__ import annotations
"""
Meesho product/scraper — called as subprocess by Node.js server.

Actions:
  extract  <store-url>   — Extract all product links from a Meesho store/shop page
  scrape   <product-url> — Extract full product details from a single product page

Env vars:
  SCRAPER_PROXY            — HTTP/HTTPS proxy URL
  SCRAPING_SERVICE_URL     — ScraperAPI base URL (for fallback)
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback

PROXY = os.environ.get("MEESHO_PROXY", "") or os.environ.get("SCRAPER_PROXY", "")
# Link extraction uses a dedicated LOCAL-IP Chrome (no proxy) — fast & reliable.
EXTRACT_CDP_URL = os.environ.get("EXTRACT_CDP_URL", "http://localhost:9225")
# Product scraping uses the Webshare-proxied Chrome so we don't get IP-blocked.
SCRAPE_CDP_URL = os.environ.get("SCRAPE_CDP_URL", "http://localhost:9223")
SCRAPE_DO_TOKEN = os.environ.get("SCRAPE_DO_TOKEN", "")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")
SCRAPING_SERVICE_URL = os.environ.get("SCRAPING_SERVICE_URL", "") or f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url="
SCRAPPLEY_API_KEY = os.environ.get("SCRAPPLEY_API_KEY", "")

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".product_cache.json")
# Bump when the extraction logic changes: entries written by an older version are
# dropped on load, otherwise a persistent cache keeps serving results produced by
# code that no longer exists (this hid the source_category_path fix completely).
CACHE_VERSION = "v4-no-empty-pages"
_cache = {}  # url_hash -> product dict
# The local scraper server is a ThreadingHTTPServer: several product scrapes run
# at once (SCRAPE_CONCURRENCY) and each success mutates _cache and saves it.
_cache_lock = threading.Lock()


def _load_cache():
    global _cache
    try:
        with open(CACHE_FILE) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _cache = {}
        return
    if isinstance(raw, dict) and raw.get("__version__") == CACHE_VERSION:
        _cache = raw.get("entries") or {}
    else:
        # older/unknown format — start clean so stale results cannot be served
        _cache = {}


def _save_cache():
    # Snapshot under the lock, then write atomically. Concurrently iterating a
    # dict that another thread is growing raised
    # `RuntimeError: dictionary changed size during iteration`, which failed
    # scrapes mid-run; concurrent writes to the same file also corrupt it.
    with _cache_lock:
        snapshot = dict(_cache)
    tmp = CACHE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"__version__": CACHE_VERSION, "entries": snapshot}, f)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


_load_cache()

MS_PARAM = "_ms=3.0.1"
PAGE_SIZE = 20
MAX_EXTRACT_PAGES = 200


def _build_store_url(store_url: str, page: int = 1) -> str:
    if "?" in store_url:
        base = store_url.split("?")[0]
    else:
        base = store_url.rstrip("/")
    return f"{base}?{MS_PARAM}&page={page}"


def _normalize_url(url: str) -> str:
    """Resolve short Meesho URLs (/p/xxx) to full canonical URLs."""
    import re as _re
    if _re.search(r'/p/[a-zA-Z0-9]+', url):
        cleaned = url.split("?")[0].rstrip("/")
        parts = cleaned.split("/p/")
        if len(parts) == 2 and (not parts[0].split("/")[-1] or parts[0].endswith("meesho.com")):
            resolved = _try_scrappey(url)
            if resolved:
                m = _re.search(r'canonical["\'\s]*[:=]\s*["\'](https://[^"\']+)', resolved)
                if m:
                    return m.group(1)
                m2 = _re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', resolved)
                if m2:
                    return m2.group(1)
    return url


_pw_working = None

def _fetch_page(url: str) -> str:
    """Fast page fetch — curl_cffi first (no proxy), then Playwright, then Scrappey."""
    html = _try_curl_cffi(url)
    if html:
        return html
    global _pw_working
    if _pw_working is None or _pw_working:
        html = _try_playwright(url)
        if html:
            _pw_working = True
            return html
        _pw_working = False
    return _try_scrappey(url) or _fetch(url)


def _fetch(url: str) -> str:
    """Fetch a URL — curl_cffi first (no proxy needed), then fallbacks."""
    html = _try_curl_cffi(url)
    if html:
        return html
    html = _try_playwright(url)
    if html:
        return html
    html = _try_scrappey(url)
    if html:
        return html
    html = _try_scraperapi(url)
    if html:
        return html
    html = _try_direct(url)

def _fetch_product_page(url: str) -> str:
    """Fetch a product page. CDP Chrome FIRST — Meesho blocks direct/curl_cffi
    requests (403), so those just waste time before the browser attempt."""
    html = _try_playwright(url)
    if html:
        return html
    html = _try_curl_cffi(url, impersonate="safari15_5")
    if html:
        return html
    return _try_scraperapi(url)


def _is_bot_page(html: str) -> bool:
    """Detect bot/challenge pages — short HTML with JS challenge or access denied."""
    if len(html) < 1000:
        return True
    checks = [
        "sec-if-cpt-container" in html,
        "_abck" in html[:2000],
        "Access Denied" in html,
        "cf-browser-verification" in html,
        "/cdn-cgi/" in html[:2000],
    ]
    return any(checks)


def _try_curl_cffi(url: str, impersonate: str = "safari15_5") -> str:
    """Fetch via curl_cffi with browser impersonation — bypasses Akamai."""
    kwargs = dict(impersonate=impersonate, timeout=30)
    if PROXY:
        kwargs["proxies"] = {"http": PROXY, "https": PROXY}
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(url, **kwargs)
        if resp.status_code == 200 and len(resp.text) > 5000 and not _is_bot_page(resp.text):
            return resp.text
    except Exception:
        pass
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(url, **kwargs, verify=False)
        if resp.status_code == 200 and len(resp.text) > 5000 and not _is_bot_page(resp.text):
            return resp.text
    except Exception:
        pass
    return ""


# ── Shared Chrome over CDP (proxied first, then direct) ──────────────────────
# We attach to the shared Chromes instead of launch()ing our own. A fresh browser
# is served Meesho's edge "Access Denied" page (measured 0/5 real pages with a
# clean profile AND with a persistent one), while the shared profile gets real
# pages — so the profile/trust is what Meesho keys on.
#
# The catch: connect_over_cdp can HANG for its full 180s default timeout on a
# stuck profile. That is what produced days of phantom "Blocked by Meesho" (the
# page never loaded, so the product looked blocked). So every attempt uses a
# short connect timeout, and we try the PROXIED Chrome first — it rotates
# residential IPs, so Meesho's per-IP rate limit never accumulates — then fall
# back to the direct one.
_CDP_ORDER = [c for c in (SCRAPE_CDP_URL, EXTRACT_CDP_URL) if c]
CDP_CONNECT_TIMEOUT_MS = int(os.environ.get("MEESHO_CDP_CONNECT_TIMEOUT_MS", "8000"))

# A Chrome behind a dead proxy (9223 routes through the Webshare proxy, which
# returns 402 when the quota is exhausted) does not fail fast - every goto just
# times out after 30s before we fall back to the direct Chrome. Two failures in
# a row demote it for ten minutes, so a whole job pays that cost once instead of
# once per product.
_CDP_STATE: dict = {}
_CDP_FAIL_LIMIT = 2
_CDP_COOLDOWN_SECONDS = int(os.environ.get("MEESHO_CDP_COOLDOWN_SECONDS", "120"))


def _cdp_candidates() -> list:
    now = time.time()
    live = [c for c in _CDP_ORDER if _CDP_STATE.get(c, (0, 0.0))[1] <= now]
    return live or list(_CDP_ORDER)


def _cdp_failed(cdp: str, why: str = "") -> None:
    fails, until = _CDP_STATE.get(cdp, (0, 0.0))
    fails += 1
    if fails >= _CDP_FAIL_LIMIT:
        until = time.time() + _CDP_COOLDOWN_SECONDS
        print(f"MEESHO: {cdp} failing ({why}) - skipping for {_CDP_COOLDOWN_SECONDS}s",
              file=sys.stderr)
    _CDP_STATE[cdp] = (fails, until)


def _cdp_ok(cdp: str) -> None:
    if cdp in _CDP_STATE:
        _CDP_STATE[cdp] = (0, 0.0)


def _try_playwright(url: str) -> str:
    """Fetch a product page via a shared Chrome over CDP (proxied, then direct).

    Optimised for a DATA scrape: images/media/fonts/CSS are aborted (the price,
    specs and images list all come from __NEXT_DATA__), and readiness is polled
    with a cheap JS probe instead of re-serialising the whole DOM every 2s. The
    old loop (20 x 2s sleep + page.content()) is why a Meesho product could take
    20-40s.
    """
    try:
        import time
        from playwright.sync_api import sync_playwright

        BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}

        for cdp in _cdp_candidates():
            try:
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(cdp, timeout=CDP_CONNECT_TIMEOUT_MS)
                    # Fresh context: the shared default context accumulates state
                    # and starts serving block pages for every navigation.
                    ctx = browser.new_context()
                    page = ctx.new_page()
                    try:
                        def _route(route):
                            try:
                                if route.request.resource_type in BLOCKED_RESOURCES:
                                    route.abort()
                                else:
                                    route.continue_()
                            except Exception:
                                pass

                        try:
                            page.route("**/*", _route)
                        except Exception:
                            pass

                        page.goto(url, wait_until="domcontentloaded", timeout=25000)
                        try:
                            page.evaluate("""() => {
                                document.querySelectorAll('video').forEach(v => {
                                    try { v.pause(); v.autoplay = false; v.removeAttribute('autoplay'); v.muted = true; } catch (e) {}
                                });
                            }""")
                        except Exception:
                            pass

                        # Cheap readiness probe: no HTML serialisation per poll.
                        ready = False
                        deadline = time.time() + 8
                        delay = 0.25
                        while time.time() < deadline:
                            try:
                                info = page.evaluate("""() => {
                                    const el = document.getElementById('__NEXT_DATA__');
                                    const html = document.documentElement ? document.documentElement.outerHTML : '';
                                    const has = !!el || html.indexOf('"@type\":\"Product"') >= 0
                                                || html.indexOf('"@type": "Product"') >= 0
                                                || html.indexOf('window.__INITIAL_STATE__') >= 0;
                                    return { size: html.length, has };
                                }""") or {}
                            except Exception:
                                info = {}
                            size = int(info.get("size") or 0)
                            if size > 5000 and info.get("has"):
                                ready = True
                                break
                            if size > 60000:
                                break
                            time.sleep(delay)
                            delay = min(delay * 1.5, 1.0)

                        html = page.content()
                        if (ready or len(html) > 20000):
                            _cdp_ok(cdp)
                            return html
                        _cdp_failed(cdp, f"no product data ({len(html)} bytes)")
                    finally:
                        for _obj in (page, ctx):
                            try:
                                _obj.close()
                            except Exception:
                                pass
            except Exception as exc:
                _cdp_failed(cdp, type(exc).__name__)
                continue
    except Exception:
        pass
    return ""

def _try_google_cache(url: str) -> str:
    """Fallback: fetch Google cached version of the page."""
    from urllib.parse import quote
    try:
        import requests
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{quote(url, safe='')}"
        resp = requests.get(cache_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        })
        if resp.status_code == 200 and len(resp.text) > 1000:
            return resp.text
    except Exception:
        pass
    return ""


def _try_scrappey(url: str) -> str:
    """Fetch via Scrappey API (free residential proxy + JS rendering)."""
    if not SCRAPPLEY_API_KEY:
        return ""
    try:
        import json
        import requests
        import time as _time
        import random
        target = f"https://publisher.scrappey.com/api/v1?key={SCRAPPLEY_API_KEY}"
        for attempt in range(20):
            resp = requests.post(
                target,
                timeout=60,
                headers={"Content-Type": "application/json"},
                json={"cmd": "request.get", "url": url},
            )
            if resp.status_code == 200:
                data = json.loads(resp.text)
                solution = data.get("solution", {})
                html = solution.get("response", "")
                if len(html) > 1000 and not _is_bot_page(html):
                    return html
            if attempt < 19:
                _time.sleep(1 + random.random() * 2)
    except Exception:
        pass
    return ""


def _try_direct(url: str) -> str:
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(url, impersonate="chrome", timeout=15)
        if resp.status_code == 200 and not _is_bot_page(resp.text):
            return resp.text
    except Exception:
        pass
    try:
        import requests
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        })
        if resp.status_code == 200 and not _is_bot_page(resp.text):
            return resp.text
    except Exception:
        pass
    return ""


def _try_scrape_do(url: str) -> str:
    """Fetch via scrape.do — fast residential proxy fallback."""
    if not SCRAPE_DO_TOKEN:
        return ""
    try:
        import requests
        from urllib.parse import quote
        target = f"http://api.scrape.do?token={SCRAPE_DO_TOKEN}&super=false&url={quote(url, safe='')}"
        resp = requests.get(target, timeout=30)
        if len(resp.text) > 1000:
            return resp.text
    except Exception:
        pass
    return ""


def _try_scraperapi(url: str) -> str:
    """Fetch via ScraperAPI — 1 credit, datacenter proxy, no JS rendering."""
    from urllib.parse import quote
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        api_key = SCRAPERAPI_KEY
        target = f"https://api.scraperapi.com?api_key={api_key}&url={quote(url, safe='')}&country_code=in"
        resp = requests.get(target, timeout=60, verify=False)
        if len(resp.text) > 1000:
            return resp.text
    except Exception:
        pass
    return ""


def _get_total_count(html: str) -> int:
    """Extract productsCount (total products across all pages) from store page HTML."""
    m = re.search(r'__NEXT_DATA__[^>]*>\s*({.*?})\s*</script>', html, re.DOTALL)
    if not m:
        return 0
    try:
        data = json.loads(m.group(1), strict=False)
        init = data.get("props", {}).get("pageProps", {}).get("initialState", {})
        # Check all listing sources, not just shopListing
        for src in ["shopListing", "hpListing", "plpListing", "searchListing", "shopCollectionListing"]:
            listing = init.get(src, {}).get("listing", {})
            count = int(listing.get("productsCount", 0))
            if count > 0:
                return count
        return 0
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0


def extract_products(html: str, store_url: str) -> dict:
    """Extract product list from a Meesho store/shop page HTML."""
    products = []
    errors = []

    m = re.search(r'__NEXT_DATA__[^>]*>\s*({.*?})\s*</script>', html, re.DOTALL)
    if not m:
        errors.append("No __NEXT_DATA__ found in page")
        return {"products": products, "errors": errors, "store_name": ""}

    try:
        data = json.loads(m.group(1), strict=False)
    except json.JSONDecodeError as e:
        errors.append(f"Failed to parse __NEXT_DATA__: {e}")
        return {"products": products, "errors": errors, "store_name": ""}

    initial = data.get("props", {}).get("pageProps", {}).get("initialState", {})

    store_name = ""
    shop_info = initial.get("shop", {})
    if isinstance(shop_info, dict):
        shop_info_inner = shop_info.get("shopInfo") or {}
        shop_profile = shop_info_inner.get("shopProfile") or {}
        profile = shop_profile.get("profile", {})
        if isinstance(profile, dict):
            store_name = profile.get("name", "") or ""

    if not store_name:
        m2 = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        if m2:
            store_name = m2.group(1).strip()

    product_lists = []

    listing_sources = ["shopListing", "hpListing", "plpListing", "searchListing"]
    for source in listing_sources:
        listing = initial.get(source, {}).get("listing", {})
        if not listing:
            continue
        pages = listing.get("products", [])
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    items = page.get("products", [])
                    if isinstance(items, list):
                        product_lists.extend(items)

    seen_pids = set()
    for item in product_lists:
        slug = item.get("slug", "")
        product_id = item.get("product_id", item.get("id", ""))
        if not slug or not product_id:
            continue
        unique_key = f"{slug}:{product_id}"
        if unique_key in seen_pids:
            continue
        seen_pids.add(unique_key)

        name = item.get("hero_product_name") or item.get("name") or ""
        price = item.get("min_catalog_price") or item.get("price") or 0
        original_price = item.get("original_price") or 0
        image = item.get("image") or ""

        # Extract all product images from store listing
        image_urls = []
        raw_images = item.get("images") or item.get("product_images") or []
        if isinstance(raw_images, list):
            for img in raw_images:
                if isinstance(img, str):
                    image_urls.append(_full_size_image_url(img))
                elif isinstance(img, dict) and isinstance(img.get("url"), str):
                    image_urls.append(_full_size_image_url(img["url"]))
        # Deduplicate
        seen_urls = set()
        unique_urls = []
        for u in image_urls:
            if u not in seen_urls:
                seen_urls.add(u)
                unique_urls.append(u)
        image_urls = unique_urls
        if image:
            image_full = _full_size_image_url(image)
            if image_full not in image_urls:
                image_urls.insert(0, image_full)

        description = item.get("description") or name
        full_details = item.get("full_details") or ""
        combined_desc = description
        if full_details:
            combined_desc = f"{description}\n\n{full_details}"
        slug_clean = slug.lower().replace(" ", "-")
        slug_clean = re.sub(r"[^a-z0-9-]", "-", slug_clean).strip("-")
        slug_clean = re.sub(r"-+", "-", slug_clean)
        product_url = f"https://www.meesho.com/{slug_clean}/p/{product_id}"

        products.append({
            "id": str(product_id),
            "title": name[:500] if name else "Untitled",
            "description": _strip_html(combined_desc)[:5000] if combined_desc else None,
            "meta_description": None,
            "imageUrl": image_urls[0] if image_urls else (image or None),
            "images": image_urls[:10],
            "hsn": None,
            "gst": None,
            "dimensions": None,
            "weight": None,
            "specifications": None,
            "variants": None,
            "price": f"\u20b9{price}" if price else None,
            "mrp": f"\u20b9{original_price}" if original_price else None,
            "url": product_url,
            "status": "pending",
            "error": None,
        })

    if not products:
        errors.append("No products found in store page")

    return {
        "products": products,
        "errors": errors,
        "store_name": store_name,
    }


def scrape_product(url: str, html: str = "") -> dict:
    """Extract full product details from a Meesho product page HTML."""
    k = _cache_key(url)
    with _cache_lock:
        if k in _cache:
            return _cache[k]
    if not html:
        url = _normalize_url(url)
        html = _fetch_product_page(url)
    if not html:
        return _blocked(f"Could not fetch {url}")

    m = re.search(r'__NEXT_DATA__[^>]*>\s*({.*?})\s*</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1), strict=False)
        except json.JSONDecodeError:
            return _blocked("Failed to parse __NEXT_DATA__")
    else:
        if _is_blocked(html):
            return _blocked("Meesho blocked the request")
        m2 = re.search(r'<script[^>]*__NEXT_DATA__[^>]*>(.*?)</script>', html, re.DOTALL)
        if m2:
            try:
                data = json.loads(m2.group(1), strict=False)
            except json.JSONDecodeError:
                return _blocked("Failed to parse __NEXT_DATA__ (alt)")
        else:
            return _blocked("No __NEXT_DATA__ found on product page")

    initial = data.get("props", {}).get("pageProps", {}).get("initialState", {})
    product_data = initial.get("product", {}).get("details", {}).get("data", {})

    if not product_data or not product_data.get("name"):
        return _fallback_scrape(html, url)

    # If product has no price and no images, it's likely a dead/recycled ID — fall back to HTML parsing
    price_raw = product_data.get("price") or 0
    has_images = bool(product_data.get("images"))
    if not price_raw and not has_images:
        return _fallback_scrape(html, url)

    name = product_data.get("name", "")
    description = product_data.get("description", "")
    price = product_data.get("price", 0)
    original_price = product_data.get("original_price", 0)
    discount = product_data.get("discount", 0)
    product_id = product_data.get("product_id", "")
    images = product_data.get("images", [])
    image_urls = _extract_images_from_data(images)

    canonical_url = product_data.get("meta_info", {}).get("canonical_url", "") or url

    specs = {}
    product_details = product_data.get("product_details", {})
    highlights = product_details.get("product_highlights", {}).get("attributes", [])
    if isinstance(highlights, list):
        for attr in highlights:
            k = attr.get("field_name", "")
            v = attr.get("value", "")
            if k and v:
                specs[k] = v

    additional = product_details.get("additional_details", {}).get("attributes", [])
    if isinstance(additional, list):
        for attr in additional:
            k = attr.get("field_name", "")
            v = attr.get("value", "")
            if k and v:
                specs[k] = v

    variations = product_data.get("variations", [])
    variants_str = ", ".join(str(v) for v in variations) if isinstance(variations, list) and variations else None

    # Category breadcrumb (e.g. "Unisex Personal Care > Face Care > Face Wash").
    # Populated on the canonical /<slug>/p/<id> URL; the short /s/p/<id> form
    # returns an empty list, so we prefer the pretty URL upstream.
    #
    # Meesho also leaves `breadcrumb` EMPTY for a sizeable share of products (no
    # parent chain anywhere in the payload). The catalog object is still present
    # though, so fall back to the category NAME it carries — enough for the
    # curated map / coarse matcher to place the product.
    source_category_path = None
    source_catalog_name = None
    bc = product_data.get("breadcrumb")
    if isinstance(bc, list):
        titles = [
            _clean(str(b.get("title") or ""))
            for b in bc
            if isinstance(b, dict)
        ]
        titles = [t for t in titles if t]
        if titles:
            source_category_path = " > ".join(titles)
    catalog = product_data.get("catalog") or {}
    if isinstance(catalog, dict):
        source_catalog_name = _clean(str(catalog.get("name") or "")) or None
        if not source_category_path:
            sub = _clean(str(catalog.get("sub_sub_category_name") or ""))
            if not sub:
                sub = source_catalog_name or ""
            if sub:
                source_category_path = sub

    hsn_val = None
    gst_val = None
    dims_val = None
    weight_val = None
    for k, v in specs.items():
        kl = k.lower()
        if "hsn" in kl:
            hsn_val = v
        if "gst" in kl:
            gst_val = v
        if "weight" in kl:
            weight_val = v
        if "dimension" in kl or kl in ("size", "product dimensions"):
            dims_val = v

    result = {
        "status": "success",
        "id": str(product_id) if product_id else None,
        "title": _clean(name[:500]) if name else "Untitled",
        "description": _strip_html(description)[:5000] if description else None,
        "meta_description": None,
        "imageUrl": image_urls[0] if image_urls else None,
        "images": image_urls[:10],
        "hsn": hsn_val,
        "gst": gst_val,
        "dimensions": dims_val,
        "weight": weight_val,
        "specifications": specs if specs else None,
        "variants": variants_str,
        "source_category_path": source_category_path,
        "source_catalog_name": source_catalog_name,
        "price": f"\u20b9{price}" if price else None,
        # Meesho's list price. The template's "Mrp Price *" is mandatory and the
        # exporter used to hardcode it empty ("no source") - the PDP payload has
        # carried original_price all along, it just was not surfaced.
        "mrp": f"\u20b9{original_price}" if original_price else None,
        "url": canonical_url,
        "error": None,
    }
    with _cache_lock:
        _cache[_cache_key(url)] = result
    _save_cache()
    return result


def _fallback_scrape(html: str, url: str) -> dict:
    """Fallback: extract product name from title tag and images from HTML.

    Returns a BLOCKED result when the page carries no product at all. It used to
    return status=success with title "Meesho" and zero images, which the job
    recorded as a scraped product - that is why products appeared "scraped" but
    had no images.
    """
    low = html.lower()
    if ("couldn't find what you were looking for" in low
            or "couldnt find what you were looking for" in low
            or "we couldn't find what you" in low):
        return _blocked("Meesho: product page not found (deleted or bad URL)")
    title = None
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    imgs = re.findall(r'https://images\.meesho\.com/images/products/\d+/[\w-]+(?:_\d+)?\.(?:jpg|webp|png)', html)
    seen = set()
    unique_imgs = []
    for u in imgs:
        u_full = _full_size_image_url(u)
        if u_full not in seen:
            seen.add(u_full)
            unique_imgs.append(u_full)

    description = None
    desc_m = re.search(r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
    if desc_m:
        description = _strip_html(desc_m.group(1))

    price = None
    pm = re.search(r'"price"\s*:\s*(\d+)', html)
    if pm:
        pval = int(pm.group(1))
        if pval > 0:
            price = f"\u20b9{pval}"
    if not price:
        pm2 = re.search(r'\u20b9\s*([\d,]+)', html)
        if pm2:
            price = f"\u20b9{pm2.group(1)}"

    if not unique_imgs and (not title or title.strip().lower() in ("meesho", "untitled", "")):
        # Nothing product-shaped on the page: no images AND a generic title.
        return _blocked("Meesho: page had no product data (blocked or empty)")

    result = {
        "status": "success",
        "id": None,
        "title": _clean(title[:500]) if title else "Untitled",
        "description": description[:5000] if description else None,
        "meta_description": None,
        "imageUrl": unique_imgs[0] if unique_imgs else None,
        "images": unique_imgs[:10],
        "hsn": None,
        "gst": None,
        "dimensions": None,
        "weight": None,
        "specifications": None,
        "variants": None,
        "price": price,
        "url": url,
        "error": None,
    }
    with _cache_lock:
        _cache[_cache_key(url)] = result
    _save_cache()
    return result


def _is_blocked(html: str) -> bool:
    checks = [
        "Something went wrong" in html,
        "couldn't find what you were looking for" in html,
        "captcha" in html.lower(),
        "Access Denied" in html,
        "Please verify you are a human" in html,
    ]
    return any(checks)


def _blocked(msg: str) -> dict:
    return {
        "status": "blocked",
        "error": msg,
        "id": None,
        "title": "Blocked by Meesho",
        "description": None,
        "meta_description": None,
        "imageUrl": None,
        "images": [],
        "hsn": None,
        "gst": None,
        "dimensions": None,
        "weight": None,
        "specifications": None,
        "variants": None,
        "price": None,
        "url": None,
        "error": msg,
    }


def _full_size_image_url(url: str) -> str:
    """Convert Meesho thumbnail URL (_512) to full-size URL."""
    return re.sub(r"_\d+\.(jpg|webp|png)$", r".\1", url)


def _extract_images_from_data(images: list) -> list:
    """Extract and convert image URLs from __NEXT_DATA__ images array to full-size."""
    urls = []
    for img in images:
        if isinstance(img, str):
            urls.append(img)
        elif isinstance(img, dict):
            u = img.get("url", "") or img.get("src", "")
            if u:
                urls.append(u)
    return [_full_size_image_url(u) for u in urls if u]


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string, preserving line breaks."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</?(?:ul|ol|li|div|p|span|b|strong|i|em|u)>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n\s*\n", "\n", text).strip()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _grab_product_links(page) -> list:
    """Collect all /p/ product links currently in the DOM."""
    products = []
    seen = set()
    links = page.locator("a[href*='/p/']")
    for i in range(links.count()):
        href = links.nth(i).get_attribute("href")
        if href and "/p/" in href and href not in seen:
            seen.add(href)
            product_id = href.rstrip("/").split("/p/")[-1]
            products.append({
                "id": product_id,
                "title": "Untitled",
                "url": f"https://www.meesho.com{href}" if href.startswith("/") else href,
                "status": "pending",
                "error": None,
            })
    return products


def extract_page(store_url: str, page_num: int, sort: str = "") -> dict:
    """Extract product links from a single paginated page of a Meesho store (Chrome CDP).

    Waits for products to load (up to 20 per page) and retries the page a few
    times if it loads fewer than expected. Kept under ngrok's ~60s limit per call.

    `sort` walks a different ordering of the same store. Meesho caps store
    pagination at ~200 pages and repeats products across them, so one ordering
    only exposes part of a large catalogue; a different sort starts from a
    disjoint set (page 1 of 'popularity' shares nothing with page 1 of the
    default order), so callers union several sorts to reach the whole store.
    """
    import time as _time
    import urllib.request
    from playwright.sync_api import sync_playwright

    url_clean = store_url.split("?")[0].rstrip("/")
    query = ["_ms=3.0.1"]
    if sort:
        query.insert(0, f"sort={sort}")
    if page_num > 1:
        query.append(f"page={page_num}")
    page_url = f"{url_clean}?{'&'.join(query)}"

    try:
        urllib.request.urlopen(f"{EXTRACT_CDP_URL}/json/version", timeout=5)
    except Exception as e:
        return {"products": [], "hasMore": False, "error": f"Chrome CDP not available: {e}"}

    products = []
    seen = set()
    last_error = None
    # Retry while we have fewer than a full page — a short page is either a
    # slow/partial load (a retry helps) or the genuine last page (retrying is
    # pure waste). Stop as soon as an attempt adds nothing new: the store's
    # trailing pages (8 products, then 0) used to burn 3 attempts x ~10s each,
    # ~120s of the job's runtime.
    for attempt in range(3):
        before = len(products)
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(EXTRACT_CDP_URL)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

                # Poll + scroll for products to load. Give up early once the
                # count has settled (5 unchanged checks) rather than always
                # burning the full 10s on a page with nothing more to give.
                links = page.locator("a[href*='/p/']")
                deadline = _time.time() + 10
                stable = 0
                last_n = -1
                while _time.time() < deadline:
                    n = links.count()
                    if n >= PAGE_SIZE:
                        break
                    stable = stable + 1 if n == last_n else 0
                    last_n = n
                    if stable >= 5:
                        break
                    _time.sleep(0.8)
                    try:
                        page.mouse.wheel(0, 2500)
                    except Exception:
                        pass

                for item in _grab_product_links(page):
                    if item["url"] not in seen:
                        seen.add(item["url"])
                        products.append(item)
                page.close()
                ctx.close()
        except Exception as e:
            last_error = str(e)
            _time.sleep(1)
            continue

        if len(products) >= PAGE_SIZE:
            break  # full page loaded
        if len(products) == before:
            break  # this attempt added nothing — another one cannot help
        _time.sleep(1)  # partial page: one more attempt may still fill it

    # A page that still has 0 products after retries = end of the store
    has_more = len(products) > 0
    error = None if (products or last_error is None) else (last_error or "")
    return {"products": products, "hasMore": has_more, "error": error}


def extract_store(store_url: str) -> dict:
    """Extract all products from a Meesho store across all pages.

    Meri Shop pages are client-side rendered: products load via JS + scroll and
    the products API is Akamai-protected. So we use the real browser (Chrome CDP)
    to load the page, scroll to trigger lazy-loading, and grab the /p/ product links.
    """
    url_clean = store_url.split("?")[0].rstrip("/")
    if "meesho.com/" in url_clean and not url_clean.rstrip("/").endswith("meesho.com"):
        # First try the fast path (server-rendered shop) — many shops still SSR
        try:
            first_url = _build_store_url(url_clean, 1)
            html = _try_curl_cffi(first_url)
            if html and _get_total_count(html) > 0:
                result = _extract_from_html(html, store_url)
                if result.get("products"):
                    return result
        except Exception:
            pass

    # Fall back to real browser (Chrome CDP) — load, scroll, grab /p/ links
    return _extract_via_browser(url_clean, store_url)


def extract_store_api(store_url: str) -> dict:
    """Enumerate a Meesho store's WHOLE catalogue through its own JSON API.

    The storefront HTML never paginates past ~2 pages (~40 products) no matter how
    big the shop is, so a 308-product shop yielded only 57 across the three sort
    orders walkStore used to union. The page's own /api/v1/meri-shop/feed endpoint
    is offset-paginated and returns the entire catalogue with names, slugs,
    prices, images and attributes, so drive that instead:

        GET  /api/v1/meri-shop/profile?supplierHandle=<handle>  -> masked supplier id + product count
        POST /api/v1/meri-shop/feed  {limit, offset, supplier_id, filter}  -> {catalogs: [...]}

    Measured on MSSOMNATHENTERPRISES: 308/308 products in 16 calls. Meesho
    rate-limits this endpoint (observed 429), so calls are throttled and retried.

    Returns {"products": [...], "total": n, "hasMore": False} in the same shape
    extract_page produces, so callers can treat them interchangeably.
    """
    import time as _time
    import urllib.request
    from playwright.sync_api import sync_playwright

    url_clean = store_url.split("?")[0].rstrip("/")
    handle = url_clean.split("/")[-1]

    try:
        urllib.request.urlopen(f"{EXTRACT_CDP_URL}/json/version", timeout=5)
    except Exception as e:
        return {"products": [], "total": 0, "hasMore": False, "error": f"Chrome CDP not available: {e}"}

    LIMIT = PAGE_SIZE  # 20
    FEED_DELAY = 0.4   # seconds between feed calls (429s appear without it)
    products: list = []
    seen = set()
    expected = 0
    error = None

    def _post_feed(page, offset: int, supplier_id: str) -> tuple:
        for _attempt in range(3):
            try:
                return page.evaluate(
                    """async (args) => {
                        const r = await fetch('/api/v1/meri-shop/feed', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({limit: args.limit, offset: args.offset,
                                supplier_id: args.sid, featured_collection_type: null,
                                filter: {selected_filter_ids: [], type: 'shop',
                                         sort_option: null, session_state: null}})
                        });
                        return {status: r.status, body: await r.text()};
                    }""",
                    {"offset": offset, "sid": supplier_id, "limit": LIMIT},
                )
            except Exception as e:
                msg = str(e).lower()
                if "navigation" in msg or "destroyed" in msg:
                    _time.sleep(1.5)
                    continue
                raise
        return {"status": -1, "body": "{}"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(EXTRACT_CDP_URL)
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                # Load the storefront once so the API calls share its origin and
                # whatever cookies/headers Meesho expects.
                page.goto(f"{url_clean}?_ms=3.0.1", wait_until="domcontentloaded", timeout=30000)

                # The store page sometimes navigates again (client-side) right
                # after load - e.g. a supplier-store redirect - which destroys the
                # evaluate context ("Execution context was destroyed, most likely
                # because of a navigation"). Wait for the URL to settle, then
                # fetch the profile with a retry that survives a mid-flight
                # navigation.
                last_url = None
                for _ in range(10):
                    _time.sleep(0.6)
                    try:
                        cur = page.url
                    except Exception:
                        cur = None
                    if cur and cur == last_url:
                        break
                    last_url = cur

                def _eval(js: str, arg) -> dict:
                    for _attempt in range(3):
                        try:
                            return page.evaluate(js, arg)
                        except Exception as e:
                            msg = str(e).lower()
                            if "navigation" in msg or "destroyed" in msg:
                                _time.sleep(1.5)
                                continue
                            raise
                    return {}

                prof = _eval(
                    """async (h) => {
                        const r = await fetch('/api/v1/meri-shop/profile?supplierHandle=' + encodeURIComponent(h));
                        return {status: r.status, body: await r.text()};
                    }""",
                    handle,
                )
                if prof.get("status") != 200:
                    return {"products": [], "total": 0, "hasMore": False,
                            "error": f"shop profile API returned {prof.get('status')}"}
                pdata = json.loads(prof["body"]).get("profile") or {}
                supplier_id = pdata.get("masked_id")
                for prop in pdata.get("shop_value_props") or []:
                    if prop.get("type") == "product":
                        expected = int((prop.get("product") or {}).get("count") or 0)
                if not supplier_id:
                    return {"products": [], "total": 0, "hasMore": False,
                            "error": "shop profile API did not return a supplier id"}

                offset = 0
                # Walk to the reported count, plus one page of slack in case the
                # count is stale. A short page also ends the walk.
                while offset < expected + LIMIT:
                    res = _post_feed(page, offset, supplier_id)
                    status = res.get("status")
                    if status == 429:
                        _time.sleep(3)
                        res = _post_feed(page, offset, supplier_id)
                        status = res.get("status")
                    if status != 200:
                        error = f"feed API returned {status} at offset {offset}"
                        break
                    catalogs = (json.loads(res["body"]) or {}).get("catalogs") or []
                    for c in catalogs:
                        pid = str(c.get("product_id") or c.get("original_product_id") or "").strip()
                        if not pid or pid in seen:
                            continue
                        seen.add(pid)
                        slug = (c.get("slug") or c.get("original_slug") or "p").strip()
                        images = [u for u in (c.get("images") or c.get("product_images") or [])
                                  if isinstance(u, str)]
                        price = c.get("min_catalog_price")
                        products.append({
                            "id": pid,
                            "title": (c.get("name") or c.get("hero_product_name") or "Untitled").strip(),
                            "url": f"https://www.meesho.com/{slug}/p/{pid}",
                            "images": images[:10],
                            "price": f"\u20b9{price}" if price else None,
                            "mrp": c.get("original_price"),
                            "category": c.get("sub_sub_category_name") or c.get("name"),
                            "attributes": c.get("product_attributes") or [],
                            "details": c.get("full_details") or "",
                            "status": "pending",
                            "error": None,
                        })
                    if len(catalogs) < LIMIT:
                        break
                    offset += LIMIT
                    _time.sleep(FEED_DELAY)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as e:
        return {"products": products, "total": len(products), "hasMore": False, "error": str(e)}

    out = {"products": products, "total": len(products), "hasMore": False}
    if expected and len(products) < expected:
        out["warning"] = f"feed API yielded {len(products)} of {expected} reported products"
    if error:
        out["error"] = error
    return out


def _extract_from_html(html: str, store_url: str) -> dict:
    result = extract_products(html, store_url)
    total_count = _get_total_count(html)
    total_pages = max(1, (min(total_count, MAX_EXTRACT_PAGES * PAGE_SIZE) + PAGE_SIZE - 1) // PAGE_SIZE) if total_count else 0
    all_products = list(result.get("products", []))
    seen_ids = {p["id"] for p in all_products}
    errors = list(result.get("errors", []))
    empty_streak = 0

    url_clean = store_url.split("?")[0].rstrip("/")
    for page in range(2, min(total_pages + 1, MAX_EXTRACT_PAGES + 1)):
        page_url = _build_store_url(url_clean, page)
        page_html = _try_curl_cffi(page_url) or _fetch_page(page_url)
        if not page_html:
            errors.append(f"Failed to fetch page {page}")
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue
        page_result = extract_products(page_html, store_url)
        page_products = page_result.get("products", [])
        if not page_products:
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue
        empty_streak = 0
        for p in page_products:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                all_products.append(p)

    return {
        "products": all_products,
        "errors": errors,
        "store_name": result.get("store_name", ""),
        "total_pages": total_pages,
        "total_products": total_count,
        "total_unique": len(all_products),
    }


def _extract_via_browser(url_clean: str, store_url: str) -> dict:
    """Load the store in real Chrome (CDP) and paginate through &page=N, grabbing /p/ links."""
    try:
        import time as _time
        import urllib.request
        from playwright.sync_api import sync_playwright

        # Verify Chrome CDP is available
        urllib.request.urlopen(f"{EXTRACT_CDP_URL}/json/version", timeout=5)
    except Exception as e:
        return {"products": [], "errors": [f"Chrome CDP not available: {e}"], "store_name": "", "total_pages": 0, "total_products": 0, "total_unique": 0}

    products = []
    seen = set()
    store_name = ""
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(EXTRACT_CDP_URL)
            ctx = browser.new_context()
            page = ctx.new_page()
            try:

                for pg in range(1, MAX_EXTRACT_PAGES + 1):
                    page_url = f"{url_clean}?_ms=3.0.1" if pg == 1 else f"{url_clean}?_ms=3.0.1&page={pg}"
                    try:
                        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                        _time.sleep(2.5)
                    except Exception:
                        errors.append(f"Failed to load page {pg}")
                        break

                    if not store_name:
                        store_name = page.title() or store_name

                    links = page.locator("a[href*='/p/']")
                    page_new = 0
                    for i in range(links.count()):
                        href = links.nth(i).get_attribute("href")
                        if href and "/p/" in href and href not in seen:
                            seen.add(href)
                            product_id = href.rstrip("/").split("/p/")[-1]
                            products.append({
                                "id": product_id,
                                "title": "Untitled",
                                "url": f"https://www.meesho.com{href}" if href.startswith("/") else href,
                                "status": "pending",
                                "error": None,
                            })
                            page_new += 1

                    # Stop when a page has no product links or no new products (reached the end)
                    if links.count() == 0 or page_new == 0:
                        if pg > 1:
                            break
            finally:
                for _obj in (page, ctx):   # ctx is ours (browser.new_context())
                    try:
                        _obj.close()
                    except Exception:
                        pass
    except Exception as e:
        import traceback
        return {"products": products, "errors": [f"Browser extract failed: {e}"], "store_name": store_name, "total_pages": 0, "total_products": len(products), "total_unique": len(products)}

    return {
        "products": products,
        "errors": errors,
        "store_name": store_name,
        "total_pages": 0,
        "total_products": len(products),
        "total_unique": len(products),
    }




if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "failed", "error": "Usage: _meesho_scraper.py <action> [url]"}))
        sys.exit(0)

    action = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else ""

    if not url:
        print(json.dumps({"status": "failed", "error": "No URL provided"}))
        sys.exit(0)

    try:
        if action == "extract":
            result = extract_store(url)
            print(json.dumps(result))
        elif action == "scrape":
            html = _fetch_product_page(url)
            if not html:
                print(json.dumps(_blocked(f"Could not fetch {url}")))
                sys.exit(0)
            result = scrape_product(url, html)
            print(json.dumps(result))
        elif action == "scrape-html":
            html = sys.stdin.read()
            result = scrape_product(url, html)
            print(json.dumps(result))
        elif action == "clear-cache":
            with _cache_lock:
                _cache.clear()
            _save_cache()
            print(json.dumps({"status": "success", "message": "Cache cleared"}))
        else:
            print(json.dumps({"status": "failed", "error": f"Unknown action: {action}"}))
            sys.exit(0)
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
        sys.exit(0)
