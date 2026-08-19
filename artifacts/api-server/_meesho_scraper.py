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
_cache = {}  # url_hash -> product dict

def _load_cache():
    global _cache
    try:
        with open(CACHE_FILE) as f:
            _cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _cache = {}

def _save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(_cache, f)

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
    """Fetch a product page — Safari impersonation (free), then Playwright, then ScraperAPI."""
    html = _try_curl_cffi(url, impersonate="safari15_5")
    if html:
        return html
    html = _try_playwright(url)
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


# ── Real Chrome session (CDP) — uses your own browser, no automation detection ──
def _try_playwright(url: str) -> str:
    """Fetch via YOUR real Chrome (port 9222) — used as last resort when proxy fails."""
    try:
        import time, urllib.request
        from playwright.sync_api import sync_playwright

        urllib.request.urlopen(f"{SCRAPE_CDP_URL}/json/version", timeout=5)

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(SCRAPE_CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            for _ in range(15):
                time.sleep(2)
                html = page.content()
                if "__NEXT_DATA__" in html and len(html) > 5000:
                    page.close()
                    return html
            page.close()
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
        "price": f"\u20b9{price}" if price else None,
        "url": canonical_url,
        "error": None,
    }
    _cache[_cache_key(url)] = result
    _save_cache()
    return result


def _fallback_scrape(html: str, url: str) -> dict:
    """Fallback: extract product name from title tag and images from HTML."""
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
    _cache[_cache_key(url)] = result
    _save_cache()
    return result


def _is_blocked(html: str) -> bool:
    checks = [
        "Something went wrong" in html,
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


def extract_page(store_url: str, page_num: int) -> dict:
    """Extract product links from a single paginated page of a Meesho store (Chrome CDP).

    Waits for products to load (up to 20 per page) and retries the page a few
    times if it loads fewer than expected — Webshare rotates IPs so a retry often
    succeeds. Kept under ngrok's ~60s limit per call.
    """
    import time as _time
    import urllib.request
    from playwright.sync_api import sync_playwright

    url_clean = store_url.split("?")[0].rstrip("/")
    page_url = f"{url_clean}?_ms=3.0.1" if page_num <= 1 else f"{url_clean}?_ms=3.0.1&page={page_num}"

    try:
        urllib.request.urlopen(f"{EXTRACT_CDP_URL}/json/version", timeout=5)
    except Exception as e:
        return {"products": [], "hasMore": False, "error": f"Chrome CDP not available: {e}"}

    products = []
    seen = set()
    last_error = None
    # Retry while we have fewer than a full page (20) — a short page is either a
    # slow/partial load (retry helps) or the genuine last page (we accept whatever
    # we got). Accumulate unique links across attempts.
    for attempt in range(3):
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(EXTRACT_CDP_URL)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

                # Poll + scroll for products to load (up to ~10s per attempt)
                links = page.locator("a[href*='/p/']")
                deadline = _time.time() + 10
                while _time.time() < deadline and links.count() < 20:
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

                if len(products) >= 20:
                    break  # full page loaded
                # Fewer than 20 → retry (partial load or last page); stop after 3 attempts
                _time.sleep(1)
        except Exception as e:
            last_error = str(e)
            _time.sleep(1)
            continue

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
            page.close()
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
            _cache.clear()
            _save_cache()
            print(json.dumps({"status": "success", "message": "Cache cleared"}))
        else:
            print(json.dumps({"status": "failed", "error": f"Unknown action: {action}"}))
            sys.exit(0)
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
        sys.exit(0)
