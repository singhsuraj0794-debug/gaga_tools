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
    "Answer with JSON only: {\"id\": <candidate number>, \"confidence\": <0-1>, \"reason\": \"<12 words>\"}. "
    "If no candidate is a reasonable fit, answer {\"id\": -1, \"confidence\": <0-1>, \"reason\": \"<12 words>\"}."
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
        "max_tokens": 64,
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
    conf = float(d.get("confidence") or 0.0)
    reason = str(d.get("reason") or "")[:120]
    return idx, conf, reason


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
        branches = _branches(candidates)
        bidx, bconf, breason = _ask(branches, text)
        if 0 <= bidx < len(branches):
            leaves = [x for x in paths if x == branches[bidx] or x.startswith(branches[bidx] + " > ")]
            lidx, lconf, lreason = _ask(leaves[:TOP_K], text)
            if 0 <= lidx < len(leaves[:TOP_K]):
                idx, conf, reason = lidx, lconf, f"{breason} / {lreason}"
                candidates, scores = leaves[:TOP_K], [0.0] * len(leaves[:TOP_K])

    if 0 <= idx < len(candidates):
        path = candidates[idx]
        return {"path": path, "level": len(path.split(" > ")),
                "confidence": round(conf, 3), "reason": reason,
                "method": "agent", "gap": None,
                "shortlist_top": scores[0] if scores else None}

    # Nothing fits: keep the evidence AND ask for the missing branch, so the fix
    # is a deliberate taxonomy addition rather than the nearest wrong leaf.
    l1s = sorted({x.split(" > ")[0] for x in paths})
    proposed, why = suggest_branch(p, l1s)
    return {"path": None, "level": 0, "confidence": round(conf, 3),
            "reason": reason or "no candidate fits", "method": "agent",
            "gap": {"title": str(p.get("title") or "")[:140],
                    "source_category": p.get("source_category_path") or p.get("sourceCategoryPath") or "",
                    "suggested_branch": proposed or (candidates[0] if candidates else ""),
                    "why": why,
                    "shortlist": candidates[:5]},
            "shortlist_top": scores[0] if scores else None}


def available() -> bool:
    for url in (EMBED_URL, CHAT_URL):
        try:
            requests.get(f"{url}/health", timeout=3)
        except Exception:
            return False
    return True


def apply_to_prepare(products: list[dict]) -> dict:
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
    print(f"agent classified {len(out)}/{len(products)} in {time.time()-t0:.0f}s", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="local category agent (Qwen2.5-14B)")
    ap.add_argument("--job", help="scraper job json (uses its 'products')")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="")
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
