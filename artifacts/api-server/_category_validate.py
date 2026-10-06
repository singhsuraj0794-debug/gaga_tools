#!/usr/bin/env python3
"""
Category Validation — checks a product's assigned L1..L4 against the L1-L4
taxonomy using the Marqo ecommerce embeddings model (image -> category).

Pipeline per product:
  1. Resolve product images (from the sheet, else fetched from the Gajab
     gateway product-detail endpoint via the product URL).
  2. Encode the images ONCE with Marqo.
  3. Classify hierarchically: L1 (10) -> L2 (children of picked L1) ->
     L3 -> L4, recording a confidence at each level.
  4. Compare the assigned path with the predicted path.

Input JSON:  { "products": [{ "sku", "title", "url", "l1","l2","l3","l4", "images"? }] }
Output JSON: { "algorithmVersion", "taxonomyVersion", "results": [ ... ] }

Usage:
    python3 _category_validate.py input.json
    echo '{...}' | python3 _category_validate.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

# The persistent analysis server forces torch off MPS (MLX owns the Metal
# device). When this runs as a standalone one-shot CLI there is no such guard,
# and on some Macs the MPS backend aborts with an MPSNDArray buffer assertion.
# Force CPU here too so both entrypoints behave identically.
try:
    import torch as _torch
    _torch.backends.mps.is_available = lambda: False
    _torch.backends.mps.is_built = lambda: False
except Exception:
    pass

ALGORITHM_VERSION = "1.2"  # 1.1: title+image fusion; 1.2: + bare-L4 leaf text term
# Score = w_img·image + w_path·text(full path) + w_leaf·text(bare L4 leaf),
# weights summing to 1. Title totals TITLE_WEIGHT (env CATEGORY_TITLE_WEIGHT),
# of which LEAF_WEIGHT (env CATEGORY_LEAF_WEIGHT) goes to the bare-leaf term
# and the rest to the full-path term. The leaf term exists because full-path
# labels dilute short titles: "All-In-One Sewing Kit" scores 0.0000 against
# its own path "... > Tailoring & Embroidery > Sewing Kit" but 0.674 against
# "... > Home Appliances > Sewing Machines" (noise words hijack the
# embedding), while the bare leaf "Sewing Kit" scores 1.000 vs 0.000.
# The image keeps the remainder as a guard — a title-only blend once matched
# "PET Fridge Bottle Set" to "Stretch Film" at 84%.
TITLE_WEIGHT = min(max(float(os.environ.get("CATEGORY_TITLE_WEIGHT", "0.6")), 0.0), 1.0)
LEAF_WEIGHT = min(max(float(os.environ.get("CATEGORY_LEAF_WEIGHT", "0.4")), 0.0), TITLE_WEIGHT)
TAXONOMY_PATH = os.environ.get(
    "CATEGORY_TAXONOMY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_l1l4.json")
)

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

_tax = None
_index = None

# In-process result cache (see validate_product): key -> result dict.
_RESULT_CACHE: dict = {}
_RESULT_LOCK = threading.Lock()


def _norm(v) -> str:
    if v is None:
        return ""
    return " ".join(str(v).split()).strip()


def _key(*parts: str) -> str:
    return " || ".join(_norm(p).lower() for p in parts)


# Generic / ambiguous leaf names that act as catch-alls and score highly for
# almost any product, hijacking the classifier (e.g. "Precious Jewellery >
# gemstone" won ~99% for every earring, necklace and anklet). They remain
# VALID DB paths for matching an assignment, but MUST NOT be classification
# candidates or shortlist options.
_CATCH_ALL_LEAVES = {
    "gemstone", "gemstones", "coins", "coin", "precious article",
    "general", "others", "other", "miscellaneous", "misc",
    "gift", "gifts", "combo", "combos",
}


def _is_catch_all(path: dict) -> bool:
    return _norm(path.get("l4", "")).lower() in _CATCH_ALL_LEAVES


def _load_taxonomy() -> dict:
    global _tax, _index
    if _tax is not None:
        return _tax
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        _tax = json.load(f)

    # Build normalized lookup maps keyed by lower-cased names.
    l1 = {_norm(x).lower(): _norm(x) for x in _tax.get("l1", [])}
    l2 = {_norm(k).lower(): {_norm(x).lower(): _norm(x) for x in v} for k, v in _tax.get("l2", {}).items()}
    l3_m: dict[str, dict[str, str]] = {}
    l4_m: dict[str, dict[str, str]] = {}
    # Normalize the composite keys too.
    for k, v in _tax.get("l3", {}).items():
        parts = [p.strip().lower() for p in k.split("||")]
        l3_m[" || ".join(parts)] = {_norm(x).lower(): _norm(x) for x in v}
    for k, v in _tax.get("l4", {}).items():
        parts = [p.strip().lower() for p in k.split("||")]
        l4_m[" || ".join(parts)] = {_norm(x).lower(): _norm(x) for x in v}

    valid = set()
    for p in _tax.get("paths", []):
        valid.add((_norm(p["l1"]).lower(), _norm(p["l2"]).lower(), _norm(p["l3"]).lower(), _norm(p["l4"]).lower()))

    _index = {"l1": l1, "l2": l2, "l3": l3_m, "l4": l4_m, "valid": valid, "paths": _tax.get("paths", [])}
    return _tax


def _fetch_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=GATEWAY_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def product_images(url: str, max_images: int = 5):
    """Fetch image URLs for a Gajab product URL via the gateway."""
    if not url or "product-detail/" not in url:
        return []
    tail = url.split("product-detail/", 1)[1].split("?", 1)[0].strip("/")
    parts = tail.split("/")
    if len(parts) < 2:
        return []
    slug, item_id = parts[0], parts[1]
    try:
        payload = _fetch_json(GATEWAY_TMPL.format(slug=slug, item_id=item_id))
    except Exception as e:
        print(f"[CATVAL] gateway fetch failed for {item_id}: {e}", file=sys.stderr)
        return []

    data = payload.get("data") or {}
    imgs = data.get("productImage") or data.get("productOriginalImage") or []
    out = []
    for im in imgs:
        if not isinstance(im, dict):
            continue
        raw = im.get("image") or ""
        if not raw:
            continue
        if raw.startswith("http://") or raw.startswith("https://"):
            out.append(raw)
        else:
            container = im.get("containerName") or ""
            out.append(f"{IMAGE_BASE}{container}{raw}")
        if len(out) >= max_images:
            break
    return out


def _class_value(tax, level, parent_key, picked_lower):
    """Return the canonical label for a picked lower-cased value."""
    if level == "l1":
        return tax["l1"].get(picked_lower)
    m = tax[level].get(parent_key, {})
    return m.get(picked_lower)


def _fused_top(feature, title_feature, labels, top_k=25, leaf_scores=None, leaf_of=None):
    """Top-k labels by weighted image + title scores (weights sum to 1).

    - image term:        (1 − TITLE_WEIGHT) × image score over `labels`
    - full-path term:    (TITLE_WEIGHT − LEAF_WEIGHT) × title-vs-`labels`
    - bare-leaf term:    LEAF_WEIGHT × title-vs-bare-L4-name   [only when
                         leaf_scores + leaf_of are supplied]

    All three are softmaxes over label sets that share the same strings, so
    they are on one scale. The bare-leaf term recovers signal that the long
    path string destroys (a short title is drowned by path noise words —
    see ALGORITHM_VERSION comment). Without leaf args (L3/L4 sibling
    re-scores, whose labels ARE bare names) it falls back to the plain
    two-way image+title split.
    """
    from _marqo_classifier import classify_features

    if not labels:
        return []
    if title_feature is None:
        return classify_features(feature, labels, top_k=top_k)
    img = dict(classify_features(feature, labels, top_k=len(labels))) if feature is not None else {}
    txt = dict(classify_features(title_feature, labels, top_k=len(labels)))
    use_leaf = bool(leaf_scores) and leaf_of is not None and LEAF_WEIGHT > 0
    w_img = 1.0 - TITLE_WEIGHT
    w_path = TITLE_WEIGHT - LEAF_WEIGHT if use_leaf else TITLE_WEIGHT
    w_leaf = LEAF_WEIGHT if use_leaf else 0.0
    fused = []
    for l in labels:
        s = w_img * img.get(l, 0.0) + w_path * txt.get(l, 0.0)
        if use_leaf:
            s += w_leaf * leaf_scores.get(leaf_of.get(l, ""), 0.0)
        fused.append((l, s))
    fused.sort(key=lambda x: -x[1])
    return fused[:top_k]


def validate_product(product: dict) -> dict:
    from _marqo_classifier import encode_images, classify_features

    _load_taxonomy()
    tax = _index

    sku = product.get("sku", "")
    title = product.get("title", "")
    url = product.get("url", "")
    assigned = [
        _norm(product.get("l1", "")),
        _norm(product.get("l2", "")),
        _norm(product.get("l3", "")),
        _norm(product.get("l4", "")),
    ]
    assigned_lower = tuple(x.lower() for x in assigned)
    assigned_in_db = assigned_lower in tax["valid"]

    images = product.get("images") or []
    if isinstance(images, str):
        images = [images]
    images = [u for u in images if u]

    # In-process result cache: a re-run of the same sheet (or a second job
    # with identical rows) returns instantly. Key includes the assignment and
    # versions so a taxonomy/algorithm change invalidates it. Only completed
    # verdicts are cached — network errors are transient and must retry.
    cache_key = (
        url, title, tuple(assigned), tuple(images),
        _tax.get("version", ""), ALGORITHM_VERSION,
    )
    with _RESULT_LOCK:
        hit = _RESULT_CACHE.get(cache_key)
    if hit is not None:
        import copy as _copy
        return _copy.deepcopy(hit)

    if not images:
        images = product_images(url)

    result = {
        "sku": sku,
        "title": title,
        "url": url,
        "assigned": {"l1": assigned[0], "l2": assigned[1], "l3": assigned[2], "l4": assigned[3],
                     "full": " > ".join([x for x in assigned if x])},
        "assignedInDb": assigned_in_db,
        "imageCount": len(images),
        "predicted": None,
        "predictedFull": "",
        # Primary outputs: verdict + the recommended full DB path when wrong.
        "recommended": "",
        "levelConfidence": {},
        "confidence": 0.0,
        "status": "no-images",
        "differingLevel": None,
        "qwenVerified": False,
    }

    if not images:
        return result

    feature = encode_images(images)
    if feature is None:
        result["status"] = "no-images"
        return result

    # Title embedding (the PRIMARY signal in the fusion below). One encode per
    # product; fails soft — if the title can't be encoded we fall back to
    # image-only scoring inside _fused_top.
    title_feature = None
    try:
        from _marqo_classifier import encode_text_query
        title_feature = encode_text_query(title) if title else None
    except Exception as exc:
        print(f"[CATVAL] title encode failed, image-only: {exc}", file=sys.stderr)

    # ── Hierarchy (path-label) classification ──────────────────────────────
    # Single-word leaf labels ("Flute", "Cooler Bag") give the image model too
    # little context and misclassify badly. Instead we score against FULL PATH
    # strings ("Home & Kitchen > Kitchen Accessories > Bottle, Flasks &
    # Thermoware > Bottles"), which is how the proven HSN pipeline uses Marqo.
    #
    #  1. Rank all valid full paths by image similarity; the top full path
    #     decides L1 and L2 (its confidence is the softmax over all paths).
    #  2. Re-score L3 only among that L2's children using bare L3 labels.
    #  3. Re-score L4 only among that L3's children using bare L4 labels.
    #  Each step is restricted to the siblings of the chosen node, so a
    #  misread leaf cannot drag the whole path off-course.
    l1_tax = tax["l1"]
    l2_tax = tax["l2"]
    l3_tax = tax["l3"]
    l4_tax = tax["l4"]

    # Classification candidates EXCLUDE catch-all leaves ("gemstone", "Coins",
    # "Combo", …) which otherwise dominate the image score for any product in
    # their branch. They stay valid DB paths for assignment matching, but are
    # never predicted or recommended.
    paths = [p for p in tax.get("paths", []) if not _is_catch_all(p)]
    path_labels = [p["full"] for p in paths]
    # Rank paths and aggregate probability by L1 and by L1>L2 prefix. A single
    # best path is unstable because hundreds of near-identical sibling paths
    # split the softmax mass (an L1 like "Home & Kitchen" can be correct yet
    # score 0.13 on the winning leaf). Summing over the top-K mass per prefix
    # recovers a stable top-level decision.
    #
    # Bare-L4 leaf text scores (one matmul over ~2,400 unique leaf names,
    # embeddings cached like the path labels) — the title-vs-leaf term that
    # survives path-noise dilution. See _fused_top.
    leaf_scores = None
    leaf_of = None
    if title_feature is not None and LEAF_WEIGHT > 0:
        leaf_of = {p["full"]: p["l4"] for p in paths}
        leaf_names = sorted(leaf_of.values())
        try:
            leaf_scores = dict(classify_features(title_feature, leaf_names, top_k=len(leaf_names)))
        except Exception as exc:
            print(f"[CATVAL] leaf text scoring failed, path-only: {exc}", file=sys.stderr)
    top = _fused_top(feature, title_feature, path_labels, top_k=25,
                     leaf_scores=leaf_scores, leaf_of=leaf_of)
    if not top:
        result["status"] = "unclassified"
        return result

    top_global = list(top)  # keep Marqo's global ranking for the shortlist tail
    by_full = {p["full"]: p for p in paths}
    # Mass dicts are keyed by LOWERCASED names so lookups are consistent with
    # the keyword anchor and the normalized taxonomy maps (which are lowercase).
    l1_mass: dict[str, float] = {}
    l2_mass: dict[str, float] = {}
    l3_mass: dict[str, float] = {}
    for full, score in top:
        p = by_full.get(full)
        if not p:
            continue
        l1_mass[p["l1"].lower()] = l1_mass.get(p["l1"].lower(), 0.0) + score
        k2 = _key(p["l1"], p["l2"])
        l2_mass[k2] = l2_mass.get(k2, 0.0) + score
        k3 = _key(p["l1"], p["l2"], p["l3"])
        l3_mass[k3] = l3_mass.get(k3, 0.0) + score

    # ── Title is now a first-class signal (fusion) ───────────────────────
    # Historically the title only reached the classifier through the curated
    # keyword map (74 entries); everything else was image-shape only. That
    # misclassified functionally-near products — "Radnal Garbage Dustbin 12L
    # Cream" was confidently (0.99) called "Diaper Disposal Bins" because a
    # cream lidded bin has the same SHAPE as a diaper pail, while the title
    # text scores "... > Dustbins" at 0.99.
    #
    # All scoring now goes through _fused_top: TITLE_WEIGHT·text + (1-w)·image
    # over the same label set. The image stays as the minority vote because a
    # title-only blend once produced garbage ("PET Fridge Bottle Set" →
    # "Stretch Film" at 84%) — short titles match long path strings on shared
    # words, not on product identity.
    #
    # The curated keyword anchor still overrides ABOVE the fusion: high-
    # precision product nouns ("sipper bottle", "casserole", "laundry") map to
    # an exact verified path and beat both signals at L1-L3 (and at L4 when the
    # entry names one).

    kw = None
    try:
        from _category_keywords import match_target, split_target

        m = match_target(title)
        if m:
            kw = split_target(m[1])
    except Exception as exc:
        print(f"[CATVAL] keyword map unavailable: {exc}", file=sys.stderr)

    best_l1 = max(l1_mass, key=l1_mass.get)
    if kw and kw[0]:
        kv_l1 = kw[0].lower()
        if kv_l1 in l1_tax:
            best_l1 = kv_l1

    # Restrict L2 to the chosen L1's children (keys are "l1 || l2").
    l2_candidates = {k: v for k, v in l2_mass.items() if k.split(" || ")[0] == best_l1.lower()}
    best_l2 = l2_tax.get(best_l1.lower(), {})
    if kw and kw[1]:
        kv_l2 = kw[1].lower()
        if kv_l2 in best_l2:
            l2_name = best_l2[kv_l2]
            best_l2_key = f"{best_l1.lower()} || {kv_l2}"
        else:
            if not l2_candidates:
                result["status"] = "unclassified"
                return result
            best_l2_key = max(l2_candidates, key=l2_candidates.get)
            l2_name = next((v for k, v in best_l2.items() if f"{best_l1.lower()} || {k}" == best_l2_key), "")
    else:
        if not l2_candidates:
            result["status"] = "unclassified"
            return result
        best_l2_key = max(l2_candidates, key=l2_candidates.get)
        l2_name = next((v for k, v in best_l2.items() if f"{best_l1.lower()} || {k}" == best_l2_key), "")

    picks = {"l1": l1_tax.get(best_l1, best_l1), "l2": l2_name}
    confs = {
        "l1": l1_mass.get(best_l1.lower(), 0.0),
        "l2": l2_candidates.get(best_l2_key, 0.0),
    }

    # L3: prefer the keyword anchor; otherwise pick among the L2's siblings.
    # Use the GLOBAL top-25 prefix mass first. A fresh softmax over 2-3 sibling
    # labels is unstable: for jewellery it gave "Precious Jewellery" 0.95 vs
    # "Artificial & Silver Jewellery" 0.05 even though the global ranking had
    # "…> Earrings" at #1 (0.29) — re-normalizing over a tiny set throws away
    # that evidence. Fall back to the fresh re-score only when top-25 has no
    # coverage of this L2's children.
    parent_l2 = _key(picks["l1"], picks["l2"])
    l3_children = l3_tax.get(parent_l2, {})
    if kw and kw[2] and kw[2].lower() in l3_children:
        picks["l3"] = l3_children[kw[2].lower()]
        confs["l3"] = 1.0
    else:
        l3_cov = {k: v for k, v in l3_mass.items()
                  if k.startswith(parent_l2 + " || ") and k in
                  {_key(picks["l1"], picks["l2"], n) for n in l3_children.values()}}
        if l3_cov:
            best_l3_key = max(l3_cov, key=l3_cov.get)
            picks["l3"] = l3_children[best_l3_key.split(" || ")[-1]]
            confs["l3"] = l3_cov[best_l3_key] / max(sum(l3_cov.values()), 1e-9)
        else:
            l3_vals = list(l3_children.values())
            top = _fused_top(feature, title_feature, l3_vals, top_k=1)
            if top:
                picks["l3"] = top[0][0]
                confs["l3"] = top[0][1]
            else:
                picks["l3"] = ""
                confs["l3"] = 0.0
    result["keywordAnchor"] = list(kw) if kw else None

    # L4 among siblings of the chosen L3. Fused title+image, unless the curated
    # keyword anchor named a specific L4 (then honor it — the anchor is the
    # authoritative product noun).
    parent_l3 = _key(picks["l1"], picks["l2"], picks["l3"])
    # Drop catch-all leaves here too: the sibling map still contains them
    # (e.g. "Precious Jewellery > gemstone/Coins") and they win any 2-way
    # softmax against a concrete product noun.
    l4_vals = [v for k, v in l4_tax.get(parent_l3, {}).items()
               if k not in _CATCH_ALL_LEAVES]

    # If the chosen L3 has no concrete leaf (all children are catch-alls),
    # back off to the best sibling L3 that does, so we never emit a path
    # ending at L3 or a banned catch-all.
    if not l4_vals and l3_children:
        alts = []
        for lk, lname in l3_children.items():
            k = _key(picks["l1"], picks["l2"], lname)
            vals = [v for kk, v in l4_tax.get(k, {}).items() if kk not in _CATCH_ALL_LEAVES]
            if vals:
                alts.append((lk, lname, vals, l3_mass.get(k, 0.0)))
        if alts:
            alts.sort(key=lambda x: x[3], reverse=True)
            tot = sum(a[3] for a in alts) or 1.0
            _, picks["l3"], l4_vals, confs["l3"] = alts[0]
            confs["l3"] = alts[0][3] / tot

    if kw and len(kw) > 3 and kw[3] and kw[3].lower() in {v.lower(): v for v in l4_vals}:
        picks["l4"] = {v.lower(): v for v in l4_vals}[kw[3].lower()]
        confs["l4"] = 1.0
    else:
        top = _fused_top(feature, title_feature, l4_vals, top_k=1)
        if top:
            picks["l4"] = top[0][0]
            confs["l4"] = top[0][1]
        else:
            picks["l4"] = ""
            confs["l4"] = 0.0

    predicted = [picks.get("l1", ""), picks.get("l2", ""), picks.get("l3", ""), picks.get("l4", "")]
    predicted_lower = tuple(x.lower() for x in predicted)

    result["predicted"] = {"l1": predicted[0], "l2": predicted[1], "l3": predicted[2], "l4": predicted[3]}
    result["predictedFull"] = " > ".join([x for x in predicted if x])
    result["levelConfidence"] = {k: round(v, 4) for k, v in confs.items()}
    result["confidence"] = round(confs.get("l4", 0.0), 4)

    # Constrained candidate set for the Qwen escalation. Marqo's global top-25
    # is noisy (a bottle can score "Glass"), so build a FOCUSED shortlist:
    #   - Marqo's best path and its L3-siblings,
    #   - the curated keyword-anchor path (if any),
    #   - the assigned path (if valid) and its L3-siblings.
    # Qwen then picks the best FUNCTIONAL match from this relevant set.
    def _siblings_of(full_path):
        parts = [p.strip() for p in full_path.split(">")]
        if len(parts) < 3:
            return []
        k = _key(parts[0], parts[1], parts[2])
        return [f"{parts[0]} > {parts[1]} > {parts[2]} > {v}" for v in l4_tax.get(k, {}).values()]

    shortlist = []
    seen_sl = set()

    def _add(path):
        if path and path not in seen_sl:
            seen_sl.add(path)
            shortlist.append(path)

    _add(result["predictedFull"])
    for s in _siblings_of(result["predictedFull"]):
        _add(s)
    if kw:
        anchor_full = " > ".join([x for x in kw if x])
        _add(anchor_full)
        for s in _siblings_of(anchor_full):
            _add(s)
    if assigned_in_db:
        _add(" > ".join(assigned))
        for s in _siblings_of(" > ".join(assigned)):
            _add(s)
    # Top up with Marqo's global picks so Qwen has alternatives.
    for full, _ in top_global:
        _add(full)
    result["shortlist"] = shortlist[:30]

    # ── Verdict ────────────────────────────────────────────────────────────
    # Primary goal: is the assigned L1-L4 correct, and if not, what is the
    # correct DB path?
    #   correct    — assigned path is valid AND matches the model (L1-L4).
    #   incorrect  — assigned path is valid but the model disagrees at L1-L3
    #                (a real category error). `recommended` = predicted path.
    #   review     — low confidence, or the assigned leaf is invalid / the
    #                model is unsure. `recommended` = best valid DB path.
    # `recommended` is ALWAYS a path that exists in the taxonomy.
    #
    # NOTE: an invalid assigned leaf (e.g. "... > Bottles" is not an L4 in the
    # DB) must not be reported as a confident "incorrect" with a nonsense
    # sibling suggestion — it goes to `review` so a human confirms.
    CONF_MIN = 0.50  # L1/L2 confidence floor for an auto verdict

    if assigned_lower == predicted_lower:
        result["status"] = "correct"
    else:
        first_diff = None
        for i, (a, p) in enumerate(zip(assigned_lower, predicted_lower)):
            if a != p:
                first_diff = ("L1", "L2", "L3", "L4")[i]
                break
        result["differingLevel"] = first_diff

        low_conf = confs.get("l1", 0.0) < CONF_MIN or confs.get("l2", 0.0) < CONF_MIN

        if not assigned_in_db and first_diff == "L4":
            # Top levels agree; only the (invalid) leaf differs.
            result["status"] = "review"
            result["recommended"] = result["predictedFull"]
        elif not assigned_in_db and first_diff in ("L1", "L2", "L3"):
            # Assigned path is invalid further up — can't judge it as a clean
            # "wrong": route to review with the best valid path.
            result["status"] = "review"
            result["recommended"] = result["predictedFull"]
        elif low_conf:
            result["status"] = "review"
            result["recommended"] = result["predictedFull"]
        else:
            result["status"] = "incorrect"
            result["recommended"] = result["predictedFull"]

    with _RESULT_LOCK:
        # Bounded so a long-lived server can't grow it without limit.
        if len(_RESULT_CACHE) >= 5000:
            _RESULT_CACHE.clear()
        _RESULT_CACHE[cache_key] = result
    import copy as _copy
    return _copy.deepcopy(result)


def process_products(products, workers=None, on_progress=None):
    """Validate a batch, optionally in parallel.

    workers: thread count (env CATEGORY_WORKERS, default 4, max 8). torch CPU
    ops release the GIL and image downloads are I/O, so threads scale without
    multiplying model memory (a process pool would load Marqo per worker).
    on_progress(done, results_so_far): called as products finish so background
    jobs can stream progress to disk without serialising the work.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _load_taxonomy()  # populate globals BEFORE the pool (not thread-safe init)
    n = len(products)
    if workers is None:
        workers = int(os.environ.get("CATEGORY_WORKERS", "4"))
    workers = max(1, min(int(workers), 8))
    if n <= 1:
        workers = 1

    def _one(i, product):
        try:
            return i, validate_product(product)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            return i, {
                "sku": product.get("sku", ""),
                "title": product.get("title", ""),
                "status": "error",
                "error": str(e),
            }

    results = [None] * n
    done = 0
    t0 = time.time()
    if workers == 1:
        for i, product in enumerate(products):
            i, r = _one(i, product)
            results[i] = r
            done += 1
            print(f"[CATVAL] {done}/{n} in {time.time() - t0:.1f}s", file=sys.stderr)
            if on_progress:
                on_progress(done, [r for r in results if r is not None])
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_one, i, p) for i, p in enumerate(products)]
            for fut in as_completed(futs):
                i, r = fut.result()
                results[i] = r
                done += 1
                if done % 5 == 0 or done == n:
                    print(f"[CATVAL] {done}/{n} in {time.time() - t0:.1f}s", file=sys.stderr)
                if on_progress:
                    on_progress(done, [r for r in results if r is not None])
    return {
        "algorithmVersion": ALGORITHM_VERSION,
        "taxonomyVersion": _tax.get("version", ""),
        "results": results,
    }


def main() -> None:
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        raw = open(input_path, "r", encoding="utf-8").read() if input_path else sys.stdin.read()
        payload = json.loads(raw or "{}")
        products = payload.get("products", [])
        out = process_products(products)
        sys.stdout.write(json.dumps(out))
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    main()
