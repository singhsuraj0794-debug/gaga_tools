"""
Marqo Ecommerce Embeddings — Category Classifier
==================================================
Uses Marqo via OpenCLIP. Path-based labels proven 82-97% accurate.
Multi-image averaging for better results.
"""

import sys, time
from typing import List, Tuple

_MODEL_NAME = "hf-hub:Marqo/marqo-ecommerce-embeddings-B"
_model = None
_preprocess = None
_tokenizer = None
_device = "cpu"


def _pick_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _ensure_model():
    global _model, _preprocess, _tokenizer, _device
    if _model is not None:
        return True
    try:
        import open_clip
        print(f"[MARQO] Loading {_MODEL_NAME}...", file=sys.stderr)
        t0 = time.time()
        _model, _, _preprocess = open_clip.create_model_and_transforms(_MODEL_NAME)
        _tokenizer = open_clip.get_tokenizer(_MODEL_NAME)
        _device = _pick_device()
        _model = _model.to(_device)
        _model.eval()
        print(f"[MARQO] Loaded in {time.time() - t0:.0f}s on {_device}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[MARQO] Load failed: {e}", file=sys.stderr)
        return False


def classify(
    image_sources: List[str],
    candidate_labels: List[str],
) -> List[Tuple[str, float]]:
    """
    Zero-shot: images -> category labels (path format: "Luggage > Backpacks").
    Uses all available images, averaging their features for better accuracy.
    Returns top-5 (label, confidence).
    """
    if not _ensure_model() or not candidate_labels:
        return []

    import torch

    # Load all images via the shared cache (avoids re-downloading images that
    # CLIP/DINOv2 already fetched for the same product).
    try:
        from _clip_verify import load_image
    except Exception:
        load_image = None

    img_tensors = []
    for src in image_sources[:5]:
        try:
            if load_image is not None:
                img = load_image(str(src))
                if img is None:
                    continue
            else:
                from PIL import Image
                from io import BytesIO
                import urllib.request
                if src.startswith(("http://", "https://")):
                    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                    img = Image.open(BytesIO(urllib.request.urlopen(req, timeout=15).read())).convert("RGB")
                else:
                    img = Image.open(src).convert("RGB")
            img_tensors.append(_preprocess(img).unsqueeze(0))
        except Exception:
            continue

    if not img_tensors:
        return []

    try:
        img_stack = torch.cat(img_tensors, dim=0).to(_device)
        text_tokens = _tokenizer(candidate_labels).to(_device)

        with torch.no_grad():
            img_feat = _model.encode_image(img_stack)
            img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)
            img_feat = img_feat.mean(dim=0, keepdim=True)

            txt_feat = _model.encode_text(text_tokens)
            txt_feat = txt_feat / txt_feat.norm(dim=-1, keepdim=True)

            scores = (100.0 * img_feat @ txt_feat.T).softmax(dim=-1)[0]

        n = min(5, len(candidate_labels))
        idx = torch.topk(scores.cpu(), n).indices
        return [(candidate_labels[i], float(scores.cpu()[i])) for i in idx]
    except Exception as e:
        print(f"[MARQO] classify failed: {e}", file=sys.stderr)
        return []


def is_loaded() -> bool:
    return _model is not None
