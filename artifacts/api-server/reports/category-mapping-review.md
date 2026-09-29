# Scraped categories → Gajab L1–L4 (review)

Fill the **Corrected Gajab path** column, then copy accepted rows into
`marketplace_category_map.json`. Only curated entries are used at runtime;
unmapped categories leave `Category Name *` blank in the export and the row
is flagged.

## 1. Mapped (proposal is plausible — verify/adjust)

| platform | scraped category | seen | proposed Gajab path | score | corrected Gajab path |
|---|---|---:|---|---:|---|
| amazon | Home & Kitchen > Kitchen & Home Appliances > Small Kitchen Appliances > Juicer Mixer Grinders | 1 | Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Mixers, Grinders & Juicers | 1.00 | |
| amazon | Home & Kitchen > Kitchen & Home Appliances > Small Kitchen Appliances > Hand Blenders | 1 | Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Hand Blender | 1.00 | |
| meesho | Unisex Personal Care > Unisex Fragrances & Deodorants > Unisex Perfumes | 1 | Beauty & Health Care > Health & Beauty > Fragrances > Perfumes | 0.97 | |
| amazon | Health & Personal Care > Household Supplies > Household Cleaners > Drain Openers | 1 | Home & Kitchen > Household Care & Supplies > Home Care > Drain Opener | 0.94 | |
| amazon | Home & Kitchen > Home & Décor > Clocks > Wall Clocks | 1 | Home & Kitchen > Home Decor > Clocks & Wall Decor > Wall Clocks | 0.80 | |
| meesho | Unisex Personal Care > Face Care > Face Wash | 1 | Beauty & Health Care > Health & Beauty > Bath and Spa > Face Washes | 0.79 | |
| meesho | Mens Personal Care & Grooming > Men Face Care > Men'S Face Wash | 1 | Beauty & Health Care > Health & Beauty > Bath and Spa > Face Washes | 0.79 | |
| meesho | Home & Kitchen > Kitchen & Dining > Baking Tools | 1 | Home & Kitchen > Kitchen Accessories > Kitchen Tools > Kitchen Tool Set | 0.74 | |
| amazon | Health & Personal Care > Household Supplies > Household Cleaners > All-Purpose Cleaners | 1 | Home & Kitchen > Household Care & Supplies > Housekeeping & Laundry > All Purpose Cleaner | 0.73 | |
| meesho | Home & Kitchen > Kitchen Storage & Organisers > Jars & Containers | 1 | Home & Kitchen > Household Care & Supplies > Home Organizers & Storage > Container | 0.73 | |
| amazon | Home & Kitchen > Kitchen & Dining > Kitchen Storage & Containers > Jars & Containers | 1 | Home & Kitchen > Household Care & Supplies > Home Organizers & Storage > Container | 0.73 | |
| amazon | Beauty > Skin Care > Face > Cleansing Creams & Milks > Face Wash | 1 | Beauty & Health Care > Health & Beauty > Bath and Spa > Face Washes | 0.70 | |
| amazon | Bags, Wallets and Luggage > Luggage > Suitcases & Trolley Bags | 1 | Fashion > Leather & Travel Products > Bags & Fashion Accessories > Bag | 0.67 | |
| amazon | Sports, Fitness & Outdoors > Exercise & Fitness > Strength Training Equipment > Strength Training Sets | 1 | Furniture > Home and Office Furniture > Outdoor & Cafeteria Furniture > Outdoor Set | 0.65 | |
| amazon | Home & Kitchen > Home Furnishing > Cushions & Cushion Covers > Cushion Covers | 1 | Home & Kitchen > Home Furnishing > Living Room Furnishing > Cushion Pillow Cover | 0.65 | |
| amazon | Home & Kitchen > Home & Décor > Decorative Accessories > Showpieces & Collectibles > Figurines | 1 | Home & Kitchen > Home Decor > Clocks & Wall Decor > Showpiece Figurine | 0.65 | |
| amazon | Home & Kitchen > Kitchen & Dining > Kitchen Storage & Containers > Water Bottles | 1 | Stationery > Office, School and College Supplies > School Supplies > Water Bottles | 0.58 | |
| amazon | Computers & Accessories > Accessories & Peripherals > Laptop Accessories > Bags & Sleeves > Laptop Backpacks | 1 | Fashion > Leather & Travel Products > Luggage & Travel > Backpacks | 0.57 | |
| amazon | Electronics > Mobiles & Accessories > Mobile Accessories > Stands | 1 | Electronics > Mobile, Tablets and Accessories > Mobile & Tablet Accessories > Headphone Stand | 0.53 | |
| amazon | Health & Personal Care > Household Supplies > Household Cleaners > Furniture & Wood Polishes | 1 | Beauty & Health Care > Health & Beauty > Face Care > Nail Polish | 0.50 | |
| amazon | Industrial & Scientific > Robotics > Robotic Kits & Accessories | 1 | Electronics > Gaming > Accessory kits > Gaming Accessory Kits | 0.49 | |

## 2. Taxonomy gaps / no adequate Gajab category

These scraped categories have **no matching row in All Categories** (or only a
poor one). Extend the master taxonomy, or mark them out of scope. Until then
they export with `Category Name *` blank and a flag.

| platform | scraped category | seen | best candidate | score | decision |
|---|---|---:|---|---:|---|
| amazon | Bags, Wallets and Luggage > Luggage > Luggage Sets | 1 | Home & Kitchen > Kitchen Accessories > Cuttlery and Dinning Sets > Dinner Sets | 0.35 | |
| amazon | Electronics > Mobiles & Accessories > Mobile Accessories > Automobile Accessories > Cradles | 1 | Stationery > Office, School and College Supplies > Laboratory Equipments > Newton's Cradles | 0.29 | |
| amazon | Health & Personal Care > Household Supplies > Indoor Insect & Pest Control > Sprays | 1 | Kids & Baby > Party Supplies > Party Supplies > Snow Spray | 0.44 | |
| amazon | Home & Kitchen > Home & Décor > Decorative Accessories > Idols & Figurines | 1 | Home & Kitchen > Home Decor > Clocks & Wall Decor > Showpiece Figurine | 0.35 | |
| amazon | Home & Kitchen > Indoor Lighting > Specialty Lighting > LED Strips | 1 | Home & Kitchen > Household Care & Supplies > Housekeeping & Laundry > Brush Strip | 0.35 | |
| amazon | Home Improvement > Lighting Fixtures > Outdoor Lighting | 1 | Home & Kitchen > Home Improvement Tools > Light Fixtures > Tube Light Fixtures | 0.37 | |
| flipkart | Mobiles & Accessories > Mobile Accessories > Cases & Covers > Plain Cases & Covers > KWINE CASE Plain Cases & Covers | 1 | Home & Kitchen > Kitchen Accessories > Appliance Accessories > Appliance Cover | 0.32 | |
| amazon | Office Products > Office Supplies > Desk Accessories & Storage Products > Desk Supplies, Organisers & Dispensers > Desk Supplies Organisers | 1 | Stationery > Office, School and College Supplies > Office Supplies & Accessories > Desk Organizers | 0.20 | |
| amazon | Outdoor Living > Pest Control > Rodent Control | 1 | Electronics > Cameras and Accessories > Camera Accessories > Camera Remote Control | 0.26 | |
| amazon | Pet Supplies > Dogs > Grooming > Brushes | 1 | Stationery > Pens, Stationery & Hobby Materials > Art Supplies > Paint Brushes | 0.38 | |

## 3. Alternatives considered

- **meesho · Home & Kitchen > Kitchen Storage & Organisers > Jars & Containers**
    - `Home & Kitchen > Home Improvement Tools > Gardening Tools > Plant Container Set` (0.26)
    - `Home & Kitchen > Kitchen Accessories > Kitchen Tools > Storage Pouches` (0.20)
- **meesho · Home & Kitchen > Kitchen & Dining > Baking Tools**
    - `Home & Kitchen > Home Improvement Tools > Tool Accessories > Tool Belt` (0.59)
    - `Home & Kitchen > Home Improvement Tools > Tool Accessories > Tool Cabinet` (0.59)
- **meesho · Unisex Personal Care > Face Care > Face Wash**
    - `Beauty & Health Care > Health & Beauty > Bath and Spa > Body Wash` (0.50)
    - `Beauty & Health Care > Health & Beauty > Skin Care > Hand Wash and Hand Sanitizer` (0.49)
- **amazon · Home & Kitchen > Kitchen & Dining > Kitchen Storage & Containers > Jars & Containers**
    - `Home & Kitchen > Home Improvement Tools > Gardening Tools > Plant Container Set` (0.26)
    - `Home & Kitchen > Kitchen Accessories > Kitchen Tools > Storage Pouches` (0.20)
- **amazon · Industrial & Scientific > Robotics > Robotic Kits & Accessories**
    - `Electronics > Computer - Accessories and Components > Computer Accessories > Cleaning Kit` (0.29)
    - `Home & Kitchen > Home Decor > Festival Supplies > Prayer Kit` (0.29)
- **amazon · Beauty > Skin Care > Face > Cleansing Creams & Milks > Face Wash**
    - `Beauty & Health Care > Health & Beauty > Skin Care > Hand Wash and Hand Sanitizer` (0.51)
    - `Beauty & Health Care > Health & Beauty > Bath and Spa > Body Wash` (0.41)
- **amazon · Home & Kitchen > Home & Décor > Decorative Accessories > Showpieces & Collectibles > Figurines**
    - `Stationery > Pens, Stationery & Hobby Materials > Art Supplies > Unpainted Doll Figurine` (0.20)
    - `Home & Kitchen > Kitchen Accessories > Kitchen Tools > Kitchen Knives` (0.17)
- **amazon · Home & Kitchen > Home & Décor > Decorative Accessories > Idols & Figurines**
    - `Stationery > Pens, Stationery & Hobby Materials > Art Supplies > Unpainted Doll Figurine` (0.20)
    - `Home & Kitchen > Kitchen Accessories > Kitchen Tools > Kitchen Knives` (0.17)
- **amazon · Office Products > Office Supplies > Desk Accessories & Storage Products > Desk Supplies, Organisers & Dispensers > Desk Supplies Organisers**
    - `Stationery > Office, School and College Supplies > Office Supplies & Accessories > Desk Organizers` (0.19)
    - `Stationery > Office, School and College Supplies > Office Supplies & Accessories > Office Sets` (0.19)
- **amazon · Sports, Fitness & Outdoors > Exercise & Fitness > Strength Training Equipment > Strength Training Sets**
    - `Home & Kitchen > Kitchen Accessories > Cuttlery and Dinning Sets > Dinner Sets` (0.35)
    - `Home & Kitchen > Kitchen Accessories > Cookware > Cookware Set` (0.31)
- **amazon · Electronics > Mobiles & Accessories > Mobile Accessories > Automobile Accessories > Cradles**
    - `Stationery > Office, School and College Supplies > Laboratory Equipments > Newton's Cradles` (0.19)
    - `Electronics > Mobile, Tablets and Accessories > Mobile & Tablet Accessories > Mobile Skins` (0.18)
- **amazon · Electronics > Mobiles & Accessories > Mobile Accessories > Stands**
    - `Electronics > Computer - Accessories and Components > Laptop Accessories > Laptop Stand` (0.36)
    - `Toys & General Merchandise > Musical Instruments > Musical Instruments Accessories > Musical Stand` (0.31)
- **amazon · Pet Supplies > Dogs > Grooming > Brushes**
    - `Beauty & Health Care > Health & Beauty > Personal Care & Grooming > Shaving Brush` (0.37)
    - `Home & Kitchen > Home Improvement Tools > Building & Construction Supplies > Door Brush` (0.35)
- **amazon · Home & Kitchen > Kitchen & Home Appliances > Small Kitchen Appliances > Juicer Mixer Grinders**
    - `Home & Kitchen > Kitchen Accessories > Appliance Accessories > Mixer Grinder Couplers` (0.79)
    - `Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Nutmeg Grinders` (0.70)
- **amazon · Home & Kitchen > Kitchen & Home Appliances > Small Kitchen Appliances > Hand Blenders**
    - `Home & Kitchen > Kitchen Accessories > Appliance Accessories > Mixer Blender Blades` (0.59)
    - `Home & Kitchen > Home & Kitchen Appliances > Kitchen Appliances > Hand Juicer` (0.24)
- **amazon · Home & Kitchen > Home & Décor > Clocks > Wall Clocks**
    - `Home & Kitchen > Home Decor > Clocks & Wall Decor > Table Clocks` (0.51)
    - `Home & Kitchen > Home Decor > Clocks & Wall Decor > Sand Clock` (0.50)
- **amazon · Bags, Wallets and Luggage > Luggage > Luggage Sets**
    - `Home & Kitchen > Kitchen Accessories > Cookware > Cookware Set` (0.31)
    - `Stationery > Office, School and College Supplies > Office Supplies & Accessories > Office Sets` (0.31)
- **amazon · Bags, Wallets and Luggage > Luggage > Suitcases & Trolley Bags**
    - `Electronics > Computer - Accessories and Components > Laptop Accessories > Bags` (0.57)
    - `Fashion > Leather & Travel Products > Luggage & Travel > Duffel Bag` (0.40)
- **amazon · Home & Kitchen > Kitchen & Dining > Kitchen Storage & Containers > Water Bottles**
    - `Fashion > Leather & Travel Products > Luggage & Travel > Bottles` (0.57)
    - `Home & Kitchen > Kitchen Accessories > Bar & Glassware > Bottle Racks` (0.48)
- **amazon · Home & Kitchen > Home Furnishing > Cushions & Cushion Covers > Cushion Covers**
    - `Home & Kitchen > Home Furnishing > Living Room Furnishing > Table Cover` (0.55)
    - `Home & Kitchen > Kitchen Accessories > Kitchen & Table Linen > Roti Cover` (0.52)
- **amazon · Home Improvement > Lighting Fixtures > Outdoor Lighting**
    - `Electronics > Smart Devices > Automation & Robotics > Smart Lighting` (0.29)
    - `Furniture > Home and Office Furniture > Outdoor & Cafeteria Furniture > Outdoor Set` (0.13)
- **amazon · Home & Kitchen > Indoor Lighting > Specialty Lighting > LED Strips**
    - `Beauty & Health Care > Health & Beauty > Health Care Devices > Glucometer Strip` (0.29)
    - `Home & Kitchen > Home Improvement Tools > Building & Construction Supplies > Eave & Rake Starter Strips` (0.21)
- **amazon · Computers & Accessories > Accessories & Peripherals > Laptop Accessories > Bags & Sleeves > Laptop Backpacks**
    - `Electronics > Computer - Accessories and Components > Laptop Accessories > Bags` (0.30)
    - `Electronics > Computer - Accessories and Components > Laptops > Computers` (0.29)
- **amazon · Health & Personal Care > Household Supplies > Indoor Insect & Pest Control > Sprays**
    - `Home & Kitchen > Home Improvement Tools > Paint Equipments & Supplies > Spray Paints` (0.36)
    - `Fashion > Footwear & Accessories > Footwear Accessories > Shoe Spray` (0.30)
- **amazon · Health & Personal Care > Household Supplies > Household Cleaners > Furniture & Wood Polishes**
    - `Beauty & Health Care > Health & Beauty > Face Care > Nail Polish Dryer` (0.40)
    - `Furniture > Home and Office Furniture > Home Furniture > Furniture Accessories` (0.31)
- **amazon · Health & Personal Care > Household Supplies > Household Cleaners > Drain Openers**
    - `Stationery > Office, School and College Supplies > Office Supplies & Accessories > Letter Opener` (0.41)
    - `Home & Kitchen > Kitchen Accessories > Bar & Glassware > Bottle Opener` (0.30)
- **amazon · Outdoor Living > Pest Control > Rodent Control**
    - `Electronics > Cameras and Accessories > Camera Accessories > Camera Remote Control` (0.21)
    - `Automobile Accessories > Spares > Car Spare Parts > Control Arm Bushing` (0.20)
- **amazon · Health & Personal Care > Household Supplies > Household Cleaners > All-Purpose Cleaners**
    - `Home & Kitchen > Household Care & Supplies > Home Care > Glass Cleaner` (0.64)
    - `Home & Kitchen > Household Care & Supplies > Home Care > Toilet Cleaner` (0.64)
- **flipkart · Mobiles & Accessories > Mobile Accessories > Cases & Covers > Plain Cases & Covers > KWINE CASE Plain Cases & Covers**
    - `Home & Kitchen > Kitchen Accessories > Kitchen & Table Linen > Roti Cover` (0.29)
    - `Electronics > Computer - Accessories and Components > Computer Accessories > Printer Cover` (0.29)
- **meesho · Unisex Personal Care > Unisex Fragrances & Deodorants > Unisex Perfumes**
    - `Fashion > Leather & Travel Products > Travel Accessories > Perfume Bottles` (0.30)
    - `Fashion > Leather & Travel Products > Travel Accessories > Perfume Spray Nozzle` (0.20)
- **meesho · Mens Personal Care & Grooming > Men Face Care > Men'S Face Wash**
    - `Beauty & Health Care > Health & Beauty > Bath and Spa > Body Wash` (0.50)
    - `Beauty & Health Care > Health & Beauty > Skin Care > Hand Wash and Hand Sanitizer` (0.49)
