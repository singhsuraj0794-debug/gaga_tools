#!/usr/bin/env python3
"""
Curated keyword → L1-L4 category map for Category Validation.

Marqo reads SHAPE, not FUNCTION: a kids' sipper bottle can look like a party
cup, a casserole like a cooler bag. The product TITLE, however, names the
product explicitly. This map is the safe way to use the title: match product
nouns and, when a keyword maps to a single unambiguous taxonomy node, use it
to confirm/correct the image prediction.

Design rules (important):
 * Every target path MUST exist in category_l1l4.json — validated at load time
   (run `python3 _category_keywords.py` to audit). Invalid targets are skipped.
 * Only HIGH-PRECISION keywords. Bare "bottle" is NOT used: it is ambiguous
   (Kitchen > Bottle Flasks & Thermoware, Kids > Baby Bottles, Fashion >
   Travel > Bottles, Stationery > Water Bottles, Home Decor > Decorative
   Bottle). Compound keywords ("water bottle", "sipper bottle") are used.
 * Longest keyword match wins (most specific).
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

# (id, [keywords...], target full "L1 > L2 > L3 > L4")
# Keywords matched on word boundaries (case-insensitive).
KEYWORD_MAP: List[Tuple[str, List[str], str]] = [
    # ── Drinkware / bottles (ambiguous — compound keywords only) ───────────
    ("sipper", ["sipper bottle", "sipper cup", "sipper", "sippy cup"],
     "Kids & Baby > Baby Care > Feeding & Nursing > Sipper Cups"),
    ("baby_bottle", ["baby bottle", "feeding bottle", "milk bottle"],
     "Kids & Baby > Baby Care > Feeding & Nursing > Baby Bottles"),
    ("water_bottle_school", ["school water bottle", "school bottle"],
     "Stationery > Office, School and College Supplies > School Supplies > Water Bottles"),
    ("fridge_bottle", ["fridge bottle", "fridge water bottle", "apollo bottle",
                       "gym bottle", "gallon bottle", "sports bottle", "water bottle"],
     "Home & Kitchen > Kitchen Accessories > Bottle, Flasks & Thermoware > Can Cooler"),
    ("casserole", ["casserole", "casserol", "hot pot"],
     "Home & Kitchen > Kitchen Accessories > Bottle, Flasks & Thermoware > Casserole"),
    ("cooler_bag", ["cooler bag", "cooler box", "ice box", "insulated bag"],
     "Home & Kitchen > Kitchen Accessories > Bottle, Flasks & Thermoware > Cooler Bag"),
    ("hip_flask", ["hip flask"],
     "Home & Kitchen > Kitchen Accessories > Bar & Glassware > Hip Flask"),
    ("bottle_opener", ["bottle opener", "bottle openner"],
     "Home & Kitchen > Kitchen Accessories > Bar & Glassware > Bottle Opener"),
    ("bottle_racks", ["bottle rack", "bottle stand", "bottle draine r", "bottle holder"],
     "Home & Kitchen > Kitchen Accessories > Bar & Glassware > Bottle Racks"),
    ("bottle_stopper", ["bottle stopper", "wine stopper"],
     "Home & Kitchen > Kitchen Accessories > Bar & Glassware > Bottle Stopper"),

    # ── Kitchen accessories ────────────────────────────────────────────────
    ("container", ["airtight container", "utility container", "storage container",
                   "kitchen container", "container set"],
     "Home & Kitchen > Household Care & Supplies > Home Organizers & Storage > Container"),
    ("cookware_set", ["cookware set", "cookware"],
     "Home & Kitchen > Kitchen Accessories > Cookware > Cookware Set"),
    ("pressure_cooker", ["pressure cooker", "cooker"],
     "Home & Kitchen > Kitchen Accessories > Cookware > Pressure Cooker"),
    ("cutlery_set", ["cutlery set", "cutlery"],
     "Home & Kitchen > Kitchen Accessories > Tableware & Cutlery > Cutlery Set"),
    ("chopper", ["chopper", "vegetable cutter", "veggie cutter"],
     "Home & Kitchen > Kitchen Accessories > Kitchen Tools > Choppers"),
    ("cutting_board", ["cutting board", "chopping board"],
     "Home & Kitchen > Kitchen Accessories > Kitchen Tools > Cutting Boards"),
    ("jug", ["glass jug", "water jug", "jug"],
     "Home & Kitchen > Kitchen Accessories > Bar & Glassware > Jug"),
    ("cuttlery_dining", ["dinner set", "dining set"],
     "Home & Kitchen > Kitchen Accessories > Cuttlery and Dinning Sets > Dinner Sets"),

    # ── Appliances ─────────────────────────────────────────────────────────
    ("air_fryer", ["air fryer", "airfryer"],
     "Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Air Fryers"),
    ("electric_kettle", ["electric kettle", "kettle"],
     "Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Electric Kettle & Beverage Maker"),
    ("mixer_grinder", ["mixer grinder", "mixer", "juicer", "blender", "grinder"],
     "Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Mixers, Grinders & Juicers"),
    ("vacuum_cleaner", ["vacuum cleaner", "vacuum"],
     "Home & Kitchen > Home & Kitchen Appliances > Home Appliances > Vacuum Cleaners"),
    ("water_purifier", ["water purifier", "water filter", "ro system"],
     "Home & Kitchen > Home & Kitchen Appliances > Home Appliances > Water Purifiers"),
    ("iron", ["iron", "steam iron"],
     "Home & Kitchen > Home & Kitchen Appliances > Home Appliances > Irons"),
    ("fan", ["ceiling fan", "table fan", "pedestal fan"],
     "Home & Kitchen > Home & Kitchen Appliances > Home Appliances > Fans"),

    # ── Storage / organisers ───────────────────────────────────────────────
    ("storage_furniture", ["chest of drawers", "chest of drawer", "storage cabinet",
                           "plastic drawer", "drawer unit", "cabinet", "drawer"],
     "Furniture > Home and Office Furniture > Storage Furniture > Cabinet & Drawer"),
    ("wardrobe", ["wardrobe", "almirah", "cupboard"],
     "Furniture > Home and Office Furniture > Storage Furniture > Cupboards & Almirahs"),
    ("book_shelf", ["bookshelf", "book shelf"],
     "Furniture > Home and Office Furniture > Storage Furniture > Book Shelf"),
    ("home_organizer", ["organizer", "organiser", "storage box", "storage basket"],
     "Home & Kitchen > Household Care & Supplies > Home Organizers & Storage > Kitchen Rack"),

    # ── Cleaning / detergent ───────────────────────────────────────────────
    ("detergent", ["detergent", "laundry pods", "washing powder", "soap powder",
                   "laundry liquid", "liquid detergent", "laundry"],
     "Home & Kitchen > Household Care & Supplies > Home Care > Liquid Detergent"),
    ("floor_cleaner", ["floor cleaner", "bathroom cleaner", "toilet cleaner",
                       "disinfectant", "glass cleaner"],
     "Home & Kitchen > Household Care & Supplies > Home Care > Bathroom Floor Cleaner"),

    # ── Personal care ──────────────────────────────────────────────────────
    ("soap", ["soap bar", "bathing soap", "handwash", "hand wash"],
     "Beauty & Health Care > Health & Beauty > Bath and Spa > Soaps"),
    ("body_wash", ["body wash", "shower gel"],
     "Beauty & Health Care > Health & Beauty > Bath and Spa > Body Wash"),
    ("shampoo", ["shampoo"],
     "Beauty & Health Care > Health & Beauty > Hair Care > Hair Treatment"),
    ("conditioner", ["conditioner"],
     "Beauty & Health Care > Health & Beauty > Hair Care > Conditioner"),
    ("toothpaste", ["toothpaste", "tooth paste"],
     "Beauty & Health Care > Health & Beauty > Oral Care > Toothpastes"),
    ("perfume", ["perfume", "attar"],
     "Beauty & Health Care > Health & Beauty > Fragrances > Perfumes"),
    ("deodorant", ["deodorant", "body spray"],
     "Beauty & Health Care > Health & Beauty > Fragrances > Deodorants"),
    ("moisturizer", ["moisturizer", "moisturiser", "body lotion", "face cream"],
     "Beauty & Health Care > Health & Beauty > Skin Care > Moisturizer Creams"),
    ("sunscreen", ["sunscreen", "sun screen"],
     "Beauty & Health Care > Health & Beauty > Skin Care > Sunscreens"),
    # Headbands: the image reads them as party props (hats/confetti) and the
    # title text as "Ear Muffs"/"Belts" ("rabbit ear"), so neither signal
    # reaches Hair Accessories — the exact noun is anchored instead. The
    # longer "makeup headband" keyword wins for makeup-headband titles (the
    # DB has a separate Face Care leaf for those).
    ("makeup_headband", ["makeup headband"],
     "Beauty & Health Care > Health & Beauty > Face Care > Makeup Headband"),
    ("headband", ["headband", "hairband", "hair band", "alice band"],
     "Beauty & Health Care > Health & Beauty > Hair Care > Hair Accessories"),

    # ── Home furnishing / textiles ─────────────────────────────────────────
    ("bedsheet", ["bedsheet", "bed sheet", "bed cover"],
     "Home & Kitchen > Home Furnishing > Bed Linen, Blankets & Accessories > Bedsheet"),
    ("blanket", ["blanket", "comforter", "quilt", "rajai"],
     "Home & Kitchen > Home Furnishing > Bed Linen, Blankets & Accessories > Blanket"),
    ("duvet", ["duvet cover", "duvet"],
     "Home & Kitchen > Home Furnishing > Bed Linen, Blankets & Accessories > Duvet Cover"),
    ("pillow_cover", ["pillow cover", "cushion cover"],
     "Home & Kitchen > Home Furnishing > Living Room Furnishing > Cushion Pillow Cover"),
    ("curtain", ["curtain", "window curtain", "door curtain"],
     "Home & Kitchen > Home Furnishing > Curtains & Accessories > Curtain"),
    ("bath_towel", ["bath towel", "face towel", "hand towel", "towel"],
     "Home & Kitchen > Home Furnishing > Bathing Accessories > Bath Linen Set"),
    ("carpet", ["carpet", "rug", "floor mat"],
     "Home & Kitchen > Home Furnishing > Floor Coverings > Carpet Rug"),
    ("mosquito_net", ["mosquito net"],
     "Home & Kitchen > Home Furnishing > Bed Linen, Blankets & Accessories > Mosquito Nets"),

    # ── Furniture ──────────────────────────────────────────────────────────
    ("mattress", ["mattress", "bed mattress"],
     "Furniture > Home and Office Furniture > Bedroom Furniture > Bed Mattress"),
    ("bed", ["double bed", "single bed", "wooden bed", "king size bed"],
     "Furniture > Home and Office Furniture > Bedroom Furniture > Bed"),
    ("sofa", ["sofa", "sectional", "couch"],
     "Furniture > Home and Office Furniture > Living Room Furniture > Sofa & Sectional"),
    ("coffee_table", ["coffee table", "centre table", "center table"],
     "Furniture > Home and Office Furniture > Living Room Furniture > Coffee Table"),
    ("kid_table", ["kid table", "kids table", "kids study table", "study table"],
     "Furniture > Home and Office Furniture > Kids Room Furniture > Kid Table"),

    # ── Baby / kids ────────────────────────────────────────────────────────
    ("diapers", ["diaper", "diapers", "nappy", "nappies"],
     "Kids & Baby > Baby Care > Bath Care, Diapering & Potty > Diapers"),
    ("baby_bed", ["baby bed", "crib", "baby cot"],
     "Kids & Baby > Baby Care > Baby Bedding & Gear > Baby Beds"),

    # ── Electronics ────────────────────────────────────────────────────────
    ("headphone", ["earphone", "headphone", "earbud", "airbuds", "neckband"],
     "Electronics > Mobile, Tablets and Accessories > Mobile & Tablet Accessories > Headphones"),
    ("charger", ["charger", "power adapter", "adapter plug"],
     "Electronics > Mobile, Tablets and Accessories > Mobile & Tablet Accessories > Car Chargers"),
    ("powerbank", ["power bank", "powerbank"],
     "Electronics > Mobile, Tablets and Accessories > Mobile & Tablet Accessories > Mobile Battery"),
    ("smartwatch", ["smart watch", "smartwatch", "fitness band"],
     "Electronics > Smart Devices > Wearable Gadgets > Smartwatches"),

    # ── Stationery ─────────────────────────────────────────────────────────
    # NOTE: no generic "Notebooks"/"Pens" leaf exists under Stationery (it has
    # only Art Supplies / Tailoring & Embroidery / Philantrophy & Numismatics),
    # so no generic stationery keyword is mapped.
    ("lunch_box", ["lunch box", "lunchbox", "tiffin box"],
     "Stationery > Office, School and College Supplies > School Supplies > Lunch Boxes"),
    # The title-vs-full-path signal called "All-In-One Sewing Kit" a
    # "Sewing Machines" product at 0.67 (Home Appliances path wins on noise
    # words) while the image (Button/Zipper siblings) had it right — the noun
    # is anchored so L1-L4 stay in Tailoring & Embroidery.
    ("sewing_kit", ["sewing kit", "sewing kits", "stitching kit"],
     "Stationery > Pens, Stationery & Hobby Materials > Tailoring & Embroidery > Sewing Kit"),

    # ── Fashion > Jewellery ────────────────────────────────────────────────
    # NOTE: this whole branch is Inactive in the DB but IS the live assignment
    # target on real sheets, so it stays classified. High-precision nouns only
    # ("earring"/"jhumka" etc. can only mean one leaf).
    ("jhumka_earrings", ["jhumka", "jhumkas", "jhumki", "dangler", "danglers",
                         "drop earrings", "dangle earrings", "stud earring",
                         "earrings", "earring"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Earrings"),
    ("mangalsutra", ["mangalsutra", "tanmaniya"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Mangalsutra/Tanmaniya"),
    ("anklets", ["anklet", "anklets", "payal", "payals"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Anklets"),
    ("jewellery_set", ["jewellery set", "jewelry set"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Jewellery Set"),
    ("pendant_necklace", ["pendant set", "pendant chain", "pendant with"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Pendants & Lockets"),
    ("necklace", ["necklace"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Necklace Chain"),
    ("bangles_bracelets", ["bangle", "bangles", "bracelet", "bracelets", "armlet"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Bangles, Bracelets & Armlets"),
    ("rings", ["finger ring", "rings"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Rings"),
    ("maang_tikka", ["maang tikka"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Maang Tikka"),
    ("nose_pins", ["nose ring", "nose stud", "nose pin"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Nose Rings & Studs"),
    ("brooch", ["brooch"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Brooches"),
    ("kamarband", ["kamarband", "kamarbandh", "waist belt", "hip belt",
                   "waist chain", "waist hip belt"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Jewellery Set"),
    ("dog_tag", ["dog tag", "dog tags"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Dog Tags"),
    # Ear/kaan chains are worn on the ear. NOTE: real sheets are inconsistent
    # for this type (some rows → Earrings, some → Necklace Chain); we anchor
    # to Earrings as the defensible classification and flag the rest.
    ("ear_chain", ["kaan chain", "kan chain", "kaan sahara", "ear chain",
                   "ear support chain", "ear thread"],
     "Fashion > Jewellery > Artificial & Silver Jewellery > Earrings"),
]


def _load_taxonomy_paths() -> set:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_l1l4.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {p["full"] for p in data.get("paths", [])}
    except Exception:
        return set()


def _kw_re(kw: str):
    # Word-boundary match; spaces in the keyword must match literally.
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw.strip()) + r"(?![a-z0-9])", re.IGNORECASE)


_valid_paths = None
_resolved_map = None
_invalid_targets = None


def _build():
    global _resolved_map, _valid_paths, _invalid_targets
    if _resolved_map is not None:
        return
    _valid_paths = _load_taxonomy_paths()
    resolved = []
    invalid = []
    for cid, keywords, target in KEYWORD_MAP:
        if target not in _valid_paths:
            invalid.append(target)
            continue
        resolved.append((cid, [(kw, _kw_re(kw)) for kw in keywords], target))
    _resolved_map = resolved
    _invalid_targets = invalid


def match_target(title: str) -> Optional[Tuple[str, str]]:
    """
    Return (category_id, target_full_path) for the best keyword match in the
    title, or None. Longest keyword wins (most specific). Targets not present
    in the taxonomy are skipped.
    """
    _build()
    if not title:
        return None
    title_lower = title.lower()
    best = None  # (kw_len, cid, target)
    for cid, kws, target in _resolved_map:
        for kw, rx in kws:
            if rx.search(title_lower):
                if best is None or len(kw) > best[0]:
                    best = (len(kw), cid, target)
    if best is None:
        return None
    return best[1], best[2]


def split_target(target: str) -> Tuple[str, str, str, str]:
    parts = [p.strip() for p in target.split(">")]
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def invalid_targets() -> List[str]:
    _build()
    return list(_invalid_targets or [])


if __name__ == "__main__":
    bad = invalid_targets()
    print(f"[KEYWORD_MAP] {len(_resolved_map or [])} valid entries, {len(bad)} invalid targets")
    for b in bad:
        print("  INVALID:", b)
    for t in ["Radnal Kids Sipper Bottle 900ml Flip-Top with Straw",
              "Radnal PET Fridge Bottle Set 6 x 1000 ml",
              "Radnal 4-in-1 Dissolvable Laundry Pods - 15 Washes",
              "Radnal Plastic Chest of Drawers 2-Layer",
              "Radnal Airtight Utility Container 5000ml",
              "Radnal Gym Gallon Water Bottle 1.4L with Straw"]:
        print(f"  {t[:45]:47} -> {match_target(t)}")
