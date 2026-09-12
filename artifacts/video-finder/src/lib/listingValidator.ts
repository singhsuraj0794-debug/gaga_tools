import * as XLSX from "xlsx";
import Papa from "papaparse";

// Base URL for the pre-listing validator compute API (HSN, CLIP, text correction).
// Defaults to the build-time env var, then localhost for local dev. The app can
// override it at runtime via setPrelistingApiBase() (see PreListingValidator.tsx),
// so a changing Cloudflare Tunnel URL doesn't require a rebuild.
const DEFAULT_API_BASE: string =
  (import.meta.env?.VITE_PRELISTING_API_URL as string | undefined) || "http://localhost:8080";

let _prelistingApiBase: string = DEFAULT_API_BASE;

export function getPrelistingApiBase(): string {
  return _prelistingApiBase;
}

export function setPrelistingApiBase(url: string): void {
  const trimmed = (url || "").trim().replace(/\/+$/, "");
  if (/^https?:\/\//i.test(trimmed)) {
    _prelistingApiBase = trimmed;
  }
}

export { PRELISTING_API_BASE as _deprecatedPrelistingApiBase };

// Re-export the constant name for backwards compatibility, but compute calls
// should use getPrelistingApiBase() so runtime overrides take effect.
export const PRELISTING_API_BASE: string = DEFAULT_API_BASE;

export type Decision = "PASS" | "FLAG" | "REJECT";

export interface CheckResult {
  field: string;
  check: string;
  passed: boolean;
  decision: Decision;
  message: string;
}

export interface ImageCheckResult {
  url: string;
  loaded: boolean;
  width: number;
  height: number;
  isDuplicate: boolean;
  phash?: string;
  isMarked?: boolean;
  hasContentFlag?: boolean;
  hasOpsClaim?: boolean;
  ocrText?: string;
}

export interface ValidationResult {
  sku: string;
  productName: string;
  category: string;
  decision: Decision;
  score: number;
  checks: CheckResult[];
  imageChecks?: ImageCheckResult[];
  titleSuggestion?: string;
  descSuggestion?: string;
}

/**
 * Build a compacted image-URL list per SKU by removing perceptual duplicates
 * and identical URLs within each product. Remaining URLs are shifted left so
 * there are no gaps. Returns a Map<sku, imageUrls[]> for patching the sheet.
 */
export function buildImageCorrections(results: ValidationResult[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const res of results) {
    if (!res.imageChecks || res.imageChecks.length === 0) continue;
    const loaded = res.imageChecks.filter((ic) => ic.loaded);
    if (loaded.length === 0) continue;
    // Only remove duplicates when product has more than 4 images — keep minimum 4
    if (loaded.length <= 4) continue;
    const hasDup = res.imageChecks.some((ic) => ic.isDuplicate);
    const seen = new Set<string>();
    const corrected: string[] = [];
    let seenDup = false;
    for (const ic of res.imageChecks) {
      if (!ic.loaded || ic.isDuplicate) continue;
      const url = ic.url.trim();
      if (seen.has(url)) { seenDup = true; continue; }
      seen.add(url);
      corrected.push(url);
    }
    // Only emit when compaction actually removes something — otherwise
    // exportCorrectedSheet would blank all image cells for products where
    // validation didn't load images or had no duplicates.
    const needsCompaction = hasDup || seenDup || corrected.length !== loaded.length;
    if (!needsCompaction) continue;
    out.set(res.sku, corrected);
  }
  return out;
}

/**
 * Build per-SKU review feedback for the Feedback column. Only includes
 * items that are currently flagged or rejected — dismissed and passed
 * checks are excluded so the column is clean and actionable.
 */
export function buildFeedback(
  results: ValidationResult[],
  _dismissedChecks?: Map<string, Set<number>>,
): Map<string, string> {
  const out = new Map<string, string>();
  for (const res of results) {
    const parts: string[] = [];

    // ── Only actual non-passing checks (rejected / flagged)
    for (const c of res.checks) {
      if (c.passed) continue;        // passed or dismissed — skip
      if (c.decision === "WARN") continue; // warn-only — skip
      parts.push(`${c.field}: ${c.message}`);
    }

    // ── Per-image detail (duplicates, ops claims, content flags, markings)
    if (res.imageChecks && res.imageChecks.length > 0) {
      for (let idx = 0; idx < res.imageChecks.length; idx++) {
        const ic = res.imageChecks[idx];
        const label = `Image ${idx + 1}`;
        if (!ic.loaded) continue; // already captured as a check above
        const issues: string[] = [];
        if (ic.isDuplicate) {
          const loadedCount = res.imageChecks.filter((x) => x.loaded).length;
          if (loadedCount > 4) {
            issues.push("Duplicate — removed on export");
          } else {
            issues.push("Duplicate — kept (≤4 images)");
          }
        }
        if (ic.hasOpsClaim) issues.push(`Ops/marketing claim${ic.ocrText ? ` "${ic.ocrText.trim().slice(0, 80)}"` : ""}`);
        if (ic.hasContentFlag) issues.push("Prohibited content flag");
        if (ic.isMarked) issues.push("Promo/marking overlay");
        if (issues.length > 0) {
          const short = (ic.url || "").split("/").pop()?.slice(0, 22) || "";
          parts.push(`${label}${short ? ` (${short})` : ""}: ${issues.join(", ")}`);
        }
      }
      // Distinct-image summary after dedup
      const loadedCount = res.imageChecks.filter((ic) => ic.loaded).length;
      const distinct = res.imageChecks.filter((ic) => ic.loaded && !ic.isDuplicate).length;
      const dupRemoved = loadedCount > 4 && res.imageChecks.some((ic) => ic.isDuplicate);
      if (dupRemoved && distinct < 4 && !parts.some((p) => p.includes("distinct"))) {
        parts.push(`Only ${distinct} distinct image${distinct === 1 ? "" : "s"} after dedup (min 4 required)`);
      }
    }

    if (parts.length > 0) out.set(res.sku, parts.join(" | "));
  }
  return out;
}

/**
 * Export a minimal "SKU sheet" listing ONLY the SKUs of products to be
 * deleted (removed duplicates). No kept-product SKUs are included. This is
 * meant to be fed into a marketplace's bulk-delete tool.
 */
export function exportSkuSheet(skus: Iterable<string>): void {
  const unique = [...new Set([...skus].map((s) => String(s ?? "").trim()))].filter(Boolean);
  const aoa: unknown[][] = [["Sku"], ...unique.map((s) => [s])];
  const ws = XLSX.utils.aoa_to_sheet(aoa);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "SKUs");
  XLSX.writeFile(wb, "sku-sheet.xlsx");
  return;
}

export interface ListingRow {
  [key: string]: unknown;
}

const MANDATORY_FIELDS = [
  "Sku *",
  "Listing Type *",
  "Category Name *",
  "Brand Name *",
  "Product Name *",
  "Description *",
  "Hsn *",
  "Tax *",
  "Handling Time *",
  "Quantity *",
  "Package Weight *",
  "Package Height *",
  "Package Width *",
  "Package Length *",
  "Relationship *",
  "Parent Sku *",
  "Mrp Price *",
  "Transfer Price *",
  "Product Image 1 *",
];

const IMAGE_FIELDS = Array.from({ length: 10 }, (_, i) => `Product Image ${i + 1}${i === 0 ? " *" : ""}`);

const CATEGORY_COMPLIANCE_RULES: Record<number, { name: string; checks: Array<{ type: string; required: boolean }> }> = {
  // Home & Garden subcategories — no BIS/FSSAI/ISI required per Gajab guidelines
  35: { name: "Bathroom Accessories", checks: [] },
  36: { name: "Household Supplies", checks: [] },
  37: { name: "Kitchen Utilities", checks: [] },
};

const NO_COMPLIANCE_CATEGORIES = new Set(["Bathroom Accessories", "Household Supplies", "Kitchen Utilities"]);

export function parseCategoryName(raw: string): { id: number | null; path: string } {
  const match = raw?.match(/^#(\d+)\|\s*(.+)/);
  if (match) {
    return { id: parseInt(match[1], 10), path: match[2].trim() };
  }
  if (raw?.startsWith("#")) {
    const idMatch = raw.match(/^#(\d+)/);
    if (idMatch) {
      const id = parseInt(idMatch[1], 10);
      const rest = raw.replace(/^#\d+\|\s*/, "").trim();
      return { id, path: rest || raw };
    }
  }
  return { id: null, path: raw || "" };
}

/** Strip a leading "#123|" or "#123 |" prefix from a field value. */
export function stripIdPrefix(value: string): string {
  return value.replace(/^#\d+\s*\|\s*/, "").trim();
}

export function stripHtml(html: string): string {
  if (!html) return "";
  return html.replace(/<[^>]*>/g, "").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
}

function normalizeFieldName(name: string): string {
  return name
    .replace(/[*＊]/g, "")
    .replace(/\u00A0/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function normalizeSkuValue(value: unknown): string {
  return String(value ?? "").trim().toUpperCase();
}

function getField(row: ListingRow, fieldName: string): unknown {
  if (fieldName in row) return row[fieldName];
  const normalized = normalizeFieldName(fieldName);
  for (const key of Object.keys(row)) {
    if (normalizeFieldName(key) === normalized) {
      return row[key];
    }
  }
  return undefined;
}

function hasColumn(row: ListingRow, fieldName: string): boolean {
  if (fieldName in row) return true;
  const normalized = normalizeFieldName(fieldName);
  return Object.keys(row).some((key) => normalizeFieldName(key) === normalized);
}

function isEmpty(val: unknown): boolean {
  if (val === null || val === undefined) return true;
  if (typeof val === "number" && isNaN(val)) return true;
  const s = String(val).trim();
  return s === "" || s === "null" || s === "undefined" || s === "NaN";
}

function str(val: unknown): string {
  return String(val ?? "").trim();
}

// ─── Main validation engine ─────────────────────────────────────────

/**
 * Decide whether a product requires a unit count / size / color variation in
 * its title. Reads both the title and the description to understand what the
 * product is:
 *  - Apparel / footwear / textiles / consumables / sets → required.
 *  - Single-SKU articles (cables, chargers, appliances, containers, tools,
 *    hardware, sanitary articles, etc.) → exempt.
 */
const SINGLE_ITEM_MARKERS = [
  "cable", "wire", "cord", "charger", "adapter", "plug", "earphone", "headphone",
  "earbud", "power bank", "powerbank", "sim", "memory card", "battery", "bulb",
  "lamp", "switch", "socket", "appliance", "television", "tv", "fridge",
  "refrigerator", "washing machine", "mixer", "juicer", "grinder", "iron",
  "kettle", "toaster", "microwave", "induction", "vacuum", "fan ", "water purifier",
  "filter", "ro system", "bottle", "holder", "dispenser", "sprayer", "mister",
  "stand", "mount", "bracket", "cover", "case", "frame", "glass", "mirror",
  "tumbler", "mug", "cup", "bowl", "plate", "kitchen article", "utensil",
  "spoon", "fork", "ladle", "knife", "blade", "chopper", "grater", "peeler",
  "toothbrush holder", "soap dish", "soap tray", "lunch box", "lunchbox",
  "tiffin", "container", "jar", "box ", "tool", "wrench", "hammer", "screwdriver",
  "drill", "pliers", "motor", "pump", "valve", "pipe", "lens", "sensor",
  "watch", "eyewear", "sunglass", "spectacle", "umbrella", "helmet", "cycle",
  "bicycle", "mattress", "pillow", "spray", "scent", "diffuser",
];

const VARIATION_REQUIRED_MARKERS = [
  "shirt", "tshirt", "t-shirt", "jeans", "kurta", "kurti", "saree", "sari",
  "dress", "trouser", "pant", "short", "legging", "hoodie", "jacket", "sweater",
  "socks", "apparel", "garment", "footwear", "shoe", "slipper", "sandals",
  "sneaker", "bedsheet", "bed sheet", "towel", "curtain", "pillow cover",
  "cushion cover", "duvet", "table cloth", "soap", "shampoo", "conditioner",
  "toothpaste", "detergent", "soap powder", "washing powder", "candle",
  "toy", "toy set", "stationery", "pen", "pencil", "notebook", "color set",
  "paint set", "art set", "bracelet set", "bangle", "earring", "necklace",
  "ring", "jewellery set", "cookware set", "dinner set", "cutlery set",
  "utensil set", "combo", "pack", "multipack", "assorted",
];

function inferUnitVariationEligibility(
  title: string,
  description: string,
): { exempt: boolean; reason: string } {
  const text = `${title} ${description}`.toLowerCase();
  const singleMatch = SINGLE_ITEM_MARKERS.find((m) => text.includes(m));
  if (singleMatch) {
    return { exempt: true, reason: `single-${singleMatch.trim()} product` };
  }
  const variationMatch = VARIATION_REQUIRED_MARKERS.find((m) => text.includes(m));
  if (variationMatch) {
    return { exempt: false, reason: "" };
  }
  // Description signals multi-unit / multi-color packaging (e.g. "set of",
  // "available in", "colour", "sizes S/M/L").
  if (
    /\b(set|pack|combo|multipack)\b/i.test(description) ||
    /\b(colou?r|colour|shade)\b/i.test(description) ||
    /\b(size|sizes|small|medium|large|xl|xxl)\b/i.test(description)
  ) {
    return { exempt: false, reason: "" };
  }
  // Default: an unknown product type is treated as eligible only if the
  // description talks about a consumer item, not hardware/accessories.
  const hardwareish = /(electronic|electrical|industrial|spare|part|repair|accessory|mount|bracket|fixture)/i.test(description);
  if (hardwareish) {
    return { exempt: true, reason: "hardware/accessory product" };
  }
  return { exempt: false, reason: "" };
}

const CLAIM_EXEMPT_PHRASES = /\b(best\s*friend|bestie|best\s*regards|best\s*wishes|best\s*match|best\s*suited|best\s*for\s*(gift|daily|everyday|outdoor|indoor|home|office|kitchen|travel|party))\b/i;

const CLAIM_EXEMPT_CONTEXT = /\b(design|color|colour|random|assorted|mixed|variant|style|pattern|variety|gift|gifts|birthday|party|favor|favors|return\s*gift|kanjak|giveaway|goodie|goody|classroom|reward|special\s*occasion|group\s*celebrat|festive)\b/i;

const MISLEADING_CLAIMS = [
  "best", "best seller", "bestseller", "best selling", "number 1", "#1", "guaranteed",
  "100% genuine", "100% original", "world's best", "world class", "miracle",
  "instant result", "free gift", "buy 1 get 1", "limited offer", "hurry",
  "premium", "premium quality", "best quality", "top quality", "high quality",
  "hot selling", "top rated", "top rated quality", "exclusive offer",
  // Operations / marketplace claims — these belong in terms, not product copy
  "free exchange", "free return", "free replacement", "free delivery",
  "free shipping", "money back", "cash on delivery", "cod available",
  "no cost emi", "no cost easy emi", "easy emi", "pay on delivery",
  "days return", "day return", "days exchange", "day exchange",
  "days replacement", "day replacement", "days delivery",
  "fast delivery", "express delivery", "same day delivery", "next day delivery",
  "cashback", "price match", "lowest price", "cheapest",
  "lifetime warranty", "lifetime guarantee",
];

/** Operation-claim patterns — operations/marketplace promises that do NOT
 *  describe the product itself. Match with regex since words may not be
 *  contiguous ("30 days free exchange or return"). */
const OPS_PATTERNS: RegExp[] = [
  // Returns / exchanges
  /\d+\s*days?\s+(?:(?:free\s+)?(?:return|exchange|replacement|refund)(?:\s+or\s+(?:free\s+)?(?:return|exchange|replacement|refund))?|or\s+(?:free\s+)?(?:return|exchange|replacement|refund))/i,
  /(easy|hassle\s*free|simple|quick|no\s*questions?\s*asked|seamless)\s*(return|exchange|replacement)/i,
  /return\s*(within|in)\s*\d+/i,
  /(return|exchange|replacement)\s*policy/i,
  /get\s*(a|your)\s*(replacement|refund|money)/i,
  /(free|complimentary)\s*(return|exchange|replacement)/i,
  /replace(ment|ing)?\s*(within|in)\s*\d+/i,

  // Refunds / money
  /(full|100%)\s*refund/i,
  /(\d+%)?\s*(money|refund|cash)\s*back\s*(guarantee|policy|offer)?/i,
  /(money|amount|payment)\s*(back|returned|refunded)/i,
  /no\s*questions?\s*asked\s*(return|refund|exchange)/i,
  /satisfaction\s*(guaranteed|guarantee)/i,

  // Delivery / shipping
  /free\s+(delivery|shipping)/i,
  /(doorstep|home|free|express|fast|quick|rapid|speed|priority)\s*delivery/i,
  /(same|next)\s*day\s*delivery/i,
  /(dispatch|ship|deliver)\s*(within|in)\s*\d+/i,
  /(fast|quick|express)\s*(shipping|dispatch|deliver)/i,
  /\d+\s*days?\s*(delivery|dispatch|shipping)/i,

  // COD / payment
  /cash\s*on\s*delivery/i,
  /pay\s*(on|after)\s*(delivery|receipt)/i,
  /cod\s*available/i,
  /no\s*cost\s*emi/i,
  /(easy|zero\s*cost|available)\s*emi/i,

  // Warranty / guarantee
  /(limited|lifetime|\d+\s*year|\d+\s*month|\d+\s*day)\s*(warranty|guarantee)/i,
  /warranty\s*(covered|cover|covers|included|provided)/i,
  /life\s*time\s*(warranty|guarantee|replacement)/i,

  // Pricing / offers
  /flat\s*\d+\s*%\s*off/i,
  /upto\s*\d+\s*%\s*(off|discount)/i,
  /(big|huge|massive|special)\s*(sale|discount|offer)/i,
  /limited\s*(time|period|stock)\s*offer/i,
  /(best|lowest|cheapest)\s*price/i,
  /price\s*match\s*(guarantee|promise)/i,

  // Contact / ordering
  /(call|whatsapp|contact|dm|inbox)\s*(us|now|for|to\s*order)/i,
  /bulk\s*(order|purchase|buy|discount)/i,
  /(wholesale|trade)\s*price/i,

  // Flexible rewordings: "we will replace within 30 days", "refund in 2 weeks"
  /replac(e\w*)?\b[\s\S]{0,60}?\b(within|in|after|for)\s*\d+\s*(day|week|month|year)/i,
  /we\s*(will|shall|can|would|'ll|could)\s*(replace|exchange|refund|return|ship|dispatch|deliver|repair)/i,
  /(exchange|return|replace|refund)\s*(it|your|the|this)\s*(product|item|order|purchase|good)/i,
];

const PROHIBITED_TERMS = [
  "sex", "nude", "naked", "porn", "explicit", "obscene", "erotic",
  "adult content", "illegal", "counterfeit", "fake", "smuggled",
  "defam", "hate", "racist",
];

/** Standard punctuation and accented Latin characters that are normal in
 *  product names and should never be flagged as unusual symbols. */
const KNOWN_SAFE_CHARS = new Set([
  ".", ",", "&", "(", ")", "'", "%", "-", "/", "×",
  ":", ";", '"', "!", "?", "=", "+", "#", "@", "*", "~", "₹",
  "é", "è", "ê", "ë", "à", "â", "ä", "ç", "î", "ï", "ô", "ö", "ù", "û", "ü",
  "ñ", "œ", "æ", "ÿ", "ß",
  "É", "È", "Ê", "Ë", "À", "Â", "Ä", "Ç", "Î", "Ï", "Ô", "Ö", "Ù", "Û", "Ü",
  "á", "í", "ó", "ú", "ý", "Á", "Í", "Ó", "Ú", "Ý",
]);

/** Key attribute detection — shared between title and description checks. */
const MATERIAL_WORDS = [
  "steel", "stainless", "copper", "brass", "zinc", "plastic", "acrylic",
  "aluminum", "glass", "wood", "wooden", "bamboo", "ceramic", "porcelain",
  "leather", "nylon", "fabric", "cotton", "wool", "silk", "polyester",
  "velvet", "jute", "rattan", "resin", "acrylic", "silicone", "rubber",
  "foam", "mesh", "canvas", "denim", "linen", "marble", "granite",
  "iron", "carbon", "fiber",
];

/** Check whether a description string has enough key product attributes
 *  (material, size, colour, dimensions) to pass the Product Attributes check. */
export function descriptionPassesAttributes(desc: string): boolean {
  const lower = stripHtml(desc).toLowerCase();
  const matMention = MATERIAL_WORDS.filter((m) => lower.includes(m)).length;
  const sizeMention = SIZE_WORDS.filter((s) => lower.includes(s)).length;
  const hasDims = /\b\d+\s*(ml|g|kg|l|oz|gm|mg|inch|inches|cm|mm|foot|feet|lt)\b/i.test(lower);
  const colorRe = /\b(color|colour|shade|black|white|red|blue|green|pink|brown|silver|gold|grey|navy|beige|purple|maroon|multi|multicolor|transparent|clear|yellow|orange)\b/i;
  const hasColor = colorRe.test(lower);
  const attrCount = (matMention > 0 ? 1 : 0) + (sizeMention > 0 ? 1 : 0) + (hasDims ? 1 : 0) + (hasColor ? 1 : 0);
  return attrCount >= 1;
}
const SIZE_WORDS = [
  "diameter", "width", "height", "length", "depth", "thickness",
  "capacity", "weight", "voltage", "watt", "power", "lumen",
  "frequency", "flow", "speed",
];

/** Words that describe how a product is used/worn/installed, not what it is.
 *  They must be skipped when extracting the product head noun. */
const DESCRIPTOR_WORDS = new Set([
  "mount", "mounted", "mounting", "style", "design", "type", "model",
  "version", "edition", "variant", "grade", "finish", "pattern", "form",
  "shape", "color", "colour", "home", "office", "kitchen", "bathroom",
  "wall", "desk", "table", "floor", "ceiling", "door", "window",
  "portable", "handheld", "electric", "manual", "automatic", "digital",
  "analog", "foldable", "collapsible", "adjustable", "extendable",
  "retractable", "reusable", "disposable", "waterproof", "heavy",
  "lightweight", "compact", "mini", "large", "small", "medium",
  "standard", "premium", "deluxe", "ultra", "pro", "plus", "max",
  "smart", "modern", "classic", "elegant", "simple",
  // color words
  "white", "black", "red", "blue", "green", "pink", "brown", "silver",
  "gold", "golden", "grey", "gray", "navy", "beige", "purple", "maroon",
  "teal", "coral", "ivory", "cream", "copper", "bronze", "rose",
  "chrome", "matte", "glossy", "clear", "opaque", "transparent",
  "multicolor", "multicolour",
  // display / tech
  "led", "lcd", "oled", "qled", "hd", "fhd", "uhd", "bluetooth", "wifi",
  "wireless", "wired",
  // packaging
  "single", "double", "triple", "packed", "assorted", "mixed",
  // shipping / logistics words that appear in product titles
  "dispatch", "dispatched", "delivery", "deliver", "delivered",
  "ship", "shipment", "shipped", "shipping",
  // clothing / apparel target groups
  "men", "women", "mens", "womens", "kids", "male", "female",
  "unisex", "adult", "child", "baby", "toddler", "infant",
]);

/**
 * Extract the sellable product noun from a title by scanning backwards
 * through tokens and skipping measurements, stopwords, and descriptors.
 */
function extractProductHead(title: string): string {
  const tokens = (title.toLowerCase().match(/[a-z]+/g) ?? [])
    .filter((t) => t.length > 2);
  const stop = new Set([
    "pack", "piece", "pieces", "pcs", "set", "combo", "ml", "gm", "kg",
    "inch", "of", "for", "with", "and", "the", "size", "color", "new",
    "brand", "original", "genuine", "rated", "made",
    "will", "shall", "would", "could", "should", "may", "might", "must",
    "can", "has", "have", "had", "been", "being", "was", "were", "are",
    "each", "all", "any", "some", "this", "that", "these", "those",
  ]);
  for (let i = tokens.length - 1; i >= 0; i--) {
    if (stop.has(tokens[i])) continue;
    if (DESCRIPTOR_WORDS.has(tokens[i])) continue;
    if (MATERIAL_WORDS.includes(tokens[i])) continue;
    if (/^\d+$/.test(tokens[i])) continue;
    return tokens[i];
  }
return tokens.length > 0 ? tokens[tokens.length - 1] : "";
}

// ─── AI-Powered Text Correction API ──────────────────────────────────────

export interface CorrectTextProduct {
  sku: string;
  title: string;
  description: string;
  brand?: string;
  category?: string;
  productTypeLabel?: string;
  v2Category?: string;
  v2Confidence?: number;
  images?: string[];
}

export interface CorrectTextResult {
  results: Array<{ sku: string; title: string | null; description: string | null; log?: string[] }>;
}

export async function runTextCorrection(
  products: CorrectTextProduct[],
  apiBase: string = "",
  useQwen: boolean = false,
): Promise<CorrectTextResult> {
  const baseUrl = apiBase || getPrelistingApiBase();
  const resp = await fetch(`${baseUrl}/api/products/correct-text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products, useQwen }),
  });
  if (!resp.ok) {
    throw new Error(`Text correction failed: ${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

const BATCH_SIZE = 10;

async function batchCall<TInput, TOutput>(
  items: TInput[],
  fn: (batch: TInput[]) => Promise<TOutput>,
  mergeResults: (outputs: TOutput[]) => TOutput,
  onBatch?: (batchIdx: number, totalBatches: number) => void,
  batchSize: number = BATCH_SIZE,
): Promise<TOutput> {
  const batches: TInput[][] = [];
  for (let i = 0; i < items.length; i += batchSize) {
    batches.push(items.slice(i, i + batchSize));
  }
  const outputs: TOutput[] = [];
  for (let i = 0; i < batches.length; i++) {
    onBatch?.(i + 1, batches.length);
    // Retry transient tunnel drops (TypeError: Failed to fetch / Load failed)
    let lastErr: unknown;
    for (let attempt = 0; attempt <= 2; attempt++) {
      try {
        outputs.push(await fn(batches[i]));
        lastErr = null;
        break;
      } catch (e: any) {
        lastErr = e;
        const transient = e?.name === "TypeError" || /failed to fetch|load failed|networkerror/i.test(String(e?.message));
        if (attempt === 2 || !transient) break;
        await new Promise((r) => setTimeout(r, 3000 * (attempt + 1)));
      }
    }
    if (lastErr) throw lastErr;
  }
  return mergeResults(outputs);
}

export async function runTextCorrectionBatched(
  products: CorrectTextProduct[],
  apiBase: string = "",
  useQwen: boolean = false,
  onBatch?: (batchIdx: number, totalBatches: number) => void,
  batchSize: number = BATCH_SIZE,
): Promise<CorrectTextResult> {
  return batchCall(
    products,
    (batch) => runTextCorrection(batch, apiBase, useQwen),
    (outputs) => ({
      results: outputs.flatMap((o) => o.results),
    }),
    onBatch,
    batchSize,
  );
}

export async function runHsnSuggestionBatched(
  products: HsnSuggestProduct[],
  apiBase: string = "",
  onBatch?: (batchIdx: number, totalBatches: number) => void,
): Promise<HsnSuggestResult> {
  return batchCall(
    products,
    (batch) => runHsnSuggestion(batch, apiBase),
    (outputs) => ({
      results: outputs.flatMap((o) => o.results),
      algorithmVersion: outputs[0]?.algorithmVersion || "",
    }),
    onBatch,
  );
}

export async function runClipVerificationBatched(
  products: ClipVerificationProduct[],
  apiBase: string = "",
  onBatch?: (batchIdx: number, totalBatches: number) => void,
  useQwenVerify: boolean = false,
): Promise<ClipVerificationResult> {
  return batchCall(
    products,
    (batch) => runClipVerification(batch, apiBase, useQwenVerify),
    (outputs) => ({
      results: outputs.flatMap((o) => o.results),
    }),
    onBatch,
    // Large batch: the backend clip-verify spawns ONE Python process per
    // request and loads CLIP + EasyOCR + MiniLM (~13s) each time. Batching
    // everything into one request avoids reloading those models per batch.
    // First-image OCR keeps memory/CPU bounded even for large batches.
    // Small batch keeps each request under the cloudflared tunnel's ~100s
    // limit (CLIP is ~8-15s/product; ~6 products ≈ 60-90s).
    6,
  );
}
/** Multi-signal title-accuracy check against the product description. */
function evaluateTitleAccuracy(
  title: string,
  description: string,
  category: string,
): { passed: boolean; message: string } {
  const titleLower = title.toLowerCase();
  const descLower = description.toLowerCase();

  // 1. Product head: the sellable noun from the title.
  const productHead = extractProductHead(title);

  // 2. Vocabulary overlap (softened — shared 4+ letter words).
  const stopSet = new Set(["with", "for", "pack", "size", "color", "plus", "each", "that", "this", "your", "will"]);
  const titleVocab = new Set(titleLower.match(/[a-z]{4,}/g) ?? []);
  const descVocab = (description.toLowerCase().match(/[a-z]{4,}/g) ?? [])
    .filter((w) => !stopSet.has(w) && isNaN(Number(w)));
  const overlapCount = descVocab.filter((w) => titleVocab.has(w)).length;
  const vocabRatio = descVocab.length > 0 ? overlapCount / descVocab.length : 1;

  // 3. Key attribute coverage: material, size, color, capacity, voltage,
  //    weight, dimension words in description — are any in the title?
  const descMaterial = MATERIAL_WORDS.filter((m) => descLower.includes(m));
  const descSizes = SIZE_WORDS.filter((s) => descLower.includes(s));
  const descColor = descLower.match(/\b(black|white|red|blue|green|pink|brown|silver|gold|grey|navy|beige|purple|maroon|multi|multicolor|transparent|clear|yellow|orange)\b/g) ?? [];
  const descMl = descLower.match(/\b\d+\s*(ml|g|kg|l|oz|gm|mg|inch|inches|cm|mm|foot|feet|lt)\b/g) ?? [];

  const keyAttrs = new Set([
    ...descMaterial,
    ...(descSizes.length > 0 ? ["size/dimension"] : []),
    ...(descColor.length > 0 ? ["color/variant"] : []),
    ...(descMl.length > 0 ? ["unit/measure"] : []),
  ]);
  const titleCovers = [...keyAttrs].filter((attr) => {
    return MATERIAL_WORDS.includes(attr) ? titleLower.includes(attr) : true;
  });
  const attrCoverage = keyAttrs.size > 0 ? titleCovers.length / keyAttrs.size : 1;

  // 4. Contradiction: title says one material, description says another?
  const titleMaterial = MATERIAL_WORDS.filter((m) => titleLower.includes(m));
  const contradiction = titleMaterial.some((tm) => {
    return descMaterial.includes(tm) === false &&
           descMaterial.length > 0 &&
           // Title says "steel" but description clearly says another material
           descMaterial.every((dm) => dm !== tm);
  });

  // 5. Category reference: does the title mention the category?
  const catTokens = category.toLowerCase().match(/[a-z]{4,}/g) ?? [];
  const catOverlap = catTokens.filter((t) => titleVocab.has(t)).length;

  // Composite score.
  const issues: string[] = [];

  if (vocabRatio < 0.08 && descVocab.length >= 6) {
    issues.push("low vocab overlap with description");
  }

  if (keyAttrs.size >= 2 && attrCoverage < 0.4) {
    const missing = [...keyAttrs].filter(
      (a) => !MATERIAL_WORDS.includes(a) || !titleLower.includes(a),
    );
    issues.push(`missing key attributes (${missing.slice(0, 3).join(", ")})`);
  }

  if (contradiction) {
    issues.push(
      `title material (${titleMaterial.join(",")}) contradicts description (${descMaterial.join(",")})`,
    );
  }

  if (productHead && descVocab.length >= 5 && !descLower.includes(productHead)) {
    issues.push(`product "${productHead}" not mentioned in description`);
  }

  if (issues.length === 0) {
    return { passed: true, message: "Title corresponds to description and attributes" };
  }
  return { passed: false, message: issues.join("; ") };
}

export function validateProduct(
  row: ListingRow,
  allRows: ListingRow[],
): ValidationResult {
  const checks: CheckResult[] = [];
  const sku = str(getField(row, "Sku *"));
  const productName = str(getField(row, "Product Name *"));
  const rawCategory = str(getField(row, "Category Name *"));
  const { id: categoryId, path: categoryPath } = parseCategoryName(rawCategory);

  let decision: Decision = "PASS";

  function addCheck(field: string, check: string, passed: boolean, msg: string, sev: Decision = "FLAG") {
    checks.push({ field, check, passed, decision: passed ? "PASS" : sev, message: msg });
    if (!passed && (sev === "REJECT" || (sev === "FLAG" && decision === "PASS"))) {
      decision = sev;
    }
    if (!passed && sev === "REJECT" && decision !== "REJECT") {
      decision = "REJECT";
    }
  }

  // a) Mandatory field presence — only for columns that exist in the sheet
  for (const field of MANDATORY_FIELDS) {
    if (!hasColumn(row, field)) continue; // column absent from file — skip, don't REJECT
    const val = getField(row, field);
    const passed = !isEmpty(val);
    addCheck(
      field,
      "Mandatory field",
      passed,
      passed ? `${field} is present` : `${field} is empty or missing`,
      "REJECT",
    );
  }

  // b) HSN code sanity check
  const hsn = str(getField(row, "Hsn *"));
  const mrp = str(getField(row, "Mrp Price *"));
  const transfer = str(getField(row, "Transfer Price *"));

  if (!isEmpty(hsn)) {
    const hsnNumeric = /^\d+$/.test(hsn);
    const hsnLen = hsn.replace(/\D/g, "").length;

    // HSN codes are 4, 6, or 8 digits (8-digit = full classification code).
    if (!hsnNumeric || hsnLen < 4 || hsnLen > 8 || hsnLen === 5 || hsnLen === 7) {
      addCheck("Hsn *", "HSN format", false, `HSN "${hsn}" is not a valid 4, 6, or 8 digit numeric code`, "FLAG");
    } else {
      addCheck("Hsn *", "HSN format", true, `HSN "${hsn}" is valid`, "PASS");
    }

    if (hsn === mrp || hsn === transfer) {
      addCheck(
        "Hsn *",
        "HSN vs price collision",
        false,
        `HSN "${hsn}" matches MRP/Transfer price — likely a data entry error, not an actual HSN code`,
        "FLAG",
      );
    } else {
      addCheck("Hsn *", "HSN vs price collision", true, "HSN does not match MRP or Transfer price", "PASS");
    }
  }

  // c) Title / product name checks
  if (!isEmpty(productName)) {
    if (/^[A-Z\s\d\W]+$/.test(productName) && productName.length > 5) {
      addCheck("Product Name *", "ALLCAPS detection", false, "Product name appears to be in ALL CAPS", "FLAG");
    } else {
      addCheck("Product Name *", "ALLCAPS detection", true, "Product name casing looks normal", "PASS");
    }

    if (/\.(com|in|net)\b|www\./i.test(productName)) {
      addCheck("Product Name *", "URL in title", false, "Product name contains a URL/domain reference", "FLAG");
    } else {
      addCheck("Product Name *", "URL in title", true, "No URL detected in product name", "PASS");
    }

    const rawBrand = str(getField(row, "Brand Name *"));
    const brand = stripIdPrefix(rawBrand);
    const brandInTitle = !isEmpty(brand) && productName.toLowerCase().includes(brand.toLowerCase());
    if (!isEmpty(brand)) {
      addCheck(
        "Product Name *",
        "Brand in title",
        brandInTitle,
        brandInTitle
          ? `Brand "${brand}" found in product name`
          : `Brand "${brand}" not found in product name`,
        "FLAG",
      );
    }

    // Standard structure: [Brand] + [Product Type] + [Key Attributes]
    // Flag for manual review when the brand is missing from the title or the
    // title is too short to contain product type + attributes.
    const titleLower = productName.toLowerCase();
    const titleWordCount = productName.split(/\s+/).filter(Boolean).length;
    const brandAtStart =
      !isEmpty(brand) &&
      titleLower.startsWith(brand.toLowerCase());
    if (!isEmpty(brand) && brandAtStart) {
      addCheck("Product Name *", "Standard structure", true, `Title starts with brand "${brand}"`, "PASS");
    } else if (!isEmpty(brand) && brandInTitle) {
      addCheck("Product Name *", "Standard structure", true, "Brand present in title", "PASS");
    } else if (titleWordCount < 3) {
      addCheck("Product Name *", "Standard structure", false, "Title too short to follow [Brand] + [Product Type] + [Key Attributes]", "FLAG");
    } else {
      addCheck("Product Name *", "Standard structure", false, `Brand "${brand}" not found in title`, "FLAG");
    }

    // Formatting: excessive symbols / special characters.
    // A simple negated \w captures most unusual glyphs. After identifying them,
    // we filter out standard punctuation, accented Latin characters, and
    // common product notation — only truly unusual symbols count.
    const rawSymbols = productName.match(/[^\w\s]/g) ?? [];
    // Underscores/pipes/backslashes are never legitimate in a product title
    // (leftover import/CSV artifacts) — flag them individually, even a single one.
    const junkSymbols = rawSymbols.filter((c) => c === "_" || c === "|" || c === "\\");
    if (junkSymbols.length > 0) {
      addCheck(
        "Product Name *",
        "Special characters",
        false,
        `Title contains ${junkSymbols.length} invalid character${junkSymbols.length > 1 ? "s" : ""} (${[...new Set(junkSymbols)].join(" ")})`,
        "FLAG",
      );
    } else {
      addCheck("Product Name *", "Special characters", true, "No underscores or invalid characters in title", "PASS");
    }

    const unusualSymbols = rawSymbols.filter((c) => !KNOWN_SAFE_CHARS.has(c));
    const excessiveSymbols = unusualSymbols.length >= 5;
    if (excessiveSymbols) {
        addCheck(
          "Product Name *",
          "Excessive symbols",
          false,
          `Title contains unusual symbols (${[...new Set(unusualSymbols)].slice(0, 5).join(" ")})`,
        "FLAG",
      );
    } else {
      addCheck("Product Name *", "Excessive symbols", true, "No unusual symbols in title", "PASS");
    }

    // Formatting: misleading / promotional claims.
    // Exclude words that are part of the brand name — e.g. "Magic" brand
    // shouldn't be flagged as misleading.
    const titleNoBrand = !isEmpty(brand)
      ? titleLower.replace(
          new RegExp(brand.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"),
          "",
        ).trim()
      : titleLower;
    const claim = MISLEADING_CLAIMS.find((c) => titleNoBrand.includes(c));
    // Context guard: "best friend", "best for gift", etc. are not promotional
    const claimContextSafe = claim && CLAIM_EXEMPT_PHRASES.test(titleNoBrand);
    const opsClaim = OPS_PATTERNS.find((r) => r.test(titleNoBrand));
    // Context guard: "dispatch" preceded by "color/design/random" is a
    // product variation note ("Any 1 Design & Color Will be Dispatch"),
    // not a shipping claim.
    const opsContextSafe =
      opsClaim && CLAIM_EXEMPT_CONTEXT.test(titleNoBrand);
    if (claim && !claimContextSafe) {
      addCheck("Product Name *", "Misleading claims", false, `Title contains promotional/misleading claim "${claim.trim()}"`, "FLAG");
    } else if (opsClaim && !opsContextSafe) {
      addCheck("Product Name *", "Misleading claims", false, "Title contains operations/marketplace claim (delivery, return, COD, warranty)", "FLAG");
    } else {
      addCheck("Product Name *", "Misleading claims", true, "No misleading claims detected", "PASS");
    }

    // Accuracy: title should correspond to the product description,
    // category, and key attributes. Multi-signal: vocabulary overlap,
    // product-head match, attribute coverage, and contradiction detection.
    const description = stripHtml(str(getField(row, "Description *")));
    if (!isEmpty(description)) {
      const accResult = evaluateTitleAccuracy(
        productName,
        description,
        categoryPath || rawCategory || "",
      );
      addCheck("Product Name *", "Title accuracy", accResult.passed, accResult.message, "FLAG");
    } else {
      const titleWordCount = productName.split(/\s+/).filter(Boolean).length;
      if (titleWordCount < 3) {
        addCheck("Product Name *", "Title accuracy", false, "Title too short — description is empty, cannot verify accuracy", "FLAG");
      }
    }

    // Prohibited content: explicit / defamatory / obscene / unlawful wording
    const banned = PROHIBITED_TERMS.find((t) => titleLower.includes(t));
    if (banned) {
      addCheck("Product Name *", "Prohibited content", false, `Title may contain prohibited wording ("${banned}")`, "REJECT");
    } else {
      addCheck("Product Name *", "Prohibited content", true, "No prohibited wording detected", "PASS");
    }

    // Unit count / variation is mandatory ONLY for products that come in
    // variants or packs (apparel sizes, footwear sizes, consumables, sets).
    // Single-SKU articles (cable, charger, appliance, bottle, holder, etc.)
    // are exempt. Eligibility is inferred from title + description context.
    const desc = stripHtml(str(getField(row, "Description *")));
    const eligibility = inferUnitVariationEligibility(productName, desc);
    if (eligibility.exempt) {
      addCheck("Product Name *", "Unit count/variation", true, `Unit count/variation not applicable (${eligibility.reason})`, "PASS");
    } else {
      const hasUnitOrVariation =
        /\b(pack|set|pair|piece|pieces|pcs|count|pcs\.)\s+of\s+\d+\b/i.test(productName) ||
        /\b\d+\s*(ml|g|kg|l|oz|gm|mg|inch|inches|cm|mm|foot|feet|lt)\b/i.test(productName) ||
        /\b(color|colour|size|variant|shade)\b/i.test(productName) ||
        /\b(black|white|red|blue|green|pink|brown|golden|silver|grey|navy|beige|purple|maroon|multi|multicolor)\b/i.test(productName);
      if (hasUnitOrVariation) {
        addCheck("Product Name *", "Unit count/variation", true, "Unit count or variation (pack/color/size) present in title", "PASS");
      } else {
        addCheck("Product Name *", "Unit count/variation", false, "No unit count (pack of N) or variation (color/size) found in title", "FLAG");
      }
    }
  }

  // c-b) Description checks: must be HTML-formatted, properly structured,
  //      follow content rules, and match the product data.
  const rawDescription = str(getField(row, "Description *"));
  if (!isEmpty(rawDescription)) {
    // HTML structure: description must contain HTML tags (Gajab requires
    // bullet points, bolding, paragraphs, etc. — plain text is insufficient).
    const hasHtmlTags = /<([a-z]+)[^>]*>/i.test(rawDescription);
    const descPlain = stripHtml(rawDescription);
    const descWordCount = descPlain.split(/\s+/).filter(Boolean).length;

    if (!hasHtmlTags) {
      addCheck("Description *", "HTML structure", false, "Description is plain text — should be HTML formatted (use bullet points, bold, paragraphs)", "FLAG");
    } else if (descWordCount < 10) {
      addCheck("Description *", "HTML structure", false, "Description too short — expand with features, material, usage, dimensions", "FLAG");
    } else {
      addCheck("Description *", "HTML structure", true, "HTML-formatted description with sufficient detail", "PASS");
    }

    // Formatting: ALL CAPS, excessive symbols
    if (/^[A-Z\s\d\W]+$/.test(descPlain) && descPlain.length > 20) {
      addCheck("Description *", "Formatting", false, "Description appears to be in ALL CAPS", "FLAG");
    } else {
      addCheck("Description *", "Formatting", true, "Description casing looks normal", "PASS");
    }

    const descRawSymbols = descPlain.match(/[^\w\s]/g) ?? [];
    const descUnusual = descRawSymbols.filter((c) => !KNOWN_SAFE_CHARS.has(c));
    if (descUnusual.length >= 8) {
      const uniq = [...new Set(descUnusual)].slice(0, 5);
      addCheck("Description *", "Excessive symbols", false, `Description contains unusual symbols (${uniq.join(" ")})`, "FLAG");
    } else {
      addCheck("Description *", "Excessive symbols", true, "No unusual symbols in description", "PASS");
    }

    // URL marks prohibited in description
    if (/\.(com|in|net)\b|www\./i.test(rawDescription)) {
      addCheck("Description *", "URL in description", false, "Description contains a URL/domain reference", "FLAG");
    } else {
      addCheck("Description *", "URL in description", true, "No URL detected in description", "PASS");
    }

    // Misleading claims in description (strip brand to avoid false positives)
    const rawBrandD = str(getField(row, "Brand Name *"));
    const brandD = stripIdPrefix(rawBrandD);
    const descNoBrand = !isEmpty(brandD)
      ? descPlain.toLowerCase().replace(
          new RegExp(brandD.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"),
          "",
        ).trim()
      : descPlain.toLowerCase();
    const descClaim = MISLEADING_CLAIMS.find((c) => descNoBrand.includes(c));
    const descClaimSafe = descClaim && CLAIM_EXEMPT_PHRASES.test(descNoBrand);
    const descOps = OPS_PATTERNS.find((r) => r.test(descNoBrand));
    const descOpsSafe = false; // OPS in descriptions always flagged — no context exemption
    if (descClaim && !descClaimSafe) {
      addCheck("Description *", "Misleading claims", false, `Description contains promotional claim "${descClaim.trim()}"`, "FLAG");
    } else if (descOps && !descOpsSafe) {
      addCheck("Description *", "Misleading claims", false, "Description contains operations/marketplace claim (delivery, return, COD, warranty)", "FLAG");
    } else {
      addCheck("Description *", "Misleading claims", true, "No misleading claims in description", "PASS");
    }

    // Prohibited content
    const descBanned = PROHIBITED_TERMS.find((t) => descPlain.toLowerCase().includes(t));
    if (descBanned) {
      addCheck("Description *", "Prohibited content", false, `Description may contain prohibited wording ("${descBanned}")`, "REJECT");
    } else {
      addCheck("Description *", "Prohibited content", true, "No prohibited wording in description", "PASS");
    }

    // Key attributes presence: material, dimensions, weight, color, etc.
    const descLower = descPlain.toLowerCase();
    const matMention = MATERIAL_WORDS.filter((m) => descLower.includes(m)).length;
    const sizeMention = SIZE_WORDS.filter((s) => descLower.includes(s)).length;
    const hasDims = /\b\d+\s*(ml|g|kg|l|oz|gm|mg|inch|inches|cm|mm|foot|feet|lt)\b/i.test(descPlain);
    const hasColor = /\b(color|colour|shade|black|white|red|blue|green|pink|brown|silver|gold|grey|navy|beige|purple|maroon|multi|multicolor|transparent|clear|yellow|orange)\b/i.test(descLower);
    const attrCount = (matMention > 0 ? 1 : 0) + (sizeMention > 0 ? 1 : 0) + (hasDims ? 1 : 0) + (hasColor ? 1 : 0);
    if (attrCount < 1) {
      addCheck("Description *", "Product attributes", false, "Description missing key product attributes (material, size, color, weight, or capacity)", "FLAG");
    } else {
      addCheck("Description *", "Product attributes", true, "Key product attributes present in description", "PASS");
    }
  } else {
    addCheck("Description *", "HTML structure", false, "Description is empty — Gajab requires a detailed HTML description", "REJECT");
  }

  // d) Category-driven compliance
  if (categoryId && CATEGORY_COMPLIANCE_RULES[categoryId]) {
    const catRule = CATEGORY_COMPLIANCE_RULES[categoryId];
    for (const compCheck of catRule.checks) {
      if (compCheck.required) {
        const fieldVal = getField(row, compCheck.type) ?? "";
        addCheck(
          "Category Name *",
          `Compliance: ${compCheck.type}`,
          !isEmpty(fieldVal),
          `${compCheck.type} required for category "${catRule.name}"`,
          "REJECT",
        );
      }
    }
  }

  if (categoryId !== null) {
    const catPath = CATEGORY_COMPLIANCE_RULES[categoryId]?.name || "";
    if (NO_COMPLIANCE_CATEGORIES.has(catPath)) {
      addCheck(
        "Category Name *",
        "Compliance exemptions",
        true,
        `Category "${catPath}" has no BIS/FSSAI/ISI requirement per Gajab guidelines`,
        "PASS",
      );
    }
  }

  // e) Variant / relationship consistency
  const relationship = str(getField(row, "Relationship *"));
  const parentSku = str(getField(row, "Parent Sku *"));

  if (relationship === "Simple") {
    if (parentSku !== sku) {
      addCheck(
        "Relationship *",
        "Simple variant parent match",
        false,
        `Relationship is "Simple" but Parent Sku "${parentSku}" does not match Sku "${sku}"`,
        "FLAG",
      );
    } else {
      addCheck(
        "Relationship *",
        "Simple variant parent match",
        true,
        `Simple product, Parent Sku matches Sku`,
        "PASS",
      );
    }
  } else if (relationship === "Variant") {
    if (parentSku === sku) {
      addCheck(
        "Relationship *",
        "Variant parent differs",
        false,
        `Relationship is "Variant" but Parent Sku equals Sku — should reference a different parent`,
        "FLAG",
      );
    } else {
      const parentExists = allRows.some(
        (r) => str(getField(r, "Sku *")) === parentSku,
      );
      if (!parentExists) {
        addCheck(
          "Relationship *",
          "Variant parent exists",
          false,
          `Variant parent SKU "${parentSku}" not found in uploaded batch`,
          "REJECT",
        );
      } else {
        addCheck(
          "Relationship *",
          "Variant parent exists",
          true,
          `Variant parent SKU "${parentSku}" found in batch`,
          "PASS",
        );
      }
    }
  }

  // f) Duplicate SKU check
  const skuCount = allRows.filter((r) => str(getField(r, "Sku *")) === sku).length;
  if (skuCount > 1) {
    addCheck(
      "Sku *",
      "Duplicate SKU",
      false,
      `SKU "${sku}" appears ${skuCount} times in the uploaded file`,
      "REJECT",
    );
  } else {
    addCheck("Sku *", "Duplicate SKU", true, "SKU is unique in the batch", "PASS");
  }

  // ─── Score calculation ────────────────────────────────────────────
  const hasReject = checks.some((c) => c.decision === "REJECT" && !c.passed);
  const totalCheckCount = checks.length;
  const passedCount = checks.filter((c) => c.passed).length;

  let score: number;
  if (hasReject) {
    score = Math.max(0, Math.round((passedCount / totalCheckCount) * 6));
  } else {
    score = 7 + Math.round((passedCount / totalCheckCount) * 3);
  }
  score = Math.min(10, score);

  // Suggestions are generated by the MiniLM-powered AI correction API
  // (called after HSN suggestion in the component). Set undefined here.
  const titleSuggestion: string | undefined = undefined;
  const descSuggestion: string | undefined = undefined;

  return {
    sku,
    productName: productName || "(unnamed)",
    category: categoryPath || rawCategory || "(unknown)",
    decision: hasReject ? "REJECT" : decision,
    score,
    checks,
    titleSuggestion,
    descSuggestion,
  };
}

// ─── Perceptual hashing (Canvas blockhash) ─────────────────────────

function computeBlockhash(img: HTMLImageElement, bits: number = 16): string {
  // dHash: downsample to (bits+1) x bits grayscale and encode, per row, whether
  // each pixel is darker than its right neighbour. Robust to exposure/white
  // backgrounds and never degenerates to an all-zero hash, so distinct
  // product shots (even on white) stay well below the duplicate threshold.
  const canvas = document.createElement("canvas");
  canvas.width = bits + 1;
  canvas.height = bits;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0, bits + 1, bits);
  const pixels = ctx.getImageData(0, 0, bits + 1, bits).data;

  let hash = "";
  for (let y = 0; y < bits; y++) {
    for (let x = 0; x < bits; x++) {
      const idx = (y * (bits + 1) + x) * 4;
      const g = (pixels[idx] + pixels[idx + 1] + pixels[idx + 2]) / 3;
      const gRight = (pixels[idx + 4] + pixels[idx + 5] + pixels[idx + 6]) / 3;
      hash += g < gRight ? "1" : "0";
    }
  }
  return hash;
}

function hammingDistance(h1: string, h2: string): number {
  if (h1.length !== h2.length) return h1.length;
  let dist = 0;
  for (let i = 0; i < h1.length; i++) {
    if (h1[i] !== h2[i]) dist++;
  }
  return dist;
}

/** Similarity percentage (0–100) between two equal-length hashes. */
function hashSimilarity(h1: string, h2: string): number {
  if (h1.length === 0) return 0;
  return Math.round((1 - hammingDistance(h1, h2) / h1.length) * 100);
}

// Only treat as duplicate when images are essentially identical (≥99% similar).
// A 16×16 = 256-bit hash at 99% means ≤2 bits differ — same image, not just
// "same product from a similar angle".
const BLOCKHASH_DUPLICATE_SIMILARITY = 99;

// ─── Image checks ──────────────────────────────────────────────────

export async function validateImages(
  row: ListingRow,
): Promise<{ checks: CheckResult[]; imageChecks: ImageCheckResult[] }> {
  const checks: CheckResult[] = [];
  const imageChecks: ImageCheckResult[] = [];

  // ── Collect image URLs ───────────────────────────────────────────
  const imageUrls: string[] = [];
  for (const field of IMAGE_FIELDS) {
    const url = str(getField(row, field));
    if (url && url.startsWith("http")) {
      imageUrls.push(url);
    }
  }

  // ── RULE 1: Image count 4–10 ─────────────────────────────────────
  if (imageUrls.length < 4) {
    checks.push({
      field: "Product Images",
      check: "RULE 1: Image count",
      passed: false,
      decision: "REJECT",
      message: `Only ${imageUrls.length} product image(s) found — minimum 4 required`,
    });
  } else if (imageUrls.length > 10) {
    checks.push({
      field: "Product Images",
      check: "RULE 1: Image count",
      passed: false,
      decision: "REJECT",
      message: `${imageUrls.length} product images found — maximum 10 allowed`,
    });
  } else {
    checks.push({
      field: "Product Images",
      check: "RULE 1: Image count",
      passed: true,
      decision: "PASS",
      message: `${imageUrls.length} product images (within 4–10 range)`,
    });
  }

  // ── Load each image in parallel with timeout ─────────────────────
  const loadImage = (url: string): Promise<{ url: string; img: HTMLImageElement | null }> => {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      const timer = setTimeout(() => {
        img.src = "";
        resolve({ url, img: null });
      }, 15000);
      img.onload = () => { clearTimeout(timer); resolve({ url, img }); };
      img.onerror = () => { clearTimeout(timer); resolve({ url, img: null }); };
      img.src = url;
    });
  };

  const imageResults = await Promise.all(imageUrls.map(loadImage));
  const loadedImages: Array<{ url: string; img: HTMLImageElement }> = [];

  for (const { url, img } of imageResults) {
    if (!img || img.naturalWidth === 0) {
      // WARN only: some CDNs (Meesho/Flixcart) block browser-side loads via
      // CORS/referrer restrictions. The CLIP server downloads server-side
      // and will do the definitive image-quality analysis.
      checks.push({
        field: "Product Images",
        check: "Image load failure",
        passed: true,
        decision: "WARN",
        message: `Image couldn't load in browser (CDN restriction?): ${url.substring(0, 60)}... — CLIP server will verify server-side`,
      });
      imageChecks.push({ url, loaded: false, width: 0, height: 0, isDuplicate: false });
      continue;
    }
    loadedImages.push({ url, img });
  }

  // ── RULE 3: Resolution ≥ 500x500 for EVERY image ─────────────────
  for (const { url, img } of loadedImages) {
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    imageChecks.push({ url, loaded: true, width: w, height: h, isDuplicate: false });
    if (w < 500 || h < 500) {
      checks.push({
        field: "Product Images",
        check: "RULE 3: Resolution ≥ 500x500",
        passed: false,
        decision: "REJECT",
        message: `Image ${w}x${h} below 500x500 minimum: ${url.substring(0, 60)}...`,
      });
    }
  }
  // Add a single PASS for resolution if all passed
  const allResolutionPass = loadedImages.every(({ img }) => img.naturalWidth >= 500 && img.naturalHeight >= 500);
  if (allResolutionPass && loadedImages.length > 0) {
    checks.push({
      field: "Product Images",
      check: "RULE 3: Resolution ≥ 500x500",
      passed: true,
      decision: "PASS",
      message: `All ${loadedImages.length} loaded image(s) meet 500x500 minimum`,
    });
  }

  // ── RULE 2 Step 1: Exact URL duplicate detection ─────────────────
  const uniqueUrlSet = new Set<string>();
  for (const entry of imageChecks.filter((i) => i.loaded)) {
    if (uniqueUrlSet.has(entry.url)) {
      entry.isDuplicate = true;
    } else {
      uniqueUrlSet.add(entry.url);
    }
  }
  if (uniqueUrlSet.size < imageChecks.filter((i) => i.loaded).length) {
    checks.push({
      field: "Product Images",
      check: "RULE 2: Exact URL duplicates",
      passed: false,
      decision: "REJECT",
      message: `Duplicate image URLs detected — ${uniqueUrlSet.size} unique out of ${imageChecks.filter((i) => i.loaded).length} loaded images`,
    });
  }

  // ── RULE 2 Step 2: Perceptual hash comparison ────────────────────
  const loadedEntries = imageChecks.filter((i) => i.loaded);
  const loadedImgs = loadedImages;

  // Compute blockhashes for all loaded images
  const hashes: Array<{ idx: number; hash: string }> = [];
  for (let i = 0; i < loadedImgs.length; i++) {
    try {
      const hash = computeBlockhash(loadedImgs[i].img);
      loadedEntries[i].phash = hash;
      hashes.push({ idx: i, hash });
    } catch {
      // Skip if canvas fails (CORS issue)
    }
  }

  // Mark perceptual duplicates (≥99% similar = essentially identical image)
  const pHashDuplicates = new Map<number, number>(); // flagged index -> partner index
  let maxSimilarity = 0;
  let maxPair: [number, number] | null = null;
  for (let i = 0; i < hashes.length; i++) {
    for (let j = i + 1; j < hashes.length; j++) {
      const sim = hashSimilarity(hashes[i].hash, hashes[j].hash);
      if (sim > maxSimilarity) {
        maxSimilarity = sim;
        maxPair = [hashes[i].idx, hashes[j].idx];
      }
      if (sim >= BLOCKHASH_DUPLICATE_SIMILARITY) {
        pHashDuplicates.set(hashes[j].idx, hashes[i].idx);
        loadedEntries[hashes[j].idx].isDuplicate = true;
      }
    }
  }

  if (pHashDuplicates.size > 0) {
    const dupDesc = [...pHashDuplicates.entries()]
      .map(
        ([idx, partner]) =>
          `[${loadedEntries[idx]?.url ?? `#${idx}`}] duplicates [${loadedEntries[partner]?.url ?? `#${partner}`}]`,
      )
      .join("; ");
    checks.push({
      field: "Product Images",
      check: "RULE 2: Perceptual duplicates",
      passed: false,
      decision: "REJECT",
      message: `${pHashDuplicates.size} duplicate image(s) — most similar pair scored ${maxSimilarity}% identical. ${dupDesc}`,
    });
  } else if (hashes.length > 1 && maxSimilarity >= 90) {
    checks.push({
      field: "Product Images",
      check: "RULE 2: Perceptual duplicates",
      passed: true,
      decision: "PASS",
      message: `No duplicate images (closest pair scored ${maxSimilarity}% similar — below the 99% duplicate threshold)`,
    });
  }

  // Final RULE 2 distinct count check
  const distinctCount = loadedEntries.filter((e) => !e.isDuplicate).length;
  if (distinctCount < 2 && loadedEntries.length > 0) {
    checks.push({
      field: "Product Images",
      check: "RULE 2: Distinct images",
      passed: false,
      decision: "REJECT",
      message: `Only ${distinctCount} distinct image(s) — at least 2 required${pHashDuplicates.size > 0 ? " (after both exact URL and perceptual hash deduplication)" : ""}`,
    });
  } else if (loadedEntries.length > 0) {
    checks.push({
      field: "Product Images",
      check: "RULE 2: Distinct images",
      passed: true,
      decision: "PASS",
      message: `${distinctCount} distinct images found`,
    });
  }

  // ── RULE 5: First image markings (logos/text/watermarks) ─────────
  // Placeholder — requires server-side CLIP, marked as FLAG
  // Will be filled in by runClipVerification() later
  checks.push({
    field: "Product Images",
    check: "RULE 5: Markings (1st image)",
    passed: false,
    decision: "FLAG",
    message: "Markings check pending — run DINOv2+CLIP to verify",
  });

  // ── RULE 6: Content restrictions (all images) ────────────────────
  // Placeholder — requires server-side CLIP, marked as FLAG
  checks.push({
    field: "Product Images",
    check: "RULE 6: Content restrictions",
    passed: false,
    decision: "FLAG",
    message: "Content check pending — run DINOv2+CLIP to verify",
  });

  // ── RULE 7: Ops/marketing claims on images (OCR) ────────────────
  // Placeholder — requires server-side OCR, marked as FLAG
  checks.push({
    field: "Product Images",
    check: "RULE 7: No ops claims on images",
    passed: false,
    decision: "FLAG",
    message: "Image claim check pending — run OCR to verify",
  });

  return { checks, imageChecks };
}

// ─── File parser ───────────────────────────────────────────────────

export interface ParseResult {
  headers: string[];
  rows: ListingRow[];
  rowCount: number;
  fileName: string;
  /** Original rows above the header row (row index 0), preserved so the
   *  corrected export keeps the Gajab Hub template layout (header on row 2). */
  prefixRow: unknown[];
}

export function parseXlsxFile(data: ArrayBuffer, fileName: string): ParseResult {
  const workbook = XLSX.read(data, { type: "array" });
  const sheetName = workbook.SheetNames[0];
  const sheet = workbook.Sheets[sheetName];
  const jsonData = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, {
    header: 1,
    defval: "",
    blankrows: false,
  });

  if (jsonData.length < 2) {
    return { headers: [], rows: [], rowCount: 0, fileName, prefixRow: [] };
  }

  // Detect whether row[0] is the header (standard CSV) or a prefix/title row.
  // If row[0] contains "Sku" it's the header row — don't skip it.
  const row0Str = (jsonData[0] as unknown as string[] ?? []).join(" ").toLowerCase();
  const hasPrefixRow = !row0Str.includes("sku") && !row0Str.includes("product name");

  const headerIdx = hasPrefixRow ? 1 : 0;
  const headerRow = jsonData[headerIdx] as unknown as string[];
  const headers = headerRow.map((h) => String(h ?? "").trim());
  const prefixRow = hasPrefixRow ? ((jsonData[0] as unknown) as unknown[] ?? []) : [];
  const rows: ListingRow[] = [];

  for (let i = (hasPrefixRow ? 2 : 1); i < jsonData.length; i++) {
    const rawRow = jsonData[i] as unknown as unknown[];
    if (!rawRow || rawRow.every((cell) => !cell || String(cell).trim() === "")) continue;
    const row: ListingRow = {};
    for (let j = 0; j < headers.length; j++) {
      row[headers[j]] = rawRow[j] ?? "";
    }
    rows.push(row);
  }

  return { headers, rows, rowCount: rows.length, fileName, prefixRow };
}

export function parseCsvFile(text: string, fileName: string): ParseResult {
  const result = Papa.parse<string[]>(text, {
    skipEmptyLines: true,
    header: false,
  });

  if (!result.data || result.data.length < 2) {
    return { headers: [], rows: [], rowCount: 0, fileName, prefixRow: [] };
  }

  const row0Str = (result.data[0] ?? []).join(" ").toLowerCase();
  const hasPrefixRow = !row0Str.includes("sku") && !row0Str.includes("product name");

  const headerIdx = hasPrefixRow ? 1 : 0;
  const headerRow = result.data[headerIdx];
  const headers = headerRow.map((h) => String(h ?? "").trim());
  const prefixRow = hasPrefixRow ? (result.data[0] as unknown[] ?? []) : [];
  const rows: ListingRow[] = [];

  for (let i = (hasPrefixRow ? 2 : 1); i < result.data.length; i++) {
    const rawRow = result.data[i];
    if (!rawRow || rawRow.every((cell) => !cell || String(cell).trim() === "")) continue;
    const row: ListingRow = {};
    for (let j = 0; j < headers.length; j++) {
      row[headers[j]] = rawRow[j] ?? "";
    }
    rows.push(row);
  }

  return { headers, rows, rowCount: rows.length, fileName, prefixRow };
}

export function parseFile(file: File): Promise<ParseResult> {
  const ext = file.name.split(".").pop()?.toLowerCase();

  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = (e) => {
      const data = e.target?.result;
      if (!data) {
        reject(new Error("Failed to read file"));
        return;
      }

      try {
        if (ext === "xlsx" || ext === "xls") {
          resolve(parseXlsxFile(data as ArrayBuffer, file.name));
        } else if (ext === "csv") {
          resolve(parseCsvFile(data as string, file.name));
        } else {
          reject(new Error(`Unsupported file format: .${ext}`));
        }
      } catch (err) {
        reject(err);
      }
    };

    reader.onerror = () => reject(new Error("File read error"));

    if (ext === "csv") {
      reader.readAsText(file);
    } else {
      reader.readAsArrayBuffer(file);
    }
  });
}

// ─── CSV Export ────────────────────────────────────────────────────

export interface ClipVerificationProduct {
  sku: string;
  firstImageUrl?: string;
  allImageUrls?: string[];
}

export interface ClipVerificationResult {
  results: Array<{
    sku: string;
    rule1?: { passed: boolean; count: number; message: string };
    rule2?: { passed: boolean; distinct: number; total: number; message: string };
    rule3?: { passed: boolean; message: string };
    rule5: {
      hasMarkings: boolean | null;
      watermarkScore: number | null;
      cleanScore: number | null;
      flagged: boolean | null;
      details?: Array<{
        check: string;
        positiveScore: number;
        negativeScore: number;
        ratio: number;
        flagged: boolean;
      }>;
      error?: string;
    };
    rule6: {
      images: Array<{
        url: string;
        flagged: boolean | null;
        scores: Record<string, number> | null;
        highestCategory: string | null;
        error?: string;
      }>;
      anyFlagged: boolean;
    };
    rule7: {
      images: Array<{
        url: string;
        flagged: boolean | null;
        ocrText?: string;
        claims?: string[];
        error?: string;
      }>;
      anyFlagged: boolean;
    };
  }>;
}

// Retry transient network failures (cloudflared quick tunnel intermittently
// drops a connection mid-request → "TypeError: Failed to fetch" in the browser).
async function fetchWithRetry(url: string, options: RequestInit, retries = 2): Promise<Response> {
  let lastErr: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fetch(url, options);
    } catch (e: any) {
      lastErr = e;
      const transient = e?.name === "TypeError" || /failed to fetch|networkerror|load failed/i.test(String(e?.message));
      if (attempt === retries || !transient) throw e;
      await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
    }
  }
  throw lastErr;
}

export async function runClipVerification(
  products: ClipVerificationProduct[],
  apiBase: string = "",
  useQwenVerify: boolean = false,
): Promise<ClipVerificationResult> {
  const baseUrl = apiBase || getPrelistingApiBase();
  const resp = await fetchWithRetry(`${baseUrl}/api/products/clip-verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products, useQwenVerify }),
    signal: AbortSignal.timeout(1800000),
  });

  if (!resp.ok) {
    throw new Error(`CLIP verification failed: ${resp.status} ${resp.statusText}`);
  }

  return resp.json();
}

export function mergeClipResults(
  validationResult: ValidationResult,
  clipResult: ClipVerificationResult["results"][0] | undefined,
): ValidationResult {
  if (!clipResult) return validationResult;

  const updated = { ...validationResult, checks: [...validationResult.checks.map((c) => ({ ...c }))] };

  // RULE 1-4: Server-side image checks
  const rule1 = clipResult.rule1;
  if (rule1) {
    updated.checks.push({
      field: "Product Images",
      check: "RULE 1: Image count",
      passed: rule1.passed,
      decision: rule1.passed ? "PASS" : "REJECT",
      message: rule1.message,
    });
  }
  const rule2 = clipResult.rule2;
  if (rule2) {
    updated.checks.push({
      field: "Product Images",
      check: "RULE 2: Duplicate images",
      passed: rule2.passed,
      decision: rule2.passed ? "PASS" : "REJECT",
      message: rule2.message,
    });
  }
  const rule3 = clipResult.rule3;
  if (rule3) {
    updated.checks.push({
      field: "Product Images",
      check: "RULE 3: Resolution ≥ 500x500",
      passed: rule3.passed,
      decision: rule3.passed ? "PASS" : "REJECT",
      message: rule3.message,
    });
  }

  // RULE 5: Markings
  const r5 = clipResult.rule5;
  if (r5) {
    const rule5Idx = updated.checks.findIndex((c) => c.check === "RULE 5: Markings (1st image)");
    const r5Check: { field: string; check: string; passed: boolean; decision: Decision; message: string } = {
      field: "Product Images",
      check: "RULE 5: Markings (1st image)",
      passed: false,
      decision: "FLAG",
      message: "Markings check pending",
    };
    if (r5.error) {
      r5Check.passed = false;
      r5Check.decision = "FLAG";
      r5Check.message = `Markings check failed: ${r5.error}`;
    } else if (r5.flagged) {
      const flaggedChecks = r5.details?.filter((d: any) => d.flagged).map((d: any) => d.check).join(", ") || "markings detected";
      const detailStr = r5.details
        ? r5.details.map((d: any) => `${d.check}: ${((d.ratio || 0) * 100).toFixed(0)}%`).join(", ")
        : "";
      r5Check.passed = false;
      r5Check.decision = "FLAG";
      r5Check.message = `First image flagged for: ${flaggedChecks}${detailStr ? ` (scores: ${detailStr})` : ""}`;
    } else {
      const detailStr = r5.details
        ? r5.details.map((d: any) => `${d.check}: clear`).join(", ")
        : "";
      r5Check.passed = true;
      r5Check.decision = "PASS";
      r5Check.message = `First image is clean — no markings detected${detailStr ? ` (${detailStr})` : ""}`;
    }
    if (rule5Idx >= 0) {
      updated.checks[rule5Idx] = r5Check;
    } else {
      updated.checks.push(r5Check);
    }
  }

  // RULE 6: Content restrictions
  const r6 = clipResult.rule6;
  if (r6) {
    const rule6Idx = updated.checks.findIndex((c) => c.check === "RULE 6: Content restrictions");
    const r6Check: { field: string; check: string; passed: boolean; decision: Decision; message: string } = {
      field: "Product Images",
      check: "RULE 6: Content restrictions",
      passed: false,
      decision: "FLAG",
      message: "Content check pending",
    };
    if (r6.anyFlagged) {
      const flaggedUrls = r6.images.filter((i: any) => i.flagged).map((i: any) => i.highestCategory || "flagged");
      r6Check.passed = false;
      r6Check.decision = "FLAG";
      r6Check.message = `Content moderation flagged ${r6.images.filter((i: any) => i.flagged).length}/${r6.images.length} image(s): ${flaggedUrls.join(", ")}`;
    } else {
      r6Check.passed = true;
      r6Check.decision = "PASS";
      r6Check.message = `All ${r6.images.length} image(s) passed content moderation`;
    }
    if (rule6Idx >= 0) {
      updated.checks[rule6Idx] = r6Check;
    } else {
      updated.checks.push(r6Check);
    }

    // Also update imageCheck entries with content flags
    if (updated.imageChecks) {
      for (const ic of updated.imageChecks) {
        const match = r6.images.find((img: any) => img.url === ic.url);
        if (match?.flagged) {
          ic.hasContentFlag = true;
        }
      }
    }
  }

  // RULE 7: Ops/marketing claims on images (OCR)
  const r7 = clipResult.rule7;
  if (r7) {
    const rule7Idx = updated.checks.findIndex((c) => c.check === "RULE 7: No ops claims on images");
    const flaggedImages = r7.images.filter((i: any) => i.flagged);
    const errored = r7.images.find((i: any) => i.error);
    const r7Check: { field: string; check: string; passed: boolean; decision: Decision; message: string } = {
      field: "Product Images",
      check: "RULE 7: No ops claims on images",
      passed: false,
      decision: "FLAG",
      message: "Image claim check pending",
    };
    if (errored) {
      r7Check.passed = false;
      r7Check.decision = "FLAG";
      r7Check.message = `Image claim check failed: ${errored.error}`;
    } else if (r7.anyFlagged) {
      const detail = flaggedImages
        .map(
          (i: any) =>
            `${i.url.split("/").pop()}: ${(i.claims || []).join(", ")}`
        )
        .join(" | ");
      r7Check.passed = false;
      r7Check.decision = "FLAG";
      r7Check.message = `${flaggedImages.length}/${r7.images.length} image(s) flagged for ops/marketing claims: ${detail}`;
    } else {
      r7Check.passed = true;
      r7Check.decision = "PASS";
      r7Check.message = `No ops/marketing claims found on ${r7.images.length} image(s)`;
    }
    if (rule7Idx >= 0) {
      updated.checks[rule7Idx] = r7Check;
    } else {
      updated.checks.push(r7Check);
    }

    // Also update imageCheck entries with claim flags + OCR text
    if (updated.imageChecks) {
      for (const ic of updated.imageChecks) {
        const match = r7.images.find((img) => img.url === ic.url);
        if (match) {
          if (match.flagged) ic.hasOpsClaim = true;
          if (match.ocrText) ic.ocrText = match.ocrText;
        }
      }
    }
  }

  // Recalculate score and decision
  const hasReject = updated.checks.some((c) => c.decision === "REJECT" && !c.passed);
  const hasFlag = updated.checks.some((c) => c.decision === "FLAG" && !c.passed);
  if (hasReject) {
    updated.decision = "REJECT";
  } else if (hasFlag) {
    updated.decision = "FLAG";
  } else {
    updated.decision = "PASS";
  }
  const totalCheckCount = updated.checks.length;
  const passedCount = updated.checks.filter((c) => c.passed).length;
  if (hasReject) {
    updated.score = Math.max(0, Math.round((passedCount / totalCheckCount) * 6));
  } else {
    updated.score = Math.min(10, 7 + Math.round((passedCount / totalCheckCount) * 3));
  }

  return updated;
}
export function exportResultsCsv(results: ValidationResult[]): void {
  const header =
    "Sku,Product Name,Category,Decision,Score,Issues\n";
  const rows = results
    .map((r) => {
      const issues = r.checks.filter((c) => !c.passed).map((c) => c.message.replace(/"/g, '""')).join("; ");
      const name = r.productName.replace(/"/g, '""');
      const cat = r.category.replace(/"/g, '""');
      return `"${r.sku}","${name}","${cat}",${r.decision},${r.score},"${issues}"`;
    })
    .join("\n");

  const csv = header + rows;
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "pre-listing-validation-results.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// ─── HSN suggestion (embedding-based, served by api-server) ──────────────────

export interface HsnSuggestion {
  hsn: string;
  description: string;
  gst_rate: number;
  central_tax: number;
  schedule: string;
  confidence: number;
  rank: number;
}

export interface HsnSuggestProduct {
  sku: string;
  title: string;
  description?: string;
  images?: string[];
}

export interface HsnSuggestResult {
  algorithmVersion?: string;
  results: Array<{
    sku: string;
    query: string;
    visualContext?: string;
    productType?: string;
    productTypeLabel?: string;
    unitVariation?: "required" | "optional" | "single";
    titleProductType?: string;
    titleProductTypeLabel?: string;
    titleAccuracyStatus?: "match" | "mismatch" | "unknown";
    opClaims?: string[];
    validationHints?: Record<string, boolean>;
    suggestions: HsnSuggestion[];
    algorithmVersion?: string;
    topGstRate: number | null;
    topSchedule: string | null;
    categoryV2?: string;
    categoryV2Label?: string;
    categoryConfidence?: number;
    confidenceTier?: string;
    originalCategoryV2?: string;
    originalCategoryV2Label?: string;
    categoryStatus?: "confirmed" | "needs-change" | "unknown";
    marqoCategory?: string;
    marqoCategoryLabel?: string;
    marqoConfidence?: number;
  }>;
}

export async function runHsnSuggestion(
  products: HsnSuggestProduct[],
  apiBase: string = "",
): Promise<HsnSuggestResult> {
  const baseUrl = apiBase || getPrelistingApiBase();
  const resp = await fetch(`${baseUrl}/api/products/hsn-suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products }),
  });

  if (!resp.ok) {
    throw new Error(`HSN suggestion failed: ${resp.status} ${resp.statusText}`);
  }

  return resp.json();
}

/**
 * Refine a product's "Unit count/variation" check using the image-detected
 * product type from the HSN service. When CLIP identifies a single-SKU article
 * (e.g. cable, appliance, dispenser), the unit/variation requirement is not
 * applicable; when it identifies apparel/footwear/consumables, the requirement
 * stands. This is applied asynchronously after image analysis completes.
 */
export function applyUnitVariationContext(
  result: ValidationResult,
  unitVariation: "required" | "optional" | "single" | undefined,
  productTypeLabel: string | undefined,
): ValidationResult {
  const updated: ValidationResult = {
    ...result,
    checks: result.checks.map((c) => ({ ...c })),
  };
  const unitIdx = updated.checks.findIndex((c) => c.check === "Unit count/variation");
  if (unitIdx < 0) return updated;

  if (unitVariation === "single") {
    const hasUnitOrVariation =
      /\b(pack|set|pair|piece|pieces|pcs|count|pcs\.)\s+of\s+\d+\b/i.test(result.productName) ||
      /\b\d+\s*(ml|g|kg|l|oz|gm|mg|inch|inches|cm|mm|foot|feet|lt)\b/i.test(result.productName) ||
      /\b(color|colour|size|variant|shade)\b/i.test(result.productName);
    updated.checks[unitIdx] = {
      ...updated.checks[unitIdx],
      passed: true,
      decision: "PASS",
      message: hasUnitOrVariation
        ? "Unit count/variation present (single-SKU article)"
        : `Unit count/variation not applicable (${productTypeLabel || "single article"})`,
    };
  } else if (unitVariation === "required" && updated.checks[unitIdx].passed === false) {
    updated.checks[unitIdx] = {
      ...updated.checks[unitIdx],
      message: `No unit count (pack of N) or variation (color/size) found — required for ${productTypeLabel || "this product"}`,
    };
  }

  // Recalculate decision and score after refinement.
  const hasReject = updated.checks.some((c) => c.decision === "REJECT" && !c.passed);
  const hasFlag = updated.checks.some((c) => c.decision === "FLAG" && !c.passed);
  const total = updated.checks.length;
  const passedCount = updated.checks.filter((c) => c.passed).length;
  if (hasReject) {
    updated.decision = "REJECT";
    updated.score = Math.max(0, Math.round((passedCount / total) * 6));
  } else if (hasFlag && updated.decision !== "REJECT") {
    updated.decision = "FLAG";
    updated.score = Math.min(10, 7 + Math.round((passedCount / total) * 3));
  } else {
    updated.decision = "PASS";
    updated.score = 10;
  }
  return updated;
}

/**
 * Refine a product's "Title accuracy" check using CLIP's text + image
 * product-type classification from the HSN service. When CLIP's text encoder
 * (on the title) and CLIP's image encoder (on the product photos) disagree
 * about what the product is, the title is likely inaccurate.
 */
export function applyTitleAccuracyContext(
  result: ValidationResult,
  productType: string | undefined,
  productTypeLabel: string | undefined,
  titleProductType: string | undefined,
  titleProductTypeLabel: string | undefined,
  titleAccuracyStatus?: "match" | "mismatch" | "unknown",
): ValidationResult {
  if (!productTypeLabel && !titleProductTypeLabel) return result;

  // Raw internal category IDs (e.g. "cat_cat_022", "cat_034") carry no human
  // meaning and must not be surfaced as if they were a real product type.
  if (isRawTypeId(productTypeLabel || productType) || isRawTypeId(titleProductTypeLabel || titleProductType)) {
    return result;
  }

  const updated: ValidationResult = {
    ...result,
    checks: result.checks.map((c) => ({ ...c })),
  };
  const accIdx = updated.checks.findIndex((c) => c.check === "Title accuracy");
  if (accIdx < 0) return updated;

  // The backend decides via keyword taxonomy: title-only keywords vs full
  // title+description keywords. A flag fires ONLY on a genuine keyword
  // conflict ("mismatch"). CLIP image/text disagreement is intentionally NOT
  // used — it misreads generic product photos and titles.
  if (
    titleAccuracyStatus === "mismatch" &&
    productType &&
    titleProductType &&
    productType !== titleProductType
  ) {
    updated.checks[accIdx] = {
      ...updated.checks[accIdx],
      passed: false,
      decision: "FLAG",
      message: `Title mentions "${titleProductTypeLabel || titleProductType}" but the listing describes "${productTypeLabel || productType}"`,
    };
    // Recalculate
    const hasReject = updated.checks.some((c) => c.decision === "REJECT" && !c.passed);
    const hasFlag = updated.checks.some((c) => c.decision === "FLAG" && !c.passed);
    const total = updated.checks.length;
    const passedCount = updated.checks.filter((c) => c.passed).length;
    if (hasReject) {
      updated.decision = "REJECT";
      updated.score = Math.max(0, Math.round((passedCount / total) * 6));
    } else if (hasFlag && updated.decision !== "REJECT") {
      updated.decision = "FLAG";
      updated.score = Math.min(10, 7 + Math.round((passedCount / total) * 3));
    } else {
      updated.decision = "PASS";
      updated.score = 10;
    }
  }

  return updated;
}

/**
 * Refine a product's "Category" check from the HSN/Marqo category decision.
 * Adds a real check so it participates in score/decision and can be dismissed
 * like any other flag — a "needs-change" suggestion becomes a FLAG that the
 * user can dismiss, and export is not silently blocked by it.
 */

// Raw internal category/product-type IDs (e.g. "cat_cat_022", "cat_034") carry
// no human meaning and must never be surfaced to the user as a product type.
function isRawTypeId(t?: string): boolean {
  return !t || /^cat[_-]/i.test(t) || /^[a-z0-9]+_[a-z0-9]+_\d+$/i.test(t) || /^\d+$/.test(t);
}

export function applyCategoryContext(
  result: ValidationResult,
  categoryStatus: "confirmed" | "needs-change" | "unknown" | undefined,
  categoryLabel: string | undefined,
  originalLabel: string | undefined,
  marqoConfidence: number | undefined,
): ValidationResult {
  if (!categoryStatus || categoryStatus === "unknown") return result;
  // Don't surface raw internal category IDs (e.g. "cat_cat_002") as labels.
  if (isRawTypeId(categoryLabel) || isRawTypeId(originalLabel)) return result;
  const updated: ValidationResult = {
    ...result,
    checks: result.checks.map((c) => ({ ...c })),
  };
  const existing = updated.checks.findIndex((c) => c.check === "Category");
  const conf = marqoConfidence ? ` (${Math.round(marqoConfidence * 100)}%)` : "";
  if (categoryStatus === "confirmed") {
    const check = {
      check: "Category",
      passed: true,
      decision: "PASS" as Decision,
      message: `Category "${categoryLabel}" confirmed by image classification${conf}`,
    };
    if (existing >= 0) updated.checks[existing] = check;
    else updated.checks.push(check);
  } else {
    const check = {
      check: "Category",
      passed: false,
      decision: "FLAG" as Decision,
      message: `Category needs change: "${originalLabel || "text-based"}" → "${categoryLabel}" suggested by image classification${conf}`,
    };
    if (existing >= 0) updated.checks[existing] = check;
    else updated.checks.push(check);
  }

  const hasReject = updated.checks.some((c) => c.decision === "REJECT" && !c.passed);
  const hasFlag = updated.checks.some((c) => c.decision === "FLAG" && !c.passed);
  const total = updated.checks.length;
  const passedCount = updated.checks.filter((c) => c.passed).length;
  if (hasReject) {
    updated.decision = "REJECT";
    updated.score = Math.max(0, Math.round((passedCount / total) * 6));
  } else if (hasFlag && updated.decision !== "REJECT") {
    updated.decision = "FLAG";
    updated.score = Math.min(10, 7 + Math.round((passedCount / total) * 3));
  } else {
    updated.decision = "PASS";
    updated.score = 10;
  }
  return updated;
}
export function applyValidationHints(
  result: ValidationResult,
  hints: Record<string, boolean> | undefined,
  productType: string | undefined,
): ValidationResult {
  if (!hints || Object.keys(hints).length === 0) return result;
  const updated: ValidationResult = {
    ...result,
    checks: result.checks.map((c) => ({ ...c })),
  };

  for (const [checkName, passValue] of Object.entries(hints)) {
    if (!passValue && updated.checks.some((c) => c.check === "Product attributes")) {
      updated.checks = updated.checks.map((c) => {
        if (c.check === "Product attributes" && c.passed === false) {
          return { ...c, passed: true, decision: "PASS" as Decision, message: `Product attributes not applicable (${productType || "this product type"})` };
        }
        return c;
      });
    }
  }

  // Recalculate decision after modifying checks
  const hasReject = updated.checks.some((c) => c.decision === "REJECT" && !c.passed);
  const hasFlag = updated.checks.some((c) => c.decision === "FLAG" && !c.passed);
  const total = updated.checks.length;
  const passedCount = updated.checks.filter((c) => c.passed).length;
  if (hasReject) {
    updated.decision = "REJECT";
    updated.score = Math.max(0, Math.round((passedCount / total) * 6));
  } else if (hasFlag) {
    updated.decision = "FLAG";
    updated.score = Math.min(10, 7 + Math.round((passedCount / total) * 3));
  } else {
    updated.decision = "PASS";
    updated.score = 10;
  }

  return updated;
}


/**
 * Delete rows from a SheetJS worksheet in-place while preserving cell styles,
 * column widths and other sheet metadata. Row indices are 0-based sheet rows.
 */
function deleteSheetRowsPreserve(sheet: XLSX.WorkSheet, rowsToDelete: Set<number>): void {
  if (rowsToDelete.size === 0) return;
  const delSorted = [...rowsToDelete].sort((a, b) => a - b);
  const ref = sheet["!ref"];
  if (!ref) return;
  const range = XLSX.utils.decode_range(ref);
  const newCells: Record<string, XLSX.CellObject> = {};
  for (const key of Object.keys(sheet)) {
    if (key[0] === "!") continue;
    const addr = XLSX.utils.decode_cell(key);
    if (rowsToDelete.has(addr.r)) continue;
    const shift = delSorted.filter((r) => r < addr.r).length;
    const newAddr = XLSX.utils.encode_cell({ r: addr.r - shift, c: addr.c });
    newCells[newAddr] = sheet[key] as XLSX.CellObject;
  }
  for (const k of Object.keys(sheet)) if (k[0] !== "!") delete (sheet as Record<string, unknown>)[k];
  for (const [k, v] of Object.entries(newCells)) (sheet as Record<string, unknown>)[k] = v;
  const newMaxR = range.e.r - delSorted.length;
  sheet["!ref"] = XLSX.utils.encode_range({ s: { r: range.s.r, c: range.s.c }, e: { r: newMaxR, c: range.e.c } });
  if (sheet["!merges"]) {
    const merges = sheet["!merges"] as XLSX.Range[];
    const kept: XLSX.Range[] = [];
    for (const m of merges) {
      const overlapsDeleted = delSorted.some((r) => r >= m.s.r && r <= m.e.r);
      if (overlapsDeleted) continue;
      const shiftS = delSorted.filter((r) => r < m.s.r).length;
      // merges wholly after deleted block shift uniformly by shift before start
      if (shiftS > 0) { m.s.r -= shiftS; m.e.r -= shiftS; }
      kept.push(m);
    }
    if (kept.length) sheet["!merges"] = kept; else delete sheet["!merges"];
  }
  if (sheet["!rows"]) {
    const rows = sheet["!rows"] as unknown[];
    const newRows: unknown[] = [];
    for (let r = 0; r <= range.e.r; r++) {
      if (rowsToDelete.has(r)) continue;
      newRows.push(rows[r]);
    }
    sheet["!rows"] = newRows as never;
  }
}

/** Build a corrected Gajab Hub .xlsx from the original headers/rows, applying
 *  user-selected HSN codes (and optionally GST rate) into the Hsn / Tax columns.
 *  The original prefix row (above the header row) is preserved so the exported
 *  file keeps the Gajab Hub template layout and can be re-uploaded. */
export async function exportCorrectedSheet(
  file: File,
  corrections: Map<string, { hsn?: string; tax?: string }>,
  textCorrections?: Map<string, { title?: string; description?: string }>,
  imageCorrections?: Map<string, string[]>,
  overlayUrls?: Map<string, string>,
  skipSkus?: Set<string>,
  feedback?: Map<string, string>,
): Promise<number> {
  const ext = file.name.split(".").pop()?.toLowerCase();
  const isSpreadsheet = ext === "xlsx" || ext === "xls";
  const normalizedCorrections = new Map(
    [...corrections.entries()].map(([sku, fix]) => [normalizeSkuValue(sku), fix]),
  );
  const normalizedFeedback = feedback
    ? new Map([...feedback.entries()].map(([sku, msg]) => [normalizeSkuValue(sku), msg]))
    : new Map();
  const normalizedText = textCorrections
    ? new Map([...textCorrections.entries()].map(([sku, fix]) => [normalizeSkuValue(sku), fix]))
    : new Map();
  const normalizedOverlay = overlayUrls
    ? new Map([...overlayUrls.entries()].map(([sku, url]) => [normalizeSkuValue(sku), url]))
    : new Map();
  const normalizedImages = imageCorrections
    ? new Map([...imageCorrections.entries()].map(([sku, urls]) => [normalizeSkuValue(sku), urls]))
    : new Map();
  const normalizedSkip = new Set<string>();
  if (skipSkus) {
    for (const s of skipSkus) normalizedSkip.add(normalizeSkuValue(s));
  }
  let updatedCount = 0;

  // For spreadsheets, reopen the ORIGINAL uploaded bytes and patch only the
  // Hsn / Tax cells — everything else (layout, formatting, merged cells,
  // other data) stays exactly as uploaded.
  if (isSpreadsheet) {
    const data = await file.arrayBuffer();
    const wb = XLSX.read(data, { type: "array", cellStyles: true });
    const sheet = wb.Sheets[wb.SheetNames[0]];
    const jsonData = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
      header: 1,
      defval: "",
      blankrows: true,
    });

    // Always use feedback-aware export — Feedback column is always present
    // so the reviewer can see every flagged item at a glance.
    return exportWithFeedback(jsonData, {
      normalizedCorrections,
      normalizedText,
      normalizedImages,
      normalizedOverlay,
      normalizedSkip,
      normalizedFeedback,
    }, file);
  }

  // CSV fallback: rebuild the AOA (CSV has no formatting to preserve).
  const text = await file.text();
  const { headers, rows, prefixRow } = parseCsvFile(text, file.name);
  const hsnCol = headers.findIndex((h) => normalizeFieldName(h) === "hsn");
  const taxCol = headers.findIndex((h) => normalizeFieldName(h) === "tax");
  const skuCol = headers.findIndex((h) => normalizeFieldName(h) === "sku");
  const titleCol = headers.findIndex((h) => normalizeFieldName(h) === "product name");
  const descCol = headers.findIndex((h) => normalizeFieldName(h) === "description");
  const imgCols = Array.from({ length: 10 }, (_, i) => {
    const expected = `product image ${i + 1}`;
    return headers.findIndex((h) => normalizeFieldName(h) === expected);
  });

  const aoa: unknown[][] = [prefixRow.length > 0 ? ["Feedback", ...prefixRow] : ["Feedback"]];
  aoa.push(["Feedback", ...headers]);
  for (const row of rows) {
    const out: unknown[] = headers.map((h) => row[h] ?? "");
    if (skuCol >= 0) {
      const sku = normalizeSkuValue(row[headers[skuCol]]);
      if (normalizedSkip.has(sku)) continue;
      const fix = normalizedCorrections.get(sku);
      const textFix = normalizedText.get(sku);
      const imgUrls = normalizedImages.get(sku);
      const hasFix = Boolean(
        fix || (textFix && (textFix.title || textFix.description)) || imgUrls,
      );
      if (hasFix) {
        if (fix) {
          if (hsnCol >= 0 && fix.hsn) out[hsnCol] = fix.hsn;
          if (taxCol >= 0 && fix.tax) out[taxCol] = fix.tax;
        }
        updatedCount++;
      }
      if (textFix) {
        if (titleCol >= 0 && textFix.title) out[titleCol] = textFix.title;
        if (descCol >= 0 && textFix.description) out[descCol] = textFix.description;
      }
      if (imgUrls) {
        for (let colIdx = 0; colIdx < imgCols.length; colIdx++) {
          if (imgCols[colIdx] >= 0) out[imgCols[colIdx]] = imgUrls[colIdx] || "";
        }
      }
      // Apply overlay image URL to next available image column
      const overlayUrl = normalizedOverlay.get(sku);
      if (overlayUrl) {
        let existingImageCount = 0;
        for (let ci = 0; ci < imgCols.length; ci++) {
          if (imgCols[ci] >= 0 && out[imgCols[ci]] && String(out[imgCols[ci]]).trim()) {
            existingImageCount++;
          }
        }
        const overlayColIdx = existingImageCount;
        if (overlayColIdx < imgCols.length && imgCols[overlayColIdx] >= 0) {
          out[imgCols[overlayColIdx]] = overlayUrl;
        }
      }
    }
    out.unshift(skuCol >= 0 ? (normalizedFeedback.get(normalizeSkuValue(row[headers[skuCol]])) ?? "") : "");
    aoa.push(out);
  }

  const ws = XLSX.utils.aoa_to_sheet(aoa);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
  const base = file.name.replace(/\.(csv)$/i, "");
  XLSX.writeFile(wb, `${base}-corrected.xlsx`);
  return updatedCount;
}

interface ExportFeedbackCtx {
  normalizedCorrections: Map<string, { hsn?: string; tax?: string }>;
  normalizedText: Map<string, { title?: string; description?: string }>;
  normalizedImages: Map<string, string[]>;
  normalizedOverlay: Map<string, string>;
  normalizedSkip: Set<string>;
  normalizedFeedback: Map<string, string>;
}

/**
 * Feedback-aware spreadsheet export. Rebuilds the sheet as an AOA with a
 * "Feedback" column prepended, applies all corrections, drops duplicate rows,
 * and red-highlights every row that has feedback so the user can spot them.
 */
async function exportWithFeedback(
  jsonData: unknown[][],
  ctx: ExportFeedbackCtx,
  file: File,
): number {
  const {
    normalizedCorrections,
    normalizedText,
    normalizedImages,
    normalizedOverlay,
    normalizedSkip,
    normalizedFeedback,
  } = ctx;

  const row0Str = ((jsonData[0] as unknown[]) ?? []).join(" ").toLowerCase();
  const hasPrefixRow = !row0Str.includes("sku") && !row0Str.includes("product name");
  const headerIdx = hasPrefixRow ? 1 : 0;
  const dataStartIdx = hasPrefixRow ? 2 : 1;
  const headerRow = (jsonData[headerIdx] as unknown[]) ?? [];
  const headers = headerRow.map((h) => String(h ?? "").trim());
  const skuCol = headers.findIndex((h) => normalizeFieldName(h) === "sku");
  const hsnCol = headers.findIndex((h) => normalizeFieldName(h) === "hsn");
  const taxCol = headers.findIndex((h) => normalizeFieldName(h) === "tax");
  const titleCol = headers.findIndex((h) => normalizeFieldName(h) === "product name");
  const descCol = headers.findIndex((h) => normalizeFieldName(h) === "description");
  const imgCols = Array.from({ length: 10 }, (_, i) => {
    const expected = `product image ${i + 1}`;
    return headers.findIndex((h) => normalizeFieldName(h) === expected);
  });

  const aoa: unknown[][] = [];
  if (hasPrefixRow) aoa.push(["Feedback", ...((jsonData[0] as unknown[]) ?? [])]);
  aoa.push(["Feedback", ...headers]);

  const highlightedRows: number[] = [];
  let updatedCount = 0;

  for (let i = dataStartIdx; i < jsonData.length; i++) {
    const raw = jsonData[i] as unknown[];
    if (!raw || raw.every((c) => !c || String(c).trim() === "")) continue;
    const sku = skuCol >= 0 ? normalizeSkuValue(raw[skuCol]) : "";
    if (normalizedSkip.has(sku)) continue;

    const out: unknown[] = [...raw];
    const fix = normalizedCorrections.get(sku);
    const textFix = normalizedText.get(sku);
    const imgUrls = normalizedImages.get(sku);
    const hasFix = Boolean(
      fix || (textFix && (textFix.title || textFix.description)) || imgUrls,
    );
    if (hasFix) {
      if (fix) {
        if (hsnCol >= 0 && fix.hsn) out[hsnCol] = fix.hsn;
        if (taxCol >= 0 && fix.tax) out[taxCol] = fix.tax;
      }
      updatedCount++;
    }
    if (textFix) {
      if (titleCol >= 0 && textFix.title) out[titleCol] = textFix.title;
      if (descCol >= 0 && textFix.description) out[descCol] = textFix.description;
    }
    if (imgUrls) {
      for (let colIdx = 0; colIdx < imgCols.length; colIdx++) {
        if (imgCols[colIdx] >= 0) out[imgCols[colIdx]] = imgUrls[colIdx] || "";
      }
    }
    const overlayUrl = normalizedOverlay.get(sku);
    if (overlayUrl) {
      let existingImageCount = 0;
      for (let ci = 0; ci < imgCols.length; ci++) {
        if (imgCols[ci] >= 0 && out[imgCols[ci]] && String(out[imgCols[ci]]).trim()) {
          existingImageCount++;
        }
      }
      const overlayColIdx = existingImageCount;
      if (overlayColIdx < imgCols.length && imgCols[overlayColIdx] >= 0) {
        out[imgCols[overlayColIdx]] = overlayUrl;
      }
    }

    const feedback = normalizedFeedback.get(sku);
    out.unshift(feedback ?? "");
    if (feedback) highlightedRows.push(aoa.length);
    aoa.push(out);
  }

  // Preserve column widths from original sheet — prepend Feedback column width.
  const origBuf = await file.arrayBuffer();
  const origWb = XLSX.read(origBuf, { type: "array", cellStyles: true });
  const origSheet = origWb.Sheets[origWb.SheetNames[0]] as XLSX.WorkSheet;

  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet(aoa);
  // Prepend feedback column width (18ch) + preserve original widths.
  ws["!cols"] = [{ wch: 30 }, ...((origSheet["!cols"] as unknown[]) ?? [])] as never;
  // Carry over autofilter if any (shifted by 1 col) — best-effort.
  if (origSheet["!autofilter"]) {
    const af = origSheet["!autofilter"] as { ref: string };
    try {
      const range = XLSX.utils.decode_range(af.ref);
      range.s.c += 1; range.e.c += 1;
      ws["!autofilter"] = { ref: XLSX.utils.encode_range(range) } as never;
    } catch { /* ignore */ }
  }
  // Red-fill highlighted rows so the reviewer can spot products with feedback.
  ws["!rows"] = aoa.map((_, ri) =>
    highlightedRows.includes(ri)
      ? { hpt: 18 }
      : undefined,
  );
  for (const ri of highlightedRows) {
    for (let c = 0; c < aoa[ri].length; c++) {
      const addr = XLSX.utils.encode_cell({ r: ri, c });
      const cell = ws[addr] || { t: "s", v: "" };
      cell.s = { fill: { fgColor: { rgb: "FFC7CE" }, patternType: "solid" } };
      ws[addr] = cell;
    }
  }
  // Bold the Feedback header cell.
  const fbHdr = ws[XLSX.utils.encode_cell({ r: hasPrefixRow ? 1 : 0, c: 0 })];
  if (fbHdr) fbHdr.s = { ...(fbHdr.s || {}), font: { bold: true } };
  XLSX.utils.book_append_sheet(wb, ws, origWb.SheetNames[0] || "Sheet1");
  // Preserve any additional sheets from original workbook (instructions, etc.)
  for (let i = 1; i < origWb.SheetNames.length; i++) {
    const name = origWb.SheetNames[i];
    XLSX.utils.book_append_sheet(wb, origWb.Sheets[name], name);
  }
  const base = file.name.replace(/\.(xlsx|xls)$/i, "");
  XLSX.writeFile(wb, `${base}-corrected.xlsx`, { cellStyles: true });
  return updatedCount;
}


/**
 * Export the uploaded sheet with duplicate rows REMOVED entirely (not just
 * emptied). Rebuilds the sheet as AOA, dropping any row whose SKU is in
 * skipSkus. Works for both XLSX and CSV.
 */
export async function exportDeduplicatedSheet(
  file: File,
  skipSkus?: Set<string>,
): Promise<{ totalRows: number; keptRows: number; removedRows: number }> {
  const ext = file.name.split(".").pop()?.toLowerCase();
  const isSpreadsheet = ext === "xlsx" || ext === "xls";
  const normalizedSkip = new Set<string>();
  if (skipSkus) {
    for (const s of skipSkus) normalizedSkip.add(normalizeSkuValue(s));
  }

  let aoa: unknown[][];
  let prefixRow: unknown[] = [];

  if (isSpreadsheet) {
    const data = await file.arrayBuffer();
    const wb = XLSX.read(data, { type: "array" });
    const sheet = wb.Sheets[wb.SheetNames[0]];
    const jsonData = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
      header: 1,
      defval: "",
      blankrows: true,
    });

    if (jsonData.length < 2) {
      XLSX.writeFile(wb, file.name.replace(/\.(xlsx|xls)$/i, "") + "-deduplicated.xlsx");
      return { totalRows: 0, keptRows: 0, removedRows: 0 };
    }

    const row0Str = (jsonData[0] ?? []).join(" ").toLowerCase();
    const hasPrefixRow = !row0Str.includes("sku") && !row0Str.includes("product name");
    const headerIdx = hasPrefixRow ? 1 : 0;
    const dataStartIdx = hasPrefixRow ? 2 : 1;
    prefixRow = hasPrefixRow ? (jsonData[0] as unknown[]) : [];
    const headerRow = (jsonData[headerIdx] as unknown[]) ?? [];
    const headers = headerRow.map((h) => String(h ?? "").trim());
    const skuCol = headers.findIndex((h) => normalizeFieldName(h) === "sku");

    aoa = [prefixRow, headers];
    let removed = 0;
    for (let i = dataStartIdx; i < jsonData.length; i++) {
      const raw = jsonData[i] as unknown[];
      if (!raw || raw.every((c) => !c || String(c).trim() === "")) continue;
      const sku = skuCol >= 0 ? normalizeSkuValue(raw[skuCol]) : "";
      if (normalizedSkip.has(sku)) {
        removed++;
        continue;
      }
      aoa.push(raw);
    }

    const ws = XLSX.utils.aoa_to_sheet(aoa);
    const outWb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(outWb, ws, "Sheet1");
    XLSX.writeFile(outWb, file.name.replace(/\.(xlsx|xls)$/i, "") + "-deduplicated.xlsx");
    return { totalRows: jsonData.length - dataStartIdx, keptRows: aoa.length - dataStartIdx, removedRows: removed };
  }

  // CSV path
  const text = await file.text();
  const { headers, rows, prefixRow: pr } = parseCsvFile(text, file.name);
  const skuCol = headers.findIndex((h) => normalizeFieldName(h) === "sku");

  aoa = [pr.length > 0 ? pr : [""], headers];
  let removed = 0;
  for (const row of rows) {
    const out: unknown[] = headers.map((h) => row[h] ?? "");
    const sku = skuCol >= 0 ? normalizeSkuValue(row[headers[skuCol]]) : "";
    if (normalizedSkip.has(sku)) {
      removed++;
      continue;
    }
    aoa.push(out);
  }

  const ws = XLSX.utils.aoa_to_sheet(aoa);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
  XLSX.writeFile(wb, file.name.replace(/\.csv$/i, "") + "-deduplicated.xlsx");
  return { totalRows: rows.length, keptRows: rows.length - removed, removedRows: removed };
}
