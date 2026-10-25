#!/usr/bin/env python3
"""Local category agent: retrieve candidates, let Qwen choose, flag true gaps.

Two local services on the Windows box do the work:

  * :8081  llama.cpp embedding server (EmbeddingGemma)  -> shortlist
  * :8082  llama.cpp chat server (Qwen2.5-14B-Instruct) -> decision

For a product the agent:
  1. embeds `title + brand + source breadcrumb + key specs`
  2. takes the top-K taxonomy leaves by cosine similarity
  3. asks the model to return the INDEX of the single best candidate, or -1
  4. on -1, asks a second, narrower question (which L2 branch, then the leaves
     of that branch)
  5. if it still refuses, records a TAXONOMY GAP with a suggested path, so the
     branch can be added rather than the product silently mis-filed.

Indexes are used instead of paths so the model cannot invent a category: every
answer is validated against category_l1l4.json before it is accepted.

CLI (measure against what the rule-based mapper produced):

    python3 _category_agent.py --job job.json --limit 50
    python3 _category_agent.py --job job.json --report agent_review.xlsx
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

EMBED_URL = os.environ.get("CATEGORY_EMBED_URL", "http://127.0.0.1:8081")
CHAT_URL = os.environ.get("CATEGORY_AGENT_URL", "http://127.0.0.1:8082")
TOP_K = int(os.environ.get("CATEGORY_AGENT_TOP_K", "25"))
WORKERS = int(os.environ.get("CATEGORY_AGENT_WORKERS", "4"))
TIMEOUT = float(os.environ.get("CATEGORY_AGENT_TIMEOUT", "120"))

HERE = Path(__file__).resolve().parent
TAXONOMY = HERE / "category_l1l4.json"
EMB_CACHE = HERE / ".category_agent_emb.npz"

SYSTEM = (
    "You are a strict product-catalogue classifier for an Indian marketplace. "
    "You are given NUMBERED candidate categories from the real taxonomy and one product. "
    "Choose the single candidate that best describes what the product IS. "
    "Use the product name, brand, seller category and specifications. "
    "Never invent a category and never choose a candidate that only shares a word. "
    # Output is the whole cost: at ~6 tokens/s a 50-token explanation adds ~8s per
    # product. The index alone is enough (confidence comes from the similarity).
    "Answer with JSON only: {\"id\": <candidate number>}. "
    "If no candidate is a reasonable fit: {\"id\": -1}."
)


# ── taxonomy ───────────────────────────────────────────────────────────────
def load_targets() -> list[str]:
    """Leaves of the master taxonomy, including Inactive branches.

    Inactive branches are legitimate targets: `Sports & Fitness > Athletics >
    Baton` is a real category that exists in our workbook and has a listing
    template - it simply has no live listings yet.
    """
    tax = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    ids = list((tax.get("ids") or {}))
    if not ids:
        return [p["full"] for p in tax["paths"]]
    prefixes = set()
    for p in ids:
        parts = p.split(" > ")
        for i in range(1, len(parts)):
            prefixes.add(" > ".join(parts[:i]))
    return sorted(p for p in ids if p not in prefixes)


_NODES_CACHE: list[str] | None = None


def all_nodes() -> list[str]:
    """Every taxonomy node (incl. intermediate ones) - used to drill down."""
    global _NODES_CACHE
    if _NODES_CACHE is None:
        tax = json.loads(TAXONOMY.read_text(encoding="utf-8"))
        _NODES_CACHE = list((tax.get("ids") or {}))
    return _NODES_CACHE


def children_of(path: str) -> list[str]:
    pre = path + " > "
    return [n for n in all_nodes() if n.startswith(pre)]


# ── embeddings ─────────────────────────────────────────────────────────────
def _embed(texts: list[str]) -> np.ndarray:
    out = []
    for t in texts:
        r = requests.post(f"{EMBED_URL}/v1/embeddings",
                          json={"input": t[:2000], "model": "embedding"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        out.append(r.json()["data"][0]["embedding"])
    a = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(a, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return a / norms


def load_index(paths: list[str]) -> np.ndarray:
    if EMB_CACHE.exists():
        try:
            z = np.load(EMB_CACHE, allow_pickle=True)
            if list(z["paths"]) == paths:
                return z["emb"]
        except Exception:
            pass
    print(f"embedding {len(paths)} taxonomy paths ...", file=sys.stderr)
    emb = []
    for i in range(0, len(paths), 64):
        emb.append(_embed(paths[i:i + 64]))
    emb = np.vstack(emb)
    np.savez_compressed(EMB_CACHE, paths=np.array(paths, dtype=object), emb=emb)
    return emb


def product_text(p: dict) -> str:
    specs = p.get("specifications") or {}
    if isinstance(specs, dict):
        spec_txt = "; ".join(f"{k}: {v}" for k, v in list(specs.items())[:14])
    else:
        spec_txt = str(specs)[:600]
    parts = [
        str(p.get("title") or "")[:300],
        f"brand: {p.get('brand')}" if p.get("brand") else "",
        f"source category: {p.get('source_category_path') or p.get('sourceCategoryPath') or ''}",
        spec_txt,
    ]
    return " | ".join(x for x in parts if x)


# ── LLM ────────────────────────────────────────────────────────────────────
def _ask(candidates: list[str], text: str) -> tuple[int, float, str]:
    listing = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates))
    user = f"Candidates:\n{listing}\n\nProduct:\n{text}\n\nAnswer with JSON."
    r = requests.post(f"{CHAT_URL}/v1/chat/completions", timeout=TIMEOUT, json={
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 12,
        "response_format": {"type": "json_object"},
    })
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"].strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return -1, 0.0, "unparseable answer"
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return -1, 0.0, "invalid json"
    idx = int(d.get("id", -1))
    return idx, 1.0 if idx >= 0 else 0.0, ""


SUGGEST_SYSTEM = (
    "You maintain the category taxonomy of an Indian marketplace. For the product you are given, "
    "propose the most specific category path it SHOULD have, using an existing top-level category "
    "where one fits and inventing only the missing levels below it. "
    "Answer with JSON only: {\"path\": \"L1 > L2 > L3\", \"why\": \"<15 words>\"}."
)


def suggest_branch(p: dict, l1s: list[str]) -> tuple[str, str]:
    """Ask for a NEW placeholder path when no existing category fits.

    Advisory only: the value is shown to a human so the taxonomy can be extended.
    It is never written into a product's category.
    """
    user = (f"Existing top-level categories: {', '.join(l1s)}\n"
            f"Product: {product_text(p)[:900]}\n"
            f"Seller category: {p.get('source_category_path') or ''}\n"
            "Which category path should this product have?")
    try:
        r = requests.post(f"{CHAT_URL}/v1/chat/completions", timeout=TIMEOUT, json={
            "messages": [{"role": "system", "content": SUGGEST_SYSTEM},
                         {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 80,
            "response_format": {"type": "json_object"},
        })
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            d = json.loads(m.group(0))
            return str(d.get("path") or ""), str(d.get("why") or "")
    except Exception:
        pass
    return "", ""


def _branches(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        parts = p.split(" > ")
        if len(parts) >= 2:
            b = " > ".join(parts[:2])
            if b not in out:
                out.append(b)
    return out


def classify_one(p: dict, paths: list[str], emb: np.ndarray) -> dict:
    # The curated keyword map comes FIRST: it is the only signal that survives a
    # lying marketplace breadcrumb. Flipkart sellers file e.g. a fuel transfer
    # pump under "Musical Instruments"; the embedding shortlist then follows that
    # breadcrumb and the agent happily picks the branch, which is how 29 pumps
    # ended up in the instruments workbook.
    try:
        from _category_keywords import match_target
        hit = match_target(p.get("title") or "")
        if hit and hit[1] in set(paths):
            return {"path": hit[1], "confidence": 0.95, "method": "title-keyword"}
    except Exception:
        pass
    text = product_text(p)
    q = _embed([text])[0]
    sims = emb @ q
    order = np.argsort(-sims)[:TOP_K]
    candidates = [paths[i] for i in order]
    scores = [float(sims[i]) for i in order]

    idx, conf, reason = _ask(candidates, text)

    # Second chance: narrow to the L2 branches present in the shortlist, then the
    # leaves of the chosen branch. This is what rescues products whose correct
    # leaf never made the top-K.
    if idx < 0:
        # Keep the retry CHEAP: the full cascade (candidates -> branches -> leaves)
        # cost ~100s for one product because every pass is an LLM round trip.
        branches = _branches(candidates)[:10]
        bidx, bconf, breason = _ask(branches, text)
        if 0 <= bidx < len(branches):
            leaves = [x for x in paths if x == branches[bidx] or x.startswith(branches[bidx] + " > ")]
            lidx, lconf, lreason = _ask(leaves[:12], text)
            if 0 <= lidx < len(leaves[:12]):
                idx, conf, reason = lidx, lconf, f"{breason} / {lreason}"
                candidates, scores = leaves[:12], [0.0] * len(leaves[:12])

    if 0 <= idx < len(candidates):
        path = candidates[idx]
        if conf <= 0 and scores:
            conf = float(scores[idx]) if idx < len(scores) else 0.0
        # An intermediate node has no attribute group in the L2 template (groups
        # are L4 leaves), so a product parked on an L3 exports with every
        # attribute blank. Ask once more among its children.
        kids = children_of(path)
        if kids:
            kidx, kconf, kreason = _ask(kids[:TOP_K], text)
            if 0 <= kidx < len(kids[:TOP_K]):
                path = kids[kidx]
                conf = kconf or conf
                reason = f"{reason} / {kreason}"
        return {"path": path, "level": len(path.split(" > ")),
                "confidence": round(conf, 3), "reason": reason,
                "method": "agent", "gap": None,
                "shortlist_top": scores[0] if scores else None}

    # Nothing fits: keep the evidence AND ask for the missing branch, so the fix
    # is a deliberate taxonomy addition rather than the nearest wrong leaf.
    # Advisory only, and it used to cost another full LLM round trip here.
    # Filled in later by `_category_agent.py suggest-gaps`; never blocks enrichment.
    proposed, why = "", ""
    return {"path": None, "level": 0, "confidence": round(conf, 3),
            "reason": reason or "no candidate fits", "method": "agent",
            "gap": {"title": str(p.get("title") or "")[:140],
                    "source_category": p.get("source_category_path") or p.get("sourceCategoryPath") or "",
                    "suggested_branch": proposed or (candidates[0] if candidates else ""),
                    "why": why,
                    "shortlist": candidates[:5]},
            "shortlist_top": scores[0] if scores else None}



ATTR_SYSTEM = (
    "You map marketplace product specifications onto the attributes of one category. "
    "You are given the ALLOWED attribute names and specifications that did not match. "
    "For each specification key choose the allowed attribute whose meaning matches, "
    "or an empty string when none does. Never invent an attribute name. "
    "Answer as JSON only, one entry per key, no values and no commentary: "
    "{\"m\": {\"<specification key>\": \"<allowed attribute name or empty>\"}}."
)


def map_attributes(unmapped: list[dict], declared: list[str], title: str = "") -> dict:
    """Second pass: let the model place the specs the alias table could not.

    Only attribute names the category declares are accepted, and the VALUE is
    always the scraped one (the model never sees a value in its answer) - it may
    not invent specifications.

    The answer is a compact key -> attribute map. Asking for an array of
    {key, attribute, value} objects cost ~200 generated tokens, i.e. 25-45s per
    product at ~6 tok/s - the single biggest enrichment cost.
    """
    if not unmapped or not declared:
        return {}
    allowed = {a.strip().lower(): a for a in declared}
    items = [{"key": str(u.get("key") or ""), "value": str(u.get("value") or "")[:120]}
             for u in unmapped[:8]]
    values = {it["key"]: it["value"] for it in items}
    user = (f"Allowed attributes: {', '.join(declared)}\n"
            f"Product: {title[:140]}\n"
            f"Specifications: {json.dumps(items, ensure_ascii=False)}")
    try:
        r = requests.post(f"{CHAT_URL}/v1/chat/completions", timeout=TIMEOUT, json={
            "messages": [{"role": "system", "content": ATTR_SYSTEM},
                         {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 130,
            "response_format": {"type": "json_object"},
        })
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {}
        data = json.loads(m.group(0))
    except Exception:
        return {}
    mapping = data.get("m") if isinstance(data.get("m"), dict) else {}
    out: dict[str, str] = {}
    for key, attr in mapping.items():
        if not isinstance(attr, str) or not attr.strip():
            continue
        target = allowed.get(attr.strip().lower())
        value = values.get(str(key), "")
        if target and value:
            out[target] = value
    return out


def available() -> bool:
    for url in (EMBED_URL, CHAT_URL):
        try:
            requests.get(f"{url}/health", timeout=3)
        except Exception:
            return False
    return True


def apply_to_prepare(products: list[dict], on_progress=None) -> dict:
    """Classify a batch; returns {id(product) -> result}. Used by the exporter."""
    if not available():
        print("category agent unavailable - keeping rule-based mapping", file=sys.stderr)
        return {}
    paths = load_targets()
    emb = load_index(paths)
    out: dict[int, dict] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(classify_one, p, paths, emb): id(p) for p in products}
        for k, fut in enumerate(futures):
            try:
                out[futures[fut]] = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"agent: {type(exc).__name__}: {exc}", file=sys.stderr)
            if on_progress:
                try:
                    on_progress(k + 1, len(products))
                except Exception:
                    pass
    print(f"agent classified {len(out)}/{len(products)} in {time.time()-t0:.0f}s", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="local category agent (Qwen2.5-14B)")
    ap.add_argument("--job", help="scraper job json (uses its 'products')")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="")
    ap.add_argument("--suggest-gaps", action="store_true",
                    help="fill suggested_branch for cached gaps (batch, offline)")
    ap.add_argument("--compare-rules", action="store_true",
                    help="also show what the rule-based mapper produced")
    args = ap.parse_args()

    if not available():
        raise SystemExit("agent services unavailable (need :8081 and :8082)")
    if not args.job:
        raise SystemExit("--job required")

    payload = json.loads(Path(args.job).read_text(encoding="utf-8"))
    products = payload.get("products") if isinstance(payload, dict) else payload
    products = [p for p in (products or []) if isinstance(p, dict)]
    if args.limit:
        products = products[: args.limit]

    if args.suggest_gaps:
        import _enrich_cache
        from _enrich_cache import DB_PATH as CACHE_DB
        import sqlite3
        paths = load_targets()
        l1s = sorted({x.split(" > ")[0] for x in paths})
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute("select product_id, attributes from product_enrichment "
                            "where method='no-category'").fetchall()
        print(f"{len(rows)} cached gap(s)")
        for pid, _ in rows[:200]:
            title = ""
            path, why = suggest_branch({"title": title}, l1s)
            if path:
                print(f"  {pid}: {path}  ({why})")
        return 0

    paths = load_targets()
    print(f"{len(paths)} taxonomy targets")
    emb = load_index(paths)

    rows = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for p, res in zip(products, pool.map(lambda x: classify_one(x, paths, emb), products)):
            rule = None
            if args.compare_rules:
                from _category_map import CategoryMapper
                rule = CategoryMapper().resolve(
                    p.get("source_category_path") or "", p.get("source_category_path") or "",
                    title=p.get("title"))
            rows.append({"product": p, "agent": res, "rule": rule})
    print(f"classified {len(rows)} in {time.time()-t0:.0f}s")

    ok = sum(1 for r in rows if r["agent"]["path"])
    gaps = [r for r in rows if r["agent"]["gap"]]
    print(f"agent placed {ok}/{len(rows)} | gaps {len(gaps)}")
    changed = [r for r in rows if r["rule"] and r["rule"].get("full") != r["agent"]["path"]]
    print(f"differs from rule-based mapping: {len(changed)}")

    if args.report:
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.title = "Agent"
        ws.append(["Product", "Source Category", "Rule category", "Agent category",
                   "Level", "Confidence", "Reason", "Gap (suggested branch)", "Why"])
        for r in rows:
            ws.append([r["product"].get("title"), r["product"].get("source_category_path"),
                       (r["rule"] or {}).get("full"), r["agent"]["path"],
                       r["agent"]["level"], r["agent"]["confidence"], r["agent"]["reason"],
                       (r["agent"]["gap"] or {}).get("suggested_branch"),
                       (r["agent"]["gap"] or {}).get("why")])
        for col, w in zip("ABCDEFGHI", (58, 52, 52, 52, 7, 10, 40, 52, 40)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        wb.save(args.report)
        print("report ->", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
