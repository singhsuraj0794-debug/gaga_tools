#!/usr/bin/env python3
"""Find duplicate products within an uploaded sheet using image similarity.

Proper all-image matching with pHash-aware stock-photo filtering:
  Phase 1: Download ALL images, compute pHash per image
  Phase 1b: Identify stock photos using pHash distance clustering
  Phase 2: Candidate generation using only UNIQUE (non-stock) images
  Phase 3: Parallelized pairwise comparison on unique images — threshold 4

Usage: python3 _sheet_duplicates.py <input.json> [threshold]
"""
import json
import sys
import os
import re
import time
import tempfile
import warnings
from typing import List, Dict, Optional, Tuple, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image

warnings.filterwarnings("ignore")

PROXIED_RE = re.compile(r'^https://resize\.gajab\.com/V[^/]+/(https?://.*)')

# URL -> disk path (str) or None if download failed. Images are stored on DISK
# (not as decoded PIL objects in RAM) so large sheets (1000s of images) don't
# OOM the process. Files persist across runs in the temp dir, so re-running the
# same sheet reuses downloads instead of re-fetching everything.
# Persistent disk cache location — NOT the OS temp dir (macOS clears
# /var/folders on reboot, forcing a full re-download every run). Under
# ~/Library/Caches it survives reboots so re-runs reuse downloaded images.
_CACHE_DIR = os.path.join(os.path.expanduser("~/Library/Caches"), "gajab_sheet_dup_cache")
try:
    os.makedirs(_CACHE_DIR, exist_ok=True)
except OSError:
    _CACHE_DIR = tempfile.gettempdir()

_IMAGE_CACHE: Dict[str, Optional[str]] = {}

STOCK_PHOTO_THRESHOLD = 0.30  # pHash cluster shared by >30% of products = stock
MAX_CANDIDATE_PAIRS = 50000   # safety cap
MAX_PRODUCTS_IN_BUCKET = 500  # skip LSH buckets larger than this


def clean_url(url: str) -> str:
    if not url:
        return ""
    m = PROXIED_RE.match(url)
    return m.group(1) if m else url


def _cache_path(url: str) -> str:
    import hashlib
    return os.path.join(_CACHE_DIR, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".img")


def _fetch_one(url: str, timeout: int = 8) -> Optional[str]:
    """Download an image to the disk cache and return its file path (or None).
    Reuses a previously cached file, so memory stays flat and re-runs are fast."""
    if not url:
        return None
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]
    cache_path = _cache_path(url)
    # Reuse a file left on disk from an earlier run
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        _IMAGE_CACHE[url] = cache_path
        return cache_path
    candidates = [url]
    cleaned = clean_url(url)
    if cleaned != url:
        candidates.append(cleaned)
    for u in candidates:
        try:
            resp = requests.get(u, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "image/png,image/jpeg,*/*",
            })
            if resp.status_code == 200 and len(resp.content) > 0:
                tmp = cache_path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(resp.content)
                # Verify it decodes as a valid image before caching.
                try:
                    with Image.open(tmp) as im:
                        im.verify()
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    continue
                os.replace(tmp, cache_path)
                _IMAGE_CACHE[url] = cache_path
                return cache_path
        except Exception:
            pass
    _IMAGE_CACHE[url] = None
    return None


def _collect_urls(products: List[dict]) -> List[str]:
    urls = []
    seen = set()
    for p in products:
        for url in p.get("images", [])[:10]:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _prefetch_images(urls: List[str], workers: int = 80) -> None:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, u): u for u in urls}
        done = 0
        total = len(futures)
        for f in as_completed(futures):
            done += 1
            if done % 100 == 0 or done == total:
                pct = int(done * 100 / total) if total else 0
                print(f"[DUP]   Downloaded {done}/{total} images ({pct}%)", file=sys.stderr)
                sys.stderr.flush()


def compute_phash(img: Image.Image):
    import imagehash
    return imagehash.phash(img)


def _compute_product_hashes(product: dict) -> List[Tuple[str, object]]:
    hashes = []
    for url in product.get("images", [])[:10]:
        path = _fetch_one(url)
        if path is None:
            continue
        try:
            with Image.open(path) as img:
                img = img.convert("RGB")
                h = compute_phash(img)
            hashes.append((url, h))
        except Exception:
            pass
    return hashes


def compute_image_overlap(
    hashes_a: List[Tuple[str, object]],
    hashes_b: List[Tuple[str, object]],
    threshold: int = 6,
) -> Tuple[int, int]:
    matched = 0
    used_b = set()
    for url_a, hash_a in hashes_a:
        best_idx = None
        best_dist = 999
        for j, (url_b, hash_b) in enumerate(hashes_b):
            if j in used_b:
                continue
            dist = hash_a - hash_b
            if dist < best_dist:
                best_dist = dist
                best_idx = j
        if best_idx is not None and best_dist <= threshold:
            matched += 1
            used_b.add(best_idx)
    max_possible = max(len(hashes_a), len(hashes_b))
    return matched, max_possible


def _multi_table_keys(hval: int, n_tables: int = 4) -> List[int]:
    base = 64 // n_tables
    keys = []
    for t in range(n_tables):
        shift = 64 - (t + 1) * base
        mask = (1 << base) - 1
        keys.append((hval >> shift) & mask)
    return keys


def _hash_int(hash_obj) -> int:
    return int(str(hash_obj), 16)


def _hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _build_groups(products, find_fn, n, overlap_info):
    groups_map: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups_map[find_fn(i)].append(i)
    out_groups = []
    all_remove_skus = []
    for root, members in groups_map.items():
        if len(members) < 2:
            continue
        def sort_key(idx):
            p = products[idx]
            return (-(len(p.get("title", "") or "")), p.get("sku", ""))
        members_sorted = sorted(members, key=sort_key)
        keep_idx = members_sorted[0]
        remove_idxs = members_sorted[1:]
        keep_product = products[keep_idx]
        best_overlap = {"matched": 0, "total_a": 0, "total_b": 0}
        for ri in remove_idxs:
            key = (min(keep_idx, ri), max(keep_idx, ri))
            info = overlap_info.get(key, {})
            if info.get("matched", 0) > best_overlap["matched"]:
                best_overlap = info
        matched_count = best_overlap.get("matched", 0)
        total_a = best_overlap.get("total_a", 0)
        total_b = best_overlap.get("total_b", 0)
        total_images = max(total_a, total_b, 1)
        similarity = matched_count / total_images if total_images > 0 else 0.0
        if matched_count == 0 and total_a == 0 and total_b == 0:
            match_type = "no_images_loaded"
        elif similarity >= 0.95:
            match_type = "all_images_match"
        elif similarity >= 0.6:
            match_type = "most_images_match"
        else:
            match_type = "partial_image_match"
        remove_items = []
        for ri in remove_idxs:
            p = products[ri]
            if match_type == "url_match":
                reason = f"Duplicate of {keep_product.get('sku', '?')}: shared image URL"
            else:
                reason = f"Duplicate of {keep_product.get('sku', '?')}: {matched_count}/{total_images} images match ({match_type})"
            remove_items.append({"sku": p.get("sku", ""), "title": p.get("title", ""), "reason": reason})
            all_remove_skus.append(p.get("sku", ""))
        out_groups.append({
            "keep": {"sku": keep_product.get("sku", ""), "title": keep_product.get("title", ""), "reason": f"Kept: {len(keep_product.get('images', []))} images"},
            "remove": remove_items,
            "similarity": round(similarity, 3),
            "match_type": match_type,
            "matched_images": matched_count,
            "total_images": total_images,
        })
    out_groups.sort(key=lambda g: len(g["remove"]), reverse=True)
    return out_groups


def _detect_stock_hashes(product_hashes: Dict[int, List[Tuple[str, object]]], n: int, threshold: int) -> Set[int]:
    """Detect stock photos using pHash distance clustering.

    Uses multi-table LSH (not brute-force O(n^2)) so large sheets with thousands
    of unique image hashes don't stall. Hashes that land in the same LSH bucket
    are near-identical (distance <= threshold); clusters spanning >15% of
    products are treated as stock photos.
    """
    all_hashes: List[Tuple[int, int]] = []
    for idx, hashes in product_hashes.items():
        for _, h in hashes:
            all_hashes.append((idx, _hash_int(h)))
    if not all_hashes:
        return set()

    hash_to_products: Dict[int, Set[int]] = defaultdict(set)
    for idx, hval in all_hashes:
        hash_to_products[hval].add(idx)

    unique_hash_vals = list(hash_to_products.keys())
    if len(unique_hash_vals) < 2:
        return set()

    stock_threshold_count = max(2, int(n * STOCK_PHOTO_THRESHOLD))

    # Union-find over hashes that collide in any LSH table (near-identical).
    parent = {h: h for h in unique_hash_vals}
    rank = {h: 0 for h in unique_hash_vals}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x, y):
        rx, ry = _find(x), _find(y)
        if rx != ry:
            if rank[rx] < rank[ry]:
                rx, ry = ry, rx
            parent[ry] = rx
            if rank[rx] == rank[ry]:
                rank[rx] += 1

    N_TABLES = 4
    table_buckets = [defaultdict(list) for _ in range(N_TABLES)]
    for hval in unique_hash_vals:
        keys = _multi_table_keys(hval, N_TABLES)
        for t, k in enumerate(keys):
            table_buckets[t][k].append(hval)

    for t in range(N_TABLES):
        for bucket in table_buckets[t].values():
            # Oversized buckets are likely common/generic images — union them.
            if len(bucket) > 200:
                base = bucket[0]
                for h in bucket[1:]:
                    _union(base, h)
                continue
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    if _hamming_distance(bucket[i], bucket[j]) <= threshold:
                        _union(bucket[i], bucket[j])

    # Cluster -> set of products
    cluster_products: Dict[int, Set[int]] = defaultdict(set)
    for hval in unique_hash_vals:
        cluster_products[_find(hval)].update(hash_to_products[hval])

    # A cluster is "stock" if it spans more than the threshold share of products.
    stock_hashes: Set[int] = set()
    for root, members in cluster_products.items():
        if len(members) > stock_threshold_count:
            # Gather every hash in this cluster (all are near-identical stock)
            for hval in unique_hash_vals:
                if _find(hval) == root:
                    stock_hashes.add(hval)

    return stock_hashes


def _find_sku_groups(products: List[dict]) -> List[dict]:
    """Group products that share the exact same SKU.

    Image-based duplicate detection misses listings that share a SKU but have
    different images. A repeated SKU is always a duplicate (same product code),
    so flag it regardless of images.
    """
    by_sku: Dict[str, List[int]] = defaultdict(list)
    for i, p in enumerate(products):
        sku = (p.get("sku") or "").strip()
        if sku:
            by_sku[sku].append(i)
    groups = []
    for sku, members in by_sku.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda idx: -(len(products[idx].get("title", "") or "")))
        keep_idx = members_sorted[0]
        keep_p = products[keep_idx]
        remove_items = []
        for ri in members_sorted[1:]:
            p = products[ri]
            remove_items.append({
                "sku": p.get("sku", ""),
                "title": p.get("title", ""),
                "reason": f"Duplicate SKU '{sku}' — same SKU appears {len(members)} times",
            })
        groups.append({
            "keep": {"sku": keep_p.get("sku", ""), "title": keep_p.get("title", ""), "reason": f"Kept: first occurrence of SKU '{sku}'"},
            "remove": remove_items,
            "similarity": 1.0,
            "match_type": "same_sku",
            "matched_images": 0,
            "total_images": 0,
        })
    return groups


def find_sheet_duplicates(products: List[dict], threshold: int = 6) -> dict:
    if len(products) < 2:
        return {"groups": [], "total_duplicates": 0, "remove_skus": []}
    t0 = time.time()
    n = len(products)
    print(f"[DUP] {n} products to check", file=sys.stderr)
    sys.stderr.flush()

    # Phase 1: Download ALL images and compute pHash
    print(f"[DUP] Phase 1: Downloading all images ({n} products)...", file=sys.stderr)
    sys.stderr.flush()
    all_urls = _collect_urls(products)
    print(f"[DUP]   {len(all_urls)} unique images to prefetch", file=sys.stderr)
    sys.stderr.flush()
    _prefetch_images(all_urls, workers=24)
    print(f"[DUP]   Images prefetched in {time.time()-t0:.1f}s", file=sys.stderr)
    sys.stderr.flush()

    # Parallel product hashing — image files are on disk so workers just read + phash.
    product_hashes: Dict[int, List[Tuple[str, object]]] = {}
    _hash_lock = __import__("threading").Lock()

    def _hash_product(i_p):
        i, p = i_p
        try:
            return i, _compute_product_hashes(p)
        except Exception:
            return i, []

    with ThreadPoolExecutor(max_workers=16) as pool:
        for i, hashes in pool.map(_hash_product, enumerate(products)):
            if hashes:
                with _hash_lock:
                    product_hashes[i] = hashes
    loaded = len(product_hashes)
    print(f"[DUP] Phase 1 done: {loaded}/{n} hashed ({time.time()-t0:.1f}s)", file=sys.stderr)
    sys.stderr.flush()
    if loaded < 2:
        return {"groups": [], "total_duplicates": 0, "remove_skus": []}

    # Phase 1b: Identify stock photos using pHash distance clustering
    stock_hashes = _detect_stock_hashes(product_hashes, loaded, threshold)
    print(f"[DUP] Phase 1b: {len(stock_hashes)} stock hashes detected", file=sys.stderr)
    sys.stderr.flush()

    # Filter product_hashes to exclude stock hashes
    filtered_product_hashes: Dict[int, List[Tuple[str, object]]] = {}
    for idx, hashes in product_hashes.items():
        filtered = [(url, h) for url, h in hashes if _hash_int(h) not in stock_hashes]
        if filtered:
            filtered_product_hashes[idx] = filtered

    n_kept = len(filtered_product_hashes)
    n_dropped = loaded - n_kept
    print(f"[DUP]   {n_kept}/{loaded} products have unique images ({n_dropped} fully stock-filtered)", file=sys.stderr)
    sys.stderr.flush()

    # If too many products were stock-filtered, something is wrong — fall back
    if n_kept < 2:
        print(f"[DUP]   Too few products with unique images, falling back to no stock filter", file=sys.stderr)
        sys.stderr.flush()
        filtered_product_hashes = product_hashes
        n_kept = loaded

    # Phase 2: Candidate generation on UNIQUE images only
    print(f"[DUP] Phase 2: Candidate generation on unique images...", file=sys.stderr)
    t1 = time.time()
    sys.stderr.flush()
    prod_hash_ints: Dict[int, List[int]] = {}
    hash_to_products: Dict[int, Set[int]] = defaultdict(set)
    for idx, hashes in filtered_product_hashes.items():
        ints = [_hash_int(h) for _, h in hashes]
        prod_hash_ints[idx] = ints
        for hval in ints:
            hash_to_products[hval].add(idx)

    candidate_pairs: Set[Tuple[int, int]] = set()

    # Exact-hash candidates
    for hval, members in hash_to_products.items():
        if len(members) < 2:
            continue
        if len(members) > MAX_PRODUCTS_IN_BUCKET:
            continue  # skip huge buckets (likely missed stock photos)
        members = sorted(members)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                candidate_pairs.add((members[i], members[j]))
    print(f"[DUP]   Exact-hash candidates: {len(candidate_pairs)} pairs", file=sys.stderr)
    sys.stderr.flush()

    # Multi-table LSH on unique images (near-identical, distance <= threshold)
    N_TABLES = 4
    table_buckets = [defaultdict(dict) for _ in range(N_TABLES)]
    for idx, ints in prod_hash_ints.items():
        for hval in ints:
            keys = _multi_table_keys(hval, N_TABLES)
            for t, k in enumerate(keys):
                table_buckets[t][k].setdefault(idx, set()).add(hval)

    for t in range(N_TABLES):
        for k, prod_map in table_buckets[t].items():
            if len(prod_map) > MAX_PRODUCTS_IN_BUCKET:
                continue  # skip oversized buckets
            items = list(prod_map.items())
            for i in range(len(items)):
                idx_a, hs_a = items[i]
                for j in range(i + 1, len(items)):
                    idx_b, hs_b = items[j]
                    found = False
                    for ha in hs_a:
                        for hb in hs_b:
                            if _hamming_distance(ha, hb) <= threshold:
                                found = True
                                break
                        if found:
                            break
                    if found:
                        candidate_pairs.add((min(idx_a, idx_b), max(idx_a, idx_b)))

    # Cap candidates
    if len(candidate_pairs) > MAX_CANDIDATE_PAIRS:
        print(f"[DUP]   WARNING: {len(candidate_pairs)} candidates exceeds cap {MAX_CANDIDATE_PAIRS}, capping", file=sys.stderr)
        sys.stderr.flush()
        candidate_pairs = set(list(candidate_pairs)[:MAX_CANDIDATE_PAIRS])

    print(f"[DUP] Phase 2 done: {len(candidate_pairs)} total candidates ({time.time()-t1:.1f}s)", file=sys.stderr)
    sys.stderr.flush()

    # Union-find
    parent = list(range(n))
    rank = [0] * n
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            if rank[rx] < rank[ry]:
                rx, ry = ry, rx
            parent[ry] = rx
            if rank[rx] == rank[ry]:
                rank[rx] += 1

    # Phase 3: Parallelized comparison on unique images
    overlap_info: Dict[Tuple[int, int], dict] = {}
    pair_list = list(candidate_pairs)
    print(f"[DUP] Phase 3: Full comparison ({len(pair_list)} pairs)...", file=sys.stderr)
    sys.stderr.flush()

    def _compare_pair(pair):
        a, b = pair
        hashes_a = filtered_product_hashes.get(a, [])
        hashes_b = filtered_product_hashes.get(b, [])
        if not hashes_a or not hashes_b:
            return None
        matched_ab, _ = compute_image_overlap(hashes_a, hashes_b, threshold)
        matched_ba, _ = compute_image_overlap(hashes_b, hashes_a, threshold)
        min_total = min(len(hashes_a), len(hashes_b))
        min_match = min(matched_ab, matched_ba)
        if min_total > 0 and min_match / min_total >= 0.5:
            return (a, b, max(matched_ab, matched_ba), len(hashes_a), len(hashes_b))
        return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_compare_pair, p): p for p in pair_list}
        done = 0
        total = len(futures)
        for f in as_completed(futures):
            done += 1
            if done % 500 == 0 or done == total:
                print(f"[DUP]   Compared {done}/{total} pairs", file=sys.stderr)
                sys.stderr.flush()
            result = f.result()
            if result:
                a, b, matched, ta, tb = result
                union(a, b)
                overlap_info[(min(a, b), max(a, b))] = {
                    "matched": matched,
                    "total_a": ta,
                    "total_b": tb,
                }

    out_groups = _build_groups(products, find, n, overlap_info)

    # Merge in exact-SKU duplicates (listings sharing a SKU but different images).
    # Avoid double-counting SKUs already caught by the image-based groups.
    removed_skus = {r["sku"] for g in out_groups for r in g["remove"]}
    for g in _find_sku_groups(products):
        if all(r["sku"] in removed_skus for r in g["remove"]):
            continue  # already covered by image groups
        out_groups.append(g)
        for r in g["remove"]:
            removed_skus.add(r["sku"])

    total_dupes = sum(len(g["remove"]) for g in out_groups)
    all_remove_skus = [r["sku"] for g in out_groups for r in g["remove"]]
    print(f"[DUP] Done: {total_dupes} duplicates in {len(out_groups)} groups ({time.time()-t0:.1f}s total)", file=sys.stderr)
    sys.stderr.flush()
    return {"groups": out_groups, "total_duplicates": total_dupes, "remove_skus": all_remove_skus}


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    threshold = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    with open(input_path) as f:
        products = json.load(f)
    result = find_sheet_duplicates(products, threshold=threshold)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
