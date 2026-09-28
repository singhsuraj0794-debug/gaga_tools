#!/usr/bin/env python3
"""Propose Gajab categories for scraped marketplace breadcrumbs (review aid).

This does NOT feed the runtime — the runtime uses the curated
`marketplace_category_map.json` only. This tool exists to *build* that map: it
takes scraped products (with their `source_category_path`), lists the distinct
scraped categories, and proposes the closest Gajab L1-L4 paths for a human to
accept or correct.

Input: JSON array of products, each with `source_category_path` and optionally
`platform` (default "amazon"), e.g. the output of the scraper.

Usage:
    python3 _propose_category_map.py scraped.json review.md \
        --platform amazon [--top 3] [--min-score 0.30]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

from _category_map import load_taxonomy, normalise_path

# tokens that carry no category signal
_STOP = {
    "and", "the", "for", "with", "of", "in", "to", "a", "an", "on", "by",
    "home", "page", "products", "product", "items", "item", "all", "other",
    "general", "misc", "accessories", "accessory",
}


def tokens(s: str) -> list[str]:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t and t not in _STOP]


def stem(t: str) -> str:
    """Crude but correct-enough singulariser.

    The previous version was `rstrip('s')`, which turns 'washes' into 'washe'
    and therefore never matched 'wash' — that single bug hid the correct
    'Face Washes' candidate behind 'Face Pack'.
    """
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"          # berries -> berry
    if len(t) > 4 and t.endswith("es"):
        base = t[:-2]
        # only drop 'es' when it reads like a plural (washes -> wash, boxes -> box)
        if base.endswith(("s", "x", "z", "ch", "sh", "g")):
            return base
        return t[:-1]                # 'sizes' -> 'size'
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]                # containers -> container
    return t


def _stems(xs: list[str]) -> set[str]:
    return {stem(x) for x in xs}


# Populated by _generic_tokens(): tokens so common in the taxonomy that matching
# them carries almost no signal (home, kitchen, care, supplies...).
_GENERIC: dict[str, float] = {}


def _generic_tokens(paths: list[dict]) -> dict[str, float]:
    """Share of taxonomy paths each stem appears in (0..1)."""
    n = max(len(paths), 1)
    cnt: Counter = Counter()
    for c in paths:
        seen = set()
        for key in ("l1", "l2", "l3", "l4"):
            seen |= _stems(tokens(c.get(key, "")))
        for t in seen:
            cnt[t] += 1
    return {t: c / n for t, c in cnt.items()}


def _informativeness(t: str) -> float:
    """1.0 for a rare/distinct token, near 0 for a ubiquitous one.

    Without this, matching the generic word 'furniture' across L4/L3/L2/L1 made
    'Furniture & Wood Polishes' score 0.94 against a pure 'Furniture' path.
    """
    share = _GENERIC.get(t, 0.02)
    return max(0.08, 1.0 - share)


def score(source_path: str, candidate: dict) -> float:
    """Informativeness-weighted overlap between the scraped path and a Gajab path.

    The leaf (L4) dominates — marketplaces usually agree on the product noun even
    when their higher levels differ. Two guards keep it honest:
      * generic tokens contribute little (they appear all over the taxonomy)
      * the HEAD NOUN of the scraped leaf must appear in the candidate leaf.
        Without this, 'Furniture & Wood Polishes' matched 'Furniture Accessories'
        at 0.94 because the one token 'furniture' is the whole candidate leaf —
        even though Gajab has no polish category and nothing should be proposed.
    """
    src = _stems(tokens(source_path))
    if not src:
        return 0.0

    def overlap(levels: str) -> float:
        tgt = _stems(tokens(candidate.get(levels, "")))
        if not tgt:
            return 0.0
        inter = src & tgt
        if not inter:
            return 0.0
        got = sum(_informativeness(t) for t in inter)
        want = sum(_informativeness(t) for t in tgt)
        return got / want if want else 0.0

    leaf, mid, branch, top = overlap("l4"), overlap("l3"), overlap("l2"), overlap("l1")

    # The leaf is the product noun: if it does not match at all, the candidate is
    # almost certainly the wrong product family, however well the parents agree.
    if leaf == 0.0:
        return round(0.25 * (0.20 * mid + 0.13 * branch + 0.07 * top), 4)

    # head-noun check on the scraped leaf (last crumb, last token)
    crumbs = [c for c in re.split(r"\s*>\s*", source_path) if c.strip()]
    head = ""
    if crumbs:
        ht = tokens(crumbs[-1])
        head = stem(ht[-1]) if ht else ""
    cand_leaf = _stems(tokens(candidate.get("l4", "")))
    head_ok = (not head) or (head in cand_leaf) or any(head in c for c in cand_leaf) or any(c in head for c in cand_leaf)

    raw = 0.60 * leaf + 0.20 * mid + 0.13 * branch + 0.07 * top
    if not head_ok:
        raw *= 0.35
    return round(raw, 4)


def propose(products: list[dict], platform: str, top: int, min_score: float) -> list[dict]:
    tax = load_taxonomy()
    paths = tax.get("paths", [])
    # learn which tokens are too common to be evidence, from the taxonomy itself
    _GENERIC.update(_generic_tokens(paths))
    counts: Counter = Counter()
    for p in products:
        raw = p.get("source_category_path") or p.get("sourceCategoryPath")
        if not raw:
            continue
        plat = (p.get("platform") or platform or "").lower()
        counts[(plat, normalise_path(str(raw)))] += 1

    out = []
    for (plat, src), n in counts.most_common():
        scored = sorted(
            ((score(src, c), c) for c in paths), key=lambda x: -x[0]
        )[:top]
        best = scored[0] if scored else (0.0, None)
        out.append({
            "platform": plat,
            "source_path": src,
            "count": n,
            "proposed": best[1]["full"] if best[1] and best[0] >= min_score else "",
            "score": best[0],
            "alternatives": [
                {"path": c["full"], "score": s} for s, c in scored[1:] if c
            ],
        })
    return out


def to_markdown(rows: list[dict]) -> str:
    lines = [
        "# Scraped categories → Gajab L1–L4 (review)",
        "",
        "Fill the **Corrected Gajab path** column, then copy accepted rows into",
        "`marketplace_category_map.json`. Only curated entries are used at runtime;",
        "unmapped categories leave `Category Name *` blank in the export.",
        "",
        "| platform | scraped category | seen | proposed Gajab path | score | corrected Gajab path |",
        "|---|---|---:|---|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['platform']} | {r['source_path']} | {r['count']} | "
            f"{r['proposed'] or '_no proposal_'} | {r['score']:.2f} | |"
        )
    lines.append("")
    lines.append("## Alternatives considered")
    lines.append("")
    for r in rows:
        if not r["alternatives"]:
            continue
        lines.append(f"- **{r['platform']} · {r['source_path']}**")
        for a in r["alternatives"]:
            lines.append(f"    - `{a['path']}` ({a['score']:.2f})")
    lines.append("")
    return "\n".join(lines)


def to_seed_map(rows: list[dict]) -> dict:
    """Seed map containing only self-consistent proposals (score >= 0.6)."""
    seed: dict[str, dict] = {}
    for r in rows:
        if r["proposed"] and r["score"] >= 0.60:
            seed.setdefault(r["platform"], {})[r["source_path"]] = r["proposed"]
    return {"version": "1.0", "map": seed}


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: python3 _propose_category_map.py scraped.json review.md "
            "[--platform amazon] [--top 3] [--min-score 0.30]"
        )
    src, dest = sys.argv[1], sys.argv[2]
    platform, top, min_score = "amazon", 3, 0.30
    args = sys.argv[3:]
    for i, a in enumerate(args):
        if a == "--platform" and i + 1 < len(args):
            platform = args[i + 1]
        elif a == "--top" and i + 1 < len(args):
            top = int(args[i + 1])
        elif a == "--min-score" and i + 1 < len(args):
            min_score = float(args[i + 1])

    products = json.loads(open(src, encoding="utf-8").read())
    if isinstance(products, dict):
        products = products.get("products") or products.get("results") or []
    rows = propose(products, platform, top, min_score)

    with open(dest, "w", encoding="utf-8") as f:
        f.write(to_markdown(rows))
    seed_path = dest.rsplit(".", 1)[0] + "_seed.json"
    with open(seed_path, "w", encoding="utf-8") as f:
        json.dump(to_seed_map(rows), f, ensure_ascii=False, indent=2)

    print(f"distinct scraped categories: {len(rows)}")
    print(f"review written : {dest}")
    print(f"seed map       : {seed_path}  ({sum(len(v) for v in to_seed_map(rows)['map'].values())} entries >= 0.60)")
    for r in rows[:12]:
        print(f"  [{r['platform']}] {r['source_path'][:58]:58s} -> {r['proposed'][:52] or '(none)'}  {r['score']:.2f}")


if __name__ == "__main__":
    main()
