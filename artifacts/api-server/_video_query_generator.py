#!/usr/bin/env python3
"""
Video Query Generator — uses DINOv2 + CLIP to analyze a product image
and generate broader, more accurate search queries for video platforms.

Usage:
    python3 _video_query_generator.py <image_url> <product_name>

Output (JSON):
    {
        "category": "toy motorcycle",
        "visual_features": ["wheels", "handlebars", "red"],
        "queries": [
            {"query": "toy motorcycle review", "weight": 0.95},
            {"query": "RC bike unboxing", "weight": 0.80},
            ...
        ]
    }
"""
import io
import json
import sys
import traceback

import requests
from PIL import Image

# ─── Lazy-loaded models ──────────────────────────────────────────────────────
_clip_model = None
_clip_processor = None
_dinov2_model = None
_dinov2_processor = None

# ─── Product categories for CLIP zero-shot classification ─────────────────────
PRODUCT_CATEGORIES = [
    "toy vehicle", "toy motorcycle", "toy car", "toy truck", "toy airplane",
    "toy helicopter", "toy boat", "toy train", "toy drone",
    "diecast model", "scale model", "miniature vehicle", "model kit",
    "remote control car", "RC vehicle", "RC helicopter", "RC drone",
    "action figure", "figurine", "collectible toy", "building blocks",
    "LEGO set", "puzzle", "board game", "educational toy",
    "musical instrument toy", "stuffed animal", "plush toy", "doll",
    "outdoor toy", "sports equipment", "fitness equipment",
    "kitchen appliance", "home appliance", "electronic device",
    "phone accessory", "headphones", "speaker", "charger",
    "clothing item", "shoes", "accessories", "jewelry",
    "furniture", "home decor", "garden tool",
    "pet toy", "pet accessory",
    "automotive part", "car accessory", "motorcycle accessory",
    "bike", "bicycle", "motorcycle", "scooter",
    "watch", "smartwatch", "sunglasses",
]

# ─── Visual feature labels for DINOv2 feature extraction ─────────────────────
VISUAL_FEATURES = [
    "colorful", "metallic", "plastic", "wooden", "fabric",
    "round", "angular", "tall", "compact", "large",
    "wheeled", "tracked", "winged", "floating",
    "bright", "dark", "matte", "glossy", "transparent",
    "realistic", "cartoon", "abstract", "minimalist",
    "detailed", "simple", "complex", "textured",
]


def _load_clip():
    global _clip_model, _clip_processor
    if _clip_model is not None:
        return
    try:
        from transformers import CLIPProcessor, CLIPModel
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        _clip_model.eval()
    except Exception:
        pass


def _load_dinov2():
    global _dinov2_model, _dinov2_processor
    if _dinov2_model is not None:
        return
    try:
        from transformers import AutoImageProcessor, AutoModel
        _dinov2_processor = AutoImageProcessor.from_pretrained("facebook/dinov2-small")
        _dinov2_model = AutoModel.from_pretrained("facebook/dinov2-small")
        _dinov2_model.eval()
    except Exception:
        pass


def _fetch_image(url: str):
    """Download image from URL, return PIL Image or None."""
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            if content_type.startswith("image/") or resp.content[:4] in [
                b"\xff\xd8\xff\xe0", b"\x89PNG", b"RIFF", b"\x00\x00\x00"
            ]:
                return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        pass
    return None


def classify_product_category(image: Image.Image, top_k: int = 5):
    """Use CLIP zero-shot classification to identify product category."""
    _load_clip()
    if _clip_model is None:
        return []

    import torch
    try:
        inputs = _clip_processor(
            text=PRODUCT_CATEGORIES,
            images=image,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        with torch.no_grad():
            outputs = _clip_model(**inputs)
            logits = outputs.logits_per_image[0]
            probs = logits.softmax(dim=-1)

        # Get top-k results
        top_probs, top_indices = probs.topk(top_k)
        results = []
        for prob, idx in zip(top_probs, top_indices):
            results.append({
                "category": PRODUCT_CATEGORIES[idx.item()],
                "confidence": round(prob.item(), 4),
            })
        return results
    except Exception:
        return []


def extract_visual_features(image: Image.Image, top_k: int = 5):
    """Use CLIP to describe visual characteristics of the product."""
    _load_clip()
    if _clip_model is None:
        return []

    import torch
    try:
        inputs = _clip_processor(
            text=VISUAL_FEATURES,
            images=image,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        with torch.no_grad():
            outputs = _clip_model(**inputs)
            logits = outputs.logits_per_image[0]
            probs = logits.softmax(dim=-1)

        top_probs, top_indices = probs.topk(top_k)
        results = []
        for prob, idx in zip(top_probs, top_indices):
            if prob.item() > 0.1:  # Only include features with >10% confidence
                results.append(VISUAL_FEATURES[idx.item()])
        return results
    except Exception:
        return []


def get_dinov2_embedding(image: Image.Image):
    """Get DINOv2 embedding vector for the product image."""
    _load_dinov2()
    if _dinov2_model is None:
        return None

    import torch
    try:
        inputs = _dinov2_processor(images=image, return_tensors="pt")
        with torch.no_grad():
            outputs = _dinov2_model(**inputs)
            embedding = outputs.last_hidden_state.mean(dim=1)
        return embedding.squeeze().tolist()
    except Exception:
        return None


def generate_search_queries(
    categories: list,
    visual_features: list,
    product_name: str,
    top_k: int = 8,
) -> list:
    """Generate search queries based on CLIP classification + visual features."""
    queries = []
    seen = set()

    # Primary: category-based queries
    for cat_info in categories[:3]:
        cat = cat_info["category"]
        conf = cat_info["confidence"]

        # Direct category search
        q = cat
        if q not in seen:
            seen.add(q)
            queries.append({"query": q, "weight": round(conf, 3), "reason": "category"})

        # Category + action words
        for action in ["review", "unboxing", "demo", "test", "in action"]:
            q = f"{cat} {action}"
            if q not in seen:
                seen.add(q)
                queries.append({"query": q, "weight": round(conf * 0.85, 3), "reason": "category+action"})

    # Secondary: combine product name tokens with category
    name_tokens = product_name.lower().strip().split()
    # Remove filler words
    fillers = {"for", "the", "a", "an", "in", "on", "of", "with", "and", "to", "is", "it", "at", "by", "new", "pack", "set", "combo"}
    meaningful = [t for t in name_tokens if t not in fillers and len(t) > 1]

    if meaningful:
        # Use product name as base query
        base_query = " ".join(meaningful[:5])
        if base_query not in seen:
            seen.add(base_query)
            queries.append({"query": base_query, "weight": 0.95, "reason": "product_name"})

        # Combine with top category
        if categories:
            top_cat = categories[0]["category"].split()[-1]  # Last word of category
            q = f"{' '.join(meaningful[:3])} {top_cat}"
            if q not in seen:
                seen.add(q)
                queries.append({"query": q, "weight": 0.88, "reason": "name+category"})

    # Tertiary: visual feature-based queries
    if visual_features:
        # Use top 2 visual features with category
        if categories:
            top_cat = categories[0]["category"]
            feats = " ".join(visual_features[:2])
            q = f"{top_cat} {feats}"
            if q not in seen:
                seen.add(q)
                queries.append({"query": q, "weight": 0.70, "reason": "visual+category"})

    # Broaden: general category terms
    broad_terms = set()
    for cat_info in categories[:2]:
        cat = cat_info["category"]
        parts = cat.split()
        for part in parts:
            if len(part) > 3 and part not in fillers:
                broad_terms.add(part)

    if broad_terms:
        q = " ".join(sorted(broad_terms)[:3])
        if q not in seen:
            seen.add(q)
            queries.append({"query": q, "weight": 0.60, "reason": "broad"})

    # Sort by weight descending, return top_k
    queries.sort(key=lambda x: x["weight"], reverse=True)
    return queries[:top_k]


def analyze_product(image_url: str, product_name: str) -> dict:
    """Main entry point: analyze product image and return search queries."""
    result = {
        "category": None,
        "visual_features": [],
        "queries": [],
        "error": None,
    }

    if not image_url:
        # No image — just clean the product name
        result["queries"] = [{"query": product_name, "weight": 1.0, "reason": "fallback"}]
        return result

    image = _fetch_image(image_url)
    if image is None:
        result["error"] = "Failed to fetch image"
        result["queries"] = [{"query": product_name, "weight": 1.0, "reason": "fallback"}]
        return result

    try:
        # Classify product category
        categories = classify_product_category(image, top_k=5)
        if categories:
            result["category"] = categories[0]["category"]
            result["categories"] = categories

        # Extract visual features
        result["visual_features"] = extract_visual_features(image, top_k=5)

        # Generate search queries
        result["queries"] = generate_search_queries(
            categories, result["visual_features"], product_name
        )
    except Exception as e:
        result["error"] = str(e)
        # Fallback to product name
        if not result["queries"]:
            result["queries"] = [{"query": product_name, "weight": 1.0, "reason": "fallback"}]

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: _video_query_generator.py <image_url_or_path> <product_name>"}))
        sys.exit(0)

    image_input = sys.argv[1]
    product_name = sys.argv[2]

    try:
        # Support both URL and local file path
        image = None
        if image_input.startswith("/") or image_input.startswith("./"):
            # Local file path
            try:
                image = Image.open(image_input).convert("RGB")
            except Exception:
                pass
        elif image_input.startswith("http"):
            # URL — try fetching
            image = _fetch_image(image_input)

        if image is None:
            # Fallback: just use product name
            result = {
                "category": None,
                "visual_features": [],
                "queries": [{"query": product_name, "weight": 1.0, "reason": "fallback"}],
                "error": "Could not load image",
            }
        else:
            # Classify product category
            categories = classify_product_category(image, top_k=5)
            visual_features = extract_visual_features(image, top_k=5)
            queries = generate_search_queries(categories, visual_features, product_name)
            result = {
                "category": categories[0]["category"] if categories else None,
                "categories": categories,
                "visual_features": visual_features,
                "queries": queries,
            }

        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()}))
