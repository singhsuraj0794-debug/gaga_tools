#!/usr/bin/env python3
"""
Shopify Product Catalogue Dataset Processor
==========================================
Downloads and processes the Shopify product catalogue from HuggingFace
for category-aware spec extraction.

Dataset: https://huggingface.co/datasets/Shopify/product-catalogue
Size: 48.3K rows with product titles, descriptions, images, and category paths
"""

import sys
import json
import os
import re
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

# Cache directory
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
DATASET_CACHE = os.path.join(CACHE_DIR, "shopify_dataset.json")
CATEGORY_SPECS_CACHE = os.path.join(CACHE_DIR, "category_specs.json")


def _ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    os.makedirs(CACHE_DIR, exist_ok=True)


def download_dataset() -> bool:
    """
    Download the Shopify product catalogue dataset from HuggingFace.
    Returns True if successful.
    """
    try:
        from datasets import load_dataset

        print("[SHOPIFY] Downloading dataset from HuggingFace...", file=sys.stderr)
        t0 = __import__("time").time()

        # Load the dataset (train split only to save memory)
        ds = load_dataset("Shopify/product-catalogue", split="train", trust_remote_code=True)

        import time
        print(f"[SHOPIFY] Downloaded {len(ds)} rows in {time.time() - t0:.0f}s", file=sys.stderr)

        # Process and save to cache
        _ensure_cache_dir()
        processed = []

        for row in ds:
            entry = {
                "title": row.get("product_title", ""),
                "description": row.get("product_description", ""),
                "brand": row.get("ground_truth_brand", ""),
                "category": row.get("ground_truth_category", ""),
                "potential_categories": row.get("potential_product_categories", []),
            }
            processed.append(entry)

        with open(DATASET_CACHE, "w") as f:
            json.dump(processed, f)

        print(f"[SHOPIFY] Cached {len(processed)} entries to {DATASET_CACHE}", file=sys.stderr)
        return True

    except Exception as e:
        print(f"[SHOPIFY] Download failed: {e}", file=sys.stderr)
        return False


def load_dataset_from_cache() -> List[Dict]:
    """Load processed dataset from cache."""
    if os.path.exists(DATASET_CACHE):
        with open(DATASET_CACHE) as f:
            return json.load(f)
    return []


def analyze_category_specs() -> Dict[str, Dict]:
    """
    Analyze the dataset to determine which specs are relevant for each category.
    Returns a mapping of category → {spec_name: extraction_patterns}
    """
    dataset = load_dataset_from_cache()
    if not dataset:
        print("[SHOPIFY] No cached dataset found. Run download_dataset() first.", file=sys.stderr)
        return {}

    # Group products by category
    category_products = defaultdict(list)
    for entry in dataset:
        cat = entry.get("category", "")
        if cat:
            category_products[cat].append(entry)

    print(f"[SHOPIFY] Analyzing {len(category_products)} unique categories...", file=sys.stderr)

    # Analyze specs for each category
    category_specs = {}

    for category, products in category_products.items():
        if len(products) < 5:  # Skip categories with too few products
            continue

        specs = _analyze_category(products, category)
        if specs:
            category_specs[category] = specs

    # Cache the results
    _ensure_cache_dir()
    with open(CATEGORY_SPECS_CACHE, "w") as f:
        json.dump(category_specs, f, indent=2)

    print(f"[SHOPIFY] Analyzed specs for {len(category_specs)} categories", file=sys.stderr)
    return category_specs


def _analyze_category(products: List[Dict], category: str) -> Dict:
    """Analyze products in a category to determine relevant specs."""
    specs = {}

    # Combine all titles and descriptions
    all_text = " ".join([
        f"{p.get('title', '')} {p.get('description', '')}"
        for p in products
    ]).lower()

    # Material patterns
    material_patterns = [
        r"\b(cotton|polyester|nylon|leather|rubber|plastic|wood|metal|brass|aluminium|aluminum|silk|wool|canvas|fiberglass|silicone|bamboo|ceramic|glass|porcelain|cast\s*iron|granite|marble|jute|denim|satin|velvet|terry\s*cotton|pp|abs|tpu|zinc\s*alloy|alloy|iron|steel|acrylic|resin|mDF|mdf)\b"
    ]
    if any(re.search(p, all_text) for p in material_patterns):
        specs["Material"] = {"priority": 1, "patterns": material_patterns}

    # Color patterns
    color_patterns = [
        r"\b(black|white|blue|red|green|yellow|pink|purple|orange|grey|gray|brown|beige|navy|teal|maroon|gold|silver|transparent|multicolor|rainbow|rose\s*gold|midnight\s*blue)\b"
    ]
    if any(re.search(p, all_text) for p in color_patterns):
        specs["Color"] = {"priority": 2, "patterns": color_patterns}

    # Size patterns
    size_patterns = [
        r"\b(xs|s|m|l|xl|xxl|xxxl|small|medium|large|extra\s*large|free\s*size)\b",
        r"\b(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m|ft|feet)\b"
    ]
    if any(re.search(p, all_text) for p in size_patterns):
        specs["Size"] = {"priority": 3, "patterns": size_patterns}

    # Weight patterns
    weight_patterns = [
        r"\b(\d+(?:\.\d+)?)\s*(g|kg|lb|lbs|oz|gm|grams?|kilograms?)\b"
    ]
    if any(re.search(p, all_text) for p in weight_patterns):
        specs["Weight"] = {"priority": 4, "patterns": weight_patterns}

    # Capacity patterns
    capacity_patterns = [
        r"\b(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|liter|gallon|gal|oz|gm|g|kg)\b"
    ]
    if any(re.search(p, all_text) for p in capacity_patterns):
        specs["Capacity"] = {"priority": 5, "patterns": capacity_patterns}

    # Dimensions patterns
    dimension_patterns = [
        r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m)",
        r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m)"
    ]
    if any(re.search(p, all_text) for p in dimension_patterns):
        specs["Dimensions"] = {"priority": 6, "patterns": dimension_patterns}

    # Type patterns (product type)
    type_patterns = [
        r"\b(bottle|bag|wallet|belt|watch|earring|necklace|bracelet|ring|cap|hat|shoe|sandal|slipper|towel|blanket|curtain|pillow|cover|case|stand|holder|dispenser|cooker|grinder|mixer|chopper|peeler|ladle|spoon|fork|knife|scissors|clip|keychain|keyring|toy|figurine|lamp|light|fan|mirror|hook|hanger|organizer|basket|tray|plate|bowl|cup|mug|glass|jar|container|box|purse|clutch)\b"
    ]
    if any(re.search(p, all_text) for p in type_patterns):
        specs["Type"] = {"priority": 7, "patterns": type_patterns}

    # Theme patterns
    theme_patterns = [
        r"\b(cartoon|cute|kawaii|disney|pokemon|dragon\s*ball|doraemon|tom\s*&?\s*jerry|avengers|marvel|batman|spider[\s-]?man|princess|unicorn|dinosaur|animal|monster|superhero|fantasy|vintage|retro|minimalist|modern|classic|luxury|elegant)\b"
    ]
    if any(re.search(p, all_text) for p in theme_patterns):
        specs["Theme"] = {"priority": 8, "patterns": theme_patterns}

    # Target patterns
    target_patterns = [
        r"\b(for\s+kids|for\s+girls|for\s+boys|for\s+women|for\s+men|for\s+baby|kids|girls|boys|women|men|baby|toddler|infant|adult|teen|unisex|family)\b"
    ]
    if any(re.search(p, all_text) for p in target_patterns):
        specs["Target"] = {"priority": 9, "patterns": target_patterns}

    # Usage patterns
    usage_patterns = [
        r"\b(party|gift|birthday|wedding|anniversary|festival|diwali|christmas|new\s*year|valentine|return\s*gift|home\s*decor|kitchen|bathroom|bedroom|office|travel|gym|outdoor|indoor|daily\s*use)\b"
    ]
    if any(re.search(p, all_text) for p in usage_patterns):
        specs["Use"] = {"priority": 10, "patterns": usage_patterns}

    return specs


def get_category_specs(category: str) -> Dict:
    """Get specs for a specific category."""
    if os.path.exists(CATEGORY_SPECS_CACHE):
        with open(CATEGORY_SPECS_CACHE) as f:
            all_specs = json.load(f)
            return all_specs.get(category, {})
    return {}


def get_all_category_specs() -> Dict[str, Dict]:
    """Get all category specs."""
    if os.path.exists(CATEGORY_SPECS_CACHE):
        with open(CATEGORY_SPECS_CACHE) as f:
            return json.load(f)
    return {}


# Auto-download and analyze on import if cache doesn't exist
# Disabled by default - run download_dataset() manually if needed
# if not os.path.exists(CATEGORY_SPECS_CACHE):
#     try:
#         download_dataset()
#         analyze_category_specs()
#     except Exception as e:
#         print(f"[SHOPIFY] Auto-initialization failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    # Run as script to download and analyze
    download_dataset()
    analyze_category_specs()
    print("Done! Category specs cached.")