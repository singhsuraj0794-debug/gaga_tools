#!/usr/bin/env python3
"""Shared lazy loader for the Qwen2.5-VL model.

Both _qwen_correct.py and _qwen_specs.py need the same 3B vision model.
Previously each module loaded its own copy in the same process — two copies
of a 3B model blew past 16GB RAM and crashed the machine. This module keeps
exactly ONE model loaded per Python process, shared by both callers.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

_model = None
_tokenizer = None
_processor = None
_model_path = None

# Prefer a 4-bit MLX build of Qwen2.5-VL-3B: the full-precision model runs at
# ~50-120s per image on an M-series GPU, which blows the tunnel's ~100s request
# limit and makes text correction unusable. The 4-bit build is ~2GB (vs 7.5GB)
# and several times faster at nearly the same quality. Override with
# QWEN_MODEL_PATH if needed.
QUANT_PATH = os.path.expanduser("~/.cache/huggingface/hub/Qwen2.5-VL-3B-Instruct-4bit")
QUANT_REPO = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"
MODEL_PATH = os.path.expanduser("~/.cache/huggingface/hub/Qwen2.5-VL-3B-Instruct")


def get_model():
    """Lazy-load Qwen2.5-VL-3B via mlx_lm, sharing one instance per process."""
    global _model, _tokenizer, _processor, _model_path
    if _model is not None:
        return _model, _tokenizer, _processor

    from mlx_lm import load
    from transformers import AutoProcessor

    candidates = [
        os.environ.get("QWEN_MODEL_PATH"),
        QUANT_PATH,
        MODEL_PATH,
    ]
    _model_path = next(
        (p for p in candidates if p and os.path.exists(os.path.join(p, "config.json"))),
        QUANT_REPO,
    )

    print(f"[QWEN] Loading model from {_model_path}...", file=sys.stderr)
    t0 = time.time()
    _model, _tokenizer = load(_model_path)
    _processor = AutoProcessor.from_pretrained(_model_path)
    print(f"[QWEN] Model loaded in {time.time()-t0:.1f}s", file=sys.stderr)
    return _model, _tokenizer, _processor


def prepare_vlm_image(image, max_side: int = 768, patch: int = 28):
    """Resize an image to patch-aligned dimensions for the Qwen2-VL encoder.

    The MLX/Metal build of the Qwen2.5-VL vision encoder can abort the process
    with an MPS assertion ("buffer is not large enough") on some image
    geometries/sizes. Bounding the longest side and aligning width/height to
    multiples of the patch size (28 = 2 x 14) keeps the encoder buffers valid.
    """
    try:
        from PIL import Image as _Image
        img = image.convert("RGB")
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            w, h = int(w * scale), int(h * scale)
        w = max(patch, (w // patch) * patch)
        h = max(patch, (h // patch) * patch)
        if (w, h) != img.size:
            img = img.resize((w, h), _Image.LANCZOS)
        return img
    except Exception:
        return image
