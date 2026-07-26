#!/usr/bin/env python3
"""
Multi-platform product search — called as subprocess by Node.js server.
Searches Amazon.in via scrape.do, marks Flipkart/Meesho as blocked.
"""
import concurrent.futures
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import traceback
from urllib.parse import quote

import requests
from PIL import Image
from imagehash import phash

RELIABLE_THRESHOLD = 35

# Lazy-loaded AI models
_dinov2_processor = None
_dinov2_model = None
_clip_processor = None
_clip_model = None

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

def _cache_result(key: str, result: dict):
    """Only cache results that found a URL — skip caching empty/failed results so re-search works."""
    if not result or not result.get("url"):
        return
    _cache[key] = result
    _save_cache()

def _cache_key(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()

_load_cache()

# Image hash cache (keyed by image URL)
_img_hash_cache = {}

# Reverse image search cache (keyed by image URL hash)
_reverse_cache = {}

def _get_image_phash(url: str):
    """Download image and compute perceptual hash. Cached by URL."""
    if not url:
        return None
    if url in _img_hash_cache:
        return _img_hash_cache[url]
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            if content_type.startswith("image/") or resp.content[:4] in [b"\xff\xd8\xff\xe0", b"\x89PNG", b"RIFF", b"\x00\x00\x00"]:
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img = img.resize((128, 128))
                h = phash(img)
                _img_hash_cache[url] = h
                return h
    except Exception:
        pass
    _img_hash_cache[url] = None
    return None

STOP_WORDS = {
    "set", "of", "for", "with", "and", "the", "in", "to", "a", "an", "&",
    "pack", "pcs", "piece", "pieces", "new", "box", "combo", "kit",
    "type", "style", "color", "size", "each", "per", "multi",
}

def _clean_title(text: str) -> str:
    """Lower, strip, remove punctuation, remove stop words."""
    t = text.lower().strip()
    t = re.sub(r'[^\w\s/]', ' ', t)
    tokens = [w for w in t.split() if w not in STOP_WORDS and len(w) > 1]
    return " ".join(tokens)

def _get_bigrams(tokens: list) -> set:
    return set(zip(tokens, tokens[1:]))

def _score_match(title: str, name: str, image_url: str, result_image: str,
                 gajab_price: str = "", result_price: str = "") -> float:
    score = 0.0
    title_lower = title.lower().strip()
    name_lower = name.lower().strip()

    # ---- Image similarity (80% weight) ----
    image_score = 0.0
    if image_url and result_image:
        # Fast path: exact URL match
        gajab_img = image_url.split("?")[0]
        result_img = result_image.split("?")[0]
        for suffix in ["_512", "_256", "_128", "_320", "_UL320", "_SL1500"]:
            gajab_img = gajab_img.split(suffix)[0]
            result_img = result_img.split(suffix)[0]
        if gajab_img == result_img or \
           (len(gajab_img) > 30 and gajab_img in result_img) or \
           (len(result_img) > 30 and result_img in gajab_img):
            return 100.0

        # Perceptual hash comparison
        gajab_hash = _get_image_phash(image_url)
        result_hash = _get_image_phash(result_image)
        if gajab_hash is not None and result_hash is not None:
            distance = gajab_hash - result_hash
            if distance <= 5:
                image_score = 80
            elif distance <= 10:
                image_score = 68
            elif distance <= 15:
                image_score = 55
            elif distance <= 20:
                image_score = 35
            elif distance <= 25:
                image_score = 20
            elif distance <= 30:
                image_score = 10
    score += image_score

    # ---- Text matching (20% weight) ----
    cleaned_title = _clean_title(title)
    cleaned_name = _clean_title(name)

    title_tokens = cleaned_title.split()
    name_tokens = cleaned_name.split()
    title_set = set(title_tokens)
    name_set = set(name_tokens)

    if title_set and name_set:
        # Partial ratio
        shorter_words = title_tokens if len(title_tokens) <= len(name_tokens) else name_tokens
        longer_words = name_tokens if len(title_tokens) <= len(name_tokens) else title_tokens
        longer_set = set(longer_words)
        matched = sum(1 for w in shorter_words if w in longer_set)
        partial_ratio = matched / len(shorter_words) if shorter_words else 0
        score += partial_ratio * 7.5

        # Bigram overlap
        title_bigrams = _get_bigrams(title_tokens)
        name_bigrams = _get_bigrams(name_tokens)
        if title_bigrams and name_bigrams:
            bigram_overlap = len(title_bigrams & name_bigrams)
            bigram_union = len(title_bigrams | name_bigrams)
            bigram_jaccard = bigram_overlap / bigram_union if bigram_union > 0 else 0
            score += bigram_jaccard * 5

        # Standard Jaccard
        overlap = len(title_set & name_set)
        union = len(title_set | name_set)
        jaccard = overlap / union if union > 0 else 0
        score += jaccard * 4

        # Brand match
        tf = title_tokens[0] if title_tokens else ""
        nf = name_tokens[0] if name_tokens else ""
        if tf and nf and tf == nf:
            score += 1.5

        # Price proximity
        if gajab_price and result_price:
            try:
                gp = float(re.sub(r'[^\d.]', '', gajab_price))
                rp = float(re.sub(r'[^\d.]', '', result_price))
                if gp > 0 and rp > 0:
                    ratio = min(gp, rp) / max(gp, rp)
                    if ratio >= 0.7:
                        score += min(ratio * 3, 2)
            except:
                pass

        # Small penalty when text is completely unrelated but image matched
        if overlap <= 1 and max(len(title_tokens), len(name_tokens)) > 2:
            score *= 0.85

    return min(score, 100.0)


# ---- AI Model verification (DINOv2 + CLIP) ----
def _load_dinov2():
    global _dinov2_processor, _dinov2_model
    if _dinov2_model is not None:
        return
    try:
        from transformers import AutoImageProcessor, AutoModel
        _dinov2_processor = AutoImageProcessor.from_pretrained("facebook/dinov2-small")
        _dinov2_model = AutoModel.from_pretrained("facebook/dinov2-small")
    except Exception:
        pass

def _load_clip():
    global _clip_processor, _clip_model
    if _clip_model is not None:
        return
    try:
        from transformers import CLIPProcessor, CLIPModel
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
    except Exception:
        pass

_IMAGE_CACHE = {}

def _fetch_image_pil(url: str):
    if not url:
        return None
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            _IMAGE_CACHE[url] = img
            return img
    except Exception:
        pass
    _IMAGE_CACHE[url] = None
    return None

def _get_dinov2_sim(url1: str, url2: str):
    _load_dinov2()
    if _dinov2_model is None:
        return None
    img1 = _fetch_image_pil(url1)
    img2 = _fetch_image_pil(url2)
    if img1 is None or img2 is None:
        return None
    try:
        import torch
        inputs1 = _dinov2_processor(images=img1, return_tensors="pt")
        inputs2 = _dinov2_processor(images=img2, return_tensors="pt")
        with torch.no_grad():
            f1 = _dinov2_model(**inputs1).last_hidden_state.mean(dim=1)
            f2 = _dinov2_model(**inputs2).last_hidden_state.mean(dim=1)
        sim = torch.nn.functional.cosine_similarity(f1, f2).item()
        return max(0.0, min(1.0, sim))
    except Exception:
        return None

def _get_clip_sim(url1: str, url2: str):
    _load_clip()
    if _clip_model is None:
        return None
    img1 = _fetch_image_pil(url1)
    img2 = _fetch_image_pil(url2)
    if img1 is None or img2 is None:
        return None
    try:
        import torch
        inputs1 = _clip_processor(images=img1, return_tensors="pt")
        inputs2 = _clip_processor(images=img2, return_tensors="pt")
        with torch.no_grad():
            f1 = _clip_model.get_image_features(**inputs1)
            f2 = _clip_model.get_image_features(**inputs2)
        sim = torch.nn.functional.cosine_similarity(f1, f2).item()
        return max(0.0, min(1.0, sim))
    except Exception:
        return None

def _compute_verified_score(base_score: float, image_url: str, result_image: str) -> tuple[float, float, float]:
    dinov2_sim = _get_dinov2_sim(image_url, result_image)
    clip_sim = _get_clip_sim(image_url, result_image)
    model_scores = []
    if dinov2_sim is not None:
        model_scores.append(dinov2_sim * 80)
    if clip_sim is not None:
        model_scores.append(clip_sim * 75)
    final_score = base_score
    if model_scores:
        final_score = max(final_score, max(model_scores))
    return final_score, dinov2_sim or 0.0, clip_sim or 0.0


def _make_query(title: str, gajab_url: str = "") -> str:
    """Extract core search terms, prioritizing model/SKU numbers."""
    # First, try to find model/SKU numbers (e.g. "12049-1B", "DH666-15")
    model_match = re.search(r'(\b[\dA-Za-z]{3,}[-/][\dA-Za-z]+\b)', title)
    if model_match:
        return model_match.group(1)

    # Also check for bare model numbers (e.g. "B075" at start, "12049")
    model_match2 = re.search(r'\b([A-Z]{1,3}\d{3,})\b', title)
    if model_match2:
        return model_match2.group(1)

    cleaned = _clean_title(title)
    tokens = cleaned.split()

    # Keep quantity/unit numbers and model-like tokens (e.g. "90g", "36", "1/18")
    meaningful = [t for t in tokens if not re.match(r'^\d{1,3}$', t) or len(t) > 3]

    # Use top 10 meaningful tokens for better specificity
    query = ' '.join(meaningful[:10])
    return query or ' '.join(tokens[:3])


SEARCHAPI_KEY = "pmMUQZ14cBXiC3evoMVuHog6"

def _reverse_image_search(image_url: str) -> dict:
    """Use SearchAPI Google Lens to find platform product URLs."""
    result = {"amazon": [], "flipkart": [], "meesho": []}
    if not image_url:
        return result

    ck = _cache_key(f"revimg:{image_url[:80]}")
    if ck in _reverse_cache:
        return _reverse_cache[ck]

    # Verify image is accessible before calling SearchAPI (use GET — some CDNs reject HEAD)
    try:
        img_check = requests.get(image_url, timeout=10, stream=True,
                                  headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        if img_check.status_code >= 400:
            _reverse_cache[ck] = result
            return result
        img_check.close()
    except Exception:
        _reverse_cache[ck] = result
        return result

    for attempt in range(2):
        try:
            resp = requests.get(
                "https://www.searchapi.io/api/v1/search",
                params={"engine": "google_lens", "url": image_url, "api_key": SEARCHAPI_KEY, "gl": "in", "hl": "en"},
                timeout=30,
            )
            data = resp.json()
            result = {"amazon": [], "flipkart": [], "meesho": []}
            for item in data.get("visual_matches", []):
                link = (item.get("link") or "").split("?")[0].split("#")[0]
                if "amazon.in" in link and "/dp/" in link:
                    result["amazon"].append(link)
                elif "flipkart.com" in link and ("/p/" in link or "/product/" in link):
                    result["flipkart"].append(link)
                elif "meesho.com" in link and "/p/" in link:
                    result["meesho"].append(link)
            for k in result:
                result[k] = list(dict.fromkeys(result[k]))[:3]
            if result["amazon"] or result["flipkart"] or result["meesho"]:
                break
        except Exception:
            pass
    _reverse_cache[ck] = result
    return result

def _visit_platform_page(url: str, platform: str):
    """Visit product page to extract name/price/image using Playwright."""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector('#productTitle, #title, h1', timeout=8000)
            except:
                page.wait_for_timeout(2000)

            if platform == "amazon":
                info = page.evaluate("""() => {
                    const title = document.querySelector('#productTitle, #title')?.textContent?.trim() || '';
                    let price = '';
                    const offscreen = document.querySelector('.a-price .a-offscreen');
                    const offText = offscreen?.textContent?.trim() || '';
                    if (/\\d/.test(offText) && /[₹$€£]|INR|USD|GBP|EUR|AED/.test(offText)) {
                        price = offText;
                    }
                    if (!price) {
                        const priceEl = document.querySelector('.a-price-whole');
                        const whole = priceEl?.textContent?.trim().replace(/[^\\d]/g, '') || '';
                        const symbol = document.querySelector('.a-price-symbol')?.textContent?.trim() || '\\u20b9';
                        if (whole) { price = symbol + whole; }
                    }
                    const img = document.querySelector('#landingImage, #imgTagWrapperId img, .imgTagWrapper img')?.getAttribute('src') || '';
                    return { name: title, price, image: img };
                }""")
            else:
                info = page.evaluate("""() => {
                    const title = document.querySelector('h1 span, h1')?.textContent?.trim() || '';
                    const walker = document.createTreeWalker(document.body, 4, null, false);
                    let price = '';
                    let h1Idx = -1, idx = 0;
                    let node;
                    const allPrices = [];
                    while (node = walker.nextNode()) {
                        const t = node.textContent.trim();
                        if (node === document.querySelector('h1') || node.parentElement === document.querySelector('h1')) {
                            if (h1Idx === -1) h1Idx = idx;
                        }
                        if (/^₹\\s*[\\d,]+$/.test(t)) {
                            const parent = node.parentElement;
                            const style = parent ? window.getComputedStyle(parent) : null;
                            const isStrike = style && style.textDecoration === 'line-through';
                            allPrices.push({ text: t, idx, isStrike });
                        }
                        idx++;
                    }
                    // Find the first non-strikethrough price near h1 (within 50 positions after h1)
                    const nearPrice = allPrices.find(p => !p.isStrike && h1Idx >= 0 && (p.idx - h1Idx) >= 0 && (p.idx - h1Idx) < 50);
                    if (nearPrice) {
                        price = nearPrice.text;
                    }
                    // If no price near h1, product is likely out of stock — leave price empty
                    const img = document.querySelector('img[src*="rukminim"]')?.getAttribute('src')
                        || document.querySelector('[class*="image"] img, ._396cs4 img, ._2r_T1I img, .CXW8mj img')?.getAttribute('src') || '';
                    return { name: title, price, image: img };
                }""")

            context.close()
            browser.close()
            return info if info.get("name") else None
    except Exception:
        return None

def _search_amazon(title: str, image_url: str = "", gajab_price: str = "", gajab_url: str = "", rev_result=None) -> dict:
    """Search Amazon.in — first tries reverse image search, then Playwright text search."""
    cache_key = _cache_key(f"amz:{title[:80]}|{image_url[:60] if image_url else ''}")
    if cache_key in _cache:
        return _cache[cache_key]

    # ---- Step 1: Try reverse image search + DINOv2/CLIP verification ----
    rev_img_candidates = []
    rev_img_found = False
    try:
        rev = rev_result if rev_result is not None else _reverse_image_search(image_url)
        rev_urls = rev.get("amazon", [])
        rev_img_found = bool(rev_urls)

        # Deduplicate ASINs but keep original URLs (any Amazon locale)
        asin_seen = set()
        asin_map = {}
        for rev_url in rev_urls:
            asin_m = re.search(r'/dp/([A-Z0-9]{10})', rev_url)
            if asin_m and asin_m.group(1) not in asin_seen:
                asin_seen.add(asin_m.group(1))
                asin_map[asin_m.group(1)] = rev_url

        # Visit all candidates in ONE browser
        if asin_map:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    viewport={"width": 1440, "height": 900}, locale="en-IN",
                )
                candidates = []
                for asin, canonical in asin_map.items():
                    try:
                        page = context.new_page()
                        page.goto(canonical, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_selector('#productTitle, #title, #landingImage', timeout=8000)
                        except:
                            page.wait_for_timeout(2000)
                        info = page.evaluate("""() => {
                            const title = document.querySelector('#productTitle, #title')?.textContent?.trim() || '';
                            // Extract price — skip if offscreen contains non-price text (e.g. product title)
                            let price = '';
                            const offscreen = document.querySelector('.a-price .a-offscreen');
                            const offText = offscreen?.textContent?.trim() || '';
                            if (/\\d/.test(offText) && /[₹$€£]|INR|USD|GBP|EUR|AED/.test(offText)) {
                                price = offText;
                            }
                            if (!price) {
                                const priceEl = document.querySelector('.a-price-whole');
                                const whole = priceEl?.textContent?.trim().replace(/[^\\d]/g, '') || '';
                                const symbol = document.querySelector('.a-price-symbol')?.textContent?.trim() || '\\u20b9';
                                if (whole) { price = symbol + whole; }
                            }
                            const img = document.querySelector('#landingImage, #imgTagWrapperId img, .imgTagWrapper img')?.getAttribute('src') || '';
                            return { name: title, price, image: img };
                        }""")
                        page.close()
                        if info.get("name"):
                            candidates.append({"url": canonical, "info": info})
                    except Exception:
                        pass
                context.close()
                browser.close()

            # Verify ALL candidates with DINOv2 + CLIP
            verified = []
            for c in candidates:
                info = c["info"]
                d, cl = _get_dinov2_sim(image_url, info.get("image", "")), _get_clip_sim(image_url, info.get("image", ""))
                if d is not None and cl is not None and d >= 0.75 and cl >= 0.80:
                    base = _score_match(title, info["name"], image_url, info.get("image", ""), gajab_price, info.get("price", ""))
                    fs = max(base, *(s * w for s, w in [(d, 80), (cl, 75)] if s is not None))
                    verified.append({"url": c["url"], "info": info, "d": d, "cl": cl, "fs": fs})
            verified.sort(key=lambda x: x["fs"], reverse=True)

            if verified:
                best = verified[0]
                result = {
                    "status": "success",
                    "url": best["url"],
                    "price": best["info"].get("price", ""),
                    "unavailable": not bool(best["info"].get("price", "")),
                    "title": best["info"]["name"][:300],
                    "image": best["info"].get("image", ""),
                    "match_score": round(best["fs"], 1),
                    "dinov2_sim": round(best["d"], 4) if best["d"] else None,
                    "clip_sim": round(best["cl"], 4) if best["cl"] else None,
                    "reliable": best["fs"] >= RELIABLE_THRESHOLD,
                    "candidates": len(verified),
                    "source": "revimg",
                }
                _cache_result(cache_key, result)
                return result

            # Save rev img candidates for text search fallback
            rev_img_candidates = [{"url": c["url"], "name": c["info"]["name"], "price": c["info"].get("price", ""), "image": c["info"].get("image", ""), "__revimg": True} for c in candidates]
    except Exception:
        pass

    # ---- Step 2: Fall back to text search ----
    script = os.path.join(os.path.dirname(__file__), "_playwright_search.py")
    query = _make_query(title, gajab_url)
    try:
        proc = subprocess.run(
            [sys.executable, script, "amazon", query],
            capture_output=True, text=True, timeout=90
        )
        data = json.loads(proc.stdout)
    except Exception as e:
        result = {"status": "failed", "error": f"Playwright error: {e}"}
        _cache_result(cache_key, result)
        return result

    if data.get("status") != "success" or not data.get("products"):
        if not rev_img_candidates:
            result = {"status": "failed", "error": data.get("error", "No Amazon products found")}
            _cache_result(cache_key, result)
            return result
        candidates = rev_img_candidates
    else:
        candidates = data["products"]
        if rev_img_candidates:
            candidates = rev_img_candidates + candidates

    best_match = None
    best_score = 0

    for p in candidates:
        is_rev = p.get("__revimg", False)
        score = _score_match(title, p.get("name", ""), image_url, p.get("image", ""), gajab_price, p.get("price", ""))
        if is_rev:
            score += 30
        if score > best_score:
            best_score = score
            best_match = {
                "url": p.get("url", ""),
                "price": p.get("price", ""),
                "title": p.get("name", "")[:300],
                "image": p.get("image", ""),
                "score": round(score, 1),
            }

    if not best_match:
        # No text search match — try rev img candidates directly
        if rev_img_candidates:
            best = None
            best_d = 0
            for rc in rev_img_candidates:
                d, cl = _get_dinov2_sim(image_url, rc.get("image", "")), _get_clip_sim(image_url, rc.get("image", ""))
                if d is not None and cl is not None and (d >= 0.50 or cl >= 0.60) and d > best_d:
                    best_d = d
                    best = rc
            if best:
                final_score, dinov2_sim, clip_sim = _compute_verified_score(0, image_url, best.get("image", ""))
                result = {
                    "status": "success",
                    "url": best["url"],
                    "price": best.get("price", ""),
                    "unavailable": not bool(best.get("price", "")),
                    "title": best.get("name", "")[:300],
                    "image": best.get("image", ""),
                    "match_score": round(final_score, 1),
                    "dinov2_sim": round(dinov2_sim, 4) if dinov2_sim else None,
                    "clip_sim": round(clip_sim, 4) if clip_sim else None,
                    "reliable": final_score >= RELIABLE_THRESHOLD,
                    "candidates": len(candidates),
                    "source": "search",
                }
                _cache_result(cache_key, result)
                return result
        result = {
            "status": "failed",
            "error": "No match found",
            "candidates": len(candidates),
        }
        _cache_result(cache_key, result)
        return result

    final_score, dinov2_sim, clip_sim = _compute_verified_score(
        best_score, image_url, best_match.get("image", "")
    )

    # Conditional gate: strict (text-only) vs relaxed (visual candidates exist)
    if rev_img_found:
        gate_passed = dinov2_sim is not None and clip_sim is not None and not (dinov2_sim < 0.50 and clip_sim < 0.60)
    else:
        gate_passed = dinov2_sim is not None and clip_sim is not None and dinov2_sim >= 0.70 and clip_sim >= 0.70

    if not gate_passed:
        if rev_img_candidates:
            best = None
            best_d = 0
            for rc in rev_img_candidates:
                d, cl = _get_dinov2_sim(image_url, rc.get("image", "")), _get_clip_sim(image_url, rc.get("image", ""))
                if d is not None and cl is not None and (d >= 0.50 or cl >= 0.60) and d > best_d:
                    best_d = d
                    best = rc
            if best:
                final_score, dinov2_sim, clip_sim = _compute_verified_score(0, image_url, best.get("image", ""))
                best_match = {"url": best["url"], "price": best.get("price", ""), "title": best.get("name", "")[:300], "image": best.get("image", ""), "score": round(final_score, 1)}
                gate_passed = True

    if not gate_passed:
        result = {
            "status": "failed",
            "error": "Low confidence match",
            "candidates": len(candidates),
        }
        _cache_result(cache_key, result)
        return result

    result = {
        "status": "success",
        "url": best_match["url"],
        "price": best_match["price"],
        "unavailable": not bool(best_match.get("price", "")),
        "title": best_match["title"],
        "image": best_match.get("image", ""),
        "match_score": round(final_score, 1),
        "dinov2_sim": round(dinov2_sim, 4) if dinov2_sim else None,
        "clip_sim": round(clip_sim, 4) if clip_sim else None,
        "reliable": final_score >= RELIABLE_THRESHOLD,
        "candidates": len(candidates),
        "source": "search",
    }
    _cache_result(cache_key, result)
    return result


def _search_flipkart(title: str, image_url: str = "", gajab_price: str = "", gajab_url: str = "", rev_result=None) -> dict:
    """Search Flipkart using Playwright (bypasses E002)."""
    cache_key = _cache_key(f"fk:{title[:80]}|{image_url[:60] if image_url else ''}")
    if cache_key in _cache:
        return _cache[cache_key]

    # ---- Step 1: Try reverse image search + DINOv2/CLIP verification ----
    rev_img_candidates = []
    rev_img_found = False
    try:
        rev = rev_result if rev_result is not None else _reverse_image_search(image_url)
        rev_urls = rev.get("flipkart", [])
        rev_img_found = bool(rev_urls)
        fk_urls = [u for u in rev_urls if "flipkart.com" in u and "/p/" in u][:3]

        if fk_urls:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    viewport={"width": 1440, "height": 900}, locale="en-IN",
                )
                candidates = []
                for fk_url in fk_urls:
                    try:
                        page = context.new_page()
                        page.goto(fk_url, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_selector('h1 span, h1', timeout=8000)
                        except:
                            page.wait_for_timeout(2000)
                        info = page.evaluate("""() => {
                            const title = document.querySelector('h1 span, h1')?.textContent?.trim() || '';
                            const walker = document.createTreeWalker(document.body, 4, null, false);
                            let price = '', h1Idx = -1, idx = 0;
                            let node;
                            const allPrices = [];
                            while (node = walker.nextNode()) {
                                const t = node.textContent.trim();
                                if (node === document.querySelector('h1') || node.parentElement === document.querySelector('h1'))
                                    if (h1Idx === -1) h1Idx = idx;
                                if (/^₹\\s*[\\d,]+$/.test(t)) {
                                    const parent = node.parentElement;
                                    const style = parent ? window.getComputedStyle(parent) : null;
                                    const isStrike = style && style.textDecoration === 'line-through';
                                    allPrices.push({ text: t, idx, isStrike });
                                }
                                idx++;
                            }
                            const nearPrice = allPrices.find(p => !p.isStrike && h1Idx >= 0 && (p.idx - h1Idx) >= 0 && (p.idx - h1Idx) < 50);
                            if (nearPrice) price = nearPrice.text;
                            const img = document.querySelector('img[src*="rukminim"]')?.getAttribute('src')
                                || document.querySelector('[class*="image"] img, ._396cs4 img, ._2r_T1I img, .CXW8mj img')?.getAttribute('src') || '';
                            return { name: title, price, image: img };
                        }""")
                        page.close()
                        if info.get("name"):
                            candidates.append({"url": fk_url, "info": info})
                    except Exception:
                        pass
                context.close()
                browser.close()

            # Verify ALL candidates with DINOv2 + CLIP
            verified = []
            for c in candidates:
                info = c["info"]
                d, cl = _get_dinov2_sim(image_url, info.get("image", "")), _get_clip_sim(image_url, info.get("image", ""))
                if d is not None and cl is not None and d >= 0.75 and cl >= 0.80:
                    base = _score_match(title, info["name"], image_url, info.get("image", ""), gajab_price, info.get("price", ""))
                    fs = max(base, *(s * w for s, w in [(d, 80), (cl, 75)] if s is not None))
                    verified.append({"url": c["url"], "info": info, "d": d, "cl": cl, "fs": fs})
            verified.sort(key=lambda x: x["fs"], reverse=True)

            if verified:
                best = verified[0]
                result = {
                    "status": "success",
                    "url": best["url"],
                    "price": best["info"].get("price", ""),
                    "unavailable": not bool(best["info"].get("price", "")),
                    "title": best["info"]["name"][:300],
                    "image": best["info"].get("image", ""),
                    "match_score": round(best["fs"], 1),
                    "dinov2_sim": round(best["d"], 4) if best["d"] else None,
                    "clip_sim": round(best["cl"], 4) if best["cl"] else None,
                    "reliable": best["fs"] >= RELIABLE_THRESHOLD,
                    "candidates": len(verified),
                    "source": "revimg",
                }
                _cache_result(cache_key, result)
                return result

            # Save rev img candidates for text search fallback
            rev_img_candidates = [{"url": c["url"], "name": c["info"]["name"], "price": c["info"].get("price", ""), "image": c["info"].get("image", ""), "__revimg": True} for c in candidates]
    except Exception:
        pass

    # ---- Step 2: Fall back to text search ----
    script = os.path.join(os.path.dirname(__file__), "_playwright_search.py")
    query = _make_query(title, gajab_url)
    try:
        proc = subprocess.run(
            [sys.executable, script, "flipkart", query],
            capture_output=True, text=True, timeout=90
        )
        data = json.loads(proc.stdout)
    except Exception as e:
        result = {"status": "failed", "error": f"Playwright error: {e}"}
        _cache_result(cache_key, result)
        return result

    if data.get("status") != "success" or not data.get("products"):
        if not rev_img_candidates:
            result = {"status": "failed", "error": data.get("error", "No Flipkart products found")}
            _cache_result(cache_key, result)
            return result
        candidates = rev_img_candidates
    else:
        candidates = data["products"]
        if rev_img_candidates:
            candidates = rev_img_candidates + candidates

    best_match = None
    best_score = 0

    for p in candidates:
        is_rev = p.get("__revimg", False)
        score = _score_match(title, p.get("name", ""), image_url, p.get("image", ""), gajab_price, p.get("price", ""))
        if is_rev:
            score += 30
        if score > best_score:
            best_score = score
            best_match = {
                "url": p.get("url", ""),
                "price": p.get("price", ""),
                "title": p.get("name", "")[:300],
                "image": p.get("image", ""),
                "score": round(score, 1),
            }

    if not best_match:
        # No text search match — try rev img candidates directly
        if rev_img_candidates:
            best = None
            best_d = 0
            for rc in rev_img_candidates:
                d, cl = _get_dinov2_sim(image_url, rc.get("image", "")), _get_clip_sim(image_url, rc.get("image", ""))
                if d is not None and cl is not None and (d >= 0.50 or cl >= 0.60) and d > best_d:
                    best_d = d
                    best = rc
            if best:
                final_score, dinov2_sim, clip_sim = _compute_verified_score(0, image_url, best.get("image", ""))
                result = {
                    "status": "success",
                    "url": best["url"],
                    "price": best.get("price", ""),
                    "unavailable": not bool(best.get("price", "")),
                    "title": best.get("name", "")[:300],
                    "image": best.get("image", ""),
                    "match_score": round(final_score, 1),
                    "dinov2_sim": round(dinov2_sim, 4) if dinov2_sim else None,
                    "clip_sim": round(clip_sim, 4) if clip_sim else None,
                    "reliable": final_score >= RELIABLE_THRESHOLD,
                    "candidates": len(candidates),
                    "source": "search",
                }
                _cache_result(cache_key, result)
                return result
        result = {
            "status": "failed",
            "error": "No match found",
            "candidates": len(candidates),
        }
        _cache_result(cache_key, result)
        return result

    final_score, dinov2_sim, clip_sim = _compute_verified_score(
        best_score, image_url, best_match.get("image", "")
    )

    # Conditional gate: strict (text-only) vs relaxed (visual candidates exist)
    if rev_img_found:
        gate_passed = dinov2_sim is not None and clip_sim is not None and not (dinov2_sim < 0.50 and clip_sim < 0.60)
    else:
        gate_passed = dinov2_sim is not None and clip_sim is not None and dinov2_sim >= 0.70 and clip_sim >= 0.70

    if not gate_passed:
        if rev_img_candidates:
            best = None
            best_d = 0
            for rc in rev_img_candidates:
                d, cl = _get_dinov2_sim(image_url, rc.get("image", "")), _get_clip_sim(image_url, rc.get("image", ""))
                if d is not None and cl is not None and (d >= 0.50 or cl >= 0.60) and d > best_d:
                    best_d = d
                    best = rc
            if best:
                final_score, dinov2_sim, clip_sim = _compute_verified_score(0, image_url, best.get("image", ""))
                best_match = {"url": best["url"], "price": best.get("price", ""), "title": best.get("name", "")[:300], "image": best.get("image", ""), "score": round(final_score, 1)}
                gate_passed = True

    if not gate_passed:
        result = {
            "status": "failed",
            "error": "Low confidence match",
            "candidates": len(candidates),
        }
        _cache_result(cache_key, result)
        return result

    result = {
        "status": "success",
        "url": best_match["url"],
        "price": best_match["price"],
        "unavailable": not bool(best_match.get("price", "")),
        "title": best_match["title"],
        "image": best_match.get("image", ""),
        "match_score": round(final_score, 1),
        "dinov2_sim": round(dinov2_sim, 4) if dinov2_sim else None,
        "clip_sim": round(clip_sim, 4) if clip_sim else None,
        "reliable": final_score >= RELIABLE_THRESHOLD,
        "candidates": len(candidates),
        "source": "search",
    }
    _cache_result(cache_key, result)
    return result


def _try_meesho_scraper(url: str):
    """Try to scrape a Meesho product page via _meesho_scraper.py subprocess."""
    try:
        script = os.path.join(os.path.dirname(__file__), "_meesho_scraper.py")
        env = {**os.environ}
        proc = subprocess.run(
            [sys.executable, script, "scrape", url],
            capture_output=True, text=True, timeout=30,
            env=env,
        )
        data = json.loads(proc.stdout)
        if data.get("status") == "success" and data.get("price"):
            return data
    except Exception:
        pass
    return None


def _search_meesho(title: str, image_url: str = "", gajab_price: str = "", gajab_url: str = "", rev_result=None) -> dict:
    """Search Meesho — extracts Meesho URLs from reverse image search, tries to scrape."""
    cache_key = _cache_key(f"ms:{title[:80]}|{image_url[:60] if image_url else ''}")
    if cache_key in _cache:
        return _cache[cache_key]

    # ---- Step 1: Try reverse image search + DINOv2/CLIP verification ----
    try:
        rev = rev_result if rev_result is not None else _reverse_image_search(image_url)
        rev_urls = rev.get("meesho", [])
        for rev_url in rev_urls:
            if "meesho.com" in rev_url and "/p/" in rev_url:
                scraped = _try_meesho_scraper(rev_url)
                if scraped and scraped.get("price"):
                    img = scraped.get("imageUrl") or ""
                    if img:
                        d, cl = _get_dinov2_sim(image_url, img), _get_clip_sim(image_url, img)
                        if d is not None and cl is not None and d >= 0.75 and cl >= 0.80:
                            ms = max(75.0, *(s * w for s, w in [(d, 80), (cl, 75)] if s is not None))
                            result = {
                                "status": "success",
                                "url": rev_url,
                                "price": scraped.get("price", ""),
                                "unavailable": False,
                                "title": (scraped.get("title") or "")[:300],
                                "image": img,
                                "match_score": round(ms, 1),
                                "dinov2_sim": round(d, 4) if d else None,
                                "clip_sim": round(cl, 4) if cl else None,
                                "reliable": ms >= RELIABLE_THRESHOLD,
                                "candidates": 1,
                                "source": "revimg",
                            }
                            _cache_result(cache_key, result)
                            return result
    except Exception:
        pass

    # ---- Step 2: Google site search DISABLED (saves SearchAPI credits) ----
    # Previously used SearchAPI google web search for site:meesho.com, now skipped.

    # ---- Step 3: Fall back to text search via Playwright ----
    script = os.path.join(os.path.dirname(__file__), "_playwright_search.py")
    query = _make_query(title, gajab_url)
    try:
        proc = subprocess.run(
            [sys.executable, script, "meesho", query],
            capture_output=True, text=True, timeout=90
        )
        data = json.loads(proc.stdout)
    except Exception as e:
        result = {"status": "failed", "error": f"Playwright error: {e}"}
        _cache_result(cache_key, result)
        return result

    if data.get("status") != "success" or not data.get("products"):
        result = {"status": "failed", "error": data.get("error", "No Meesho products found")}
        _cache_result(cache_key, result)
        return result

    best_match = None
    best_score = 0
    candidates = data["products"]

    for p in candidates:
        is_rev = p.get("__revimg", False)
        score = _score_match(title, p.get("name", ""), image_url, p.get("image", ""), gajab_price, p.get("price", ""))
        if is_rev:
            score += 30
        if score > best_score:
            best_score = score
            best_match = {
                "url": p.get("url", ""),
                "price": p.get("price", ""),
                "title": p.get("name", "")[:300],
                "image": p.get("image", ""),
                "score": round(score, 1),
            }

    if not best_match:
        result = {
            "status": "failed",
            "error": "No match found",
            "candidates": len(candidates),
        }
        _cache_result(cache_key, result)
        return result

    final_score, dinov2_sim, clip_sim = _compute_verified_score(
        best_score, image_url, best_match.get("image", "")
    )

    if dinov2_sim is None or clip_sim is None or (dinov2_sim < 0.50 and clip_sim < 0.60):
        result = {
            "status": "failed",
            "error": "Low confidence match",
            "candidates": len(candidates),
        }
        _cache_result(cache_key, result)
        return result

    result = {
        "status": "success",
        "url": best_match["url"],
        "price": best_match["price"],
        "unavailable": not bool(best_match.get("price", "")),
        "title": best_match["title"],
        "image": best_match.get("image", ""),
        "match_score": round(final_score, 1),
        "dinov2_sim": round(dinov2_sim, 4) if dinov2_sim else None,
        "clip_sim": round(clip_sim, 4) if clip_sim else None,
        "reliable": final_score >= RELIABLE_THRESHOLD,
        "candidates": len(candidates),
        "source": "search",
    }
    _cache_result(cache_key, result)
    return result


def search_all(title: str, image_url: str = "", gajab_price: str = "", gajab_url: str = "") -> dict:
    """Search all platforms for a product."""
    result = {
        "title": title,
        "amazon": {"status": "skipped"},
        "flipkart": {"status": "skipped"},
        "meesho": {"status": "skipped"},
    }

    # Reverse image search once, share across all platforms
    rev_result = _reverse_image_search(image_url) if image_url else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut_map = {
            ex.submit(_search_amazon, title, image_url, gajab_price, gajab_url, rev_result): "amazon",
            ex.submit(_search_flipkart, title, image_url, gajab_price, gajab_url, rev_result): "flipkart",
            ex.submit(_search_meesho, title, image_url, gajab_price, gajab_url, rev_result): "meesho",
        }
        for fut in concurrent.futures.as_completed(fut_map):
            platform = fut_map[fut]
            try:
                result[platform] = fut.result()
            except Exception as e:
                result[platform] = {"status": "failed", "error": str(e)}

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"status": "failed", "error": "Usage: _platform_searcher.py search <title>|<image_url>"}))
        sys.exit(0)

    action = sys.argv[1]
    input_str = sys.argv[2]

    try:
        if action == "search":
            if input_str.startswith("{"):
                params = json.loads(input_str)
                title = params.get("title", "")
                image_url = params.get("image", "")
                gajab_price = params.get("price", "")
                gajab_url = params.get("url", "")
            elif "|" in input_str:
                parts = input_str.split("|")
                # Heuristic: find image by URL pattern, price by number pattern, URL by gajab.com
                # Image contains resize.gajab.com or ends with image extension
                # Price looks like a number (optionally with ₹)
                # Product URL starts with https://gajab.com/
                img_idx = None
                for i, p in enumerate(parts):
                    s = p.strip()
                    if "resize.gajab.com" in s or (s.startswith("http") and any(s.lower().endswith(x) for x in [".jpg",".jpeg",".png",".webp"])):
                        img_idx = i; break
                if img_idx is None:
                    # Fallback: first part that starts with http
                    for i, p in enumerate(parts):
                        if p.strip().startswith("http"):
                            img_idx = i; break
                if img_idx is not None:
                    title = "|".join(parts[:img_idx]).strip()
                    after = parts[img_idx+1:]
                    image_url = parts[img_idx].strip()
                    # Find price: first non-empty part that looks like a number
                    price_idx = None
                    for i, p in enumerate(after):
                        s = p.strip()
                        if s and __import__("re").match(r'^₹?\s*[\d,.]+\s*$', s):
                            price_idx = i; break
                    if price_idx is not None:
                        gajab_price = after[price_idx].strip()
                        url_parts = after[price_idx+1:]
                        gajab_url = "".join(p.strip() for p in url_parts)
                    else:
                        gajab_price = ""
                        gajab_url = "".join(p.strip() for p in after)
                else:
                    title = parts[0].strip()
                    image_url = parts[1].strip() if len(parts) > 1 else ""
                    gajab_price = parts[2].strip() if len(parts) > 2 else ""
                    gajab_url = parts[3].strip() if len(parts) > 3 else ""
            else:
                title = input_str.strip()
                image_url = ""
                gajab_price = ""
                gajab_url = ""
            result = search_all(title, image_url, gajab_price, gajab_url)
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()
        else:
            print(json.dumps({"status": "failed", "error": f"Unknown action: {action}"}))
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
