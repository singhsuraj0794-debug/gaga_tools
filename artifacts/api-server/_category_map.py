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
import unicodedata
from functools import lru_cache
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


@lru_cache(maxsize=None)
def _norm_key(raw: str) -> str:
    """Case/punctuation-insensitive key for map lookup."""
    s = normalise_path(raw).lower()
    s = s.replace("&", "and")
    # Fold accents BEFORE the non-[a-z] strip below. Without this 'Décor' is
    # mangled into the two junk tokens 'd' + 'cor' (the 'é' is replaced by a
    # space), which can never match the taxonomy's 'decor' — so every Amazon
    # 'Home & Décor' breadcrumb failed every overlap test and stalled at the
    # bare L1 with no attributes.
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )
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



# Words too generic to justify a cross-category match on their own. 'Water
# Treatments' (aquarium) used to land on Grocery > Beverages > Water purely
# because both contain "water".
_GENERIC_TOKENS = {
    "and", "or", "the", "of", "for", "in", "on", "with", "to", "a", "an",
    "care", "supplies", "supply", "accessories", "accessory", "items",
    "products", "product", "general", "multi", "pack", "packs", "sets",
    "new", "best", "quality", "water", "home", "kit", "kits", "style",
    "type", "uses", "all", "other", "others", "misc", "professional",
    "premium", "combo", "value", "size", "colors", "colour", "colors_",
}


# Irregular plurals the crude suffix stemmer cannot relate. Amazon's
# "Keyboards, Mice & Input Devices > Mice" breadcrumb never matched Gajab's
# "… > Computer Accessories > Mouse" leaf (mice -> mic vs mouse -> mouse), so
# these products stalled at the bare L1.
_IRREGULAR = {
    "mice": "mouse", "geese": "goose", "feet": "foot", "teeth": "tooth",
    "children": "child", "people": "person", "oxen": "ox",
    "indices": "index", "matrices": "matrix", "vertices": "vertex",
    "analyses": "analysis", "crises": "crisis", "theses": "thesis",
    "cacti": "cactus", "fungi": "fungus", "alumni": "alumnus",
}


@lru_cache(maxsize=None)
def _stem(token: str) -> str:
    """Crude singular stem so 'repellents' and 'repellent' compare equal."""
    t = token.lower()
    if t in _IRREGULAR:
        return _IRREGULAR[t]
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("es") and not t.endswith("ss"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


@lru_cache(maxsize=None)
def _significant(text: str) -> set:
    """Distinctive, stemmed tokens of a crumb/path — used for overlap scoring."""
    out = set()
    for raw in _norm_key(text).split():
        t = _stem(raw)
        if len(t) >= 3 and t not in _GENERIC_TOKENS:
            out.add(t)
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


def _leafful_sets(tax: dict) -> tuple[set, set, set]:
    """(L4 leaves, leaves + their ancestors, L1s owning at least one L4 leaf).

    The master workbook keeps every branch node in `ids`, including ACTIVE
    branches that hold no products at all — `Toys & Games`, `Luggage & Bags`,
    `Sporting Goods`, `Arts & Crafts`, plus the Inactive `General Merchandise`
    and `Sports & Fitness`. Those are legal ids but they own no L4 leaf, so a
    row landing on one exports a sheet with ZERO attribute columns and cannot
    be listed. Everything that validates a target (the curated map, the L1
    alias table, the coarse descent) uses this set instead of raw `ids`.
    """
    leaves: set[str] = set()
    for p in tax.get("paths", []):
        v = p.get("full") or p.get("path")
        if v:
            leaves.add(v)
    if not leaves:  # very old taxonomy without paths
        ids = set(tax.get("ids") or {})
        prefixes = set()
        for p in ids:
            parts = p.split(" > ")
            for i in range(1, len(parts)):
                prefixes.add(" > ".join(parts[:i]))
        leaves = {p for p in ids if p not in prefixes}
    leafful = set(leaves)
    for p in leaves:
        parts = p.split(" > ")
        for i in range(1, len(parts)):
            leafful.add(" > ".join(parts[:i]))
    return leaves, leafful, {p.split(" > ")[0] for p in leaves}


def validate_map(map_data: dict, taxonomy: dict | None = None) -> tuple[dict, list[str]]:
    """Drop entries whose target is not a real Gajab category.

    Accepts ANY level that owns an L4 leaf (L1/L2/L3 ancestors and the L4
    itself): a shallower target is a legitimate fallback — better to place a
    product at L3 than to leave the category blank when no L4 fits. Branches
    with no L4 underneath are rejected, because they carry no attributes and
    the product could not be listed from that sheet.

    Returns (clean_map, errors).
    """
    tax = taxonomy or load_taxonomy()
    _, valid, _ = _leafful_sets(tax)
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
                errors.append(
                    f"{platform}: '{src}' -> '{dest}' is not a Gajab category path "
                    f"(or owns no L4 leaf)"
                )
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
    "jewellery & accessories": "Fashion",
    "bags": "Fashion",
    "kids clothing": "Kids & Baby",
    "kids & toys": "Toys & General Merchandise",
    "electronic accessories": "Electronics",
    "electronics": "Electronics",
    "car & bike accessories": "Automobile Accessories",
    "furniture": "Furniture",
    "grocery": "Grocery",
    "watches": "Fashion",
    "sports & fitness": "Toys & General Merchandise",
    "books": "Toys & General Merchandise",
    "music": "Toys & General Merchandise",
    # Amazon
    "beauty": "Beauty & Health Care",
    "health & personal care": "Beauty & Health Care",
    "computers & accessories": "Electronics",
    "office products": "Stationery",
    "sports, fitness & outdoors": "Toys & General Merchandise",
    "bags, wallets and luggage": "Fashion",
    "home improvement": "Home & Kitchen",
    "tools & home improvement": "Home & Kitchen",
    "outdoor living": "Home & Kitchen",
    "toys & games": "Toys & General Merchandise",
    "baby": "Kids & Baby",
    "musical instruments": "Toys & General Merchandise",
    "grocery & gourmet foods": "Grocery",
    "industrial & scientific": "Toys & General Merchandise",
    "car & motorbike": "Automobile Accessories",
    "automobiles": "Automobile Accessories",
    "pet supplies": "Toys & General Merchandise",
    # Flipkart
    "home": "Home & Kitchen",
    "mobiles & accessories": "Electronics",
    "tvs & appliances": "Electronics",
    "baby & kids": "Kids & Baby",
    "sports, books & more": "Toys & General Merchandise",
    "beauty & personal care": "Beauty & Health Care",
    # Flipkart prefixes every breadcrumb with "Home"; the real top level is the
    # department after it, so these are the department aliases (added when the
    # leading-"Home" strip exposed them).
    "kitchen & dining": "Home & Kitchen",
    "kitchen, cookware & serveware": "Home & Kitchen",
    "home & furniture": "Home & Kitchen",
    "furnishing": "Home & Kitchen",
    "appliances": "Home & Kitchen",
    "home appliances": "Home & Kitchen",
    "kitchen appliances": "Home & Kitchen",
    "computers": "Electronics",
    "computer peripherals": "Electronics",
    "audio & video": "Electronics",
    "camera & accessories": "Electronics",
    "automotive": "Automobile Accessories",
    "auto & accessories": "Automobile Accessories",
    "books & media": "Toys & General Merchandise",
    "kids accessories": "Kids & Baby",
    "home furnishing": "Home & Kitchen",
    "home decor": "Home & Kitchen",
    "toys": "Toys & General Merchandise",
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
        # first — including Active branches that own no L4 yet (an L3 such as
        # 'Toys & General Merchandise > Toys > Action Figures' is a real
        # assignment target). A node without attribute rows is no longer a
        # problem: SpecAttributeMapper.attrs_for() falls back to the catalog
        # core set, so no sheet is ever attribute-less.
        ids = self.taxonomy.get("ids") or {}
        self._coarse: list[str] = sorted(ids, key=lambda p: -p.count(" > "))
        # Deepest path for each name — used for the title-leaf pass.
        prefixes: set[str] = set()
        for p in ids:
            parts = p.split(" > ")
            for i in range(1, len(parts)):
                prefixes.add(" > ".join(parts[:i]))
        self._leaf_paths: list[str] = sorted(p for p in ids if p not in prefixes)
        # L1s that own at least one L4 leaf: the only safe L1 FALLBACK, since
        # these are the branches the attribute master actually populates.
        _, _, self._valid_l1 = _leafful_sets(self.taxonomy)
        # name -> full path, for single-level category names (Meesho supplies
        # `catalog.sub_sub_category_name` when its breadcrumb comes back empty).
        self._by_name: dict[str, str] = {}
        for path in self._leaf_paths:
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
        so '&' vs 'and' and punctuation can never cause a silent miss.

        Only returns an L1 that actually owns L4 leaves — the alias table is
        checked against the taxonomy so a stale entry pointing at a branch with
        no products (and hence no attributes) can never reach the export."""
        norm = _norm_key(raw_l1)
        hit = _L1_ALIASES_NORM.get(norm)
        if hit in self._valid_l1:
            return hit
        for k, v in _L1_ALIASES_NORM.items():
            if k and (k in norm or norm in k):
                if v in self._valid_l1:
                    return v
        return None

    def _best_by_overlap(self, candidates, crumbs, title: str = "") -> str | None:
        """Deepest candidate sharing distinctive tokens with the breadcrumb.

        Ties are broken by (a) whether the LAST crumb — the product noun — is
        matched, then (b) overlap with the product title, then (c) depth. The
        title matters: "Pest Control > Insect Control" alone ties between
        Insect Repellents / Electric Insect Killer / Insect Net, and a product
        named "Termite Repellent Spray" settles it.
        """
        if not crumbs:
            return None
        want = set()
        for c in crumbs:
            want |= _significant(c)
        if not want:
            return None
        last = _significant(crumbs[-1])
        title_toks = _significant(title) if title else set()

        best, best_key, best_overlap = None, None, set()
        for cand in candidates:
            ptoks = _significant(cand)
            score = len(want & ptoks)
            if not score:
                continue
            leaf_toks = _significant(cand.split(" > ")[-1])
            # How precisely the candidate's OWN leaf names the product noun.
            # 'Candles' and 'Candle Mould' both contain 'candl', but only the
            # first is nothing else — so between two equally-evidenced
            # siblings this picks 'Candles' over 'Candle Mould'. Ranked BELOW
            # the breadcrumb score: 'Toy Figures' must not prefer the shallow
            # 'Toys & Games > Toys' (precision 1.0) over the deeper
            # '… > Action Figures' (precision 0.5, but one more cue).
            precision = len(last & leaf_toks) / max(1, len(leaf_toks))
            key = (
                1 if (last & ptoks) else 0,   # the product noun matched
                len(title_toks & ptoks),      # the title agrees
                score,                        # total breadcrumb overlap
                round(precision, 3),          # the leaf names that noun alone
                cand.count(" > "),            # deeper is more specific
            )
            if best_key is None or key > best_key:
                best, best_key = cand, key
                best_overlap = want & ptoks

        if best is None:
            return None

        # A single shared token is only evidence when it is specific. 'polish'
        # matches six unrelated paths (Nail Polish, Shoe Polish Cream,
        # Polishers, …) — picking any of them is a coin flip, so treat a lone
        # token that many candidates share as no signal at all and let the
        # caller fall back to the L1 instead of filing a wrong leaf.
        if best_key[2] == 1:   # only a single token carried the match
            token = next(iter(best_overlap))
            sharers = [c for c in candidates if token in _significant(c)]
            if len(sharers) > 3:
                # Ambiguous only when the token is spread over DIFFERENT
                # branches. 'candl' sits on seven siblings under a single
                # 'Candles & Fragrances' L3 — an unambiguous branch, not a
                # coin flip — so the tie-break above picks among them; 'polish'
                # spans unrelated L3s and still falls back.
                branches = {" > ".join(c.split(" > ")[:3]) for c in sharers}
                if len(branches) > 1:
                    return None
        return best

    def resolve_coarse(self, platform: str, source_path: str | None,
                       title: str = "") -> dict | None:
        """Coarse but valid placement, in four passes.

        1. LEAF match across the WHOLE taxonomy: the last crumb is the product
           noun ('Wall Clocks', 'First Aid kits'). Containment only counts when
           the Gajab leaf is itself a phrase — a one-word leaf like 'water' or
           'furniture' must not swallow 'Water Treatments' or 'Furniture & Wood
           Polishes' and file them in a different branch entirely.
        2. Descend inside the L1 the marketplace top level maps to, scoring
           every candidate path by distinctive-token overlap (the old version
           needed a whole crumb to appear verbatim, so 'Pest Control' never
           matched 'Insect Repellents' and the row stalled at the bare L1).
        3. The title names a Gajab leaf verbatim.
        4. Whole-taxonomy descent the same way — reached even when an L1 alias
           exists, so a breadcrumb whose only Gajab equivalent sits elsewhere
           (drawing supplies -> Stationery) is still found; it must clear a
           higher bar than pass 2, since it is crossing taxonomies.
        Only if all four fail does the row fall back to the aliased L1.
        """
        if not source_path:
            return None
        # normalise_path() drops a leading "Home"/"Home Page" crumb. Flipkart
        # prefixes EVERY breadcrumb with "Home" (its homepage), so parsing the
        # raw path here made parts[0] == "Home" and _l1_alias("Home") resolved
        # the whole catalogue to Home & Kitchen — the source of Flipkart's
        # ~90% wrong/unmapped categories.
        parts = [p.strip() for p in re.split(r"\s*>\s*", normalise_path(source_path)) if p.strip()]
        if not parts:
            return None

        # ── pass 1: leaf noun vs Gajab leaves ──────────────────────────────
        leaf_crumb = _norm_key(parts[-1])
        if len(leaf_crumb.split()) >= 2:
            variants = _key_variants(leaf_crumb)
            found = None
            for want_exact in (True, False):
                for cand in self._coarse:          # deepest first
                    cand_leaf = _norm_key(cand.split(" > ")[-1])
                    if want_exact:
                        if cand_leaf in variants:
                            found = cand
                            break
                    elif len(cand_leaf.split()) >= 2 and any(
                        v in cand_leaf or cand_leaf in v for v in variants if len(v) >= 6
                    ):
                        found = cand
                        break
                if found:
                    break
            if found:
                return self._result_for(found, "coarse-leaf", 0.7)

        # ── pass 2: descend inside the mapped L1 ───────────────────────────
        gajab_l1 = self._l1_alias(parts[0])
        if gajab_l1:
            inside = self._coarse_candidates(gajab_l1)
            best = self._best_by_overlap(inside, parts[1:], title)
            if best:
                return self._result_for(best, "coarse", 0.55)
            # NOT a dead end: 'Toys & Games > … > Pencil Erasers' has no match
            # inside the aliased L1 yet does in the whole taxonomy
            # (Stationery > … > Erasers). Returning the bare L1 here — as this
            # used to — stranded every such row at L1 with no attributes.

        # ── pass 3: the TITLE names a Gajab leaf verbatim ──────────────────
        # 'Latte Art Coffee Stencil Set' contains the leaf 'Coffee Stencil'.
        # Checked BEFORE the cross-taxonomy descent: a verbatim two-word leaf
        # inside the product's own name is far stronger evidence than any
        # shared-token overlap between two unrelated taxonomies.
        hit = self._title_leaf(title)
        if hit:
            return self._result_for(hit, "coarse-title", 0.5)

        # ── pass 4: whole-taxonomy descent ─────────────────────────────────
        # Deliberately STRICTER than pass 2: inside the aliased L1 the
        # breadcrumb already carries context, but a cross-taxonomy match on one
        # or two shared words is a coin flip — '… > Aquarium Décor > Ornaments'
        # would otherwise file an aquarium ornament under 'Spiritual & Festive
        # Decor > Deity Ornaments', and '… > Water Treatments' an aquarium
        # conditioner under 'Hair Treatment'. Cues must compound (breadcrumb +
        # product noun + title) before a cross-branch answer is accepted.
        best = self._best_by_overlap(self._coarse, parts, title)
        if best:
            want: set = set()
            for c in parts:
                want |= _significant(c)
            best_toks = _significant(best)
            score = len(want & best_toks)
            last_toks = _significant(parts[-1])
            title_toks = _significant(title) if title else set()
            title_ovl = len(title_toks & best_toks)
            # Leaving the L1 the marketplace top level maps to is the risky
            # move: inside it the breadcrumb already constrains the branch, so
            # two agreeing cues suffice; crossing taxonomies demands a third
            # (three breadcrumb tokens, or the product name twice over).
            cross = best.split(" > ")[0] != (gajab_l1 or "")
            if cross:
                # A cross-taxonomy answer must be well supported: three
                # breadcrumb cues, OR the candidate leaf must be described
                # entirely by the product noun (precision 1.0) with the title
                # agreeing. Without the precision test a "Men's Sports Shoes"
                # crumb latched onto "Sports & Fitness > Cycling > Brake Shoes"
                # — the leaf 'brake shoes' only half-matches 'shoes'.
                leaf_toks = _significant(best.split(" > ")[-1])
                prec = len(last_toks & leaf_toks) / max(1, len(leaf_toks))
                strong = score >= 3 or (
                    bool(last_toks & best_toks) and title_ovl >= 2 and prec >= 1.0
                )
            else:
                strong = score >= 2 or (
                    score >= 1 and bool(last_toks & best_toks) and title_ovl >= 1
                )
            if strong:
                return self._result_for(best, "coarse-cross", 0.5)

        # ── nothing matched anywhere: the aliased L1 is the honest placement ─
        if gajab_l1:
            return self._result_for(gajab_l1, "coarse", 0.4)
        return None

    def _title_leaf(self, title: str) -> str | None:
        """A leaf name that appears verbatim in the product title."""
        t = f" {_norm_key(title)} "
        if len(t.split()) < 3:                 # title too short to be evidence
            return None
        best = None
        for cand in self._leaf_paths:
            n = _norm_key(cand.split(" > ")[-1])
            if len(n.split()) < 2:             # single-word leaves are too loose
                continue
            if f" {n} " in t:
                if best is None or len(n) > len(_norm_key(best.split(" > ")[-1])):
                    best = cand
        return best

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

    def resolve(self, platform: str, source_path: str | list | tuple | None,
                 allow_coarse: bool = True,
                 title: str | None = None) -> dict | None:
        """Return {'full','l1'..'l4','level','confidence','method'} or None.

        Order: curated exact/L4 (then L3/L2) -> curated-coarse -> mechanical
        coarse (marketplace L1 -> Gajab L1, descended as far as the breadcrumb
        names). A product should almost always leave with SOME valid category.
        """
        # A few payloads carry the crumb as a list of segments rather than
        # "A > B > C" — coerce rather than crash.
        if isinstance(source_path, (list, tuple)):
            source_path = " > ".join(str(p) for p in source_path if p)
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
            coarse = (self.resolve_coarse(plat, src, title=title)
                      if allow_coarse else None)
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
