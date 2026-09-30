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
from collections import OrderedDict
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
    # per-spec column metadata (attribute name, leaf, type, mandatory, valid)
    # so each sheet's headers keep the template's "attribute" spelling
    col_meta: dict[str, dict] = {}
    # per-category declared attribute set — a sheet gets ITS category's
    # attributes (not merely the ones that happened to be populated)
    cat_attrs: dict[str, list[dict]] = {}
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

        if attrs and node not in cat_attrs:
            cat_attrs[node] = list(attrs)
        for a in attrs:
            col_meta.setdefault(a["specification"], {
                "specification": a["specification"],
                "attribute": a["attribute"],
                "leaf": a["leaf"],
                "type": a.get("type"),
                "mandatory": a.get("mandatory"),
                "valid": a.get("valid", []),
            })

        vals = mapped["attributes"]
        fixed = mapped["fixed"]
        stats["attributes_assigned"] += len(vals)

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

    return build_sheets(rows, stats, col_meta, cat_attrs)


def _sheet_name(category: str, used: set[str]) -> str:
    """Excel sheet name for a category.

    Constraints: <= 31 chars, none of [ ] : * ? / \\ , not blank, unique. The
    sheet is named after the deepest level that actually distinguishes it (the
    L4 leaf when there is one, else the node's own last segment).
    """
    leaf = (category.split(" > ")[-1] if category else "Unmapped") or "Unmapped"
    name = re.sub(r"[\[\]:*?/\\]", "-", leaf).strip()[:31] or "Unmapped"
    if name not in used:
        used.add(name)
        return name
    # disambiguate with the parent, then a counter
    parts = [p for p in (category or "").split(" > ") if p]
    for depth in range(len(parts) - 1, -1, -1):
        cand = re.sub(r"[\[\]:*?/\\]", "-", " ".join(parts[depth:])).strip()[:31]
        if cand and cand not in used:
            used.add(cand)
            return cand
    i = 2
    while f"{name[:28]} {i}" in used:
        i += 1
    final = f"{name[:28]} {i}"
    used.add(final)
    return final


def build_sheets(rows: list[dict], stats: dict | None = None,
                 col_meta: dict[str, dict] | None = None,
                 cat_attrs: dict[str, list[dict]] | None = None) -> dict:
    """Group the prepared rows into ONE SHEET PER CATEGORY.

    Each sheet carries the 38 fixed template columns plus only the attribute
    columns that category actually declares — the union across every category
    (the sample file's ~7,000 columns) is never emitted. Column order follows
    Attribute Final's own order for that node.
    """
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for row in rows:
        groups.setdefault(row["Mapped Category Path"] or "", []).append(row)

    used_names: set[str] = set()
    sheets: list[dict] = []
    for category, cat_rows in groups.items():
        # The sheet carries every attribute its category declares (Attribute
        # Final's own order), so mandatory fields stay visible even when the
        # scraped spec set had no value for them. Only if the category resolved
        # to nothing (unmapped) do we fall back to whatever was populated.
        spec_columns: list[dict] = []
        declared = (cat_attrs or {}).get(category)
        if declared:
            spec_columns = [dict(a) for a in declared]
        else:
            seen: set[str] = set()
            for row in cat_rows:
                for key in row:
                    if not key.startswith("spec::"):
                        continue
                    spec = key[len("spec::"):]
                    if spec in seen:
                        continue
                    seen.add(spec)
                    spec_columns.append(dict(col_meta.get(spec) or {"specification": spec}))
            spec_columns.sort(key=lambda c: c["specification"])
        sheets.append({
            "category": category,
            "sheet_name": _sheet_name(category, used_names),
            "fixed_columns": FIXED_COLUMNS,
            "audit_columns": AUDIT_COLUMNS,
            "spec_columns": spec_columns,
            "rows": cat_rows,
        })

    # biggest sheet first, so the most useful one opens first
    sheets.sort(key=lambda s: (-len(s["rows"]), s["category"]))

    summary = {
        "total_products": len(rows),
        "total_categories": len(sheets),
        "categories": [
            {
                "category": s["category"] or "(unmapped)",
                "sheet_name": s["sheet_name"],
                "products": len(s["rows"]),
                "attribute_columns": len(s["spec_columns"]),
            }
            for s in sheets
        ],
    }
    return {
        "summary": summary,
        "sheets": sheets,
        # kept for callers that only want the flat view
        "fixed_columns": FIXED_COLUMNS,
        "audit_columns": AUDIT_COLUMNS,
        "spec_columns": [c for s in sheets for c in s["spec_columns"]],
        "rows": rows,
        "stats": stats or {},
    }


def summarise(products: list[dict]) -> dict:
    """Category breakdown only — cheap enough to call BEFORE exporting so the UI
    can show how many categories the scraped file contains."""
    catalog = load_catalog()
    attr_mapper = SpecAttributeMapper(catalog)
    cat_mapper = CategoryMapper()
    counts: "OrderedDict[str, int]" = OrderedDict()
    for p in products:
        resolved = cat_mapper.resolve(_platform_of(p),
                                      _clean(p.get("source_category_path") or p.get("sourceCategoryPath")))
        node = resolved["full"] if resolved else ""
        counts[node] = counts.get(node, 0) + 1
    used: set[str] = set()
    return {
        "total_products": len(products),
        "total_categories": len(counts),
        "categories": [
            {
                "category": node or "(unmapped)",
                "sheet_name": _sheet_name(node, used),
                "products": n,
                "attribute_columns": len(attr_mapper.attrs_for(node)) if node else 0,
            }
            for node, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read())
    products = payload.get("products") if isinstance(payload, dict) else payload
    if "--summary" in sys.argv:
        print(json.dumps(summarise(products or []), ensure_ascii=False))
        return
    print(json.dumps(prepare(products or []), ensure_ascii=False))


if __name__ == "__main__":
    main()
