#!/usr/bin/env python3
"""
Qwen VLM Spec Extractor
========================
Uses Qwen2.5-VL-3B-Instruct to extract accurate product specs
from product image + title + description.

Usage:
    python3 _qwen_specs.py <input.json>

Input:
{
  "products": [
    {"sku": "...", "title": "...", "description": "...", "images": ["https://..."]}
  ]
}

Output:
{
  "results": [
    {"sku": "...", "specs": {"Material": "...", "Color": "...", "Type": "...", ...}}
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


def _load_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    """Load image from URL."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        print(f"[QWEN-SPECS] Failed to load image {url}: {e}", file=sys.stderr)
        return None


# ─── Spec extraction ─────────────────────────────────────────────────────────

_VALID_SPECS = {
    "Material", "Color", "Type", "Use", "Target", "Size", "Capacity",
    "Weight", "Dimensions", "Pattern", "Theme", "Pack", "Occasion",
    "Brand", "Power", "Warranty", "Product", "Category",
}


def extract_specs_qwen(
    image: Image.Image,
    title: str,
    description: str = "",
) -> Dict[str, str]:
    """
    Ask Qwen to extract accurate specs from the product image + text.
    Returns dict of {spec_name: value}.
    """
    from mlx_lm import stream_generate

    model, tokenizer, processor = _get_model()

    # Truncate description to avoid token limits
    desc_short = (description[:500] if description else "")[:500]

    prompt_text = f"""Examine this product image and extract accurate specifications.

PRODUCT TITLE: {title or 'N/A'}
PRODUCT DESCRIPTION: {desc_short or 'N/A'}

RULES:
- Extract ONLY specs you can CONFIRM from the image or title/description.
- For Material: What does the product APPEAR to be made of? (cotton, plastic, metal, glass, wood, silicone, leather, steel, ceramic, paper, rubber, bamboo, nylon, polyester, melamine, resin, fabric, canvas, linen, jute, clay, copper, bronze, alloy, acrylic)
- For Color: What is the ACTUAL color of the main product? Ignore backgrounds/packaging.
- For Type: What TYPE of product is this? (e.g., bottle, bag, cleaner, tablet, shirt, shoe, lamp, cooker, toy, etc.)
- For Use: What is the product USED FOR? (cleaning, cooking, decoration, storage, personal care, etc.)
- For Pack/Quantity: How many items in the pack? (e.g., "15 tablets", "set of 3", "pair")
- For Target: Who is this for? (home, professional, kids, men, women, etc.)
- If UNSURE about a spec, DO NOT include it.
- NEVER fabricate specs. Only include what you can see or confirm from the text.
- NEVER return Material: "fabric" for cleaning products, tablets, liquids, or chemicals.

Respond in EXACTLY this JSON object format (no other text):
{{"specs": {{"Material": "...", "Color": "...", "Type": "...", "Use": "...", "Pack": "...", "Target": "..."}}}}

Only include specs you are confident about. Omit any you are unsure about."""

    # Save temp image for processor (patch-aligned to avoid MLX/MPS crash)
    from _qwen_model import prepare_vlm_image
    image = prepare_vlm_image(image)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        image.save(f, "JPEG", quality=90)
        temp_path = f.name

    try:
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
        print(f"[QWEN-SPECS] VLM query took {elapsed:.1f}s", file=sys.stderr)

        return _parse_specs(response_text)

    except Exception as e:
        print(f"[QWEN-SPECS] VLM query failed: {e}", file=sys.stderr)
        return {}
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _parse_specs(response: str) -> Dict[str, str]:
    """Parse Qwen's JSON response into specs dict."""
    # Extract JSON from response (may be wrapped in ```json ... ```)
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON object
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            print(f"[QWEN-SPECS] No JSON found in response: {response[:200]}", file=sys.stderr)
            return {}

    try:
        data = json.loads(json_str)
        specs = data.get("specs", data) if isinstance(data, dict) else {}
        # Validate spec names and clean values
        cleaned = {}
        for k, v in specs.items():
            if k in _VALID_SPECS and v and isinstance(v, str) and v.strip():
                # Reject garbage values
                v_lower = v.strip().lower()
                if v_lower in ("observed", "original", "n/a", "none", "unknown", "unclear", "not sure", "unsure"):
                    continue
                cleaned[k] = v.strip()
        return cleaned
    except json.JSONDecodeError as e:
        print(f"[QWEN-SPECS] JSON parse error: {e}", file=sys.stderr)
        return {}


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    products = data.get("products", [])
    results = []

    for p in products:
        sku = p.get("sku", "")
        title = p.get("title", "")
        description = p.get("description", "")
        images = p.get("images", [])

        if not images:
            results.append({"sku": sku, "specs": {}})
            continue

        # Use first image
        image = _load_image(images[0])
        if image is None:
            results.append({"sku": sku, "specs": {}})
            continue

        specs = extract_specs_qwen(image, title, description)
        results.append({"sku": sku, "specs": specs})

    print(json.dumps({"results": results}))
