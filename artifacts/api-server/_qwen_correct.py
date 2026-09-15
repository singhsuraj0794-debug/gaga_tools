#!/usr/bin/env python3
"""
Stage 2: Qwen2.5-VL-3B correction for flagged attributes.

Takes flagged items from Stage 1 (CLIP flagging) and uses a local VLM
to state what it actually sees for the flagged attribute(s), then builds
a corrected description.

Usage:
    python3 _qwen_correct.py <input.json>

Input:
{
  "products": [
    {
      "sku": "...",
      "title": "...",
      "description": "...",
      "images": ["https://..."],
      "flagged": [
        {"type": "color", "stated": "red", "clip_top2": ["purple", "pink"]},
        {"type": "material", "stated": "silicon", "clip_top2": ["plastic", "rubber"]}
      ]
    }
  ]
}

Output:
{
  "results": [
    {
      "sku": "...",
      "corrections": [
        {"attribute": "color", "original": "red", "observed": "purple", "confidence": "high"},
        {"attribute": "material", "original": "silicon", "observed": "plastic", "confidence": "medium"}
      ],
      "corrected_title": "...",
      "corrected_description": "..."
    }
  ]
}
"""
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional
from io import BytesIO

import requests
from PIL import Image

# ─── Model loading ────────────────────────────────────────────────────────────

from _qwen_model import get_model as _shared_get_model

def _get_model():
    """Lazy-load Qwen2.5-VL-3B-Instruct via mlx_lm, shared across modules."""
    return _shared_get_model()


# ─── Image loading ────────────────────────────────────────────────────────────

def _load_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    """Load image from URL."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        print(f"[QWEN] Failed to load image {url}: {e}", file=sys.stderr)
        return None


# ─── VLM query ────────────────────────────────────────────────────────────────

def query_vlm(
    image: Image.Image,
    flagged_attrs: List[dict],
    title: str = "",
    description: str = "",
) -> List[dict]:
    """
    Ask Qwen what it actually sees for each flagged attribute.

    Returns list of corrections:
    [{"attribute": "color", "original": "red", "observed": "purple", "confidence": "high"}]
    """
    from mlx_lm import stream_generate

    model, tokenizer, processor = _get_model()

    # Build focused prompt for flagged attributes
    # Group by type for cleaner prompt
    color_flags = [fa for fa in flagged_attrs if fa["type"] == "color"]
    material_flags = [fa for fa in flagged_attrs if fa["type"] == "material"]

    attr_sections = []
    if color_flags:
        colors_listed = ", ".join(f'"{fa["stated"]}"' for fa in color_flags)
        attr_sections.append(f"FLAGGED COLORS (listing claims these colors exist: {colors_listed})")
    if material_flags:
        mats_listed = ", ".join(f'"{fa["stated"]}"' for fa in material_flags)
        attr_sections.append(f"FLAGGED MATERIALS (listing claims: {mats_listed})")

    prompt_text = f"""Examine this product image carefully and answer ONLY about what you see in the image itself.

PRODUCT TITLE: {title or 'N/A'}

WHAT TO CHECK:
{chr(10).join(attr_sections)}

RULES:
- For colors: What is the ACTUAL color of the main product? Ignore backgrounds, packaging.
- For materials: What material does the product APPEAR to be made of based on texture/sheen? Common materials: cotton, leather, plastic, silicone, metal, wood, ceramic, paper, rubber, steel, bamboo, glass, fabric, canvas, nylon, linen, melamine, resin, clay, copper.
- If the listing is CORRECT, return the SAME value as "original" in "observed" with confidence "high".
- If UNSURE, return the SAME value as "original" with confidence "low".
- NEVER use placeholder text. Only use actual material/color names.

Respond in EXACTLY this JSON array format (no other text):
[
  {{"attribute": "TYPE", "original": "ORIGINAL_VALUE", "observed": "ACTUAL_VALUE_OR_SAME", "confidence": "high_or_medium_or_low"}}
]

Example — if listing says "cotton" but image shows leather:
[{{"attribute": "material", "original": "cotton", "observed": "leather", "confidence": "high"}}]"""

    # Save temp image for processor (patch-aligned to avoid MLX/MPS crash)
    from _qwen_model import prepare_vlm_image
    image = prepare_vlm_image(image)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        image.save(f, "JPEG", quality=90)
        temp_path = f.name

    try:
        # Build chat messages with image
        messages = [
            {"role": "user", "content": [
                {"type": "image", "image": temp_path},
                {"type": "text", "text": prompt_text}
            ]}
        ]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        t0 = time.time()
        responses = []
        for r in stream_generate(model, tokenizer, prompt=text, max_tokens=256):
            responses.append(r)
            if len(responses) > 1024:
                break
        elapsed = time.time() - t0
        response_text = "".join([r.text for r in responses])
        print(f"[QWEN] VLM query took {elapsed:.1f}s", file=sys.stderr)

        # Parse JSON from response
        return _parse_corrections(response_text, flagged_attrs)

    except Exception as e:
        print(f"[QWEN] VLM query failed: {e}", file=sys.stderr)
        return [{"attribute": fa["type"], "original": fa["stated"],
                 "observed": None, "confidence": "failed", "error": str(e)}
                for fa in flagged_attrs]
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _parse_corrections(response: str, flagged_attrs: List[dict]) -> List[dict]:
    """Parse Qwen's JSON response into corrections list."""
    # Extract JSON from response (may be wrapped in ```json ... ```)
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON array
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            print(f"[QWEN] Could not parse response: {response[:200]}", file=sys.stderr)
            return [{"attribute": fa["type"], "original": fa["stated"],
                     "observed": None, "confidence": "parse_error"}
                    for fa in flagged_attrs]

    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError as e:
        print(f"[QWEN] JSON decode error: {e}", file=sys.stderr)

    return [{"attribute": fa["type"], "original": fa["stated"],
             "observed": None, "confidence": "parse_error"}
            for fa in flagged_attrs]


# ─── Apply corrections to text ────────────────────────────────────────────────

def apply_corrections(
    title: str,
    description: str,
    corrections: List[dict],
) -> dict:
    """
    Apply VLM corrections to title and description.
    Returns {"corrected_title": ..., "corrected_description": ...}
    """
    corrected_title = title or ""
    corrected_desc = description or ""

    # Valid material/color values — reject garbage
    _VALID_MATERIALS = {
        "cotton", "leather", "plastic", "silicone", "rubber", "metal", "steel",
        "wood", "bamboo", "ceramic", "glass", "paper", "fabric", "canvas",
        "nylon", "linen", "melamine", "resin", "clay", "copper", "aluminum",
        "iron", "zinc", "stone", "marble", "jute", "wool", "silk", "polyester",
        "acrylic", "cardboard", "porcelain", "terracotta",
    }

    for c in corrections:
        if not c.get("observed") or c.get("confidence") == "failed":
            continue

        attr = c["attribute"]
        original = c["original"]
        observed = c["observed"]

        # Reject garbage values
        if observed.lower() in ("observed", "original", "none", "unknown", "n/a", "tbd", "see image", "unclear", "not sure"):
            c["confidence"] = "failed"
            c["_reject_reason"] = f"observed value '{observed}' is placeholder text"
            continue
        # Reject if observed is same as attribute name
        if observed.lower() == attr.lower():
            c["confidence"] = "failed"
            c["_reject_reason"] = f"observed value matches attribute name"
            continue
        # For materials, reject if not in known list (unless it's clearly a valid word)
        if attr == "material" and observed.lower() not in _VALID_MATERIALS and len(observed) > 20:
            c["confidence"] = "failed"
            c["_reject_reason"] = f"observed material '{observed}' not recognized"
            continue

    for c in corrections:
        if not c.get("observed") or c.get("confidence") == "failed":
            continue

        attr = c["attribute"]
        original = c["original"]
        observed = c["observed"]

        if attr == "color":
            # Replace color in title (case-insensitive)
            corrected_title = re.sub(
                r"\b" + re.escape(original) + r"\b",
                observed,
                corrected_title,
                flags=re.IGNORECASE,
            )
            # Replace in description
            corrected_desc = re.sub(
                r"\b" + re.escape(original) + r"\b",
                observed,
                corrected_desc,
                flags=re.IGNORECASE,
            )
        elif attr == "material":
            corrected_title = re.sub(
                r"\b" + re.escape(original) + r"\b",
                observed,
                corrected_title,
                flags=re.IGNORECASE,
            )
            corrected_desc = re.sub(
                r"\b" + re.escape(original) + r"\b",
                observed,
                corrected_desc,
                flags=re.IGNORECASE,
            )

    return {
        "corrected_title": corrected_title,
        "corrected_description": corrected_desc,
    }


# ─── Main entry point ─────────────────────────────────────────────────────────

def correct_products(products: List[dict]) -> List[dict]:
    """
    Run Stage 2 correction on flagged products.

    Each product must have: sku, title, description, images, flagged
    """
    results = []

    # Only process products that have flagged items
    flagged_products = [p for p in products if p.get("flagged")]
    if not flagged_products:
        print("[QWEN] No flagged products to correct", file=sys.stderr)
        return [{"sku": p.get("sku"), "corrections": [], "corrected_title": p.get("title"),
                 "corrected_description": p.get("description")} for p in products]

    for p in flagged_products:
        sku = p.get("sku", "unknown")
        title = p.get("title", "")
        description = p.get("description", "")
        images = p.get("images", [])
        flagged = p.get("flagged", [])

        if not images:
            results.append({
                "sku": sku,
                "corrections": [],
                "corrected_title": title,
                "corrected_description": description,
                "error": "No images",
            })
            continue

        image = _load_image(images[0])
        if image is None:
            results.append({
                "sku": sku,
                "corrections": [],
                "corrected_title": title,
                "corrected_description": description,
                "error": "Failed to load image",
            })
            continue

        t0 = time.time()
        corrections = query_vlm(image, flagged, title, description)
        applied = apply_corrections(title, description, corrections)
        elapsed = time.time() - t0

        n_corrected = sum(1 for c in corrections if c.get("observed"))
        print(f"[QWEN] {sku}: {n_corrected}/{len(corrections)} corrected ({elapsed:.1f}s)", file=sys.stderr)

        results.append({
            "sku": sku,
            "corrections": corrections,
            "corrected_title": applied["corrected_title"],
            "corrected_description": applied["corrected_description"],
            "elapsed_ms": round(elapsed * 1000),
        })

    # Add non-flagged products back with no corrections
    flagged_skus = {p.get("sku") for p in flagged_products}
    for p in products:
        if p.get("sku") not in flagged_skus:
            results.append({
                "sku": p.get("sku"),
                "corrections": [],
                "corrected_title": p.get("title"),
                "corrected_description": p.get("description"),
            })

    return results


# ─── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 _qwen_correct.py <input.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    products = data.get("products", [])
    results = correct_products(products)
    json.dump({"results": results}, sys.stdout, indent=2)
