#!/usr/bin/env python3
"""
CLI wrapper around _hsn_suggest.correct_product_text for the
POST /api/products/correct-text endpoint.

Input JSON (via stdin or file): { "products": [{ "sku", "title", "description", "brand", "category", "productTypeLabel" }] }
Output JSON: { "results": [{ "sku", "title": "..."|null, "description": "..."|null }] }
"""
from __future__ import annotations

import json
import sys
import traceback

from _hsn_suggest import correct_product_text


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
    use_qwen = data.get("useQwen", False)
    if not products:
        print(json.dumps({"results": []}))
        return

    try:
        results = []
        for p in products:
            corrected = correct_product_text(
                title=p.get("title", ""),
                description=p.get("description", ""),
                brand=p.get("brand", ""),
                category=p.get("category", ""),
                product_type_label=p.get("productTypeLabel", ""),
                images=p.get("images", []) if p.get("images") else None,
                polish=False,
                v2_category=p.get("v2Category", ""),
                use_qwen=use_qwen,
            )
            results.append({
                "sku": p.get("sku", ""),
                "title": corrected.get("title"),
                "description": corrected.get("description"),
                "log": corrected.get("log", []),
            })
        out = json.dumps({"results": results})
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
