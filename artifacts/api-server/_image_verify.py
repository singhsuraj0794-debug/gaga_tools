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
CLAIM_PATTERNS = [
    (r"\bBEST\s*SELL(?:ER|ING)\b", "best seller"),
    (r"\bNO\.?\s*1\b|\bNUMBER\s*1\b|\b#1\b", "number 1"),
    (r"\bFREE\s*(?:DELIVERY|SHIPPING|RETURN|RETURNS|REPLACEMENT|EXCHANGE|GIFT)\b", "free delivery/return"),
    (r"\bCASH\s*ON\s*DELIVERY\b|\bCOD\s*AVAILABLE\b|\bPAY\s*ON\s*DELIVERY\b", "COD"),
    (r"\b(?:FLAT\s*)?\d{1,3}\s*%\s*(?:OFF|DISCOUNT)\b", "discount"),
    (r"\b(?:MEGA|BIG|HOT|FLASH|CLEARANCE|END\s*OF\s*SEASON)\s*SALE\b", "sale"),
    (r"\bLIMITED\s*(?:TIME|STOCK|PERIOD)\s*OFFER\b|\bSPECIAL\s*OFFER\b", "offer"),
    (r"\bBUY\s*\d+\s*GET\s*\d+\b|\bBUY\s*1\s*GET\s*1\b", "buy/get offer"),
    (r"\bNO\s*COST\s*EMI\b|\bEASY\s*EMI\b|\bEMI\s*AVAILABLE\b", "EMI"),
    (r"\b(?:LIFETIME|FREE|EXTENDED)\s*WARRANTY\b", "warranty claim"),
    (r"\bHASSLE[- ]?FREE\s*(?:RETURN|RETURNS|RETURN\s*POLICY)\b", "hassle-free return"),
    (r"\bBEST\s*PRICE\b|\bLOWEST\s*PRICE\b|\bTOP\s*RATED\b|\bHIGHLY\s*RATED\b", "price/rating claim"),
    (r"\bHURRY\b|\bORDER\s*NOW\b|\bBUY\s*NOW\b|\bSHOP\s*NOW\b", "urgency CTA"),
    (r"\b100%\s*(?:ORIGINAL|GENUINE|AUTHENTIC)\b", "authenticity claim"),
    (r"\b(?:FREE|COMPLIMENTARY)\s*GIFT\b", "free gift"),
]
_COMPILED = [(re.compile(p, re.I), label) for p, label in CLAIM_PATTERNS]


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


def detect_claims(img) -> list[dict]:
    """OCR the image and return the claim text boxes found."""
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
        if len(t) < 3 or conf < 0.15:
            continue
        for rx, label in _COMPILED:
            if rx.search(t):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                out.append({
                    "x1": int(min(xs)), "y1": int(min(ys)),
                    "x2": int(max(xs)), "y2": int(max(ys)),
                    "text": t, "label": label, "conf": round(float(conf), 2),
                })
                break
    return out


def build_mask(img, boxes: list[dict], pad: int = 6):
    """White mask over the claim boxes (dilated a little)."""
    cv2, np = _load_cv()
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    painted = 0
    for b in boxes:
        x1 = max(0, b["x1"] - pad); y1 = max(0, b["y1"] - pad)
        x2 = min(w - 1, b["x2"] + pad); y2 = min(h - 1, b["y2"] + pad)
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
        ok, ibuf = cv2.imencode(".png", img)
        ok2, mbuf = cv2.imencode(".png", mask)
        if ok and ok2:
            raw = _image_mcp.inpaint(ibuf.tobytes(), mbuf.tobytes(),
                                     prompt=("clean seamless product photo background, "
                                             "remove all text and badges, natural surface"))
            if raw:
                gen = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                if gen is not None:
                    if gen.shape[:2] != img.shape[:2]:
                        gen = cv2.resize(gen, (img.shape[1], img.shape[0]))
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


def verify_image(source: str, api_key: str) -> dict:
    """Detect + remove claims + re-upload one image."""
    t0 = time.time()
    img = load_image(source)
    if img is None:
        return {"source": source, "status": "unreachable", "claims": [], "url": "", "seconds": 0}
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
                  progress_path: str | None = None) -> dict:
    from openpyxl import load_workbook

    ext = os.path.splitext(input_path)[1].lower()
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
        return {"error": "no image URL column found", "header": header}

    tasks = []
    for r, row in enumerate(rows[1:], start=2):
        for c in cols:
            if c < len(row) and _is_url(row[c]):
                tasks.append((r, c, row[c]))
    if limit:
        tasks = tasks[:limit]

    report = []
    url_col = len(header) + 1
    claim_col = url_col + 1
    status_col = url_col + 2
    ws.cell(row=1, column=url_col, value="Verified Image URL")
    ws.cell(row=1, column=claim_col, value="Claims Removed")
    ws.cell(row=1, column=status_col, value="Image Verify Status")

    done = 0
    for r, c, url in tasks:
        res = verify_image(url, api_key)
        done += 1
        if res["url"]:
            # header indices are 0-based; openpyxl columns are 1-based.
            ws.cell(row=r, column=c + 1, value=res["url"])
            ws.cell(row=r, column=url_col, value=res["url"])
        ws.cell(row=r, column=claim_col, value="; ".join(sorted({x["label"] for x in res["claims"]})))
        ws.cell(row=r, column=status_col, value=res["status"])
        report.append(res)
        if progress_path:
            try:
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump({"done": done, "total": len(tasks),
                               "current": os.path.basename(str(url))[:80],
                               "fixed": sum(1 for x in report if x["status"] == "fixed"),
                               "clean": sum(1 for x in report if x["status"] == "clean")}, f)
            except Exception:
                pass

    wb.save(out_path)
    return {"total": len(tasks), "done": done,
            "fixed": sum(1 for x in report if x["status"] == "fixed"),
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
    a = ap.parse_args()
    cols = [c.strip() for c in a.columns.split(",") if c.strip()] or None
    res = process_sheet(a.inp, a.out, a.imgbb_key, cols, a.limit, a.progress or None)
    print(json.dumps({k: v for k, v in res.items() if k != "images"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
