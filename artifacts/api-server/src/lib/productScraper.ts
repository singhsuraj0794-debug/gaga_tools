import { execFile } from "child_process";
import { promisify } from "util";
import { logger } from "./logger";

const execFileAsync = promisify(execFile);

export interface Product {
  id: string;
  name: string;
  price: string | null;
  imageUrl: string | null;
  url: string;
  category: string | null;
}

interface ScrapedPage {
  products: Product[];
  totalPages: number;
  total: number;
}

const PAGE_SIZE = 5;
let cachedProducts: Product[] = [];
let lastScrapeTime = 0;
const CACHE_TTL_MS = 15 * 60 * 1000;

const GATEWAY_BASE = "https://gatewayservice.gajab.com";
const GATEWAY_KEY = "8097571064818418";
const IMAGE_CDN = "https://resize.gajab.com";

function buildImageUrl(containerName: string | null, image: string | null): string | null {
  if (!image) return null;
  if (image.startsWith("http")) return image;
  return `${IMAGE_CDN}/${containerName || ""}${image}?height=300&width=300`;
}

function formatPrice(price: any, mrp: any): string | null {
  const v = price != null && price !== "" ? price : mrp;
  if (v != null && v !== "") return `₹${Number(v).toLocaleString("en-IN")}`;
  return null;
}

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function dedupeByProductId(products: Product[]): Product[] {
  const seen = new Set<string>();
  return products.filter((p) => {
    if (seen.has(p.id)) return false;
    seen.add(p.id);
    return true;
  });
}

function mapItems(items: any[]): Product[] {
  return items.map((item: any): Product => ({
    id: `gajab-${item.productId || item.skuId || Math.random()}`,
    name: item.productName || item.variantName || "Unknown Product",
    price: formatPrice(item.price, item.mrpPrice),
    imageUrl: buildImageUrl(item.containerName, item.image),
    url: item.productSlug
      ? `https://gajab.com/product/${item.productSlug}`
      : "https://gajab.com/product-list/all",
    category: item.categorySlug || item.categoryName || "general",
  }));
}

// Use curl to bypass Node.js TLS fingerprinting issues with the gateway
async function gatewayGet(params: Record<string, string | number>): Promise<any[]> {
  const qs = Object.entries(params)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");
  const url = `${GATEWAY_BASE}/product/api/list/custom-product-list?${qs}`;

  const { stdout } = await execFileAsync("curl", [
    "-s",
    "--max-time", "15",
    url,
    "-H", `key: ${GATEWAY_KEY}`,
    "-H", "Origin: https://gajab.com",
    "-H", "Referer: https://gajab.com/",
    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
  ]);

  const data = JSON.parse(stdout);
  return Array.isArray(data?.data) ? data.data : [];
}

async function fetchProducts(): Promise<Product[]> {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const items = await gatewayGet({ page: 1, limit: 50 });
      const products = dedupeByProductId(mapItems(items));
      logger.info({ count: products.length, attempt }, "Fetched products from gajab.com");
      return products;
    } catch (err: any) {
      logger.warn({ attempt, msg: err?.message?.slice(0, 80) }, "Product fetch failed");
      if (attempt < 3) await new Promise((r) => setTimeout(r, 2000 * attempt));
    }
  }
  return cachedProducts; // return whatever we have on full failure
}

// Called at server startup: fetch products and populate cache
export async function warmUp(): Promise<void> {
  const products = await fetchProducts();
  if (products.length > 0) {
    cachedProducts = products;
    lastScrapeTime = Date.now();
    logger.info({ count: cachedProducts.length }, "Cache warmed — products ready");
  } else {
    logger.warn("warmUp: no products fetched, cache empty");
  }
}

export async function scrapeProducts(forceRefresh = false): Promise<Product[]> {
  const now = Date.now();
  const cacheValid = cachedProducts.length > 0 && now - lastScrapeTime < CACHE_TTL_MS;

  if (!forceRefresh && cacheValid) {
    logger.info({ count: cachedProducts.length }, "Returning cached products");
    return cachedProducts;
  }

  const products = await fetchProducts();
  if (products.length > 0) {
    cachedProducts = products;
    lastScrapeTime = Date.now();
  }
  return cachedProducts;
}

export function getPaginatedProducts(
  allProducts: Product[],
  page: number,
  randomize = false,
): ScrapedPage {
  const pool = randomize ? shuffle(allProducts) : allProducts;
  const total = pool.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  return { products: pool.slice(start, start + PAGE_SIZE), total, totalPages };
}
