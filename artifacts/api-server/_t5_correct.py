#!/usr/bin/env python3
"""
Text polish using Flan-T5 — pure coherence / grammar / readability polish
on already-cleaned text. MiniLM handles claim detection; mechanical cleaning
handles URLs / symbols / formatting; T5 only smooths the final output.

Model: google/flan-t5-large (~2.8 GB, 780M params)
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Optional

_MODEL = None
_TOKENIZER = None
_MODEL_NAME = "google/flan-t5-large"  # 780M params, ~2.8GB

_USE_MPS = False  # MPS has limited memory for 2.8GB model; use CPU


def _load_model():
    global _MODEL, _TOKENIZER, _USE_MPS
    if _MODEL is not None:
        return
    if _MODEL is False:
        return
    try:
        import torch
        from transformers import T5Tokenizer, T5ForConditionalGeneration

        print(f"[INFO] Loading Flan-T5 polish model: {_MODEL_NAME} (~2.8GB)", file=sys.stderr)
        t0 = time.time()
        _TOKENIZER = T5Tokenizer.from_pretrained(_MODEL_NAME, legacy=False)
        _MODEL = T5ForConditionalGeneration.from_pretrained(
            _MODEL_NAME,
            device_map="auto" if torch.cuda.is_available() else None,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        _MODEL.eval()
        # Only try MPS if CUDA not available and user has 16GB+ unified memory
        if not torch.cuda.is_available() and torch.backends.mps.is_available():
            try:
                import psutil
                mem = psutil.virtual_memory().total / (1024**3)
                if mem >= 12:
                    _MODEL = _MODEL.to("mps")
                    _USE_MPS = True
            except Exception:
                pass
        print(f"[INFO] Flan-T5 ready in {time.time() - t0:.1f}s", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Failed to load T5 model: {e}", file=sys.stderr)
        _MODEL = False


def _generate(prompt: str, max_length: int = 200) -> str:
    _load_model()
    if not _MODEL or _MODEL is False:
        return ""
    import torch
    inputs = _TOKENIZER(prompt, return_tensors="pt", truncation=True, max_length=512)
    if _MODEL.device.type == "mps":
        inputs = {k: v.to("mps") for k, v in inputs.items()}
    with torch.no_grad():
        outputs = _MODEL.generate(
            **inputs,
            max_new_tokens=max_length,
            do_sample=False,
            num_beams=4,
            early_stopping=True,
        )
    return _TOKENIZER.decode(outputs[0], skip_special_tokens=True).strip()


def polish_title(title: str) -> Optional[str]:
    """Polish a mechanically-cleaned title for grammar and readability."""
    if not title or len(title) < 3:
        return None
    prompt = f"Polish grammar: {title}"
    try:
        result = _generate(prompt, max_length=120)
        if result and result != title and result != prompt:
            return result
        return None
    except Exception:
        return None


def polish_description(desc: str) -> Optional[str]:
    """Polish a mechanically-cleaned plain-text description."""
    if not desc or len(desc) < 20:
        return None
    prompt = f"Polish grammar: {desc}"
    try:
        result = _generate(prompt, max_length=600)
        if result and len(result) > 10 and result != prompt:
            return result
        return None
    except Exception:
        return None


def main() -> None:
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(json.dumps({"error": f"Failed to read input: {e}"}))
        sys.exit(1)

    products = data.get("products", [])
    if not products:
        print(json.dumps({"results": []}))
        return

    results = []
    for p in products:
        polished_title = None
        polished_desc = None
        try:
            if p.get("title"):
                polished_title = polish_title(p["title"])
            if p.get("description"):
                polished_desc = polish_description(p["description"])
        except Exception as e:
            traceback.print_exc(file=sys.stderr)

        results.append({
            "sku": p.get("sku", ""),
            "title": polished_title,
            "description": polished_desc,
        })

    out = json.dumps({"results": results})
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
