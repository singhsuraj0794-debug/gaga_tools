#!/usr/bin/env python3
"""Find duplicate products within an uploaded sheet using image similarity.

Optimized for large datasets (28K+ products):
- URL-based dedup first (no download needed for identical URLs)
- Only downloads first image per unique-URL product for pre-filtering
- Bucket-based grouping (O(n)) instead of O(n²)
- Only downloads remaining images for candidate pairs

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
_IMAGE_CACHE: Dict[str, Optional[Image.Image]] = {}


def clean_url(url: str) -> str:
    if not url:
        return ""
    m = PROXIED_RE.match(url)
    return m.group(1) if m else url


def _fetch_one(url: str, timeout: int = 8) -> Optional[Image.Image]:
    if not url:
        return None
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]

    candidates = [url]
    cleaned = clean_url(url)
    if cleaned != url:
        candidates.append(cleaned)

    for u in candidates:
        try:
            resp = requests.get(u, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/png,image/jpeg,*/*",
            })
            if resp.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    f.write(resp.content)
                    tmp = f.name
                try:
                    img = Image.open(tmp).convert("RGB")
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                _IMAGE_CACHE[url] = img
                return img
        except Exception:
            pass

    _IMAGE_CACHE[url] = None
    return None


def compute_phash(img: Image.Image):
    import imagehash
    return imagehash.phash(img)


def _compute_first_image_hash(product: dict) -> Optional[object]:
    images = product.get("images", [])
    if not images:
        return None
    img = _fetch_one(images[0])
    if img is None:
        return None
    try:
        return compute_phash(img)
    except Exception:
        return None


def _compute_all_image_hashes(product: dict) -> List[Tuple[str, object]]:
    hashes = []
    for url in product.get("images", [])[:10]:
        img = _fetch_one(url)
        if img is not None:
            try:
                h = compute_phash(img)
                hashes.append((url, h))
            except Exception:
                pass
    return hashes


def compute_image_overlap(
    hashes_a: List[Tuple[str, object]],
    hashes_b: List[Tuple[str, object]],
    threshold: int = 4,
) -> Tuple[int, int]:
    matched = 0
    used_b: Set[int] = set()
    for _, hash_a in hashes_a:
        best_idx = None
        best_dist = 999
        for j, (_, hash_b) in enumerate(hashes_b):
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


def _bucket_key(h, prefix_bits: int = 16) -> int:
    h_str = str(h)
    val = int(h_str, 16)
    shift = max(0, 64 - prefix_bits)
    return val >> shift


def find_sheet_duplicates(products: List[dict], threshold: int = 4) -> dict:
    if len(products) < 2:
        return {"groups": [], "total_duplicates": 0, "remove_skus": []}

    t0 = time.time()
    n = len(products)
    print(f"[DUP] {n} products to check", file=sys.stderr)

    # ── Phase 0: URL-based dedup (instant, no downloads) ────────────────────
    # Products with identical first image URL are duplicates — no download needed.
    url_groups: Dict[str, List[int]] = defaultdict(list)
    for i, p in enumerate(products):
        imgs = p.get("images", [])
        first_url = clean_url(imgs[0]) if imgs else ""
        url_groups[first_url].append(i)

    # Find URL-duplicate groups
    url_dup_groups: List[Tuple[int, List[int]]] = []
    unique_url_indices: Set[int] = set()
    for url, indices in url_groups.items():
        if len(indices) >= 2 and url:
            # All products with same URL are duplicates
            url_dup_groups.append((indices[0], indices[1:]))
        for idx in indices:
            unique_url_indices.add(idx)

    total_url_dupes = sum(len(remove) for _, remove in url_dup_groups)
    print(f"[DUP] Phase 0: {total_url_dupes} URL-exact duplicates in {len(url_dup_groups)} groups (0 downloads)", file=sys.stderr)

    # ── Phase 1: Download first image for UNIQUE-URL products only ──────────
    # Only download for products whose first image URL is unique
    # (products already matched by URL don't need image download)
    products_needing_hash = [idx for idx in unique_url_indices
                             if not any(idx in remove_list for _, remove_list in url_dup_groups)]

    # Also include one representative per unique URL for pHash comparison
    url_representatives: Dict[str, int] = {}
    for i, p in enumerate(products):
        imgs = p.get("images", [])
        first_url = clean_url(imgs[0]) if imgs else ""
        if first_url and first_url not in url_representatives:
            url_representatives[first_url] = i

    hash_candidates = list(set(list(url_representatives.values()) + products_needing_hash))
    n_to_hash = len(hash_candidates)
    print(f"[DUP] Phase 1: Downloading first image for {n_to_hash} unique products (40 workers)...", file=sys.stderr)

    first_hashes: Dict[int, Optional[object]] = {}
    with ThreadPoolExecutor(max_workers=40) as pool:
        future_map = {}
        for idx in hash_candidates:
            future_map[pool.submit(_compute_first_image_hash, products[idx])] = idx
        done = 0
        for f in as_completed(future_map):
            idx = future_map[f]
            first_hashes[idx] = f.result()
            done += 1
            if done % 500 == 0 or done == n_to_hash:
                print(f"[DUP]   Hashed {done}/{n_to_hash} first images", file=sys.stderr)

    loaded = sum(1 for v in first_hashes.values() if v is not None)
    print(f"[DUP] Phase 1 done: {loaded}/{n_to_hash} hashed ({time.time()-t0:.1f}s)", file=sys.stderr)

    if loaded < 2 and not url_dup_groups:
        return {"groups": [], "total_duplicates": 0, "remove_skus": []}

    # ── Phase 2: Bucket-based pre-filtering ─────────────────────────────────
    BUCKET_BITS = 12
    BUCKET_NEIGHBORS = 1

    buckets: Dict[int, List[int]] = defaultdict(list)
    for idx, h in first_hashes.items():
        if h is not None:
            bk = _bucket_key(h, BUCKET_BITS)
            buckets[bk].append(idx)

    print(f"[DUP] Phase 2: {len(buckets)} buckets...", file=sys.stderr)
    t1 = time.time()

    candidate_pairs: Set[Tuple[int, int]] = set()
    sorted_buckets = sorted(buckets.keys())
    bucket_set = set(sorted_buckets)

    for bk in sorted_buckets:
        members = buckets[bk]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                idx_a, idx_b = members[i], members[j]
                h_a, h_b = first_hashes[idx_a], first_hashes[idx_b]
                if h_a is not None and h_b is not None and (h_a - h_b) <= threshold:
                    candidate_pairs.add((min(idx_a, idx_b), max(idx_a, idx_b)))
        for offset in range(1, BUCKET_NEIGHBORS + 1):
            for nbk in [bk - offset, bk + offset]:
                if nbk not in bucket_set:
                    continue
                for idx_a in members:
                    for idx_b in buckets[nbk]:
                        h_a, h_b = first_hashes[idx_a], first_hashes[idx_b]
                        if h_a is not None and h_b is not None and (h_a - h_b) <= threshold:
                            candidate_pairs.add((min(idx_a, idx_b), max(idx_a, idx_b)))

    print(f"[DUP] Phase 2 done: {len(candidate_pairs)} candidate pairs ({time.time()-t1:.1f}s)", file=sys.stderr)

    # ── Phase 3: Download ALL images for candidates + full comparison ────────
    involved: Set[int] = set()
    for a, b in candidate_pairs:
        involved.add(a)
        involved.add(b)

    if involved:
        print(f"[DUP] Phase 3: Downloading all images for {len(involved)} candidates...", file=sys.stderr)
        t2 = time.time()
        product_hashes: Dict[int, List[Tuple[str, object]]] = {}
        with ThreadPoolExecutor(max_workers=20) as pool:
            future_map = {}
            for idx in involved:
                future_map[pool.submit(_compute_all_image_hashes, products[idx])] = idx
            done = 0
            total = len(future_map)
            for f in as_completed(future_map):
                idx = future_map[f]
                hashes = f.result()
                if hashes:
                    product_hashes[idx] = hashes
                done += 1
                if done % 100 == 0 or done == total:
                    print(f"[DUP]   Downloaded all images for {done}/{total}", file=sys.stderr)
        print(f"[DUP] Phase 3 downloads done ({time.time()-t2:.1f}s)", file=sys.stderr)
    else:
        product_hashes = {}

    # ── Phase 3b: Pairwise comparison ───────────────────────────────────────
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

    overlap_info: Dict[Tuple[int, int], dict] = {}

    for idx_a, idx_b in candidate_pairs:
        hashes_a = product_hashes.get(idx_a, [])
        hashes_b = product_hashes.get(idx_b, [])
        if not hashes_a or not hashes_b:
            continue
        matched_ab, _ = compute_image_overlap(hashes_a, hashes_b, threshold)
        matched_ba, _ = compute_image_overlap(hashes_b, hashes_a, threshold)
        if matched_ab == len(hashes_a) and len(hashes_a) > 0 and matched_ba == len(hashes_b) and len(hashes_b) > 0:
            union(idx_a, idx_b)
            overlap_info[(min(idx_a, idx_b), max(idx_a, idx_b))] = {
                "matched": matched_ab,
                "total_a": len(hashes_a),
                "total_b": len(hashes_b),
            }

    # Merge URL-based duplicate groups into union-find
    for keep_idx, remove_list in url_dup_groups:
        for ri in remove_list:
            union(keep_idx, ri)

    # ── Phase 4: Collect groups ──────────────────────────────────────────────
    groups_map: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        root = find(i)
        groups_map[root].append(i)

    out_groups = []
    all_remove_skus = []

    for root, members in groups_map.items():
        if len(members) < 2:
            continue

        def sort_key(idx):
            p = products[idx]
            hashes = product_hashes.get(idx, [])
            # URL-exact dupes have 0 hashes but are still duplicates
            return (-len(hashes), -(len(p.get("title", "") or "")), p.get("sku", ""))

        members_sorted = sorted(members, key=sort_key)
        keep_idx = members_sorted[0]
        remove_idxs = members_sorted[1:]

        keep_product = products[keep_idx]
        keep_hashes = product_hashes.get(keep_idx, [])

        best_overlap = {"matched": 0, "total_a": 0, "total_b": 0}
        for ri in remove_idxs:
            key = (min(keep_idx, ri), max(keep_idx, ri))
            info = overlap_info.get(key, {})
            if info.get("matched", 0) > best_overlap["matched"]:
                best_overlap = info

        matched_count = best_overlap.get("matched", 0)
        total_images = max(len(keep_hashes), 1)
        similarity = matched_count / total_images if total_images > 0 else 0.0

        # Determine match type
        if matched_count == total_images and all(
            len(product_hashes.get(ri, [])) == total_images for ri in remove_idxs
        ):
            match_type = "all_images_match"
        elif matched_count > 0 and similarity >= 0.8:
            match_type = "most_images_match"
        elif matched_count > 0:
            match_type = "partial_image_match"
        else:
            # URL-exact duplicate (no image hashes needed)
            match_type = "all_images_match"
            matched_count = len(keep_hashes) if keep_hashes else 1
            total_images = matched_count

        keep_reason = f"Kept: {len(keep_hashes)} images"

        remove_items = []
        for ri in remove_idxs:
            p = products[ri]
            remove_hash_count = len(product_hashes.get(ri, []))
            reason = f"Duplicate of {keep_product.get('sku', '?')}: {matched_count}/{max(remove_hash_count, total_images)} images match ({match_type})"
            remove_items.append({
                "sku": p.get("sku", ""),
                "title": p.get("title", ""),
                "reason": reason,
            })
            all_remove_skus.append(p.get("sku", ""))

        out_groups.append({
            "keep": {
                "sku": keep_product.get("sku", ""),
                "title": keep_product.get("title", ""),
                "reason": keep_reason,
            },
            "remove": remove_items,
            "similarity": round(similarity, 3),
            "match_type": match_type,
            "matched_images": matched_count,
            "total_images": total_images,
        })

    out_groups.sort(key=lambda g: len(g["remove"]), reverse=True)
    total_dupes = sum(len(g["remove"]) for g in out_groups)

    print(f"[DUP] Done: {total_dupes} duplicates in {len(out_groups)} groups ({time.time()-t0:.1f}s total)", file=sys.stderr)

    return {
        "groups": out_groups,
        "total_duplicates": total_dupes,
        "remove_skus": all_remove_skus,
    }


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    threshold = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    with open(input_path) as f:
        products = json.load(f)

    result = find_sheet_duplicates(products, threshold=threshold)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
