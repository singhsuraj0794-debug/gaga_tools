#!/usr/bin/env python3
"""
CLIP Image Verification — Rules 5 & 6 from Gajab Pre-Listing Module Guideline.

Rule 5: First image markings detection (watermark/logo/text overlay)
Rule 6: Content moderation across all images

Usage:
    python3 _clip_verify.py <input.json>
    python3 _clip_verify.py --first-image <image_url>    # Rule 5 only
    python3 _clip_verify.py --content <image_url>        # Rule 6 only

Input JSON format:
{
  "products": [
    {
      "sku": "...",
      "firstImageUrl": "https://...",
      "allImageUrls": ["https://...", ...]
    }
  ]
}

Output JSON (stdout):
{
  "results": [
    {
      "sku": "...",
      "rule5": { "hasMarkings": true, "watermarkScore": 0.87, "cleanScore": 0.13, "flagged": true },
      "rule6": { "images": [...], "anyFlagged": false }
    }
  ]
}
"""
import io
import json
import os
import re
import sys
import traceback
import time
from typing import Optional, Dict, List, Any

import requests
from PIL import Image

# Paddle 3.x static inference crashes on some CPUs with the oneDNN (MKLDNN)
# backend ("ConvertPirAttribute2RuntimeAttribute not support"). Disable it
# before paddle is imported. Must be set before the paddle import in
# get_ocr_reader().
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# ─── Lazy-loaded model ───────────────────────────────────────────────────────
_clip_model = None
_clip_processor = None

# ─── Rule 5 prompts: markings/logo/text detection ────────────────────────────
# Prompts target markings ADDED to the image (overlays, stamps, edited text),
# NOT the product's own printed branding or packaging text — which is expected
# and allowed. The "logo on the product" case was a false-positive source, so
# it is intentionally excluded.
RULE5_CHECKS = [
    # (check_name, positive_prompt, negative_prompt, threshold)
    # The "watermark/markings" sub-check was removed (not required). The
    # promotional-overlay checks below still catch sale badges ("BESTSELLER").
    (
        "overlay_graphics",
        "a photo with a large translucent sale banner, discount badge, or price sticker digitally pasted across the top of a plain product image",
        "a plain product photograph showing only the product itself without any added banners",
        0.88,
    ),
    (
        "added_text",
        "a product photo with promotional captions, price tags, or sale text edited on top of the image",
        "a product photo with no promotional text added on top",
        # 0.80: clean product/packaging shots score 0.53-0.75, while a real
        # promotional badge ("BESTSELLER" ribbon) scores ~0.82 — 0.90 missed it.
        0.80,
    ),
]

# Minimum ratio of positive_score / (positive_score + negative_score) to flag
RULE5_CONFIDENCE_MARGIN = 0.60

# Global minimum overlay confidence required to flag ANY overlay sub-check.
# The user-facing rule is: an image is only flagged for an overlay when the
# detection confidence is 65% or higher. This acts as a floor on top of each
# sub-check's own threshold, so noisy signals (notably the OCR-based CJK
# overlay check, which previously fired at just 10% CJK text) stop producing
# false positives. Override with OVERLAY_FLAG_THRESHOLD=0.xx if needed.
OVERLAY_FLAG_THRESHOLD = float(os.environ.get("OVERLAY_FLAG_THRESHOLD", "0.65"))

# ─── Rule 6 prompts: content moderation ──────────────────────────────────────
# Kept deliberately specific; CLIP zero-shot is noisy for moderation, so the
# confidence margin is high to avoid false positives on ordinary products.
RULE6_PROMPTS = [
    "a photo containing nudity or sexually explicit pornographic content",
    "a photo of graphic violence, gore, or blood",
    "a clean, appropriate product photograph suitable for an e-commerce listing",
]

# Bad prompt must beat clean by this ratio (high bar — false positives are costly)
RULE6_CONFIDENCE_MARGIN = 3.5

# ...AND the bad prompt must reach this absolute probability. Without it a
# near-tie (e.g. clean 0.20 / gore 0.51) could flag a perfectly normal product
# photo just because CLIP didn't strongly match the "clean" prompt — the main
# source of "no gore in this image" false positives.
RULE6_MIN_BAD_SCORE = 0.30

# ─── Image loading ───────────────────────────────────────────────────────────

_image_bytes_cache: Dict[str, bytes] = {}
_image_pil_cache: Dict[str, "Image.Image"] = {}
# Memory bounds. A decoded 1125x1500 RGB PIL image is ~5MB, so caching 300 of
# them costs ~1.5GB. Combined with Qwen (~3GB) + CLIP + EasyOCR that pushed the
# 16GB machine into swap during long runs — which is why image analysis started
# fast and then slowed to a crawl. Caches are now small LRUs: the bytes cache
# avoids re-downloading, and a few decoded images cover a product's rules.
_IMAGE_CACHE_MAX = 150          # raw bytes (~30MB)
_IMAGE_PIL_CACHE_MAX = 24       # decoded PIL images (~120MB)

# ── Shared cross-process disk cache ─────────────────────────────────────────
# clip-verify, HSN, correct-text, Qwen, Marqo and sheet-duplicates all download
# the SAME product images; without a shared cache each URL is fetched 4-6x per
# run. Cache the raw bytes on disk keyed by URL so any process reuses them.
# Override the location with GAJAB_IMG_CACHE.
_IMG_CACHE_DIR = os.environ.get("GAJAB_IMG_CACHE") or os.path.join(
    os.path.expanduser("~"), ".cache", "gajab_images"
)
try:
    os.makedirs(_IMG_CACHE_DIR, exist_ok=True)
except OSError:
    _IMG_CACHE_DIR = None


def _disk_cache_path(url: str) -> Optional[str]:
    if not _IMG_CACHE_DIR:
        return None
    import hashlib
    return os.path.join(_IMG_CACHE_DIR, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".img")


_http_session = None
_http_session_fast = None


def _get_http_session(fast: bool = False):
    """Shared requests session with a pooled adapter + retries.

    fetch_all_products downloads thousands of CDN images; plain requests.get()
    opens a fresh connection each time and under load fails with
    'HTTPSConnectionPool ... Max retries exceeded'. A pooled session with
    automatic retry on transient errors fixes those false 'Failed to load'
    results for images that are actually live.

    fast=True (category validation): ONE quick retry with no backoff. The
    robust session above uses backoff_factor=1.0 x 6 retries, which sleeps
    ~62s (0+2+4+8+16+32) before failing on a persistently-502ing image —
    that single stall froze an entire validation batch for over a minute.
    A dead image should fail in seconds, not a minute.
    """
    global _http_session, _http_session_fast
    if fast:
        if _http_session_fast is None:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            retry = Retry(
                total=1, connect=1, read=1, status=1, other=1,
                backoff_factor=0.2,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "HEAD"],
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
            s = requests.Session()
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _http_session_fast = s
        return _http_session_fast
    if _http_session is None:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=6,
            connect=6,   # retries connection/DNS failures ("Failed to resolve host")
            read=3,
            other=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        s = requests.Session()
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _http_session = s
    return _http_session


def _alternate_image_urls(source: str):
    """Yield fallback URLs for a Flipkart CDN image that may 404.

    Flipkart's live CDN images use HYPHENATED slugs; many sheets (and older
    scrapes) contain legacy UNDERSCORE variants that return 404. Swapping
    '_' -> '-' in the path recovers them, so the validator works even on
    sheets that were not repaired at the source.
    """
    try:
        m = re.match(r"(https?://[^/]+/image/\d+/\d+/)(.*)$", source)
        if not m:
            return
        alt = m.group(1) + m.group(2).replace("_", "-")
        if alt != source:
            yield alt
    except Exception:
        return


def load_image(source: str, timeout: int = 15, fast: bool = False) -> Optional[Image.Image]:
    """Load image from URL or local path. PIL images are cached so the same
    URL is never downloaded or decoded twice across all callers.

    fast=True uses the quick-retry session (see _get_http_session) — for
    latency-sensitive paths like category validation where a dead image must
    fail in seconds instead of ~60s of backoff.
    """
    try:
        if source.startswith(("http://", "https://")):
            cached = _image_pil_cache.get(source)
            if cached is not None:
                return cached
            data = _image_bytes_cache.get(source)
            if data is None:
                # Shared cross-process disk cache (see _disk_cache_path).
                disk = _disk_cache_path(source)
                if disk and os.path.exists(disk) and os.path.getsize(disk) > 0:
                    try:
                        with open(disk, "rb") as fh:
                            data = fh.read()
                    except OSError:
                        data = None
            if data is None:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                }
                if "flixcart" in source or "flipkart" in source:
                    headers["Referer"] = "https://www.flipkart.com/"
                session = _get_http_session(fast=fast)
                resp = session.get(source, headers=headers, timeout=timeout)
                # Auto-recover legacy underscore URL variants that 404.
                if resp.status_code == 404:
                    for alt in _alternate_image_urls(source):
                        try:
                            alt_resp = session.get(alt, headers=headers, timeout=timeout)
                            if alt_resp.status_code == 200:
                                resp = alt_resp
                                break
                        except Exception:
                            continue
                resp.raise_for_status()
                data = resp.content
                # LRU: evict the oldest entry rather than clearing everything
                # (clearing caused re-download bursts and memory spikes).
                if len(_image_bytes_cache) >= _IMAGE_CACHE_MAX:
                    _image_bytes_cache.pop(next(iter(_image_bytes_cache)), None)
                _image_bytes_cache[source] = data
                # Persist to the shared disk cache so other processes reuse it.
                if disk:
                    try:
                        tmp = disk + ".tmp"
                        with open(tmp, "wb") as fh:
                            fh.write(data)
                        os.replace(tmp, disk)
                    except OSError:
                        pass
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if len(_image_pil_cache) >= _IMAGE_PIL_CACHE_MAX:
                _image_pil_cache.pop(next(iter(_image_pil_cache)), None)
            _image_pil_cache[source] = img
            return img
        else:
            return Image.open(source).convert("RGB")
    except Exception as e:
        print(f"[WARN] Failed to load image {source[:80]}: {e}", file=sys.stderr)
        return None


# ─── CLIP model ──────────────────────────────────────────────────────────────

def get_clip():
    global _clip_model, _clip_processor
    if _clip_model is None:
        import torch
        # AutoModel/AutoProcessor load either a CLIP or a SigLIP checkpoint, so
        # CLIP_MODEL can be swapped (e.g. google/siglip-so400m-patch14-384) for
        # stronger zero-shot. Both expose get_text_features/get_image_features.
        from transformers import AutoProcessor, AutoModel

        model_name = os.environ.get("CLIP_MODEL") or "openai/clip-vit-base-patch32"
        print(f"[INFO] Loading image model: {model_name}", file=sys.stderr)
        t0 = time.time()
        _use_cuda = torch.cuda.is_available()
        # bf16 on CUDA: halves VRAM and uses the tensor cores (fp32 wasted both).
        _dt = torch.bfloat16 if _use_cuda else torch.float32
        _clip_model = AutoModel.from_pretrained(model_name, torch_dtype=_dt)
        _clip_processor = AutoProcessor.from_pretrained(model_name)
        _clip_model.eval()
        if _use_cuda:
            _clip_model = _clip_model.to("cuda")
        elif torch.backends.mps.is_available():
            _clip_model = _clip_model.to("mps")
        t1 = time.time()
        print(f"[INFO] Image model loaded in {t1 - t0:.1f}s", file=sys.stderr)
    return _clip_model, _clip_processor


# ─── Qwen VLM second-pass verifier ────────────────────────────────────────────
# CLIP zero-shot produces false positives for Rule 5 (markings) and Rule 6
# (content moderation). Qwen2.5-VL actually sees the image and can confirm or
# clear a CLIP flag. Used as a second-pass to reduce false positives.

def _get_qwen():
    """Shared Qwen2.5-VL model (transformers backend, GPU when available)."""
    from _qwen_model import get_model as _shared_get_model
    model, processor = _shared_get_model()
    return model, None, processor


def verify_rule_with_qwen(img: Image.Image, rule_type: str) -> Optional[bool]:
    """
    Second-pass verification of a CLIP flag using Qwen VLM.
    rule_type: "markings" or "content".
    Returns:
        True  — Qwen confirms the flag (real problem)
        False — Qwen clears the flag (false positive)
        None  — Qwen unsure / failed
    """
    if rule_type == "markings":
        question = (
            "Does this image have a watermark, logo stamp, or promotional text "
            "overlay that was digitally added on top of the product photo? "
            "Note: branding or text printed ON the product itself does NOT count. "
            "Answer with only YES or NO."
        )
    elif rule_type == "content":
        question = (
            "Does this image contain nudity, sexually explicit content, graphic "
            "violence, gore, or blood? Answer with only YES or NO."
        )
    else:
        return None

    try:
        from _qwen_model import generate as _qwen_generate

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img.save(f, "JPEG", quality=90)
            temp_path = f.name

        try:
            t0 = time.time()
            response_text = _qwen_generate(question, temp_path, max_tokens=16).strip().upper()
            print(f"[QWEN-VERIFY] {rule_type} query took {time.time()-t0:.1f}s → '{response_text}'", file=sys.stderr)

            if "YES" in response_text:
                return True
            if "NO" in response_text:
                return False
            return None
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    except Exception as e:
        print(f"[QWEN-VERIFY] Verification failed: {e}", file=sys.stderr)
        return None


_clip_text_feat_cache: dict = {}


def _clip_text_features(model, processor, labels):
    """CLIP/SigLIP text features for a label set, cached across calls.

    clip_zero_shot runs once per image AND per taxonomy; re-encoding the same
    label set on every call was the dominant cost of the product-context
    analysis (~10s/product). Cache the normalized text features on device.
    """
    import torch
    key = tuple(labels)
    hit = _clip_text_feat_cache.get(key)
    if hit is not None:
        return hit
    device = next(model.parameters()).device
    is_siglip = type(model).__name__.startswith("Siglip")
    kwargs: dict = {"text": list(labels), "return_tensors": "pt", "truncation": True}
    if is_siglip:
        kwargs["padding"] = "max_length"
        kwargs["max_length"] = 64
    else:
        kwargs["padding"] = True
    text_inputs = {
        k: (v.to(device=device, dtype=next(model.parameters()).dtype) if v.is_floating_point() else v.to(device))
        for k, v in processor(**kwargs).items()
    }
    with torch.no_grad():
        feats = model.get_text_features(**text_inputs)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    _clip_text_feat_cache[key] = feats
    return feats


_clip_img_feat_cache: dict = {}


def _clip_image_features(model, processor, image):
    """CLIP/SigLIP image features, cached by image content.

    clip_zero_shot is called once per taxonomy for the SAME image; SigLIP image
    encoding (~0.14s) × 6 taxonomies was ~1s/product. Cache by content hash.
    """
    import torch
    key = hash(image.tobytes())
    hit = _clip_img_feat_cache.get(key)
    if hit is not None:
        return hit
    device = next(model.parameters()).device
    inputs = {
        k: (v.to(device=device, dtype=next(model.parameters()).dtype) if v.is_floating_point() else v.to(device))
        for k, v in processor(images=image, return_tensors="pt").items()
    }
    with torch.no_grad():
        feats = model.get_image_features(**inputs)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    if len(_clip_img_feat_cache) > 2000:
        _clip_img_feat_cache.clear()
    _clip_img_feat_cache[key] = feats
    return feats


def clip_zero_shot(
    image: Image.Image,
    candidate_labels: list,
) -> Dict[str, float]:
    """Zero-shot classification, return {label: score} dict (softmax over cosine).

    Works for CLIP and SigLIP: both expose get_text_features/get_image_features.
    CLIP scales cosine by its learned logit_scale (identical to the old
    logits_per_image path); SigLIP has no logit_scale so a fixed temperature is
    used — the scores stay a comparable relative ranking.
    """
    import torch
    import torch.nn.functional as F

    model, processor = get_clip()
    device = next(model.parameters()).device

    text_feats = _clip_text_features(model, processor, candidate_labels)
    image_feats = _clip_image_features(model, processor, image)
    logits = image_feats @ text_feats.T
    scale = getattr(model, "logit_scale", None)
    logits = logits * (scale.exp() if scale is not None else 100.0)
    probs = F.softmax(logits, dim=-1).squeeze(0)

    return {label: float(probs[i]) for i, label in enumerate(candidate_labels)}


# ─── DINOv2 + CLIP Combined Classification ────────────────────────────────────

_dinov2_model: Optional[Any] = None
_dinov2_processor: Optional[Any] = None
_dinov2_device: str = "cpu"


def _get_dinov2():
    """Lazy-load DINOv2-small (22M params, ~100MB) for visual feature extraction."""
    global _dinov2_model, _dinov2_processor, _dinov2_device
    if _dinov2_model is not None:
        return _dinov2_model, _dinov2_processor, _dinov2_device

    import torch
    from transformers import AutoModel, AutoImageProcessor

    model_name = "facebook/dinov2-small"
    _dinov2_device = "mps" if torch.backends.mps.is_available() else (
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    print(f"[INFO] Loading DINOv2 model: {model_name}", file=sys.stderr)
    _dinov2_processor = AutoImageProcessor.from_pretrained(model_name, use_fast=True)
    _dinov2_model = AutoModel.from_pretrained(
        model_name, torch_dtype=(torch.bfloat16 if _dinov2_device == "cuda" else torch.float32)
    )
    _dinov2_model.to(_dinov2_device)
    _dinov2_model.eval()
    print(f"[INFO] DINOv2 model loaded on {_dinov2_device}", file=sys.stderr)
    return _dinov2_model, _dinov2_processor, _dinov2_device


def dinov2_extract_features(image_sources: List[str]) -> Optional[Any]:
    """Extract DINOv2 visual features from the first valid product image.
    Returns a normalized feature tensor (1 x 384) or None.
    """
    if not image_sources:
        return None
    try:
        import torch
        model, processor, device = _get_dinov2()
        for source in image_sources[:3]:
            img = load_image(str(source))
            if img is None:
                continue
            inputs = processor(images=img, return_tensors="pt")
            inputs = {k: (v.to(device=device, dtype=next(dinov2_model.parameters()).dtype) if v.is_floating_point() else v.to(device)) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = model(**inputs)
                # Use CLS token as global image feature
                features = outputs.last_hidden_state[:, 0, :]
                features = features / features.norm(dim=-1, keepdim=True)
                return features.cpu().numpy()
        return None
    except Exception as e:
        print(f"[DINOv2] Feature extraction failed: {e}", file=sys.stderr)
        return None


def dinov2_clip_classify(
    image_sources: List[str],
    labels: List[str],
    threshold: float = 0.10,
) -> List[dict]:
    """Combined DINOv2 + CLIP classification for better accuracy.

    Strategy:
    1. CLIP zero-shot for broad semantic matching (text-to-image)
    2. DINOv2 visual features for fine-grained texture/pattern/material detection
    3. Combine scores: 70% CLIP + 30% DINOv2 similarity

    Returns list of {label, confidence} sorted by combined confidence.
    """
    if not image_sources:
        return []
    try:
        import torch
        import numpy as np

        # Get both models
        clip_model, clip_processor = get_clip()
        dinov2_model, dinov2_processor, dinov2_device = _get_dinov2()

        clip_scores: dict[str, float] = {}
        dinov2_features_list: list = []

        for source in image_sources[:3]:
            img = load_image(str(source))
            if img is None:
                continue

            # CLIP zero-shot
            clip_probs = clip_zero_shot(img, labels)

            # DINOv2 features
            inputs = dinov2_processor(images=img, return_tensors="pt")
            inputs = {k: v.to(dinov2_device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = dinov2_model(**inputs)
                feat = outputs.last_hidden_state[:, 0, :]
                feat = feat / feat.norm(dim=-1, keepdim=True)
                dinov2_features_list.append(feat.cpu().numpy())

            for lbl in labels:
                clip_scores[lbl] = clip_scores.get(lbl, 0.0) + clip_probs.get(lbl, 0.0)

        loaded = len(dinov2_features_list)
        if loaded == 0:
            return []

        # Average CLIP scores
        for lbl in labels:
            clip_scores[lbl] /= loaded

        # If DINOv2 features available, compute visual similarity
        # by projecting features through CLIP text embeddings for each label
        if dinov2_features_list:
            # Average DINOv2 features across images
            avg_dinov2 = np.mean(dinov2_features_list, axis=0)  # 1 x 384
            avg_dinov2 = avg_dinov2 / (np.linalg.norm(avg_dinov2) + 1e-8)

            # Get CLIP text features for each label
            try:
                _is_siglip = type(clip_model).__name__.startswith("Siglip")
                _tkw = {"text": labels, "return_tensors": "pt", "truncation": True,
                        "padding": "max_length" if _is_siglip else True}
                if _is_siglip:
                    _tkw["max_length"] = 64
                text_inputs = clip_processor(**_tkw)
                text_inputs = {k: v.to("cpu") for k, v in text_inputs.items()}
                with torch.no_grad():
                    text_features = clip_model.get_text_features(**text_inputs)
                    text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                    text_features = text_features.cpu().numpy()

                # DINOv2 features are 384-dim, CLIP text features are 512-dim
                # Use CLIP text features directly — they're more aligned for classification
                # DINOv2 provides fine-grained visual cues, CLIP provides semantic matching
                # For now: use CLIP as primary (80%) + DINOv2 boost for visual consistency

                combined: dict[str, float] = {}
                for i, lbl in enumerate(labels):
                    # CLIP text-image similarity (from zero-shot)
                    clip_score = clip_scores[lbl]
                    # DINOv2 visual quality: how "clean/recognizable" is the image?
                    # Use DINOv2 feature magnitude as a quality signal
                    dino_quality = float(np.mean(np.abs(avg_dinov2)))
                    # Combined: CLIP provides classification, DINOv2 provides confidence boost
                    combined[lbl] = clip_score * 0.75 + dino_quality * 0.25 * clip_score

            except Exception:
                combined = clip_scores

        else:
            combined = clip_scores

        # Sort and filter
        results = []
        for lbl in labels:
            score = combined.get(lbl, clip_scores.get(lbl, 0.0))
            if score >= threshold:
                results.append({"label": lbl, "confidence": round(float(score), 3)})
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results[:5]

    except Exception as e:
        print(f"[DINOv2+CLIP] Classification failed: {e}", file=sys.stderr)
        return []


# ─── Rule 5: Markings detection (first image only) ───────────────────────────

def check_rule5(image_url: str) -> dict:
    """Check first image for watermark/logo/text overlay using split sub-checks."""
    img = load_image(image_url)
    if img is None:
        return {
            "hasMarkings": None,
            "watermarkScore": None,
            "cleanScore": None,
            "flagged": None,
            "details": [],
            "error": "Image failed to load",
        }

    try:
        details = []
        any_flagged = False
        import time as _t5
        _c0 = _t5.time()

        for check_name, positive_prompt, negative_prompt, threshold in RULE5_CHECKS:
            scores = clip_zero_shot(img, [positive_prompt, negative_prompt])
            positive_score = scores[positive_prompt]
            negative_score = scores[negative_prompt]
            total = positive_score + negative_score
            ratio = positive_score / total if total > 0 else 0.0

            # Apply the global 65% floor on top of the sub-check's own threshold.
            effective_threshold = max(threshold, OVERLAY_FLAG_THRESHOLD)
            sub_flagged = positive_score > effective_threshold and ratio >= RULE5_CONFIDENCE_MARGIN

            details.append({
                "check": check_name,
                "positiveScore": round(positive_score, 4),
                "negativeScore": round(negative_score, 4),
                "ratio": round(ratio, 4),
                "threshold": round(effective_threshold, 2),
                "flagged": sub_flagged,
            })

            if sub_flagged:
                any_flagged = True

        # ── CJK / Chinese text overlay detection (OCR-based) ──────────
        # Chinese overlays are invisible to CLIP's English prompts but EasyOCR
        # (now loaded with ch_sim) can detect them directly.
        try:
            _c1 = _t5.time()
            ocr_text = extract_image_text(img)
            print(f"[TIMING] rule5.clip={_c1-_c0:.2f}s ocr={_t5.time()-_c1:.2f}s", file=sys.stderr)
            # CJK Unified Ideographs: U+4E00–U+9FFF
            cjk_chars = [ch for ch in ocr_text if "\u4e00" <= ch <= "\u9fff"]
            cjk_ratio = len(cjk_chars) / max(len(ocr_text.strip()), 1)
            # Overlays must meet the same 65% confidence floor as the CLIP
            # overlay checks — a handful of Chinese characters on legitimate
            # packaging must not flag the image.
            cjk_flagged = len(cjk_chars) >= 3 and cjk_ratio >= OVERLAY_FLAG_THRESHOLD
            details.append({
                "check": "cjk_overlay",
                "positiveScore": round(cjk_ratio, 4),
                "negativeScore": 0.0,
                "ratio": round(cjk_ratio, 4),
                "threshold": round(OVERLAY_FLAG_THRESHOLD, 2),
                "flagged": cjk_flagged,
            })
            if cjk_flagged:
                any_flagged = True
        except Exception:
            pass  # OCR failure should not break Rule 5

        return {
            "hasMarkings": any_flagged,
            "watermarkScore": None,
            "cleanScore": None,
            "flagged": any_flagged,
            "details": details,
        }
    except Exception as e:
        return {
            "hasMarkings": None,
            "watermarkScore": None,
            "cleanScore": None,
            "flagged": None,
            "details": [],
            "error": str(e),
        }


# ─── Rule 6: Content moderation (all images) ─────────────────────────────────

def check_rule6(image_url: str) -> dict:
    """Check a single image for prohibited content."""
    img = load_image(image_url)
    if img is None:
        return {
            "url": image_url,
            "flagged": None,
            "scores": None,
            "highestCategory": None,
            "error": "Image failed to load",
        }

    try:
        scores = clip_zero_shot(img, RULE6_PROMPTS)

        bad_prompts = RULE6_PROMPTS[:-1]  # all except the last (clean) prompt
        clean_prompt = RULE6_PROMPTS[-1]
        clean_score = scores[clean_prompt]

        max_bad_score = max(scores[p] for p in bad_prompts)
        max_bad_label = max(bad_prompts, key=lambda p: scores[p])

        flagged = (
            max_bad_score > clean_score * RULE6_CONFIDENCE_MARGIN
            and max_bad_score >= RULE6_MIN_BAD_SCORE
        )

        return {
            "url": image_url,
            "flagged": flagged,
            "scores": {p: round(scores[p], 4) for p in RULE6_PROMPTS},
            "highestCategory": max_bad_label if flagged else "clean",
        }
    except Exception as e:
        return {
            "url": image_url,
            "flagged": None,
            "scores": None,
            "highestCategory": None,
            "error": str(e),
        }


# ─── Batch processing ────────────────────────────────────────────────────────

# ─── Rule 7: Operations/marketing claims on images (OCR-based) ──────────────
# CLIP zero-shot cannot read text, so it false-flagged product branding. The
# reliable approach is OCR: extract the actual text rendered on the image, then
# run the same intelligent claims detection (regex + MiniLM semantic) used for
# titles and descriptions.

_ocr_reader = None
_ocr_lock = None
# EasyOCR/PaddleOCR readers are NOT reentrant — concurrent inference from the
# analysis server's worker threads deadlocks. Serialize the actual OCR call.
import threading as _threading
_OCR_INFER_LOCK = _threading.Lock()


def get_ocr_reader():
    global _ocr_reader, _ocr_lock
    if _ocr_reader is not None:
        return _ocr_reader
    if _ocr_lock is None:
        import threading
        _ocr_lock = threading.Lock()
    with _ocr_lock:
        if _ocr_reader is None:
            # Prefer EasyOCR on the GPU (torch/CUDA): ~100x faster than PaddleOCR
            # CPU (0.06s vs ~3-8s per image), and the Paddle GPU build is blocked
            # by a Windows Application Control policy on this host. PaddleOCR
            # (lang="ch") is the fallback. Override with OCR_BACKEND=paddle.
            pref = (os.environ.get("OCR_BACKEND") or "easyocr").lower()
            order = ["paddle", "easyocr"] if pref == "paddle" else ["easyocr", "paddle"]
            for backend in order:
                try:
                    if backend == "easyocr":
                        import easyocr
                        import torch as _torch
                        gpu = _torch.cuda.is_available()
                        print(f"[INFO] Loading EasyOCR (gpu={gpu})...", file=sys.stderr)
                        t0 = time.time()
                        reader = easyocr.Reader(["en", "ch_sim"], gpu=gpu, verbose=False)
                        print(f"[INFO] EasyOCR ready in {time.time() - t0:.1f}s", file=sys.stderr)
                        _ocr_reader = ("easyocr", reader)
                        break
                    os.environ.setdefault("FLAGS_use_mkldnn", "0")
                    from paddleocr import PaddleOCR
                    print("[INFO] Loading PaddleOCR (en+ch)...", file=sys.stderr)
                    t0 = time.time()
                    reader = None
                    last_err = None
                    for _kwargs in (
                        {"lang": "ch", "enable_mkldnn": False,
                         "use_doc_orientation_classify": False,
                         "use_doc_unwarping": False,
                         "use_textline_orientation": False},
                        {"lang": "ch", "enable_mkldnn": False},
                        {"lang": "ch"},
                    ):
                        try:
                            reader = PaddleOCR(**_kwargs)
                            break
                        except Exception as e:
                            last_err = e
                    if reader is None:
                        raise last_err or RuntimeError("PaddleOCR init failed")
                    _ocr_reader = ("paddle", reader)
                    print(f"[INFO] PaddleOCR ready in {time.time() - t0:.1f}s", file=sys.stderr)
                    break
                except Exception as e:
                    print(f"[INFO] {backend} unavailable ({str(e)[:80]})", file=sys.stderr)
                    continue
    return _ocr_reader


_ocr_text_cache: Dict[int, str] = {}
_OCR_CACHE_MAX = 400


def extract_image_text(img: Image.Image) -> str:
    """Run OCR on a PIL image and return the detected text grouped by visual
    line. Words close in vertical position are grouped into one line and lines
    are joined with newlines, so sentence/claim-splitting downstream can treat
    each text block independently (merging everything into one string hides
    that structure and suppresses detection).

    Results are cached by image content. Rule 5 (CJK overlay) and Rule 7 (ops
    claims) both OCR the SAME first image; without this cache EasyOCR ran twice
    per product (~4s wasted each), which was the dominant cost of the whole
    image-analysis step.
    """
    import numpy as np

    key = hash(img.tobytes())
    cached = _ocr_text_cache.get(key)
    if cached is not None:
        return cached

    backend, reader = get_ocr_reader()
    # Downscale before OCR: PaddleOCR on CPU is pixel-bound (a 1500px hero image
    # took ~30s). Overlay/claim text is large, so ~1000px keeps accuracy while
    # cutting the cost several-fold. Override with OCR_MAX_SIDE.
    _im = img.convert("RGB")
    _maxside = int(os.environ.get("OCR_MAX_SIDE") or "1000")
    if max(_im.size) > _maxside:
        _scale = _maxside / float(max(_im.size))
        _im = _im.resize((max(1, int(_im.size[0] * _scale)), max(1, int(_im.size[1] * _scale))))
    arr = np.array(_im)
    lines: list = []
    _OCR_INFER_LOCK.acquire()
    try:
        if backend == "paddle":
            # PaddleOCR v3: predict() -> list of dicts with "rec_texts";
            # v2: ocr() -> [[box, (text, conf)], ...].
            res = None
            for meth in ("predict", "ocr"):
                fn = getattr(reader, meth, None)
                if fn is None:
                    continue
                try:
                    res = fn(arr)
                    break
                except Exception:
                    continue
            for page in (res or []):
                # PaddleOCR v3 OCRResult is dict-like (supports .get/.keys).
                texts = None
                try:
                    texts = page.get("rec_texts") if hasattr(page, "get") else None
                except Exception:
                    texts = None
                if texts:
                    for t in texts:
                        if t:
                            lines.append(str(t).strip())
                    continue
                if isinstance(page, (list, tuple)):
                    for line in page:
                        try:
                            txt = line[1][0]
                        except Exception:
                            txt = None
                        if txt:
                            lines.append(str(txt).strip())
        else:
            for line in reader.readtext(arr, detail=0):
                if line:
                    lines.append(str(line).strip())
    except Exception as e:
        print(f"[WARN] OCR failed: {e}", file=sys.stderr)
    finally:
        _OCR_INFER_LOCK.release()

    text = "\n".join(l for l in lines if l)

    if len(_ocr_text_cache) >= _OCR_CACHE_MAX:
        _ocr_text_cache.clear()
    _ocr_text_cache[key] = text
    return text


def check_ops_on_image(image_url: str) -> dict:
    """OCR the image, then detect operations/marketing claims in the extracted
    text using the same regex + MiniLM semantic pipeline as title/description
    checks. Returns flagged=True with the matched claim text when found."""
    img = load_image(image_url)
    if img is None:
        return {"url": image_url, "flagged": None, "error": "Image failed to load"}

    try:
        text = extract_image_text(img)
        if not text or len(text) < 4:
            return {"url": image_url, "flagged": False, "ocrText": text, "claims": []}

        # Lazy import to avoid circular dependency (_hsn_suggest imports from us).
        from _hsn_suggest import detect_op_claims, _CLAIM_CONTEXT

        # Fast regex pass for short claim phrases OCR frequently renders as
        # isolated, capitalized blocks (e.g. "FREE SHIPPING", "COD", "SALE",
        # "50% OFF"). These are too short for the embedding pipeline to catch.
        claims: list = []
        low = (text or "").upper()
        # OCR is noisy on low-res marketplace images ("Weter", "REPEATEO",
        # "M@@IG"). Normalise common confusions and match patterns against BOTH
        # the raw and the normalised text.
        norm = low.replace("0", "O").replace("1", "I").replace("5", "S").replace("|", "I")
        norm = re.sub(r"[^A-Z% ]+", " ", norm)
        norm = re.sub(r"\s{2,}", " ", norm)
        short_claim_patterns = [
            (r"\bFREE\s*(SHIPPING|DELIVERY|RETURN|RETURNS|REPLACEMENT|EXCHANGE|GIFT)\b", "free shipping/delivery/return claim"),
            (r"\b(COD|CASH ON DELIVERY)\b", "COD available claim"),
            (r"\b(?:100%|FULL|MONEY[- ]BACK)\s*GUARANTEE\b", "money-back guarantee claim"),
            (r"\b(?:NO[- ]COST|0%)?\s*EMI\b", "EMI/installment claim"),
            (r"\bSALE\b", "sale claim"),
            (r"\b(?:FLAT\s*)?\d{1,3}\s*%[- ]?OFF\b", "discount/off claim"),
            (r"\bBEST\s*PRICE\b", "best price claim"),
            (r"\bLOWEST\s*PRICE\b", "lowest price claim"),
            (r"\bBUY\s*NOW\b", "buy now claim"),
            (r"\bPAY\s*LATER\b", "pay later claim"),
            (r"\b(?:FREE|FAST|EXPRESS|NEXT[- ]DAY)\s*(?:DELIVERY|SHIPPING|DISPATCH)\b", "delivery claim"),
            (r"\bHASSLE[- ]?FREE\s*(?:RETURN|RETURNS|RETURN POLICY)\b", "hassle-free return claim"),
            (r"\bWARRANTY\b", "warranty claim"),
            (r"\b(?:LIFETIME|NUMBER\s*1|#1|WORLD'?S\s*BEST)\b", "quality claim"),
            (r"\bBEST[\s-]*SELL(?:ER|ING)\b", "best seller claim"),
            (r"\bTOP[\s-]*(?:SELL(?:ER|ING)|RATED)\b", "top rated/seller claim"),
            (r"\bNO\.?\s*1\b", "number 1 claim"),
            (r"\bAMAZON'?S\s*CHOICE\b", "amazon's choice claim"),
            (r"\bMOST\s*POPULAR\b", "most popular claim"),
            (r"\bHIGHLY\s*RATED\b", "highly rated claim"),
            (r"\b(?:MEGA|BIG|HOT|FLASH|CLEARANCE)\s*SALE\b", "sale claim"),
            (r"\b(?:SPECIAL|LIMITED\s*TIME)\s*OFFER\b", "offer claim"),
            (r"\bGREAT\s*DEAL\b", "deal claim"),
            (r"\bHURRY\b", "urgency claim"),
        ]
        # Loose variants tolerant of OCR noise.
        short_claim_patterns += [
            (r"\bFRE?E\s*(SHIPPING|SHIP|DELIVERY|RETURN|REPLACEMENT|EXCHANGE|GIFT)\b", "free shipping/delivery/return claim"),
            (r"\bC\s*[O0]\s*D\b", "COD available claim"),
            (r"\b\d{1,3}\s*%\s*[O0]FF\b", "discount/off claim"),
            (r"\bB[E3]ST\s*PRIC[E3]\b", "best price claim"),
            (r"\bL[O0]W[E3]ST\s*PRIC[E3]\b", "lowest price claim"),
            (r"\bS[AB]L[E3]\b", "sale claim"),
            (r"\bBUY\s*N[O0]W\b", "buy now claim"),
            (r"\bWARRANT[YV]\b", "warranty claim"),
        ]
        claim_texts: list = []
        for pat, label in short_claim_patterns:
            m = re.search(pat, low) or re.search(pat, norm)
            if m:
                # Skip if the matched context is a product descriptor
                if _CLAIM_CONTEXT.search(text):
                    continue
                claims.append(label)
                claim_texts.append(m.group(0).strip())
        if claims:
            return {
                "url": image_url,
                "flagged": True,
                "ocrText": text,
                "claims": list(dict.fromkeys(claims)),
                "claimTexts": list(dict.fromkeys(claim_texts)),
            }

        # Semantic pipeline for longer OCR text. OCR lines are short standalone
        # blocks, not sentences — keep min_len small and lower the min_text_len
        # so short blocks like "Free Shipping COD Available" are still analyzed.
        # OCR text is noisy, so use a lower semantic threshold than titles.
        claims = detect_op_claims(text, min_len=6, min_text_len=6, threshold_override=0.60)

        return {
            "url": image_url,
            "flagged": len(claims) > 0,
            "ocrText": text,
            "claims": claims,
            "claimTexts": claims,
        }
    except Exception as e:
        return {"url": image_url, "flagged": None, "error": str(e)}


def _run_ocr_parallel(image_urls: List[str]) -> List[dict]:
    """Run OCR claim detection over multiple image URLs using a thread pool.
    EasyOCR releases the GIL during inference, so threads give near-parallel
    speedup; the reader is loaded once (guarded by a lock) to avoid duplicate
    model loads."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: Dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(image_urls))) as pool:
        futures = {pool.submit(check_ops_on_image, url): url for url in image_urls}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                results[url] = fut.result()
            except Exception as e:
                results[url] = {"url": url, "flagged": None, "error": str(e)}
    return [results[url] for url in image_urls]


def check_rule1(image_urls: List[str]) -> dict:
    """RULE 1: Image count 4-10."""
    count = len(image_urls)
    if count < 4:
        return {"passed": False, "count": count, "message": f"Only {count} image(s) — minimum 4 required"}
    if count > 10:
        return {"passed": False, "count": count, "message": f"{count} images — maximum 10 allowed"}
    return {"passed": True, "count": count, "message": f"{count} images (within 4-10 range)"}


def check_rule2(image_urls: List[str]) -> dict:
    """RULE 2: Duplicate detection (exact URL + perceptual hash)."""
    # Exact URL dedup
    unique_urls = list(dict.fromkeys(image_urls))
    exact_dupes = len(image_urls) - len(unique_urls)

    # Perceptual hash dedup on loaded images
    loaded = []
    for url in unique_urls[:10]:
        img = load_image(url)
        if img:
            loaded.append((url, img))

    phash_dupes = 0
    if len(loaded) > 1:
        try:
            import imagehash
            hashes = [(url, imagehash.phash(img)) for url, img in loaded]
            seen = set()
            for i, (url_i, h_i) in enumerate(hashes):
                for url_j, h_j in hashes[i+1:]:
                    if h_i - h_j <= 2:  # very similar
                        phash_dupes += 1
                        break
        except ImportError:
            pass

    total_dupes = exact_dupes + phash_dupes
    distinct = len(image_urls) - total_dupes
    # Flag only when there are >=2 duplicate images: 1 duplicate (3 distinct out
    # of 4) is tolerated; >=2 duplicates (<=2 distinct out of 4, or 3 distinct
    # out of >4) is a real problem.
    if total_dupes >= 2:
        return {"passed": False, "distinct": distinct, "total": len(image_urls),
                "message": f"{total_dupes} duplicate(s) detected — {distinct} distinct out of {len(image_urls)}"}
    return {"passed": True, "distinct": distinct, "total": len(image_urls),
            "message": f"{len(image_urls)} images ({distinct} distinct, {total_dupes} duplicate) — within tolerance"}


def check_rule3(image_urls: List[str]) -> dict:
    """RULE 3: Resolution >= 350x350 for every image."""
    issues = []
    checked = 0
    for url in image_urls[:10]:
        img = load_image(url)
        if img is None:
            issues.append(f"Failed to load: {url[:60]}")
            continue
        w, h = img.size
        checked += 1
        if w < 350 or h < 350:
            issues.append(f"{w}x{h} below 350x350: {url[:60]}")
    if issues:
        return {"passed": False, "message": "; ".join(issues)}
    return {"passed": True, "message": f"All {checked} loaded image(s) meet 350x350 minimum"}


# Rule 4 removed — white-background check dropped per product-team request.


def _available_memory_gb() -> Optional[float]:
    """Approximate free physical memory, cross-platform.

    psutil (macOS/Windows/Linux) -> /proc/meminfo (Linux) -> vm_stat (macOS) ->
    sysconf. Windows has neither SC_AVPHYS_PAGES nor vm_stat, so without psutil
    it returns None and the caller simply skips the memory-pressure check.
    """
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:
        pass
    # Linux
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1e6
    except Exception:
        pass
    # macOS
    try:
        import re as _re
        import subprocess

        out = subprocess.check_output(["vm_stat"], text=True, timeout=5)
        page_size = 4096
        m = _re.search(r"page size of (\d+)", out)
        if m:
            page_size = int(m.group(1))
        free = inactive = 0
        for line in out.splitlines():
            if line.startswith("Pages free:"):
                free = int(_re.sub(r"\D", "", line.split(":", 1)[1]) or 0)
            elif line.startswith("Pages inactive:"):
                inactive = int(_re.sub(r"\D", "", line.split(":", 1)[1]) or 0)
        if free or inactive:
            return (free + inactive) * page_size / 1e9
    except Exception:
        pass
    # Fallback: sysconf where supported (Linux).
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
    except Exception:
        return None


def _maybe_release_memory(threshold_gb: float = 1.5) -> bool:
    """Drop caches under memory pressure so long runs degrade gracefully.

    When free memory gets low the OS starts paging, which turns image
    analysis from ~1s/product into tens of seconds. Releasing our caches
    (they only cost a re-download) keeps the working set small.
    """
    avail = _available_memory_gb()
    if avail is None or avail >= threshold_gb:
        return False
    _image_pil_cache.clear()
    _image_bytes_cache.clear()
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    return True


def process_batch(products: List[dict], use_qwen_verify: bool = True) -> dict:
    """Process multiple products."""
    _maybe_release_memory()
    results = []

    for i, product in enumerate(products):
        sku = product.get("sku", f"unknown-{i}")
        first_image = product.get("firstImageUrl", "")
        all_images = product.get("allImageUrls", [])

        out = {"sku": sku}

        import time as _time
        _tt = [_time.time()]

        def _tm(lbl):
            print(f"[TIMING] [{sku}] {lbl}: {_time.time() - _tt[0]:.2f}s", file=sys.stderr)
            _tt[0] = _time.time()

        # Rule 1: Image count
        print(f"[INFO] [{sku}] Rule 1: checking image count...", file=sys.stderr)
        out["rule1"] = check_rule1(all_images)
        _tm("rule1")

        # Rule 2: Duplicates
        print(f"[INFO] [{sku}] Rule 2: checking duplicates...", file=sys.stderr)
        out["rule2"] = check_rule2(all_images)
        _tm("rule2")

        # Rule 3: Resolution
        print(f"[INFO] [{sku}] Rule 3: checking resolution...", file=sys.stderr)
        out["rule3"] = check_rule3(all_images)
        _tm("rule3")

        # Rule 5 removed (markings / separate promotional-overlay check). The
        # promotional-overlay signal is folded into Rule 7 below.
        _tm("rule5")

        # Rule 6: All images
        if all_images:
            print(f"[INFO] [{sku}] Rule 6: checking {len(all_images)} image(s)...", file=sys.stderr)
            image_results = [check_rule6(url) for url in all_images]
            # Qwen second-pass verification for content-moderation false positives
            if use_qwen_verify:
                for r in image_results:
                    if r.get("flagged"):
                        img = load_image(r["url"])
                        if img is not None:
                            qwen_verdict = verify_rule_with_qwen(img, "content")
                            if qwen_verdict is False:
                                r["flagged"] = False
                                r["qwenVerified"] = "cleared"
                            elif qwen_verdict is True:
                                r["qwenVerified"] = "confirmed"
                            else:
                                r["qwenVerified"] = "unsure"
            out["rule6"] = {
                "images": image_results,
                "anyFlagged": any(r.get("flagged") for r in image_results if r.get("flagged") is not None),
            }
        else:
            out["rule6"] = {"images": [], "anyFlagged": False}
        _tm("rule6")

        # Rule 7: OCR-based ops/marketing claim detection on the hero (first)
        # image only. EasyOCR runs on CPU (~1.5-2.5s/image), so for large
        # catalogues (1000 products) we OCR only the first image to keep
        # validation tractable. The regex/semantic detection is broad enough to
        # catch "BEST SELLER", "FREE SHIPPING", "COD", etc. on the hero image.
        # Rule 7: OCR-based ops/marketing claim detection on the first few images
        # (claims often live on images 2-4, not just the hero).
        ocr_urls = [u for u in (all_images or []) if u][:4] or ([first_image] if first_image else [])
        r7_results = _run_ocr_parallel(ocr_urls) if ocr_urls else []
        # Flag ONLY on actual ops-claim TEXT found by OCR. A bare promotional
        # overlay (any added text) is not itself an ops claim, so it never flags.
        out["rule7"] = {
            "images": r7_results,
            "anyFlagged": any(r.get("flagged") for r in r7_results),
            "flaggedImages": [r["url"] for r in r7_results if r.get("flagged")],
        }
        _tm("rule7")

        results.append(out)

    return {"results": results}


# ─── CLI entry ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: _clip_verify.py <input.json> | --first-image <url> | --content <url>"}))
        sys.exit(1)

    if sys.argv[1] == "--first-image" and len(sys.argv) >= 3:
        result = check_rule5(sys.argv[2])
        print(json.dumps({"rule5": result}))
        return

    if sys.argv[1] == "--content" and len(sys.argv) >= 3:
        result = check_rule6(sys.argv[2])
        print(json.dumps({"rule6": result}))
        return

    # Batch mode: read input JSON file
    input_path = sys.argv[1]
    try:
        with open(input_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(json.dumps({"error": f"Failed to read input: {e}"}))
        sys.exit(1)

    products = data.get("products", [])
    use_qwen_verify = data.get("useQwenVerify", True)
    if not products:
        print(json.dumps({"results": []}))
        return

    try:
        result = process_batch(products, use_qwen_verify=use_qwen_verify)
        print(json.dumps(result))
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
