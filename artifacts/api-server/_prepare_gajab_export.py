#!/usr/bin/env python3
"""Prepare Gajab ProductImport rows for the scraper exports.

Turns scraped products into template-shaped rows:

  * resolves the marketplace category (source_category_path) to a Gajab L1-L4
    path via the CURATED map (marketplace_category_map.json), falling back to a
    shallower level (L2/L3) when the map says so;
  * assigns the attributes that "Attributes Final With Valid Entries.xlsx"
    defines for that node path — nothing is invented, out-of-category attributes
    are never emitted;
  * maps scraped specification keys onto those attributes
    (spec_attribute_map.json);
  * fills the template's fixed columns (title, description, images, price...);
  * flags rows whose category could not be mapped.

Source of truth for attributes: gajab_attributes.json, which is a byte-faithful
extraction of "Attributes Final With Valid Entries.xlsx" (verified: 3,116 paths,
58,346 rows, 0 differences).

Usage:
    python3 _prepare_gajab_export.py products.json  # -> JSON on stdout
    echo '<json>' | python3 _prepare_gajab_export.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from _category_map import CategoryMapper  # noqa: E402
from _spec_to_attribute import SpecAttributeMapper, load_catalog  # noqa: E402

# The template's 38 fixed columns, in order (row-2 headers of Product excel sheet).
FIXED_COLUMNS = [
    "Sku *", "Listing  Type *", "Category  Name *", "Brand  Name *", "Product  Name *",
    "Description *", "Hsn *", "Tax *", "Product  Location *", "Auto  Renewal",
    "Start  Date", "Quantity *", "Low  Stock  Alert", "Package  Weight *",
    "Package  Height *", "Package  Width  *", "Package  Length *", "Relationship *",
    "Parent  Sku*", "Variantion  Name", "Mrp  Price *", "Transfer  Price *",
    "Meta  Tag  Title", "Meta  Tag  Description", "Meta  Tag  Keyword",
    "Cod", "Cod Charge",
    "Product Image 1 *", "Product Image 2", "Product Image 3", "Product Image 4",
    "Product Image 5", "Product Image 6", "Product Image 7", "Product Image 8",
    "Product Image 9", "Product Image 10", "Product Videos",
]

# Extra trailing columns we add for review (not part of the template).
AUDIT_COLUMNS = ["Mapping Status", "Source Category", "Mapped Category Path", "Review Notes"]


def _clean(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _html_description(text: str) -> str:
    """Render the description as HTML for the template.

    The Gajab listing template expects markup, but marketplaces hand us plain
    text (Meesho's description is stripped of tags before it reaches us). Rebuild
    it as paragraphs, with simple bullet runs turned into <ul><li>.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # already markup? leave it alone (Amazon/Flipkart often give HTML)
    if re.search(r"<\s*(p|br|ul|ol|li|div|span|strong|b|em|h[1-6])\b", t, re.I):
        return t

    # normalise whitespace and split into blocks on blank lines
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", t) if b.strip()]
    if len(blocks) <= 1:
        # no blank lines: split on bullets / lines instead
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        blocks = []
        bullets: list[str] = []
        for ln in lines:
            m = re.match(r"^[\-\*•·▪]\s+(.*)$", ln)
            if m:
                bullets.append(m.group(1).strip())
                continue
            if bullets:
                blocks.append("\x00" + "\x01".join(bullets))
                bullets = []
            blocks.append(ln)
        if bullets:
            blocks.append("\x00" + "\x01".join(bullets))

    html: list[str] = []
    for b in blocks:
        if b.startswith("\x00"):
            items = b[1:].split("\x01")
            html.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
        else:
            html.append(f"<p>{b}</p>")
    return "".join(html)


def _platform_of(p: dict) -> str:
    """Which marketplace this product came from.

    The frontend export body is just { products } with no platform field, and
    defaulting to 'amazon' meant EVERY Meesho/Flipkart row was looked up in the
    Amazon map — producing 'unmapped' for all of them with no attribute columns.
    Fall back to the product URL host so the caller cannot get this wrong.
    """
    plat = _clean(p.get("platform") or p.get("source"))
    if plat:
        return plat.lower()
    url = _clean(p.get("url")).lower()
    if "meesho.com" in url:
        return "meesho"
    if "flipkart.com" in url:
        return "flipkart"
    if "amazon." in url or "amzn." in url:
        return "amazon"
    return "amazon"


def _sku_for(p: dict, idx: int) -> str:
    for k in ("sku", "id", "asin", "productId", "product_id"):
        v = _clean(p.get(k))
        if v:
            return v
    # derive from the product URL
    url = _clean(p.get("url"))
    m = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", url)
    if m:
        return m.group(1)
    m = re.search(r"/p/([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    return f"ROW{idx + 1}"


def _price_number(v) -> str:
    s = _clean(v)
    if not s:
        return ""
    m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)", s.replace(" ", ""))
    return m.group(1).replace(",", "") if m else s


def prepare(products: list[dict]) -> dict:
    catalog = load_catalog()
    attr_mapper = SpecAttributeMapper(catalog)
    cat_mapper = CategoryMapper()

    rows: list[dict] = []
    # column order: preserve first-seen order of each category's attributes
    spec_columns: list[dict] = []
    seen_cols: set[str] = set()
    stats = {"products": len(products), "mapped": 0, "unmapped": 0, "attributes_assigned": 0}

    for idx, p in enumerate(products):
        platform = _platform_of(p)
        src_cat = _clean(p.get("source_category_path") or p.get("sourceCategoryPath"))
        resolved = cat_mapper.resolve(platform, src_cat)
        specs = p.get("specifications") or {}

        mapping_note = ""
        mapping_method = ""
        if resolved:
            node = resolved["full"]
            mapping_method = resolved.get("method", "curated")
            attrs = attr_mapper.attrs_for(node)
            mapped = attr_mapper.map_specs(specs, node)
            stats["mapped"] += 1
            # A coarse assignment (no L4 fits) is valid but less specific — say so
            # rather than presenting it as a precise match.
            if resolved.get("level", 4) < 4 or mapping_method.startswith("coarse") \
                    or mapping_method == "curated-coarse":
                mapping_note = (f"coarse category: assigned at L{resolved.get('level')} "
                                f"({mapping_method}) — no exact L4 match")
                stats["coarse"] = stats.get("coarse", 0) + 1
        else:
            node = ""
            attrs = []
            mapped = {"attributes": {}, "fixed": {}, "unmapped": [], "stats": {}}
            stats["unmapped"] += 1
            mapping_note = (
                "platform exposed no category — nothing to map"
                if not src_cat
                else f"no curated mapping for '{src_cat[:60]}' (and no coarse/name match)"
            )

        vals = mapped["attributes"]
        fixed = mapped["fixed"]
        stats["attributes_assigned"] += len(vals)

        # register this category's attribute columns, in the file's own order
        for a in attrs:
            spec = a["specification"]
            if spec in seen_cols:
                continue
            seen_cols.add(spec)
            spec_columns.append({
                "specification": spec,
                "attribute": a["attribute"],
                "leaf": a["leaf"],
                "type": a["type"],
                "mandatory": a["mandatory"],
                "valid": a.get("valid", []),
            })

        dims = _clean(fixed.get("dimensions"))
        dim_parts = [d.strip() for d in dims.split(" x ")] if dims else []
        images = p.get("images") or ([p["imageUrl"]] if p.get("imageUrl") else [])

        row: dict = {
            "Sku *": _sku_for(p, idx),
            "Listing  Type *": _clean(p.get("listing_type")) or "Make an Offer",
            "Category  Name *": node,
            "Brand  Name *": _clean(p.get("brand")) or _clean(fixed.get("brand")),
            "Product  Name *": _clean(p.get("title")),
            "Description *": _html_description(_clean(p.get("description"))),
            "Hsn *": _clean(p.get("hsn")),
            "Tax *": _clean(p.get("gst")).replace("%", ""),
            "Product  Location *": _clean(p.get("location")),
            "Auto  Renewal": "",
            "Start  Date": "",
            "Quantity *": _clean(p.get("quantity")) or "1",
            "Low  Stock  Alert": "",
            "Package  Weight *": _clean(fixed.get("weight")),
            "Package  Height *": dim_parts[2] if len(dim_parts) > 2 else "",
            "Package  Width  *": dim_parts[1] if len(dim_parts) > 1 else "",
            "Package  Length *": dim_parts[0] if dim_parts else "",
            "Relationship *": "Simple",
            "Parent  Sku*": _clean(p.get("sku")) or _sku_for(p, idx),
            "Variantion  Name": _clean(p.get("variants")),
            "Mrp  Price *": _price_number(p.get("price")),
            "Transfer  Price *": "",
            "Meta  Tag  Title": _clean(p.get("title"))[:60],
            "Meta  Tag  Description": _clean(p.get("meta_description")) or _clean(p.get("description"))[:160],
            "Meta  Tag  Keyword": "",
            "Cod": "", "Cod Charge": "",
            "Product Videos": _clean(p.get("video")),
            # audit
            "Mapping Status": (
                "unmapped" if not resolved
                else "ok-coarse" if mapping_note.startswith("coarse")
                else "ok"
            ),
            "Source Category": src_cat,
            "Mapped Category Path": node,
            "Review Notes": mapping_note,
        }
        for i in range(1, 11):
            row[f"Product Image {i}" + (" *" if i == 1 else "")] = images[i - 1] if len(images) >= i else ""
        # attribute values, keyed by specification so they land in the right column
        for a in attrs:
            v = vals.get(a["attribute"])
            if v not in (None, ""):
                row["spec::" + a["specification"]] = v

        rows.append(row)

    return {
        "fixed_columns": FIXED_COLUMNS,
        "spec_columns": spec_columns,
        "audit_columns": AUDIT_COLUMNS,
        "rows": rows,
        "stats": stats,
    }


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read())
    products = payload.get("products") if isinstance(payload, dict) else payload
    print(json.dumps(prepare(products or []), ensure_ascii=False))


if __name__ == "__main__":
    main()
