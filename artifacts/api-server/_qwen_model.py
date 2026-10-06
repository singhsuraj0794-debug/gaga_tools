#!/usr/bin/env python3
"""Shared lazy loader for the Qwen2.5-VL model.

Both _qwen_correct.py and _qwen_specs.py (and the CLIP-verify second pass) need
the same 3B vision model. This module keeps exactly ONE model loaded per Python
process — two copies of a 3B model blew past 16GB RAM and crashed the machine.

Backend: Hugging Face `transformers` (Qwen2_5_VL), which runs on CUDA, Apple
MPS, or CPU. It replaced the previous MLX-only path so the same code runs on the
Windows/NVIDIA host and on the Mac. On CUDA the model is loaded in bfloat16;
elsewhere float32.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

_model = None
_processor = None
_model_path = None
_lock = threading.Lock()

MODEL_PATH = os.path.expanduser("~/.cache/huggingface/hub/Qwen2.5-VL-3B-Instruct")
HF_ID = "Qwen/Qwen2.5-VL-3B-Instruct"


def _pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def get_model():
    """Lazy-load Qwen2.5-VL-3B via transformers, sharing one instance per process.

    Returns (model, processor).
    """
    global _model, _processor, _model_path
    if _model is not None:
        return _model, _processor

    with _lock:
        if _model is not None:
            return _model, _processor

        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        # NOTE: only transformers-compatible checkpoints are candidates. The old
        # mlx-community 4-bit MLX build cannot be loaded here.
        candidates = [os.environ.get("QWEN_MODEL_PATH"), MODEL_PATH]
        _model_path = next(
            (p for p in candidates if p and os.path.exists(os.path.join(p, "config.json"))),
            HF_ID,
        )

        device = _pick_device()
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        print(f"[QWEN] Loading model from {_model_path} on {device}...", file=sys.stderr)
        t0 = time.time()
        _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            _model_path, torch_dtype=dtype, low_cpu_mem_usage=True,
        )
        _model = _model.to(device)
        _model.eval()
        _processor = AutoProcessor.from_pretrained(_model_path)
        print(f"[QWEN] Model loaded in {time.time()-t0:.1f}s on {device}", file=sys.stderr)
        return _model, _processor


def generate(prompt_text: str, image_path: Optional[str] = None, max_tokens: int = 256) -> str:
    """Run a single generation against the shared model.

    Image is optional — pass a path for the vision model, or omit for text-only.
    Returns the decoded response string (without the prompt tokens).
    """
    import torch
    from PIL import Image

    model, processor = get_model()

    content = []
    if image_path:
        content.append({"type": "image", "image": image_path})
    content.append({"type": "text", "text": prompt_text})
    messages = [{"role": "user", "content": content}]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    kwargs = {"text": [text], "return_tensors": "pt", "padding": True}
    if image_path:
        kwargs["images"] = [Image.open(image_path).convert("RGB")]

    inputs = processor(**kwargs).to(model.device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    trimmed = out[:, inputs["input_ids"].shape[1]:]
    return processor.batch_decode(
        trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False,
    )[0]


def prepare_vlm_image(image, max_side: int = 768, patch: int = 28):
    """Resize an image to patch-aligned dimensions for the Qwen2-VL encoder.

    Bounding the longest side and aligning width/height to multiples of the
    patch size (28 = 2 x 14) keeps the encoder buffers small and the request
    within the tunnel's time budget.
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
