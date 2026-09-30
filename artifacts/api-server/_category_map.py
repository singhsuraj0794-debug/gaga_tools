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


# ── Coarse fallback ────────────────────────────────────────────────────────
# When nothing in the curated map matches, a product must still get a category.
# The marketplace's TOP-LEVEL name is mapped to a Gajab L1 (curated, small and
# stable), then we try to descend to the deepest Gajab L2/L3 whose name the
# scraped breadcrumb actually mentions. The result is a coarser but valid master
# category, and the export marks it "ok-coarse" so it is visibly less specific.
_L1_ALIASES: dict[str, str] = {
    # Meesho
    "home & kitchen": "Home & Kitchen",
    "kitchen utility": "Home & Kitchen",
    "office supplies & stationery": "Stationery",
    "stationery": "Stationery",
    "beauty & health": "Beauty & Health Care",
    "unisex personal care": "Beauty & Health Care",
    "mens personal care & grooming": "Beauty & Health Care",
    "women personal care & makeup": "Beauty & Health Care",
    "health & beauty": "Beauty & Health Care",
    "women": "Fashion",
    "women western": "Fashion",
    "men": "Fashion",
    "lingerie": "Fashion",
    "ethnic": "Fashion",
    "footwear": "Fashion",
    "jewellery & accessories": "Fashion Accessories",
    "bags": "Luggage & Bags",
    "kids clothing": "Kids & Baby",
    "kids & toys": "Toys & Games",
    "electronic accessories": "Electronics",
    "electronics": "Electronics",
    "car & bike accessories": "Automobile Accessories",
    "furniture": "Furniture",
    "grocery": "Grocery",
    "watches": "Fashion Accessories",
    "sports & fitness": "Sports & Fitness",
    "books": "Books & General Merchandise",
    "music": "Toys & General Merchandise",
    # Amazon
    "beauty": "Beauty & Health Care",
    "health & personal care": "Beauty & Health Care",
    "computers & accessories": "Electronics",
    "office products": "Stationery",
    "sports, fitness & outdoors": "Sports & Fitness",
    "bags, wallets and luggage": "Luggage & Bags",
    "home improvement": "Home & Kitchen",
    "tools & home improvement": "Home & Kitchen",
    "outdoor living": "Home & Kitchen",
    "toys & games": "Toys & Games",
    "baby": "Kids & Baby",
    "musical instruments": "Toys & General Merchandise",
    "grocery & gourmet foods": "Grocery",
    "industrial & scientific": "Industrial & Scientific",
    # Flipkart
    "home": "Home & Kitchen",
    "mobiles & accessories": "Electronics",
    "tvs & appliances": "Electronics",
    "baby & kids": "Kids & Baby",
    "sports, books & more": "Sports & Fitness",
    "beauty & personal care": "Beauty & Health Care",
}


def _build_l1_norm() -> None:
    _L1_ALIASES_NORM.clear()
    for k, v in _L1_ALIASES.items():
        n = " ".join(str(k).lower().replace("&", "and").split())
        if n:
            _L1_ALIASES_NORM[n] = v


# Normalised alias keys — built once so '&'/'and'/punctuation can never miss.
_L1_ALIASES_NORM: dict[str, str] = {}


_build_l1_norm()



# Marketplace crumbs that carry no product meaning. Meesho files whole swathes
# of its catalogue under a bare "Others" — descending that gives garbage (and
# the master taxonomy has leaves literally named "Others"), so these products
# are routed by their TITLE instead.
_GENERIC_PLACEHOLDERS = {
    "others", "other", "misc", "miscellaneous", "mixed", "general",
    "unknown", "unspecified", "n/a", "na", "all products", "products",
    "no category", "not specified",
}

# High-precision title keywords -> exact taxonomy path. Only used when the
# breadcrumb itself is unusable (generic placeholder, or nothing matched).
# Every target is validated against category_l1l4.json at load time; a typo
# fails loudly rather than silently mis-filing a product.
_TITLE_KEYWORDS: list[tuple[str, str]] = [
    (r"\bovulation|\blh\s*test|\bfertility\s*test|\bovu\s*kit",
     "Beauty & Health Care > Health & Beauty > Sexual Wellness > Fertility Kits"),
    (r"\bpregnancy\s*test|\bpregnancy\s*kit|\bhcg\s*test",
     "Beauty & Health Care > Health & Beauty > Sexual Wellness > Pregnancy Kits"),
    (r"\burine\s*test|\bdrug\s*test|\bdip\s*kit|\btest\s*strip",
     "Beauty & Health Care > Medical Supplies > Health Monitors > Health Test Kit"),
]


def _last_crumb(path: str) -> str:
    parts = [x for x in re.split(r"\s*>\s*", path or "") if x]
    return parts[-1] if parts else ""


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _is_generic(path: str) -> bool:
    return _norm_name(_last_crumb(path)) in _GENERIC_PLACEHOLDERS

class CategoryMapper:
    """Resolves a scraped marketplace category path to a Gajab L1-L4 path."""

    def __init__(self, map_path: str | Path | None = None):
        self.taxonomy = load_taxonomy()
        self._by_full = {p["full"]: p for p in self.taxonomy.get("paths", [])}
        raw = json.loads(Path(map_path).read_text(encoding="utf-8")) if map_path else _default_map()
        self.map, self.errors = validate_map(raw, self.taxonomy)
        # Candidate targets for the coarse descent: EVERY valid path, deepest
        # first. Restricting this to L1/L2/L3 meant a breadcrumb ending in a
        # product noun that Gajab HAS as an L4 ('Artificial Flowers',
        # 'First Aid Kit') could never reach it and stopped at the L1.
        self._coarse: list[str] = sorted(
            (self.taxonomy.get("ids") or {}),
            key=lambda p: -p.count(" > "),
        )
        # name -> full path, for single-level category names (Meesho supplies
        # `catalog.sub_sub_category_name` when its breadcrumb comes back empty).
        self._by_name: dict[str, str] = {}
        for path in (self.taxonomy.get("ids") or {}):
            leaf = path.split(" > ")[-1]
            n = _norm_key(leaf)
            if not n:
                continue
            # prefer the DEEPEST path for a given name (most specific category)
            cur = self._by_name.get(n)
            if cur is None or path.count(" > ") > cur.count(" > "):
                self._by_name[n] = path
        self._warned: set[str] = set()

    def resolve_by_title(self, title: str | None) -> dict | None:
        """Last resort when the breadcrumb says nothing usable.

        Deliberately keyword-based and high-precision: a wrong category pulls
        the wrong attribute set, so only product nouns that map to exactly one
        place in the taxonomy are listed. Returns None for anything ambiguous.
        """
        t = (title or "").lower()
        if not t:
            return None
        for rx, path in _TITLE_KEYWORDS:
            if path not in self._by_full:
                continue  # stale target — ignore rather than mis-file
            if re.search(rx, t):
                return self._result_for(path, "title-keyword", 0.6)
        return None

    def resolve_by_name(self, name: str) -> dict | None:
        """Resolve a bare category NAME (no parents) to a Gajab path.

        Used when a platform gives only a leaf name — e.g. Meesho's
        `sub_sub_category_name` ('Kitchen Napkins'). Exact name, then
        singular/plural variants, then a multi-word containment match.
        """
        if not name or ">" in name:
            return None
        n = _norm_key(name)
        if not n:
            return None
        cand = self._by_name.get(n)
        if not cand:
            for v in _key_variants(n):
                cand = self._by_name.get(v)
                if cand:
                    break
        if not cand and len(n.split()) >= 2:
            for k, v in self._by_name.items():
                if len(k.split()) >= 2 and (n in k or k in n):
                    cand = v
                    break
        # NOTE: a looser "head noun" match was tried here and REMOVED — it
        # produced confident nonsense ('Mop Sticks' -> 'Lip Care',
        # 'Diwali Lightings' -> 'Automation & Robotics'). A wrong category pulls
        # the wrong attribute set, which is worse than leaving the row blank and
        # flagged, so anything short of an exact/multi-word match is left for the
        # curated map.
        if not cand:
            return None
        lv = cand.split(" > ")
        return {
            "full": cand,
            "l1": lv[0],
            "l2": lv[1] if len(lv) > 1 else "",
            "l3": lv[2] if len(lv) > 2 else "",
            "l4": lv[3] if len(lv) > 3 else "",
            "level": len(lv),
            "confidence": 0.6,
            "method": "name",
        }

    def _coarse_candidates(self, l1: str) -> list[str]:
        return [p for p in self._coarse if p == l1 or p.startswith(l1 + " > ")]

    def _l1_alias(self, raw_l1: str) -> str | None:
        """Marketplace top-level -> Gajab L1. Keys are normalised once at import
        so '&' vs 'and' and punctuation can never cause a silent miss."""
        norm = _norm_key(raw_l1)
        hit = _L1_ALIASES_NORM.get(norm)
        if hit:
            return hit
        for k, v in _L1_ALIASES_NORM.items():
            if k and (k in norm or norm in k):
                return v
        return None

    def resolve_coarse(self, platform: str, source_path: str | None) -> dict | None:
        """Coarse but valid placement, in two passes.

        1. LEAF match across the WHOLE taxonomy: the marketplace's last crumb is
           the product noun ('Wall Clocks', 'Artificial Flowers', 'First Aid
           kits'). If a Gajab leaf matches it, that is the right home — and this
           works across L1s, which the old L1-descend could never do (Meesho puts
           'First Aid kits' under Grocery; Gajab keeps it under Health Care).
        2. Otherwise descend inside the L1 the marketplace top level maps to.
        """
        if not source_path:
            return None
        parts = [p.strip() for p in re.split(r"\s*>\s*", source_path) if p.strip()]
        if not parts:
            return None

        # ── pass 1: leaf noun vs Gajab leaves ──────────────────────────────
        leaf_crumb = _norm_key(parts[-1])
        if len(leaf_crumb.split()) >= 2:
            variants = _key_variants(leaf_crumb)
            found = None
            # exact leaf equality first (deepest path wins), then containment
            for want_exact in (True, False):
                for cand in self._coarse:          # deepest first
                    cand_leaf = _norm_key(cand.split(" > ")[-1])
                    if want_exact:
                        if cand_leaf in variants:
                            found = cand
                            break
                    elif any(v in cand_leaf or cand_leaf in v for v in variants if len(v) >= 6):
                        found = cand
                        break
                if found:
                    break
            if found:
                return self._result_for(found, "coarse-leaf", 0.7)

        # ── pass 2: descend inside the mapped L1 ───────────────────────────
        gajab_l1 = self._l1_alias(parts[0])
        if not gajab_l1:
            return None
        from_here_norm = {_norm_key(p) for p in parts[1:]}
        phrases = [c for c in from_here_norm if len(c.split()) >= 2]
        chosen = gajab_l1
        if phrases:
            for cand in self._coarse_candidates(gajab_l1):
                norm_cand = _norm_key(cand)
                if any(ph in norm_cand for ph in phrases):
                    chosen = cand
                    break
        return self._result_for(chosen, "coarse", 0.4)

    def _result_for(self, full: str, method: str, confidence: float) -> dict:
        lv = full.split(" > ")
        return {
            "full": full,
            "l1": lv[0],
            "l2": lv[1] if len(lv) > 1 else "",
            "l3": lv[2] if len(lv) > 2 else "",
            "l4": lv[3] if len(lv) > 3 else "",
            "level": len(lv),
            "confidence": confidence,
            "method": method,
        }

    def resolve(self, platform: str, source_path: str | None, allow_coarse: bool = True,
                 title: str | None = None) -> dict | None:
        """Return {'full','l1'..'l4','level','confidence','method'} or None.

        Order: curated exact/L4 (then L3/L2) -> curated-coarse -> mechanical
        coarse (marketplace L1 -> Gajab L1, descended as far as the breadcrumb
        names). A product should almost always leave with SOME valid category.
        """
        src = (source_path or "").strip()
        title = title or ""
        plat = (platform or "").lower()

        # A generic placeholder ("Others") carries no signal, so descending it
        # would attach an arbitrary branch's attributes. Route those by title.
        if not src or _is_generic(src):
            by_title = self.resolve_by_title(title) if title else None
            if by_title:
                return by_title
            if src and src not in self._warned:
                self._warned.add(src)
            return None

        bucket = self.map.get("map", {}).get(plat, {})
        dest = _lookup(bucket, src) if bucket else None

        if not dest:
            # Single-level name (no parents) — e.g. Meesho's
            # catalog.sub_sub_category_name when its breadcrumb is empty.
            if ">" not in src:
                named = self.resolve_by_name(src)
                if named:
                    return named
            coarse = self.resolve_coarse(plat, src) if allow_coarse else None
            if coarse is None:
                # Nothing in the breadcrumb mapped: a precise title keyword
                # beats leaving the row unmapped.
                by_title = self.resolve_by_title(title) if title else None
                if by_title:
                    return by_title
                if src not in self._warned:
                    self._warned.add(src)
                return None
            return coarse

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
            "confidence": 0.9,
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
