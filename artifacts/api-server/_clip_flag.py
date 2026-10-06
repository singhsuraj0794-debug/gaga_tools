#!/usr/bin/env python3
"""
Stage 1: CLIP-based flagging for color/material mismatches.

Extracts color and material words from title+description, then asks CLIP to
rank those same vocabularies against the actual product image. If the stated
term isn't in CLIP's top-2 visual guesses, it's flagged for Stage 2 (Qwen).

Usage:
    python3 _clip_flag.py <input.json>

Input:
{
  "products": [
    {
      "sku": "...",
      "title": "...",
      "description": "...",
      "images": ["https://..."]
    }
  ]
}

Output:
{
  "results": [
    {
      "sku": "...",
      "stated_colors": ["red", "blue"],
      "stated_materials": ["silicon"],
      "clip_color_scores": {"red": 0.82, "blue": 0.11, "purple": 0.05, ...},
      "clip_material_scores": {"silicon": 0.15, "plastic": 0.60, ...},
      "flagged": [
        {"type": "color", "stated": "red", "clip_top2": ["purple", "pink"]},
        {"type": "material", "stated": "silicon", "clip_top2": ["plastic", "rubber"]}
      ]
    }
  ]
}
"""
import json
import re
import sys
import time
from typing import Dict, List, Optional, Tuple
from io import BytesIO

import requests
from PIL import Image

# ─── Vocabulary ───────────────────────────────────────────────────────────────

COLORS = [
    "black", "white", "red", "blue", "green", "pink", "silver", "grey", "gray",
    "navy", "beige", "purple", "maroon", "gold", "yellow", "orange", "brown",
    "multi", "multicolor", "transparent", "clear", "turquoise", "teal", "violet",
    "indigo", "magenta", "cyan", "lavender", "peach", "mint", "coral", "cream",
    "ivory", "tan", "bronze", "copper", "burgundy", "charcoal", "khaki", "olive",
    "mustard",
]

MATERIALS = [
    "cotton", "silk", "wool", "leather", "plastic", "metal", "steel", "copper",
    "iron", "aluminium", "aluminum", "glass", "wood", "ceramic", "porcelain",
    "rubber", "silicon", "silicone", "nylon", "polyester", "acrylic", "vinyl",
    "resin", "bamboo", "paper", "cardboard", "velvet", "linen", "denim",
    "canvas", "brass", "zinc", "marble", "granite", "clay", "fabric",
    "microfiber", "melamine", "stainless steel",
]

_COLOR_RE = re.compile(
    r"\b(?:black|white|red|blue|green|pink|silver|grey|gray|navy|beige|"
    r"purple|maroon|gold|yellow|orange|brown|multi|multicolor|transparent|"
    r"clear|turquoise|teal|violet|indigo|magenta|cyan|lavender|peach|mint|"
    r"coral|cream|ivory|tan|bronze|copper|burgundy|charcoal|khaki|"
    r"olive|mustard)\b",
    re.IGNORECASE,
)

_MATERIAL_RE = re.compile(
    r"\b(?:cotton|silk|wool|leather|plastic|metal|steel|copper|iron|aluminium|"
    r"aluminum|glass|wood|ceramic|porcelain|rubber|silicon|silicone|nylon|"
    r"polyester|acrylic|vinyl|resin|bamboo|paper|cardboard|velvet|linen|"
    r"denim|canvas|brass|zinc|marble|granite|clay|fabric|microfiber|melamine|"
    r"stainless\s*steel)\b",
    re.IGNORECASE,
)


def _extract_stated_colors(text: str) -> List[str]:
    """Extract unique color words from text."""
    if not text:
        return []
    return sorted(set(c.lower() for c in _COLOR_RE.findall(text)))


def _extract_stated_materials(text: str) -> List[str]:
    """Extract unique material words from text."""
    if not text:
        return []
    return sorted(set(m.lower().replace(" ", "") for m in _MATERIAL_RE.findall(text)))


# ─── Image loading ────────────────────────────────────────────────────────────

def _load_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    """Load image from URL, return PIL Image or None."""
    try:
        req = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        req.raise_for_status()
        return Image.open(BytesIO(req.content)).convert("RGB")
    except Exception as e:
        print(f"[CLIP_FLAG] Failed to load image {url}: {e}", file=sys.stderr)
        return None


# ─── CLIP zero-shot ───────────────────────────────────────────────────────────

_clip_model = None
_clip_processor = None


def _get_clip():
    """Lazy-load openai/clip-vit-base-patch32."""
    global _clip_model, _clip_processor
    if _clip_model is not None:
        return _clip_model, _clip_processor
    import torch
    from transformers import CLIPProcessor, CLIPModel

    model_name = "openai/clip-vit-base-patch32"
    print(f"[CLIP_FLAG] Loading {model_name}...", file=sys.stderr)
    t0 = time.time()
    _clip_model = CLIPModel.from_pretrained(model_name)
    _clip_processor = CLIPProcessor.from_pretrained(model_name)
    _clip_model.eval()
    print(f"[CLIP_FLAG] Loaded in {time.time()-t0:.1f}s", file=sys.stderr)
    return _clip_model, _clip_processor


def clip_rank_vocabulary(
    image: Image.Image,
    vocabulary: List[str],
    top_k: int = 5,
) -> Dict[str, float]:
    """Rank vocabulary against image using CLIP zero-shot.
    Returns {term: score} for all terms, sorted by score descending.
    """
    if not vocabulary:
        return {}
    import torch
    import torch.nn.functional as F

    model, processor = _get_clip()
    inputs = processor(text=vocabulary, images=image, return_tensors="pt", padding=True)
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        probs = F.softmax(outputs.logits_per_image, dim=-1).squeeze(0)

    scores = {vocabulary[i]: float(probs[i]) for i in range(len(vocabulary))}
    return dict(sorted(scores.items(), key=lambda x: -x[1]))


# ─── Main flagging logic ──────────────────────────────────────────────────────

def flag_product(
    title: str,
    description: str,
    image_url: str,
    confidence_threshold: float = 0.15,
) -> dict:
    """
    Stage 1 flagging for a single product.

    1. Extract stated colors/materials from title + description
    2. Load first image
    3. Ask CLIP to rank stated terms + common alternatives against the image
    4. Flag if stated term is not in CLIP's top-2
    """
    # Strip HTML from description
    desc_plain = re.sub(r"<[^>]+>", " ", description or "")
    combined_text = f"{title or ''} {desc_plain}"

    stated_colors = _extract_stated_colors(combined_text)
    stated_materials = _extract_stated_materials(combined_text)

    image = _load_image(image_url)
    if image is None:
        return {
            "stated_colors": stated_colors,
            "stated_materials": stated_materials,
            "clip_color_scores": {},
            "clip_material_scores": {},
            "flagged": [],
            "error": "Failed to load image",
        }

    # ── Colors ──
    clip_color_scores = {}
    flagged_colors = []
    if stated_colors:
        # Build vocabulary: stated colors + common alternatives
        color_vocab = list(set(stated_colors + COLORS))
        clip_color_scores = clip_rank_vocabulary(image, color_vocab)
        # Get top-2 CLIP color guesses
        sorted_colors = sorted(clip_color_scores.items(), key=lambda x: -x[1])
        top2_colors = [c for c, s in sorted_colors[:2]]
        for sc in stated_colors:
            if sc not in top2_colors and clip_color_scores.get(sc, 0) < confidence_threshold:
                flagged_colors.append({
                    "type": "color",
                    "stated": sc,
                    "clip_top2": top2_colors,
                    "clip_score": clip_color_scores.get(sc, 0),
                })

    # ── Materials ──
    # Materials are hard for CLIP to distinguish visually (silicone vs plastic,
    # cotton vs polyester, etc.) — only flag when confidence is VERY low.
    MATERIAL_AMBIGUOUS_PAIRS = {
        frozenset(("silicone", "plastic", "resin")),
        frozenset(("cotton", "polyester", "nylon", "melamine", "bamboo")),
        frozenset(("leather", "faux leather", "vinyl")),
        frozenset(("wood", "bamboo", "cork")),
        frozenset(("steel", "stainless steel", "iron")),
        frozenset(("glass", "acrylic")),
        frozenset(("rubber", "silicone", "plastic")),
        frozenset(("silk", "polyester", "satin")),
        frozenset(("ceramic", "porcelain", "melamine")),
        frozenset(("granite", "marble")),
    }

    def _is_ambiguous_material(mat1: str, mat2: str) -> bool:
        """Check if two materials are visually ambiguous (CLIP can't tell apart)."""
        m1, m2 = mat1.lower(), mat2.lower()
        for pair in MATERIAL_AMBIGUOUS_PAIRS:
            if m1 in pair and m2 in pair:
                return True
        return False

    clip_material_scores = {}
    flagged_materials = []
    if stated_materials:
        material_vocab = list(set(stated_materials + MATERIALS))
        clip_material_scores = clip_rank_vocabulary(image, material_vocab)
        sorted_mats = sorted(clip_material_scores.items(), key=lambda x: -x[1])
        top2_mats = [m for m, s in sorted_mats[:2]]
        for sm in stated_materials:
            # Skip if CLIP's top pick is an ambiguous synonym of the stated material
            if any(_is_ambiguous_material(sm, t) for t in top2_mats):
                continue
            # Only flag if stated material has very low CLIP score (< 0.005)
            # This catches genuine mismatches but not CLIP uncertainty
            if sm not in top2_mats and clip_material_scores.get(sm, 0) < 0.005:
                flagged_materials.append({
                    "type": "material",
                    "stated": sm,
                    "clip_top2": top2_mats,
                    "clip_score": clip_material_scores.get(sm, 0),
                })

    return {
        "stated_colors": stated_colors,
        "stated_materials": stated_materials,
        "clip_color_scores": {k: round(v, 4) for k, v in clip_color_scores.items() if v > 0.01},
        "clip_material_scores": {k: round(v, 4) for k, v in clip_material_scores.items() if v > 0.01},
        "flagged": flagged_colors + flagged_materials,
    }


def flag_products(products: List[dict]) -> List[dict]:
    """Run Stage 1 flagging on a list of products."""
    results = []
    for p in products:
        sku = p.get("sku", "unknown")
        title = p.get("title", "")
        description = p.get("description", "")
        images = p.get("images", [])
        image_url = images[0] if images else None

        if not image_url:
            results.append({
                "sku": sku,
                "stated_colors": [],
                "stated_materials": [],
                "clip_color_scores": {},
                "clip_material_scores": {},
                "flagged": [],
                "error": "No images",
            })
            continue

        t0 = time.time()
        r = flag_product(title, description, image_url)
        elapsed = time.time() - t0
        r["sku"] = sku
        r["elapsed_ms"] = round(elapsed * 1000)
        n_flagged = len(r["flagged"])
        if n_flagged:
            print(f"[CLIP_FLAG] {sku}: {n_flagged} flagged ({elapsed:.1f}s)", file=sys.stderr)
        results.append(r)

    return results


# ─── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 _clip_flag.py <input.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    products = data.get("products", [])
    results = flag_products(products)
    json.dump({"results": results}, sys.stdout, indent=2)
