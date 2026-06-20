/**
 * seedProducts.ts
 *
 * Scrapes products from gajab.com via the gateway API (curl-based to bypass
 * TLS fingerprinting) and upserts them into the Supabase `products` table.
 *
 * Usage:
 *   SUPABASE_URL=https://... SUPABASE_KEY=sb_publishable_... \
 *     node --import tsx/esm ./src/seedProducts.ts
 *
 * Or via the helper script at the repo root:
 *   ./scripts/seed.sh
 */

import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

// ─── Config ───────────────────────────────────────────────────────────────

const SUPABASE_URL = process.env.SUPABASE_URL ?? "https://okxyskmjsmtykblrtmyi.supabase.co";
const SUPABASE_KEY =
  process.env.SUPABASE_KEY ?? "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp";

const GATEWAY_BASE = "https://gatewayservice.gajab.com";
const GATEWAY_KEY = "8097571064818418";
const IMAGE_CDN = "https://resize.gajab.com";

// Single large request — server caps at ~2413 rows, giving ~1732 unique products
const FETCH_LIMIT = 5000;

// ─── Types ────────────────────────────────────────────────────────────────

interface Product {
  id: string;
  name: string;
  price: string | null;
  image_url: string | null;
  url: string;
  category: string | null;
}

// ─── Gateway helpers ──────────────────────────────────────────────────────

function buildImageUrl(containerName: string | null, image: string | null): string | null {
  if (!image) return null;
  if (image.startsWith("http")) return image;
  return `${IMAGE_CDN}/${containerName ?? ""}${image}?height=300&width=300`;
}

function formatPrice(price: any, mrp: any): string | null {
  const v = price != null && price !== "" ? price : mrp;
  if (v != null && v !== "") return `₹${Number(v).toLocaleString("en-IN")}`;
  return null;
}

function mapItems(items: any[]): Product[] {
  return items.map((item: any): Product => ({
    id: `gajab-${item.productId || item.skuId || Math.random()}`,
    name: item.productName || item.variantName || "Unknown Product",
    price: formatPrice(item.price, item.mrpPrice),
    image_url: buildImageUrl(item.containerName, item.image),
    url: item.productSlug && item.itemId
      ? `https://gajab.com/product-detail/${item.productSlug}/${item.itemId}`
      : "https://gajab.com/product-list/all",
    category: item.categorySlug || item.categoryName || null,
  }));
}

async function gatewayFetch(limit: number): Promise<any[]> {
  const url = `${GATEWAY_BASE}/product/api/list/custom-product-list?page=1&limit=${limit}`;
  console.log(`  → GET ${url}`);

  const { stdout } = await execFileAsync("curl", [
    "-s",
    "--max-time", "30",
    url,
    "-H", `key: ${GATEWAY_KEY}`,
    "-H", "Origin: https://gajab.com",
    "-H", "Referer: https://gajab.com/",
    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
  ], { maxBuffer: 50 * 1024 * 1024 }); // 50 MB buffer

  let data: any;
  try {
    data = JSON.parse(stdout);
  } catch {
    console.error("  ✗ Invalid JSON response");
    console.error("  Raw:", stdout.slice(0, 300));
    return [];
  }

  if (!Array.isArray(data?.data)) {
    console.warn(`  ✗ Unexpected shape — keys: ${Object.keys(data ?? {}).join(", ")}`);
    return [];
  }

  return data.data;
}

async function fetchAllProducts(): Promise<Product[]> {
  const items = await gatewayFetch(FETCH_LIMIT);
  console.log(`  ✓ Got ${items.length} rows from gateway`);

  const seen = new Set<string>();
  const all: Product[] = [];
  for (const p of mapItems(items)) {
    if (!seen.has(p.id)) {
      seen.add(p.id);
      all.push(p);
    }
  }
  console.log(`  ✓ ${all.length} unique products after dedup`);
  return all;
}

// ─── Supabase upsert ──────────────────────────────────────────────────────

async function upsertProducts(products: Product[]): Promise<void> {
  const url = `${SUPABASE_URL}/rest/v1/products`;

  // Upsert in batches of 100
  const BATCH = 100;
  for (let i = 0; i < products.length; i += BATCH) {
    const batch = products.slice(i, i + BATCH);
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_KEY,
        "Authorization": `Bearer ${SUPABASE_KEY}`,
        "Prefer": "resolution=merge-duplicates",
      },
      body: JSON.stringify(batch),
    });

    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Supabase upsert failed (${res.status}): ${body}`);
    }

    console.log(`  ✓ Upserted batch ${Math.floor(i / BATCH) + 1} (${batch.length} rows)`);
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────

async function main() {
  console.log("=== gajab.com → Supabase seed script ===\n");

  console.log("1. Fetching products from gajab.com gateway...");
  const products = await fetchAllProducts();

  if (products.length === 0) {
    console.error("\n✗ No products fetched. Check gateway connectivity.");
    process.exit(1);
  }

  console.log(`\n   Total unique products: ${products.length}`);

  console.log("\n2. Upserting into Supabase...");
  await upsertProducts(products);

  console.log(`\n✓ Done! ${products.length} products seeded into Supabase.`);
}

main().catch((err) => {
  console.error("\n✗ Fatal error:", err.message);
  process.exit(1);
});
