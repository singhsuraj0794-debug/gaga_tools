#!/usr/bin/env python3
"""Image Verification: strip ops/marketing claims from product images.

Pipeline per image:
  1. OCR the image (EasyOCR) and match the text against the same high-precision
     operations-claim patterns the pre-listing validator uses (best seller, free
     delivery, COD, % off, sale, warranty ...).
  2. Build a mask over those text boxes (padded) and remove them with OpenCV's
     content-aware inpainting (Telea).
  3. Upload the cleaned image to imgbb and return the new URL.
  4. Rewrite the URL in the sheet, in place, and keep an audit column.

CLI:
    python _image_verify.py --in sheet.xlsx --out sheet_fixed.xlsx \
        --imgbb-key <key> [--columns "Product Image 1,Product Image 2"] \
        [--limit N] [--progress progress.json]

The same functions power the MCP tools in scripts/image_verify_mcp.py.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IMGBB_ENDPOINT = "https://api.imgbb.com/1/upload"

# High-precision operations/marketing claim wording. Deliberately does NOT match
# pack copy such as "MRP", "Net Qty", "1 Year Warranty", "Made in India" - those
# are product/packaging facts, not promo claims.
def _claim_hits(text: str) -> list[dict]:
    """Ops claims per the PRE-LISTING VALIDATOR rules (single source of truth).

    Kept as a thin wrapper so this tool cannot drift into flagging marketing
    badges ("Best Gift", "Eco-friendly", "Premium Quality") or printed specs
    ("1 Year Warranty") that the validator deliberately allows.
    """
    from _clip_verify import detect_claims_in_text
    return detect_claims_in_text(text)


def _load_cv():
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401
    return cv2, np


_READER = None


def _reader():
    global _READER
    if _READER is None:
        import easyocr
        _READER = easyocr.Reader(["en"], gpu=True, verbose=False)
    return _READER


def load_image(source: str):
    """Load from an http(s) URL or a local path -> BGR numpy array (or None)."""
    cv2, np = _load_cv()
    try:
        if str(source).startswith(("http://", "https://")):
            req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        else:
            with open(source, "rb") as f:
                data = f.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def _ocr_words(img) -> list[dict]:
    """OCR the image once -> [{x1,y1,x2,y2,text,conf}]."""
    if img is None:
        return []
    reader = _reader()
    try:
        results = reader.readtext(img, detail=1, paragraph=False)
    except Exception:
        return []
    out = []
    for box, text, conf in results:
        t = (text or "").strip()
        if len(t) < 2 or conf < 0.15:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({"x1": int(min(xs)), "y1": int(min(ys)),
                    "x2": int(max(xs)), "y2": int(max(ys)),
                    "text": t, "conf": round(float(conf), 2)})
    return out


def _group_words(words: list[dict], h: int, w: int) -> list[list[dict]]:
    """Cluster nearby words into text blocks (headline + its sub-line, etc.)."""
    items = sorted(words, key=lambda b: (b["y1"], b["x1"]))
    groups: list[list[dict]] = []
    for b in items:
        placed = False
        for g in groups:
            gx1 = min(x["x1"] for x in g); gx2 = max(x["x2"] for x in g)
            gy2 = max(x["y2"] for x in g)
            overlap = min(gx2, b["x2"]) - max(gx1, b["x1"])
            if overlap > 0 and b["y1"] <= gy2 + h * 0.04:
                g.append(b); placed = True; break
        if not placed:
            groups.append([b])
    return groups


def detect_claims(img) -> list[dict]:
    """OCR the image and return the claim text boxes found.

    Claims are decided per TEXT BLOCK, not per line: overlays routinely split a
    phrase across lines ("FREE" / "DELIVERY"), and a per-line matcher misses it.
    The vocabulary itself comes from the pre-listing validator.
    """
    words = _ocr_words(img)
    if not words:
        return []
    h, w = img.shape[:2]
    out: list[dict] = []
    for g in _group_words(words, h, w):
        joined = " ".join(x["text"] for x in g)
        hits = _claim_hits(joined)
        if hits:
            for x in g:
                out.append({**x, "label": hits[0]["label"]})
    if out:
        return out
    if not _claim_hits(" ".join(x["text"] for x in words)):
        return []
    return [dict(x, label="claim") for x in words]


def build_mask(img, boxes: list[dict], pad: int = 6):
    """White mask over the claim boxes (dilated a little).

    Masks the BLOCK around each claim, not just the glyphs: "BEST SELLER" is
    printed on a white ribbon with a red border, so masking only the letters
    leaves the ribbon behind. The block box (padded) lets the inpaint rebuild the
    whole badge area with the surrounding product/background.
    """
    cv2, np = _load_cv()
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    painted = 0
    grouping = os.environ.get("IMAGE_VERIFY_MASK", "group") != "line"
    rects = [(b["x1"], b["y1"], b["x2"], b["y2"]) for b in boxes]
    if grouping and rects:
        # merge boxes that touch/overlap within a small gap
        gap = int(min(h, w) * 0.012)
        merged: list[list[int]] = []
        for r in rects:
            for m in merged:
                if not (r[0] > m[2] + gap or r[2] < m[0] - gap or
                        r[1] > m[3] + gap or r[3] < m[1] - gap):
                    m[0] = min(m[0], r[0]); m[1] = min(m[1], r[1])
                    m[2] = max(m[2], r[2]); m[3] = max(m[3], r[3])
                    break
            else:
                merged.append(list(r))
        rects = [tuple(m) for m in merged]
    for (bx1, by1, bx2, by2) in rects:
        # Badges/ribbons carry a border around the glyphs; pad by a fraction of
        # the block's own height so the frame goes with the text.
        extra = int((by2 - by1) * float(os.environ.get("IMAGE_VERIFY_MASK_PAD", "0.30")))
        p_ = max(pad, extra)
        x1 = max(0, bx1 - p_); y1 = max(0, by1 - p_)
        x2 = min(w - 1, bx2 + p_); y2 = min(h - 1, by2 + p_)
        if x2 <= x1 or y2 <= y1:
            continue
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
        painted += 1
    if painted:
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    return mask, painted


def remove_claims(img, boxes: list[dict], pad: int = 6, radius: int = 5):
    """Content-aware fill over the given boxes.

    Preferred path: the image-mcp inpaint model (prompt-guided diffusion), with
    its result composited back ONLY inside the mask so the product itself is never
    re-rendered. Falls back to OpenCV Telea when the MCP server is unreachable.
    """
    cv2, np = _load_cv()
    if img is None or not boxes:
        return img, 0
    mask, painted = build_mask(img, boxes, pad)
    if not painted:
        return img, 0

    # 1) MCP inpaint, composited inside the mask only.
    try:
        import _image_mcp
        h0, w0 = img.shape[:2]
        # The MCP endpoint 413s on big PNG payloads (a 2428x1830 product shot).
        # Inpainting works at ~1280px, so downscale the pair and map the result
        # back - the composite below still uses the full-resolution original.
        limit = int(os.environ.get("IMAGE_VERIFY_MCP_MAX_SIDE", "1280"))
        scale = min(1.0, limit / float(max(h0, w0)))
        src = img if scale >= 1.0 else cv2.resize(img, (int(w0 * scale), int(h0 * scale)))
        msk = mask if scale >= 1.0 else cv2.resize(mask, (int(w0 * scale), int(h0 * scale)),
                                                  interpolation=cv2.INTER_NEAREST)
        ok, ibuf = cv2.imencode(".png", src)
        ok2, mbuf = cv2.imencode(".png", msk)
        if ok and ok2:
            raw = _image_mcp.inpaint(ibuf.tobytes(), mbuf.tobytes(),
                                     prompt=("clean seamless product photo background, "
                                             "remove all text and badges, natural surface"))
            if raw:
                gen = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                if gen is not None:
                    if gen.shape[:2] != (h0, w0):
                        gen = cv2.resize(gen, (w0, h0))
                    soft = cv2.GaussianBlur(mask, (7, 7), 0).astype(np.float32) / 255.0
                    soft = soft[..., None]
                    out = (gen.astype(np.float32) * soft + img.astype(np.float32) * (1 - soft))
                    return out.clip(0, 255).astype(np.uint8), painted
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] MCP inpaint unavailable ({type(exc).__name__}); using OpenCV Telea",
              file=sys.stderr)

    # 2) Local fallback.
    out = cv2.inpaint(img, mask, radius, cv2.INPAINT_TELEA)
    return out, painted


def encode_png(img) -> bytes:
    cv2, _ = _load_cv()
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""


def upload_imgbb(img_bytes: bytes, api_key: str, name: str = "image.png") -> str:
    """Upload PNG bytes to imgbb, return the direct URL ('' on failure)."""
    if not img_bytes or not api_key:
        return ""
    body = urllib.parse.urlencode({
        "key": api_key,
        "image": base64.b64encode(img_bytes).decode("ascii"),
        "name": re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:48],
    }).encode()
    req = urllib.request.Request(IMGBB_ENDPOINT, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        if data.get("success"):
            return (data.get("data") or {}).get("url") or (data.get("data") or {}).get("display_url") or ""
        print(f"[imgbb] upload failed: {str(data)[:200]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[imgbb] upload error: {type(exc).__name__}: {exc}", file=sys.stderr)
    return ""



def detect_text_panels(img, min_boxes: int = 3, min_area_frac: float = 0.03) -> list[dict]:
    """Cluster OCR text into rectangular PANELS (infographic copy blocks).

    Word-level masking cannot cleanly rebuild designed headline/paragraph layouts
    (it shreds the typography - "Perfect for..." came back as "Rongest Fldcfus").
    A panel is a group of >= min_boxes text lines that overlap horizontally and
    together cover at least min_area_frac of the image; the whole block is then
    masked and the model repaints a clean background.
    """
    if img is None:
        return []
    h, w = img.shape[:2]
    try:
        boxes = _reader().readtext(img, detail=1, paragraph=False)
    except Exception:
        return []
    items = []
    for box, text, conf in boxes:
        if not str(text or "").strip() or conf < 0.2:
            continue
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        items.append({"x1": int(min(xs)), "y1": int(min(ys)),
                      "x2": int(max(xs)), "y2": int(max(ys)),
                      "text": str(text).strip()})
    if len(items) < min_boxes:
        return []
    # group vertically-adjacent lines that share horizontal space
    items.sort(key=lambda b: (b["y1"], b["x1"]))
    groups: list[list[dict]] = []
    for b in items:
        placed = False
        for g in groups:
            gx1 = min(x["x1"] for x in g); gx2 = max(x["x2"] for x in g)
            gy2 = max(x["y2"] for x in g)
            overlap = min(gx2, b["x2"]) - max(gx1, b["x1"])
            if overlap > 0 and b["y1"] <= gy2 + h * 0.04:
                g.append(b)
                placed = True
                break
        if not placed:
            groups.append([b])
    panels = []
    for g in groups:
        if len(g) < min_boxes:
            continue
        joined = " ".join(x.get("text", "") for x in g)
        if len(g) < 4 and not _claim_hits(joined):
            continue
        x1 = min(x["x1"] for x in g); y1 = min(x["y1"] for x in g)
        x2 = max(x["x2"] for x in g); y2 = max(x["y2"] for x in g)
        if (x2 - x1) * (y2 - y1) < min_area_frac * w * h:
            continue
        pad = int(max(w, h) * 0.012)
        panels.append({"x1": max(0, x1 - pad), "y1": max(0, y1 - pad),
                       "x2": min(w - 1, x2 + pad), "y2": min(h - 1, y2 + pad),
                       "lines": len(g), "text": joined[:300]})
    return panels


def remove_panels(img, panels: list[dict], radius: int = 7):
    """Mask whole panels and repaint a clean background with the image model."""
    cv2, np = _load_cv()
    if img is None or not panels:
        return img, 0
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for b in panels:
        cv2.rectangle(mask, (b["x1"], b["y1"]), (b["x2"], b["y2"]), 255, -1)
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=1)
    try:
        import _image_mcp
        ok, ibuf = cv2.imencode(".png", img)
        ok2, mbuf = cv2.imencode(".png", mask)
        if ok and ok2:
            raw = _image_mcp.inpaint(
                ibuf.tobytes(), mbuf.tobytes(),
                prompt=("clean seamless studio product background, soft even lighting, "
                        "smooth gradient, empty space, no text, no words, no lines"),
                negative_prompt=("text, letters, words, paragraph, headline, bullet points, "
                                 "logo, watermark, border, frame, icons, arrows"),
                steps=28, guidance=6.5)
            if raw:
                gen = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                if gen is not None:
                    if gen.shape[:2] != img.shape[:2]:
                        gen = cv2.resize(gen, (img.shape[1], img.shape[0]))
                    soft = cv2.GaussianBlur(mask, (15, 15), 0).astype(np.float32) / 255.0
                    soft = soft[..., None]
                    out = gen.astype(np.float32) * soft + img.astype(np.float32) * (1 - soft)
                    return out.clip(0, 255).astype(np.uint8), len(panels)
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] panel repaint unavailable ({type(exc).__name__})", file=sys.stderr)
    out = cv2.inpaint(img, mask, radius, cv2.INPAINT_TELEA)
    return out, len(panels)



def _corner_stats(img):
    """Std-dev and median of the four corner patches (the background probe)."""
    cv2, np = _load_cv()
    h, w = img.shape[:2]
    s = max(6, min(h, w) // 16)
    patches = [img[:s, :s], img[:s, -s:], img[-s:, :s], img[-s:, -s:]]
    px = np.concatenate([p.reshape(-1, 3) for p in patches])
    return float(np.std(px)), px


def _page_bg(img) -> tuple:
    cv2, np = _load_cv()
    h, w = img.shape[:2]
    f = max(8, int(min(h, w) * 0.02))
    frame = np.concatenate([
        img[:f].reshape(-1, 3), img[-f:].reshape(-1, 3),
        img[:, :f].reshape(-1, 3), img[:, -f:].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(frame, axis=0))


def _panel_region(img, panel: dict):
    """Bounding box of everything drawn on the panel's background.

    Infographic cards are often the SAME colour as the page (only a soft shadow
    separates them), so brightness cannot find the card. Instead take every
    component that differs from the page background and intersects the text
    block - that captures headline+body+bullet icons+badges as one region, and
    the fill colour (the page background) then blends invisibly.
    """
    cv2, np = _load_cv()
    h, w = img.shape[:2]
    bg = np.array(_page_bg(img), dtype=np.int16)
    diff = np.abs(img.astype(np.int16) - bg).max(axis=2)
    ink = (diff > 10).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    px1, py1, px2, py2 = panel["x1"], panel["y1"], panel["x2"], panel["y2"]
    xs1 = ys1 = 10 ** 9; xs2 = ys2 = -1; area = 0
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        if x > px2 or y > py2 or x + bw < px1 or y + bh < py1:
            continue
        xs1 = min(xs1, x); ys1 = min(ys1, y)
        xs2 = max(xs2, x + bw); ys2 = max(ys2, y + bh)
        area += a
    if xs2 < 0 or area > 0.8 * w * h:
        return None
    if (xs2 - xs1) < (px2 - px1) * 0.9:
        return None
    return (int(xs1), int(ys1), int(xs2), int(ys2))


def product_crop(img, margin: float = 0.06, pad_square: bool = True):
    """Keep only the product: drop every infographic element around it.

    Designed panels (badges, dashed circles, icons, copy) are frequently
    interleaved with the product, so a rectangle fill cuts them in half and leaves
    fragments. Cropping to the product's own silhouette removes the whole
    composition deterministically, which is what a compliant listing image needs.
    """
    cv2, np = _load_cv()
    if img is None:
        return img, None
    h, w = img.shape[:2]
    bg = np.array(_page_bg(img), dtype=np.int16)
    diff = np.abs(img.astype(np.int16) - bg).max(axis=2)
    ink = (diff > 18).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    if n <= 1:
        return img, None
    areas = stats[1:, cv2.CC_STAT_AREA]
    main = 1 + int(np.argmax(areas))
    mx, my, mw, mh, _ = stats[main]
    # pull in nearby pieces of the same product (cap, strap, detached shadow band)
    xs1, ys1, xs2, ys2 = mx, my, mx + mw, my + mh
    main_area = float(stats[main, cv2.CC_STAT_AREA])
    for i in range(1, n):
        if i == main:
            continue
        x, y, bw, bh, a = stats[i]
        # only substantial pieces fully inside the product's box (cap, detached
        # strap). Side-by-side icons and the brand logo must stay out, so no
        # "nearby" tolerance: that chained every icon into the product.
        if a < 0.15 * main_area:
            continue
        if x >= mx and y >= my and x + bw <= mx + mw and y + bh <= my + mh:
            xs1 = min(xs1, x); ys1 = min(ys1, y)
            xs2 = max(xs2, x + bw); ys2 = max(ys2, y + bh)
    if (xs2 - xs1) * (ys2 - ys1) < 0.12 * w * h:
        return img, None
    # A "product" spanning nearly the whole frame is really a lifestyle shot or a
    # brochure where everything merges into one blob - cropping changes nothing,
    # so report it as not-croppable instead of claiming a fix.
    if (xs2 - xs1) > 0.88 * w and (ys2 - ys1) > 0.88 * h:
        return img, None
    # wipe anything inside the box that is not the product (icon slivers, logo
    # edges): keep only the main component, then crop.
    keep = np.zeros((h, w), dtype=np.uint8)
    keep[labels == main] = 255
    for i in range(1, n):
        if i == main:
            continue
        x, y, bw, bh, a = stats[i]
        if a >= 0.15 * main_area and x >= mx and y >= my and x + bw <= mx + mw and y + bh <= my + mh:
            keep[labels == i] = 255
    # Only wipe stray fragments when the background is uniform (it is, we just
    # checked); on designed backgrounds the fill leaves a hard halo.
    if True:
        keep = cv2.dilate(keep, np.ones((5, 5), np.uint8), 1)
        clean = np.zeros_like(img); clean[:] = _page_bg(img)
        clean[keep > 0] = img[keep > 0]
        img = clean
    m = int(max(xs2 - xs1, ys2 - ys1) * margin)
    xs1, ys1 = max(0, xs1 - m), max(0, ys1 - m)
    xs2, ys2 = min(w, xs2 + m), min(h, ys2 + m)
    out = img[ys1:ys2, xs1:xs2]
    # Nothing meaningful was removed -> do not present a "fix". Checking area is
    # not enough (a brochure fills the frame yet is slightly inset), so compare
    # how much of the image's ink actually falls inside the crop.
    total_ink = float(ink.sum())
    if total_ink > 0:
        kept = float(ink[ys1:ys2, xs1:xs2].sum())
        if kept / total_ink > 0.95:
            return img, None
    if (xs2 - xs1) * (ys2 - ys1) > 0.85 * (w * h):
        return img, None
    if pad_square:
        oh, ow = out.shape[:2]
        side = max(oh, ow)
        canvas = np.zeros((side, side, 3), dtype=np.uint8)
        canvas[:] = _page_bg(img)
        oy = (side - oh) // 2; ox = (side - ow) // 2
        canvas[oy:oy + oh, ox:ox + ow] = out
        out = canvas
    return out, (xs1, ys1, xs2, ys2)


def remove_panels_flat(img, panels: list[dict], feather: int = 9):
    """Erase infographic panels into a clean page background.

    The panels sit on flat design backgrounds (white cards / grey page). Diffusion
    invents objects on large empty areas (a grey card came back as a pink
    cylinder), and sampling a thin ring caught the orange badge blocks. So: detect
    the enclosing card, fill it with the image's own page-background colour, and
    feather the seam. Deterministic and invisible on studio mockups.
    """
    cv2, np = _load_cv()
    if img is None or not panels:
        return img, 0
    h, w = img.shape[:2]
    bg = _page_bg(img)
    out = np.zeros_like(img); out[:] = bg
    mask = np.zeros((h, w), dtype=np.uint8)
    done = 0
    for b in panels:
        card = _panel_region(img, b)
        x1, y1, x2, y2 = card if card else (b["x1"], b["y1"], b["x2"], b["y2"])
        pad = int(max(w, h) * 0.01)
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
        done += 1
    mask = cv2.dilate(mask, np.ones((feather, feather), np.uint8), 1)
    soft = cv2.GaussianBlur(mask, (feather * 2 + 1, feather * 2 + 1), 0).astype(np.float32) / 255.0
    soft = soft[..., None]
    res = out.astype(np.float32) * soft + img.astype(np.float32) * (1 - soft)
    return res.clip(0, 255).astype(np.uint8), done


def verify_panels(source: str, api_key: str) -> dict:
    """Whole-panel regeneration for infographic images."""
    t0 = time.time()
    img = load_image(source)
    if img is None:
        return {"source": source, "status": "unreachable", "panels": 0, "url": "", "seconds": 0}
    panels = detect_text_panels(img)
    if not panels:
        return {"source": source, "status": "no-panel", "panels": 0, "url": "", "seconds": round(time.time() - t0, 1)}
    if os.environ.get("IMAGE_VERIFY_PANEL_ENGINE", "flat") == "mcp":
        out, n = remove_panels(img, panels)
    else:
        out, n = remove_panels_flat(img, panels)
    url = upload_imgbb(encode_png(out), api_key, name=os.path.basename(str(source)) or "image.png")
    return {"source": source, "status": "repainted" if url else "repainted-no-upload",
            "panels": n, "boxes": panels, "url": url, "seconds": round(time.time() - t0, 1)}


def verify_image(source: str, api_key: str, mode: str = "word") -> dict:
    """Detect + remove claims + re-upload one image.

    mode:
      word  - mask just the claim words (least change, can shred typography)
      panel - erase the whole designed copy block into the page background
      crop  - keep only the product (drops icons, badges, logo, copy)
      auto  - crop when the product is a distinct object, else panel
    """
    t0 = time.time()
    img = load_image(source)
    if img is None:
        return {"source": source, "status": "unreachable", "claims": [], "url": "", "seconds": 0}
    if mode in ("panel", "crop", "auto"):
        panels = detect_text_panels(img)
        claims = detect_claims(img)
        if mode == "crop" or (mode == "auto" and panels):
            out, box = product_crop(img)
            if box is not None:
                leftover = detect_claims(out)
                url = upload_imgbb(encode_png(out), api_key, name=os.path.basename(str(source)) or "image.png")
                st = "cropped" if not leftover else "cropped-claims-remain"
                if not url:
                    st = "cropped-no-upload"
                return {"source": source, "status": st,
                        "claims": [{"label": b["label"], "text": b["text"]} for b in claims],
                        "leftover": [{"label": b["label"], "text": b["text"]} for b in leftover],
                        "crop": list(box), "url": url, "seconds": round(time.time() - t0, 1)}
        if panels:
            if os.environ.get("IMAGE_VERIFY_PANEL_ENGINE", "flat") == "mcp":
                out, n = remove_panels(img, panels)
            else:
                out, n = remove_panels_flat(img, panels)
            url = upload_imgbb(encode_png(out), api_key, name=os.path.basename(str(source)) or "image.png")
            return {"source": source, "status": "repainted" if url else "repainted-no-upload",
                    "claims": [{"label": b["label"], "text": b["text"]} for b in claims],
                    "panels": n, "url": url, "seconds": round(time.time() - t0, 1)}
        if claims:
            cleaned, painted = remove_claims(img, claims)
            url = upload_imgbb(encode_png(cleaned), api_key, name=os.path.basename(str(source)) or "image.png")
            return {"source": source, "status": "fixed" if url else "fixed-no-upload",
                    "claims": [{"label": b["label"], "text": b["text"]} for b in claims],
                    "url": url, "seconds": round(time.time() - t0, 1)}
        return {"source": source, "status": "clean", "claims": [], "url": "", "seconds": round(time.time() - t0, 1)}
    boxes = detect_claims(img)
    if not boxes:
        return {"source": source, "status": "clean", "claims": [], "url": "", "seconds": round(time.time() - t0, 1)}
    cleaned, painted = remove_claims(img, boxes)
    url = upload_imgbb(encode_png(cleaned), api_key, name=os.path.basename(str(source)) or "image.png")
    return {
        "source": source,
        "status": "fixed" if url else "fixed-no-upload",
        "claims": [{"label": b["label"], "text": b["text"], "box": [b["x1"], b["y1"], b["x2"], b["y2"]]} for b in boxes],
        "boxes_removed": painted,
        "url": url,
        "seconds": round(time.time() - t0, 1),
    }


# ── sheet handling ──────────────────────────────────────────────────────────
IMAGE_HEADER = re.compile(r"(image|img|photo|picture)", re.I)


def _is_url(v) -> bool:
    return isinstance(v, str) and v.startswith(("http://", "https://"))


def process_sheet(input_path: str, out_path: str, api_key: str,
                  columns: list[str] | None = None, limit: int = 0,
                  progress_path: str | None = None, mode: str = "word") -> dict:
    from openpyxl import load_workbook

    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".csv", ".txt"):
        # openpyxl only reads xlsx; build a workbook from the CSV so the UI's
        # ".csv accepted" promise holds (it used to fail with InvalidFileException).
        from openpyxl import Workbook
        with open(input_path, newline="", encoding="utf-8-sig") as f:
            data = [row for row in csv.reader(f)]
        wb = Workbook()
        ws = wb.active
        for row in data:
            ws.append(row)
    else:
        wb = load_workbook(input_path)
        ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"error": "empty sheet"}
    header = [str(c or "").strip() for c in rows[0]]

    cols = []
    if columns:
        wanted = {c.strip().lower() for c in columns}
        cols = [i for i, h in enumerate(header) if h.lower() in wanted]
    if not cols:
        cols = [i for i, h in enumerate(header) if IMAGE_HEADER.search(h) and "url" in h.lower()]
        if not cols:
            cols = [i for i, h in enumerate(header) if IMAGE_HEADER.search(h)]
    if not cols:
        # Headerless export ("a list of links"): row 1 is data, so sniff the
        # columns that actually hold URLs and add a proper header row.
        data_rows = rows if any(_is_url(c) for c in rows[0]) else rows[1:]
        seen: dict[int, int] = {}
        for row in data_rows[:50]:
            for i, cell in enumerate(row):
                if _is_url(cell):
                    seen[i] = seen.get(i, 0) + 1
        cols = sorted(seen)
        if not cols:
            return {"error": "no image URL column found", "header": header}
        ncols = max(len(r) for r in rows)
        ws.insert_rows(1)
        header = [""] * ncols
        for n, i in enumerate(cols):
            header[i] = "Image URL" if n == 0 else f"Image URL {n + 1}"
            ws.cell(row=1, column=i + 1, value=header[i])
        rows = [header] + data_rows

    tasks = []
    for r, row in enumerate(rows[1:], start=2):
        for c in cols:
            if c < len(row) and _is_url(row[c]):
                tasks.append((r, c, row[c]))
    if limit:
        tasks = tasks[:limit]

    report = []
    ncols = max(len(r) for r in rows)
    url_col = ncols + 1
    claim_col = url_col + 1
    status_col = url_col + 2
    ws.cell(row=1, column=url_col, value="Verified Image URL")
    ws.cell(row=1, column=claim_col, value="Claims Removed")
    ws.cell(row=1, column=status_col, value="Image Verify Status")

    done = 0
    by_row: dict[int, list] = {}
    for r, c, url in tasks:
        by_row.setdefault(r, []).append((c, url))

    def _ok(st: str) -> bool:
        return st in ("fixed", "cropped", "repainted")

    for r, items in by_row.items():
        claims: set[str] = set()
        statuses: list[str] = []
        changed = 0
        for c, url in items:
            res = verify_image(url, api_key, mode)
            done += 1
            if res["url"]:
                ws.cell(row=r, column=c + 1, value=res["url"])
                changed += 1
            claims.update(x["label"] for x in res.get("claims", []))
            statuses.append(res["status"])
            report.append(res)
            if progress_path:
                try:
                    with open(progress_path, "w", encoding="utf-8") as f:
                        json.dump({"done": done, "total": len(tasks),
                                   "current": os.path.basename(str(url))[:80],
                                   "fixed": sum(1 for x in report if _ok(x["status"])),
                                   "clean": sum(1 for x in report if x["status"] == "clean")}, f)
                except Exception:
                    pass
        if changed:
            ws.cell(row=r, column=url_col, value=ws.cell(row=r, column=items[0][0] + 1).value)
        ws.cell(row=r, column=claim_col, value="; ".join(sorted(claims)))
        # One status per row, worst-first, so a partially handled image is visible.
        order = ["failed", "burnt", "needs-manual", "fixed-leftover",
                 "cropped-claims-remain", "skipped-no-panel", "unreachable"]
        pick = next((s for o in order for s in statuses if s == o), None)
        ws.cell(row=r, column=status_col, value=pick or (statuses[0] if statuses else ""))

    wb.save(out_path)
    return {"total": len(tasks), "done": done,
            "fixed": sum(1 for x in report if _ok(x["status"])),
            "clean": sum(1 for x in report if x["status"] == "clean"),
            "unreachable": sum(1 for x in report if x["status"] == "unreachable"),
            "columns": [header[i] for i in cols], "images": report}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--imgbb-key", default=os.environ.get("IMGBB_API_KEY", "92789523467a2cc743f1c0f143322dbc"))
    ap.add_argument("--columns", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--progress", default="")
    ap.add_argument("--mode", required=True, choices=["word", "panel", "crop", "auto"],
                    help="word = mask claims only; panel = erase copy block; crop = product only")
    a = ap.parse_args()
    cols = [c.strip() for c in a.columns.split(",") if c.strip()] or None
    res = process_sheet(a.inp, a.out, a.imgbb_key, cols, a.limit, a.progress or None, a.mode)
    print(json.dumps({k: v for k, v in res.items() if k != "images"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
