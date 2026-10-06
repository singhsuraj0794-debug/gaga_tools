"""
Category & Attribute Schema V4 — from category_custom_attribute_mapping_complete.xlsx
=============================================================================
TAXONOMY_VERSION = "4.1"
91 categories with improved baby keywords
"""
import re

TAXONOMY_VERSION = "4.1"
CONFIDENCE_HIGH = 0.75
CONFIDENCE_MEDIUM = 0.50

CATEGORY_TREE = {}

CATEGORY_SCHEMA = {
 "cat_cat_001": {
  "label": "a arts & crafts product",
  "full_path": "Arts & Crafts",
  "keywords": [
   "arts & crafts",
   "arts and crafts"
  ],
  "hsn": [
   "4820",
   "3926"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "item_type",
    "recommended_age",
    "kit_contents",
    "safety_certification"
   ],
   "optional": [
    "non-toxic",
    "package_dimensions",
    "weight",
    "country_of_origin",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_002": {
  "label": "a writing & drawing boards product",
  "full_path": "Arts & Crafts > Writing & Drawing Boards",
  "keywords": [
   "arts & crafts",
   "arts and crafts",
   "writing & drawing boards",
   "writing and drawing boards"
  ],
  "hsn": [
   "4820",
   "3926"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "board_type",
    "surface_material",
    "frame_material",
    "erasable"
   ],
   "optional": [
    "double-sided",
    "included_accessories",
    "dimensions",
    "weight",
    "recommended_age",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_003": {
  "label": "a automobile accessories product",
  "full_path": "Automobile Accessories",
  "keywords": [
   "automobile accessories",
   "automobile accessory"
  ],
  "hsn": [
   "8708"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "vehicle_type_compatibility",
    "installation_method",
    "material",
    "color"
   ],
   "optional": [
    "dimensions",
    "weight",
    "warranty",
    "weather_resistance",
    "certification"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_004": {
  "label": "a bike accessories product",
  "full_path": "Automobile Accessories > Bike Accessories",
  "keywords": [
   "automobile accessories",
   "automobile accessory",
   "bike accessories",
   "bike accessory"
  ],
  "hsn": [
   "8708"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "bike_compatibility",
    "mounting_type",
    "material",
    "reflective"
   ],
   "optional": [
    "water_resistance",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_005": {
  "label": "a car accessories product",
  "full_path": "Automobile Accessories > Car Accessories",
  "keywords": [
   "automobile accessories",
   "automobile accessory",
   "car accessories",
   "car accessory"
  ],
  "hsn": [
   "8708"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "placement_in_vehicle",
    "power_source_(if_electronic)",
    "voltage",
    "material"
   ],
   "optional": [
    "color",
    "dimensions",
    "weight",
    "warranty",
    "easy_clean"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_006": {
  "label": "a electronics product",
  "full_path": "Electronics",
  "keywords": [
   "electronics"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "model_number",
    "connectivity_technology",
    "power_source",
    "operating_system"
   ],
   "optional": [
    "included_components",
    "warranty",
    "dimensions",
    "weight",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_007": {
  "label": "a camera & accessories product",
  "full_path": "Electronics > Camera & Accessories",
  "keywords": [
   "electronics",
   "camera & accessories",
   "camera & accessory",
   "camera and accessories"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "compatible_mounts",
    "material",
    "waterproof_rating",
    "weight"
   ],
   "optional": [
    "dimensions",
    "warranty",
    "color",
    "included_straps/cases"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_008": {
  "label": "a binoculars product",
  "full_path": "Electronics > Camera & Accessories > Binoculars",
  "keywords": [
   "electronics",
   "camera & accessories",
   "camera & accessory",
   "camera and accessories",
   "binoculars"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "magnification",
    "objective_lens_diameter",
    "prism_type",
    "field_of_view"
   ],
   "optional": [
    "focus_system",
    "water_resistance",
    "dimensions",
    "weight",
    "warranty",
    "tripod_adaptable"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_009": {
  "label": "a cameras product",
  "full_path": "Electronics > Camera & Accessories > Cameras",
  "keywords": [
   "electronics",
   "camera & accessories",
   "camera & accessory",
   "camera and accessories",
   "cameras"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "sensor_resolution",
    "optical_zoom",
    "video_capture_resolution",
    "display_size"
   ],
   "optional": [
    "connectivity_type",
    "battery_life",
    "memory_card_type",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_010": {
  "label": "a telescopes product",
  "full_path": "Electronics > Camera & Accessories > Telescopes",
  "keywords": [
   "electronics",
   "camera & accessories",
   "camera & accessory",
   "camera and accessories",
   "telescopes"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "optical_design",
    "aperture",
    "focal_length",
    "mount_type"
   ],
   "optional": [
    "eyepieces_included",
    "finderscope",
    "tripod_material",
    "total_weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_011": {
  "label": "a earphones product",
  "full_path": "Electronics > Earphones",
  "keywords": [
   "electronics",
   "earphones"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "form_factor_(in-ear/on-ear)",
    "connectivity_(wireless/wired)",
    "bluetooth_version",
    "active_noise_cancellation"
   ],
   "optional": [
    "battery_playback_time",
    "microphone_included",
    "water_resistance_rating",
    "impedance",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_012": {
  "label": "a laptop & tablet accessories product",
  "full_path": "Electronics > Laptop & Tablet Accessories",
  "keywords": [
   "electronics",
   "laptop & tablet accessories",
   "laptop & tablet accessory",
   "laptop and tablet accessories"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "compatible_device_size",
    "material",
    "closure_type",
    "pockets_&_compartments"
   ],
   "optional": [
    "shock_absorbent",
    "dimensions",
    "weight",
    "color",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_013": {
  "label": "a speakers product",
  "full_path": "Electronics > Speakers",
  "keywords": [
   "electronics",
   "speakers"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "speaker_maximum_output_power",
    "connectivity_technology_(bluetooth/aux)",
    "battery_life",
    "waterproof_rating"
   ],
   "optional": [
    "frequency_response",
    "signal-to-noise_ratio",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_014": {
  "label": "a usb gadgets product",
  "full_path": "Electronics > USB Gadgets",
  "keywords": [
   "electronics",
   "usb gadgets"
  ],
  "hsn": [
   "8517",
   "8518",
   "8471",
   "8544",
   "8504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "interface_type_(usb-c/usb-a)",
    "data_transfer_speed",
    "power_output",
    "material"
   ],
   "optional": [
    "led_indicator",
    "dimensions",
    "weight",
    "compatibility",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_015": {
  "label": "a fashion accessories product",
  "full_path": "Fashion Accessories",
  "keywords": [
   "fashion accessories",
   "fashion accessory"
  ],
  "hsn": [],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "department",
    "material_composition",
    "care_instructions",
    "color"
   ],
   "optional": [
    "pattern",
    "style",
    "country_of_origin",
    "season",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_016": {
  "label": "a men's accessories product",
  "full_path": "Fashion Accessories > Men's Accessories",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "men's accessories",
   "men's accessory"
  ],
  "hsn": [],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "item_sub-type",
    "material",
    "color",
    "closure_type"
   ],
   "optional": [
    "dimensions/size",
    "care_instructions",
    "style",
    "gift_box_included",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_017": {
  "label": "a men's watches product",
  "full_path": "Fashion Accessories > Men's Accessories > Men's Watches",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "men's accessories",
   "men's accessory",
   "men's watches"
  ],
  "hsn": [
   "9102"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "movement_type_(quartz/automatic)",
    "dial_shape",
    "case_material",
    "strap_material"
   ],
   "optional": [
    "water_resistance_depth",
    "case_diameter",
    "clasp_type",
    "display_type_(analog/digital)",
    "warranty_period"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_018": {
  "label": "a women's accessories product",
  "full_path": "Fashion Accessories > Women's Accessories",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "women's accessories",
   "women's accessory"
  ],
  "hsn": [],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "item_sub-type",
    "material",
    "color",
    "closure/attachment_type"
   ],
   "optional": [
    "dimensions/size",
    "care_instructions",
    "style",
    "occasion",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_019": {
  "label": "a women's watches product",
  "full_path": "Fashion Accessories > Women's Accessories > Women's Watches",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "women's accessories",
   "women's accessory",
   "women's watches"
  ],
  "hsn": [
   "9102"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "movement_type",
    "dial_shape",
    "case_material",
    "strap_material"
   ],
   "optional": [
    "water_resistance_depth",
    "case_diameter",
    "gemstone_accents",
    "display_type",
    "warranty_period"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_020": {
  "label": "a women's hair accessories product",
  "full_path": "Fashion Accessories > Women's Accessories > Women's Hair Accessories",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "women's accessories",
   "women's accessory",
   "women's hair accessories",
   "women's hair accessory"
  ],
  "hsn": [
   "3305"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "accessory_type_(headband/clip/scrunchie)",
    "material",
    "color/pattern",
    "hair_type_suitability"
   ],
   "optional": [
    "dimensions",
    "closure/hold_mechanism",
    "quantity_in_set",
    "weight",
    "care_instructions"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_021": {
  "label": "a women's jewellery product",
  "full_path": "Fashion Accessories > Women's Jewellery",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "women's jewellery"
  ],
  "hsn": [
   "7113",
   "7117"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "metal_type",
    "gemstone_type",
    "plating",
    "setting_type"
   ],
   "optional": [
    "clasp_type",
    "chain_length/ring_size",
    "hypoallergenic",
    "occasion",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_022": {
  "label": "a furniture product",
  "full_path": "Furniture",
  "keywords": [
   "furniture"
  ],
  "hsn": [
   "9401",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "primary_material",
    "assembly_required",
    "style",
    "max_weight_capacity"
   ],
   "optional": [
    "dimensions_(l_x_w_x_h)",
    "weight",
    "warranty",
    "care_&_maintenance",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_023": {
  "label": "a table & chair product",
  "full_path": "Furniture > Table & Chair",
  "keywords": [
   "furniture",
   "table & chair",
   "table and chair"
  ],
  "hsn": [
   "9401",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "set_includes",
    "table_shape",
    "chair_count",
    "frame_material"
   ],
   "optional": [
    "upholstery_material",
    "max_weight_capacity",
    "dimensions",
    "assembly_required",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_024": {
  "label": "a general merchandise product",
  "full_path": "General Merchandise",
  "keywords": [
   "general merchandise"
  ],
  "hsn": [
   "9503"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "product_category",
    "material",
    "color",
    "dimensions"
   ],
   "optional": [
    "weight",
    "use_case",
    "package_contents",
    "safety_standard",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_025": {
  "label": "a baby accessories product",
  "full_path": "General Merchandise > Baby Accessories",
  "keywords": [
   "general merchandise",
   "baby accessories",
   "baby accessory"
  ],
  "hsn": [
   "6111",
   "3924"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "target_age_group",
    "material_composition",
    "bpa_free",
    "hypoallergenic"
   ],
   "optional": [
    "care_instructions",
    "safety_certification",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_026": {
  "label": "a kid bags product",
  "full_path": "General Merchandise > Kid Bags",
  "keywords": [
   "general merchandise",
   "kid bags"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "target_age_group",
    "material",
    "capacity_(liters)",
    "closure_type"
   ],
   "optional": [
    "number_of_pockets",
    "padded_straps",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_027": {
  "label": "a home & garden product",
  "full_path": "Home & Garden",
  "keywords": [
   "home & garden",
   "home and garden"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "material",
    "color",
    "style",
    "indoor/outdoor_usage"
   ],
   "optional": [
    "dimensions",
    "weight",
    "assembly_required",
    "care_instructions",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_028": {
  "label": "a bathroom accessories product",
  "full_path": "Home & Garden > Bathroom Accessories",
  "keywords": [
   "home & garden",
   "home and garden",
   "bathroom accessories",
   "bathroom accessory"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "set/individual_piece",
    "mounting_type",
    "material",
    "rust_resistant"
   ],
   "optional": [
    "color",
    "dimensions",
    "weight",
    "care_instructions",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_029": {
  "label": "a decor product",
  "full_path": "Home & Garden > Decor",
  "keywords": [
   "home & garden",
   "home and garden",
   "decor"
  ],
  "hsn": [
   "6913",
   "3926",
   "7013"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "decor_type",
    "material",
    "color",
    "placement"
   ],
   "optional": [
    "theme",
    "dimensions",
    "weight",
    "care_instructions",
    "handmade_(yes/no)"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_030": {
  "label": "a fireplaces product",
  "full_path": "Home & Garden > Fireplaces",
  "keywords": [
   "home & garden",
   "home and garden",
   "fireplaces"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "fireplace_type_(electric/gas/wood)",
    "heating_coverage_area",
    "power_rating",
    "material"
   ],
   "optional": [
    "safety_features",
    "dimensions",
    "weight",
    "warranty",
    "installation_required"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_031": {
  "label": "a household appliances product",
  "full_path": "Home & Garden > Household Appliances",
  "keywords": [
   "home & garden",
   "home and garden",
   "household appliances"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "appliance_type",
    "energy_efficiency_rating",
    "power_consumption",
    "voltage"
   ],
   "optional": [
    "capacity",
    "color",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_032": {
  "label": "a household supplies product",
  "full_path": "Home & Garden > Household Supplies",
  "keywords": [
   "home & garden",
   "home and garden",
   "household supplies",
   "door stopper",
   "doorstop",
   "door stop",
   "slam stopper",
   "wall protector",
   "crash pad",
   "door guard",
   "home safety"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "supply_type",
    "material/composition",
    "quantity/pack_size",
    "scent/fragrance"
   ],
   "optional": [
    "eco-friendly",
    "dimensions",
    "weight",
    "storage_instructions",
    "shelf_life"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_033": {
  "label": "a drinkware product",
  "full_path": "Home & Garden > Household Supplies > Drinkware",
  "keywords": [
   "home & garden",
   "home and garden",
   "household supplies",
   "drinkware"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "drinkware_type_(mug/tumbler/bottle)",
    "capacity_(ml/oz)",
    "material_(stainless_steel/glass)",
    "insulation_type_(double_wall_vacuum)"
   ],
   "optional": [
    "bpa_free",
    "dishwasher_safe",
    "leak_proof",
    "dimensions",
    "weight"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_034": {
  "label": "a kitchen & dining product",
  "full_path": "Home & Garden > Kitchen & Dining",
  "keywords": [
   "home & garden",
   "home and garden",
   "kitchen & dining",
   "kitchen and dining"
  ],
  "hsn": [
   "7323",
   "7615"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "material",
    "heat_resistance",
    "dishwasher_safe"
   ],
   "optional": [
    "microwave_safe",
    "color",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_035": {
  "label": "a kitchen utilities product",
  "full_path": "Home & Garden > Kitchen Utilities",
  "keywords": [
   "home & garden",
   "home and garden",
   "kitchen utilities"
  ],
  "hsn": [
   "7323",
   "7615"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "utility_type",
    "blade/surface_material",
    "handle_material",
    "dishwasher_safe"
   ],
   "optional": [
    "ergonomic_grip",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_036": {
  "label": "a lawn & garden product",
  "full_path": "Home & Garden > Lawn & Garden",
  "keywords": [
   "home & garden",
   "home and garden",
   "lawn & garden",
   "lawn and garden"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "weather_resistant",
    "material",
    "power_source_(if_motorized)"
   ],
   "optional": [
    "coverage_area",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_037": {
  "label": "a lighting product",
  "full_path": "Home & Garden > Lighting",
  "keywords": [
   "home & garden",
   "home and garden",
   "lighting"
  ],
  "hsn": [
   "9405",
   "8539"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "lighting_technology_(led/incandescent)",
    "wattage",
    "color_temperature_(kelvin)",
    "dimmable"
   ],
   "optional": [
    "power_source",
    "material",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_038": {
  "label": "a linens & bedding product",
  "full_path": "Home & Garden > Linens & Bedding",
  "keywords": [
   "home & garden",
   "home and garden",
   "linens & bedding",
   "linens and bedding"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "fabric_type",
    "thread_count",
    "size_(twin/queen/king)",
    "machine_washable"
   ],
   "optional": [
    "hypoallergenic",
    "color/pattern",
    "included_pieces",
    "dimensions",
    "weight"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_039": {
  "label": "a parasols & rain umbrellas product",
  "full_path": "Home & Garden > Parasols & Rain Umbrellas",
  "keywords": [
   "home & garden",
   "home and garden",
   "parasols & rain umbrellas",
   "parasols and rain umbrellas"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "canopy_material",
    "frame_material",
    "mechanism_(manual/automatic)",
    "uv_protection_rating"
   ],
   "optional": [
    "windproof",
    "open_diameter",
    "folded_length",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_040": {
  "label": "a pool & spa product",
  "full_path": "Home & Garden > Pool & Spa",
  "keywords": [
   "home & garden",
   "home and garden",
   "pool & spa",
   "pool and spa"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "material",
    "capacity",
    "pump_included"
   ],
   "optional": [
    "filter_type",
    "dimensions",
    "weight",
    "warranty",
    "safety_cover_included"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_041": {
  "label": "a umbrella sleevs & cases product",
  "full_path": "Home & Garden > Umbrella Sleevs & Cases",
  "keywords": [
   "home & garden",
   "home and garden",
   "umbrella sleevs & cases",
   "umbrella sleevs and cases"
  ],
  "hsn": [
   "6302",
   "9403"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "compatible_umbrella_size",
    "material",
    "waterproof",
    "closure_type"
   ],
   "optional": [
    "carry_strap",
    "dimensions",
    "weight",
    "color",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_042": {
  "label": "a kids & baby product",
  "full_path": "Kids & Baby",
  "keywords": [
   "kids & baby",
   "kids and baby"
  ],
  "hsn": [
   "6111",
   "3924"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "target_age_range",
    "material_composition",
    "safety_standards_met",
    "non-toxic"
   ],
   "optional": [
    "hypoallergenic",
    "care_instructions",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_043": {
  "label": "a luggage & bags product",
  "full_path": "Luggage & Bags",
  "keywords": [
   "luggage & bags",
   "luggage and bags"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "bag_type",
    "material",
    "capacity_(liters)",
    "closure_type"
   ],
   "optional": [
    "number_of_compartments",
    "water_resistant",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_044": {
  "label": "a backpacks product",
  "full_path": "Luggage & Bags > Backpacks",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "backpacks"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "laptop_compartment_size",
    "capacity_(liters)",
    "material",
    "water_resistant"
   ],
   "optional": [
    "padded_back_panel",
    "usb_charging_port",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_045": {
  "label": "a cosmetic & toiletry bags product",
  "full_path": "Luggage & Bags > Cosmetic & Toiletry Bags",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "cosmetic & toiletry bags",
   "cosmetic and toiletry bags"
  ],
  "hsn": [
   "3304"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "closure_type",
    "material",
    "waterproof_lining",
    "number_of_pockets/slots"
   ],
   "optional": [
    "hang_hook_included",
    "dimensions",
    "weight",
    "color",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_046": {
  "label": "a duffel bags product",
  "full_path": "Luggage & Bags > Duffel Bags",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "duffel bags"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "capacity_(liters)",
    "material",
    "shoe_compartment",
    "strap_type_(removable/padded)"
   ],
   "optional": [
    "water_resistant",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_047": {
  "label": "a garment bags product",
  "full_path": "Luggage & Bags > Garment Bags",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "garment bags"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "capacity_(number_of_suits)",
    "material",
    "transparent_window",
    "full-length_zipper"
   ],
   "optional": [
    "hanger_loop",
    "foldable",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_048": {
  "label": "a kids bags product",
  "full_path": "Luggage & Bags > Kids Bags",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "kids bags"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "target_age",
    "character_theme",
    "material",
    "lightweight"
   ],
   "optional": [
    "padded_straps",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_049": {
  "label": "a laptop sleeves product",
  "full_path": "Luggage & Bags > Laptop Sleeves",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "laptop sleeves"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "compatible_laptop_size_(inches)",
    "material",
    "shock_absorption_foam",
    "water_repellent"
   ],
   "optional": [
    "zipped_pocket_for_accessories",
    "dimensions",
    "weight",
    "color",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_050": {
  "label": "a luggage accessories product",
  "full_path": "Luggage & Bags > Luggage Accessories",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "luggage accessories",
   "luggage accessory"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "accessory_type_(lock/tag/strap)",
    "material",
    "tsa_approved_(if_lock)",
    "color"
   ],
   "optional": [
    "dimensions",
    "weight",
    "durability",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_051": {
  "label": "a shopping totes product",
  "full_path": "Luggage & Bags > Shopping Totes",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "shopping totes"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "material_(canvas/jute/cotton)",
    "foldable",
    "reinforced_handles",
    "capacity"
   ],
   "optional": [
    "washable",
    "dimensions",
    "weight",
    "color",
    "eco-friendly"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_052": {
  "label": "a sling bags product",
  "full_path": "Luggage & Bags > Sling Bags",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "sling bags"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "strap_style_(reversible/adjustable)",
    "material",
    "anti-theft_pocket",
    "headphone_port"
   ],
   "optional": [
    "water_resistant",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_053": {
  "label": "a suitcases product",
  "full_path": "Luggage & Bags > Suitcases",
  "keywords": [
   "luggage & bags",
   "luggage and bags",
   "suitcases"
  ],
  "hsn": [
   "4202"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "size_(cabin/medium/large)",
    "material_(polycarbonate/abs)",
    "wheel_type_(360_spinner)",
    "tsa_lock_included"
   ],
   "optional": [
    "expandable",
    "capacity_(l)",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_054": {
  "label": "a sporting goods product",
  "full_path": "Sporting Goods",
  "keywords": [
   "sporting goods"
  ],
  "hsn": [
   "9506"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "sport_type",
    "material",
    "skill_level",
    "gender"
   ],
   "optional": [
    "dimensions",
    "weight",
    "warranty",
    "safety_features",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_055": {
  "label": "a athletics product",
  "full_path": "Sporting Goods > Athletics",
  "keywords": [
   "sporting goods",
   "athletics"
  ],
  "hsn": [
   "9506"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "athletic_discipline",
    "material",
    "size/fit",
    "durability"
   ],
   "optional": [
    "weather_resistant",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_056": {
  "label": "a fitness & general exercise equipment product",
  "full_path": "Sporting Goods > Fitness & General Exercise Equipment",
  "keywords": [
   "sporting goods",
   "fitness & general exercise equipment",
   "fitness and general exercise equipment"
  ],
  "hsn": [
   "9506"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "equipment_type",
    "maximum_user_weight_capacity",
    "resistance_levels/weight_stack",
    "foldable"
   ],
   "optional": [
    "digital_display/monitor",
    "dimensions",
    "weight",
    "warranty",
    "assembly_required"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_057": {
  "label": "a indoor games product",
  "full_path": "Sporting Goods > Indoor Games",
  "keywords": [
   "sporting goods",
   "indoor games"
  ],
  "hsn": [
   "9506"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "game_type",
    "minimum_players",
    "recommended_age",
    "material"
   ],
   "optional": [
    "included_accessories",
    "dimensions",
    "weight",
    "warranty",
    "storage_box_included"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_058": {
  "label": "a outdoor recreation product",
  "full_path": "Sporting Goods > Outdoor Recreation",
  "keywords": [
   "sporting goods",
   "outdoor recreation"
  ],
  "hsn": [
   "9506"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "activity_type",
    "material",
    "waterproof/weatherproof",
    "portability/foldable"
   ],
   "optional": [
    "max_weight_capacity",
    "dimensions",
    "weight",
    "warranty",
    "carrying_bag_included"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_059": {
  "label": "a stationery product",
  "full_path": "Stationery",
  "keywords": [
   "stationery"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "material",
    "color",
    "dimensions"
   ],
   "optional": [
    "weight",
    "pack_size",
    "non-toxic",
    "country_of_origin",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_060": {
  "label": "a paper handling product",
  "full_path": "Stationery > Paper Handling",
  "keywords": [
   "stationery",
   "paper handling"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "device_type_(trimmer/puncher/binder)",
    "sheet_capacity",
    "cutting_length/hole_count",
    "material"
   ],
   "optional": [
    "safety_guard",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_061": {
  "label": "a craft materials product",
  "full_path": "Stationery > Craft Materials",
  "keywords": [
   "stationery",
   "craft materials"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "material_type",
    "color_assortment",
    "non-toxic",
    "washable"
   ],
   "optional": [
    "recommended_age",
    "quantity",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_062": {
  "label": "a filing & organization product",
  "full_path": "Stationery > Filing & Organization",
  "keywords": [
   "stationery",
   "filing & organization",
   "filing and organization"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "organizer_type",
    "capacity_(sheet/folder_count)",
    "material",
    "closure_type"
   ],
   "optional": [
    "color",
    "dimensions",
    "weight",
    "warranty",
    "index_tabs_included"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_063": {
  "label": "a general product",
  "full_path": "Stationery > General",
  "keywords": [
   "stationery",
   "general"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "item_type",
    "material",
    "color",
    "dimensions"
   ],
   "optional": [
    "weight",
    "quantity",
    "usage",
    "warranty",
    "country_of_origin"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_064": {
  "label": "a general office supplies product",
  "full_path": "Stationery > General Office Supplies",
  "keywords": [
   "stationery",
   "general office supplies"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "supply_type",
    "material",
    "color",
    "pack_size"
   ],
   "optional": [
    "dimensions",
    "weight",
    "durable",
    "refillable_(if_applicable)",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_065": {
  "label": "a lap desks product",
  "full_path": "Stationery > Lap Desks",
  "keywords": [
   "stationery",
   "lap desks"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "cushion_material",
    "surface_material",
    "built-in_device_slot",
    "wrist_rest_included"
   ],
   "optional": [
    "portability_handle",
    "dimensions",
    "weight",
    "color",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_066": {
  "label": "a office & chair mats product",
  "full_path": "Stationery > Office & Chair Mats",
  "keywords": [
   "stationery",
   "office & chair mats",
   "office and chair mats"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "floor_type_compatibility_(hardwood/carpet)",
    "material",
    "shape",
    "anti-slip_backing"
   ],
   "optional": [
    "thickness",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_067": {
  "label": "a office instruments product",
  "full_path": "Stationery > Office Instruments",
  "keywords": [
   "stationery",
   "office instruments"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "instrument_type_(calculator/stapler/punch)",
    "power_source_(if_electronic)",
    "material",
    "capacity"
   ],
   "optional": [
    "color",
    "dimensions",
    "weight",
    "warranty",
    "precision"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_068": {
  "label": "a presentation supplies product",
  "full_path": "Stationery > Presentation Supplies",
  "keywords": [
   "stationery",
   "presentation supplies"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "supply_type_(board/pointer/folder)",
    "material",
    "size",
    "erasable/reusable"
   ],
   "optional": [
    "color",
    "dimensions",
    "weight",
    "warranty",
    "portable"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_069": {
  "label": "a shipping supplies product",
  "full_path": "Stationery > Shipping Supplies",
  "keywords": [
   "stationery",
   "shipping supplies"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "supply_type_(box/tape/mailer)",
    "material",
    "dimensions_(l_x_w_x_h)",
    "adhesive_strength"
   ],
   "optional": [
    "water_resistant",
    "pack_quantity",
    "weight_capacity",
    "weight",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_070": {
  "label": "a writing instruments product",
  "full_path": "Stationery > Writing Instruments",
  "keywords": [
   "stationery",
   "writing instruments"
  ],
  "hsn": [
   "4820",
   "9608"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "pen/pencil_type",
    "ink_color",
    "line_width/point_size",
    "refillable"
   ],
   "optional": [
    "grip_type",
    "pack_size",
    "body_material",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_071": {
  "label": "a toys & games product",
  "full_path": "Toys & Games",
  "keywords": [
   "toys & games",
   "toys and games"
  ],
  "hsn": [
   "9504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "recommended_minimum_age",
    "maximum_age",
    "material",
    "battery_operated"
   ],
   "optional": [
    "safety_warnings",
    "number_of_players",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_072": {
  "label": "a games product",
  "full_path": "Toys & Games > Games",
  "keywords": [
   "toys & games",
   "toys and games",
   "games"
  ],
  "hsn": [
   "9504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "game_genre",
    "player_count",
    "play_time",
    "recommended_age"
   ],
   "optional": [
    "rules_included",
    "material",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_073": {
  "label": "a outdoor play equipment product",
  "full_path": "Toys & Games > Outdoor Play Equipment",
  "keywords": [
   "toys & games",
   "toys and games",
   "outdoor play equipment"
  ],
  "hsn": [
   "9504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "equipment_type",
    "max_weight_capacity",
    "material",
    "weather_resistant"
   ],
   "optional": [
    "safety_certification",
    "assembly_required",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_074": {
  "label": "a puzzles product",
  "full_path": "Toys & Games > Puzzles",
  "keywords": [
   "toys & games",
   "toys and games",
   "puzzles"
  ],
  "hsn": [
   "9504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "puzzle_type_(jigsaw/3d/wooden)",
    "piece_count",
    "completed_dimensions",
    "material"
   ],
   "optional": [
    "recommended_age",
    "skill_development",
    "weight",
    "box_included",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_075": {
  "label": "a toys product",
  "full_path": "Toys & Games > Toys",
  "keywords": [
   "toys & games",
   "toys and games",
   "toys"
  ],
  "hsn": [
   "9504"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "toy_category",
    "material",
    "interactive_features",
    "battery_type"
   ],
   "optional": [
    "non-toxic",
    "recommended_age",
    "dimensions",
    "weight",
    "safety_standard"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_076": {
  "label": "a watches (overall) product",
  "full_path": "Watches (Overall)",
  "keywords": [
   "watches (overall)"
  ],
  "hsn": [
   "9102"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "movement_type_(quartz/automatic/smart)",
    "dial_shape",
    "case_material",
    "strap_material"
   ],
   "optional": [
    "water_resistance",
    "display_type",
    "target_demographics",
    "warranty_period",
    "clasp_type"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_077": {
  "label": "a kids watches product",
  "full_path": "Fashion Accessories > Watches (Overall) > Kids Watches",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "watches (overall)",
   "kids watches"
  ],
  "hsn": [
   "9102"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "target_age_group",
    "movement_type",
    "strap_material_(silicone/nylon)",
    "water_resistance"
   ],
   "optional": [
    "educational_features_(time-learning)",
    "shock_resistant",
    "display_type",
    "color/theme",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_078": {
  "label": "a couple watches product",
  "full_path": "Fashion Accessories > Watches (Overall) > Couple Watches",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "watches (overall)",
   "couple watches"
  ],
  "hsn": [
   "9102"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "set_includes_(his_&_her_watches)",
    "movement_type",
    "matching_design",
    "case_material"
   ],
   "optional": [
    "strap_material",
    "water_resistance",
    "gift_box_included",
    "warranty",
    "dial_color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_079": {
  "label": "a gifting (overall) product",
  "full_path": "Gifting (Overall)",
  "keywords": [
   "gifting (overall)"
  ],
  "hsn": [],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "gift_type",
    "occasion_suitability",
    "recipient",
    "gift_box_included"
   ],
   "optional": [
    "customizable/personalized",
    "theme",
    "dimensions",
    "weight",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_080": {
  "label": "a festive gifting product",
  "full_path": "Gifting (Overall) > Festive Gifting",
  "keywords": [
   "gifting (overall)",
   "festive gifting"
  ],
  "hsn": [
   "9405",
   "3926"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "festival/holiday_theme",
    "hamper/gift_set_contents",
    "packaging_type",
    "shelf_life_(if_food/consumable)"
   ],
   "optional": [
    "weight",
    "dimensions",
    "greeting_card_included",
    "allergen_info_(if_applicable)",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_081": {
  "label": "a keychains product",
  "full_path": "Fashion Accessories > Keychains",
  "keywords": [
   "fashion accessories",
   "fashion accessory",
   "keychains"
  ],
  "hsn": [
   "7326",
   "3926"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "keychain_type",
    "material_(metal/leather/acrylic)",
    "theme/character",
    "ring_diameter"
   ],
   "optional": [
    "attachment_mechanism_(clasp/ring)",
    "dimensions",
    "weight",
    "durability",
    "gift_packaging"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_082": {
  "label": "a baby socks product",
  "full_path": "General Merchandise > Baby Accessories > Baby Socks",
  "keywords": [
   "baby socks",
   "baby sock",
   "baby stocking",
   "baby tights",
   "infant socks",
   "newborn socks",
   "kids socks",
   "baby accessories",
   "general merchandise",
   "baby accessories",
   "baby accessory",
   "baby socks"
  ],
  "hsn": [
   "6115"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "age_group_(months)",
    "material_composition_(cotton/spandex)",
    "anti-slip_grips",
    "pack_quantity"
   ],
   "optional": [
    "hypoallergenic",
    "machine_washable",
    "color/pattern_assortment",
    "seamless_toe",
    "safety_standard"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_083": {
  "label": "a festive accessories product",
  "full_path": "Gifting (Overall) > Festive Accessories",
  "keywords": [
   "gifting (overall)",
   "festive accessories",
   "festive accessory"
  ],
  "hsn": [
   "9405",
   "3926"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "festival_theme",
    "accessory_type",
    "material",
    "reusable"
   ],
   "optional": [
    "color",
    "dimensions",
    "weight",
    "care_instructions",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_084": {
  "label": "a festive essentials product",
  "full_path": "Gifting (Overall) > Festive Essentials",
  "keywords": [
   "gifting (overall)",
   "festive essentials"
  ],
  "hsn": [
   "9405",
   "3926"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "essential_type",
    "material",
    "occasion/festival",
    "quantity"
   ],
   "optional": [
    "dimensions",
    "weight",
    "safety_certification",
    "storage_guide",
    "warranty"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_085": {
  "label": "a anniversary & everyday gifting product",
  "full_path": "Gifting (Overall) > Anniversary & Everyday Gifting",
  "keywords": [
   "gifting (overall)",
   "anniversary & everyday gifting",
   "anniversary and everyday gifting"
  ],
  "hsn": [],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "occasion_(anniversary/birthday/housewarming)",
    "item_contents",
    "custom_engraving_available",
    "gift_packaging"
   ],
   "optional": [
    "material",
    "dimensions",
    "weight",
    "warranty",
    "card_included"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_086": {
  "label": "a baby feeding product",
  "full_path": "Kids & Baby > Baby Feeding",
  "keywords": [
   "baby feeding",
   "feeding bottle",
   "baby bottle",
   "sipper",
   "nipple",
   "baby feeder",
   "breast pump",
   "baby food",
   "kids & baby",
   "baby feeding"
  ],
  "hsn": [
   "6111",
   "3924"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "material",
    "bpa_free",
    "capacity",
    "age_range",
    "nipple_flow",
    "sterilizable"
   ],
   "optional": [
    "microwave_safe",
    "dishwasher_safe",
    "non-toxic",
    "leak-proof",
    "colic_prevention",
    "dimensions",
    "weight"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_087": {
  "label": "a baby skincare & bath product",
  "full_path": "Kids & Baby > Baby Skincare & Bath",
  "keywords": [
   "baby skincare",
   "baby bath",
   "baby powder",
   "baby puff",
   "powder puff",
   "baby lotion",
   "baby cream",
   "baby oil",
   "baby shampoo",
   "baby soap",
   "baby wash",
   "diaper cream",
   "baby wipe",
   "kids & baby",
   "baby skincare & bath"
  ],
  "hsn": [
   "3304"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "material",
    "hypoallergenic",
    "dermatologist_tested",
    "tear-free"
   ],
   "optional": [
    "paraben-free",
    "sulfate-free",
    "phthalate-free",
    "age_range",
    "capacity",
    "dimensions",
    "weight"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_088": {
  "label": "a baby gear product",
  "full_path": "Kids & Baby > Baby Gear",
  "keywords": [
   "baby gear",
   "stroller",
   "pram",
   "baby carrier",
   "baby walker",
   "baby chair",
   "high chair",
   "baby monitor",
   "baby bathtub",
   "kids & baby",
   "baby gear"
  ],
  "hsn": [
   "6111",
   "3924"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "material",
    "weight_capacity",
    "age_range",
    "safety_certification"
   ],
   "optional": [
    "foldable",
    "portable",
    "assembly_required",
    "dimensions",
    "weight",
    "warranty",
    "color"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_089": {
  "label": "a baby clothing product",
  "full_path": "Kids & Baby > Baby Clothing",
  "keywords": [
   "baby clothing",
   "baby clothes",
   "baby dress",
   "baby onesie",
   "baby romper",
   "baby shirt",
   "baby pant",
   "infant clothing",
   "newborn clothes",
   "kids & baby",
   "baby clothing"
  ],
  "hsn": [
   "61",
   "62"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "material",
    "size",
    "color",
    "age_range"
   ],
   "optional": [
    "season",
    "gender",
    "closure_type",
    "machine_washable",
    "pattern",
    "pack_count"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_090": {
  "label": "a diapers & hygiene product",
  "full_path": "Kids & Baby > Diapers & Hygiene",
  "keywords": [
   "diaper",
   "nappy",
   "baby diaper",
   "newborn diaper",
   "baby wipe",
   "diaper rash",
   "baby hygiene",
   "kids & baby",
   "diapers & hygiene"
  ],
  "hsn": [
   "9619"
  ],
  "unit": "required",
  "attributes": {
   "required": [
    "brand",
    "product_type",
    "size",
    "age_range",
    "pack_count",
    "hypoallergenic"
   ],
   "optional": [
    "fragrance-free",
    "wetness_indicator",
    "breathable",
    "material",
    "dimensions",
    "weight"
   ],
   "value_sets": {}
  }
 },
 "cat_cat_091": {
  "label": "a baby toys product",
  "full_path": "Kids & Baby > Baby Toys",
  "keywords": [
   "baby toy",
   "baby rattle",
   "teether",
   "baby teether",
   "infant toy",
   "newborn toy",
   "baby sensory",
   "baby play",
   "kids & baby",
   "baby toys"
  ],
  "hsn": [
   "6111",
   "3924"
  ],
  "unit": "optional",
  "attributes": {
   "required": [
    "brand",
    "toy_type",
    "material",
    "age_range",
    "bpa_free",
    "non-toxic"
   ],
   "optional": [
    "teether",
    "sensory_development",
    "easy_grip",
    "dishwasher_safe",
    "dimensions",
    "weight",
    "color"
   ],
   "value_sets": {}
  }
 }
}

def _wb(tok):
    """Word-boundary regex for a lowercase token — 'top' must not match
    inside 'stopper'."""
    return rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])"


def match_category(title, description, clip_type=""):
    text = ((title or "") + " " + (description or "")).lower()
    title_lower = (title or "").lower()
    best_id, best_score = "generic", 0
    for cid, data in CATEGORY_SCHEMA.items():
        score = 0
        for kw in data.get("keywords", []):
            if not kw: continue
            kw_lower = kw.lower()
            if re.search(_wb(kw_lower), text):
                score += len(kw) * (3.0 if re.search(_wb(kw_lower), title_lower) else 1.0)
                continue
            tokens = [t for t in kw_lower.split() if any(c.isalpha() for c in t)]
            if not tokens: continue
            matched = 0
            for t in tokens:
                if len(t) <= 2: continue
                if re.search(_wb(t), title_lower): matched += 1; continue
                if re.search(_wb(t), text): matched += 1; continue
                root = re.sub(r"(s|es|ed|ing|ment|ness|ion|er|or|ly)$", "", t)
                if len(root) >= 3 and re.search(_wb(root), title_lower): matched += 1
                elif len(root) >= 3 and re.search(_wb(root), text): matched += 1
            if matched >= max(1, len(tokens) * 0.5):
                score += len(kw) * (matched / len(tokens))
        if score > best_score: best_score, best_id = score, cid
    return best_id

def get_required_attributes(cat):
    return CATEGORY_SCHEMA.get(cat, {}).get("attributes", {}).get("required", [])

def get_optional_attributes(cat):
    return CATEGORY_SCHEMA.get(cat, {}).get("attributes", {}).get("optional", [])

def get_specs_for_category(cat):
    a = CATEGORY_SCHEMA.get(cat, {}).get("attributes", {})
    return a.get("required", []) + a.get("optional", [])

def get_hsn_for_category(cat):
    return CATEGORY_SCHEMA.get(cat, {}).get("hsn", [])

def get_label_for_category(cat):
    return CATEGORY_SCHEMA.get(cat, {}).get("label", "")

def get_unit_variation(cat):
    return CATEGORY_SCHEMA.get(cat, {}).get("unit", "optional")

def get_value_set(cat, attr):
    schema = CATEGORY_SCHEMA.get(cat, {})
    vs = schema.get("attributes", {}).get("value_sets", {})
    return vs.get(attr)

def build_clip_taxonomy():
    return [(cid, d.get("label", "")) for cid, d in CATEGORY_SCHEMA.items()]

def classify_category(title, description, image_sources=None):
    text = ((title or "") + " " + (description or "")).lower()
    title_lower = (title or "").lower()
    best_id, best_score, best_info = "generic", 0, {}
    for cid, data in CATEGORY_SCHEMA.items():
        kws = data.get("keywords", [])
        if not kws: continue
        total_tokens = 0
        matched_tokens = 0
        for kw in kws:
            tokens = [t for t in kw.lower().split() if any(c.isalpha() for c in t)]
            if not tokens: continue
            total_tokens += len(tokens)
            for t in tokens:
                if len(t) <= 2: continue  # skip filler tokens ('of', 'for', 'to', ...)
                # Word-boundary match — 'top' must not match inside 'stopper',
                # 'mat' must not match inside 'mattress'.
                if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", title_lower):
                    matched_tokens += 1
                    continue
                if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text):
                    matched_tokens += 1
                    continue
                root = re.sub(r"(s|es|ed|ing|ment|ness|ion|er|or|ly)$", "", t)
                if len(root) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(root)}(?![a-z0-9])", title_lower):
                    matched_tokens += 1
                    continue
                if len(root) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(root)}(?![a-z0-9])", text):
                    matched_tokens += 1
        if total_tokens > 0:
            ratio = matched_tokens / total_tokens
            score = ratio * len(kws)
            if score > best_score:
                best_score = score
                best_id = cid
                best_info = {"ratio": round(ratio, 3)}
    if best_id == "generic":
        return "generic", 0.3, {}
    ratio = best_info.get("ratio", 0)
    conf = min(0.90, ratio * 1.1 + 0.15) if ratio > 0 else 0.3
    return best_id, round(conf, 4), best_info

def get_confidence_tier(confidence):
    if confidence >= CONFIDENCE_HIGH: return "high"
    if confidence >= CONFIDENCE_MEDIUM: return "medium"
    return "low"
