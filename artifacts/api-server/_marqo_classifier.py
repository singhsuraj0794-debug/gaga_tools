"""
Marqo Ecommerce Embeddings — Category Classifier
==================================================
Uses Marqo via OpenCLIP. Path-based labels proven 82-97% accurate.
Multi-image averaging for better results.

Exposes two layers:
 * classify(image_sources, candidate_labels)         — one-shot, used by HSN.
 * encode_images(...) / classify_features(...)       — encode images ONCE and
   score against many candidate label sets (hierarchical category validation).

Label text embeddings are cached in-process, so calling classify repeatedly
with overlapping labels (e.g. per taxonomy level) does not re-encode them.
"""

import hashlib
import os
import sys, time
import threading
from typing import List, Optional, Tuple

_MODEL_NAME = "hf-hub:Marqo/marqo-ecommerce-embeddings-B"
_model = None
_preprocess = None
_tokenizer = None
_device = "cpu"
# Model load + label-embedding cache mutation are guarded so a thread pool can
# call encode_images()/classify_features() concurrently. torch/OpenCLIP release
# the GIL during CPU ops, so concurrent encode_image() calls give real speedup;
# the lock only serialises the one-time load and the cache writes.
_model_lock = threading.Lock()
_cache_lock = threading.Lock()

# label text -> normalized embedding tensor (kept on _device)
_label_emb_cache = {}

# Persisted label-embedding cache. Encoding the ~2,400 taxonomy path labels
# costs 90-200s on CPU; caching to disk means this happens ONCE ever, not per
# process (critical for the one-shot CLI path, which is a fresh Python process).
_LABEL_CACHE_PATH = os.environ.get(
    "MARQO_LABEL_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".marqo_label_emb.pt"),
)
_label_disk = None  # lazy-loaded dict {label: tensor(cpu)}


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
    with _model_lock:
        if _model is not None:  # another thread won the race
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


def _load_image(image_src):
    """Load a single image path/URL as an RGB PIL image, or None."""
    try:
        from _clip_verify import load_image
    except Exception:
        load_image = None
    try:
        if load_image is not None:
            # fast=True: 1 quick retry, no exponential backoff. The default
            # session sleeps ~62s on a persistently-502ing image (6 retries,
            # backoff_factor=1.0) — that was the 66-132s batch stall.
            # Short timeout: fail a dead image in seconds, not a minute.
            return load_image(str(image_src), timeout=6, fast=True)
        from PIL import Image
        from io import BytesIO
        import urllib.request
        src = str(image_src)
        if src.startswith(("http://", "https://")):
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            return Image.open(BytesIO(urllib.request.urlopen(req, timeout=6).read())).convert("RGB")
        return Image.open(src).convert("RGB")
    except Exception:
        return None


def encode_images(image_sources: List[str], max_images: int = 5):
    """
    Encode all available images and average their features into a single
    normalized (1, D) tensor. Returns None if no image could be loaded.
    Encoding once lets the caller score against several label sets cheaply.
    """
    if not _ensure_model() or not image_sources:
        return None

    import torch
    from concurrent.futures import ThreadPoolExecutor

    srcs = list(image_sources[:max_images])
    # Download image bytes CONCURRENTLY. Serial downloads were the source of
    # the 66-132s per-product stalls: one unresponsive image host x 5 images
    # x timeout blocks everything (gateway metadata is fast; the byte
    # download happens here, not in product_images()).
    if len(srcs) > 1:
        with ThreadPoolExecutor(max_workers=min(6, len(srcs))) as pool:
            loaded = list(pool.map(_load_image, srcs))
    else:
        loaded = [_load_image(srcs[0])] if srcs else []

    tensors = []
    for img in loaded:
        if img is None:
            continue
        try:
            tensors.append(_preprocess(img).unsqueeze(0))
        except Exception:
            continue
    if not tensors:
        return None

    try:
        stack = torch.cat(tensors, dim=0).to(_device)
        with torch.no_grad():
            feat = _model.encode_image(stack)
            feat = feat / feat.norm(dim=-1, keepdim=True)
            feat = feat.mean(dim=0, keepdim=True)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat
    except Exception as e:
        print(f"[MARQO] encode_images failed: {e}", file=sys.stderr)
        return None


def encode_text_query(text: str):
    """Encode a text query (e.g. the product title) into the same normalized
    (1, D) embedding space as images and labels, so it can be fused with the
    image feature before classification."""
    if not text or not text.strip() or not _ensure_model():
        return None
    import torch
    try:
        with _cache_lock:
            tokens = _tokenizer([text]).to(_device)
            with torch.no_grad():
                feat = _model.encode_text(tokens)
                feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat
    except Exception as e:
        print(f"[MARQO] encode_text_query failed: {e}", file=sys.stderr)
        return None


def _load_disk_cache():
    """Lazily load persisted label embeddings (CPU tensors) into memory."""
    global _label_disk
    if _label_disk is not None:
        return _label_disk
    try:
        import torch
        if os.path.exists(_LABEL_CACHE_PATH):
            _label_disk = torch.load(_LABEL_CACHE_PATH, map_location="cpu")
            print(f"[MARQO] Loaded {len(_label_disk)} cached label embeddings from disk", file=sys.stderr)
        else:
            _label_disk = {}
    except Exception as e:
        print(f"[MARQO] label cache load failed: {e}", file=sys.stderr)
        _label_disk = {}
    return _label_disk


def _save_disk_cache():
    try:
        import torch
        disk = _load_disk_cache()
        torch.save(disk, _LABEL_CACHE_PATH)
        print(f"[MARQO] Saved {len(disk)} label embeddings to disk", file=sys.stderr)
    except Exception as e:
        print(f"[MARQO] label cache save failed: {e}", file=sys.stderr)


def _encode_labels(candidate_labels: List[str]):
    """Return normalized text embeddings for labels, computing only misses.

    Checks the in-process cache, then the on-disk cache, then encodes the
    remainder. New embeddings are written back to disk so a fresh process
    (e.g. the one-shot CLI) never re-encodes the full taxonomy.
    """
    import torch

    with _cache_lock:
        disk = _load_disk_cache()
        for label in candidate_labels:
            if label not in _label_emb_cache and label in disk:
                _label_emb_cache[label] = disk[label]

        missing = [l for l in candidate_labels if l not in _label_emb_cache]
        if missing:
            try:
                tokens = _tokenizer(missing).to(_device)
                with torch.no_grad():
                    feats = _model.encode_text(tokens)
                    feats = feats / feats.norm(dim=-1, keepdim=True)
                for label, vec in zip(missing, feats):
                    vec_cpu = vec.detach().cpu()
                    _label_emb_cache[label] = vec.detach()
                    disk[label] = vec_cpu
                _save_disk_cache()
            except Exception as e:
                print(f"[MARQO] label encoding failed: {e}", file=sys.stderr)
                return None
        return torch.stack([_label_emb_cache[l] for l in candidate_labels])


def classify_features(feature, candidate_labels: List[str], top_k: int = 5) -> List[Tuple[str, float]]:
    """Score a pre-encoded image feature against candidate labels."""
    if feature is None or not candidate_labels or not _ensure_model():
        return []
    import torch

    try:
        txt = _encode_labels(candidate_labels)
        if txt is None:
            return []
        with torch.no_grad():
            scores = (100.0 * feature @ txt.T).softmax(dim=-1)[0]
        n = min(top_k, len(candidate_labels))
        idx = torch.topk(scores.cpu(), n).indices
        return [(candidate_labels[int(i)], float(scores.cpu()[int(i)])) for i in idx]
    except Exception as e:
        print(f"[MARQO] classify_features failed: {e}", file=sys.stderr)
        return []


def classify_scores(feature, candidate_labels: List[str]) -> List[Tuple[str, float]]:
    """Full (label, probability) list for a pre-encoded image feature."""
    if feature is None or not candidate_labels or not _ensure_model():
        return []
    import torch

    try:
        txt = _encode_labels(candidate_labels)
        if txt is None:
            return []
        with torch.no_grad():
            scores = (100.0 * feature @ txt.T).softmax(dim=-1)[0].cpu()
        return [(l, float(scores[i])) for i, l in enumerate(candidate_labels)]
    except Exception as e:
        print(f"[MARQO] classify_scores failed: {e}", file=sys.stderr)
        return []


def classify_scores_text(text: str, candidate_labels: List[str]) -> List[Tuple[str, float]]:
    """Score a text query against candidate labels (title-text tiebreaker)."""
    if not text or not candidate_labels or not _ensure_model():
        return []
    import torch

    try:
        q_tokens = _tokenizer([text]).to(_device)
        with torch.no_grad():
            q_feat = _model.encode_text(q_tokens)
            q_feat = q_feat / q_feat.norm(dim=-1, keepdim=True)
        txt = _encode_labels(candidate_labels)
        if txt is None:
            return []
        with torch.no_grad():
            scores = (100.0 * q_feat @ txt.T).softmax(dim=-1)[0].cpu()
        return [(l, float(scores[i])) for i, l in enumerate(candidate_labels)]
    except Exception as e:
        print(f"[MARQO] classify_scores_text failed: {e}", file=sys.stderr)
        return []


def classify(
    image_sources: List[str],
    candidate_labels: List[str],
) -> List[Tuple[str, float]]:
    """
    Zero-shot: images -> category labels (path format: "Luggage > Backpacks").
    Uses all available images, averaging their features for better accuracy.
    Returns top-5 (label, confidence).
    """
    feature = encode_images(image_sources)
    return classify_features(feature, candidate_labels)


def is_loaded() -> bool:
    return _model is not None
