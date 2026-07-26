#!/usr/bin/env python3
"""
Meesho product search — called as subprocess by Node.js server.
Searches Meesho by product title using Google CSE, then scrapes PDP pages for price.

Actions:
  search  <title>|<image_url>  — Search Meesho via Google CSE, return best match
"""
import hashlib
import json
import os
import re
import sys
import traceback
from urllib.parse import quote

SCRAPE_DO_TOKEN = os.environ.get("SCRAPE_DO_TOKEN", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".search_cache.json")
_cache = {}

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

def _cache_key(query: str) -> str:
    return hashlib.md5(query.encode()).hexdigest()

_load_cache()

def _try_scrape_do(url: str) -> str:
    if not SCRAPE_DO_TOKEN:
        return ""
    try:
        import requests
        target = f"http://api.scrape.do?token={SCRAPE_DO_TOKEN}&super=true&url={quote(url, safe='')}"
        resp = requests.get(target, timeout=15)
        if resp.status_code == 200 and len(resp.text) > 5000:
            return resp.text
    except Exception:
        pass
    return ""

def _google_search(title: str) -> list[dict]:
    """Search Google for site:meesho.com <title> and return product URLs."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    cache_key = _cache_key(f"google_search:{title[:80]}")
    if cache_key in _cache:
        return _cache[cache_key]

    query = quote(f"site:meesho.com {title[:100]}")

    try:
        import requests
        url = f"https://www.googleapis.com/customsearch/v1?key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&q={query}&num=10"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data.get("items", [])
    except Exception:
        return []

    results = []
    seen_urls = set()

    for item in items:
        link = item.get("link", "")
        title_text = item.get("title", "")
        snippet = item.get("snippet", "")

        if not link or not title_text:
            continue

        # Filter to only Meesho product pages (not category/search/supplier pages)
        if "/p/" not in link:
            continue

        if link in seen_urls:
            continue
        seen_urls.add(link)

        results.append({
            "url": link,
            "title": title_text[:300],
            "snippet": snippet[:500],
        })

    _cache[cache_key] = results
    _save_cache()
    return results

def _scrape_pdp_price(url: str) -> dict:
    """Scrape a Meesho PDP page for price."""
    cache_key = _cache_key(f"pdp:{url}")
    if cache_key in _cache:
        return _cache[cache_key]

    html = _try_scrape_do(url)
    if not html:
        result = {"status": "failed", "error": "Could not fetch PDP"}
        _cache[cache_key] = result
        _save_cache()
        return result

    m = re.search(r'__NEXT_DATA__[^>]*>({.*?})</script>', html, re.DOTALL)
    if not m:
        result = {"status": "failed", "error": "No PDP data"}
        _cache[cache_key] = result
        _save_cache()
        return result

    try:
        data = json.loads(m.group(1), strict=False)
    except json.JSONDecodeError:
        result = {"status": "failed", "error": "Failed to parse PDP data"}
        _cache[cache_key] = result
        _save_cache()
        return result

    init = data.get("props", {}).get("pageProps", {}).get("initialState", {})
    product = init.get("product", {})
    pdp = product.get("pdpData", {}) or product.get("productDetails", {}) or {}

    price = pdp.get("minPrice") or pdp.get("price") or pdp.get("offerPrice") or 0
    title_from_pdp = pdp.get("productName") or pdp.get("name") or ""

    images = set()
    for img in (pdp.get("images") or pdp.get("productImages") or []):
        if isinstance(img, str):
            images.add(img.split("_512")[0].split("_256")[0])
        elif isinstance(img, dict):
            u = img.get("url", "")
            if u:
                images.add(u.split("_512")[0].split("_256")[0])

    result = {
        "status": "success",
        "price": f"\u20b9{price}" if price else None,
        "title": title_from_pdp[:300],
        "images": list(images),
        "description": (pdp.get("description") or "")[:1000],
    }
    _cache[cache_key] = result
    _save_cache()
    return result


def search_meesho(title: str, image_url: str = "") -> dict:
    """Search Meesho via Google CSE, find best match by image comparison."""
    cache_key = _cache_key(f"meesho_search:{title[:100]}|{image_url[:80] if image_url else ''}")
    if cache_key in _cache:
        return _cache[cache_key]

    results = _google_search(title)
    if not results:
        result = {"status": "failed", "error": "No Google search results"}
        _cache[cache_key] = result
        _save_cache()
        return result

    best_match = None
    best_score = 0

    for item in results:
        pdp_data = _scrape_pdp_price(item["url"])
        if pdp_data.get("status") != "success":
            continue

        name = pdp_data.get("title", "")
        price = pdp_data.get("price")

        # Score by image match + title overlap
        score = 0
        pdp_images = pdp_data.get("images", [])

        if image_url:
            img_base = image_url.split("_512")[0].split("_256")[0]
            if img_base in pdp_images:
                score += 100  # Exact image match

        title_lower = title.lower()
        name_lower = name.lower()
        title_words = set(title_lower.split())
        name_words = set(name_lower.split())
        if title_words and name_words:
            overlap = len(title_words & name_words)
            score += (overlap / max(len(title_words), len(name_words))) * 50

        if score > best_score:
            best_score = score
            best_match = {
                "url": item["url"],
                "price": price,
                "title": name,
                "image_match": image_url and any(img_base in pdp_images for pdp_images in [pdp_images]),
                "score": score,
            }

    if not best_match or best_score < 10:
        # Return best available even with low score
        if results:
            first = results[0]
            pdp = _scrape_pdp_price(first["url"])
            best_match = {
                "url": first["url"],
                "price": pdp.get("price"),
                "title": pdp.get("title") or first["title"],
                "image_match": False,
                "score": 0,
            }

    if not best_match:
        result = {"status": "failed", "error": "No products found"}
        _cache[cache_key] = result
        _save_cache()
        return result

    result = {
        "status": "success",
        "url": best_match["url"],
        "price": best_match["price"],
        "title": best_match["title"],
        "match_score": best_score,
        "image_match": best_match["image_match"],
    }
    _cache[cache_key] = result
    _save_cache()
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"status": "failed", "error": "Usage: _meesho_searcher.py search <title>|<image_url>"}))
        sys.exit(0)

    action = sys.argv[1]
    input_str = sys.argv[2]

    try:
        if action == "search" and "|" in input_str:
            title, image_url = input_str.split("|", 1)
            result = search_meesho(title.strip(), image_url.strip())
            print(json.dumps(result))
        elif action == "search":
            result = search_meesho(input_str.strip())
            print(json.dumps(result))
        else:
            print(json.dumps({"status": "failed", "error": f"Unknown action: {action}"}))
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
    sys.exit(0)
