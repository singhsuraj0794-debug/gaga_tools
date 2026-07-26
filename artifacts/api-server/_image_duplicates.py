#!/usr/bin/env python3
"""Find duplicate products by image similarity using DINOv2 only.

Usage: python3 _image_duplicates.py <input.json> [threshold]
Input JSON: [{"id":"...","image_url":"...","name":"...","price":"...","brand":"...","category":"..."}, ...]
Threshold: optional similarity threshold (default 0.80)
Output to stdout: JSON with groups and to_delete_ids
"""
import json, sys, io, os, warnings, re
import requests
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import logging
logging.disable(logging.WARNING)

PROXIED_RE = re.compile(r'^https://resize\.gajab\.com/V[^/]+/(https?://.*)')


def clean_url(url):
    if not url:
        return ""
    m = PROXIED_RE.match(url)
    return m.group(1) if m else url

_dinov2_processor = None
_dinov2_model = None
_IMAGE_CACHE = {}


def fetch_image(url):
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
            resp = requests.get(u, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
            })
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                _IMAGE_CACHE[url] = img
                return img
        except Exception:
            pass

    _IMAGE_CACHE[url] = None
    return None


def load_model():
    global _dinov2_processor, _dinov2_model
    if _dinov2_model is None:
        _dinov2_processor = AutoImageProcessor.from_pretrained("facebook/dinov2-small")
        _dinov2_model = AutoModel.from_pretrained("facebook/dinov2-small")
        _dinov2_model.eval()


def compute_embeddings(products):
    """Compute DINOv2 embeddings for all products. Returns list of (product, embedding) tuples."""
    results = []
    batch_images = []
    batch_products = []

    for p in products:
        img = fetch_image(p.get("image_url", ""))
        if img is None:
            continue
        batch_images.append(img)
        batch_products.append(p)

        if len(batch_images) >= 32:
            embeddings = _encode_batch(batch_images)
            for prod, emb in zip(batch_products, embeddings):
                results.append((prod, emb))
            batch_images = []
            batch_products = []

    if batch_images:
        embeddings = _encode_batch(batch_images)
        for prod, emb in zip(batch_products, embeddings):
            results.append((prod, emb))

    return results


def _encode_batch(images):
    inputs = _dinov2_processor(images=images, return_tensors="pt", padding=True)
    with torch.no_grad():
        outputs = _dinov2_model(**inputs)
        embeddings = outputs.last_hidden_state.mean(dim=1)
    return [embeddings[i] for i in range(len(images))]


def find_groups(embedded_products, threshold):
    """Group products by image similarity using Union-Find."""
    n = len(embedded_products)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    embeddings = torch.stack([e for _, e in embedded_products])

    batch_size = 512
    for i in range(0, n, batch_size):
        batch_i = embeddings[i:min(i + batch_size, n)]
        for j in range(i, n, batch_size):
            batch_j = embeddings[j:min(j + batch_size, n)]
            sims = torch.nn.functional.cosine_similarity(
                batch_i.unsqueeze(1), batch_j.unsqueeze(0), dim=2
            )
            for bi in range(sims.shape[0]):
                for bj in range(sims.shape[1]):
                    gi = i + bi
                    gj = j + bj
                    if gi >= gj:
                        continue
                    if sims[bi, bj].item() >= threshold:
                        union(gi, gj)

    groups_map = {}
    for idx in range(n):
        root = find(idx)
        if root not in groups_map:
            groups_map[root] = []
        groups_map[root].append(embedded_products[idx][0])

    return [group for group in groups_map.values() if len(group) > 1]


def main():
    load_model()
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.80

    with open(input_path) as f:
        products = json.load(f)

    embedded = compute_embeddings(products)
    if not embedded:
        json.dump({"groups": [], "total_groups": 0, "total_duplicate_products": 0, "to_delete_ids": []}, sys.stdout)
        sys.stdout.flush()
        return

    groups = find_groups(embedded, threshold)

    out_groups = []
    to_delete_ids = []
    for group in groups:
        group_sorted = sorted(group, key=lambda p: p.get("id", ""))
        first = group_sorted[0]
        duplicates = group_sorted[1:]
        to_delete_ids.extend(p["id"] for p in duplicates)

        out_groups.append({
            "name": first.get("name", ""),
            "price": first.get("price", ""),
            "brand": first.get("brand", ""),
            "count": len(group),
            "products": [
                {
                    "id": p["id"],
                    "url": p.get("url", ""),
                    "image_url": p.get("image_url", ""),
                    "category": p.get("category", ""),
                    "mrp_price": p.get("mrp_price", ""),
                }
                for p in group_sorted
            ],
        })

    out_groups.sort(key=lambda g: g["count"], reverse=True)
    total_dupes = sum(g["count"] - 1 for g in out_groups)

    result = {
        "total_groups": len(out_groups),
        "total_duplicate_products": total_dupes,
        "to_delete_ids": to_delete_ids,
        "groups": out_groups,
    }
    json.dump(result, sys.stdout)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
