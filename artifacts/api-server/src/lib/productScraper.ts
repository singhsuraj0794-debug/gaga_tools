import { execFile } from "child_process";
import { promisify } from "util";
import puppeteer from "puppeteer-core";
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
const IMAGE_CDN = "https://resize.gajab.com";

// Key for gajab.com internal gateway — must be set via GAJAB_API_KEY env var
const GATEWAY_KEY = process.env.GAJAB_API_KEY;
if (!GATEWAY_KEY) {
  // Log once at module load so operators know to set the env var
  console.error("[productScraper] GAJAB_API_KEY is not set — product fetch will fail. Set this env var.");
}

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

// ─── Puppeteer-based crawl (primary) ─────────────────────────────────────

async function scrapeWithPuppeteer(): Promise<Product[]> {
  // Resolve chromium from common system paths in the Replit/NixOS environment
  const executablePath = (() => {
    const candidates = [
      process.env.PUPPETEER_EXECUTABLE_PATH,
      "/nix/store/chromium/bin/chromium",
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser",
      "/usr/bin/google-chrome",
    ].filter(Boolean) as string[];
    const fs = require("fs") as typeof import("fs");
    for (const p of candidates) {
      try { if (fs.existsSync(p)) return p; } catch {}
    }
    return null;
  })();

  if (!executablePath) {
    throw new Error("No chromium executable found — cannot use Puppeteer scraper");
  }

  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
    ],
  });

  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    );

    logger.info("Puppeteer: navigating to gajab.com product list");
    await page.goto("https://gajab.com/product-list/all", {
      waitUntil: "networkidle2",
      timeout: 30000,
    });

    // Extract product data from the page DOM.
    // page.evaluate() runs serialized in the browser context; use Function
    // constructor to avoid TypeScript complaining about missing DOM types in
    // the Node.js compilation target.
    type RawProduct = { name: string; price: string | null; imageUrl: string | null; url: string };
    const products: RawProduct[] = await page.evaluate(
      // eslint-disable-next-line @typescript-eslint/no-implied-eval
      new Function(`
        const items = [];
        const cards = document.querySelectorAll(
          "[class*='product-card'], [class*='ProductCard'], [class*='product_card'], .product-item, [data-testid*='product']"
        );
        cards.forEach(card => {
          const name = (card.querySelector("[class*='name'], [class*='title'], h3, h2") || {}).textContent || "";
          const priceEl = card.querySelector("[class*='price'], [class*='Price']");
          const price = priceEl ? priceEl.textContent.trim() : null;
          const img = card.querySelector("img");
          const imageUrl = img ? (img.src || img.getAttribute("data-src")) : null;
          const link = card.querySelector("a");
          const href = link ? link.href : "";
          const url = href.startsWith("http") ? href : href ? "https://gajab.com" + href : "https://gajab.com/product-list/all";
          if (name.trim()) items.push({ name: name.trim(), price, imageUrl, url });
        });
        return items;
      `) as () => RawProduct[],
    );

    if (products.length === 0) {
      throw new Error("Puppeteer: no product cards found in DOM — page structure may have changed");
    }

    logger.info({ count: products.length }, "Puppeteer crawl succeeded");

    return products.map((p, i): Product => ({
      id: `gajab-puppeteer-${i}-${Buffer.from(p.name).toString("base64").slice(0, 8)}`,
      name: p.name,
      price: p.price || null,
      imageUrl: p.imageUrl || null,
      url: p.url,
      category: "general",
    }));
  } finally {
    await browser.close();
  }
}

async function fetchProducts(): Promise<Product[]> {
  // Primary: Puppeteer headless crawl of gajab.com
  try {
    const products = await scrapeWithPuppeteer();
    if (products.length > 0) {
      logger.info({ count: products.length }, "Fetched products via Puppeteer");
      return products;
    }
  } catch (err: any) {
    logger.warn(
      { msg: err?.message?.slice(0, 120) },
      "Puppeteer scrape failed — falling back to gateway API",
    );
  }

  // Fallback: internal gateway API via curl (bypasses Node.js TLS fingerprinting)
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const items = await gatewayGet({ page: 1, limit: 50 });
      const products = dedupeByProductId(mapItems(items));
      logger.info({ count: products.length, attempt }, "Fetched products via gateway API (fallback)");
      return products;
    } catch (err: any) {
      logger.warn({ attempt, msg: err?.message?.slice(0, 80) }, "Gateway API fetch failed");
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
