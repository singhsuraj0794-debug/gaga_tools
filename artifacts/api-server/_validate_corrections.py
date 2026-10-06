#!/usr/bin/env python3
"""
Helper script to validate visual-verify corrections.

Usage:
    python3 _validate_corrections.py <products.json>

This script:
1. Calls visual-verify on the products
2. Shows each correction with the image URL
3. Asks if the correction is correct (y/n)
4. Submits feedback to the validation endpoint

For non-interactive use, pipe answers:
    echo "y\nn\ny" | python3 _validate_corrections.py products.json
"""
import json
import sys
import requests

API_BASE = "http://localhost:8080"

def get_corrections(products):
    """Get corrections from visual-verify endpoint."""
    resp = requests.post(
        f"{API_BASE}/api/products/visual-verify",
        json={"products": products, "skipQwen": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()

def submit_validation(sku, attribute, original, observed, is_correct, actual_value=None):
    """Submit validation feedback."""
    resp = requests.post(
        f"{API_BASE}/api/products/validate-correction",
        json={
            "sku": sku,
            "attribute": attribute,
            "original": original,
            "observed": observed,
            "isCorrect": is_correct,
            "actualValue": actual_value,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def get_stats():
    """Get current validation stats."""
    resp = requests.get(f"{API_BASE}/api/products/validation-stats", timeout=10)
    resp.raise_for_status()
    return resp.json()

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 _validate_corrections.py <products.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    products = data.get("products", [])
    if not products:
        print("No products in input file")
        sys.exit(1)

    print(f"Running visual-verify on {len(products)} products...")
    results = get_corrections(products)

    corrections_to_validate = []
    for r in results.get("results", []):
        for c in r.get("corrections", []):
            if c.get("observed") and c.get("observed") != c.get("original"):
                corrections_to_validate.append({
                    "sku": r["sku"],
                    "image": r.get("images", [""])[0] if r.get("images") else "",
                    **c,
                })

    if not corrections_to_validate:
        print("No corrections to validate")
        sys.exit(0)

    print(f"\nFound {len(corrections_to_validate)} corrections to validate\n")
    print("-" * 60)

    interactive = sys.stdin.isatty()
    answers = []

    for i, c in enumerate(corrections_to_validate, 1):
        print(f"\n[{i}/{len(corrections_to_validate)}] SKU: {c['sku']}")
        print(f"  Attribute: {c['attribute']}")
        print(f"  Original:  {c['original']}")
        print(f"  Observed:  {c['observed']}")
        print(f"  Confidence: {c.get('confidence', 'N/A')}")
        if c.get("image"):
            print(f"  Image: {c['image'][:80]}")

        if interactive:
            answer = input("\n  Is this correction correct? (y/n/s=skip): ").strip().lower()
        else:
            answer = sys.stdin.readline().strip().lower() or "s"

        answers.append(answer)

        if answer == "y":
            submit_validation(c["sku"], c["attribute"], c["original"], c["observed"], True)
            print("  ✓ Marked as CORRECT")
        elif answer == "n":
            actual = input("  What's the actual value? ").strip() if interactive else ""
            submit_validation(c["sku"], c["attribute"], c["original"], c["observed"], False, actual or None)
            print("  ✗ Marked as INCORRECT")
        else:
            print("  - Skipped")

    print("\n" + "=" * 60)
    print("Validation complete!")
    print()

    stats = get_stats()
    print(f"Total validations: {stats['total']}")
    print(f"Accuracy: {stats['accuracy']}")
    print(f"Color accuracy: {stats['byAttribute']['color']['accuracy']}")
    print(f"Material accuracy: {stats['byAttribute']['material']['accuracy']}")

if __name__ == "__main__":
    main()
