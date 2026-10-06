#!/usr/bin/env python3
"""
Amazon <-> Gajab cross-reference.

For every row in an uploaded Amazon sheet, decide whether the same product
already exists in our catalogue (All_products.xlsx), and report the matched
Gajab SKU / product / category / seller plus the Amazon seller name and a
seller store link.

Matching is two-stage:
  1. Fast text pass over normalized titles (blocking + token/bigram similarity).
  2. Image pass (pHash + DINOv2 cosine) for uncertain rows, fused with the text
     score. Gajab images are pulled on demand from the storefront gateway and
     cached on disk.

Standalone: run against files in ~/Downloads. No API server, no Supabase.

Usage:
    python3 scripts/amazon_crossref.py \
        --amazon "~/Downloads/IN_AMAZON_blackBoxProducts_Backpacks.xlsx" \
        --gajab  "~/Downloads/All_products.xlsx" \
        --out    "~/Downloads/amazon_crossref.xlsx"

    python3 scripts/amazon_crossref.py --amazon sheet.xlsx --limit 20 --no-images
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
from PIL import Image
import imagehash

# --------------------------------------------------------------------------- #
# Config / constants
# --------------------------------------------------------------------------- #

GATEWAY_TMPL = (
    "https://gatewayservice.gajab.com/product/api/product-store/"
    "product-detail/{slug}/{item_id}?pincode="
)
IMAGE_BASE = "https://resize.gajab.com/"
GATEWAY_HEADERS = {
    "Content-type": "application/json",
    "Origin": "https://gajab.com",
    "Referer": "https://gajab.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".crossref_cache")
GAJAB_IMG_CACHE_FILE = os.path.join(CACHE_DIR, "gajab_images.json")

# Fusion weights (text + image, both on a 0-100 scale).
TEXT_WEIGHT = 0.6
IMAGE_WEIGHT = 0.4

# Candidate retrieval.
MAX_CANDIDATES = 600          # max blocked candidates scored per query
TOP_K = 12                    # candidates kept for the image pass

# Decision thresholds (text/image on 0-100).
EXISTS_THRESHOLD = 85.0       # fused confidence needed to mark "Exists"
REVIEW_THRESHOLD = 55.0       # below this -> "Not Found"
SKIP_IMAGE_TEXT_SCORE = 15.0  # don't bother with images when text is hopeless
HIGH_TEXT_SCORE = 90.0        # text-only high confidence, skip images

# Image similarity: pHash + semantic vision models (DINOv2 / CLIP / SigLIP).
#   * pHash anchors exact/near-identical files (robust, checksum-like).
#   * DINOv2 = structural/self-supervised features (same crop/object).
#   * CLIP   = semantic image embedding.
#   * SigLIP = semantic image embedding (usually the sharpest of the three).
# image_score = PHASH_WEIGHT*phash_sim + SEMANTIC_WEIGHT*(0.5*mean + 0.5*min).
# The `min` term makes a "visual duplicate" require ALL models to agree, which
# keeps precision high: different-but-same-category products score high on
# mean but are dragged down by the weakest model.
IMAGE_MODELS = ["dino", "clip", "siglip"]   # subset override: --image-models dino,siglip
MODEL_IDS = {
    "dino": "facebook/dinov2-small",
    "clip": "openai/clip-vit-base-patch32",
    "siglip": "google/siglip-base-patch16-224",
}
PHASH_WEIGHT = 0.30           # pHash share of image_score
SEMANTIC_WEIGHT = 0.70        # semantic-models' share of image_score
IMAGE_STRONG_COS = 0.90       # min semantic cosine across models -> visual duplicate
IMAGE_STRONG_PHASH = 12       # pHash hamming distance -> near identical
STRONG_VISUAL_FUSED = 92.0    # forced confidence when the strong-visual gate fires

# Secondary "visual match" tier: the SAME product re-photographed (different
# file, so pHash is high) but every semantic model still strongly agrees. Kept
# stricter on title evidence than the duplicate tier. All tunable via CLI.
VISUAL_MATCH_ENABLED = True
VISUAL_MATCH_COS = 0.85       # min semantic cosine across models
VISUAL_MATCH_PHASH = 35       # pHash hamming distance ceiling (different photo)
VISUAL_MATCH_TEXT = 45        # minimum title score to corroborate
VISUAL_MATCH_FUSED = 86.0     # forced confidence when it fires (just over Exists)

STOP_WORDS = {
    "set", "of", "for", "with", "and", "the", "in", "to", "a", "an", "&",
    "pack", "pcs", "pc", "piece", "pieces", "new", "box", "combo", "kit",
    "type", "style", "color", "colour", "colours", "colors", "size", "sizes",
    "each", "per", "multi", "free", "assorted", "multicolor", "multicolour",
    "ml", "ltr", "litre", "liter", "litres", "liters", "cm", "mm", "inch",
    "inches", "kg", "gms", "gm", "gram", "grams", "g", "l", "nos", "no",
    "pack of", "packof", "combo of",
}


# --------------------------------------------------------------------------- #
# Title normalization + similarity
# --------------------------------------------------------------------------- #

def _singular(w: str) -> str:
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and not w.endswith("ses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def normalize_title(text) -> str:
    """Lowercase, strip punctuation/units/stopwords, singularize, dedupe tokens."""
    if text is None:
        return ""
    t = str(text).lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    out = []
    seen = set()
    for w in t.split():
        if len(w) < 2 or w in STOP_WORDS:
            continue
        w = _singular(w)
        if w in STOP_WORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return " ".join(out)


def text_score(q: str, c: str) -> float:
    """Similarity of two normalized titles, 0-100. Exact equality -> 100."""
    if not q or not c:
        return 0.0
    if q == c:
        return 100.0
    qs, cs = q.split(), c.split()
    qset, cset = set(qs), set(cs)
    union = qset | cset
    jaccard = len(qset & cset) / len(union) if union else 0.0

    shorter, longer_set = (qs, cset) if len(qs) <= len(cs) else (cs, qset)
    partial = sum(1 for w in shorter if w in longer_set) / len(shorter) if shorter else 0.0

    qb = set(zip(qs, qs[1:]))
    cb = set(zip(cs, cs[1:]))
    allb = qb | cb
    bigram = len(qb & cb) / len(allb) if allb else 0.0

    seq = SequenceMatcher(None, q, c).ratio()

    score = 100.0 * (0.30 * jaccard + 0.25 * partial + 0.20 * bigram + 0.25 * seq)
    if qs and cs and qs[0] == cs[0]:
        score += 5.0
    return min(score, 100.0)


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #

def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _norm_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return " ".join(str(v).split()).strip()


def _first_url(value) -> str:
    """Pull the first http(s) URL out of a cell (handles 'All Images' blobs)."""
    s = _norm_cell(value)
    if not s:
        return ""
    m = re.search(r"https?://[^\s,;|\"']+", s)
    return m.group(0) if m else ""


def pick_column(cols, patterns, required=False):
    """Case-insensitive exact-then-substring column match."""
    low = {str(c).strip().lower(): c for c in cols}
    for p in patterns:
        for lc, orig in low.items():
            if p == lc:
                return orig
    for p in patterns:
        for lc, orig in low.items():
            if p in lc:
                return orig
    if required:
        raise ValueError(f"No column matching {patterns} among {list(cols)}")
    return None


def pick_image_column(cols):
    """Return a list of likely image columns, in priority order."""
    low = {str(c).strip().lower(): c for c in cols}
    ordered = []
    for p in ["image url", "main image", "image link", "image 1", "image_1",
              "image1", "image 2", "image 3", "image", "images", "all images"]:
        for lc, orig in low.items():
            if p == lc and orig not in ordered:
                ordered.append(orig)
    # Any remaining "image N" columns.
    for lc, orig in low.items():
        if "image" in lc and orig not in ordered:
            ordered.append(orig)
    return ordered


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #

def load_gajab(path: str):
    df = pd.read_excel(path)
    cols = list(df.columns)
    c_title = pick_column(cols, ["product name", "product title", "title", "name"], required=True)
    c_sku = pick_column(cols, ["sku number", "sku", "product id", "productid"])
    c_url = pick_column(cols, ["product url", "url", "link"])
    c_seller = pick_column(cols, ["seller name", "seller", "business name"])
    c_levels = [pick_column(cols, [lvl]) for lvl in ["l1", "l2", "l3", "l4"]]

    out = []
    for _, r in df.iterrows():
        title = _norm_cell(r.get(c_title))
        if not title:
            continue
        cat = [_norm_cell(r.get(c)) for c in c_levels if c]
        cat = [c for c in cat if c]
        out.append({
            "title": title,
            "norm": normalize_title(title),
            "sku": _norm_cell(r.get(c_sku)) if c_sku else "",
            "url": _norm_cell(r.get(c_url)) if c_url else "",
            "seller": _norm_cell(r.get(c_seller)) if c_seller else "",
            "category": " > ".join(cat),
        })
    # All_products repeats the same product name across variant SKUs (~10k
    # duplicate rows). Collapse to one representative per normalized title so
    # candidate slots aren't wasted on the same product.
    seen = set()
    deduped = []
    for row in out:
        if row["norm"] in seen:
            continue
        seen.add(row["norm"])
        row["idx"] = len(deduped)
        deduped.append(row)
    return deduped


def build_index(gajab):
    inv = {}
    exact = {}
    for row in gajab:
        exact.setdefault(row["norm"], row["idx"])
        for tok in set(row["norm"].split()):
            inv.setdefault(tok, []).append(row["idx"])
    return inv, exact


def score_sheet(path):
    """Heuristic: pick the sheet whose header holds a title-like column."""
    try:
        xl = pd.ExcelFile(path)
    except Exception as e:
        raise SystemExit(f"Could not open {path}: {e}")
    best, best_score = None, -1
    for sh in xl.sheet_names:
        try:
            cols = list(xl.parse(sh, nrows=0).columns)
        except Exception:
            continue
        low = {str(c).strip().lower() for c in cols}
        score = 0
        for key in ["title", "product name", "product title"]:
            if key in low:
                score += 3
        for key in ["seller", "asin", "image url", "image 1", "product url", "url", "price"]:
            if key in low:
                score += 1
        if "category name *" in low and "attribute columns" in low:
            score -= 5
        if score > best_score:
            best, best_score = sh, score
    return xl, best


def load_amazon(path: str):
    xl, sh = score_sheet(path)
    df = xl.parse(sh)
    cols = list(df.columns)
    c_title = pick_column(cols, ["title", "product name", "product title", "name"])
    c_seller = pick_column(cols, ["seller name", "seller", "merchant", "vendor"])
    c_url = pick_column(cols, ["product url", "url", "link", "product link"])
    c_asin = pick_column(cols, ["asin"])
    c_brand = pick_column(cols, ["brand"])
    c_cat = pick_column(cols, ["subcategory", "category"])
    c_price = pick_column(cols, ["price"])
    img_cols = pick_image_column(cols)

    rows = []
    for i, r in df.iterrows():
        title = _norm_cell(r.get(c_title)) if c_title else ""
        img = ""
        for ic in img_cols:
            img = _first_url(r.get(ic))
            if img:
                break
        if not title and not img:
            continue
        rows.append({
            "rid": len(rows),
            "title": title,
            "seller": _norm_cell(r.get(c_seller)) if c_seller else "",
            "url": _norm_cell(r.get(c_url)) if c_url else "",
            "asin": _norm_cell(r.get(c_asin)) if c_asin else "",
            "brand": _norm_cell(r.get(c_brand)) if c_brand else "",
            "category": _norm_cell(r.get(c_cat)) if c_cat else "",
            "price": _norm_cell(r.get(c_price)) if c_price else "",
            "image": img,
            "norm": normalize_title(title),
        })
    return sh, rows


# --------------------------------------------------------------------------- #
# Gajab images (gateway) + disk cache
# --------------------------------------------------------------------------- #

_img_url_cache = load_json(GAJAB_IMG_CACHE_FILE, {})
_img_url_lock = threading.Lock()


def gajab_image_url(product_url: str) -> str:
    if not product_url or "product-detail/" not in product_url:
        return ""
    tail = product_url.split("product-detail/", 1)[1].split("?", 1)[0].strip("/")
    parts = tail.split("/")
    if len(parts) < 2:
        return ""
    slug, item_id = parts[0], parts[1]
    with _img_url_lock:
        if item_id in _img_url_cache:
            return _img_url_cache[item_id]
    url = ""
    try:
        req = Request(GATEWAY_TMPL.format(slug=slug, item_id=item_id), headers=GATEWAY_HEADERS)
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        imgs = (data.get("data") or {}).get("productImage") \
            or (data.get("data") or {}).get("productOriginalImage") or []
        for im in imgs:
            if not isinstance(im, dict):
                continue
            raw = im.get("image") or ""
            if not raw:
                continue
            url = raw if raw.startswith("http") else f"{IMAGE_BASE}{im.get('containerName') or ''}{raw}"
            break
    except Exception:
        url = ""
    with _img_url_lock:
        _img_url_cache[item_id] = url
    return url


def flush_img_cache():
    with _img_url_lock:
        save_json(GAJAB_IMG_CACHE_FILE, _img_url_cache)


# --------------------------------------------------------------------------- #
# Image download + similarity (pHash + DINOv2 + CLIP + SigLIP)
# --------------------------------------------------------------------------- #

_img_cache = {}
_img_lock = threading.Lock()

# Lazy-loaded vision models: name -> {"processor", "model"}.
_models = {}
_models_lock = threading.Lock()
_models_ready = False

# Per-URL embedding cache: url -> {model_name: np.ndarray (L2-normalized)}.
_emb_cache = {}
_emb_cache_lock = threading.Lock()


def fetch_image(url: str):
    if not url:
        return None
    with _img_lock:
        if url in _img_cache:
            return _img_cache[url]
    img = None
    try:
        req = Request(url, headers=BROWSER_HEADERS)
        with urlopen(req, timeout=20) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        img = None
    with _img_lock:
        _img_cache[url] = img
    return img


def _load_model(name):
    from transformers import AutoImageProcessor, AutoModel
    mid = MODEL_IDS[name]
    proc = AutoImageProcessor.from_pretrained(mid)
    model = AutoModel.from_pretrained(mid)
    model.eval()
    return {"processor": proc, "model": model}


def load_models():
    """Load every configured vision model once. Failed models are dropped."""
    global _models_ready
    with _models_lock:
        if _models_ready:
            return
        _models_ready = True
        try:
            import torch  # noqa: F401
        except Exception as e:
            print(f"[crossref] torch unavailable ({e}); using pHash only", file=sys.stderr)
            IMAGE_MODELS[:] = []
            return
        for name in list(IMAGE_MODELS):
            try:
                _models[name] = _load_model(name)
            except Exception as e:
                print(f"[crossref] {name} unavailable ({e}); skipping", file=sys.stderr)
                IMAGE_MODELS.remove(name)


def image_embeddings(img, key=None):
    """Return {model_name: normalized np.ndarray} for one image, cached by URL key."""
    if key is not None:
        with _emb_cache_lock:
            if key in _emb_cache:
                return _emb_cache[key]
    out = {}
    load_models()
    if _models:
        try:
            import numpy as np
            import torch
            for name in list(IMAGE_MODELS):
                m = _models.get(name)
                if not m:
                    continue
                inputs = m["processor"](images=img, return_tensors="pt")
                with torch.no_grad():
                    if name == "dino":
                        vec = m["model"](**inputs).last_hidden_state.mean(dim=1)[0]
                    else:
                        vec = m["model"].get_image_features(**inputs)[0]
                    vec = torch.nn.functional.normalize(vec, dim=0)
                out[name] = vec.cpu().numpy().astype("float32")
        except Exception:
            pass
    if key is not None:
        with _emb_cache_lock:
            _emb_cache[key] = out
    return out


def image_similarity(emb_a, emb_b, img_a, img_b):
    """Return (score 0-100, detail dict).

    emb_a/emb_b: {model_name: normalized vector} from image_embeddings.
    pHash handles exact/near-identical files; the semantic models add robustness
    to re-crops/re-shoots. The `min` across models gates visual duplicates.
    """
    if img_a is None or img_b is None:
        return 0.0, {"reason": "missing-image"}
    a = img_a.resize((160, 160))
    b = img_b.resize((160, 160))
    d = int(imagehash.phash(a) - imagehash.phash(b))
    ph_sim = max(0.0, 1.0 - d / 64.0)

    cosines = {}
    for name in IMAGE_MODELS:
        if name in emb_a and name in emb_b:
            c = float(np.dot(emb_a[name], emb_b[name]))
            cosines[name] = round(c, 4)

    detail = {"phash_distance": d, "cosines": cosines,
              "phash_sim": round(ph_sim, 4)}
    if not cosines:
        # No model available -> pHash only.
        return ph_sim * 100.0, detail

    vals = [max(0.0, min(1.0, c)) for c in cosines.values()]
    sem_mean = sum(vals) / len(vals)
    sem_min = min(vals)
    combined = 0.5 * sem_mean + 0.5 * sem_min
    score = 100.0 * (PHASH_WEIGHT * ph_sim + SEMANTIC_WEIGHT * combined)
    detail["semantic_mean"] = round(sem_mean, 4)
    detail["semantic_min"] = round(sem_min, 4)
    detail["combined"] = round(combined, 4)
    return score, detail


# --------------------------------------------------------------------------- #
# Seller store link
# --------------------------------------------------------------------------- #

SELLER_CACHE_FILE = os.path.join(CACHE_DIR, "seller_links.json")
_seller_link_cache = load_json(SELLER_CACHE_FILE, {})
_seller_link_lock = threading.Lock()
_SELLER_ID_PATTERNS = [
    re.compile(r"/sp\?[^\"']*?seller=([A-Z0-9]{8,})"),
    re.compile(r'"sellerId"\s*:\s*"([A-Z0-9]{8,})"'),
    re.compile(r'"merchantId"\s*:\s*"([A-Z0-9]{8,})"'),
    re.compile(r"merchantId=([A-Z0-9]{8,})"),
]


def seller_search_link(name: str) -> str:
    if not name:
        return ""
    return "https://www.amazon.in/s?k=" + quote(name)


def resolve_store_link(seller: str, product_url: str) -> str:
    """Best effort: scrape the product page for the seller id -> real store URL.
    Falls back to a seller search URL built from the name."""
    fallback = seller_search_link(seller)
    if not product_url:
        return fallback
    with _seller_link_lock:
        if seller in _seller_link_cache:
            return _seller_link_cache[seller] or fallback
    found = ""
    try:
        req = Request(product_url, headers=BROWSER_HEADERS)
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
        for pat in _SELLER_ID_PATTERNS:
            m = pat.search(html)
            if m:
                found = "https://www.amazon.in/sp?seller=" + m.group(1)
                break
    except Exception:
        found = ""
    with _seller_link_lock:
        _seller_link_cache[seller] = found
    return found or fallback


# --------------------------------------------------------------------------- #
# Core matching
# --------------------------------------------------------------------------- #

def retrieve_candidates(norm_q, inv, gajab):
    if not norm_q:
        return []
    q_tokens = set(norm_q.split())
    counts = {}
    for tok in q_tokens:
        for ridx in inv.get(tok, ()):
            counts[ridx] = counts.get(ridx, 0) + 1
    if not counts:
        return []
    min_share = 2 if len(q_tokens) >= 3 else 1
    pool = {r: c for r, c in counts.items() if c >= min_share}
    if not pool:
        pool = counts
    if len(pool) > MAX_CANDIDATES:
        top = sorted(pool.items(), key=lambda kv: kv[1], reverse=True)[:MAX_CANDIDATES]
        pool = dict(top)
    scored = []
    seen_norm = set()
    for ridx, share in pool.items():
        row = gajab[ridx]
        if row["norm"] in seen_norm:
            continue
        seen_norm.add(row["norm"])
        s = text_score(norm_q, row["norm"])
        if row["norm"] == norm_q:
            s = 100.0
        scored.append((s, share, ridx))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[:TOP_K]


def process_text(entry, inv, exact, gajab):
    norm_q = entry["norm"]
    if norm_q and norm_q in exact:
        idx = exact[norm_q]
        g = gajab[idx]
        entry.update({
            "status": "Exists", "confidence": 100.0, "match_type": "Exact Title",
            "match": g, "candidates": [(100.0, g["idx"])], "needs_image": False,
        })
        return
    cands = retrieve_candidates(norm_q, inv, gajab)
    entry["candidates"] = [(s, r) for s, _, r in cands]
    best_text = cands[0][0] if cands else 0.0
    if not norm_q or best_text < SKIP_IMAGE_TEXT_SCORE:
        entry.update({"status": "Not Found", "confidence": round(best_text, 1),
                      "match_type": "None", "match": None, "needs_image": False})
        return
    if best_text >= HIGH_TEXT_SCORE:
        g = gajab[cands[0][2]]
        entry.update({"status": "Exists", "confidence": round(best_text, 1),
                      "match_type": "Title", "match": g, "needs_image": False})
        return
    entry.update({"needs_image": True, "best_text": best_text})


def process_image(entry, gajab, amazon_img):
    amazon_emb = image_embeddings(amazon_img, entry.get("image")) if amazon_img is not None else {}
    best = None  # (fused, text, image, gajab_row, detail)
    for text_s, ridx in entry.get("candidates", []):
        g = gajab[ridx]
        img_s, detail = (0.0, {})
        if amazon_img is not None and g["url"]:
            g_url = gajab_image_url(g["url"])
            g_img = fetch_image(g_url)
            if g_img is not None:
                g_emb = image_embeddings(g_img, g_url)
                img_s, detail = image_similarity(amazon_emb, g_emb, amazon_img, g_img)
        fused = TEXT_WEIGHT * text_s + IMAGE_WEIGHT * img_s
        sem_min = detail.get("semantic_min")
        phash_d = detail.get("phash_distance", 999)
        # Tier 1 — visual duplicate: every semantic model agrees (min cosine) AND
        # pHash is near-identical AND there is at least weak title evidence.
        if (sem_min is not None and sem_min >= IMAGE_STRONG_COS
                and phash_d <= IMAGE_STRONG_PHASH and text_s >= SKIP_IMAGE_TEXT_SCORE):
            detail["strong_visual"] = True
            fused = max(fused, STRONG_VISUAL_FUSED)
        # Tier 2 — visual match: same product re-photographed (different file, so
        # pHash is high) but all models still strongly agree and the title agrees.
        elif (VISUAL_MATCH_ENABLED and sem_min is not None and sem_min >= VISUAL_MATCH_COS
                and phash_d <= VISUAL_MATCH_PHASH and text_s >= VISUAL_MATCH_TEXT):
            detail["visual_match"] = True
            fused = max(fused, VISUAL_MATCH_FUSED)
        if best is None or fused > best[0]:
            best = (fused, text_s, img_s, g, detail)

    if best is None:
        entry.update({"status": "Not Found", "confidence": 0.0,
                      "match_type": "None", "match": None})
        return
    fused, text_s, img_s, g, detail = best
    if fused >= EXISTS_THRESHOLD:
        status = "Exists"
    elif fused >= REVIEW_THRESHOLD:
        status = "Review"
    else:
        status = "Not Found"
    if detail.get("strong_visual"):
        mtype = "Image Duplicate"
    elif detail.get("visual_match"):
        mtype = "Image+Title"
    else:
        mtype = "Title+Image"
    entry.update({
        "status": status,
        "confidence": round(fused, 1),
        "text_score": round(text_s, 1),
        "image_score": round(img_s, 1),
        "image_detail": detail,
        "match_type": mtype if status != "Not Found" else "None",
        "match": g if status != "Not Found" else None,
    })


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def build_dataframe(entries, args):
    out_rows = []
    for e in entries:
        g = e.get("match") or {}
        conf = e.get("confidence", 0.0)
        review = "Yes" if (e.get("status") == "Review" or
                           (REVIEW_THRESHOLD <= conf < EXISTS_THRESHOLD and e.get("status") != "Exists")) else "No"
        out_rows.append({
            "Match Status": e.get("status", "Not Found"),
            "Review Needed": review,
            "Match Confidence": conf,
            "Match Type": e.get("match_type", "None"),
            "Amazon Title": e["title"],
            "Amazon Brand": e["brand"],
            "Amazon ASIN": e["asin"],
            "Amazon Seller Name": e["seller"],
            "Amazon Seller Store Link": e.get("store_link", ""),
            "Amazon URL": e["url"],
            "Amazon Image": e["image"],
            "Amazon Category": e["category"],
            "Matched Gajab SKU": g.get("sku", ""),
            "Matched Gajab Product Name": g.get("title", ""),
            "Matched Gajab Category (L1>L2>L3>L4)": g.get("category", ""),
            "Matched Gajab Seller": g.get("seller", ""),
            "Matched Gajab URL": g.get("url", ""),
            "Text Score": e.get("text_score", e.get("best_text", e.get("confidence", 0.0))),
            "Image Score": e.get("image_score", ""),
            "DINOv2 Cos": _detail_val(e, "cosines", "dino"),
            "CLIP Cos": _detail_val(e, "cosines", "clip"),
            "SigLIP Cos": _detail_val(e, "cosines", "siglip"),
            "Semantic Min": _detail_val(e, "semantic_min"),
            "pHash Distance": _detail_val(e, "phash_distance"),
        })
    df = pd.DataFrame(out_rows)
    df.insert(0, "#", range(1, len(df) + 1))
    return df


def _detail_val(e, key, sub=None):
    d = e.get("image_detail") or {}
    if sub is None:
        return d.get(key, "")
    return (d.get(key) or {}).get(sub, "")


def write_excel(df, candidates, entries, args, out_path):
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    summary = pd.DataFrame([{
        "Generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Amazon sheet": os.path.basename(_expand(args.amazon)),
        "Gajab source": os.path.basename(_expand(args.gajab)),
        "Amazon rows": len(df),
        "Exists": int((df["Match Status"] == "Exists").sum()),
        "Not Found": int((df["Match Status"] == "Not Found").sum()),
        "Review": int((df["Match Status"] == "Review").sum()),
        "Unique Amazon sellers": df["Amazon Seller Name"].replace("", pd.NA).nunique(),
        "Exact matches": int((df["Match Type"] == "Exact Title").sum()),
        "Title+Image matches": int((df["Match Type"] == "Title+Image").sum()),
        "Text-only matches": int((df["Match Type"] == "Title").sum()),
        "Exists threshold": EXISTS_THRESHOLD,
        "Text/Image weight": f"{TEXT_WEIGHT}/{IMAGE_WEIGHT}",
        "Images used": "no" if args.no_images else "yes",
    }]).T.rename(columns={0: "Value"})

    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Results", index=False)
        summary.to_excel(xw, sheet_name="Summary")
        if candidates is not None and not candidates.empty:
            candidates.to_excel(xw, sheet_name="Candidates", index=False)

        wb = xw.book
        ws = wb["Results"]
        headers = [c.value for c in ws[1]]
        status_col = headers.index("Match Status") + 1
        review_col = headers.index("Review Needed") + 1
        green = PatternFill("solid", fgColor="C6EFCE")
        red = PatternFill("solid", fgColor="FFC7CE")
        amber = PatternFill("solid", fgColor="FFEB9C")
        for r in range(2, ws.max_row + 1):
            cell = ws.cell(row=r, column=status_col)
            val = cell.value
            if val == "Exists":
                cell.fill = green
            elif val == "Not Found":
                cell.fill = red
            elif val == "Review":
                cell.fill = amber
            rc = ws.cell(row=r, column=review_col)
            if rc.value == "Yes":
                rc.fill = amber
        ws.freeze_panes = "A2"
        # Sensible widths for the key columns.
        widths = {"Amazon Title": 55, "Matched Gajab Product Name": 55,
                  "Matched Gajab URL": 45, "Amazon URL": 35, "Amazon Image": 35,
                  "Amazon Seller Store Link": 40, "Amazon Seller Name": 26,
                  "Matched Gajab Category (L1>L2>L3>L4)": 42, "Amazon Category": 22}
        for i, h in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 16)
        for sh in wb.sheetnames:
            wb[sh].sheet_view.showGridLines = True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def parse_args():
    ap = argparse.ArgumentParser(description="Cross-reference Amazon products against Gajab All_products.xlsx")
    ap.add_argument("--amazon", required=True, help="Uploaded Amazon sheet (.xlsx)")
    ap.add_argument("--gajab", default="~/Downloads/All_products.xlsx",
                    help="Gajab source of truth (default: ~/Downloads/All_products.xlsx)")
    ap.add_argument("--out", default="", help="Output Excel path (default: ~/Downloads/amazon_crossref_<ts>.xlsx)")
    ap.add_argument("--threshold", type=float, default=EXISTS_THRESHOLD, help="Exists confidence threshold (default 85)")
    ap.add_argument("--workers", type=int, default=8, help="Image-pass threads (default 8)")
    ap.add_argument("--limit", type=int, default=0, help="Only process first N Amazon rows (testing)")
    ap.add_argument("--no-images", action="store_true", help="Skip the image pass (text only)")
    ap.add_argument("--image-models", default=os.environ.get("CROSSREF_IMAGE_MODELS", ""),
                    help="Comma list of vision models: dino,clip,siglip (default: all)")
    ap.add_argument("--visual-cos", type=float, default=VISUAL_MATCH_COS,
                    help=f"Min cosine (all models) to treat a re-shot image as the same product (default {VISUAL_MATCH_COS})")
    ap.add_argument("--visual-text", type=float, default=VISUAL_MATCH_TEXT,
                    help=f"Min title score to corroborate a re-shot visual match (default {VISUAL_MATCH_TEXT})")
    ap.add_argument("--no-visual-match", action="store_true",
                    help="Disable the re-shot 'visual match' tier (only near-identical images count)")
    ap.add_argument("--no-store-links", action="store_true",
                    help="Skip scraping real Amazon seller store links (use a seller search link instead)")
    return ap.parse_args()


def main():
    global EXISTS_THRESHOLD, VISUAL_MATCH_COS, VISUAL_MATCH_TEXT, VISUAL_MATCH_ENABLED
    args = parse_args()
    args.store_links = not args.no_store_links
    EXISTS_THRESHOLD = args.threshold
    VISUAL_MATCH_COS = args.visual_cos
    VISUAL_MATCH_TEXT = args.visual_text
    VISUAL_MATCH_ENABLED = not args.no_visual_match
    if args.image_models:
        wanted = [m.strip().lower() for m in args.image_models.split(",") if m.strip()]
        IMAGE_MODELS[:] = [m for m in wanted if m in MODEL_IDS]
    if not args.no_images:
        print(f"[crossref] Loading vision models: {', '.join(IMAGE_MODELS) or 'none'}")
        load_models()

    t0 = time.time()
    gajab_path = _expand(args.gajab)
    amazon_path = _expand(args.amazon)
    if not os.path.exists(gajab_path):
        raise SystemExit(f"Gajab source not found: {gajab_path}")
    if not os.path.exists(amazon_path):
        raise SystemExit(f"Amazon sheet not found: {amazon_path}")

    print(f"[crossref] Loading Gajab source: {gajab_path}")
    gajab = load_gajab(gajab_path)
    print(f"[crossref]   {len(gajab)} Gajab products")
    inv, exact = build_index(gajab)

    print(f"[crossref] Loading Amazon sheet: {amazon_path}")
    sheet, entries = load_amazon(amazon_path)
    if args.limit:
        entries = entries[:args.limit]
    print(f"[crossref]   sheet '{sheet}', {len(entries)} Amazon rows")

    print("[crossref] Text pass...")
    for e in entries:
        process_text(e, inv, exact, gajab)
    n_amb = sum(1 for e in entries if e.get("needs_image"))
    print(f"[crossref]   done ({n_amb} rows need image verification)")

    if not args.no_images and n_amb:
        print("[crossref] Image pass...")
        todo = [e for e in entries if e.get("needs_image")]
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {ex.submit(_image_worker, e, gajab): e for e in todo}
            for fut in as_completed(futs):
                fut.result()
                done += 1
                if done % 25 == 0 or done == len(todo):
                    print(f"[crossref]   {done}/{len(todo)}")
        flush_img_cache()
        for e in todo:
            e.setdefault("status", "Not Found")
    else:
        for e in entries:
            if e.get("needs_image"):
                # No image pass: fall back to the best text candidate.
                cands = e.get("candidates", [])
                if cands:
                    s, ridx = cands[0]
                    status = "Exists" if s >= EXISTS_THRESHOLD else ("Review" if s >= REVIEW_THRESHOLD else "Not Found")
                    e.update({"status": status, "confidence": round(s, 1),
                              "match_type": "Title" if status != "Not Found" else "None",
                              "match": gajab[ridx] if status != "Not Found" else None})
                else:
                    e.update({"status": "Not Found", "confidence": 0.0, "match": None, "match_type": "None"})

    # Seller store links (one lookup per unique seller; cached across runs).
    print("[crossref] Seller store links...")
    sellers = {}
    for e in entries:
        if e["seller"] and e["seller"] not in sellers:
            sellers[e["seller"]] = e["url"]
    used = {}
    if args.store_links:
        with ThreadPoolExecutor(max_workers=max(1, min(4, args.workers))) as ex:
            futs = {ex.submit(resolve_store_link, s, u): s for s, u in sellers.items()}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    used[s] = fut.result()
                except Exception:
                    used[s] = seller_search_link(s)
        save_json(SELLER_CACHE_FILE, _seller_link_cache)
    else:
        for s in sellers:
            used[s] = seller_search_link(s)
    for e in entries:
        e["store_link"] = used.get(e["seller"], "")

    # Export.
    out_path = args.out or os.path.join(
        os.path.dirname(amazon_path),
        f"amazon_crossref_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
    )
    out_path = _expand(out_path)
    print(f"[crossref] Writing {out_path}")
    df = build_dataframe(entries, args)
    cand_df = build_candidates_df(entries, gajab)
    write_excel(df, cand_df, entries, args, out_path)

    exists = int((df["Match Status"] == "Exists").sum())
    review = int((df["Match Status"] == "Review").sum())
    notfound = int((df["Match Status"] == "Not Found").sum())
    print(f"[crossref] Done in {time.time() - t0:.1f}s — "
          f"Exists {exists}, Review {review}, Not Found {notfound} -> {out_path}")


def _image_worker(entry, gajab):
    amazon_img = fetch_image(entry["image"]) if entry.get("image") else None
    try:
        process_image(entry, gajab, amazon_img)
    except Exception as ex:
        entry.update({"status": "Not Found", "confidence": 0.0, "match": None,
                      "match_type": "None", "image_error": str(ex)})


def build_candidates_df(entries, gajab):
    rows = []
    for e in entries:
        for rank, item in enumerate(e.get("candidates", []), start=1):
            s, ridx = item
            g = gajab[ridx]
            rows.append({
                "Amazon Row": e["rid"] + 1,
                "Amazon Title": e["title"],
                "Rank": rank,
                "Text Score": round(s, 1),
                "Gajab SKU": g["sku"],
                "Gajab Product Name": g["title"],
                "Gajab Category": g["category"],
                "Gajab URL": g["url"],
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    main()
