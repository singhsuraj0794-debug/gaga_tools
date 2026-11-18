#!/usr/bin/env python3
"""Shared vision-model backend for _qwen_correct / _qwen_specs / CLIP-verify.

Primary backend: HTTP to a llama.cpp chat server (DeepSeek-V4-Pro-Qwen3.5-9B on
the GPU host, :8082) via the OpenAI-compatible `/v1/chat/completions` API. This
is the same server the category/text agents use, so one process serves every
LLM call and no transformers weights are loaded locally.

Fallback backend: Hugging Face `transformers` (Qwen2_5_VL), which runs on CUDA,
Apple MPS, or CPU — used when no HTTP server is reachable (e.g. local Mac dev).

Select explicitly with QWEN_BACKEND=http|transformers; otherwise it probes
QWEN_AGENT_URL (default http://127.0.0.1:8082) and picks HTTP when it answers.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.request
from typing import Optional

_model = None
_processor = None
_model_path = None
_lock = threading.Lock()

_DEFAULT_HF_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
# Override with QWEN_MODEL (HF id) — e.g. a bigger/stronger VL model on the CUDA
# host (Qwen/Qwen2.5-VL-7B-Instruct). MODEL_PATH follows the chosen id so a
# locally-cached bigger model is used when present, otherwise it is downloaded.
HF_ID = os.environ.get("QWEN_MODEL") or _DEFAULT_HF_ID
MODEL_PATH = os.path.expanduser(
    os.environ.get("QWEN_MODEL_PATH")
    or os.path.join("~/.cache/huggingface/hub", HF_ID.split("/")[-1])
)


def _pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
_backend: Optional[str] = None


def _agent_url() -> str:
    return (
        os.environ.get("QWEN_AGENT_URL")
        or os.environ.get("VISION_AGENT_URL")
        or os.environ.get("CATEGORY_AGENT_URL")
        or "http://127.0.0.1:8082"
    ).rstrip("/")


def _http_available() -> bool:
    try:
        req = urllib.request.Request(_agent_url() + "/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def _pick_backend() -> str:
    global _backend
    if _backend:
        return _backend
    forced = (os.environ.get("QWEN_BACKEND") or "").strip().lower()
    if forced in ("http", "transformers"):
        _backend = forced
    elif forced in ("", "auto") and _http_available():
        _backend = "http"
    else:
        _backend = "transformers"
    return _backend


def _http_generate(prompt_text: str, image_path: Optional[str], max_tokens: int) -> str:
    content = []
    if image_path:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": prompt_text})
    payload = {
        "messages": [{"role": "user", "content": content}],
        "max_tokens": int(max_tokens),
        "temperature": 0,
    }
    req = urllib.request.Request(
        _agent_url() + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    return (data["choices"][0]["message"].get("content") or "")
def get_model():
    """Return the shared local model, or (None, None) for the HTTP backend.

    Lazy-loads Qwen2.5-VL-3B via transformers, sharing one instance per process.
    Returns (model, processor).
    """
    if _pick_backend() == "http":
        return None, None
    global _model, _processor, _model_path
    if _model is not None:
        return _model, _processor

    with _lock:
        if _model is not None:
            return _model, _processor

        import torch
        from transformers import AutoProcessor

        # NOTE: only transformers-compatible checkpoints are candidates. The old
        # mlx-community 4-bit MLX build cannot be loaded here.
        candidates = [os.environ.get("QWEN_MODEL_PATH"), MODEL_PATH]
        _model_path = next(
            (p for p in candidates if p and os.path.exists(os.path.join(p, "config.json"))),
            HF_ID,
        )
        # Qwen3-VL and Qwen2.5-VL use different model classes.
        if "qwen3-vl" in str(_model_path).lower():
            from transformers import Qwen3VLForConditionalGeneration as VLM
        else:
            from transformers import Qwen2_5_VLForConditionalGeneration as VLM

        device = _pick_device()
        load_4bit = (
            device == "cuda"
            and str(os.environ.get("QWEN_LOAD_IN_4BIT", "")).lower() in ("1", "true", "yes", "on")
        )
        kwargs: dict = {"low_cpu_mem_usage": True}
        if load_4bit:
            # 4-bit NF4 lets a 7B VL model fit in ~6GB VRAM alongside CLIP/Marqo.
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
            kwargs["device_map"] = "auto"
            quant = "4bit"
        else:
            kwargs["torch_dtype"] = torch.bfloat16 if device == "cuda" else torch.float32
            quant = "bf16" if device == "cuda" else "fp32"

        print(f"[QWEN] Loading {_model_path} on {device} ({quant})...", file=sys.stderr)
        t0 = time.time()
        _model = VLM.from_pretrained(_model_path, **kwargs)
        if not load_4bit:
            _model = _model.to(device)
        _model.eval()
        _processor = AutoProcessor.from_pretrained(_model_path)
        print(f"[QWEN] Model loaded in {time.time()-t0:.1f}s ({quant})", file=sys.stderr)
        return _model, _processor


def generate(prompt_text: str, image_path: Optional[str] = None, max_tokens: int = 256) -> str:
    """Run a single generation against the shared vision/chat model.

    Image is optional — pass a path for the vision model, or omit for text-only.
    Returns the decoded response string (without the prompt tokens). Uses the
    HTTP llama.cpp backend when available, else local transformers.
    """
    if _pick_backend() == "http":
        return _http_generate(prompt_text, image_path, max_tokens)

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
