import { supabase } from "./supabase";
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

function mapRow(row: any): Product {
  return {
    id: row.id,
    name: row.name,
    price: row.price ?? null,
    imageUrl: row.image_url ?? null,
    url: row.url,
    category: row.category ?? null,
  };
}

function normalizeText(text: string): string {
  return text.toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
}

function searchProducts(allProducts: Product[], searchQuery: string): Product[] {
  const normalizedQuery = normalizeText(searchQuery);
  if (!normalizedQuery) return allProducts;
  
  return allProducts.filter(product => {
    const normalizedName = normalizeText(product.name);
    return normalizedName.includes(normalizedQuery);
  });
}

export async function scrapeProducts(forceRefresh = false): Promise<Product[]> {
  // Supabase REST caps at 1000 rows per request — paginate to get all
  const allRows: any[] = [];
  const PAGE = 1000;
  let from = 0;

  while (true) {
    const { data, error } = await supabase
      .from("products")
      .select("*")
      .order("created_at", { ascending: false })
      .range(from, from + PAGE - 1);

    if (error) {
      logger.error({ err: error.message }, "Failed to fetch products from Supabase");
      throw new Error(error.message);
    }

    const rows = data ?? [];
    allRows.push(...rows);

    if (rows.length < PAGE) break; // last page
    from += PAGE;
  }

  const products = allRows.map(mapRow);
  logger.info({ count: products.length }, "Fetched products from Supabase");
  return products;
}

export function getPaginatedProducts(
  allProducts: Product[],
  page: number,
  searchQuery?: string,
): ScrapedPage {
  let filteredProducts = allProducts;
  
  if (searchQuery) {
    filteredProducts = searchProducts(allProducts, searchQuery);
  }
  
  const total = filteredProducts.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  return {
    products: filteredProducts.slice(start, start + PAGE_SIZE),
    total,
    totalPages,
  };
}

// No-op warmUp kept so index.ts import doesn't break
export async function warmUp(): Promise<void> {
  try {
    const products = await scrapeProducts();
    logger.info({ count: products.length }, "Supabase products ready");
  } catch (err: any) {
    logger.warn({ err: err?.message }, "warmUp: failed to reach Supabase");
  }
}
