#!/usr/bin/env python3
"""
ImageGen — Product spec overlay generator.
Professional overlay with left specs panel + top title bar.
Responsive: panel sizes to content, no wasted space.
"""

import io, sys, json, os, base64, urllib.request, urllib.error
from typing import List, Dict, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

# ── Design constants ─────────────────────────────────────────────────────────
BAR_OPACITY = 0.85
TOP_BAR_HEIGHT_RATIO = 0.09       # top title bar: 9% of image height
CORNER_RADIUS = 14
PADDING = 16
BRAND_BADGE_PAD = 14

# Canvas normalization: cap to reasonable size to prevent huge overlays
MAX_CANVAS_WIDTH = 1200
MAX_CANVAS_HEIGHT = 1200

# Colors
DARK_BG = (15, 25, 55, int(255 * BAR_OPACITY))  # Dark navy blue
PANEL_BG = (15, 25, 55, int(255 * 0.88))  # Dark navy blue
ORANGE_BG = (255, 107, 0, int(255 * BAR_OPACITY))
WHITE = (255, 255, 255)
WHITE_DIM = (255, 255, 255, 160)
ORANGE_ACCENT = (255, 140, 50)


def _load_font(size: int, bold: bool = False):
    try:
        if bold:
            return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size, index=1)
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except Exception:
        try:
            return ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size
            )
        except Exception:
            return ImageFont.load_default()


def _draw_rounded_rect(draw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _text_width(text, font, draw):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_text(text: str, font, max_width: int, draw) -> List[str]:
    """Word-wrap text to fit within max_width pixels. Returns list of lines."""
    words = text.split()
    if not words:
        return [text]

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = current_line + " " + word
        if _text_width(test_line, font, draw) <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def _truncate_text(text: str, font, max_width: int, draw) -> str:
    """Truncate text with ellipsis if it exceeds max_width."""
    if _text_width(text, font, draw) <= max_width:
        return text
    truncated = text
    while _text_width(truncated + "...", font, draw) > max_width and len(truncated) > 1:
        truncated = truncated[:-1]
    # Remove trailing space/colon/separator
    truncated = truncated.rstrip(" :,-")
    return truncated + "..."


def generate_spec_overlay(
    image_source: str,
    product_name: str,
    specs: Dict[str, str],
    brand: str = "",
    output_path: Optional[str] = None,
) -> Optional[bytes]:
    try:
        if image_source.startswith(("http://", "https://")):
            req = urllib.request.Request(image_source, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            img = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=15).read())).convert("RGBA")
        else:
            img = Image.open(image_source).convert("RGBA")

        w, h = img.size
        if w < 400 or h < 400:
            return None

        # ── Normalize canvas: cap dimensions to prevent huge overlays ──────
        if w > MAX_CANVAS_WIDTH or h > MAX_CANVAS_HEIGHT:
            scale = min(MAX_CANVAS_WIDTH / w, MAX_CANVAS_HEIGHT / h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            w, h = new_w, new_h

        # ── Ensure minimum width for overlay readability ───────────────────
        if w < 600:
            w = 600
            img = img.resize((w, int(h * (600 / w))), Image.LANCZOS)
            h = img.size[1]

        base = min(w, h)
        font_title_size = max(16, int(base * 0.032))
        font_spec_size = max(10, int(base * 0.018))
        font_val_size = max(11, int(base * 0.020))
        font_brand_size = max(12, int(base * 0.022))

        font_title = _load_font(font_title_size, bold=True)
        font_spec = _load_font(font_spec_size, bold=True)
        font_val = _load_font(font_val_size)
        font_brand = _load_font(font_brand_size, bold=True)

        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # ── Top bar: dark with product title + brand badge ──────────────
        top_h = max(50, int(h * TOP_BAR_HEIGHT_RATIO))

        _draw_rounded_rect(draw, (0, 0, w, top_h + CORNER_RADIUS), radius=CORNER_RADIUS, fill=DARK_BG)
        draw.rectangle((0, top_h - CORNER_RADIUS, w, top_h + CORNER_RADIUS), fill=DARK_BG)

        # Brand badge (top-right)
        brand_w = 0
        if brand:
            brand_text = brand.upper()
            brand_bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
            brand_w = brand_bbox[2] - brand_bbox[0] + PADDING + 8
            brand_h = brand_bbox[3] - brand_bbox[1] + 12
            badge_x = w - brand_w - BRAND_BADGE_PAD
            badge_y = BRAND_BADGE_PAD
            _draw_rounded_rect(draw, (badge_x, badge_y, badge_x + brand_w, badge_y + brand_h), radius=brand_h // 2, fill=ORANGE_BG)
            draw.text((badge_x + PADDING // 2, badge_y + 5), brand_text, fill=WHITE, font=font_brand)

        # Title: single line, truncated with ellipsis
        title_max_w = w - PADDING * 2 - (brand_w + PADDING if brand else 0)
        title_text = _truncate_text(product_name, font_title, title_max_w, draw)
        ty = (top_h - font_title_size) // 2
        draw.text((PADDING, ty), title_text, fill=WHITE, font=font_title)

        # ── Left specs panel: responsive, fits content ──────────────────
        spec_items = list(specs.items())[:10]
        if not spec_items and brand:
            spec_items = [("Brand", brand)]

        if spec_items:
            panel_x = PADDING

            # Measure for individual spec cards
            row_h = font_spec_size + font_val_size + 12
            card_padding_x = 14
            card_padding_y = 10
            card_h = row_h + card_padding_y * 2

            # Find max card width from all specs (allow word wrapping for long values)
            max_card_inner_w = 0
            for label, value in spec_items:
                lw = _text_width(label.upper(), font_spec, draw)
                # For values, measure wrapped width
                val_lines = _wrap_text(str(value), font_val, int(w * 0.25), draw)
                vw = max(_text_width(line, font_val, draw) for line in val_lines) if val_lines else 0
                max_card_inner_w = max(max_card_inner_w, lw, vw)

            card_w = max_card_inner_w + card_padding_x * 2 + 12  # extra padding for accent line
            card_w = max(card_w, int(w * 0.25))
            card_w = min(card_w, int(w * 0.40))

            card_spacing = 6
            total_h = len(spec_items) * card_h + (len(spec_items) - 1) * card_spacing

            # Center vertically
            top_margin = int(h * 0.12)
            available_h = h - top_margin * 2
            start_y = top_margin + max(0, (available_h - total_h) // 2)

            # Draw individual spec cards
            for idx, (label, value) in enumerate(spec_items):
                card_y = start_y + idx * (card_h + card_spacing)
                card_right = panel_x + card_w
                card_bottom = card_y + card_h

                # Card background
                _draw_rounded_rect(draw, (panel_x, card_y, card_right, card_bottom), radius=10, fill=PANEL_BG)

                # Orange accent line on left - full height of card
                line_x = panel_x + 5
                line_top = card_y + 6
                line_bottom = card_bottom - 6
                draw.rounded_rectangle((line_x, line_top, line_x + 3, line_bottom), radius=2, fill=ORANGE_BG)

                # Label + Value
                lx = panel_x + card_padding_x + 6
                draw.text((lx, card_y + card_padding_y), label.upper(), fill=ORANGE_ACCENT, font=font_spec)

                val_text = str(value)
                max_val_w = card_w - card_padding_x * 2 - 12
                # Word-wrap value if needed
                val_lines = _wrap_text(val_text, font_val, max_val_w, draw)
                for line_idx, line in enumerate(val_lines[:2]):  # max 2 lines per value
                    draw.text(
                        (lx, card_y + card_padding_y + font_spec_size + 3 + line_idx * (font_val_size + 2)),
                        line, fill=WHITE, font=font_val
                    )

        result = Image.alpha_composite(img, overlay)
        result = result.convert("RGB")

        if output_path:
            result.save(output_path, "PNG", quality=95)
            return None
        else:
            buf = io.BytesIO()
            result.save(buf, format="PNG", quality=95)
            return buf.getvalue()

    except Exception as e:
        print(f"[IMAGEGEN] Failed: {e}", file=sys.stderr)
        return None


def generate_batch(products: List[dict], output_dir: str, imgb_key: str = "") -> List[dict]:
    """Generate overlays for multiple products. Uploads to ImgBB if key provided."""
    results = []
    for p in products:
        sku = p.get("sku", "")
        img = p.get("firstImageUrl") or (p.get("images") or [None])[0]
        if not img:
            results.append({"sku": sku, "error": "no image"})
            continue

        overlay_bytes = generate_spec_overlay(
            img, p.get("title", ""), p.get("specs", {}), brand=p.get("brand", "")
        )
        if not overlay_bytes:
            results.append({"sku": sku, "error": "generation failed"})
            continue

        image_url = ""
        if imgb_key:
            image_url = _upload_to_imgbb(imgb_key, overlay_bytes)
            if image_url:
                results.append({"sku": sku, "image_url": image_url, "image_base64": base64.b64encode(overlay_bytes).decode("utf-8")})
            else:
                results.append({"sku": sku, "image_base64": base64.b64encode(overlay_bytes).decode("utf-8"), "error": "imgbb upload failed"})
        else:
            results.append({"sku": sku, "image_base64": base64.b64encode(overlay_bytes).decode("utf-8")})

    return results


def _upload_to_imgbb(api_key: str, image_bytes: bytes) -> str:
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data = urllib.parse.urlencode({"key": api_key, "image": b64}).encode("utf-8")
        req = urllib.request.Request("https://api.imgbb.com/1/upload", data=data, method="POST")
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        if result.get("success") and result.get("data"):
            return result["data"]["url"]
        print(f"[IMAGEGEN] ImgBB upload failed: {result.get('error', 'unknown')}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[IMAGEGEN] ImgBB error: {e}", file=sys.stderr)
        return ""


def main():
    """CLI: _image_gen.py <input.json> [output_dir]"""
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp"

    with open(input_path) as f:
        data = json.load(f)

    products = data.get("products", [])
    imgb_key = data.get("imgbbKey", os.environ.get("IMGBB_API_KEY", ""))
    os.makedirs(output_dir, exist_ok=True)

    results = generate_batch(products, output_dir, imgb_key)
    print(json.dumps({"results": results}))


if __name__ == "__main__":
    main()
