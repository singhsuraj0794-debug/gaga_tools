#!/usr/bin/env python3
"""Map scraped specifications onto Gajab's category attributes.

Scraped spec keys are marketplace-specific ('Colour', 'Material Type',
'product_weight', 'capacity'), while a Gajab attribute is named like 'color',
'material', 'net quantity (Unit Value)', 'capacity (UOM)'. This module bridges
the two, in the same spirit as the category map: a deterministic normalisation
step plus a CURATED alias table — never a guess.

Resolution order for a scraped key:
  1. normalise (lowercase, '_'/'&'/punctuation -> space, collapse)
  2. exact attribute-name match
  3. curated alias (spec_attribute_map.json)
  4. unit-pair inference: 'X' -> 'X (Unit Value)' + 'X (UOM)' when the category
     declares that pair (e.g. 'capacity'
     "1500" -> capacity (Unit Value)=1500)

Anything unresolved is reported, so an unmapped spec is visible rather than
silently dropped.

Public API:
    normalise_key(k)                       -> str
    SpecAttributeMapper(catalog)           -> mapper
    mapper.map_specs(specs, node_path)     -> {"attributes": {...}, "unmapped": [...], "stats": {...}}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DEFAULT_ALIASES = Path(__file__).with_name("spec_attribute_map.json")

# Fields that belong in the template's FIXED columns, not in category attributes.
FIXED_COLUMN_KEYS = {
    "product weight": "weight",
    "package weight": "weight",
    "item weight": "weight",
    "product length": "dim_length",
    "product length value": "dim_length",
    "product height": "dim_height",
    "product height value": "dim_height",
    "product breadth": "dim_breadth",
    "product width": "dim_width",
    "brand": "brand",
    "brand name": "brand",
}

# scraped key -> the shared unit token that applies to the dimension columns
_DIMENSION_UNIT_KEYS = {"product unit", "product dimension unit", "dimension unit"}

# Common unit tokens, used to split '1500 ml' style values.
_UOM = {
    "g": "g", "gram": "g", "grams": "g", "gm": "g", "gms": "g",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "millilitres": "ml",
    "l": "L", "lt": "L", "ltr": "L", "litre": "L", "liter": "L", "litres": "L",
    "cm": "cm", "centimetre": "cm", "centimeter": "cm",
    "m": "m", "mm": "mm", "ft": "ft", "feet": "ft", "inch": "inch", "in": "inch",
    "pcs": "pieces/units", "pc": "pieces/units", "piece": "pieces/units",
    "pieces": "pieces/units", "count": "pieces/units", "unit": "pieces/units",
    "units": "pieces/units", "pack": "pieces/units", "packs": "pieces/units",
    "sq ft": "sq ft", "sq m": "sq m", "cu ft": "cu ft",
}

_MULTI = {
    "x": " ", "×": " ", "_": " ", "-": " ",
}


def normalise_key(k: str) -> str:
    s = str(k or "").lower().strip()
    s = s.replace("&", " and ")
    for a, b in _MULTI.items():
        s = s.replace(a, b)
    s = s.replace("(", " ").replace(")", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def split_value_unit(value: str) -> tuple[str | None, str | None]:
    """'1500 ml' -> ('1500', 'ml'); '2 Count' -> ('2', 'pieces/units')."""
    v = str(value or "").strip()
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z .]+)$", v)
    if not m:
        num = re.match(r"^([0-9]+(?:\.[0-9]+)?)$", v)
        return (num.group(1), None) if num else (None, None)
    num, unit = m.group(1), m.group(2).strip().lower()
    return num, _UOM.get(unit, unit)


def load_aliases(path: str | Path | None = None) -> dict[str, list[str]]:
    """Curated aliases. A value may be a single target or a candidate list —
    the first candidate the category actually declares wins (e.g. 'type' is
    'product form' on Face Washes but 'model or style' on a Container)."""
    p = Path(path) if path else _DEFAULT_ALIASES
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out: dict[str, list[str]] = {}
        for k, v in (data.get("aliases") or {}).items():
            key = normalise_key(k)
            if isinstance(v, str):
                out[key] = [v] if v else []
            elif isinstance(v, list):
                out[key] = [str(x) for x in v]
            else:
                out[key] = []
        return out
    except Exception:
        return {}


_INT_ATTRS = ("integer",)
_DATE_ATTRS = ("date",)


def coerce_value(entry: dict, value: str) -> str:
    """Coerce a scraped string to the attribute's declared type where sensible.

    The master declares e.g. net quantity (Unit Value) as Integer and
    shelf life (in days) as Integer, but marketplaces send 'Pack Of 1' and
    '18 Months'.
    """
    t = (entry.get("type") or "").lower()
    v = str(value).strip()

    if any(k in t for k in _INT_ATTRS):
        # '18 Months' -> 540 days when the attribute is a *days* attribute
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(month|months|year|years|day|days)?", v, re.I)
        if m:
            n = float(m.group(1))
            unit = (m.group(2) or "").lower()
            if "day" in (entry.get("attribute", "") + entry.get("specification", "")).lower():
                if unit.startswith("month"):
                    n *= 30
                elif unit.startswith("year"):
                    n *= 365
            return str(int(n)) if n == int(n) else str(n)
        return v

    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", v)
    if m and ("unit value" in (entry.get("attribute") or "").lower()):
        return m.group(1)

    return v


class SpecAttributeMapper:
    def __init__(self, catalog: dict, alias_path: str | Path | None = None):
        self.catalog = catalog
        self.by_path: dict[str, list[dict]] = catalog.get("by_path", {})
        self.aliases = load_aliases(alias_path)
        # normalised attribute name -> canonical attribute name
        self.attr_index: dict[str, str] = {}
        for name in (catalog.get("attribute_names") or {}):
            self.attr_index[normalise_key(name)] = name

    # -- helpers ---------------------------------------------------------
    def _attrs_for(self, node_path: str) -> list[dict]:
        """Attributes for a node, walking UP the path when it has none.

        Attribute Final defines attributes on 2,620 L4 leaves and only 496 L3
        nodes — L1/L2 have none. So a coarse (L2/L3) category target would
        otherwise yield zero attribute columns; falling back to the nearest
        ancestor that defines any keeps the export useful.
        """
        if not node_path:
            return []
        parts = [p for p in node_path.split(" > ") if p]
        while parts:
            hit = self.by_path.get(" > ".join(parts))
            if hit:
                return hit
            parts.pop()
        return []

    def attrs_for(self, node_path: str, with_source: bool = False):
        return self._attrs_for(node_path)

    @staticmethod
    def _valid_values(entry: dict) -> list[str]:
        return entry.get("valid") or []

    def _match_valid(self, entry: dict, value: str) -> str | None:
        """If the attribute has enumerated values, snap the scraped value to one."""
        vals = self._valid_values(entry)
        if not vals:
            return None
        nv = normalise_key(value)
        for v in vals:
            if normalise_key(v) == nv:
                return v
        for v in vals:
            cv = normalise_key(v)
            if cv and (cv in nv or nv in cv):
                return v
        return None

    # -- main ------------------------------------------------------------
    def map_specs(self, specs: dict, node_path: str) -> dict:
        attrs = self._attrs_for(node_path)
        by_name = {normalise_key(a["attribute"]): a for a in attrs}
        # unit pairs, e.g. 'capacity (Unit Value)' + 'capacity (UOM)'
        pairs: dict[str, dict] = {}
        for a in attrs:
            m = re.match(r"^(.*?)\s*\((unit value|uom)\)$", normalise_key(a["attribute"]))
            if m:
                pairs.setdefault(m.group(1).strip(), {})[m.group(2)] = a

        out: dict[str, str] = {}
        unmapped: list[dict] = []
        used: set[str] = set()
        # unit companions (product_weight_unit, product_unit ...) are folded into
        # the value they qualify, so 'weight=0.2' + 'Kg' -> '0.2 Kg'.
        unit_suffix: dict[str, str] = {}
        dim_unit = ""
        specs_l = {normalise_key(k): v for k, v in (specs or {}).items()}
        for k, v in specs_l.items():
            if k in _DIMENSION_UNIT_KEYS:
                dim_unit = str(v or "").strip()
                continue
            for base in ("product weight", "item weight"):
                if k == f"{base} unit" or k == f"{base} uom":
                    unit_suffix[base] = str(v or "").strip()

        dims: dict[str, str] = {}
        for raw_key, raw_val in (specs or {}).items():
            key = normalise_key(raw_key)
            val = "" if raw_val is None else str(raw_val).strip()
            if not val:
                continue

            if key in _DIMENSION_UNIT_KEYS:
                used.add(key)
                continue

            # fixed template column, not a category attribute
            if key in FIXED_COLUMN_KEYS:
                col = FIXED_COLUMN_KEYS[key]
                if col.startswith("dim_"):
                    dims[col] = f"{val} {dim_unit}".strip()
                else:
                    if key in unit_suffix:
                        val = f"{val} {unit_suffix[key]}".strip()
                    out["_fixed_" + col] = val
                used.add(key)
                continue

            # the unit companion itself is not a standalone attribute
            if any(key == f"{b} unit" or key == f"{b} uom" for b in
                   ("product weight", "item weight")):
                used.add(key)
                continue

            # exact attribute name, then curated candidates (first one the
            # category declares wins)
            target = by_name.get(key)
            if target is None and key in self.aliases:
                for cand in self.aliases[key]:
                    target = by_name.get(normalise_key(cand))
                    if target is not None:
                        break
            if target is not None:
                snapped = self._match_valid(target, val)
                out[target["attribute"]] = snapped or coerce_value(target, val)
                used.add(key)
                continue

            # unit pair: 'capacity' -> capacity (Unit Value) + capacity (UOM)
            if key in pairs:
                num, uom = split_value_unit(val)
                pair = pairs[key]
                if num and "unit value" in pair:
                    out[pair["unit value"]["attribute"]] = num
                if uom and "uom" in pair:
                    uv = pair["uom"]
                    snapped = self._match_valid(uv, uom)
                    out[uv["attribute"]] = snapped or uom
                elif "unit value" in pair and "uom" not in pair:
                    pass
                used.add(key)
                continue

            # a scraped value that already carries a unit ('product_weight_unit')
            num, uom = split_value_unit(val)
            if uom and f"{key} uom" in by_name:
                out[by_name[f"{key} uom"]["attribute"]] = uom
                used.add(key)
                continue

            unmapped.append({"key": raw_key, "value": val, "reason": "no matching Gajab attribute"})

        # Assemble Package Length x Width x Height as one dimension string.
        if dims:
            order = ["dim_length", "dim_width", "dim_breadth", "dim_height"]
            parts = [dims[k] for k in order if k in dims]
            # de-duplicate identical values (marketplaces often repeat one axis)
            uniq = []
            for part in parts:
                if part not in uniq:
                    uniq.append(part)
            out["_fixed_dimensions"] = " x ".join(uniq)

        return {
            "attributes": {k: v for k, v in out.items() if not k.startswith("_fixed_")},
            "fixed": {k[len("_fixed_"):]: v for k, v in out.items() if k.startswith("_fixed_")},
            "unmapped": unmapped,
            "stats": {
                "node_path": node_path,
                "category_attributes": len(attrs),
                "mandatory": sum(1 for a in attrs if a.get("mandatory")),
                "specs_in": len(specs or {}),
                "mapped": len([k for k in out if not k.startswith("_fixed_")]),
                "fixed_columns": len([k for k in out if k.startswith("_fixed_")]),
                "unmapped": len(unmapped),
            },
        }


if __name__ == "__main__":
    import sys

    catalog_path = sys.argv[1] if len(sys.argv) > 1 else "gajab_attributes.json"
    node = sys.argv[2] if len(sys.argv) > 2 else ""
    mapper = SpecAttributeMapper(json.loads(Path(catalog_path).read_text(encoding="utf-8")))
    print(f"attribute names indexed : {len(mapper.attr_index)}")
    print(f"curated aliases         : {len(mapper.aliases)}")
    if node:
        print(f"attributes on node      : {len(mapper._attrs_for(node))}")
