#!/usr/bin/env python3
"""
HSN Suggestion Service — embedding-based semantic search over the
GST 2.0 Rate Notification (Notification No. 09/2025-Central Tax (Rate)).

For each product (title + optional description), embed the text and return the
closest HSN entries from the notification as ranked suggestions with the GST
rate, central tax, schedule, and a confidence score.

Approach: zero-shot semantic search. Every HSN entry description from
hsn_entries.json is embedded once with a sentence-embedding transformer model
(cached to a .npy file). A product's title/description is embedded the same way
and matched to the nearest entries by cosine similarity. No training data is
required — the notification itself is the knowledge base.

Usage:
    python3 _hsn_suggest.py <input.json>
    python3 _hsn_suggest.py <input.json> <output.json>
    echo '{"products":[...]}' | python3 _hsn_suggest.py /dev/stdin

Input JSON:
{
  "products": [
    {"sku": "...", "title": "Prestige PVC veggie cutter", "description": "..."}
  ]
}

Output JSON:
{
  "results": [
    {
      "sku": "...",
      "query": "title. description",
      "suggestions": [
        {
          "hsn": "8211 00 00",
          "description": "...",
          "gst_rate": 18.0,
          "central_tax": 9.0,
          "schedule": "Schedule II",
          "confidence": 0.78,
          "rank": 1
        }, ...
      ],
      "topGstRate": 18.0,
      "topSchedule": "Schedule II"
    }
  ]
}

Environment:
    HSN_ENTRIES    path to hsn_entries.json (default: next to this script)
    HSN_MODEL      HF model name (default: sentence-transformers/all-MiniLM-L6-v2)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np

# ─── Config ───────────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENTRIES_PATH = os.environ.get("HSN_ENTRIES") or os.path.join(_SCRIPT_DIR, "hsn_entries.json")
MODEL_NAME = os.environ.get("HSN_MODEL") or "sentence-transformers/all-MiniLM-L6-v2"
CACHE_PATH = os.path.join(_SCRIPT_DIR, ".hsn_embeddings.npy")
CLIP_TEXT_CACHE = os.path.join(_SCRIPT_DIR, ".hsn_clip_text.npy")
TOPK = 1
ALGORITHM_VERSION = "clip-type-v5"
MAX_TOKENS = 256
VISION_LABELS = [
    "a kitchen utensil or household kitchen article",
    "an electric household appliance",
    "a phone, tablet, or computer",
    "clothing or apparel",
    "footwear or shoes",
    "furniture",
    "a cosmetic or personal care product",
    "food, drink, or edible commodity",
    "a bottle, container, or storage article",
    "a tool or hardware article",
    "a toy or game",
    "jewellery or an accessory",
    "a vehicle or vehicle part",
    "stationery or a paper product",
    "a textile or home furnishing",
]

# ─── Query expansion ──────────────────────────────────────────────────────────
# Product titles use retail language ("tshirt", "fridge", "veggie") while HSN
# descriptions use tariff language ("articles of apparel", "refrigerating
# appliances"). Expanding retail tokens with their tariff-family synonyms
# meaningfully improves embedding recall. This is standard query expansion —
# matching itself is still pure cosine similarity over the embeddings.
STOPWORDS = {
    "men", "male", "woman", "women", "kids", "for", "the", "and", "new",
    "with", "of", "in", "on", "free", "shipping", "offer", "sale", "deal",
    "official", "genuine", "premium", "deluxe", "model", "pro", "plus",
    "mini", "max", "ultra", "smart", "size", "set", "pack",
}

HEAD_SYNONYMS = {
    "dispenser": {"dispenser", "dispensers", "dispersing", "sprayer", "sprayers", "spraying", "projecting", "container", "kitchenware", "household", "articles"},
    "sprayer": {"sprayer", "sprayers", "spraying", "projecting", "dispersing"},
    "mixer": {"mixer", "mixing", "electro-mechanical", "domestic", "appliance"},
    "grinder": {"grinder", "grinding", "electro-mechanical", "domestic", "appliance"},
    "juicer": {"juicer", "electro-mechanical", "domestic", "appliance"},
    "bottle": {"bottle", "container", "plastic", "kitchenware", "household", "articles"},
    "container": {"container", "containers", "kitchenware", "household", "articles"},
}

SYNONYMS = {
    "tshirt": "apparel clothing t-shirt knitted",
    "t-shirt": "apparel clothing t-shirt knitted",
    "shirt": "apparel clothing shirt",
    "jeans": "apparel clothing denim trousers",
    "trouser": "apparel clothing trousers pants",
    "pants": "apparel clothing trousers",
    "dress": "apparel clothing dress",
    "gown": "apparel clothing dress",
    "kurta": "apparel clothing dress",
    "lehenga": "apparel clothing dress",
    "saree": "apparel clothing sari",
    "sari": "apparel clothing sari",
    "dupatta": "apparel clothing scarf",
    "scarf": "apparel clothing scarf",
    "socks": "apparel clothing hosiery socks",
    "underwear": "apparel clothing undergarment",
    "smartphone": "mobile telephone apparatus smartphone",
    "mobile": "telephone apparatus mobile",
    "phone": "telephone apparatus",
    "tablet": "telephone apparatus tablet computer",
    "laptop": "computer portable automatic data processing",
    "computer": "automatic data processing machine computer",
    "monitor": "television receiver monitor computer",
    "tv": "television receiver monitor",
    "television": "television receiver",
    "camera": "photographic apparatus camera",
    "blender": "electro-mechanical domestic appliance machine",
    "grinder": "electro-mechanical domestic appliance machine",
    "mixer": "electro-mechanical domestic appliance machine",
    "juicer": "electro-mechanical domestic appliance machine",
    "mixie": "electro-mechanical domestic appliance machine",
    "iron": "electric domestic iron",
    "kettle": "electric kettle domestic appliance",
    "toaster": "electric toaster domestic appliance",
    "induction": "electric cooking appliance induction",
    "microwave": "microwave oven cooking appliance",
    "chopper": "kitchen utensil cutlery cutter",
    "veg": "vegetable",
    "veggie": "vegetable",
    "running": "sports footwear",
    "sneaker": "sports footwear",
    "shoe": "footwear",
    "shoes": "footwear",
    "sandals": "footwear",
    "slippers": "footwear",
    "chappal": "footwear",
    "mug": "ceramic tableware cup mug",
    "cup": "tableware ceramic",
    "glass": "tableware glassware",
    "plate": "tableware ceramic plate",
    "bowl": "tableware ceramic bowl",
    "refrigerator": "refrigerating appliance",
    "fridge": "refrigerator refrigerating appliance",
    "backpack": "travel goods bag rucksack",
    "handbag": "travel goods handbag",
    "wallet": "travel goods wallet",
    "luggage": "travel goods suitcase trunk",
    "suitcase": "travel goods suitcase trunk",
    "soap": "toilet soap cosmetics",
    "shampoo": "hair oil shampoo",
    "conditioner": "hair preparation cosmetics",
    "toothpaste": "dentifrice oral hygiene",
    "deodorant": "cosmetics deodorant",
    "perfume": "cosmetics perfume",
    "cream": "cosmetics beauty cream",
    "lotion": "cosmetics lotion",
    "sunscreen": "cosmetics sunscreen",
    "lipstick": "cosmetics lipstick",
    "makeup": "cosmetics make-up",
    "watch": "wrist watch",
    "toy": "toy games",
    "toys": "toys games",
    "bottle": "container bottle",
    "lamp": "electric lamp lighting",
    "bulb": "electric lamp lighting",
    "cable": "electric cable conductor",
    "wire": "electric conductor wire",
    "speaker": "sound reproducing apparatus loudspeaker",
    "headphone": "sound reproducing apparatus headphone",
    "earphone": "sound reproducing apparatus headphone",
    "pen": "stationery ball point pen",
    "pencil": "stationery pencil",
    "notebook": "stationery paper notebook",
    "chair": "furniture seating",
    "table": "furniture table",
    "mattress": "bedding mattress",
    "pillow": "bedding pillow cushion",
    "towel": "textile towel",
    "bed": "bedding furniture",
    "curtain": "textile curtain furnishing",
    "carpet": "carpet floor covering",
    "basket": "basket wicker container",
    "jar": "glass container jar",
    "cutlery": "spoons forks knives cutlery",
    "utensil": "kitchen utensil household articles",
    "cookware": "kitchen utensil table kitchen household articles",
    "pressure": "cooking appliance pressure cooker",
    "cooker": "cooking appliance pressure cooker",
    "tawa": "table kitchen household articles iron steel",
    "kadai": "table kitchen household articles iron steel",
    "pan": "table kitchen household articles iron steel",
    "vessel": "table kitchen household articles metal",
    "dinner": "tableware dinner set",
    "cutlery_set": "spoons forks cutlery tableware",
    "frying": "table kitchen household articles iron steel",
    "nonstick": "table kitchen household articles iron steel",
    "exercise": "sports equipment gym",
    "dumbbell": "sports equipment gym",
    "yoga": "sports equipment yoga",
    "cycle": "cycle bicycle",
    "bicycle": "cycle bicycle",
    "car": "motor vehicle automobile",
    "bike": "motor vehicle motorcycle",
    "helmet": "safety headgear helmet",
    "drone": "unmanned aircraft drone",
    "charger": "electric accumulator charger battery",
    "battery": "electric accumulator battery",
    "powerbank": "electric accumulator battery",
    "adapter": "electric transformer adapter",
    "fan": "electric fan",
    "cooler": "cooler air conditioner appliance",
    "ac": "air conditioner appliance",
    "purifier": "air purifier appliance",
    "vacuum": "vacuum cleaner appliance",
    "washing": "washing machine appliance",
    "machine": "machine appliance",
    "dishwasher": "washing machine appliance",
    "water": "water purifier appliance",
    "stove": "cooking stove appliance",
    "gas": "cooking stove appliance",
    "lighter": "pocket lighter",
    "umbrella": "umbrella",
    "sunglasses": "spectacles sunglasses eye wear",
    "glasses": "spectacles optical",
    "jewellery": "jewellery jewellery",
    "jewelry": "jewellery jewellery",
    "gold": "precious metal gold jewellery",
    "silver": "precious metal silver jewellery",
    "diamond": "diamond precious stone",
    "ring": "jewellery ring",
    "necklace": "jewellery necklace",
    "bracelet": "jewellery bracelet",
    "earrings": "jewellery earrings",
    "bangle": "jewellery bangle",
    "bindi": "cosmetics bindi",
    "kajal": "cosmetics eye make-up",
    "mascara": "cosmetics eye make-up",
    "nail": "cosmetics nail polish",
    "shaving": "razor shaving cosmetics",
    "razor": "razor",
    "tissue": "paper tissue",
    "napkin": "sanitary napkin paper",
    "diaper": "baby diaper",
    "pampers": "baby diaper",
    "bread": "bread bakery",
    "biscuit": "biscuit bakery",
    "cookie": "biscuit bakery",
    "chocolate": "chocolate confectionery",
    "candy": "confectionery candy",
    "namkeen": "snacks food preparation",
    "chips": "snacks potato food preparation",
    "oil": "edible oil vegetable oil",
    "ghee": "dairy ghee butter",
    "milk": "dairy milk",
    "cheese": "dairy cheese",
    "butter": "dairy butter",
    "curd": "dairy curd yoghurt",
    "yogurt": "dairy yoghurt",
    "rice": "cereal rice",
    "wheat": "cereal wheat",
    "flour": "cereal flour",
    "atta": "cereal flour",
    "sugar": "sugar",
    "tea": "tea",
    "coffee": "coffee",
    "juice": "fruit juice beverage",
    "drink": "beverage drink",
    "colddrink": "beverage aerated",
    "soft": "beverage aerated",
    "cookie": "biscuit bakery",
    "jam": "jam preserve fruit preparation",
    "honey": "natural honey",
    "pickle": "pickle vegetables prepared",
    "spice": "spices",
    "turmeric": "spices turmeric",
    "masala": "spices mixture",
    "salt": "salt",
    "seeds": "oil seeds seeds",
    "nuts": "nuts edible",
    "dryfruit": "dried fruit nuts",
    "almonds": "nuts almonds",
    "cashew": "nuts cashew",
    "raisin": "dried fruit raisin",
    "cereal": "cereal breakfast",
    "oats": "cereal oats",
    "pasta": "pasta",
    "noodles": "pasta noodles",
    "instant": "food preparation instant",
}


# ─── Lazy-loaded model ────────────────────────────────────────────────────────
_model = None
_tokenizer = None
_entries: Optional[List[dict]] = None
_corpus_vecs: Optional[np.ndarray] = None


def get_model():
    global _model, _tokenizer
    if _model is None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        t0 = time.time()
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        _model = AutoModel.from_pretrained(MODEL_NAME)
        _model.eval()
        if torch.cuda.is_available():
            _model = _model.to("cuda")
        print(f"[INFO] Embedding model loaded in {time.time() - t0:.1f}s", file=sys.stderr)
    return _model, _tokenizer


def embed_texts(texts: List[str]) -> np.ndarray:
    """Mean-pool + L2-normalize sentence embeddings -> (N, D)."""
    import torch

    model, tokenizer = get_model()
    device = next(model.parameters()).device

    inputs = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_TOKENS,
        return_tensors="pt",
    ).to(device)

    with torch.no_grad():
        outputs = model(**inputs)
        token_embeddings = outputs.last_hidden_state
        attention_mask = inputs["attention_mask"].unsqueeze(-1).float()
        masked = token_embeddings * attention_mask
        summed = masked.sum(dim=1)
        counts = attention_mask.sum(dim=1).clamp(min=1e-9)
        mean = (summed / counts).cpu().numpy()

    norms = np.linalg.norm(mean, axis=1, keepdims=True)
    return mean / np.maximum(norms, 1e-12)


def entries_ref() -> List[dict]:
    return load_entries()


def load_entries() -> List[dict]:
    global _entries
    if _entries is None:
        with open(ENTRIES_PATH, "r", encoding="utf-8") as f:
            _entries = json.load(f)
    return _entries


def get_corpus_vecs() -> np.ndarray:
    global _corpus_vecs
    if _corpus_vecs is not None:
        return _corpus_vecs
    entries = load_entries()

    if os.path.exists(CACHE_PATH):
        cached = np.load(CACHE_PATH, allow_pickle=False)
        if cached.shape[0] == len(entries):
            _corpus_vecs = cached
            print(f"[INFO] Loaded cached corpus embeddings ({cached.shape})", file=sys.stderr)
            return _corpus_vecs

    print(f"[INFO] Embedding {len(entries)} HSN entries...", file=sys.stderr)
    t0 = time.time()
    texts = [e["description"] for e in entries]
    vecs = embed_texts(texts)
    np.save(CACHE_PATH, vecs)
    _corpus_vecs = vecs
    print(f"[INFO] Embedded corpus in {time.time() - t0:.1f}s", file=sys.stderr)
    return vecs


def normalize_query(raw: str) -> str:
    """Lowercase, drop stopwords, and expand retail tokens with tariff synonyms."""
    tokens = re.findall(r"[a-zA-Z]+", raw.lower())
    tokens = [t for t in tokens if t not in STOPWORDS]
    expanded = list(tokens)
    for t in tokens:
        if t in SYNONYMS:
            expanded.append(SYNONYMS[t])
    return " ".join(expanded)


def word_stem(word: str) -> str:
    """Small, dependency-free stemmer for product-noun overlap."""
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 4 and word.endswith("s"):
        return word[:-1]
    return word


def build_query(product: dict) -> str:
    parts = [product.get("title", "").strip()]
    desc = product.get("description", "").strip()
    if desc:
        parts.append(desc)
    query = " ".join(p for p in parts if p).strip()
    return normalize_query(query or product.get("sku", ""))



PRODUCT_TYPES = [
    # (id, CLIP label, [title keywords], [hsn candidates], unitVariation)
    # unitVariation: "required" (size/color/count always apply) |
    #                "optional" (varies by item) |
    #                "single" (single-SKU article, unit/count not applicable)
    ("kitchen_sprayer", "a cooking oil sprayer or spray bottle", ["sprayer", "spray", "dispenser", "mister"], ["8424", "3924", "9616"], "single"),
    ("soap_dispenser", "a liquid soap dispenser pump bottle", ["soap dispenser", "soap pump", "lotion dispenser"], ["3924", "8424", "9616"], "single"),
    ("kitchen_utensil", "a kitchen utensil or cookware", ["tawa", "kadai", "pan", "vessel", "tiffin", "cookware", "utensil", "steel", "stainless"], ["7323", "7615", "7418", "3924"], "optional"),
    ("electric_appliance", "an electric household appliance", ["mixer", "juicer", "grinder", "blender", "iron", "kettle", "toaster", "microwave", "induction", "appliance"], ["8509", "8516", "8424"], "single"),
    ("fridge", "a refrigerator", ["refrigerator", "fridge"], ["8418"], "single"),
    ("washing_machine", "a washing machine", ["washing machine", "washer"], ["8450"], "single"),
    ("tv", "a television", ["television", "tv ", "led tv", "smart tv"], ["8528"], "single"),
    ("phone", "a mobile phone", ["mobile", "smartphone", "phone"], ["8517"], "single"),
    ("computer", "a laptop or computer", ["laptop", "computer", "tablet", "ipad"], ["8471"], "single"),
    ("chopper_blade", "a vegetable chopper or cutting blade", ["chopper", "cutter", "veggie", "blade"], ["8208", "8215"], "single"),
    ("cutlery", "cutlery spoons forks", ["cutlery", "spoon", "fork", "ladle"], ["8215"], "required"),
    ("ceramic_ware", "ceramic tableware dishes", ["mug", "cup", "plate", "bowl", "ceramic", "dinner set", "bone china"], ["6911", "6912"], "optional"),
    ("bathroom_accessory", "a toothbrush holder, soap dish, or bathroom accessory", ["toothbrush", "tooth brush", "toothbrush holder", "soap dish", "soap tray", "towel rack", "towel holder", "bathroom shelf", "bathroom accessory", "dispenser"], ["3924", "6911", "6912"], "single"),
    ("plastic_ware", "plastic kitchenware or plastic household articles", ["plastic", "lunchbox", "lunch box", "container"], ["3924", "3923", "3926"], "optional"),
    ("soap", "a bar of soap", ["soap bar", "bathing soap", "soap "], ["3401"], "required"),
    ("toothpaste", "a tube of toothpaste", ["toothpaste", "tooth paste"], ["3306"], "required"),
    ("shampoo", "a bottle of shampoo", ["shampoo", "conditioner"], ["3305"], "required"),
    ("perfume", "a bottle of perfume", ["perfume", "deodorant", "body spray"], ["3303", "3307"], "optional"),
    ("lotion", "a bottle of lotion or cream", ["lotion", "cream", "sunscreen", "body lotion"], ["3304"], "optional"),
    ("candle", "a candle", ["candle", "tea light"], ["3406"], "required"),
    ("detergent", "a box of detergent or soap powder", ["detergent", "washing powder", "soap powder"], ["3402"], "required"),
    ("toy", "a toy", ["toy", "kids toy", "play set"], ["9503"], "required"),
    ("footwear", "a pair of shoes", ["shoe", "slipper", "sandals", "footwear", "sneaker"], ["6401", "6402", "6403", "6404"], "required"),
    ("watch", "a wrist watch", ["watch"], ["9102"], "single"),
    ("eyewear", "a pair of spectacles or sunglasses", ["sunglass", "spectacle", "eyewear", "glasses"], ["9004"], "single"),
    ("bag", "a handbag backpack or bag", ["bag", "backpack", "handbag", "wallet", "luggage", "suitcase", "satchel"], ["4202"], "optional"),
    ("umbrella", "an umbrella", ["umbrella"], ["6601"], "single"),
    ("apparel", "clothing, a garment, shirt, t-shirt, kurta, dress", ["shirt", "t-shirt", "tshirt", "t shirt", "dress", "kurta", "kurti", "jeans", "trouser", "pants", "saree", "sari", "lehenga", "gown", "jacket", "sweater", "hoodie", "top", "blouse", "apparel", "clothing", "garment", "undergarment", "innerwear", "socks", "stocking", "shawl", "scarf", "cap", "hat"], ["61", "62", "6111", "6204", "6109"], "required"),
    ("fabric_textile", "fabric or textile material", ["fabric", "textile", "cloth", "cotton fabric", "silk fabric", "linen", "denim", "velvet", "polyester fabric", "nylon fabric", "curtain", "bedsheet", "bed sheet", "pillow cover", "blanket", "towel", "cushion cover", "tablecloth", "mat", "rug", "carpet", "quilts"], ["5208", "5209", "5210", "5211", "5407", "5512", "5513", "6302", "6304", "5701", "5703", "9404"], "optional"),
    ("vacuum", "a vacuum cleaner", ["vacuum", "vacuum cleaner"], ["8508"], "single"),
    ("water_filter", "a water purifier or filter", ["water purifier", "water filter", "ro system"], ["8421"], "single"),
    ("mattress", "a mattress or pillow", ["mattress", "pillow", "bedding"], ["9404"], "required"),
    ("sports", "sports equipment", ["dumbbell", "exercise", "gym", "yoga", "sports", "fitness", "jump rope", "skipping rope", "workout", "treadmill", "resistance band"], ["9506"], "optional"),
    ("home_safety", "a door stopper, wall protector, or home safety accessory", ["door stopper", "doorstop", "door stop", "slam stopper", "wall protector", "crash pad", "door guard", "shock absorber guard", "baby safety", "anti collision"], ["3926", "4016", "8302"], "single"),
    ("cycle", "a bicycle", ["bicycle", "cycle"], ["8712"], "single"),
    ("helmet", "a helmet", ["helmet"], ["6506"], "required"),
    ("keychain", "a keychain or key ring", ["keychain", "key chain", "key ring", "keyring", "key holder", "key charm"], ["7326", "8308"], "single"),
    ("bracelet", "a bracelet or bangle", ["bracelet", "bangle", "wristband", "bangles"], ["7117", "7113"], "required"),
    ("necklace", "a necklace or pendant", ["necklace", "pendant", "chain", "mangalsutra"], ["7117", "7113"], "required"),
    ("earrings", "earrings", ["earring", "earrings", "jhumkas", "studs"], ["7117", "7113"], "required"),
    ("cable", "an electrical cable or wire", ["usb cable", "charging cable", "extension cable", "power cable", "hdmi cable", "electrical wire", "electric cable", "wire", "cord"], ["8544"], "single"),
    ("charger", "a phone charger or power adapter", ["charger", "adapter", "power adapter", "adapter plug"], ["8504"], "single"),
    ("earphone", "earphones or headphones", ["earphone", "headphone", "earbud", "airbuds", "bluetooth earphone"], ["8518"], "single"),
    ("powerbank", "a power bank", ["power bank", "powerbank"], ["8507"], "single"),
    ("apparel", "clothing such as a t-shirt or shirt", ["t-shirt", "tshirt", "shirt", "jeans", "kurta", "saree", "dress", "trouser", "apparel", "leggings", "hoodie", "jacket", "shorts", "kurti", "lehenga"], ["6109", "6205", "6204", "6203"], "required"),
    ("home_textile", "bed sheets, towels, or curtains", ["bedsheet", "bed sheet", "towel", "curtain", "pillow cover", "duvet", "cushion cover", "table cloth", "bath towel", "face towel"], ["6302", "6303"], "required"),
    ("lunchbox", "a lunch box", ["lunch box", "lunchbox", "tiffin box"], ["3924", "7323"], "single"),
    ("blade", "a blade or knife", ["blade", "knife", "cutting blade"], ["8208", "8211"], "single"),
    ("pillow", "a pillow", ["pillow"], ["9404"], "single"),
]

# Validation-rule hints per product type — derived from CLIP understanding.
# These tell the frontend which checks are meaningful for this product.
PRODUCT_VALIDATION_HINTS = {
    # single-SKU articles: no pack count, no material or dimensions needed
    "cable": {"materialDesc": False, "dimensionsDesc": False, "attrsDesc": False},
    "charger": {"materialDesc": False, "dimensionsDesc": False, "attrsDesc": False},
    "earphone": {"materialDesc": False, "dimensionsDesc": False, "attrsDesc": False},
    "powerbank": {"materialDesc": False, "dimensionsDesc": True, "attrsDesc": False},
    "bathroom_accessory": {"attrsDesc": True},
    "umbrella": {"attrsDesc": False},
    "watch": {"attrsDesc": False},
    "eyewear": {"attrsDesc": False},
    "chopper_blade": {"attrsDesc": False},
    "plastic_ware": {"materialDesc": True, "dimensionsDesc": True},
    # appliances: no material needed, dimensions often included
    "electric_appliance": {"materialDesc": False},
    "fridge": {"materialDesc": False, "attrsDesc": False},
    "washing_machine": {"materialDesc": False, "attrsDesc": False},
    "tv": {"materialDesc": False, "attrsDesc": False},
    "phone": {"materialDesc": False, "attrsDesc": False},
    "computer": {"materialDesc": False, "attrsDesc": False},
    "fan": {"materialDesc": False, "attrsDesc": False},
    "vacuum": {"materialDesc": False, "attrsDesc": False},
    "water_filter": {"materialDesc": False, "attrsDesc": False},
    "cycle": {"materialDesc": False, "attrsDesc": False},
    # apparel, footwear, consumables: all attributes expected
    "apparel": {"attrsDesc": True},
    "footwear": {"attrsDesc": True},
    "soap": {"attrsDesc": True},
    "toothpaste": {"attrsDesc": True},
    "shampoo": {"attrsDesc": True},
    "perfume": {"attrsDesc": True},
    "lotion": {"attrsDesc": True},
    "candle": {"attrsDesc": True},
    "detergent": {"attrsDesc": True},
    "toy": {"attrsDesc": True},
    "mattress": {"attrsDesc": True},
    "helmet": {"attrsDesc": True},
    "bracelet": {"attrsDesc": True},
    "necklace": {"attrsDesc": True},
    "earrings": {"attrsDesc": True},
}

def build_code_index() -> dict:
    index = {}
    for i, e in enumerate(load_entries()):
        for raw in e["hsn_codes"]:
            code = re.sub(r"\D", "", str(raw))
            if 2 <= len(code) <= 6:
                index.setdefault(code, []).append(i)
    return index


def best_entry_for_code(code: str, title: str, description: str) -> int:
    """Among corpus entries sharing a code, pick the entry whose description is
    closest to the product text. Strongly prefer entries where the target code
    is the primary code rather than a secondary one (e.g. 7326 standalone vs
    7326 merged into a 7310 mathematical-boxes entry)."""
    code_index = build_code_index()
    idxs = code_index.get(code, [])
    if len(idxs) <= 1:
        return idxs[0] if idxs else -1
    text = " ".join((title, description)).lower()
    # Score each candidate: primary-code bonus + text overlap.
    def _score(idx: int) -> float:
        entry_codes = [re.sub(r"\D", "", str(c)) for c in entries_ref()[idx]["hsn_codes"]]
        # Position bonus: code appearing first (or as the only code) wins.
        pos = entry_codes.index(code) if code in entry_codes else len(entry_codes)
        pos_bonus = 0.5 if pos == 0 else 0.2 if pos <= 2 else 0.0
        # Text overlap bonus.
        text_bonus = sum(
            1 for t in re.findall(r"[a-zA-Z]+", text)
            if t in entries_ref()[idx]["description"].lower()
        ) * 0.01
        return pos_bonus + text_bonus
    return max(idxs, key=_score)


def classify_with_clip(image_sources: List[str]) -> "Optional[str]":
    """Run CLIP zero-shot over the curated product-type labels; return the id
    of the best matching type (with a minimum confidence) or None."""
    if not image_sources:
        return None
    try:
        from _clip_verify import clip_zero_shot, load_image
        from _category_specs import CATEGORY_SCHEMA as _CS, build_clip_taxonomy
        taxonomy = build_clip_taxonomy()  # [(id, label), ...]
        labels = [label for _, label in taxonomy]
        totals = {pid: 0.0 for pid, _ in taxonomy}
        loaded = 0
        for source in image_sources[:3]:
            image = load_image(str(source))
            if image is None:
                continue
            scores = clip_zero_shot(image, labels)
            for (pid, _), label in zip(taxonomy, labels):
                totals[pid] += scores[label]
            loaded += 1
        if not loaded:
            return None
        totals = {k: v / loaded for k, v in totals.items()}
        best = max(totals.items(), key=lambda kv: kv[1])
        return best[0] if best[1] >= 0.25 else None
    except Exception as exc:
        print(f"[WARN] CLIP product-type classification unavailable: {exc}", file=sys.stderr)
        return None


def classify_with_clip_conf(
    image_sources: List[str],
) -> Tuple["Optional[str]", float]:
    """Like classify_with_clip but also returns the winning label's confidence
    so callers can distinguish a confident image match from a coin-flip."""
    if not image_sources:
        return None, 0.0
    try:
        from _clip_verify import clip_zero_shot, load_image
        from _category_specs import CATEGORY_SCHEMA as _CS, build_clip_taxonomy
        taxonomy = build_clip_taxonomy()
        labels = [label for _, label in taxonomy]
        totals = {pid: 0.0 for pid, _ in taxonomy}
        loaded = 0
        for source in image_sources[:3]:
            image = load_image(str(source))
            if image is None:
                continue
            scores = clip_zero_shot(image, labels)
            for (pid, _), label in zip(taxonomy, labels):
                totals[pid] += scores[label]
            loaded += 1
        if not loaded:
            return None, 0.0
        totals = {k: v / loaded for k, v in totals.items()}
        best_pid, best_conf = max(totals.items(), key=lambda kv: kv[1])
        if best_conf < 0.25:
            return None, best_conf
        return best_pid, best_conf
    except Exception as exc:
        print(f"[WARN] CLIP product-type classification unavailable: {exc}", file=sys.stderr)
        return None, 0.0


# ─── CLIP colour / material detection ────────────────────────────────────

_COLOUR_LABELS = [
    "a yellow product", "a pink product", "a red product", "a blue product",
    "a green product", "a black product", "a white product", "a purple product",
    "an orange product", "a brown product", "a grey product", "a gold product",
    "a silver product", "a beige product", "a navy product", "a maroon product",
]
_COLOUR_MAP = {
    "a yellow product": "yellow", "a pink product": "pink",
    "a red product": "red", "a blue product": "blue",
    "a green product": "green", "a black product": "black",
    "a white product": "white", "a purple product": "purple",
    "an orange product": "orange", "a brown product": "brown",
    "a grey product": "grey", "a gold product": "gold",
    "a silver product": "silver", "a beige product": "beige",
    "a navy product": "navy", "a maroon product": "maroon",
}

_MATERIAL_LABELS = [
    "a cotton product", "a plastic product", "a silicone product",
    "a steel product", "a metal product", "a wooden product",
    "a ceramic product", "a glass product", "a leather product",
    "a fabric product", "a rubber product", "a nylon product",
    "a velvet product", "a wool product", "a copper product",
]
_MATERIAL_MAP = {k: k.replace("a ", "").replace(" product", "") for k in _MATERIAL_LABELS}

# Combined label set: one CLIP pass instead of two separate ones.
_COMBINED_LABELS = _COLOUR_LABELS + _MATERIAL_LABELS


def detect_visual_attributes(image_sources: List[str]) -> tuple:
    """Detect colours AND materials in one CLIP pass (downloads images once)."""
    if not image_sources:
        return [], []
    try:
        from _clip_verify import clip_zero_shot, load_image
        totals = {lbl: 0.0 for lbl in _COMBINED_LABELS}
        loaded = 0
        for source in image_sources[:3]:
            image = load_image(str(source))
            if image is None:
                continue
            scores = clip_zero_shot(image, _COMBINED_LABELS)
            for lbl in _COMBINED_LABELS:
                totals[lbl] += scores.get(lbl, 0)
            loaded += 1
        if not loaded:
            return [], []
        colours = [
            _COLOUR_MAP[lbl]
            for lbl, score in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            if lbl in _COLOUR_MAP and score / loaded >= 0.18
        ][:3]
        materials = [
            _MATERIAL_MAP[lbl]
            for lbl, score in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            if lbl in _MATERIAL_MAP and score / loaded >= 0.18
        ][:3]
        return colours, materials
    except Exception:
        return [], []


def detect_colours_clip(image_sources: List[str]) -> List[str]:
    """Return colours detected in the product images (CLIP zero-shot)."""
    colours, _ = detect_visual_attributes(image_sources)
    return colours


def detect_materials_clip(image_sources: List[str]) -> List[str]:
    """Return materials detected in the product images (CLIP zero-shot)."""
    _, materials = detect_visual_attributes(image_sources)
    return materials


# ── Product Context Engine — CLIP zero-shot taxonomy ─────────────────────

_PRODUCT_TYPE_TAXONOMY = [
    # Apparel
    "shirt", "t-shirt", "kurti", "kurta", "saree", "lehenga", "dress", "top", "blouse",
    "jacket", "coat", "sweater", "hoodie", "jeans", "trousers", "shorts", "skirt",
    "leggings", "suit", "blazer", "track pants", "underwear", "socks", "scarf", "stole",
    "dupatta", "salwar suit", "night dress", "night suit",
    # Footwear
    "shoes", "sneakers", "sandals", "slippers", "heels", "boots", "flip flops",
    "loafers", "sports shoes", "running shoes", "casual shoes", "formal shoes",
    # Bags & accessories
    "handbag", "backpack", "wallet", "purse", "tote bag", "laptop bag", "sling bag",
    "belt", "watch", "sunglasses", "jewellery set", "earrings", "necklace", "bracelet",
    "ring", "bangle", "hair accessories", "anklet", "keychain",
    # Home & kitchen
    "bowl", "cup", "mug", "plate", "glass", "bottle", "utensil set", "knife",
    "cutting board", "pan", "pot", "cookware", "lunch box", "water bottle",
    "spray bottle", "storage container", "jar", "basket", "organizer", "rack",
    "cushion", "cushion cover", "bedsheet", "pillow", "blanket", "towel", "curtain",
    "doormat", "carpet", "table cloth", "napkin", "coaster set",
    # Electronics
    "mobile phone", "charger", "cable", "earphones", "headphones", "speaker",
    "power bank", "phone case", "screen guard", "smartwatch", "USB hub", "adapter",
    "lamp", "LED light", "bulb", "camera", "tripod", "mouse", "keyboard",
    # Beauty & personal care
    "makeup kit", "lipstick", "nail polish", "perfume", "deodorant", "hair oil",
    "face cream", "soap", "shampoo", "comb", "hair brush", "makeup brush",
    "razor", "trimmer", "hair dryer", "hair straightener",
    # Baby & kids
    "baby clothes", "baby blanket", "diaper", "baby toy", "feeding bottle",
    "baby powder", "baby shampoo", "baby oil", "baby powder puff",
    # Toys & games
    "teddy bear", "soft toy", "doll", "action figure", "puzzle", "board game",
    "car toy", "building blocks", "ball", "balloon", "educational toy",
    # Sports & fitness
    "dumbbell", "yoga mat", "resistance band", "skipping rope", "gym bag",
    "sports ball", "cricket bat", "badminton racket", "swimming goggles",
    # Food & beverages
    "tea", "coffee", "spice box", "dry fruits", "snacks", "chocolate", "sweets",
    # Stationery
    "notebook", "pen", "pencil", "marker", "highlighter", "eraser", "sharpener",
    "ruler", "sticky notes", "folder", "file", "art supplies", "paint brush",
    # Decor & gifting
    "photo frame", "showpiece", "idol", "wall hanging", "wind chime", "candle",
    "diy kit", "gift hamper", "greeting card", "return gift", "party favor",
    "friendship band", "rakhi", "thread", "craft kit", "beads",
    # Other
    "tool", "hardware", "car accessory", "bike accessory", "pet product",
    "grocery", "medicine", "book", "magazine",
]

_USE_CASE_TAXONOMY = [
    "gift", "daily use", "office use", "travel", "sports", "party", "wedding",
    "festival", "kitchen use", "bathroom use", "outdoor use", "indoor use",
    "storage", "organization", "cleaning", "cooking", "serving", "decorative",
    "educational", "professional use", "casual wear", "formal wear",
    "winter wear", "summer wear", "baby care", "personal care", "grooming",
]

_DESIGN_TAXONOMY = [
    "floral", "striped", "solid color", "geometric", "animal print", "polka dot",
    "embroidered", "printed", "plain", "patterned", "traditional", "modern",
    "vintage", "minimalist", "glossy", "matte", "metallic", "wooden finish",
    "transparent", "opaque", "glittery", "shiny",
]

_TARGET_GENDER = ["men", "women", "unisex", "boys", "girls", "kids"]

_AGE_GROUP = ["baby", "toddler", "kids", "teens", "adults", "seniors", "all ages"]


def describe_product_context(
    title: str,
    description: str,
    images: Optional[List[str]] = None,
    brand: str = "",
    category: str = "",
    product_type_label: str = "",
) -> dict:
    """Use CLIP + text analysis to produce a comprehensive product profile.

    Returns: {
        "product_type": { "label": str, "confidence": float },
        "colors": [{ "label": str, "confidence": float }],
        "materials": [{ "label": str, "confidence": float }],
        "use_case": { "label": str, "confidence": float },
        "design": { "label": str, "confidence": float },
        "target_gender": { "label": str, "confidence": float },
        "age_group": { "label": str, "confidence": float },
        "log": [str, ...],    # human-readable analysis lines
    }
    """
    log: list[str] = []
    result: dict = {}

    def _clsp(images_list, taxonomy, threshold=0.10):
        """Run DINOv2+CLIP combined classification against a taxonomy."""
        if not images_list:
            return []
        try:
            from _clip_verify import dinov2_clip_classify
            return dinov2_clip_classify(images_list, taxonomy, threshold=threshold)
        except Exception as e:
            log.append(f"  DINOv2+CLIP error: {e}")
            return []

    log.append(f"Product Context Analysis for: \"{title[:60]}\"")
    log.append(f"  Brand: {brand or '(unknown)'}")
    log.append(f"  Category: {category or '(unknown)'}")
    log.append(f"  HSN-derived type: {product_type_label or '(unknown)'}")

    # ── Text-based analysis ──────────────────────────────────────────
    combined_text = f"{title} {description}".lower()
    text_colors = list(set(_extract_colors(combined_text)))
    text_materials = [
        m for m in MATERIALS_LIST
        if re.search(r'\b' + re.escape(m) + r'\b', combined_text, re.IGNORECASE)
    ]
    log.append(f"  Text mentions colors: {text_colors or '(none)'}")
    log.append(f"  Text mentions materials: {text_materials or '(none)'}")

    # ── Image-based analysis (CLIP) ─────────────────────────────────
    has_images = bool(images and any(images))
    if has_images:
        log.append(f"  Images: {len([u for u in images if u])} provided")
        log.append(f"  Running CLIP taxonomy classification...")

        # Narrow product-type taxonomy based on category + HSN type hint
        narrowed_types = list(_PRODUCT_TYPE_TAXONOMY)
        cat_lower = category.lower()
        type_lower = product_type_label.lower()
        # Keep all labels that match the category or product type hint
        relevant = [t for t in narrowed_types if (
            t.lower() in cat_lower or cat_lower in t.lower() or
            t.lower() in type_lower or type_lower in t.lower()
        )]
        if relevant:
            narrowed_types = relevant + narrowed_types
            log.append(f"  Category-guided taxonomy: {', '.join(narrowed_types[:10])}...")

        result["product_type"] = _clsp(images, narrowed_types, threshold=0.08)
        result["use_case"] = _clsp(images, _USE_CASE_TAXONOMY, threshold=0.08)
        result["design"] = _clsp(images, _DESIGN_TAXONOMY, threshold=0.08)
        result["target_gender"] = _clsp(images, _TARGET_GENDER, threshold=0.10)
        result["age_group"] = _clsp(images, _AGE_GROUP, threshold=0.10)

        # Colors and materials via existing CLIP helpers (higher threshold)
        clip_colours = detect_colours_clip([u for u in images if u])
        clip_materials = detect_materials_clip([u for u in images if u])

        result["colors"] = [
            {"label": c, "confidence": round(1.0 - i * 0.15, 3)}
            for i, c in enumerate(clip_colours)
        ]
        result["materials"] = [
            {"label": m, "confidence": round(1.0 - i * 0.15, 3)}
            for i, m in enumerate(clip_materials)
        ]
    else:
        log.append(f"  Images: none — using text-only analysis")
        result["colors"] = [{"label": c, "confidence": 0.7} for c in text_colors]
        result["materials"] = [{"label": m, "confidence": 0.7} for m in text_materials]

    # ── Build human-readable summary ────────────────────────────────
    summary_parts = []
    clip_type = result.get("product_type", [])
    if clip_type and clip_type[0]["confidence"] >= 0.50:
        summary_parts.append(f"Product type: {clip_type[0]['label']} ({clip_type[0]['confidence']:.0%})")
    elif product_type_label:
        summary_parts.append(f"Product type (HSN): {product_type_label}")
    elif clip_type:
        summary_parts.append(f"Product type (low confidence): {clip_type[0]['label']} ({clip_type[0]['confidence']:.0%})")
    else:
        summary_parts.append(f"Product type: unknown")
    if result.get("colors"):
        colors_str = ", ".join(f"{c['label']}" for c in result["colors"][:3])
        summary_parts.append(f"Colors: {colors_str}")
    if result.get("materials"):
        mats_str = ", ".join(f"{m['label']}" for m in result["materials"][:3])
        summary_parts.append(f"Materials: {mats_str}")
    if result.get("use_case"):
        summary_parts.append(f"Use case: {result['use_case'][0]['label']}")
    if result.get("design"):
        summary_parts.append(f"Design: {result['design'][0]['label']}")
    if result.get("target_gender"):
        summary_parts.append(f"Target: {result['target_gender'][0]['label']}")
    if result.get("age_group"):
        summary_parts.append(f"Age group: {result['age_group'][0]['label']}")

    summary = " | ".join(summary_parts) if summary_parts else "Insufficient data"
    log.append(f"  SUMMARY: {summary}")

    # ── Flag contradictions ─────────────────────────────────────────
    if has_images and text_colors and clip_colours:
        missing = [c for c in text_colors if c not in clip_colours]
        if missing:
            log.append(f"  WARNING: Text mentions colors not seen in images: {missing}")

    if has_images and text_materials and clip_materials:
        missing = [m for m in text_materials if m.lower() not in [cm.lower() for cm in clip_materials]]
        if missing:
            log.append(f"  WARNING: Text mentions materials not seen in images: {missing}")

    result["log"] = log
    return result
    """Match the title/description against the curated product-type keywords."""
    text = (title + " " + description).lower()
    best = None
    best_score = 0
    for pid, _, keywords, _, _ in PRODUCT_TYPES:
        for kw in keywords:
            if kw in text:
                score = 1.0 if kw.split()[-1] in title.lower() else 0.6
                if score > best_score:
                    best_score = score
                    best = pid
    return best


def _kw_re(kw: str) -> "re.Pattern":
    """Word-boundary regex for a keyword, so 'top' does not match inside
    'stopper' and 'mat' does not match inside 'mattress'."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(kw.lower())}(?![a-z0-9])")


def keyword_match_type(title: str, description: str) -> "Optional[str]":
    """Score PRODUCT_TYPES + CATEGORY_SCHEMA by keyword occurrence.
    Keywords match on word boundaries only — 'top' must not match inside
    'stopper'. Longer keywords win; a match on the title outweighs one in
    the description. Returns the best matching product-type id, or None."""
    title_lower = (title or "").lower()
    text_lower = title_lower + "\n" + (description or "").lower()
    best_id = None
    best_score = 0.0

    all_types: List[tuple] = []
    for row in PRODUCT_TYPES:
        all_types.append((row[0], row[2]))  # (id, keywords)
    from _category_specs import CATEGORY_SCHEMA as _CS2
    for cid, cdata in _CS2.items():
        if cid not in {t[0] for t in all_types}:
            all_types.append((cid, cdata.get("keywords", [])))

    for pid, keywords in all_types:
        score = 0.0
        for kw in keywords:
            if kw and _kw_re(kw).search(text_lower):
                score += len(kw) * 2.0 if _kw_re(kw).search(title_lower) else len(kw)
        if score > best_score:
            best_score = score
            best_id = pid
    return best_id if best_score > 0 else None


def suggest(
    query: str,
    title: str = "",
    description: str = "",
    visual_context: str = "",
    image_sources: Optional[List[str]] = None,
    v2_type: str = "",
) -> List[dict]:
    """Curated product-type classification driven by CLIP images and title
    keywords, with text-embedding fallback when no type matches."""
    entries = load_entries()
    code_index = build_code_index()

    image_sources = image_sources or []
    clip_type = classify_with_clip(image_sources) if image_sources else None
    kw_type = keyword_match_type(title, description)
    chosen_type = clip_type or kw_type

    # Find the matched type's HSN candidates, keeping track of which specific
    # code was matched (some corpus entries share multiple codes and we want the
    # one we explicitly searched for, not the entry's first listed code).
    candidates: List[tuple] = []  # (entry_idx, matched_hsn)
    candidate_idxs: List[int] = []
    if chosen_type:
        for row in PRODUCT_TYPES:
            if row[0] == chosen_type:
                for hsn in row[3]:
                    idx = best_entry_for_code(hsn, title, description)
                    if idx >= 0 and idx not in candidate_idxs:
                        candidates.append((idx, hsn))
                        candidate_idxs.append(idx)
    # Also search V2 CATEGORY_SCHEMA for HSN codes
        from _category_specs import CATEGORY_SCHEMA as _CS3
        types_to_check = [chosen_type]
        if v2_type and v2_type not in types_to_check:
            types_to_check.append(v2_type)
        for t in types_to_check:
            if t in _CS3:
                for hsn in _CS3[t].get("hsn", []):
                    idx = best_entry_for_code(hsn, title, description)
                    if idx >= 0 and idx not in candidate_idxs:
                        candidates.append((idx, hsn))
                        candidate_idxs.append(idx)

    if candidates:
        # Rank the candidate entries by title-weighted text similarity.
        title_query = normalize_query(title)
        description_query = normalize_query(description)
        if title_query and description_query:
            query_texts = [title_query, description_query, normalize_query(query)]
            weights = (0.62, 0.18, 0.20)
        elif title_query:
            query_texts = [title_query]
            weights = (1.0,)
        else:
            query_texts = [normalize_query(query)]
            weights = (1.0,)
        query_vecs = embed_texts(query_texts)
        vecs = get_corpus_vecs().astype(np.float64)
        cand_scores = {}
        preferred_hsn = {}
        for idx, matched_code in candidates:
            score = 0.0
            for w, qv in zip(weights, query_vecs):
                score += w * float(vecs[idx] @ qv.astype(np.float64))
            cand_scores[idx] = score
            preferred_hsn[idx] = matched_code
        # Curated priority is authoritative within a matched type; text
        # similarity only breaks ties.
        priority = {idx: rank_pos for rank_pos, (idx, _) in enumerate(candidates)}
        ranked = sorted(
            [idx for idx, _ in candidates],
            key=lambda idx: (priority.get(idx, 99), -cand_scores[idx]),
        )
        suggestions = []
        seen_codes = set()
        for idx in ranked:
            e = entries[idx]
            # Use the specifically matched code, not the entry's first code.
            best_code = preferred_hsn.get(idx)
            if not best_code or best_code in seen_codes:
                continue
            seen_codes.add(best_code)
            suggestions.append({
                "hsn": best_code,
                "description": e["description"],
                "gst_rate": e["gst_rate"],
                "central_tax": e["central_tax"],
                "schedule": e["schedule"],
                "confidence": round(min(0.96, float(cand_scores[idx]) * 2.5 + 0.30), 4),
                "rank": len(suggestions) + 1,
            })
            if len(suggestions) == TOPK:
                break
        if suggestions:
            return suggestions

    # General path for products that do not match a curated type.
    return _general_suggest(title, description, query, image_sources)


def _product_head(title: str) -> str:
    """Extract the final meaningful noun from the title (the sellable object)."""
    tokens = [t for t in re.findall(r"[a-zA-Z]+", title.lower()) if t not in STOPWORDS]
    for t in reversed(tokens):
        if t not in {"pack", "piece", "pieces", "pcs", "set", "combo", "ml", "gm", "kg", "inch", "box"}:
            return t
    return ""


def _general_suggest(title: str, description: str, query: str, image_sources: List[str]) -> List[dict]:
    """Classify any product: text embeds a head-emphasized query (so content
    words like 'oil'/'soap' do not dominate the product noun), then CLIP
    re-ranks the top text candidates against the product images."""
    entries = load_entries()
    vecs = get_corpus_vecs()

    head = _product_head(title)
    head_query = (head + " " + head + " " + head + " ") if head else ""
    title_query = normalize_query(head_query + title)
    description_query = normalize_query(description)
    full_query = normalize_query(query)

    if title_query and description_query:
        query_texts = [title_query, description_query, full_query]
        weights = (0.66, 0.14, 0.20)
    elif title_query:
        query_texts = [title_query]
        weights = (1.0,)
    else:
        query_texts = [full_query]
        weights = (1.0,)
    query_vecs = embed_texts(query_texts)

    with np.errstate(all="ignore"):
        corpus = vecs.astype(np.float64)
        text_sims = np.zeros(len(entries), dtype=np.float64)
        for w, qv in zip(weights, query_vecs):
            text_sims += w * (corpus @ qv.astype(np.float64))
    text_sims = np.nan_to_num(text_sims, nan=-1.0, posinf=-1.0, neginf=-1.0)

    # Filter out vague "residual"/catch-all entries that never correspond to a
    # real retail product (they dominate raw image/text matching).
    vague = ("not specified", "not elsewhere", "residual", "other than those", "goods which are not")
    candidate_idx = np.argsort(text_sims)[::-1][:40]
    candidates = [i for i in candidate_idx if not any(v in entries[i]["description"].lower() for v in vague)]

    if not candidates:
        candidates = [i for i in candidate_idx]

    clip_sims = clip_image_scores(image_sources)
    if clip_sims is not None:
        # Re-rank text candidates by CLIP image evidence (weighted fusion).
        clip_values = np.array([clip_sims[i] for i in candidates], dtype=np.float64)
        text_values = np.array([text_sims[i] for i in candidates], dtype=np.float64)
        clip_norm = minmax(clip_values)
        text_norm = minmax(text_values)
        fused = {
            i: (0.45 * text_norm[rank] + 0.55 * clip_norm[rank])
            for rank, i in enumerate(candidates)
        }
        ranked = sorted(candidates, key=lambda i: fused[i], reverse=True)
    else:
        ranked = sorted(candidates, key=lambda i: text_sims[i], reverse=True)

    suggestions = []
    seen_codes = set()
    for idx in ranked:
        e = entries[idx]
        valid_codes = []
        for raw_code in e.get("hsn_codes", []):
            code = re.sub(r"\D", "", str(raw_code))
            if 4 <= len(code) <= 6 and code not in valid_codes:
                valid_codes.append(code)
        if not valid_codes or valid_codes[0] in seen_codes:
            continue
        seen_codes.add(valid_codes[0])
        suggestions.append({
            "hsn": valid_codes[0],
            "description": e["description"],
            "gst_rate": e["gst_rate"],
            "central_tax": e["central_tax"],
            "schedule": e["schedule"],
            "confidence": round(min(0.94, float(text_sims[idx]) * 1.6 + 0.22), 4),
            "rank": len(suggestions) + 1,
        })
        if len(suggestions) == TOPK:
            break
    return suggestions


def get_clip_text_vecs() -> np.ndarray:
    """CLIP text embeddings for every HSN entry description (cached to disk)."""
    entries = load_entries()
    if os.path.exists(CLIP_TEXT_CACHE):
        cached = np.load(CLIP_TEXT_CACHE, allow_pickle=False)
        if cached.shape[0] == len(entries):
            return cached
    import torch
    from _clip_verify import get_clip
    model, processor = get_clip()
    device = next(model.parameters()).device
    prompts = ["a photo of " + e["description"][:250] for e in entries]
    print(f"[INFO] Computing CLIP text embeddings for {len(prompts)} HSN descriptions...", file=sys.stderr)
    t0 = time.time()
    vecs = []
    B = 64
    for i in range(0, len(prompts), B):
        inputs = processor(text=prompts[i:i + B], return_tensors="pt", padding=True, truncation=True).to(device)
        with torch.no_grad():
            feats = model.get_text_features(**inputs)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        vecs.append(feats.cpu().numpy())
    out = np.concatenate(vecs, axis=0)
    np.save(CLIP_TEXT_CACHE, out)
    print(f"[INFO] CLIP text embeddings ready in {time.time() - t0:.1f}s", file=sys.stderr)
    return out


def clip_image_scores(image_sources: List[str]) -> "Optional[np.ndarray]":
    """Average CLIP similarity of up to 3 product images vs all HSN descriptions."""
    if not image_sources:
        return None
    try:
        import torch
        from _clip_verify import get_clip, load_image
        model, processor = get_clip()
        device = next(model.parameters()).device
        text_vecs = get_clip_text_vecs()
        img_feats = []
        for source in image_sources[:3]:
            image = load_image(str(source))
            if image is None:
                continue
            inputs = processor(images=image, return_tensors="pt").to(device)
            with torch.no_grad():
                feat = model.get_image_features(**inputs)
            feat = feat / feat.norm(dim=-1, keepdim=True)
            img_feats.append(feat.cpu().numpy()[0])
        if not img_feats:
            return None
        mean_img = np.mean(np.stack(img_feats), axis=0)
        mean_img = mean_img / max(float(np.linalg.norm(mean_img)), 1e-12)
        return text_vecs @ mean_img
    except Exception as exc:
        print(f"[WARN] CLIP image scoring unavailable: {exc}", file=sys.stderr)
        return None


def minmax(x: np.ndarray) -> np.ndarray:
    lo, hi = float(x.min()), float(x.max())
    return (x - lo) / max(1e-9, hi - lo)


def classify_title_with_clip(title: str) -> "Optional[str]":
    """Classify a product TITLE against the curated PRODUCT_TYPES labels using
    CLIP's text encoder — the same model space as the image classifier. This
    gives us what the title SAYS the product is, so we can compare with what
    the IMAGES show."""
    if not title:
        return None
    try:
        import torch
        from _clip_verify import get_clip
        model, processor = get_clip()
        device = next(model.parameters()).device
        labels = [pt[1] for pt in PRODUCT_TYPES]
        # Encode title + all labels
        all_texts = [title] + labels
        inputs = processor(text=all_texts, return_tensors="pt", padding=True, truncation=True).to(device)
        with torch.no_grad():
            feats = model.get_text_features(**inputs)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        title_vec = feats[0]  # first row = title
        label_vecs = feats[1:]  # remaining rows = labels
        sims = (label_vecs @ title_vec).cpu().numpy()
        best_idx = int(np.argmax(sims))
        return PRODUCT_TYPES[best_idx][0]
    except Exception as exc:
        print(f"[WARN] CLIP title classification unavailable: {exc}", file=sys.stderr)
        return None


OP_CLAIM_PHRASES = [
    "free return or exchange within 30 days",
    "money back guarantee full refund",
    "free shipping and delivery",
    "cash on delivery available",
    "no cost EMI payment option",
    "lifetime warranty guaranteed",
    "next day fast delivery shipping",
    "hassle free easy return policy",
    "contact us to place order call now",
    "limited time sale offer discount",
    "we will replace or refund your item",
    "doorstep delivery to your home",
    "satisfaction guaranteed or money back",
    "COD available pay after delivery",
    "express delivery within hours",
    "buy now pay later no cost emi",
    "free replacement under warranty",
    "flat discount off limited offer",
    "cheapest lowest price guarantee",
    "priority express fast dispatch",
]
_claim_vecs: Optional[np.ndarray] = None

# Two reference embeddings to classify text intent: marketing/policy vs product.
CLAIM_INTENT_PROMPTS = [
    "marketing claims about delivery returns shipping offers warranties policies",
    "product description about features materials specifications uses gifts",
]


def get_claim_vecs() -> np.ndarray:
    global _claim_vecs
    if _claim_vecs is None:
        _claim_vecs = embed_texts(OP_CLAIM_PHRASES)
    return _claim_vecs


def _text_is_claim_oriented(text: str) -> bool:
    """Classify whether a text is primarily marketing/policy (claim-oriented)
    or a product description. Returns True if claim-oriented."""
    try:
        prompts_vecs = embed_texts(CLAIM_INTENT_PROMPTS)
        text_vec = embed_texts([text])[0]
        # Cosine sim to "marketing claims" prompt (index 0) vs "product" (index 1)
        claim_sim = float(np.dot(text_vec, prompts_vecs[0]))
        product_sim = float(np.dot(text_vec, prompts_vecs[1]))
        return claim_sim > product_sim
    except Exception:
        return True  # conservative: check claims if we can't classify

def detect_op_claims(text: str, min_len: int = 15, min_text_len: int = 20) -> List[str]:
    """Detect operations/marketing claims in text using MiniLM semantic
    similarity. Entire text is first classified as claim-oriented or product-
    oriented; product-oriented text gets a much higher threshold, eliminating
    false positives for titles like 'Birthday Return Gifts' or 'Design Will
    be Dispatch' without needing per-word exception lists.

    min_len filters out short/noisy fragments; pass a smaller value for OCR
    line-based text where each block is a separate, meaningfully short line.
    min_text_len is the minimum full-text length before analysis is skipped."""
    if not text or len(text.strip()) < min_text_len:
        return []

    sentences = re.split(r"[.!\n]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= min_len]
    if not sentences:
        return []

    # Filter out sentences that are contextually exempt (e.g. "return gifts",
    # "birthday", "party favors") — these are product descriptions, not claims.
    sentences = [s for s in sentences if not _CLAIM_CONTEXT.search(s)]

    # Keyword pre-filter: if the text contains claim-signalling words, use
    # the lower threshold even if the overall embedding seems product-oriented.
    claim_oriented = _text_is_claim_oriented(text)
    if not claim_oriented:
        lower = text.lower()
        if any(
            kw in lower for kw in (
                "free", "shipping", "delivery", "cod", "guarantee",
                "returns", "exchange", "refund", "emi", "discount",
                "offer", "dispatch", "cheapest", "lowest", "best price",
                "satisfaction", "hassle", "doorstep", "express",
                "limited time", "sale", "buy now", "pay later",
                "!!!", "★★", "✪",
            )
        ):
            claim_oriented = True

    threshold = 0.52 if claim_oriented else 0.62

    try:
        sentence_vecs = embed_texts(sentences)
        claim_vecs = get_claim_vecs()
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            sims = sentence_vecs @ claim_vecs.T
        max_sims = sims.max(axis=1)
        flagged = []
        for i, sim in enumerate(max_sims):
            if sim >= threshold:
                flagged.append(sentences[i])

        # N-gram fallback: for long mixed text where sentence-level
        # embedding is diluted, check 3-5 word windows against claim
        # vectors to catch isolated claim phrases.
        if not flagged and claim_oriented and len(text) > 40:
            words = text.split()
            if len(words) >= 6:
                ngrams = []
                ngram_spans = []
                for n in (3, 4, 5):
                    for start in range(0, len(words) - n + 1):
                        ngrams.append(" ".join(words[start:start + n]))
                        ngram_spans.append((start, start + n))
                if ngrams:
                    ng_vecs = embed_texts(ngrams)
                    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
                        ng_sims = ng_vecs @ claim_vecs.T
                    ng_max = ng_sims.max(axis=1)
                    # Collect n-gram spans above threshold
                    claimed_spans: set = set()
                    for j, sim in enumerate(ng_max):
                        if sim >= threshold:
                            claimed_spans.add(ngram_spans[j])
                    # Merge adjacent claimed spans into continuous ranges
                    if claimed_spans:
                        merged = []
                        current = None
                        for start, end in sorted(claimed_spans):
                            if current and start <= current[1]:
                                current = (current[0], max(current[1], end))
                            else:
                                if current:
                                    merged.append(current)
                                current = (start, end)
                        if current:
                            merged.append(current)
                        # Extract claimed word ranges from original text
                        for start, end in merged:
                            claimed_phrase = " ".join(words[start:end])
                            if claimed_phrase and claimed_phrase not in flagged:
                                flagged.append(claimed_phrase)

        return flagged
    except Exception:
        return []


# ─── Text Correction (MiniLM-based) ───────────────────────────────────────
# Uses the same semantic claim-detection pipeline as detect_op_claims to
# identify and remove marketing/ops claims from titles and descriptions,
# then reconstructs clean output. The MiniLM model understands context
# ("Birthday Return Gifts" vs "free returns policy"), unlike keyword matching.

# Regex for URL/domain patterns — mechanical cleanup before semantic analysis.
_URL_RE = re.compile(
    r"https?://\S+"  # explicit URLs
    r"|\bwww\.\S+\.\S+\b"  # www.domain.tld
    r"|\b\S+\.(com|in|net|org|co|io)\b",  # bare domain words
    re.IGNORECASE,
)

_ALLCAPS_RE = re.compile(r"^[A-Z\s\d\W]{10,}$")

# Context words that make claim-like words innocent (same guard as opsContextSafe in JS)
_CLAIM_CONTEXT = re.compile(
    r"\b(design|color|colour|random|assorted|mixed|variant|style|pattern|"
    r"variety|gift|gifts|birthday|party|favor|favors|return\s*gift|kanjak|"
    r"giveaway|goodie|goody)\b",
    re.IGNORECASE,
)


# ── Comprehensive claim/ops stripping ────────────────────────────────

_GLOBAL_CLAIM_PATTERNS = re.compile(
    r"\b(?:number\s*1|#1|guaranteed|world'?s\s*best|miracle|instant\s*result)\b"
    r"|\b(?:free\s*(?:gift|exchange|returns?|replacements?|delivery|shipping))\b"
    r"|\b(?:buy\s*1\s*get\s*1|limited\s*offer|hurry)\b"
    r"|\b(?:money\s*back|cash\s*on\s*delivery|cod\s*available)\b"
    r"|\b(?:no\s*cost\s*emi|easy\s*emi|pay\s*on\s*delivery)\b"
    r"|\b\d+\s*days?\s+(?:(?:free\s+)?(?:exchange|replacement|return|refund|delivery)(?:\s+or\s+(?:free\s+)?(?:exchange|replacement|return|refund|delivery))?|or\s+(?:free\s+)?(?:exchange|replacement|return|refund|delivery))\b"
    r"|\b(?:fast\s*delivery|express\s*delivery|same\s*day\s*delivery|next\s*day\s*delivery)\b"
    r"|\b(?:cashback|price\s*match|lowest\s*price|cheapest|lifetime\s*(?:warranty|guarantee))\b"
    r"|\b(?:flat\s*\d+\s*%\s*off|upto\s*\d+\s*%\s*(?:off|discount))\b"
    r"|\b(?:best\s*prices?|best\s*quality|satisfaction\s*guaranteed|100%\s*(?:genuine|original|authentic))\b"
    r"|\b(?:free\s*(?:returns?|exchanges?|replacements?))\b"
    r"|\b(?:easy|hassle\s*free|simple|quick|no\s*questions?\s*asked|seamless)\s*(?:returns?|exchanges?|replacements?)\b"
    r"|\b(?:returns?|exchanges?|replacements?)\s*policy\b"
    r"|\bget\s*(?:a|your)\s*(?:replacements?|refunds?|money)\b"
    r"|\b(?:full|100%)\s*refunds?\b"
    r"|\b(?:money|amount|payment)\s*(?:back|returned|refunded)\b"
    r"|\bno\s*questions?\s*asked\s*(?:returns?|refunds?|exchanges?)\b"
    r"|\b(?:doorstep|home|express|fast|quick|rapid|speed|priority)\s*delivery\b"
    r"|\b(?:dispatch|ship|deliver)\s*(?:within|in)\s*\d+\b"
    r"|\bpay\s*(?:on|after)\s*(?:delivery|receipt)\b"
    r"|\b(?:easy|zero\s*cost|available)\s*emi\b"
    r"|\b(?:limited|lifetime|\d+\s*year|\d+\s*month|\d+\s*day)\s*(?:warranty|guarantee)\b"
    r"|\bwarranty\s*(?:covered|cover|covers|included|provided)\b"
    r"|\blife\s*time\s*(?:warranty|guarantee|replacements?)\b"
    r"|\b(?:call|whatsapp|contact|dm|inbox)\s*(?:us|now|for|to\s*order)\b"
    r"|\bbulk\s*(?:order|purchase|buy|discount)\b"
    r"|\b(?:wholesale|trade)\s*price\b"
    r"|\breplac(?:e\w*|ing)?\s*(?:within|in)\s*\d+\b"
    r"|\bwe\s*(?:will|shall|can|would|'ll|could)\s*(?:replace|exchange|refund|return|ship|dispatch|deliver|repair)\b"
    r"|(?:for|at|price[:\s]*|cost[:\s]*|mrp[:\s]*|₹|rs\.?)\s*(?:₹|rs\.?)?\s*\d+(?:[.,]\d+)*(?:\s*(?:/-|/)?\s*(?:₹|rs\.?)?\s*\d+(?:[.,]\d+)*)?"
    r"|\b(?:inr|rupees?|usd|dollars?|euros?|gbp|£)\s*\d+(?:[.,]\d+)*\b"
    r"|\bprice[:\s]*₹?\s*\d+(?:[.,]\d+)*\b"
    , re.IGNORECASE,
)

_GLOBAL_PROHIBITED = re.compile(
    r"\b(?:sex|nude|naked|porn|explicit|obscene|erotic|adult\s*content|illegal|counterfeit|fake|smuggled|defam|hate|racist)\b",
    re.IGNORECASE,
)

def _strip_all_claims(text: str) -> str:
    """Remove ALL flagged content: domains, ops claims, misleading claims,
    prohibited terms, and mechanical noise from any text."""
    # 1) Domain/URL patterns
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"(?:[:\s]+(?:at|from|on)\s*\S+\.(?:com|in|net)\b\s*:?\s*)", " ", text, flags=re.IGNORECASE)
    # 2) Ops and misleading claims
    text = _GLOBAL_CLAIM_PATTERNS.sub(" ", text)
    # 3) Prohibited terms
    text = _GLOBAL_PROHIBITED.sub(" ", text)
    # 4) Clean up separators and whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"^[,;|\-–—]+", "", text).strip()
    text = re.sub(r"[,;|\-–—]+$", "", text).strip()
    text = re.sub(r"\s*,\s*,", ",", text)
    text = re.sub(r"\s*[-–—]\s*[-–—]", "", text)
    text = re.sub(r"\s+(?:at|from|on|buy)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\.\s*){2,}", ". ", text)
    text = re.sub(r"^\s*\.\s*", "", text)
    return text.strip()


def _strip_mechanical(text: str) -> str:
    """Remove URLs, domains, and symbols only. Claims are stripped at sentence level."""
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"(?:[:\s]+(?:at|from|on)\s*\S+\.(?:com|in|net)\b\s*:?\s*)", " ", text, flags=re.IGNORECASE)
    # Underscores/pipes/backslashes are never legitimate in a product title
    # (CSV/import artifacts). \w includes underscore, so strip them explicitly.
    text = re.sub(r"[_|\\]+", " ", text)
    text = re.sub(r"[^\w\s\-.,()%°'\"&®©™–—!?]+", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*:\s*:", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s+(?:at|from|on|buy)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+from\s+\S+\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+in\s+\S+\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:Buy\s+|Shop\s+|Purchase\s+)", "", text, flags=re.IGNORECASE)
    return text.strip()


def _is_allcaps(text: str) -> bool:
    return bool(_ALLCAPS_RE.search(text))


def _to_title_case(text: str) -> str:
    """Title-case while preserving known acronyms."""
    ALWAYS_UPPER = {
        "USB", "HDMI", "LED", "LCD", "TV", "IPX", "HSN", "OTG", "PVC", "ABS",
        "COD", "EMI", "UPI", "RFID", "SIM", "GPS", "NFC",
    }

    def _fix(m: re.Match) -> str:
        word = m.group(0)
        if word.upper() in ALWAYS_UPPER:
            return word.upper()
        if re.fullmatch(r"\d+.*", word):
            return word
        return word[0].upper() + word[1:].lower()

    return re.sub(r"\b\w+(?:['\u2019]\w+)?\b", _fix, text)


def correct_product_text(
    title: str,
    description: str,
    brand: str = "",
    category: str = "",
    product_type_label: str = "",
    images: Optional[List[str]] = None,
    polish: bool = False,
    v2_category: str = "",
    use_qwen: bool = False,
) -> dict:
    """Correct title and description using MiniLM semantic claim detection
    and CLIP image verification for colour / material ground truth.

    When *polish* is True, the mechanically-cleaned output is passed through
    Flan-T5 for a final grammar/coherence pass.

    Returns dict with keys ``title`` (str or None) and ``description`` (str or None).
    None means no correction was needed / possible.
    """
    import numpy as np

    out_title = None
    out_desc = None
    log: list[str] = []
    sku = ""  # set by caller for logging
    qwen_specs: Dict[str, str] = {}

    def _log(msg: str) -> None:
        log.append(msg)
        print(f"[ANALYZE] {msg}", file=sys.stderr)

    # ── CLIP image verification ────────────────────────────────────────
    clip_colours: list[str] = []
    clip_materials: list[str] = []
    marqo_category = ""  # Marqo-derived category path for better spec extraction
    if images and any(images):
        _log("Running CLIP visual attribute detection...")
        clip_colours, clip_materials = detect_visual_attributes([u for u in images if u])
        if clip_colours:
            _log(f"  Colours: {', '.join(clip_colours)}")
        if clip_materials:
            _log(f"  Materials: {', '.join(clip_materials)}")

        # Marqo ecommerce embeddings for better product category understanding
        try:
            from _marqo_classifier import classify as _marqo_classify
            from _category_specs import CATEGORY_SCHEMA as _CAT_SCHEMA
            _marqo_labels = [data.get("full_path", cid) for cid, data in _CAT_SCHEMA.items()]
            _marqo_ids = list(_CAT_SCHEMA.keys())
            _marqo_results = _marqo_classify([u for u in images if u], _marqo_labels)
            if _marqo_results:
                marqo_label, marqo_score = _marqo_results[0]
                if marqo_score > 0.05:
                    marqo_category = _marqo_ids[_marqo_labels.index(marqo_label)]
                    _log(f"  Marqo classification: {marqo_label} (confidence={marqo_score:.2f})")
        except Exception as e:
            _log(f"  Marqo classification skipped: {e}")

    # ── Product context engine ────────────────────────────────────────
    # Run CLIP zero-shot taxonomy to understand what the product actually is
    ctx = describe_product_context(
        title=title, description=description, images=images,
        brand=brand, category=category, product_type_label=product_type_label,
    )
    for ctx_line in ctx.get("log", []):
        _log(ctx_line)

    # Map the authoritative product_type_label back to its PRODUCT_TYPES id so
    # the Title accuracy correction uses the same taxonomy as the flag.
    detected_type = next(
        (row[0] for row in PRODUCT_TYPES if row[1] == product_type_label),
        None,
    ) or keyword_match_type(title or "", description or "")

    # ── TITLE ──────────────────────────────────────────────────────────
    if title and title.strip():
        t = _strip_mechanical(title)
        t = _strip_all_claims(t)
        if _is_allcaps(t):
            t = _to_title_case(t)

        # Inject brand if missing from title (flagged as "Brand not found in title")
        if brand and brand.strip():
            brand_clean = brand.strip()
            if brand_clean.lower() not in t.lower():
                # Check if the title already starts with a DIFFERENT brand-like word
                # (e.g. title says "Techtiq ..." but sheet brand is "calyxia").
                # In that case it's a brand MISMATCH — flag it but do NOT prepend
                # a second brand name.
                first_word = re.match(r"^([A-Za-z][A-Za-z0-9&.'-]{2,})\b", t)
                first_lower = first_word.group(1).lower() if first_word else ""
                # Common descriptor/adjective words that legitimately start titles
                # (not brands) — safe to prepend the brand before these.
                non_brand_starters = {
                    "portable", "rechargeable", "cute", "stainless", "leak",
                    "waterproof", "new", "best", "premium", "original", "pure",
                    "natural", "soft", "silicone", "led", "usb", "electric",
                    "automatic", "manual", "mini", "foldable", "adjustable",
                    "compact", "multi", "universal", "heavy", "lightweight",
                    "wireless", "smart", "digital", "kids", "baby", "adult",
                    "unisex", "men", "women", "ladies", "girls", "boys",
                    # Product-type / material nouns that legitimately start titles
                    "yoga", "bamboo", "wooden", "glass", "ceramic", "plastic",
                    "metal", "steel", "iron", "copper", "aluminum", "aluminium",
                    "cotton", "linen", "leather", "rubber", "foam", "wood",
                    "kitchen", "bathroom", "office", "car", "home", "garden",
                    "pet", "cat", "dog", "travel", "camping", "outdoor", "sports",
                    "fitness", "exercise", "gym", "hair", "face", "hand", "foot",
                    "eye", "ear", "neck", "wrist", "breathable", "stretchable",
                    "washable", "reusable", "disposable", "diy", "handmade",
                    "toy", "baby", "kitchenware", "storage", "organizer",
                    "organiser", "holder", "stand", "rack", "mat", "set", "pack",
                }
                if first_lower and first_lower not in non_brand_starters:
                    # Brand MISMATCH: the title starts with a different brand-like
                    # word. The sheet brand is authoritative, so REPLACE the wrong
                    # leading brand rather than prepending a second one.
                    brand_display = _to_title_case(brand_clean)
                    t = re.sub(rf"^{re.escape(first_word.group(1))}\b", brand_display, t, count=1, flags=re.IGNORECASE)
                    t = t.strip()
                    _log(f"  → Brand mismatch: replaced leading '{first_word.group(1)}' with '{brand_display}'")
                else:
                    # Proper-case the brand for prepending
                    brand_display = _to_title_case(brand_clean)
                    t = f"{brand_display} {t}".strip()
                    _log(f"  → Brand missing from title — prepending '{brand_display}'")

        # Detect claim sentences in the title. Split aggressively first —
        # long mixed-content titles dilute the claim signal. Individual
        # short segments are more likely to be correctly classified.
        segments = re.split(
            r"\s*[,;|\-–—!★☆✱✶✷✸✹✺✻✼✽✾✿❀❁]+"
            r"|\s+(?:AT|FOR|WITH|FROM)\s+",
            t, flags=re.IGNORECASE,
        )
        segments = [s.strip() for s in segments if len(s.strip()) > 6]
        if not segments:
            segments = [t]

        all_claims: list[str] = []
        for seg in segments:
            # Short segments (10-20 chars) often contain pure claims like
            # 'FREE SHIPPING' or 'COD Available' — use lower thresholds.
            seg_claims = detect_op_claims(seg, min_len=8, min_text_len=10)
            all_claims.extend(seg_claims)

        claims_in_title = all_claims
        if claims_in_title:
            # Remove the flagged claim text from the title
            for claim in claims_in_title:
                t = t.replace(claim, "")
            t = re.sub(r"\s{2,}", " ", t).strip(" ,-")
            # Clean up orphaned separators after claim removal
            t = re.sub(r"\s+(?:at|for|with|from)\s*$", "", t, flags=re.IGNORECASE).strip()
            t = re.sub(r"^[!★☆✪✱✶✷✸✹]+", "", t)  # leading orphaned punctuation
            t = re.sub(r"\s+[!★☆✪✱✶✷✸✹]+\s*$", "", t)  # trailing
            t = re.sub(r"\s+", " ", t).strip()
            # Title-case the surviving text (it may have lost its case from
            # claim removal or never been title-cased because original was mixed)
            if not _is_allcaps(t):
                t = _to_title_case(t)
            # If claims ate everything, rebuild from available data
            if not t or len(t.split()) < 2:
                parts = [brand, product_type_label]
                t = " ".join(p for p in parts if p)
                if t and not _is_allcaps(t):
                    t = _to_title_case(t)
            out_title = t if t and t != _strip_mechanical(title) else None
        elif t != title:
            # Mechanical changes (URL removed, ALLCAPS fixed) but no
            # claims — only suggest if the result has enough substance.
            words = t.strip().split()
            if len(words) >= 3:
                out_title = t
        elif len(t.strip().split()) < 3 and (brand or product_type_label):
            # Too-short title: rebuild from brand + product type so the title
            # follows [Brand] + [Product Type] + [Key Attributes] structure.
            parts = [brand, product_type_label]
            rebuilt = " ".join(p for p in parts if p and p not in t)
            if rebuilt:
                t = f"{t} {rebuilt}".strip()
                t = re.sub(r"\s{2,}", " ", t).strip()
                if not _is_allcaps(t):
                    t = _to_title_case(t)
                if len(t.strip().split()) >= 3:
                    out_title = t
                    _log(f"  → Title too short — rebuilt as '{t}'")

        # ── Product-type conflict correction ────────────────────────────
        # When the listing's detected product type (HSN/Marqo keyword-based)
        # contradicts the product type the title itself names, correct the
        # title. Uses the same keyword taxonomy as the Title accuracy flag,
        # so it never corrects on CLIP.
        _correct_type = (product_type_label or "").strip()
        if _correct_type:
            _title_type = keyword_match_type(title, "")
            _detected_type = detected_type
            if _title_type and _detected_type and _title_type != _detected_type:
                _clean_label = re.sub(r"^(?:a|an|the)\s+", "", _correct_type, flags=re.IGNORECASE)
                _type_noun = _clean_label.split(",")[0].split(" or ")[0].strip()
                # The wrong type's keyword nouns (from the taxonomy) that the
                # title matched — remove them so the title names the right thing.
                _wrong_kws = next((row[2] for row in PRODUCT_TYPES if row[0] == _title_type), [])
                _wrong_noun = ""
                if _wrong_kws:
                    # Prefer the longest keyword present in the title.
                    _found = [k for k in sorted(_wrong_kws, key=len, reverse=True) if k.lower() in title.lower()]
                    if _found:
                        _wrong_noun = _found[0]
                _new_title = title
                if _wrong_noun and _wrong_noun.lower() in _new_title.lower():
                    _new_title = re.sub(
                        re.escape(_wrong_noun), _type_noun, _new_title, flags=re.IGNORECASE,
                    )
                if _type_noun and _type_noun.lower() not in _new_title.lower():
                    _new_title = re.sub(r"\s+", " ", f"{_new_title} {_type_noun}".strip())
                if _new_title != title:
                    t = _to_title_case(_new_title) if not _is_allcaps(_new_title) else _new_title
                    if t != _strip_mechanical(title):
                        out_title = t
                        _log(f"  → Title type conflict '{_title_type}' vs '{_detected_type}' — corrected '{_type_noun}' in title")

    # ── CLIP fact-check: colours ──────────────────────────────────────
        title_colours = _extract_colors(title)
        desc_colours = _extract_colors(description)
        if clip_colours and (title_colours or desc_colours):
            _log(f"  CLIP colours: {clip_colours}")
            _log(f"  Title colours: {title_colours}")
            _log(f"  Desc colours:  {desc_colours}")
            # Title has colour not in CLIP → DO NOT correct title text (CLIP is unreliable for colors)
            # Only log the mismatch for debugging
            bad_title = [c for c in title_colours if c not in clip_colours]
            if bad_title:
                _log(f"  → CLIP disagrees on title colours: {bad_title} vs CLIP: {clip_colours} (keeping title text)")
            # Desc has colour not in CLIP → remove it from specs
            bad_desc = [c for c in desc_colours if c not in clip_colours]
            if bad_desc:
                _log(f"  → Desc colour '{', '.join(bad_desc)}' not in image — will exclude from specs")

        # ── CLIP fact-check: materials ─────────────────────────────────
        desc_lower = (description or "").lower()
        desc_mats = [m for m in MATERIALS_LIST if m.lower() in desc_lower]
        title_mats = [m for m in MATERIALS_LIST if m.lower() in (title or "").lower()]
        flagged_attrs = []
        if clip_materials and desc_mats:
            _log(f"  CLIP materials: {clip_materials}")
            _log(f"  Desc materials: {desc_mats}")
            bad_mats = [m for m in desc_mats if m.lower() not in [c.lower() for c in clip_materials]]
            if bad_mats:
                _log(f"  → Desc material '{', '.join(bad_mats)}' not in CLIP — will exclude from specs")
                for bm in bad_mats:
                    flagged_attrs.append({"type": "material", "stated": bm, "clip_top2": clip_materials[:2]})

        # ── CLIP fact-check: colours ──────────────────────────────────
        if clip_colours and desc_colours:
            bad_desc = [c for c in desc_colours if c not in clip_colours]
            if bad_desc:
                for bc in bad_desc:
                    flagged_attrs.append({"type": "color", "stated": bc, "clip_top2": clip_colours[:2]})

        # ── Qwen VLM correction when CLIP finds mismatches ────────────
        if flagged_attrs and images and use_qwen:
            _log(f"  → Running Qwen VLM to correct {len(flagged_attrs)} flagged attributes...")
            try:
                from _qwen_correct import query_vlm, apply_corrections, _load_image as qwen_load_image
                img = qwen_load_image(images[0])
                if img:
                    corrections = query_vlm(img, flagged_attrs, title=title, description=description or "")
                    _log(f"  → Qwen corrections: {corrections}")
                    # Apply corrections to title and description
                    applied = [c for c in corrections if c.get("observed") and c.get("confidence") != "failed"]
                    if applied:
                        corrected = apply_corrections(title or "", description or "", applied)
                        if corrected.get("corrected_title") and corrected["corrected_title"] != title:
                            out_title = corrected["corrected_title"]
                            _log(f"  → Corrected title: {out_title}")
                        if corrected.get("corrected_description") and corrected["corrected_description"] != description:
                            # Update raw for spec extraction
                            raw = corrected["corrected_description"]
                            _log(f"  → Corrected description (applied to specs)")
                        # Update CLIP materials list with corrected values
                        for c in applied:
                            if c.get("observed"):
                                _log(f"  → {c['attribute']}: '{c['original']}' → '{c['observed']}'")
            except Exception as e:
                _log(f"  → Qwen correction failed: {e} (falling back to exclusion only)")

        # ── Qwen VLM spec extraction for description ──────────────────
        # When use_qwen=True and we have images, extract accurate specs
        # from the product image to generate a correct description
        if use_qwen and images:
            try:
                from _qwen_specs import extract_specs_qwen, _load_image as qwen_spec_load_image
                img = qwen_spec_load_image(images[0])
                if img:
                    qwen_specs = extract_specs_qwen(img, title or "", description or "")
                    _log(f"  → Qwen VLM extracted specs: {qwen_specs}")
            except Exception as e:
                _log(f"  → Qwen spec extraction failed: {e}")

    # ── DESCRIPTION ────────────────────────────────────────────────────
    if description and description.strip():
        raw = description
        is_html = bool(re.search(r"<[a-z]+[^>]*>", raw, re.IGNORECASE))

        # Work with plain text for claim detection
        if is_html:
            plain = _html_to_plain(raw)
        else:
            plain = raw

        # Strip claims: remove entire sentences containing claim patterns
        cleaned = _strip_mechanical(plain)  # removes domains only
        sentences = re.split(r"(?<=[.!])\s+", cleaned)
        kept = []
        for sent in sentences:
            s = sent.strip()
            if not s: continue
            # Skip sentences that are primarily claims
            if _GLOBAL_CLAIM_PATTERNS.search(s):
                continue
            # Semantic check for claim-like sentences
            semantic_claims = detect_op_claims(s, min_len=3) if len(s) > 10 else []
            if semantic_claims:
                continue
            kept.append(s)
        cleaned = ". ".join(s.rstrip(".") for s in kept).strip()
        if cleaned:
            cleaned += "."
        # Restructure: remove sentence fragments and orphaned words
        cleaned = re.sub(r"\b\w{1,3}\s+\.", ".", cleaned)  # single-letter orphans
        cleaned = re.sub(r"\.\s*(\w{1,4})\s*\.", ". ", cleaned)  # short word between periods
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        # ── Extract attributes for specs ────────────────────────────
        combined = (title or "") + " " + cleaned
        mat = re.search(
            r"\b(?:cotton|silk|wool|leather|plastic|metal|steel|copper|iron|"
            r"aluminium|aluminum|glass|wood|ceramic|porcelain|rubber|silicon|silicone|"
            r"nylon|polyester|acrylic|vinyl|resin|bamboo|paper|cardboard|velvet|"
            r"velour|linen|denim|spandex|canvas|brass|zinc|marble|granite|clay|"
            r"terracotta|fabric|towels?|microfiber|bone china|melamine|stainless|"
            r"stainless\s*steel)\b",
            combined, re.IGNORECASE,
        )
        dims = re.findall(
            r"\b\d+(?:\.\d+)?\s*(?:ml|g|kg|l|oz|gm|mg|inch|inches|cm|mm|ft|feet)\b",
            combined, re.IGNORECASE,
        )
        weight_match = re.findall(
            r"\b\d+(?:\.\d+)?\s*(?:g|kg|gm|oz|lb|lbs)\b",
            combined, re.IGNORECASE,
        )
        capacity_match = re.findall(
            r"\b\d+(?:\.\d+)?\s*(?:ml|l|litre|liter)\b",
            combined, re.IGNORECASE,
        )
        color_match = re.findall(
            r"\b(?:black|white|red|blue|green|pink|silver|grey|navy|beige|purple|"
            r"maroon|gold|yellow|orange|brown|multi|multicolor)\b",
            combined, re.IGNORECASE,
        )

        # ── Build specs from category template ───────────────────────
        from _category_specs import match_category as _match_cat, get_specs_for_category, CATEGORY_SCHEMA

        if v2_category and v2_category in CATEGORY_SCHEMA:
            cat_id = v2_category
        elif marqo_category and marqo_category in CATEGORY_SCHEMA:
            cat_id = marqo_category
        else:
            cat_id = _match_cat(title or "", cleaned)
        cat_specs = get_specs_for_category(cat_id)
        _log(f"  Category: {cat_id} | Required specs: {cat_specs}")

        def _extract_spec(spec_name: str) -> Optional[str]:
            # Normalize: strip parenthetical like "material_(metal/leather)" -> "material"
            base_name = re.sub(r"\(.*\)", "", spec_name).strip("_").lower()

            # ── Qwen VLM specs take PRIORITY when available ────────────
            # Map the normalized spec name to Qwen spec keys
            qwen_key_map: Dict[str, str] = {
                "material": "Material", "materials": "Material",
                "color": "Color", "colour": "Color",
                "type": "Type", "item_type": "Type", "product_type": "Type",
                "use": "Use", "usage": "Use", "occasion": "Occasion",
                "size": "Size", "dimension": "Size", "dimensions": "Size",
                "capacity": "Capacity", "volume": "Capacity",
                "weight": "Weight",
                "target": "Target", "audience": "Target",
                "pattern": "Pattern", "theme": "Theme",
                "brand": "Brand",
                "product": "Product",
                "pack": "Pack", "quantity": "Pack", "pack_size": "Pack",
            }
            for norm_key, qwen_key in qwen_key_map.items():
                if norm_key in base_name and qwen_key in qwen_specs:
                    val = qwen_specs[qwen_key]
                    if val and val.strip():
                        _log(f"  → Using Qwen spec [{qwen_key}]: {val}")
                        return val.strip()


            # ── Enhanced Material extractor ──────────────────────────
            # CLIP FIRST: product descriptions are often copy-pasted wrong.
            # Use CLIP as ground truth, fall back to text only when CLIP has no answer.
            if base_name in ("material", "materials"):
                # Products where CLIP material is unreliable (tablets, cleaners, liquids, chemicals, powders)
                _NON_MATERIAL_PRODUCTS = re.search(
                    r"\b(tablet|cleaner|descaler|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|strip|sachet|capsule|spray|foam|scrub)\b",
                    combined, re.IGNORECASE,
                )
                # Check text for material mentions
                text_mat = None
                mat_patterns = [
                    r"\b(cotton|polyester|nylon|stainless\s*steel|leather|rubber|plastic|wood|metal|brass|aluminium|aluminum|silk|wool|canvas|fiberglass|silicon|silicone|bamboo|ceramic|glass|porcelain|cast\s*iron|granite|marble|jute|denim|satin|velvet|terry\s*cotton|pp|abs|tpu|zinc\s*alloy|alloy|iron|steel|acrylic|resin|mDF|mdf|terracotta|clay|copper|bronze|nickel|chrome|tin|pvc|ev|eva|microfiber|net|mesh|lace|chiffon|georgette|crepe|rayon|linen|khadi|chikankari|kundan|meenakari|handmade|handcrafted)\b"
                ]
                for p in mat_patterns:
                    m = re.search(p, combined, re.IGNORECASE)
                    if m:
                        text_mat = m.group(1)
                        break
                # For artificial flowers, default to fabric/polyester
                if not text_mat and re.search(r"\b(?:artificial|fake|silk|faux)\s*(?:flower|rose|plant)\b", combined, re.IGNORECASE):
                    return "Polyester"
                # If Qwen VLM is enabled, trust Qwen completely — it saw the image.
                # Qwen's Material (if any) was already used at the top of _extract_spec.
                # If we reached here, Qwen did NOT provide a Material, so do NOT fall
                # back to CLIP's unreliable material guess (CLIP misreads glossy
                # plastic as "ceramic", etc.). Use text material or nothing.
                if use_qwen:
                    if text_mat:
                        return text_mat.title()
                    return None
                # If CLIP has a confident answer, prefer it (descriptions are often wrong)
                if clip_materials and clip_materials[0]:
                    clip_mat = clip_materials[0]
                    # If product is a non-material type (cleaner, tablet, etc.), ignore CLIP material
                    if _NON_MATERIAL_PRODUCTS:
                        # CLIP sees box/packaging — trust text over CLIP for these products
                        if text_mat:
                            return text_mat.title()
                        return None
                    # If text and CLIP agree, use CLIP (more specific for same material)
                    if text_mat and text_mat.lower() == clip_mat.lower():
                        return clip_mat.title()
                    # If they disagree, prefer CLIP (image is ground truth)
                    return clip_mat.title()
                # Fall back to text if CLIP has no answer
                if text_mat: return text_mat.title()
                return None

            # ── Enhanced Color extractor ──────────────────────────
            # TEXT FIRST: text-based extraction is more reliable than CLIP for colors
            if base_name in ("color", "colour", "colors"):
                # Try text-based first (title + description)
                if color_match: return ", ".join(sorted(set(c.lower() for c in color_match)))
                # Enhanced color patterns (multi-word colors)
                color_patterns = [
                    r"\b(rose\s*gold|midnight\s*blue|dark\s*blue|light\s*blue|sky\s*blue|navy\s*blue|forest\s*green|olive\s*green|sea\s*green|hot\s*pink|baby\s*pink|dusty\s*pink|burnt\s*orange|rust\s*red|wine\s*red|maroon|burgundy|teal|turquoise|lavender|mauve|peach|cream|ivory|charcoal|slate|beige|tan|camel|mahogany|walnut|cherry|maple|ebony|bronze|copper|golden|silver|platinum|metallic|matte|glossy|shimmer|iridescent|holographic|neon|fluorescent|pastel|earth\s*tone|multicolor|rainbow)\b",
                    r"\b(black|white|blue|red|green|yellow|pink|purple|orange|grey|gray|brown|beige|navy|teal|maroon|gold|silver|transparent|multicolor|rainbow)\b"
                ]
                for p in color_patterns:
                    m = re.search(p, combined, re.IGNORECASE)
                    if m: return m.group(1).title()
                # Only use CLIP as last resort if text has no color info
                if clip_colours: return ", ".join(sorted(set(c.lower() for c in clip_colours)))
                return None

            if base_name in ("brand",):
                # If brand is provided and not generic, use it
                if brand and brand.lower() not in ("generic", "unbranded", "no brand", "n/a", ""):
                    return brand
                # Try to extract brand from title (first word(s) before common separators)
                if title:
                    # Common patterns: "BrandName Product..." or "BrandName - Product..."
                    brand_match = re.match(r"^([A-Z][A-Za-z0-9&\s]{1,30}?)(?:\s+[-–—|,]\s+|\s+(?:Artificial|Soft|Gift|Golden|Red|Blue|Green|Black|White|Stainless|Digital|Mini|Pro|Premium|Luxury|Royal|Classic|Modern|Smart|Fancy|Designer|Original|New|Combo|Set|Pack|PVC|Steel|Metal|Wooden|Cotton|Silk|Leather|Plastic|Ceramic|Glass|Bamboo|Iron|Alloy|Zinc))", title)
                    if brand_match:
                        b = brand_match.group(1).strip()
                        # Skip generic words
                        if b.lower() not in ("the", "a", "an", "for", "with", "and", "or", "new", "best", "top", "high", "super"):
                            return b
                # Return original brand if it exists (even if generic)
                return brand if brand else None

            # ── Measurement extractors ─────────────────────────────
            if any(w in base_name for w in ("dimension", "size", "length", "width", "height", "diameter", "ring_diameter")):
                dim_pat = re.search(r"\b(\d+(?:\.\d+)?\s*(?:x|×|\*)\s*\d+(?:\.\d+)?\s*(?:cm|mm|inch|inches)?)\b", combined, re.IGNORECASE)
                if dim_pat: return dim_pat.group(1)
                # Only take up to 3 unique measurements, dedup same values
                meas = re.findall(r"\b(\d+(?:\.\d+)?\s*(?:cm|mm|inch|inches|ft|feet))\b", combined, re.IGNORECASE)
                if meas:
                    unique_meas: list = []
                    seen = set()
                    for m in meas:
                        m_norm = re.sub(r"\s+", " ", m.strip()).lower()
                        if m_norm not in seen:
                            seen.add(m_norm)
                            unique_meas.append(m)
                    return ", ".join(unique_meas[:3])
                return None
            if base_name in ("capacity", "volume"):
                if capacity_match: return ", ".join(sorted(set(capacity_match)))
                return None
            if base_name in ("weight",):
                if weight_match: return ", ".join(weight_match)
                return None

            # ── Enhanced Type extractor ─────────────────────────────
            if base_name.endswith("_type") or base_name in ("type", "cable_type", "connector_type",
                "board_type", "keychain_type", "accessory_type", "item_type"):
                # Products where type extraction from text is unreliable (cleaners, tablets, liquids)
                _NON_TYPE_PRODUCTS = re.search(
                    r"\b(cleaner|descaler|cleaning|tablet|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|sachet|capsule|spray|foam|scrub|freshener|deodorant|sanitizer|disinfectant|steriliz)\b",
                    combined, re.IGNORECASE,
                )
                if _NON_TYPE_PRODUCTS:
                    return None
                type_words = []
                board_kw = re.findall(r"\b(?:whiteboard|chalkboard|drawing board|magnetic board|bulletin board|flip chart)\b", combined, re.IGNORECASE)
                type_words.extend(board_kw)
                conn_kw = re.findall(r"\b(?:usb[- ]?c|type[- ]?c|micro[- ]?usb|lightning|hdmi|aux|3\.5mm)\b", combined, re.IGNORECASE)
                type_words.extend(conn_kw)
                key_kw = re.findall(r"\b(?:key[\s-]?ring|key[\s-]?chain|carabiner|lobster clasp|split ring)\b", combined, re.IGNORECASE)
                type_words.extend(key_kw)
                # Enhanced type patterns
                enhanced_type_kw = re.findall(r"\b(bottle|bag|wallet|belt|watch|earring|necklace|bracelet|ring|cap|hat|shoe|sandal|slipper|towel|blanket|curtain|pillow|cover|case|stand|holder|dispenser|cooker|grinder|mixer|chopper|peeler|ladle|spoon|fork|knife|scissors|clip|keychain|keyring|toy|figurine|lamp|light|fan|mirror|hook|hanger|organizer|basket|tray|plate|bowl|cup|mug|glass|jar|container|box|purse|clutch|saree|kurti|kurta|lehenga|shirt|t[\-\s]?shirt|top|blouse|dress|skirt|pant|trouser|jeans|shorts|jacket|coat|showpiece)\b", combined, re.IGNORECASE)
                type_words.extend(enhanced_type_kw)
                if type_words: return ", ".join(sorted(set(c.lower() for c in type_words)))
                return None

            # ── Enhanced Theme extractor ─────────────────────────────
            if base_name in ("theme", "character"):
                theme_patterns = [
                    r"\b(cartoon|cute|kawaii|disney|pokemon|dragon\s*ball|doraemon|tom\s*&?\s*jerry|avengers|marvel|batman|spider[\s-]?man|princess|unicorn|dinosaur|animal|monster|superhero|fantasy|vintage|retro|minimalist|modern|classic|luxury|elegant|bohemian|boho|rustic|industrial|scandinavian|feng\s*shui|vastu|religious|spiritual)\b"
                ]
                for p in theme_patterns:
                    m = re.search(p, combined, re.IGNORECASE)
                    if m: return m.group(1).title()
                return None

            # ── Enhanced Target extractor ─────────────────────────────
            if base_name in ("target", "audience", "for"):
                target_patterns = [
                    r"\b(for\s+kids|for\s+girls|for\s+boys|for\s+women|for\s+men|for\s+baby|for\s+couples|for\s+teens|for\s+toddlers|kids|girls|boys|women|men|baby|toddler|infant|adult|teen|unisex|family|couples|ladies|gentlemen|children|senior|professional)\b"
                ]
                for p in target_patterns:
                    m = re.search(p, combined, re.IGNORECASE)
                    if m:
                        val = m.group(1).replace("for ", "").strip()
                        return val.title()
                return None

            # ── Use extractor (purpose/activity) ─────────────────────────────
            if base_name in ("use", "usage", "purpose"):
                use_patterns = [
                    r"\b(daily\s*use|home\s*decor|kitchen|bathroom|bedroom|office|travel|gym|outdoor|indoor|cooking|cleaning|storage|organiz|decor|utility|professional|personal|commercial|industrial)\b"
                ]
                for p in use_patterns:
                    m = re.search(p, combined, re.IGNORECASE)
                    if m: return m.group(1).title()
                return None

            # ── Occasion extractor (event/celebration) ─────────────────────────────
            if base_name in ("occasion",):
                occasion_patterns = [
                    r"\b(party|birthday|wedding|anniversary|festival|diwali|christmas|new\s*year|valentine|return\s*gift|casual|formal|ethnic|western|traditional|contemporary|fusion|bohemian|sporty|trendy|fashion|fancy|designer|branded|premium|luxury)\b"
                ]
                for p in occasion_patterns:
                    m = re.search(p, combined, re.IGNORECASE)
                    if m: return m.group(1).title()
                return None

            # ── Compatibility extractors ───────────────────────────
            if "compatib" in base_name:
                compat_kw = re.findall(r"\b(?:universal|road|mountain|kids|adult|sedan|suv|hatchback|motorcycle|bike|bicycle|all bikes)\b", combined, re.IGNORECASE)
                if compat_kw: return ", ".join(sorted(set(c.lower() for c in compat_kw)))
                return None

            # ── Mounting/installation extractors ───────────────────
            if "mount" in base_name or "install" in base_name:
                mount_kw = re.findall(r"\b(?:wall[\s-]*mount(?:ed|able)?|tabletop|easel|ceiling|clip[\s-]*on|suction|adhesive|magnetic mount|screw[\s-]*in|snap[\s-]*on)\b", combined, re.IGNORECASE)
                if mount_kw: return ", ".join(sorted(set(c.lower().replace("-", " ") for c in mount_kw)))
                return None

            # ── Count/quantity extractors ───────────────────────────
            if any(w in base_name for w in ("pieces", "count", "quantity", "chair_count", "set_includes", "pack")):
                # Try "Pack of N" pattern first
                pack_match = re.search(r"\b[Pp]ack\s+of\s+(\d+)", combined)
                if pack_match: return f"Pack of {pack_match.group(1)}"
                # Try "N pieces/pcs/set" pattern
                qty = re.findall(r"\b(\d+)\s*(?:pieces?|pcs|count|set|pack|suits?|items?)\b", combined, re.IGNORECASE)
                if qty: return ", ".join(qty)
                return None

            # ── Power/battery extractors ────────────────────────────
            if any(w in base_name for w in ("power", "watt", "battery", "voltage")):
                pw = re.findall(r"\b(\d+\s*(?:w|watts?|kw|hp|v|mah|ah|hours?|hrs?)|rechargeable|cordless)\b", combined, re.IGNORECASE)
                if pw: return ", ".join(pw)
                return None

            # ── Packaging type extractor ───────────────────────────
            if base_name in ("packaging_type", "package_type", "package"):
                pkg = re.search(r"\b(?:box|bag|bottle|jar|tin|pouch|wrap|carton|case|pouch|sachet|tube|can|drum|crate|pallet)\b", combined, re.IGNORECASE)
                if pkg: return pkg.group(0).title()
                return None

            # ── Country of origin extractor ────────────────────────
            if base_name in ("country_of_origin", "origin", "made_in"):
                country = re.search(r"\b(?:made\s+in|origin[:\s]*|country[:\s]*)(India|China|USA|US|UK|Germany|Japan|Italy|France|Spain|Korea|Taiwan|Bangladesh|Sri\s*Lanka|Vietnam|Thailand|Indonesia|Nepal)\b", combined, re.IGNORECASE)
                if country: return country.group(1).title()
                return None  # Don't fall through to generic matcher

            # ── Festival/holiday theme extractor ───────────────────
            if "festival" in base_name or "holiday" in base_name:
                fest = re.search(r"\b(Diwali|Christmas|Eid|Holi|Navratri|Dussehra|Onam|Pongal|Baisakhi|Karva\s*Chauth|Lohri|Republic\s*Day|Independence\s*Day|Valentine|New\s*Year|Raksha\s*Bandhan|Ganesh\s*Chaturthi|Janmashtami|Makar\s*Sankranti)\b", combined, re.IGNORECASE)
                if fest: return fest.group(1).title()
                return None

            # ── Gift packaging extractor ───────────────────────────
            if "gift" in base_name and "packag" in base_name:
                if re.search(r"\b(?:gift\s*box|gift\s*wrap|gift\s*bag|gift\s*pack)\b", combined, re.IGNORECASE):
                    return "Yes"
                return None

            # ── Age extractors ──────────────────────────────────────
            if "age" in base_name:
                age = re.findall(r"\b(\d+\+?\s*(?:years?|yrs?|months?))\b", combined, re.IGNORECASE)
                if age: return ", ".join(age)
                return None

            # ── Material-related extractors (composition, frame, etc.) ──
            if "material" in base_name:
                # Try text-based first
                if mat: return mat.group(0)
                mat_words = re.findall(r"\b(?:cotton|silk|wool|leather|plastic|metal|steel|wood|glass|ceramic|rubber|silicone|silicon|fabric|nylon|polyester|acrylic|bamboo|paper|velvet|linen|denim|canvas|brass|zinc|marble|granite|clay|terracotta|microfiber|melamine|stainless|copper|iron|aluminium|aluminum)\b", combined, re.IGNORECASE)
                if mat_words: return ", ".join(sorted(set(m.lower() for m in mat_words)))
                # For artificial flowers, default to fabric/polyester
                if re.search(r"\b(?:artificial|fake|silk|faux)\s*(?:flower|rose|plant)\b", combined, re.IGNORECASE):
                    return "Polyester"
                # Only use CLIP as last resort
                if clip_materials: return clip_materials[0]
                return None

            # ── Boolean detection for binary properties ──────────
            # Only return "Yes" when the full concept is found in text
            display_name = base_name.replace("_", " ").replace("-", " ").strip()
            if len(display_name) >= 3:
                # Build multiple search patterns
                patterns = [display_name]
                # Hyphenated variant: "bpa free" → "bpa-free"  
                patterns.append(display_name.replace(" ", "-"))
                # Underscore variant: "bpa_free" → "bpa free"
                if "_" in spec_name:
                    patterns.append(spec_name.replace("_", "-"))
                # For "X free" specs (BPA free, BPA-free), also check just the key word
                if display_name.endswith(" free"):
                    key = display_name[:-5].strip()
                    if len(key) >= 3:
                        patterns.append(key)
                # Check each pattern - must match FULL concept, not partial
                for pat in patterns:
                    if len(pat) >= 3 and re.search(r"\b" + re.escape(pat) + r"\b", combined, re.IGNORECASE):
                        return "Yes"
                    # Also check combined form: "bpa-free" matches "BPA-free" or "bpa free"
                    combined_pat = pat.replace(" ", r"[\s\-]?")
                    if len(combined_pat) > len(pat) and re.search(r"\b" + combined_pat + r"\b", combined, re.IGNORECASE):
                        return "Yes"

            # ── Generic: "SpecName: value" pattern ────────────────────
            m = re.search(r"\b" + re.escape(display_name) + r"\s*[:\-]?\s*([^.!]{2,50})", combined, re.IGNORECASE)
            if m:
                val = m.group(1).strip().rstrip(",").strip()
                if val and len(val) > 1:
                    return val[:50]

            return None

        spec_items: list = []

        # ── Product identity: derive what the product IS from title ──
        if title:
            # Use productTypeLabel if available from CLIP classification
            if product_type_label:
                prod_clean = re.sub(r"^(?:a|an|the)\s+", "", product_type_label, flags=re.IGNORECASE)
                prod_words = [w for w in prod_clean.split() if w.lower() not in ("or", "and", "the", "a", "an", "with", "for", "in", "of")]
                prod_type = " / ".join(w.capitalize() for w in prod_words[:3])
                spec_items.append(f"<li><b>Product:</b> {prod_type}</li>")
            else:
                # Fallback: extract product type from title
                title_clean = re.sub(r"\b(for|with|in|of|the|a|an|and|or|new|best|top|high|super|original|authentic|premium)\b", " ", title, flags=re.IGNORECASE)
                words = [w for w in title_clean.split() if len(w) > 2][:4]
                if words:
                    spec_items.append(f"<li><b>Product:</b> {' '.join(words)}</li>")

        # Products where Material/Type specs are unreliable
        _is_non_material = bool(re.search(
            r"\b(tablet|cleaner|descaler|cleaning|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|strip|sachet|capsule|spray|foam|scrub)\b",
            combined, re.IGNORECASE,
        ))

        def _dedup_comma(val: str) -> str:
            parts = [p.strip() for p in re.split(r"[,;]", val)]
            seen: set = set()
            unique = []
            for p in parts:
                if p and p.lower() not in seen:
                    seen.add(p.lower())
                    unique.append(p)
            return ", ".join(unique)

        for spec in cat_specs:
            # Skip Material/Type for non-material products
            spec_lower = spec.lower().replace("_", "")
            if _is_non_material and ("material" in spec_lower or spec_lower.endswith("type")):
                continue
            val = _extract_spec(spec)
            if val:
                # Dedup comma-separated values (e.g. "Rechargeable, Rechargeable")
                val = _dedup_comma(val)
                label = spec.replace("_", " ").title()
                spec_items.append(f"<li><b>{label}:</b> {val}</li>")

        if brand and not any("Brand:" in s for s in spec_items):
            spec_items.append(f"<li><b>Brand:</b> {brand}</li>")

        # ── Always add common specs (Color, Material, Type) if not already present ──
        # Skip if a similar category-specific spec already has the same value
        existing_labels = set()
        existing_values = set()
        for s in spec_items:
            # Extract label text (strip HTML tags)
            m_lbl = re.search(r"<b>([^<]+)</b>", s)
            if m_lbl:
                lbl = m_lbl.group(1).rstrip(":").strip().lower()
                existing_labels.add(lbl)
            m_v = re.search(r":\s*(.+)</li>", s)
            if m_v:
                existing_values.add(m_v.group(1).strip().lower())

        common_specs = [
            ("Brand", ["brand"]),
            ("Color", ["color", "colour"]),
            ("Material", ["material", "materials"]),
            ("Type", ["type", "item_type"]),
            ("Size", ["size", "dimensions", "dimension"]),
            ("Capacity", ["capacity", "volume"]),
            ("Theme", ["theme", "character"]),
            ("Target", ["target", "audience", "for"]),
            ("Use", ["use", "usage", "purpose"]),
            ("Occasion", ["occasion", "event"]),
            ("Pack", ["pack", "quantity", "count"]),
        ]

        for spec_name, spec_keys in common_specs:
            # Skip if this common spec already exists by name (exact or partial match)
            spec_lower = spec_name.lower()
            if spec_lower in existing_labels:
                continue
            # Skip if any existing label contains the spec name (e.g. "Material Composition" contains "material")
            if any(spec_lower in lbl for lbl in existing_labels):
                continue
            # Skip "Size" if "Dimensions" already present (same measurement)
            if spec_lower == "size" and "dimensions" in existing_labels:
                continue
            # Skip Material and Type for non-material products (cleaners, tablets, liquids)
            if _is_non_material and spec_lower in ("material", "type"):
                continue
            # Skip if a category-specific sibling already has the same value
            skip = False
            for key in spec_keys:
                val = _extract_spec(key)
                if val and val.strip().lower() in existing_values:
                    skip = True
                    break
            if skip:
                continue
            for key in spec_keys:
                val = _extract_spec(key)
                if val:
                    spec_items.append(f"<li><b>{spec_name}:</b> {_dedup_comma(val)}</li>")
                    break

        # ── Build output: preserve original text, append specs ───────
        if is_html:
            # Strip existing specs section if present
            base = re.sub(r"<h4>Specifications</h4>\s*<ul>.*?</ul>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
            # Strip domain references and claims from HTML text
            base = _URL_RE.sub(" ", base)
            base = re.sub(r"(?:[:\s]+(?:at|from|on)\s*\S+\.(?:com|in|net)\b\s*:?\s*)", " ", base, flags=re.IGNORECASE)
            base = _GLOBAL_CLAIM_PATTERNS.sub(" ", base)
            base = re.sub(r"\s{2,}", " ", base).strip()
            base = re.sub(r"\s*:\s*:", ":", base)  # orphaned colons from domain removal
            if spec_items:
                result_desc = base + "\n<h4>Specifications</h4>\n<ul>" + "\n".join(spec_items) + "</ul>"
            else:
                result_desc = base
        else:
            result_desc = cleaned
            if spec_items:
                result_desc += "\n\n<h4>Specifications</h4>\n<ul>" + "\n".join(spec_items) + "</ul>"

        if result_desc.strip() != description.strip():
            out_desc = result_desc.strip()

    # ── Optional T5 polish ──────────────────────────────────────────────
    if polish and (out_title or out_desc):
        try:
            from _t5_correct import polish_title as _t5_title, polish_description as _t5_desc

            if out_title:
                polished = _t5_title(out_title)
                if polished:
                    out_title = polished

            if out_desc:
                # T5 can't preserve HTML, but the mechanical output IS
                # HTML. Strip tags → T5 polish plain text → re-wrap.
                plain = re.sub(r"<[^>]*>", "", out_desc)
                plain = re.sub(r"\s{2,}", " ", plain).strip()
                if len(plain) > 30:
                    polished = _t5_desc(plain)
                    # Sanity: T5 must preserve > 50% of content words
                    if polished and _content_retained(plain, polished):
                        # Re-wrap polished text in HTML, graft specs from mechanical
                        spec_match = re.search(
                            r"(<h4>Specifications</h4>\s*<ul>.*?</ul>)",
                            out_desc, re.DOTALL | re.IGNORECASE,
                        )
                        polished_sentences = re.split(r"[.!\n]+", polished)
                        polished_sentences = [s.strip() for s in polished_sentences if len(s.strip()) > 8]
                        wrapped = "\n".join(
                            f"<p>{s}.</p>" for s in polished_sentences
                        )
                        if spec_match:
                            wrapped += "\n" + spec_match.group(1)
                        if wrapped:
                            out_desc = wrapped
        except Exception:
            pass  # T5 not available — return mechanically-cleaned result

    return {"title": out_title, "description": out_desc, "log": log}


def _texts_overlap(shorter: str, longer: str) -> bool:
    """True when `shorter` is largely covered by `longer`."""
    s = shorter.lower().strip(".,!? ")
    l = longer.lower().strip(".,!? ")
    if l.startswith(s) or l.endswith(s):
        return True
    sw = set(s.split())
    lw = set(l.split())
    if sw and len(sw & lw) / len(sw) > 0.7:
        return True
    return False

_COLOR_RE = re.compile(
    r"\b(?:black|white|red|blue|green|pink|silver|grey|gray|navy|beige|"
    r"purple|maroon|gold|yellow|orange|brown|multi|multicolor|transparent|"
    r"clear|turquoise|teal|violet|indigo|magenta|cyan|lavender|peach|mint|"
    r"coral|cream|ivory|tan|bronze|copper|burgundy|charcoal|khaki|"
    r"olive|mustard)\b",
    re.IGNORECASE,
)

def _extract_colors(text: str) -> list[str]:
    """Return unique lowercase color words found in text."""
    if not text:
        return []
    return sorted(set(c.lower() for c in _COLOR_RE.findall(text)))

MATERIALS_LIST = [
    "cotton", "silk", "wool", "leather", "plastic", "metal", "steel",
    "copper", "iron", "aluminium", "aluminum", "glass", "wood", "ceramic",
    "porcelain", "rubber", "silicone", "nylon", "polyester", "acrylic",
    "vinyl", "resin", "bamboo", "paper", "cardboard", "velvet", "velour",
    "linen", "denim", "spandex", "canvas", "brass", "zinc", "marble",
    "granite", "clay", "terracotta", "fabric", "towel", "microfiber",
    "bone china", "melamine", "stainless steel", "stainless",
]


def _content_retained(original: str, polished: str) -> bool:
    """True if polished text keeps >50% of original's meaningful words."""
    orig_words = set(original.lower().split())
    pol_words = set(polished.lower().split())
    if not orig_words:
        return False
    overlap = len(orig_words & pol_words) / len(orig_words)
    return overlap > 0.5


_HTML_TO_PLAIN_RE = re.compile(r"</?(?:h[1-6]|p|br|div|li|tr|td|th|section|article)[^>]*>", re.IGNORECASE)
_BOILERPLATE_RE = re.compile(
    r"^(Product Description|Features|About this item|Specifications?|Details?|"
    r"Description|Highlights|What.s in the box|Package Contents|"
    r"Product Information|Technical Details?|Additional Information)$",
    re.IGNORECASE,
)


def _html_to_plain(html_text: str) -> str:
    """Extract readable plain text from HTML, preserving sentence boundaries."""
    # Replace block-level tags with newlines so sentences don't merge
    text = _HTML_TO_PLAIN_RE.sub("\n", html_text)
    # Strip remaining tags
    text = re.sub(r"<[^>]*>", "", text)
    # Collapse whitespace
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Remove leading/trailing whitespace per line
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def process_products(products: List[dict]) -> dict:
    results = []
    for product in products:
        sku = product.get("sku", "")
        query = build_query(product)
        image_sources = product.get("images", [])
        # V2: Fusion classifier for confidence + gating (runs first)
        from _category_specs import classify_category as _cc, get_confidence_tier as _gct, get_unit_variation as _guv, get_label_for_category as _glc
        from _category_specs import CATEGORY_SCHEMA as _CSV2, build_clip_taxonomy as _bct

        def _is_text_confident(conf: float) -> bool:
            return conf >= 0.5
        cat_v2, cat_conf, cat_diag = _cc(
            product.get("title", ""),
            product.get("description", ""),
            image_sources,
        )
        cat_tier = _gct(cat_conf)

        # Marqo image-based classification (multi-image, path labels) — the
        # authoritative visual category signal. Marqo is trained on ecommerce
        # data (82-97% accurate), so when images are present it decides the
        # category; text-only classification fills in when images are absent.
        # Guard: only accept Marqo's pick when it is at least as specific as
        # the text-derived category. Marqo's broad top-level catch-alls
        # ("General Merchandise") must NOT downgrade a specific text category.
        def _cat_depth(cid):
            path = _CSV2.get(cid, {}).get("full_path", "")
            return path.count(" > ") if path else 0

        marqo_id, marqo_score = None, 0.0
        if image_sources:
            try:
                from _marqo_classifier import classify as _marqo
                from _category_specs import CATEGORY_SCHEMA as _CSV2
                marqo_labels = [data.get("full_path", cid) for cid, data in _CSV2.items()]
                marqo_ids = list(_CSV2.keys())
                marqo_results = _marqo(image_sources, marqo_labels)
                if marqo_results:
                    marqo_label, marqo_score = marqo_results[0]
                    marqo_id = marqo_ids[marqo_labels.index(marqo_label)]
            except Exception as exc:
                print(f"[HSN] Marqo classification skipped: {exc}", file=sys.stderr)
        text_cat = cat_v2
        categoryStatus = "unknown"
        if marqo_id and marqo_score > 0.05:
            if marqo_id == cat_v2:
                # Marqo confirms the text-derived category — boost confidence.
                cat_conf = min(0.95, cat_conf * 0.5 + marqo_score * 2.0)
                categoryStatus = "confirmed"
            elif _cat_depth(marqo_id) < _cat_depth(text_cat):
                # Marqo's pick is less specific than the text category (e.g. a
                # top-level catch-all) — keep the more specific category.
                categoryStatus = "unknown"
                print(f"[HSN] Marqo '{_CSV2.get(marqo_id, {}).get('full_path', marqo_id)}' ({marqo_score:.0%}) less specific than text '{_CSV2.get(text_cat, {}).get('full_path', text_cat)}' — keeping text category", file=sys.stderr)
            elif _is_text_confident(cat_conf) and marqo_score < 0.60:
                # Text category is already confident and specific; Marqo's
                # image pick is not high-confidence. A door-stopper photo that
                # looks like a USB gadget (87%) must NOT override the text
                # category — Marqo reads shapes, not function. Only a strong
                # Marqo signal (>60%) beats a confident text category.
                categoryStatus = "unknown"
                print(f"[HSN] Marqo '{_CSV2.get(marqo_id, {}).get('full_path', marqo_id)}' ({marqo_score:.0%}) disagrees but text category '{_CSV2.get(text_cat, {}).get('full_path', text_cat)}' is confident ({cat_conf:.0%}) — keeping text category", file=sys.stderr)
            else:
                # Marqo disagrees strongly and/or text is weak — Marqo wins on images.
                cat_v2 = marqo_id
                cat_conf = min(0.85, marqo_score * 2.5)
                categoryStatus = "needs-change"
            cat_tier = _gct(cat_conf)

        # Direct image-to-HSN reranking below is the authoritative visual
        # signal. Avoid running a second broad-label CLIP pass for the same
        # images; this keeps batch validation practical.
        visual_context = (
            "CLIP image-to-HSN fusion applied"
            if image_sources else "text only (no images supplied)"
        )
        suggestions = suggest(
            query,
            product.get("title", ""),
            product.get("description", ""),
            visual_context if not image_sources else "",
            image_sources,
            v2_type=cat_v2,
        ) if query else []
        # Expose the detected product type and whether unit/variation applies,
        # so the validator can decide if a "pack of N / size / color" is even
        # relevant for this product (e.g. not for a cable or an appliance).
        clip_type, clip_type_conf = classify_with_clip_conf(image_sources) if image_sources else (None, 0.0)
        kw_type = keyword_match_type(product.get("title", ""), product.get("description", ""))
        # The curated keyword taxonomy (title + description) is the authority
        # for what a product IS. CLIP image zero-shot is unreliable for generic
        # product photos (a bathroom accessory can score "apparel"), so it only
        # fills in when text keywords find nothing.
        detected_type = kw_type or clip_type

        unit_variation = "optional"
        type_label = ""
        for row in PRODUCT_TYPES:
            if row[0] == detected_type:
                unit_variation = row[4]
                type_label = row[1]
                break
        if not type_label and detected_type:
            type_label = _glc(detected_type)
            unit_variation = _guv(detected_type)
        # Title accuracy: compare what the TITLE says (title-only keywords)
        # against the full title+description keyword match. Both sides use the
        # curated keyword taxonomy — NOT CLIP, which misreads generic product
        # photos and titles (e.g. a toothbrush holder scored "apparel", a sports
        # title scored "cable"). Keyword evidence is the authoritative signal.
        title_pid = keyword_match_type(product.get("title", ""), "")
        title_ptype_label = next((row[1] for row in PRODUCT_TYPES if row[0] == title_pid), "") if title_pid else ""
        title_accuracy_status = "unknown"
        if title_pid and detected_type and title_pid != detected_type:
            # Genuine keyword conflict: title says one thing, full listing another.
            title_accuracy_status = "mismatch"
        elif title_pid and detected_type == title_pid:
            title_accuracy_status = "match"
        desc_text = product.get("description", "")
        op_claim_sentences = detect_op_claims(
            product.get("title", "") + ". " + desc_text,
        )
        validation_hints = PRODUCT_VALIDATION_HINTS.get(
            detected_type or "",
            PRODUCT_VALIDATION_HINTS.get(title_pid or "", {}),
        )
        results.append({
            "sku": sku,
            "query": " ".join(part for part in (query, visual_context) if part),
            "visualContext": visual_context,
            "algorithmVersion": ALGORITHM_VERSION,
            "productType": detected_type,
            "productTypeLabel": type_label,
            "unitVariation": unit_variation,
            "titleProductType": title_pid,
            "titleProductTypeLabel": title_ptype_label,
            "titleAccuracyStatus": title_accuracy_status,
            "opClaims": op_claim_sentences,
            "validationHints": validation_hints,
            "suggestions": suggestions,
            "topGstRate": suggestions[0]["gst_rate"] if suggestions else None,
            "topSchedule": suggestions[0]["schedule"] if suggestions else None,
            "categoryConfidence": cat_conf,
            "confidenceTier": cat_tier,
            "categoryV2": cat_v2,
            "categoryV2Label": _CSV2.get(cat_v2, {}).get("full_path", cat_v2),
            "originalCategoryV2": text_cat,
            "originalCategoryV2Label": _CSV2.get(text_cat, {}).get("full_path", text_cat) if text_cat else "",
            "categoryStatus": categoryStatus,
            "marqoCategory": marqo_id,
            "marqoCategoryLabel": _CSV2.get(marqo_id, {}).get("full_path", marqo_id) if marqo_id else "",
            "marqoConfidence": round(marqo_score, 4),
        })
    return {"algorithmVersion": ALGORITHM_VERSION, "results": results}


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

    try:
        result = process_products(products)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    out = json.dumps(result)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
