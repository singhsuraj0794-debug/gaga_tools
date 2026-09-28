# Scraped categories → Gajab L1–L4 (review)

Fill the **Corrected Gajab path** column, then copy accepted rows into
`marketplace_category_map.json`. Only curated entries are used at runtime;
unmapped categories leave `Category Name *` blank in the export and the row
is flagged.

## 1. Mapped (proposal is plausible — verify/adjust)

| platform | scraped category | seen | proposed Gajab path | score | corrected Gajab path |
|---|---|---:|---|---:|---|
| meesho | Unisex Personal Care > Unisex Fragrances & Deodorants > Unisex Perfumes | 1 | Beauty & Health Care > Health & Beauty > Fragrances > Perfumes | 0.97 | |
| amazon | Health & Personal Care > Household Supplies > Household Cleaners > Drain Openers | 1 | Home & Kitchen > Household Care & Supplies > Home Care > Drain Opener | 0.94 | |
| meesho | Unisex Personal Care > Face Care > Face Wash | 1 | Beauty & Health Care > Health & Beauty > Bath and Spa > Face Washes | 0.79 | |
| meesho | Mens Personal Care & Grooming > Men Face Care > Men'S Face Wash | 1 | Beauty & Health Care > Health & Beauty > Bath and Spa > Face Washes | 0.79 | |
| meesho | Home & Kitchen > Kitchen & Dining > Baking Tools | 1 | Home & Kitchen > Kitchen Accessories > Kitchen Tools > Kitchen Tool Set | 0.74 | |
| amazon | Health & Personal Care > Household Supplies > Household Cleaners > All-Purpose Cleaners | 1 | Home & Kitchen > Household Care & Supplies > Housekeeping & Laundry > All Purpose Cleaner | 0.73 | |
| meesho | Home & Kitchen > Kitchen Storage & Organisers > Jars & Containers | 1 | Home & Kitchen > Household Care & Supplies > Home Organizers & Storage > Container | 0.73 | |
| amazon | Health & Personal Care > Household Supplies > Household Cleaners > Furniture & Wood Polishes | 1 | Beauty & Health Care > Health & Beauty > Face Care > Nail Polish | 0.50 | |

## 2. Taxonomy gaps / no adequate Gajab category

These scraped categories have **no matching row in All Categories** (or only a
poor one). Extend the master taxonomy, or mark them out of scope. Until then
they export with `Category Name *` blank and a flag.

| platform | scraped category | seen | best candidate | score | decision |
|---|---|---:|---|---:|---|
| amazon | Health & Personal Care > Household Supplies > Indoor Insect & Pest Control > Sprays | 3 | Kids & Baby > Party Supplies > Party Supplies > Snow Spray | 0.44 | |
| flipkart | Mobiles & Accessories > Mobile Accessories > Cases & Covers > Plain Cases & Covers > KWINE CASE Plain Cases & Covers | 1 | Home & Kitchen > Kitchen Accessories > Appliance Accessories > Appliance Cover | 0.32 | |
| amazon | Outdoor Living > Pest Control > Rodent Control | 2 | Electronics > Cameras and Accessories > Camera Accessories > Camera Remote Control | 0.26 | |

## 3. Alternatives considered

- **amazon · Health & Personal Care > Household Supplies > Indoor Insect & Pest Control > Sprays**
    - `Home & Kitchen > Home Improvement Tools > Paint Equipments & Supplies > Spray Paints` (0.36)
    - `Fashion > Footwear & Accessories > Footwear Accessories > Shoe Spray` (0.30)
- **amazon · Outdoor Living > Pest Control > Rodent Control**
    - `Electronics > Cameras and Accessories > Camera Accessories > Camera Remote Control` (0.21)
    - `Automobile Accessories > Spares > Car Spare Parts > Control Arm Bushing` (0.20)
- **amazon · Health & Personal Care > Household Supplies > Household Cleaners > Furniture & Wood Polishes**
    - `Beauty & Health Care > Health & Beauty > Face Care > Nail Polish Dryer` (0.40)
    - `Furniture > Home and Office Furniture > Home Furniture > Furniture Accessories` (0.31)
- **amazon · Health & Personal Care > Household Supplies > Household Cleaners > Drain Openers**
    - `Stationery > Office, School and College Supplies > Office Supplies & Accessories > Letter Opener` (0.41)
    - `Home & Kitchen > Kitchen Accessories > Bar & Glassware > Bottle Opener` (0.30)
- **amazon · Health & Personal Care > Household Supplies > Household Cleaners > All-Purpose Cleaners**
    - `Home & Kitchen > Household Care & Supplies > Home Care > Glass Cleaner` (0.64)
    - `Home & Kitchen > Household Care & Supplies > Home Care > Toilet Cleaner` (0.64)
- **flipkart · Mobiles & Accessories > Mobile Accessories > Cases & Covers > Plain Cases & Covers > KWINE CASE Plain Cases & Covers**
    - `Home & Kitchen > Kitchen Accessories > Kitchen & Table Linen > Roti Cover` (0.29)
    - `Electronics > Computer - Accessories and Components > Computer Accessories > Printer Cover` (0.29)
- **meesho · Home & Kitchen > Kitchen Storage & Organisers > Jars & Containers**
    - `Home & Kitchen > Home Improvement Tools > Gardening Tools > Plant Container Set` (0.26)
    - `Home & Kitchen > Kitchen Accessories > Kitchen Tools > Storage Pouches` (0.20)
- **meesho · Home & Kitchen > Kitchen & Dining > Baking Tools**
    - `Home & Kitchen > Home Improvement Tools > Tool Accessories > Tool Belt` (0.59)
    - `Home & Kitchen > Home Improvement Tools > Tool Accessories > Tool Cabinet` (0.59)
- **meesho · Unisex Personal Care > Face Care > Face Wash**
    - `Beauty & Health Care > Health & Beauty > Bath and Spa > Body Wash` (0.50)
    - `Beauty & Health Care > Health & Beauty > Skin Care > Hand Wash and Hand Sanitizer` (0.49)
- **meesho · Unisex Personal Care > Unisex Fragrances & Deodorants > Unisex Perfumes**
    - `Fashion > Leather & Travel Products > Travel Accessories > Perfume Bottles` (0.30)
    - `Fashion > Leather & Travel Products > Travel Accessories > Perfume Spray Nozzle` (0.20)
- **meesho · Mens Personal Care & Grooming > Men Face Care > Men'S Face Wash**
    - `Beauty & Health Care > Health & Beauty > Bath and Spa > Body Wash` (0.50)
    - `Beauty & Health Care > Health & Beauty > Skin Care > Hand Wash and Hand Sanitizer` (0.49)
