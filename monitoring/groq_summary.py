"""
groq_summary.py — Generate a natural-language summary of monitoring data via
the Groq LLM API (OpenAI-compatible chat completions).

Uses urllib (no extra dependency). Config comes from GROQ_API_KEY / GROQ_MODEL.
"""
from __future__ import annotations

import json
import re
import urllib.request

from config import GROQ_API_KEY, GROQ_MODEL

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _strip_thinking(text: str) -> str:
    # Reasoning models wrap their chain-of-thought in <think>...</think> — drop it
    if "</think>" in text:
        return text.split("</think>", 1)[1].strip()
    # Unclosed <think> (truncated) — return everything after the opening tag, or drop it entirely
    if text.lstrip().startswith("<think>"):
        return ""
    return text.strip()


def summarize(prompt: str, system: str = "You are a concise, plain-English monitoring analyst. Always respond in English.") -> str:
    """Return a natural-language summary, or empty string if Groq is unavailable."""
    if not GROQ_API_KEY:
        print("[GROQ] Skipping — no GROQ_API_KEY")
        return ""
    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 700,
    }).encode()
    req = urllib.request.Request(GROQ_URL, data=payload, headers={
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (gajab-monitor/1.0)",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        return _strip_thinking(content)
    except Exception as e:
        print(f"[GROQ] Summary error: {e}")
        return ""
