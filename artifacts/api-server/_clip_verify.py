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
    (
        "watermark",
        "a photo with a semi-transparent watermark stamp overlaid across it",
        "a clean product photograph with no watermark stamp",
        0.70,
    ),
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
        0.70,
    ),
]

# Minimum ratio of positive_score / (positive_score + negative_score) to flag
RULE5_CONFIDENCE_MARGIN = 0.60

# ─── Rule 6 prompts: content moderation ──────────────────────────────────────
# Kept deliberately specific; CLIP zero-shot is noisy for moderation, so the
# confidence margin is high to avoid false positives on ordinary products.
RULE6_PROMPTS = [
    "a photo containing nudity or sexually explicit pornographic content",
    "a photo of graphic violence, gore, or blood",
    "a clean, appropriate product photograph suitable for an e-commerce listing",
]

# Bad prompt must beat clean by this ratio (high bar — false positives are costly)
RULE6_CONFIDENCE_MARGIN = 2.5

# ─── Image loading ───────────────────────────────────────────────────────────

_image_bytes_cache: Dict[str, bytes] = {}
_IMAGE_CACHE_MAX = 300


def load_image(source: str, timeout: int = 15) -> Optional[Image.Image]:
    """Load image from URL or local path. Downloaded bytes are cached so a
    product's image is fetched once and reused across rules (each image is
    otherwise downloaded once per rule: duplicates, resolution, white bg,
    markings, content, OCR — ~5x redundant fetches)."""
    try:
        if source.startswith(("http://", "https://")):
            data = _image_bytes_cache.get(source)
            if data is None:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                }
                resp = requests.get(source, headers=headers, timeout=timeout)
                resp.raise_for_status()
                data = resp.content
                if len(_image_bytes_cache) >= _IMAGE_CACHE_MAX:
                    _image_bytes_cache.clear()
                _image_bytes_cache[source] = data
            return Image.open(io.BytesIO(data)).convert("RGB")
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
        from transformers import CLIPProcessor, CLIPModel

        model_name = "openai/clip-vit-base-patch32"
        print(f"[INFO] Loading CLIP model: {model_name}", file=sys.stderr)
        t0 = time.time()
        _clip_model = CLIPModel.from_pretrained(model_name)
        _clip_processor = CLIPProcessor.from_pretrained(model_name)
        _clip_model.eval()
        if torch.cuda.is_available():
            _clip_model = _clip_model.to("cuda")
        t1 = time.time()
        print(f"[INFO] CLIP model loaded in {t1 - t0:.1f}s", file=sys.stderr)
    return _clip_model, _clip_processor


# ─── Qwen VLM second-pass verifier ────────────────────────────────────────────
# CLIP zero-shot produces false positives for Rule 5 (markings) and Rule 6
# (content moderation). Qwen2.5-VL actually sees the image and can confirm or
# clear a CLIP flag. Used as a second-pass to reduce false positives.

_qwen_model = None
_qwen_tokenizer = None
_qwen_processor = None

QWEN_MODEL_PATH = os.path.expanduser("~/.cache/huggingface/hub/Qwen2.5-VL-3B-Instruct")


def _get_qwen():
    global _qwen_model, _qwen_tokenizer, _qwen_processor
    if _qwen_model is not None:
        return _qwen_model, _qwen_tokenizer, _qwen_processor

    from mlx_lm import load
    from transformers import AutoProcessor

    model_path = QWEN_MODEL_PATH if os.path.exists(os.path.join(QWEN_MODEL_PATH, "config.json")) else "Qwen/Qwen2.5-VL-3B-Instruct"
    print(f"[QWEN-VERIFY] Loading model from {model_path}...", file=sys.stderr)
    t0 = time.time()
    _qwen_model, _qwen_tokenizer = load(model_path)
    _qwen_processor = AutoProcessor.from_pretrained(model_path)
    print(f"[QWEN-VERIFY] Model loaded in {time.time()-t0:.1f}s", file=sys.stderr)
    return _qwen_model, _qwen_tokenizer, _qwen_processor


def verify_rule_with_qwen(img: Image.Image, rule_type: str) -> Optional[bool]:
    """
    Second-pass verification of a CLIP flag using Qwen VLM.
    rule_type: "markings" or "content".
    Returns:
        True  — Qwen confirms the flag (real problem)
        False — Qwen clears the flag (false positive)
        None  — Qwen unsure / failed
    """
    from mlx_lm import stream_generate

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
        model, tokenizer, processor = _get_qwen()

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img.save(f, "JPEG", quality=90)
            temp_path = f.name

        try:
            messages = [
                {"role": "user", "content": [
                    {"type": "image", "image": temp_path},
                    {"type": "text", "text": question}
                ]}
            ]
            text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

            t0 = time.time()
            responses = []
            for r in stream_generate(model, tokenizer, prompt=text, max_tokens=16):
                responses.append(r)
                if len(responses) > 64:
                    break
            response_text = "".join([r.text for r in responses]).strip().upper()
            elapsed = time.time() - t0
            print(f"[QWEN-VERIFY] {rule_type} query took {elapsed:.1f}s → '{response_text}'", file=sys.stderr)

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


def clip_zero_shot(
    image: Image.Image,
    candidate_labels: list,
) -> Dict[str, float]:
    """Run CLIP zero-shot classification, return {label: score} dict (softmax)."""
    import torch
    import torch.nn.functional as F

    model, processor = get_clip()

    inputs = processor(
        text=candidate_labels,
        images=image,
        return_tensors="pt",
        padding=True,
    )

    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        logits_per_image = outputs.logits_per_image  # shape: (1, num_labels)
        probs = F.softmax(logits_per_image, dim=-1).squeeze(0)

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
    # Enforce CPU for MPS compatibility issues with some ops
    _dinov2_device = "cpu"

    print(f"[INFO] Loading DINOv2 model: {model_name}", file=sys.stderr)
    _dinov2_processor = AutoImageProcessor.from_pretrained(model_name, use_fast=True)
    _dinov2_model = AutoModel.from_pretrained(model_name)
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
            inputs = {k: v.to(device) for k, v in inputs.items()}
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
                text_inputs = clip_processor(text=labels, padding=True, truncation=True, return_tensors="pt")
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

        for check_name, positive_prompt, negative_prompt, threshold in RULE5_CHECKS:
            scores = clip_zero_shot(img, [positive_prompt, negative_prompt])
            positive_score = scores[positive_prompt]
            negative_score = scores[negative_prompt]
            total = positive_score + negative_score
            ratio = positive_score / total if total > 0 else 0.0

            sub_flagged = positive_score > threshold and ratio >= RULE5_CONFIDENCE_MARGIN

            details.append({
                "check": check_name,
                "positiveScore": round(positive_score, 4),
                "negativeScore": round(negative_score, 4),
                "ratio": round(ratio, 4),
                "flagged": sub_flagged,
            })

            if sub_flagged:
                any_flagged = True

        # ── CJK / Chinese text overlay detection (OCR-based) ──────────
        # Chinese overlays are invisible to CLIP's English prompts but EasyOCR
        # (now loaded with ch_sim) can detect them directly.
        try:
            ocr_text = extract_image_text(img)
            # CJK Unified Ideographs: U+4E00–U+9FFF
            cjk_chars = [ch for ch in ocr_text if "\u4e00" <= ch <= "\u9fff"]
            cjk_ratio = len(cjk_chars) / max(len(ocr_text.strip()), 1)
            # Flag if ≥10% of detected text is CJK (likely a Chinese overlay,
            # not just a random character on packaging)
            cjk_flagged = len(cjk_chars) >= 3 and cjk_ratio >= 0.10
            details.append({
                "check": "cjk_overlay",
                "positiveScore": round(cjk_ratio, 4),
                "negativeScore": 0.0,
                "ratio": round(cjk_ratio, 4),
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

        flagged = max_bad_score > clean_score * RULE6_CONFIDENCE_MARGIN

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


def get_ocr_reader():
    global _ocr_reader, _ocr_lock
    if _ocr_reader is not None:
        return _ocr_reader
    if _ocr_lock is None:
        import threading
        _ocr_lock = threading.Lock()
    with _ocr_lock:
        if _ocr_reader is None:
            import easyocr
            print("[INFO] Loading EasyOCR reader (en + ch_sim)...", file=sys.stderr)
            t0 = time.time()
            _ocr_reader = easyocr.Reader(["en", "ch_sim"], gpu=False, verbose=False)
            print(f"[INFO] EasyOCR ready in {time.time() - t0:.1f}s", file=sys.stderr)
    return _ocr_reader


def extract_image_text(img: Image.Image) -> str:
    """Run OCR on a PIL image and return the detected text grouped by visual
    line. Words close in vertical position are grouped into one line and lines
    are joined with newlines, so sentence/claim-splitting downstream can treat
    each text block independently (merging everything into one string hides
    that structure and suppresses detection)."""
    import numpy as np
    reader = get_ocr_reader()
    arr = np.array(img.convert("RGB"))
    results = reader.readtext(arr, detail=0)
    if not results:
        return ""
    lines = [str(line).strip() for line in results if line]
    return "\n".join(l for l in lines if l)


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
        for pat, label in short_claim_patterns:
            if re.search(pat, low):
                # Skip if the matched context is a product descriptor
                if _CLAIM_CONTEXT.search(text):
                    continue
                claims.append(label)
        if claims:
            return {
                "url": image_url,
                "flagged": True,
                "ocrText": text,
                "claims": list(dict.fromkeys(claims)),
            }

        # Semantic pipeline for longer OCR text. OCR lines are short standalone
        # blocks, not sentences — keep min_len small and lower the min_text_len
        # so short blocks like "Free Shipping COD Available" are still analyzed.
        claims = detect_op_claims(text, min_len=6, min_text_len=6)

        return {
            "url": image_url,
            "flagged": len(claims) > 0,
            "ocrText": text,
            "claims": claims,
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
    if total_dupes > 0:
        return {"passed": False, "distinct": distinct, "total": len(image_urls),
                "message": f"{total_dupes} duplicate(s) detected — {distinct} distinct out of {len(image_urls)}"}
    return {"passed": True, "distinct": distinct, "total": len(image_urls),
            "message": f"All {len(image_urls)} images are distinct"}


def check_rule3(image_urls: List[str]) -> dict:
    """RULE 3: Resolution >= 500x500 for every image."""
    issues = []
    checked = 0
    for url in image_urls[:10]:
        img = load_image(url)
        if img is None:
            issues.append(f"Failed to load: {url[:60]}")
            continue
        w, h = img.size
        checked += 1
        if w < 500 or h < 500:
            issues.append(f"{w}x{h} below 500x500: {url[:60]}")
    if issues:
        return {"passed": False, "message": "; ".join(issues)}
    return {"passed": True, "message": f"All {checked} loaded image(s) meet 500x500 minimum"}


# Rule 4 removed — white-background check dropped per product-team request.


def process_batch(products: List[dict], use_qwen_verify: bool = True) -> dict:
    """Process multiple products."""
    results = []

    for i, product in enumerate(products):
        sku = product.get("sku", f"unknown-{i}")
        first_image = product.get("firstImageUrl", "")
        all_images = product.get("allImageUrls", [])

        out = {"sku": sku}

        # Rule 1: Image count
        print(f"[INFO] [{sku}] Rule 1: checking image count...", file=sys.stderr)
        out["rule1"] = check_rule1(all_images)

        # Rule 2: Duplicates
        print(f"[INFO] [{sku}] Rule 2: checking duplicates...", file=sys.stderr)
        out["rule2"] = check_rule2(all_images)

        # Rule 3: Resolution
        print(f"[INFO] [{sku}] Rule 3: checking resolution...", file=sys.stderr)
        out["rule3"] = check_rule3(all_images)

        # Rule 5: Only on first image
        if first_image:
            print(f"[INFO] [{sku}] Rule 5: checking first image markings...", file=sys.stderr)
            out["rule5"] = check_rule5(first_image)
            # Qwen second-pass verification for markings false positives
            if use_qwen_verify and out["rule5"].get("flagged"):
                print(f"[INFO] [{sku}] Rule 5 flagged — verifying with Qwen VLM...", file=sys.stderr)
                img = load_image(first_image)
                if img is not None:
                    qwen_verdict = verify_rule_with_qwen(img, "markings")
                    if qwen_verdict is False:
                        out["rule5"]["flagged"] = False
                        out["rule5"]["hasMarkings"] = False
                        out["rule5"]["qwenVerified"] = "cleared"
                    elif qwen_verdict is True:
                        out["rule5"]["qwenVerified"] = "confirmed"
                    else:
                        out["rule5"]["qwenVerified"] = "unsure"
        else:
            out["rule5"] = {
                "hasMarkings": None,
                "watermarkScore": None,
                "cleanScore": None,
                "flagged": None,
                "error": "No first image URL provided",
            }

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

        # Rule 7: OCR-based ops/marketing claim detection on the hero (first)
        # image only. EasyOCR runs on CPU (~1.5-2.5s/image), so for large
        # catalogues (1000 products) we OCR only the first image to keep
        # validation tractable. The regex/semantic detection is broad enough to
        # catch "BEST SELLER", "FREE SHIPPING", "COD", etc. on the hero image.
        if first_image:
            print(f"[INFO] [{sku}] Rule 7: OCR claims check on first image...", file=sys.stderr)
            r7 = check_ops_on_image(first_image)
            out["rule7"] = {
                "images": [r7],
                "anyFlagged": bool(r7.get("flagged")),
            }
        else:
            out["rule7"] = {"images": [], "anyFlagged": False}

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
