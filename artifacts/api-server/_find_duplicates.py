#!/usr/bin/env python3
"""Find visually identical products (duplicates) using pHash + DINOv2 verification."""

import json, os, sys, io, hashlib, time, requests, threading
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
from collections import defaultdict
from PIL import Image
from imagehash import phash
import torch
from transformers import AutoImageProcessor, AutoModel

SUPABASE_URL = "https://okxyskmjsmtykblrtmyi.supabase.co"
SUPABASE_KEY = "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp"
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".duplicates_cache.json")

DINOV2_THRESHOLD = 0.95
P_HASH_THRESHOLD = 10

_dinov2_processor = None
_dinov2_model = None
_dinov2_lock = threading.Lock()

def _load_dinov2():
    global _dinov2_processor, _dinov2_model
    if _dinov2_model is not None: return
    with _dinov2_lock:
        if _dinov2_model is not None: return
        print("Loading DINOv2...")
        _dinov2_processor = AutoImageProcessor.from_pretrained("facebook/dinov2-small", use_fast=True)
        _dinov2_model = AutoModel.from_pretrained("facebook/dinov2-small")
        _dinov2_model.eval()
        print("DINOv2 loaded")

def _dinov2_similarity(img1, img2):
    global _dinov2_processor, _dinov2_model
    if _dinov2_model is None: _load_dinov2()
    inputs1 = _dinov2_processor(images=img1, return_tensors="pt")
    inputs2 = _dinov2_processor(images=img2, return_tensors="pt")
    with torch.no_grad():
        emb1 = _dinov2_model(**inputs1).last_hidden_state.mean(dim=1)
        emb2 = _dinov2_model(**inputs2).last_hidden_state.mean(dim=1)
    return torch.nn.functional.cosine_similarity(emb1, emb2).item()

def _fetch_json(url):
    hdrs = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    resp = requests.get(url, headers=hdrs, timeout=60)
    return resp.json()

def _fetch_all(table, select="*", extra=""):
    rows, offset = [], 0
    while True:
        url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}&limit=1000&offset={offset}"
        if extra: url += f"&{extra}"
        data = _fetch_json(url)
        if not data or not isinstance(data, list): break
        rows.extend(data)
        if len(data) < 1000: break
        offset += 1000
    return rows

def _download_image(url, timeout=15):
    if not url: return None
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except: pass
    return None

def _img_hash(img):
    if img is None: return None
    try: return phash(img)
    except: return None

def _name_key(name):
    """Normalize name for fuzzy comparison."""
    if not name: return ""
    n = name.lower().strip()
    # Remove common suffixes/prefixes that don't affect product identity
    import re
    n = re.sub(r'\b(pack of \d+|set of \d+|multicolor|multicolour|random color|random colour|assorted|as per stock|as per availability|pack of)\b', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n

def main():
    print("Fetching products...")
    prods = _fetch_all("products", "id,name,image_url,brand,category")
    prod_map = {p["id"]: p for p in prods}
    print(f"Products: {len(prods)}")

    print("Fetching mappings...")
    mappings = _fetch_all("price_mappings", "id,gajab_product_id,gajab_title,gajab_image_url,amazon_url,flipkart_url,meesho_url,search_error")
    print(f"Mappings: {len(mappings)}")

    # Build index of mapped products
    mapped_ids = set()
    for m in mappings:
        if m.get("amazon_url") or m.get("flipkart_url") or m.get("meesho_url"):
            mapped_ids.add(m["gajab_product_id"])

    # Known duplicates from previous runs (stored in search_error)
    known_dups = set()
    for m in mappings:
        err = m.get("search_error")
        if err:
            try:
                e = json.loads(err) if isinstance(err, str) else err
                if isinstance(e, dict) and "duplicate_of" in e:
                    known_dups.add(m["gajab_product_id"])
            except: pass

    # Unmapped products (excluding known dups)
    unmapped_ids = [p["id"] for p in prods if p["id"] not in mapped_ids and p["id"] not in known_dups]

    # Group by brand for faster candidate matching
    unmapped_by_brand = defaultdict(list)
    mapped_by_brand = defaultdict(list)
    for p in prods:
        brand = (p.get("brand") or "").upper().strip()
        if p["id"] in mapped_ids:
            mapped_by_brand[brand].append(p["id"])
        elif p["id"] not in known_dups:
            unmapped_by_brand[brand].append(p["id"])

    print(f"Mapped: {len(mapped_ids)}, Unmapped: {len(unmapped_ids)}, Known dups: {len(known_dups)}")

    # Download images — mapped first (for reference)
    print(f"\nDownloading mapped images...")
    mapped_images = {}
    def _dl(pid):
        p = prod_map.get(pid)
        if not p: return None
        url = p.get("image_url")
        if not url:
            m = next((m for m in mappings if m["gajab_product_id"] == pid), None)
            if m: url = m.get("gajab_image_url")
        if not url: return None
        img = _download_image(url)
        if img is None: return None
        return (pid, img)
    
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_dl, pid): pid for pid in mapped_ids}
        for f in as_completed(futs):
            r = f.result()
            if r:
                pid, img = r
                h = _img_hash(img)
                if h is not None:
                    mapped_images[pid] = (h, img)

    print(f"Mapped images: {len(mapped_images)}")

    # Build pHash index for fast lookup
    # For each brand, get mapped image hashes
    brand_mapped_hashes = defaultdict(list)
    for brand, pids in mapped_by_brand.items():
        for pid in pids:
            if pid in mapped_images:
                brand_mapped_hashes[brand].append((pid, mapped_images[pid][0]))

    # Load DINOv2 model before threading
    _load_dinov2()

    # Download and check unmapped products
    print(f"\nProcessing unmapped...")
    dupes = {}
    checked = 0
    no_brand = 0
    skipped_brand_match = 0

    def _process_unmapped(pid):
        p = prod_map.get(pid)
        if not p: return None
        brand = (p.get("brand") or "").upper().strip()
        img = _download_image(p.get("image_url"))
        if img is None: return None
        uh = _img_hash(img)
        if uh is None: return None
        
        # Find candidates: same brand products with close pHash
        candidates = []
        for mpid, mh in brand_mapped_hashes.get(brand, []):
            dist = abs(uh - mh)
            if dist < P_HASH_THRESHOLD:
                candidates.append((dist, mpid))
        
        # Also check if name is similar to any mapped product with close pHash
        if not candidates:
            # Check other brands for close pHash match
            for mpid, (mh, _) in mapped_images.items():
                dist = abs(uh - mh)
                if dist < P_HASH_THRESHOLD:
                    candidates.append((dist, mpid))
                    if len(candidates) >= 3:
                        break
        
        if not candidates:
            return None
        
        # Best candidate
        candidates.sort()
        best_mpid = candidates[0][1]
        best_dist = candidates[0][0]
        mimg = mapped_images[best_mpid][1]
        
        # DINOv2 verify
        dsim = _dinov2_similarity(img, mimg)
        if dsim >= DINOV2_THRESHOLD:
            return (pid, best_mpid, round(dsim, 4))
        return None

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_process_unmapped, pid): pid for pid in unmapped_ids}
        done = 0
        for f in as_completed(futs):
            r = f.result()
            done += 1
            if r:
                dupes[r[0]] = {"duplicate_of": r[1], "dinov2_score": r[2]}
            if done % 200 == 0:
                print(f"  Processed {done}/{len(unmapped_ids)}, found {len(dupes)} dupes")

    print(f"\nDuplicates found: {len(dupes)}")

    # Merge with existing cache
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
        except: pass
    cache.update(dupes)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"Saved to {CACHE_FILE}")

    # Also write to Supabase price_mappings (search_error field)
    hdrs = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}
    updated = 0
    s = requests.Session()
    for upid, info in dupes.items():
        payload = json.dumps({"search_error": json.dumps(info)})
        # Upsert: try update first
        existing = [m for m in mappings if m.get("gajab_product_id") == upid]
        if existing:
            resp = s.patch(f'{SUPABASE_URL}/rest/v1/price_mappings?id=eq.{existing[0]["id"]}', payload, headers=hdrs, timeout=15)
            if resp.status_code < 300:
                updated += 1
        else:
            new_row = {
                "gajab_product_id": upid,
                "gajab_title": prod_map.get(upid, {}).get("name", ""),
                "gajab_image_url": prod_map.get(upid, {}).get("image_url", ""),
                "gajab_price": prod_map.get(upid, {}).get("price", ""),
                "gajab_url": prod_map.get(upid, {}).get("url", ""),
                "search_error": json.dumps(info),
            }
            new_row = {k: v for k, v in new_row.items() if v}
            resp = s.post(f'{SUPABASE_URL}/rest/v1/price_mappings', json.dumps(new_row), headers=hdrs, timeout=15)
            if resp.status_code < 300:
                updated += 1
    s.close()
    print(f"Supabase updated: {updated}")

if __name__ == "__main__":
    main()
