#!/usr/bin/env python3
"""Verify duplicate products using DINOv2 (image) + CLIP (text) similarity.
Usage: python3 verify_duplicate.py <input.json>
Input JSON file: [{"id1":"...","img1":"...","title1":"...","id2":"...","img2":"...","title2":"..."}, ...]
Output to stdout: [{"id1":"...","id2":"...","dinov2_sim":0.95,"clip_text_sim":0.98,"is_duplicate":true}, ...]
"""
import json, sys, io, os, warnings
import requests
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel, CLIPProcessor, CLIPModel

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import logging
logging.disable(logging.WARNING)

_dinov2_processor = None
_dinov2_model = None
_clip_processor = None
_clip_model = None

_IMAGE_CACHE = {}

def fetch_image(url):
    if not url: return None
    if url in _IMAGE_CACHE: return _IMAGE_CACHE[url]
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            _IMAGE_CACHE[url] = img
            return img
    except Exception:
        pass
    _IMAGE_CACHE[url] = None
    return None

def load_models():
    global _dinov2_processor, _dinov2_model, _clip_processor, _clip_model
    if _dinov2_model is None:
        _dinov2_processor = AutoImageProcessor.from_pretrained("facebook/dinov2-small")
        _dinov2_model = AutoModel.from_pretrained("facebook/dinov2-small")
    if _clip_model is None:
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")

def dinov2_sim(url1, url2):
    img1 = fetch_image(url1)
    img2 = fetch_image(url2)
    if img1 is None or img2 is None: return None
    inputs1 = _dinov2_processor(images=img1, return_tensors="pt")
    inputs2 = _dinov2_processor(images=img2, return_tensors="pt")
    with torch.no_grad():
        f1 = _dinov2_model(**inputs1).last_hidden_state.mean(dim=1)
        f2 = _dinov2_model(**inputs2).last_hidden_state.mean(dim=1)
    sim = torch.nn.functional.cosine_similarity(f1, f2).item()
    return max(0.0, min(1.0, sim))

def clip_text_sim(text1, text2):
    if not text1 or not text2: return None
    inputs = _clip_processor(text=[text1, text2], return_tensors="pt", padding=True, truncation=True)
    with torch.no_grad():
        features = _clip_model.get_text_features(**inputs)
    sim = torch.nn.functional.cosine_similarity(features[0:1], features[1:2]).item()
    return max(0.0, min(1.0, sim))

def main():
    load_models()
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    with open(input_path) as f:
        pairs = json.load(f)
    results = []
    for p in pairs:
        try:
            dino = dinov2_sim(p.get("img1", ""), p.get("img2", ""))
            clip = clip_text_sim(p.get("title1", ""), p.get("title2", ""))
            is_dup = (dino is not None and dino >= 0.80) and (clip is not None and clip >= 0.90)
            results.append({
                "id1": p["id1"],
                "id2": p["id2"],
                "dinov2_sim": round(dino, 4) if dino is not None else None,
                "clip_text_sim": round(clip, 4) if clip is not None else None,
                "is_duplicate": is_dup,
            })
        except Exception as e:
            results.append({
                "id1": p["id1"],
                "id2": p["id2"],
                "dinov2_sim": None,
                "clip_text_sim": None,
                "is_duplicate": False,
                "error": str(e)[:200],
            })
    json.dump(results, sys.stdout)
    sys.stdout.flush()

if __name__ == "__main__":
    main()
