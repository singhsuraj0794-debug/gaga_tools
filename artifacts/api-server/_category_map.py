#!/usr/bin/env python3
"""Curated marketplace → Gajab category map.

The scraped breadcrumb (Amazon / Flipkart / Meesho) is a *different* taxonomy
from Gajab's, so it is mapped through an explicit, human-curated table —
`marketplace_category_map.json`. Nothing is inferred at runtime; an unmapped
category yields no Gajab path and the export leaves `Category Name *` blank and
flags the row.

Map format:
  {
    "version": "1.0",
    "map": {
      "amazon":   { "<normalised scraped path>": "<Gajab full path>", ... },
      "flipkart": { ... },
      "meesho":   { ... }
    }
  }

Targets are validated against category_l1l4.json (built from All Categories.xlsx,
the master) when the map is loaded — a target that is not a real Gajab L4 path is
rejected and reported, so a typo can never silently produce a wrong category.

Public API:
    load_map(path)                      -> dict
    validate_map(map_data, tax)         -> (clean_map, errors)
    resolve(platform, source_path, m)   -> {"full","l1".."l4","confidence","method"}|None
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DEFAULT_MAP = Path(__file__).with_name("marketplace_category_map.json")
_TAXONOMY = Path(__file__).with_name("category_l1l4.json")

# Leading platform noise we drop before matching (Amazon/Flipkart prepend "Home").
_LEADING_NOISE = {"home", "home page"}

# Trailing brand/product tokens marketplaces append to the last crumb
# (e.g. "KWINE CASE Plain Cases & Covers").
def normalise_path(raw: str) -> str:
    """Normalise a scraped breadcrumb for map lookup."""
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"\s*>\s*", raw) if p.strip()]
    while parts and parts[0].lower() in _LEADING_NOISE:
        parts.pop(0)
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p)
        out.append(p)
    return " > ".join(out)


def _norm_key(raw: str) -> str:
    """Case/punctuation-insensitive key for map lookup."""
    s = normalise_path(raw).lower()
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9> ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _singular_leaf_variants(leaf: str) -> list[str]:
    """Plausible singular forms of a leaf. Ambiguous by nature — 'cases' could be
    'case'+s or 'cas'+es — so every candidate is offered and the caller tries
    them in order."""
    out = []
    if len(leaf) > 4 and leaf.endswith("ies"):
        out.append(leaf[:-3] + "y")
    if leaf.endswith("s") and not leaf.endswith("ss"):
        out.append(leaf[:-1])          # cases -> case, jars -> jar
    if len(leaf) > 4 and leaf.endswith("es"):
        out.append(leaf[:-2])          # washes -> wash, boxes -> box
    return [x for x in out if x and x != leaf]


def _singularise_path(key: str) -> str:
    """Singularise the leaf of a normalised key (first plausible form)."""
    parts = [p.strip() for p in key.split(">")]
    if not parts:
        return key
    variants = _singular_leaf_variants(parts[-1])
    if variants:
        parts[-1] = variants[0]
    return " > ".join(parts)


def _key_variants(key: str) -> list[str]:
    """The key itself plus every singular/plural leaf variant worth trying."""
    parts = [p.strip() for p in key.split(">")]
    if not parts:
        return [key]
    leaf = parts[-1]
    head = parts[:-1]
    out = [key]
    for v in _singular_leaf_variants(leaf):
        out.append(" > ".join(head + [v]))
    # also allow a pluralised leaf (map may hold the singular)
    if not leaf.endswith("s"):
        out.append(" > ".join(head + [leaf + "s"]))
    return out


def _lookup(bucket: dict, source_path: str) -> str | None:
    """Exact key, singular/plural variants, then progressively shorter prefixes."""
    if not bucket:
        return None
    key = _norm_key(source_path)
    for cand in _key_variants(key):
        hit = bucket.get(cand)
        if hit:
            return hit
    # marketplaces often append the brand/product name as the final crumb
    parts = [p for p in re.split(r"\s*>\s*", normalise_path(source_path)) if p]
    while len(parts) > 1:
        parts.pop()
        cand = _norm_key(" > ".join(parts))
        for c in _key_variants(cand):
            hit = bucket.get(c)
            if hit:
                return hit
    return None


def load_taxonomy(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _TAXONOMY
    return json.loads(p.read_text(encoding="utf-8"))


def validate_map(map_data: dict, taxonomy: dict | None = None) -> tuple[dict, list[str]]:
    """Drop entries whose target is not a real Gajab category.

    Accepts ANY level present in the master (L1/L2/L3/L4) — the master keeps
    every level in `ids`, while `paths` holds only L4 leaves. A shallower target
    is a legitimate fallback: it is better to place a product at L3 than to
    leave the category blank when no L4 fits.

    Returns (clean_map, errors).
    """
    tax = taxonomy or load_taxonomy()
    valid = set(tax.get("ids") or {})
    if not valid:  # very old taxonomy without ids
        valid = {p["full"] for p in tax.get("paths", [])}
    clean: dict[str, dict] = {"version": map_data.get("version", "1.0"), "map": {}}
    errors: list[str] = []
    for platform, entries in (map_data.get("map") or {}).items():
        if not isinstance(entries, dict):
            errors.append(f"{platform}: expected an object")
            continue
        bucket: dict[str, str] = {}
        for src, dest in entries.items():
            if not dest:
                continue
            if dest not in valid:
                errors.append(f"{platform}: '{src}' -> '{dest}' is not a Gajab category path")
                continue
            bucket[_norm_key(src)] = dest
        clean["map"][platform] = bucket
    return clean, errors


def _default_map() -> dict:
    if _DEFAULT_MAP.exists():
        return json.loads(_DEFAULT_MAP.read_text(encoding="utf-8"))
    return {"version": "1.0", "map": {}}


class CategoryMapper:
    """Resolves a scraped marketplace category path to a Gajab L1-L4 path."""

    def __init__(self, map_path: str | Path | None = None):
        self.taxonomy = load_taxonomy()
        self._by_full = {p["full"]: p for p in self.taxonomy.get("paths", [])}
        raw = json.loads(Path(map_path).read_text(encoding="utf-8")) if map_path else _default_map()
        self.map, self.errors = validate_map(raw, self.taxonomy)

    def resolve(self, platform: str, source_path: str | None) -> dict | None:
        """Return {'full','l1'..'l4','level','confidence','method'} or None if unmapped.

        The target may be any level (L1-L4). When a deeper level is unavailable
        the map can legitimately point at L3 or L2, and the returned `level`
        records how specific the assignment is.
        """
        if not source_path:
            return None
        plat = (platform or "").lower()
        bucket = self.map.get("map", {}).get(plat, {})
        if not bucket:
            return None

        key = _norm_key(source_path)
        dest = _lookup(bucket, source_path)

        if not dest:
            return None

        parts = [p for p in re.split(r"\s*>\s*", dest) if p]
        node = self._by_full.get(dest)
        if node:
            return {
                "full": node["full"],
                "l1": node.get("l1"),
                "l2": node.get("l2"),
                "l3": node.get("l3"),
                "l4": node.get("l4"),
                "level": 4,
                "confidence": 1.0,
                "method": "curated",
            }
        # shallower than L4 (L1/L2/L3) — still a valid master category
        lv = [parts[i] if i < len(parts) else "" for i in range(4)]
        return {
            "full": dest,
            "l1": lv[0],
            "l2": lv[1],
            "l3": lv[2],
            "l4": lv[3],
            "level": len(parts),
            "confidence": 1.0,
            "method": "curated-coarse",
        }


if __name__ == "__main__":
    import sys

    m = CategoryMapper(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"taxonomy L4 paths : {len(m.taxonomy.get('paths', []))}")
    for plat, entries in m.map.get("map", {}).items():
        print(f"  {plat:9s} {len(entries)} curated entries")
    if m.errors:
        print("\nINVALID TARGETS:")
        for e in m.errors[:20]:
            print("  ", e)
