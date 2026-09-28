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


def score(source_path: str, candidate: dict) -> float:
    """Overlap between the scraped breadcrumb and a Gajab path.

    The leaf (L4) dominates — marketplaces usually agree on the product noun
    even when their higher levels differ.
    """
    src = tokens(source_path)
    if not src:
        return 0.0
    l4 = tokens(candidate.get("l4", ""))
    l3 = tokens(candidate.get("l3", ""))
    l2 = tokens(candidate.get("l2", ""))
    l1 = tokens(candidate.get("l1", ""))

    # singularity-insensitive containment on the leaf
    def hit(a: list[str], b: list[str]) -> float:
        if not b:
            return 0.0
        a_set = {x.rstrip("s") for x in a}
        b_set = {x.rstrip("s") for x in b}
        inter = len(a_set & b_set)
        return inter / max(len(b_set), 1)

    leaf = hit(src, l4)
    mid = hit(src, l3)
    branch = hit(src, l2)
    top = hit(src, l1)
    return round(0.60 * leaf + 0.20 * mid + 0.13 * branch + 0.07 * top, 4)


def propose(products: list[dict], platform: str, top: int, min_score: float) -> list[dict]:
    tax = load_taxonomy()
    paths = tax.get("paths", [])
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
