#!/usr/bin/env python3
"""Title polishing with the local Qwen agent (llama-server on :8082).

The pre-listing text-correction step is rule-based (claim stripping, ALL-CAPS
fixes, colour/material fixes). Those rules cannot rewrite a badly worded title;
the Windows agent can. This module sends titles to the same llama-server the
category agent uses and returns a cleaned title only when the rewrite passes a
set of guards, so a hallucination can never reach the sheet.

Guards on every rewrite:
  * the brand (or the title's first token) must still be present;
  * no new digits appear (prices/quantities/sizes must not be invented);
  * no marketing/ops wording is introduced (free delivery, warranty, ...);
  * length stays within 12..140 chars;
  * at least two of the original significant words survive.

Usage:
    from _text_agent import polish_titles
    polished = polish_titles([{ "sku": ..., "title": ... }, ...])
    # -> {sku: cleaned_title}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

CHAT_URL = os.environ.get("TEXT_AGENT_URL", os.environ.get("CATEGORY_AGENT_URL", "http://127.0.0.1:8082"))
MODEL_TIMEOUT = int(os.environ.get("TEXT_AGENT_TIMEOUT", "90"))
BATCH = int(os.environ.get("TEXT_AGENT_BATCH", "5"))
MAX_CHARS = int(os.environ.get("TEXT_AGENT_MAX_CHARS", "140"))

SYSTEM = (
    "You clean marketplace product titles for an Indian e-commerce catalogue. "
    "Rules: keep the brand at the start; keep the product type and its key attributes "
    "(size, capacity, colour, pack count); remove promotional or operational claims "
    "(free delivery, best quality, warranty, return, COD, EMI, best seller); use Title "
    "Case instead of ALL CAPS; fix spacing and punctuation; do not invent facts, "
    "numbers or materials; keep it under 120 characters. "
    "Answer as JSON only: {\"1\": \"<title>\", \"2\": \"<title>\", ...} with one entry per input."
)

_BANNED = re.compile(
    r"\b(free\s*delivery|free\s*shipping|best\s*quality|best\s*seller|warranty|"
    r"return|refund|replacement|cod|emi|discount|offer|sale|guarantee|"
    r"cash\s*on\s*delivery|limited\s*time)\b",
    re.I,
)
_SIGNIFICANT = re.compile(r"[a-z0-9]{3,}")


def available() -> bool:
    """True when the local agent answers."""
    try:
        with urllib.request.urlopen(f"{CHAT_URL}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _sig_words(text: str) -> set:
    return set(_SIGNIFICANT.findall(str(text or "").lower()))


def _restore_codes(original: str, candidate: str) -> str:
    """Model codes keep their original casing: 'SXP DEVIL-EYE-S39' must not become
    'Sxp Devil-Eye-S39'."""
    # Only model/SKU-looking tokens (a digit or a hyphen). Plain uppercase words
    # are brands, and re-uppercasing those undoes the Title Case cleanup.
    codes = {}
    for tok in re.findall(r"[A-Z0-9][A-Z0-9-]+", original or ""):
        if re.search(r"[0-9-]", tok):
            codes[tok.lower()] = tok
    if not codes:
        return candidate

    def fix(m):
        word = m.group(0)
        return codes.get(word.lower(), word)

    return re.sub(r"[A-Za-z0-9][A-Za-z0-9-]*", fix, candidate)


def _accept(original: str, candidate: str, brand: str = "") -> bool:
    """Keep a rewrite only when it is a plausible cleanup of the same product."""
    cand = (candidate or "").strip()
    if not cand or cand == original:
        return False
    if len(cand) < 12 or len(cand) > MAX_CHARS:
        return False
    if _BANNED.search(cand) and not _BANNED.search(original):
        return False
    if sorted(re.findall(r"\d+(?:\.\d+)?", cand)) != sorted(re.findall(r"\d+(?:\.\d+)?", original)):
        return False
    anchor = (brand or "").strip().lower()
    if not anchor:
        first = re.sub(r"[^A-Za-z0-9&]+", " ", original or "").strip().split()
        anchor = first[0].lower() if first else ""
    if anchor and anchor not in cand.lower():
        return False
    if len(_sig_words(cand) & _sig_words(original)) < 2:
        return False
    return True


def _chat(messages: list[dict], max_tokens: int = 320) -> str:
    body = json.dumps({
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(f"{CHAT_URL}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def needs_polish(title: str) -> bool:
    """Only send titles that actually need a rewrite.

    The 14B agent generates at ~6-8 tok/s, so polishing every row of a 500-row
    sheet costs minutes. Most titles are already fine; skip them and the agent
    only sees the ones with ALL CAPS, promo wording, doubled spaces or extreme
    length.
    """
    t = str(title or "").strip()
    if len(t) < 8:
        return False
    if len(t) > 110:
        return True
    if "  " in t:
        return True
    if re.search(r"\s[,;:]|[!]{2,}|\*", t):
        return True
    letters = [c for c in t if c.isalpha()]
    if letters:
        upper = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper > 0.7 and len(letters) > 12:
            return True
    if _BANNED.search(t):
        return True
    return False


def polish_titles(items: list[dict], batch: int | None = None) -> dict:
    """Return {sku: cleaned_title} for the titles that passed the guards."""
    batch = max(1, int(batch or BATCH))
    out: dict[str, str] = {}
    started = time.time()
    for start in range(0, len(items), batch):
        chunk = [x for x in items[start:start + batch]
                 if str(x.get("title") or "").strip() and needs_polish(x.get("title"))]
        if not chunk:
            continue
        numbered = "\n".join(
            f"{i}. {str(x['title']).strip()[:220]}" for i, x in enumerate(chunk, 1)
        )
        try:
            raw = _chat([
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": numbered},
            ])
            m = re.search(r"\{.*\}", raw, re.S)
            mapping = json.loads(m.group(0)) if m else {}
        except Exception as exc:  # noqa: BLE001
            print(f"[TEXT-AGENT] batch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        for i, item in enumerate(chunk, 1):
            cand = _restore_codes(str(item.get("title") or ""),
                                  str(mapping.get(str(i)) or "").strip())
            original = str(item.get("title") or "").strip()
            if _accept(original, cand, str(item.get("brand") or "")):
                out[str(item.get("sku") or "")] = cand
    print(f"[TEXT-AGENT] polished {len(out)}/{len(items)} in {time.time() - started:.1f}s "
          f"(only titles that needed it were sent)", file=sys.stderr)
    return out


if __name__ == "__main__":
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else []
    print(json.dumps(polish_titles(payload), ensure_ascii=False, indent=2))
