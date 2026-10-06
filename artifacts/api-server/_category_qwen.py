#!/usr/bin/env python3
"""
Qwen-VL category re-rank (escalation path).

Marqo reads SHAPE, not FUNCTION: a kids' sipper bottle image reads as "party
cup", a casserole as "cooler bag". For the low-confidence / `review` rows this
module asks Qwen2.5-VL (local, MLX 4-bit) to pick the best category from a
CONSTRAINED shortlist of real DB paths.

Constrained choice (pick one of these / none) is far more reliable than free
generation and guarantees the returned path exists in the taxonomy.

Loaded lazily and shared via _qwen_model (one 3B model per process). Falls back
to the Marqo path when Qwen is unavailable or returns nothing usable.

Usage:
    python3 _category_qwen.py input.json   # {"products":[{title, images, shortlist:[...]}]}
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from typing import List, Optional

MAX_TOKENS = 64


def _load_image(url: str, timeout: int = 15):
    try:
        import urllib.request
        from io import BytesIO
        from PIL import Image
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return Image.open(BytesIO(urllib.request.urlopen(req, timeout=timeout).read())).convert("RGB")
    except Exception as e:
        print(f"[CATQWEN] image load failed {url}: {e}", file=sys.stderr)
        return None


def _build_prompt(title: str, shortlist: List[str]) -> str:
    options = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(shortlist))
    return f"""You are an e-commerce cataloguer. Choose the SINGLE best category for this product from the numbered list.

PRODUCT TITLE: {title or 'N/A'}

CANDIDATE CATEGORIES:
{options}

RULES:
- Use what the product actually IS and is USED FOR (its function), not just its shape.
- Choose exactly one candidate number from the list, or 0 if none fit.
- Respond with ONLY the number. No other text.

Answer (number):"""


def rerank_one(title: str, image_urls: List[str], shortlist: List[str]) -> Optional[str]:
    """Return the best shortlist path per Qwen, or None."""
    if not shortlist:
        return None
    try:
        from mlx_lm import stream_generate
        from _qwen_model import get_model, prepare_vlm_image
    except Exception as e:
        print(f"[CATQWEN] Qwen unavailable: {e}", file=sys.stderr)
        return None

    model, tokenizer, processor = get_model()
    image = _load_image(image_urls[0]) if image_urls else None
    if image is None:
        return None
    image = prepare_vlm_image(image)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        image.save(f, "JPEG", quality=90)
        temp_path = f.name

    try:
        messages = [
            {"role": "user", "content": [
                {"type": "image", "image": temp_path},
                {"type": "text", "text": _build_prompt(title, shortlist)},
            ]}
        ]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        t0 = time.time()
        chunks = []
        for r in stream_generate(model, tokenizer, prompt=text, max_tokens=MAX_TOKENS):
            chunks.append(r.text)
            if len(chunks) > 256:
                break
        raw = "".join(chunks)
        print(f"[CATQWEN] {title[:40]!r} -> {raw.strip()[:60]!r} in {time.time() - t0:.1f}s", file=sys.stderr)

        m = re.search(r"\d+", raw)
        if not m:
            return None
        idx = int(m.group(0))
        if 1 <= idx <= len(shortlist):
            return shortlist[idx - 1]
        return None  # 0 or out of range -> "none"
    except Exception as e:
        print(f"[CATQWEN] rerank failed: {e}", file=sys.stderr)
        return None
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass


def rerank_products(products: List[dict]) -> dict:
    results = []
    t0 = time.time()
    for i, p in enumerate(products):
        path = rerank_one(
            p.get("title", ""),
            p.get("images") or [],
            p.get("shortlist") or [],
        )
        results.append({"sku": p.get("sku", ""), "chosen": path})
        print(f"[CATQWEN] {i + 1}/{len(products)} in {time.time() - t0:.1f}s", file=sys.stderr)
    return {"results": results}


def main() -> None:
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        raw = open(input_path, "r", encoding="utf-8").read() if input_path else sys.stdin.read()
        payload = json.loads(raw or "{}")
        out = rerank_products(payload.get("products", []))
        sys.stdout.write(json.dumps(out))
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    main()
