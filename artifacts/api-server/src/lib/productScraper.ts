import axios from "axios";
import { logger } from "./logger";

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

const PAGE_SIZE = 20;
let cachedProducts: Product[] | null = null;
let lastScrapeTime = 0;
const CACHE_TTL_MS = 10 * 60 * 1000;

const GATEWAY_BASE = "https://gatewayservice.gajab.com";
const GATEWAY_KEY = "8097571064818418";
const IMAGE_CDN = "https://resize.gajab.com";

const gatewayClient = axios.create({
  baseURL: GATEWAY_BASE,
  headers: {
    "Content-type": "application/json",
    key: GATEWAY_KEY,
    "Cache-Control": "no-cache",
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    Origin: "https://gajab.com",
    Referer: "https://gajab.com/product-list/all",
  },
  timeout: 8000,
});

function buildImageUrl(containerName: string | null, image: string | null): string | null {
  if (!image) return null;
  if (image.startsWith("http")) return image;
  const container = containerName || "";
  return `${IMAGE_CDN}/${container}${image}?height=300&width=300`;
}

function buildProductUrl(slug: string): string {
  return `https://gajab.com/product/${slug}`;
}

function formatPrice(price: number | string | null, mrp: number | string | null): string | null {
  if (price != null && price !== "") {
    return `₹${Number(price).toLocaleString("en-IN")}`;
  }
  if (mrp != null && mrp !== "") {
    return `₹${Number(mrp).toLocaleString("en-IN")}`;
  }
  return null;
}

async function fetchPage(page: number): Promise<{ products: Product[]; total: number }> {
  const resp = await gatewayClient.get("/product/api/list/custom-product-list", {
    params: { page, limit: PAGE_SIZE },
  });

  const data = resp.data;
  const items: any[] = Array.isArray(data?.data) ? data.data : [];

  const products: Product[] = items.map((item: any): Product => {
    const id = `gajab-${item.productId || item.skuId || item.itemId || Math.random()}`;
    const slug = item.productSlug || "";
    return {
      id,
      name: item.productName || item.variantName || "Unknown Product",
      price: formatPrice(item.price, item.mrpPrice),
      imageUrl: buildImageUrl(item.containerName, item.image),
      url: slug ? buildProductUrl(slug) : "https://gajab.com/product-list/all",
      category: item.categorySlug || null,
    };
  });

  return { products, total: items.length };
}

export async function scrapeProducts(forceRefresh = false): Promise<Product[]> {
  const now = Date.now();
  if (!forceRefresh && cachedProducts && now - lastScrapeTime < CACHE_TTL_MS) {
    logger.info({ count: cachedProducts.length }, "Returning cached products");
    return cachedProducts;
  }

  logger.info("Fetching products from gajab.com gateway API");

  try {
    const allProducts: Product[] = [];
    const maxPages = 5;

    for (let page = 1; page <= maxPages; page++) {
      try {
        const { products, total } = await fetchPage(page);
        allProducts.push(...products);
        if (products.length < PAGE_SIZE) break;
      } catch (err) {
        logger.warn({ page, err }, "Failed to fetch page, stopping");
        break;
      }
    }

    const seen = new Set<string>();
    const unique = allProducts.filter((p) => {
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });

    if (unique.length > 0) {
      cachedProducts = unique;
      lastScrapeTime = Date.now();
    }
    logger.info({ count: unique.length }, "Product fetch complete");
    return unique;
  } catch (err) {
    logger.error({ err }, "Product fetch failed");
    throw err;
  }
}

export function getPaginatedProducts(
  allProducts: Product[],
  page: number,
): ScrapedPage {
  const total = allProducts.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const products = allProducts.slice(start, start + PAGE_SIZE);
  return { products, total, totalPages };
}
