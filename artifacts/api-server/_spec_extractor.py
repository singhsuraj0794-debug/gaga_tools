#!/usr/bin/env python3
"""
Category-Aware Spec Extractor
==============================
Uses Shopify dataset + Marqo (OpenCLIP) for intelligent spec extraction.
Combines regex patterns with ML-based category classification.
"""

import sys
import re
import json
import os
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

# Import Shopify dataset processor (optional - may not be downloaded yet)
try:
    from _shopify_dataset import get_category_specs, get_all_category_specs
    SHOPIFY_AVAILABLE = True
except ImportError:
    SHOPIHY_AVAILABLE = False
    print("[SPEC_EXTRACTOR] Shopify dataset not available", file=sys.stderr)

# Import Marqo classifier
try:
    from _marqo_classifier import classify as marqo_classify
    MARQO_AVAILABLE = True
except ImportError:
    MARQO_AVAILABLE = False
    print("[SPEC_EXTRACTOR] Marqo classifier not available", file=sys.stderr)


# ── Master spec patterns (fallback when category unknown) ──────────────────
MASTER_SPEC_PATTERNS = {
    "Material": {
        "priority": 1,
        "patterns": [
            r"\b(cotton|polyester|nylon|stainless\s*steel|leather|rubber|plastic|wood|metal|brass|aluminium|aluminum|silk|wool|canvas|fiberglass|silicon|silicone|bamboo|ceramic|glass|porcelain|cast\s*iron|granite|marble|jute|denim|satin|velvet|terry\s*cotton|pp|abs|tpu|zinc\s*alloy|alloy|iron|steel|acrylic|resin|mDF|mdf|terracotta|clay|copper|bronze|nickel|chrome|tin|pvc|ev|eva|microfiber|net|mesh|lace|chiffon|georgette|crepe|rayon|linen|khadi|chikankari|kundan|meenakari|jaduai|thewa|bidri|dhokra|pattachitra|madhubani|warli|ikat|bandhani|leheriya|ajrakh|kalamkari|block\s*print|screen\s*print|digital\s*print|embroidered|handloom|powerloom|handmade|handcrafted|artisan)\b"
        ]
    },
    "Color": {
        "priority": 2,
        "patterns": [
            r"(?:color|colour)[:\s]*([a-z\s]+?)(?:\s*[,.\n]|$)",
            r"\b(rose\s*gold|midnight\s*blue|dark\s*blue|light\s*blue|sky\s*blue|navy\s*blue|forest\s*green|olive\s*green|sea\s*green|hot\s*pink|baby\s*pink|dusty\s*pink|burnt\s*orange|rust\s*red|wine\s*red|maroon|burgundy|teal|turquoise|lavender|mauve|peach|cream|ivory|charcoal|slate|beige|tan|camel|mahogany|walnut|cherry|maple|ebony|bronze|copper|golden|silver|platinum|metallic|matte|glossy|shimmer|iridescent|holographic|neon|fluorescent|pastel|earth\s*tone|multicolor|rainbow|mix(?:ed)?\s*color|assorted)\b",
            r"\b(black|white|blue|red|green|yellow|pink|purple|orange|grey|gray|brown|beige|navy|teal|maroon|gold|silver|transparent|multicolor|rainbow)\b"
        ]
    },
    "Type": {
        "priority": 3,
        "patterns": [
            r"\b(bottle|bag|wallet|belt|watch|earring|necklace|bracelet|ring|cap|hat|shoe|sandal|slipper|towel|blanket|curtain|pillow|cover|case|stand|holder|dispenser|cooker|grinder|mixer|chopper|peeler|ladle|spoon|fork|knife|scissors|clip|keychain|keyring|toy|figurine|lamp|light|fan|mirror|hook|hanger|organizer|basket|tray|plate|bowl|cup|mug|glass|jar|container|box|purse|clutch|saree|kurti|kurta|lehenga|sherwani|suit|blazer|shirt|t[\-\s]?shirt|top|blouse|dress|skirt|pant|trouser|jeans|shorts|jumpsuit|romper|jersey|polo|hoodie|sweater|jacket|coat|vest|shawl|stole|dupatta|scarf|glove|sock|inner|thermal|nightwear|lounge|pyjama|pajama|tracksuit|sports|gym|yoga|swim|bikini|underwear|bra|brief|boxers|camisole|slip|bodysuit|corset|bustier|chemise|nightie|robe|gown|anarkali|palazzo|patiala|dhoti|lungi|nagra|mojari|jutti|kolhapuri|chappal|floaters|sneakers|boots|heels|flats|loafers|oxfords|moccasins|sliders|clogs|wedges|platforms|stilettos|pumps|mules|espadrilles|brogues|derby|monk)\b"
        ]
    },
    "Theme": {
        "priority": 4,
        "patterns": [
            r"\b(cartoon|cute|kawaii|disney|pokemon|dragon\s*ball|doraemon|tom\s*&?\s*jerry|avengers|marvel|batman|spider[\s-]?man|princess|unicorn|dinosaur|animal|monster|superhero|fantasy|vintage|retro|minimalist|modern|classic|luxury|elegant|bohemian|boho|rustic|industrial|scandinavian|japanese|chinese|indian|pakistani|arabic|turkish|moroccan|french|italian|spanish|american|british)\b"
        ]
    },
    "Target": {
        "priority": 5,
        "patterns": [
            r"\b(for\s+kids|for\s+girls|for\s+boys|for\s+women|for\s+men|for\s+baby|for\s+couples|for\s+teens|for\s+toddlers|kids|girls|boys|women|men|baby|toddler|infant|adult|teen|unisex|family|couples|women|ladies|gentlemen|girls|boys|kids|children|senior|elderly|professional)\b"
        ]
    },
    "Use": {
        "priority": 6,
        "patterns": [
            r"\b(daily\s*use|home\s*decor|kitchen|bathroom|bedroom|office|travel|gym|outdoor|indoor|cooking|cleaning|storage|organiz|utility|professional|personal|commercial|industrial|casual|formal|ethnic|western|traditional|contemporary|sporty|fashion|budget|affordable|economical|value|combo|set|pair|single|piece)\b"
        ]
    },
    "Size": {
        "priority": 7,
        "patterns": [
            r"\bsize[:\s]*(xs|s|m|l|xl|xxl|xxxl|xxxxl|small|medium|large|extra\s*large|free\s*size)\b",
            r"\b(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in(?=\s+(?:x|×|\d))|m|ft|feet)\b"
        ]
    },
    "Capacity": {
        "priority": 8,
        "patterns": [
            r"\b(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|liter|gallon|gal|oz|gm|g|kg|ltrs|litres|liters|cl|dl|qt|pt|fl\s*oz|cup|tbsp|tsp)\b"
        ]
    },
    "Weight": {
        "priority": 9,
        "patterns": [
            r"(?:weight|wt|net\s*wt|gross\s*wt)[:\s]*(\d+(?:\.\d+)?)\s*(g|kg|lb|lbs|oz|gm|grams?|kilograms?)\b"
        ]
    },
    "Dimensions": {
        "priority": 10,
        "patterns": [
            r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m|ft|feet)",
            r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m|ft|feet)"
        ]
    },
    "Pattern": {
        "priority": 11,
        "patterns": [
            r"\b(solid|printed|striped|floral|plain|checked|plaid|geometric|abstract|polka\s*dot|cartoon|cute|embroidered|woven|knitted|crocheted|patchwork|tie\s*dye|batik|block\s*print|screen\s*print|digital\s*print|sublimation|engraved|etched|carved|molded|textured|smooth|rough|matte|glossy|shimmer|glitter|sequin|beaded|tasseled|fringed|ruffled|pleated|gathered|smocked|quilted|padded|lined|unlined)\b"
        ]
    },
    "Pack": {
        "priority": 12,
        "patterns": [
            r"\b(set\s+of\s+\d+|pack\s+of\s+\d+|combo\s+of\s+\d+|\d+\s*pcs?|\d+\s*pieces?|\d+\s*items?|\d+\s*pair|\d+\s*pairs|pair|single|assorted|mixed)\b"
        ]
    },
    "Occasion": {
        "priority": 13,
        "patterns": [
            r"\b(party|gift|birthday|wedding|anniversary|festival|diwali|christmas|new\s*year|valentine|return\s*gift|navratri|dussehra|ganesh\s*chaturthi|onam|pongal|baisakhi|eid|republic\s*day|independence\s*day|mothers\s*day|fathers\s*day|teachers\s*day|ceremony|celebration|festive|pooja|puja|temple|religious|spiritual|astrological|vastu|feng\s*shui|fusion|bohemian|trendy|fancy|designer|branded|premium|luxury)\b"
        ]
    },
    "Origin": {
        "priority": 14,
        "patterns": [
            r"(?:country\s+of\s+origin|made\s+in|origin|manufactured\s+in|produced\s+in)[:\s]*(india|china|usa|united\s*states|uk|united\s*kingdom|japan|korea|germany|france|italy|spain|turkey|uae|dubai|bangladesh|sri\s*lanka|nepal|pakistan|thailand|vietnam|indonesia|malaysia|philippines|taiwan|hong\s*kong|singapore|australia|canada|brazil|mexico|russia)\b"
        ]
    },
    "Power": {
        "priority": 15,
        "patterns": [
            r"\b(\d+\s*(?:w|watts?|kw|hp|v|volts?|mah|ah|hours?|hrs?)|rechargeable|cordless|wireless|usb\s*powered|battery\s*operated|ac\s*powered|dc\s*powered|solar\s*powered|manual|electric|non[\s-]electric)\b"
        ]
    },
    "Warranty": {
        "priority": 16,
        "patterns": [
            r"\b(\d+\s*(?:days?|months?|years?|yrs?)\s*(?:warranty|guarantee|replacement|return))\b"
        ]
    }
}


class SpecExtractor:
    """
    Category-aware spec extractor using:
    1. Shopify dataset for category understanding
    2. Marqo (OpenCLIP) for product classification
    3. Enhanced regex patterns for spec extraction
    """

    def __init__(self):
        self.category_specs = {}
        self.marqo_labels = []
        self.marqo_ids = []

        # Load category specs from Shopify dataset
        if SHOPIFY_AVAILABLE:
            try:
                self.category_specs = get_all_category_specs()
                print(f"[SPEC_EXTRACTOR] Loaded {len(self.category_specs)} category specs from Shopify dataset", file=sys.stderr)
            except Exception as e:
                print(f"[SPEC_EXTRACTOR] Failed to load Shopify specs: {e}", file=sys.stderr)

        # Load our internal category schema for Marqo labels
        try:
            from _category_specs import CATEGORY_SCHEMA
            self.marqo_labels = [data.get("full_path", cid) for cid, data in CATEGORY_SCHEMA.items()]
            self.marqo_ids = list(CATEGORY_SCHEMA.keys())
        except Exception as e:
            print(f"[SPEC_EXTRACTOR] Failed to load category schema: {e}", file=sys.stderr)

    def classify_product(self, images: List[str], title: str = "", description: str = "") -> Tuple[str, float]:
        """
        Classify product using Marqo (OpenCLIP) if images available,
        otherwise use text-based classification.
        Returns (category_id, confidence)
        """
        # Try Marqo image classification first
        if images and MARQO_AVAILABLE:
            try:
                results = marqo_classify(images, self.marqo_labels)
                if results:
                    label, score = results[0]
                    if score > 0.05:
                        idx = self.marqo_labels.index(label)
                        cat_id = self.marqo_ids[idx]
                        return cat_id, score
            except Exception as e:
                print(f"[SPEC_EXTRACTOR] Marqo classification failed: {e}", file=sys.stderr)

        # Fallback to text-based classification
        return self._classify_by_text(title, description)

    def _classify_by_text(self, title: str, description: str) -> Tuple[str, float]:
        """Classify product based on text content."""
        text = f"{title} {description}".lower()

        # Simple keyword-based classification
        category_keywords = {
            "cat_cat_001": ["arts", "craft", "diy", "hobby"],
            "cat_cat_003": ["car", "automobile", "vehicle", "auto"],
            "cat_cat_004": ["baby", "infant", "toddler", "newborn"],
            "cat_cat_005": ["beauty", "cosmetic", "skincare", "makeup"],
            "cat_cat_006": ["electronics", "digital", "smart", "wireless"],
            "cat_cat_007": ["fashion", "clothing", "apparel", "wear"],
            "cat_cat_008": ["food", "snack", "beverage", "edible"],
            "cat_cat_009": ["furniture", "chair", "table", "sofa"],
            "cat_cat_010": ["garden", "outdoor", "plant", "flower"],
            "cat_cat_011": ["gift", "present", "gifting"],
            "cat_cat_012": ["health", "wellness", "medicinal", "supplement"],
            "cat_cat_013": ["home", "decor", "decoration", "interior"],
            "cat_cat_014": ["jewelry", "jewellery", "accessories", "ornament"],
            "cat_cat_015": ["kitchen", "cooking", "dinnerware", "utensil"],
            "cat_cat_016": ["luggage", "bag", "travel", "backpack"],
            "cat_cat_017": ["mobile", "phone", "smartphone", "accessory"],
            "cat_cat_018": ["pet", "dog", "cat", "animal"],
            "cat_cat_019": ["sports", "fitness", "exercise", "gym"],
            "cat_cat_020": ["toy", "game", "play", "fun"],
            "cat_cat_021": ["vehicle", "car", "motorcycle", "bike"],
            "cat_cat_022": ["watch", "clock", "timepiece"],
        }

        best_match = "cat_cat_013"  # Default to Home & Decor
        best_score = 0

        for cat_id, keywords in category_keywords.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_match = cat_id

        return best_match, min(0.8, best_score * 0.2)

    def extract_specs(
        self,
        title: str,
        description: str = "",
        category_id: str = "",
        images: List[str] = None
    ) -> Dict[str, str]:
        """
        Extract specs from product text using category-aware patterns.
        Returns dict of {spec_name: value}
        """
        specs = {}
        text = f"{title} {description}"
        text_lower = text.lower()

        # Products where Material/Type specs are unreliable from images/text alone
        _NON_MATERIAL_PRODUCTS = re.search(
            r"\b(tablet|cleaner|descaler|cleaning|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|strip|sachet|capsule|spray|foam|scrub|freshener|deodorant|sanitizer|disinfectant|steriliz)\b",
            text, re.IGNORECASE,
        )

        # ── Product identity: derive what the product IS from title ──
        if title:
            # Clean up title for product type extraction
            title_clean = re.sub(r"\b(for|with|in|of|the|a|an|and|or|new|best|top|high|super|original|authentic|premium)\b", " ", title, flags=re.IGNORECASE)
            words = [w for w in title_clean.split() if len(w) > 2][:4]
            if words:
                specs["Product"] = " ".join(words)

        # Get category-specific patterns if available
        category_patterns = {}
        if category_id and category_id in self.category_specs:
            category_patterns = self.category_specs[category_id]

        # Determine which patterns to use
        if category_patterns:
            # Use category-specific patterns with higher priority
            patterns_to_use = category_patterns
        else:
            # Fall back to master patterns
            patterns_to_use = MASTER_SPEC_PATTERNS

        # Sort by priority
        sorted_specs = sorted(
            patterns_to_use.items(),
            key=lambda x: x[1].get("priority", 99)
        )

        # Extract each spec
        for spec_name, spec_config in sorted_specs:
            if spec_name in specs:
                continue  # Already extracted

            # Skip Material and Type for non-material products (cleaners, tablets, liquids)
            if _NON_MATERIAL_PRODUCTS and spec_name.lower() in ("material", "materials", "type"):
                continue

            patterns = spec_config.get("patterns", [])
            value = self._extract_spec_value(spec_name, patterns, text, text_lower)

            if value:
                specs[spec_name] = value

        # ── Deduplicate: remove common specs that overlap with category-specific ones ──
        _overlap_map = {
            "type": ["toy_type", "bottle_type", "board_type", "cable_type", "connector_type",
                     "keychain_type", "accessory_type", "item_type", "fabric_type", "sleeve_type",
                     "closure_type", "strap_type", "lens_type", "frame_type", "handle_type"],
            "size": ["dimensions", "dimension", "product_size", "bag_size", "shoe_size"],
            "color": ["colour", "primary_colour", "product_color"],
            "material": ["fabric", "outer_material", "inner_material", "frame_material"],
        }
        values_seen = {}
        to_remove = []
        for k, v in specs.items():
            v_lower = v.strip().lower()
            if v_lower in values_seen:
                # Keep the more specific one (category-specific over common)
                prev_key = values_seen[v_lower]
                if k.lower() in _overlap_map:
                    to_remove.append(prev_key)
                elif prev_key.lower() in [item for items in _overlap_map.values() for item in items]:
                    to_remove.append(k)
                else:
                    to_remove.append(k)
            else:
                values_seen[v_lower] = k
        for k in to_remove:
            specs.pop(k, None)

        # Add brand if found
        if not specs.get("Brand"):
            brand = self._extract_brand(text)
            if brand:
                specs["Brand"] = brand

        return specs

    def _extract_spec_value(self, spec_name: str, patterns: List[str], text: str, text_lower: str) -> Optional[str]:
        """Extract a specific spec value using patterns."""
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                if match.groups():
                    # Get the first non-None group
                    value = next((g for g in match.groups() if g), None)
                    if value:
                        return self._format_spec_value(spec_name, value)
                else:
                    return self._format_spec_value(spec_name, match.group(0))
        return None

    def _format_spec_value(self, spec_name: str, value: str) -> str:
        """Format spec value appropriately."""
        value = value.strip()

        # Capitalize appropriately
        if spec_name in ["Material", "Color", "Type", "Theme", "Target", "Use", "Pattern", "Occasion", "Origin"]:
            return value.replace("-", " ").title()
        elif spec_name in ["Size"]:
            return value.upper()
        elif spec_name in ["Capacity", "Weight", "Dimensions"]:
            return value  # Keep as-is for measurements

        return value

    def _extract_brand(self, text: str) -> Optional[str]:
        """Extract brand from text."""
        # Look for brand patterns
        brand_patterns = [
            r"(?:brand|by|from)[:\s]*([A-Z][A-Za-z0-9\s&]+?)(?:\s+(?:for|new|latest|original|authentic|premium|best|top|high|quality|combo|set|pack|piece|pair|assorted))",
            r"^([A-Z][A-Za-z0-9\s&]+?)(?:\s+(?:for|new|latest|original|authentic|premium|best|top|high|quality|combo|set|pack|piece|pair|assorted))"
        ]

        for pattern in brand_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                brand = match.group(1).strip()
                if 2 < len(brand) < 30:
                    return brand

        return None


# Global instance
_extractor = None


def get_extractor() -> SpecExtractor:
    """Get or create the global spec extractor instance."""
    global _extractor
    if _extractor is None:
        _extractor = SpecExtractor()
    return _extractor


def extract_specs(
    title: str,
    description: str = "",
    category_id: str = "",
    images: List[str] = None
) -> Dict[str, str]:
    """
    Convenience function to extract specs from product text.
    Uses category-aware patterns + Marqo classification.
    """
    extractor = get_extractor()

    # If no category provided, classify the product
    if not category_id and images:
        category_id, _ = extractor.classify_product(images, title, description)

    return extractor.extract_specs(title, description, category_id, images)


if __name__ == "__main__":
    import sys
    import json

    # CLI mode: read input from file or stdin
    if len(sys.argv) > 1:
        input_path = sys.argv[1]
        with open(input_path) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    products = data.get("products", [])
    results = []

    for product in products:
        sku = product.get("sku", "")
        title = product.get("title", "")
        description = product.get("description", "")
        category = product.get("category", "")
        images = product.get("images", [])

        try:
            specs = extract_specs(title, description, category, images)
            results.append({"sku": sku, "specs": specs})
        except Exception as e:
            print(f"[SPEC_EXTRACTOR] Error for {sku}: {e}", file=sys.stderr)
            results.append({"sku": sku, "error": str(e)})

    print(json.dumps({"results": results}))