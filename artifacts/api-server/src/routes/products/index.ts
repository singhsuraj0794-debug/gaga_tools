import { Router, type IRouter } from "express";
import { scrapeProducts, getPaginatedProducts } from "../../lib/productScraper";
import { ScrapeProductsQueryParams } from "@workspace/api-zod";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { logger } from "../../lib/logger";
import { existsSync } from "node:fs";
import { getSupabase } from "../../lib/supabase";

const __filename = fileURLToPath(import.meta.url);
const __parentDirname = path.dirname(__filename);

function resolveScript(name: string): string {
  const localPath = path.resolve(__parentDirname, name);
  if (existsSync(localPath)) return localPath;
  const tmpPath = `/tmp/${name}`;
  return tmpPath;
}

const execFileAsync = promisify(execFile);

const PROXIED_IMAGE_RE = /^https:\/\/resize\.gajab\.com\/V[^/]+\/(https?:\/\/.*)/;

function cleanImageUrl(url: string | null): string | null {
  if (!url) return null;
  const match = url.match(PROXIED_IMAGE_RE);
  return match ? match[1] : url;
}

const router: IRouter = Router();

router.get("/products", async (req, res): Promise<void> => {
  const parsed = ScrapeProductsQueryParams.safeParse(req.query);
  const page = parsed.success ? (parsed.data.page ?? 1) : 1;
  const refresh = parsed.success ? (parsed.data.refresh ?? false) : false;
  const search = typeof req.query.search === "string" ? req.query.search : undefined;

  try {
    const allProducts = await scrapeProducts(refresh);
    const { products, total, totalPages } = getPaginatedProducts(allProducts, page, search);
    res.json({ products, total, page, totalPages });
  } catch (err: any) {
    req.log.error({ err }, "Failed to scrape products");
    res.status(500).json({ error: "Failed to scrape products: " + (err?.message || String(err)) });
  }
});

router.post("/products/sync", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    // Fetch existing Supabase product URLs to avoid duplicates
    const existingUrls = new Set<string>();
    {
      let offset = 0;
      const PAGE_LIMIT = 1000;
      while (true) {
        const { data: page, error: pageError } = await supabase
          .from("products")
          .select("url")
          .range(offset, offset + PAGE_LIMIT - 1);
        if (pageError) throw pageError;
        if (!page || page.length === 0) break;
        for (const p of page) {
          if (p.url) existingUrls.add(p.url);
        }
        offset += PAGE_LIMIT;
      }
    }

    const products: { id: string; name: string; url: string; image_url: string | null; price: null; category: null }[] = [];
    const seen = new Set<string>();

    for (let batch = 0; batch < 50; batch += 2) {
      const batchPromises: Promise<{ i: number; stdout: string } | null>[] = [];
      for (let i = batch + 1; i <= batch + 2 && i <= 50; i++) {
        const idx = i;
        const from = (idx - 1) * 500 + 1;
        const to = idx * 500;
        const sitemapUrl = `https://gajab.com/sitemap_products_${idx}.xml?from=${from}&to=${to}`;
        batchPromises.push(
          execFileAsync("curl", [
            "-s", "--max-time", "30", sitemapUrl,
            "-H", "User-Agent: Mozilla/5.0",
          ], { maxBuffer: 10 * 1024 * 1024 })
            .then(({ stdout }) => ({ i: idx, stdout }))
            .catch(() => null)
        );
      }
      const results = await Promise.all(batchPromises);
      let foundEmpty = false;
      for (const r of results) {
        if (!r) continue;
        if (!r.stdout.includes("<loc>")) { foundEmpty = true; continue; }

        const urlMatches = [...r.stdout.matchAll(/<loc>([^<]+)<\/loc>/g)];
        const imageMatches = [...r.stdout.matchAll(/<image:loc>([^<]+)<\/image:loc>/g)];
        const titleMatches = [...r.stdout.matchAll(/<image:title>([^<]*)<\/image:title>/g)];

        for (let j = 0; j < urlMatches.length; j++) {
          const loc = urlMatches[j][1];
          if (existingUrls.has(loc)) continue;
          const pid = loc.split("/").pop() ?? "";
          const id = `gajab-${pid}`;
          if (seen.has(id)) continue;
          seen.add(id);
          products.push({
            id,
            name: titleMatches[j]?.[1]?.trim() ?? "Unknown Product",
            url: loc,
            image_url: cleanImageUrl(imageMatches[j]?.[1] ?? null),
            price: null,
            category: null,
          });
        }
      }
      if (foundEmpty) break;
    }

    if (!products.length) {
      res.json({ synced: 0, total: 0, message: "All Gajab products already in Supabase" });
      return;
    }

    const BATCH = 100;
    let upserted = 0;
    for (let i = 0; i < products.length; i += BATCH) {
      const batch = products.slice(i, i + BATCH);
      const { error } = await supabase.from("products").upsert(batch, { onConflict: "id" });
      if (error) throw error;
      upserted += batch.length;
    }

    res.json({ synced: upserted, total: products.length });
  } catch (err: any) {
    req.log.error({ err }, "Failed to sync products");
    res.status(500).json({ error: err.message });
  }
});

router.get("/products/status", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const { count: supabaseTotal, error: countError } = await supabase
      .from("products")
      .select("*", { count: "exact", head: true });
    if (countError) throw countError;

    res.json({
      supabase_total: supabaseTotal ?? 0,
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to get product status");
    res.status(500).json({ error: err.message });
  }
});

router.post("/products/upload-gajab", async (req, res): Promise<void> => {
  try {
    const { gajabIds } = req.body;
    if (!Array.isArray(gajabIds)) {
      res.status(400).json({ error: "gajabIds array required" });
      return;
    }

    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const allSupabaseProducts: { id: string; url: string | null }[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, url")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      allSupabaseProducts.push(...page);
      offset += PAGE_LIMIT;
    }
    const supabaseProducts = allSupabaseProducts;

    const gajabIdSet = new Set(gajabIds.map(String));
    const inactiveProducts: { id: string; url: string }[] = [];
    const missingIds: string[] = [];

    for (const p of supabaseProducts) {
      const pid = (p.url ?? "").split("/").pop() ?? "";
      if (!gajabIdSet.has(pid)) {
        inactiveProducts.push({ id: p.id, url: p.url ?? "" });
      }
    }

    for (const gid of gajabIdSet) {
      const exists = supabaseProducts.some(
        (p) => (p.url ?? "").split("/").pop() === gid
      );
      if (!exists) missingIds.push(gid);
    }

    res.json({
      supabase_total: supabaseProducts.length,
      gajab_total: gajabIds.length,
      inactive_count: inactiveProducts.length,
      inactive: inactiveProducts.slice(0, 100),
      inactive_sample: inactiveProducts.slice(0, 10),
      missing_count: missingIds.length,
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to compare products");
    res.status(500).json({ error: err.message });
  }
});

router.post("/products/delete-inactive", async (req, res): Promise<void> => {
  try {
    const { ids } = req.body;
    if (!Array.isArray(ids) || ids.length === 0) {
      res.status(400).json({ error: "ids array required" });
      return;
    }

    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const BATCH = 100;
    let deleted = 0;
    for (let i = 0; i < ids.length; i += BATCH) {
      const batch = ids.slice(i, i + BATCH);
      const { error } = await supabase.from("products").delete().in("id", batch);
      if (error) throw error;
      deleted += batch.length;
    }

    res.json({ deleted });
  } catch (err: any) {
    req.log.error({ err }, "Failed to delete products");
    res.status(500).json({ error: err.message });
  }
});

router.post("/products/sync-and-clean", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    // Fetch existing Supabase product URLs to avoid duplicates
    const existingUrls = new Set<string>();
    const existingGajabIds = new Set<string>();
    const allSupabaseProducts: { id: string; url: string | null }[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, url")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      for (const p of page) {
        if (p.url) existingUrls.add(p.url);
        const pid = (p.url ?? "").split("/").pop() ?? "";
        if (pid) existingGajabIds.add(pid);
      }
      allSupabaseProducts.push(...page);
      offset += PAGE_LIMIT;
    }

    // Fetch Gajab sitemaps in parallel batches to get all active products
    const sitemapIds = new Set<string>();
    const sitemapEntries: { url: string; image_url: string; name: string; id: string }[] = [];
    const MAX_SITEMAPS = 50;
    const PARALLEL = 2;
    for (let batch = 0; batch < MAX_SITEMAPS; batch += PARALLEL) {
      const batchPromises: Promise<{ i: number; stdout: string } | null>[] = [];
      for (let i = batch + 1; i <= batch + PARALLEL && i <= MAX_SITEMAPS; i++) {
        const idx = i;
        const from = (idx - 1) * 500 + 1;
        const to = idx * 500;
        const sitemapUrl = `https://gajab.com/sitemap_products_${idx}.xml?from=${from}&to=${to}`;
        batchPromises.push(
          execFileAsync("curl", [
            "-s", "--max-time", "30", sitemapUrl,
            "-H", "User-Agent: Mozilla/5.0",
          ], { maxBuffer: 10 * 1024 * 1024 })
            .then(({ stdout }) => ({ i: idx, stdout }))
            .catch(() => null)
        );
      }
      const results = await Promise.all(batchPromises);
      let foundEmpty = false;
      for (const r of results) {
        if (!r) continue;
        if (!r.stdout.includes("<loc>")) { foundEmpty = true; continue; }
        const urlMatches = [...r.stdout.matchAll(/<loc>([^<]+)<\/loc>/g)];
        const imageMatches = [...r.stdout.matchAll(/<image:loc>([^<]+)<\/image:loc>/g)];
        const titleMatches = [...r.stdout.matchAll(/<image:title>([^<]*)<\/image:title>/g)];
        for (let j = 0; j < urlMatches.length; j++) {
          const loc = urlMatches[j][1];
          const pid = loc.split("/").pop() ?? "";
          sitemapIds.add(pid);
          sitemapEntries.push({
            url: loc,
            image_url: cleanImageUrl(imageMatches[j]?.[1] ?? null) ?? "",
            name: titleMatches[j]?.[1]?.trim() ?? "Unknown Product",
            id: pid,
          });
        }
      }
      if (foundEmpty) break;
    }

    // Find missing (in sitemaps but not in Supabase) — import only, never delete
    const missingEntries = sitemapEntries.filter((e) => !existingGajabIds.has(e.id) && !existingUrls.has(e.url));

    // Import missing products
    const importedRecords: { id: string; name: string; url: string; image_url: string | null; price: null; category: null }[] = [];
    const seen = new Set<string>();
    for (const entry of missingEntries) {
      const id = `gajab-${entry.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      importedRecords.push({
        id,
        name: entry.name,
        url: entry.url,
        image_url: entry.image_url || null,
        price: null,
        category: null,
      });
    }

    let imported = 0;
    const enrichedUrls: { id: string; url: string }[] = [];
    if (importedRecords.length > 0) {
      const BATCH = 100;
      for (let i = 0; i < importedRecords.length; i += BATCH) {
        const batch = importedRecords.slice(i, i + BATCH);
        const { error } = await supabase.from("products").upsert(batch, { onConflict: "id" });
        if (error) throw error;
        imported += batch.length;
      }
      for (const r of importedRecords) {
        if (r.url) enrichedUrls.push({ id: r.id, url: r.url });
      }
    }

    // Enrich only newly imported products inline
    let enriched = 0;
    if (enrichedUrls.length > 0) {
      enriched = await enrichProducts(supabase, enrichedUrls);
    }

    res.json({
      gajab_active: sitemapIds.size,
      supabase_before: allSupabaseProducts.length,
      supabase_after: allSupabaseProducts.length + imported,
      deleted: 0,
      imported,
      enriched,
      message: `Synced: ${imported} imported, ${enriched} enriched; 0 removed`,
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to sync and clean products");
    res.status(500).json({ error: err.message });
  }
});

async function enrichProducts(
  supabase: any,
  products: { id: string; url: string }[],
): Promise<number> {
  let enriched = 0;
  const BATCH = 20;
  const total = products.length;
  for (let i = 0; i < total; i += BATCH) {
    const batch = products.slice(i, i + BATCH);
    await Promise.all(batch.map(async (p) => {
      try {
        const parts = p.url.replace("https://gajab.com/product-detail/", "").split("/");
        if (parts.length < 2) return;
        const slug = parts[0];
        const itemId = parts[1];
        let category: string | undefined;
        let brandName: string | undefined;
        let priceNum = 0;
        let mrpNum = 0;

        const apiUrl = `https://gatewayservice.gajab.com/product/api/product-store/product/${slug}/${itemId}?pincode=`;
        try {
          const { stdout } = await execFileAsync("curl", [
            "-s", "--max-time", "15", apiUrl,
            "-H", `key: ${GATEWAY_KEY}`,
            "-H", "Content-type: application/json",
            "-H", "Origin: https://gajab.com",
            "-H", "Referer: https://gajab.com/",
          ], { maxBuffer: 1024 * 1024 });
          const resp = JSON.parse(stdout);
          const d = resp.data;
          if (d) {
            const pd = parseProductData(d);
            if (pd) {
              priceNum = Number(pd.price) || 0;
              mrpNum = Number(pd.mrpPrice) || 0;
              category = pd.category;
              brandName = pd.brandName;
            }
          }
        } catch {
          // gateway failed, try HTML
        }

        if (!category || !brandName) {
          try {
            const { stdout: html } = await execFileAsync("curl", [
              "-s", "--max-time", "10", p.url,
              "-H", "User-Agent: Mozilla/5.0",
            ], { maxBuffer: 1024 * 1024 });
            const htmlDetails = extractProductDetails(html);
            if (htmlDetails) {
              if (!category && htmlDetails.category) category = htmlDetails.category;
              if (!brandName && htmlDetails.brandName) brandName = htmlDetails.brandName;
              if (!priceNum) priceNum = Number(htmlDetails.price) || 0;
              if (!mrpNum) mrpNum = Number(htmlDetails.mrpPrice) || 0;
            }
          } catch {
            // HTML fallback failed
          }
        }

        const updates: Record<string, any> = {};
        if (priceNum > 0) {
          updates.price = `₹${priceNum.toLocaleString("en-IN")}`;
          updates.mrp_price = `₹${(mrpNum || priceNum).toLocaleString("en-IN")}`;
        }
        if (category) updates.category = category;
        if (brandName) updates.brand = brandName;

        if (Object.keys(updates).length > 0) {
          const { error } = await supabase.from("products").update(updates).eq("id", p.id);
          if (!error) enriched++;
        }
      } catch (err) {
        logger.warn({ err: (err as Error)?.message, url: p.url }, "Enrich error");
      }
    }));
    await new Promise((r) => setTimeout(r, 50));
    if ((i / BATCH) % 25 === 0) {
      logger.info({ progress: `${i + BATCH}/${total}`, enriched }, "Enrich progress");
    }
  }
  logger.info({ enriched, total }, "Enrich complete");
  return enriched;
}

router.post("/products/enrich", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    // Fetch all products that need enrichment
    const allProducts: { id: string; url: string | null; image_url: string | null; price: string | null; category: string | null; brand: string | null; mrp_price: string | null }[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, url, image_url, price, category, brand, mrp_price")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      allProducts.push(...page);
      offset += PAGE_LIMIT;
    }

    // Build sitemap lookup: url -> { image_url, name }
    const sitemapLookup = new Map<string, { image_url: string; name: string }>();
    for (let batch = 0; batch < 50; batch += 2) {
      const batchPromises: Promise<{ i: number; stdout: string } | null>[] = [];
      for (let i = batch + 1; i <= batch + 2 && i <= 50; i++) {
        const idx = i;
        const from = (idx - 1) * 500 + 1;
        const to = idx * 500;
        const sitemapUrl = `https://gajab.com/sitemap_products_${idx}.xml?from=${from}&to=${to}`;
        batchPromises.push(
          execFileAsync("curl", [
            "-s", "--max-time", "30", sitemapUrl,
            "-H", "User-Agent: Mozilla/5.0",
          ], { maxBuffer: 10 * 1024 * 1024 })
            .then(({ stdout }) => ({ i: idx, stdout }))
            .catch(() => null)
        );
      }
      const results = await Promise.all(batchPromises);
      let foundEmpty = false;
      for (const r of results) {
        if (!r) continue;
        if (!r.stdout.includes("<loc>")) { foundEmpty = true; continue; }

        const urlMatches = [...r.stdout.matchAll(/<loc>([^<]+)<\/loc>/g)];
        const imageMatches = [...r.stdout.matchAll(/<image:loc>([^<]+)<\/image:loc>/g)];
        const titleMatches = [...r.stdout.matchAll(/<image:title>([^<]*)<\/image:title>/g)];

        for (let j = 0; j < urlMatches.length; j++) {
          sitemapLookup.set(urlMatches[j][1], {
            image_url: imageMatches[j]?.[1] ?? "",
            name: titleMatches[j]?.[1]?.trim() ?? "",
          });
        }
      }
      if (foundEmpty) break;
    }

    // 1. Backfill image_url from sitemaps (fast)
    let imgUpdated = 0;
    for (const p of allProducts) {
      if (p.image_url || !p.url) continue;
      const sitemapEntry = sitemapLookup.get(p.url);
      if (sitemapEntry?.image_url) {
        const cleaned = cleanImageUrl(sitemapEntry.image_url);
        const { error } = await supabase
          .from("products")
          .update({ image_url: cleaned })
          .eq("id", p.id);
        if (!error) imgUpdated++;
      }
    }

    // 2. Enrich products with price, category, brand, mrp_price
    const LOW_PRICE_THRESHOLD = 50;
    const toScrape = allProducts.filter((p) => {
      if (!p.url) return false;
      if (!p.price || !p.category || !p.brand || !p.mrp_price) return true;
      const priceNum = Number(p.price.replace(/[^0-9]/g, ""));
      if (priceNum > 0 && priceNum < LOW_PRICE_THRESHOLD) return true;
      return false;
    });
    const toScrapeUrls = toScrape
      .filter((p) => p.url)
      .map((p) => ({ id: p.id, url: p.url! }));
    const totalEnriched = await enrichProducts(supabase, toScrapeUrls);

    res.json({
      products_total: allProducts.length,
      image_url_backfilled: imgUpdated,
      prices_scraped: totalEnriched,
      note: "Price/category scraping complete",
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to enrich products");
    res.status(500).json({ error: err.message });
  }
});

const GATEWAY_KEY = process.env.GATEWAY_KEY ?? "";

async function scrapePricesInBackground(
  supabase: any,
  products?: { id: string; url: string | null }[],
): Promise<void> {
  if (!products || products.length === 0) {
    const all: { id: string; url: string | null }[] = [];
    let offset = 0;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, url, price, category, brand, mrp_price")
        .range(offset, offset + 1000 - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      for (const p of page) {
        if (p.url && (!p.price || !p.category || !p.brand || !p.mrp_price)) {
          all.push({ id: p.id, url: p.url });
        } else if (p.url && p.price) {
          const priceNum = Number(p.price.replace(/[^0-9]/g, ""));
          if (priceNum > 0 && priceNum < 50) {
            all.push({ id: p.id, url: p.url });
          }
        }
      }
      offset += 1000;
    }
    products = all;
  }
  const BATCH = 20;
  let updated = 0;
  const total = products.length;
  for (let i = 0; i < total; i += BATCH) {
    const batch = products.slice(i, i + BATCH);
    await Promise.all(batch.map(async (p) => {
      try {
        const url = p.url!;
        const parts = url.replace("https://gajab.com/product-detail/", "").split("/");
        if (parts.length < 2) return;
        const slug = parts[0];
        const itemId = parts[1];
        const apiUrl = `https://gatewayservice.gajab.com/product/api/product-store/product/${slug}/${itemId}?pincode=`;
        const { stdout } = await execFileAsync("curl", [
          "-s", "--max-time", "15", apiUrl,
          "-H", `key: ${GATEWAY_KEY}`,
          "-H", "Content-type: application/json",
          "-H", "Origin: https://gajab.com",
          "-H", "Referer: https://gajab.com/",
        ], { maxBuffer: 1024 * 1024 });

        const resp = JSON.parse(stdout);
        const d = resp.data;
        if (!d) return;

        const pdBlock = parseProductData(d);
        if (!pdBlock) return;

        let category = pdBlock.category;
        let brandName = pdBlock.brandName;
        if (!category || !brandName) {
          try {
            const { stdout: html } = await execFileAsync("curl", [
              "-s", "--max-time", "10", p.url!,
              "-H", "User-Agent: Mozilla/5.0",
            ], { maxBuffer: 1024 * 1024 });
            const htmlDetails = extractProductDetails(html);
            if (htmlDetails) {
              if (!category && htmlDetails.category) category = htmlDetails.category;
              if (!brandName && htmlDetails.brandName) brandName = htmlDetails.brandName;
            }
          } catch {
            // HTML scrape fallback failed, continue with gateway data
          }
        }

        const updates: Record<string, any> = {};
        const priceNum = Number(pdBlock.price);
        const mrpNum = Number(pdBlock.mrpPrice);
        if (priceNum > 0 && mrpNum > 0) {
          updates.price = `₹${priceNum.toLocaleString("en-IN")}`;
          updates.mrp_price = `₹${mrpNum.toLocaleString("en-IN")}`;
        } else if (priceNum > 0) {
          updates.price = `₹${priceNum.toLocaleString("en-IN")}`;
          updates.mrp_price = `₹${priceNum.toLocaleString("en-IN")}`;
        }
        if (category) {
          updates.category = category;
        }
        if (brandName) {
          updates.brand = brandName;
        }

        if (Object.keys(updates).length > 0) {
          const { error } = await supabase.from("products").update(updates).eq("id", p.id);
          if (!error) updated++;
        }
      } catch (err) {
        logger.warn({ err: (err as Error)?.message, url: p.url }, "Scrape error");
      }
    }));
    await new Promise((r) => setTimeout(r, 50));
    if ((i / BATCH) % 25 === 0) {
      logger.info({ progress: `${i + BATCH}/${total}`, updated }, "Scrape progress");
    }
  }
  logger.info({ updated, total }, "Price/category scrape complete");
}

function parseProductData(d: any): { price?: string; mrpPrice?: string; category?: string; brandName?: string } | null {
  const cats = d.Category ?? [];
  let price = d.price;
  let mrpPrice = d.mrpPrice;
  if (Array.isArray(d.skuList) && d.skuList.length > 0) {
    const defaultSku = d.skuList.find((s: any) => s.isDefault === 1) ?? d.skuList[0];
    if (defaultSku) {
      price = defaultSku.price;
      mrpPrice = defaultSku.mrpPrice;
    }
  }
  return {
    price: price ?? undefined,
    mrpPrice: mrpPrice ?? undefined,
    category: cats.length > 0 ? cats[0].categoryName : undefined,
    brandName: d.brandName ?? undefined,
  };
}

function extractProductDetails(html: string): { price?: string; mrpPrice?: string; category?: string; brandName?: string } | null {
  const startMarker = 'self.__next_f.push([1,"';
  const endMarker = '"])';
  let searchFrom = 0;
  while (true) {
    const blockStart = html.indexOf(startMarker, searchFrom);
    if (blockStart < 0) return null;
    const contentStart = blockStart + startMarker.length;
    const blockEnd = html.indexOf(endMarker, contentStart);
    if (blockEnd < 0) return null;

    const block = html.slice(contentStart, blockEnd);
    if (block.includes("productDetails")) {
      const unescaped = block.replace(/\\u[\da-f]{4}/gi, (m: string) =>
        String.fromCharCode(parseInt(m.slice(2), 16))
      ).replace(/\\(.)/g, "$1");

      const pdIdx = unescaped.indexOf('"productDetails":');
      if (pdIdx < 0) return null;

      let depth = 0;
      let objEnd = pdIdx + 17;
      for (let j = objEnd; j < unescaped.length; j++) {
        if (unescaped[j] === "{") depth++;
        else if (unescaped[j] === "}") { depth--; if (depth === 0) { objEnd = j + 1; break; } }
      }

      const pdStr = unescaped.slice(pdIdx + 17, objEnd);
      const details = JSON.parse(pdStr);
      return parseProductData(details.data ?? details);
    }
    searchFrom = blockEnd + endMarker.length;
  }
}

router.post("/products/fix-images", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const allProducts: { id: string; image_url: string | null }[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, image_url")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      allProducts.push(...page);
      offset += PAGE_LIMIT;
    }

    let fixed = 0;
    let skipped = 0;

    for (const p of allProducts) {
      const cleaned = cleanImageUrl(p.image_url);
      if (cleaned === p.image_url) { skipped++; continue; }
      const { error } = await supabase
        .from("products")
        .update({ image_url: cleaned })
        .eq("id", p.id);
      if (!error) fixed++;
    }

    res.json({
      total: allProducts.length,
      fixed,
      skipped,
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to fix images");
    res.status(500).json({ error: err.message });
  }
});

router.get("/products/duplicates", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const allProducts: any[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, name, price, brand, url, image_url, category, mrp_price")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      allProducts.push(...page);
      offset += PAGE_LIMIT;
    }

    const groups = new Map<string, any[]>();
    for (const p of allProducts) {
      if (!p.name || !p.price || !p.brand) continue;
      const key = `${p.name.toLowerCase().trim()}|${p.price}|${p.brand.toLowerCase().trim()}`;
      const group = groups.get(key) ?? [];
      group.push(p);
      groups.set(key, group);
    }

    const duplicates: { key: string; products: any[] }[] = [];
    for (const [key, group] of groups) {
      if (group.length > 1) {
        duplicates.push({ key, products: group });
      }
    }

    duplicates.sort((a, b) => b.products.length - a.products.length);

    const totalDupes = duplicates.reduce((sum, g) => sum + g.products.length - 1, 0);

    res.json({
      total_groups: duplicates.length,
      total_duplicate_products: totalDupes,
      groups: duplicates.map((g) => ({
        name: g.products[0].name,
        price: g.products[0].price,
        brand: g.products[0].brand,
        count: g.products.length,
        products: g.products.map((p: any) => ({
          id: p.id,
          url: p.url,
          image_url: p.image_url,
          category: p.category,
          mrp_price: p.mrp_price,
        })),
      })),
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to find duplicates");
    res.status(500).json({ error: err.message });
  }
});

router.post("/products/image-duplicates", async (req, res): Promise<void> => {
  try {
    const { threshold } = (req.body ?? {}) as { threshold?: number };
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const allProducts: any[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, name, price, brand, url, image_url, category, mrp_price")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      allProducts.push(...page);
      offset += PAGE_LIMIT;
    }

    const productsWithImages = allProducts.filter((p) => p.image_url);
    if (productsWithImages.length === 0) {
      res.json({ total_groups: 0, total_duplicate_products: 0, groups: [], to_delete_ids: [] });
      return;
    }

    const scriptPath = resolveScript("_image_duplicates.py");
    const { writeFile, unlink } = await import("node:fs/promises");
    const inputPath = `/tmp/image_dup_input_${Date.now()}.json`;
    await writeFile(inputPath, JSON.stringify(productsWithImages));
    const args = [scriptPath, inputPath];
    if (threshold !== undefined) args.push(String(threshold));
    const { stdout } = await execFileAsync("python3", args, {
      maxBuffer: 100 * 1024 * 1024,
      timeout: 600000,
    });
    await unlink(inputPath).catch(() => {});

    const result = JSON.parse(stdout);
    res.json(result);
  } catch (err: any) {
    req.log.error({ err }, "Failed to find image duplicates");
    res.status(500).json({ error: err.message });
  }
});

router.post("/products/verify-duplicates", async (req, res): Promise<void> => {
  try {
    const groups = req.body as { name: string; price: string; brand: string; count: number; products: { id: string; url: string; image_url: string }[] }[];
    if (!Array.isArray(groups) || groups.length === 0) {
      res.status(400).json({ error: "groups array required" });
      return;
    }

    const pairs: { id1: string; img1: string; title1: string; id2: string; img2: string; title2: string }[] = [];
    for (const group of groups) {
      const first = group.products[0];
      if (!first) continue;
      for (let i = 1; i < group.products.length; i++) {
        const dup = group.products[i];
        pairs.push({
          id1: first.id,
          img1: first.image_url ?? "",
          title1: group.name,
          id2: dup.id,
          img2: dup.image_url ?? "",
          title2: group.name,
        });
      }
    }

    if (pairs.length === 0) {
      res.json({ verified_groups: groups, verified_duplicates: 0 });
      return;
    }

    const scriptPath = resolveScript("verify_duplicate.py");
    const { writeFile, unlink } = await import("node:fs/promises");
    const inputPath = `/tmp/verify_input_${Date.now()}.json`;
    await writeFile(inputPath, JSON.stringify(pairs));
    const { stdout } = await execFileAsync("python3", [scriptPath, inputPath], {
      maxBuffer: 100 * 1024 * 1024,
      timeout: 600000,
    });
    await unlink(inputPath).catch(() => {});

    const scores: { id1: string; id2: string; dinov2_sim: number | null; clip_text_sim: number | null; is_duplicate: boolean }[] = JSON.parse(stdout);

    const toDelete = new Set<string>();
    for (const s of scores) {
      if (s.is_duplicate) toDelete.add(s.id2);
    }

    const scoreMap = new Map<string, { dinov2_sim: number | null; clip_text_sim: number | null }>();
    for (const s of scores) {
      scoreMap.set(`${s.id1}|${s.id2}`, { dinov2_sim: s.dinov2_sim, clip_text_sim: s.clip_text_sim });
    }

    const outGroups = groups.map((g) => {
      const first = g.products[0];
      return {
        ...g,
        products: g.products.map((p) => {
          const pairKey = first ? `${first.id}|${p.id}` : "";
          const scores_ = pairKey ? scoreMap.get(pairKey) : undefined;
          return {
            ...p,
            verified_duplicate: p.id !== first?.id && toDelete.has(p.id),
            dinov2_sim: scores_?.dinov2_sim ?? null,
            clip_text_sim: scores_?.clip_text_sim ?? null,
          };
        }),
      };
    });

    const verifiedCount = toDelete.size;
    res.json({
      verified_groups: outGroups,
      verified_duplicates: verifiedCount,
      verified_total_pairs: pairs.length,
      to_delete_ids: [...toDelete],
    });
  } catch (err: any) {
    req.log.error({ err }, "Failed to verify duplicates");
    res.status(500).json({ error: err.message });
  }
});

router.post("/products/delete-duplicates", async (req, res): Promise<void> => {
  try {
    const { ids } = req.body as { ids: string[] };
    if (!Array.isArray(ids) || ids.length === 0) {
      res.status(400).json({ error: "ids array required" });
      return;
    }

    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    let deleted = 0;
    const BATCH = 100;
    for (let i = 0; i < ids.length; i += BATCH) {
      const batch = ids.slice(i, i + BATCH);
      const { error } = await supabase.from("products").delete().in("id", batch);
      if (error) throw error;
      deleted += batch.length;
    }

    res.json({ deleted, total_requested: ids.length });
  } catch (err: any) {
    req.log.error({ err }, "Failed to delete duplicates");
    res.status(500).json({ error: err.message });
  }
});

router.get("/products/export-duplicates", async (req, res): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const allProducts: any[] = [];
    let offset = 0;
    const PAGE_LIMIT = 1000;
    while (true) {
      const { data: page, error: pageError } = await supabase
        .from("products")
        .select("id, name, price, brand, url, image_url, category, mrp_price")
        .range(offset, offset + PAGE_LIMIT - 1);
      if (pageError) throw pageError;
      if (!page || page.length === 0) break;
      allProducts.push(...page);
      offset += PAGE_LIMIT;
    }

    const groups = new Map<string, any[]>();
    for (const p of allProducts) {
      if (!p.name || !p.price || !p.brand) continue;
      const key = `${p.name.toLowerCase().trim()}|${p.price}|${p.brand.toLowerCase().trim()}`;
      const group = groups.get(key) ?? [];
      group.push(p);
      groups.set(key, group);
    }

    const rows: string[] = [
      '"Group","ID","Name","Price","Brand","Category","MRP","URL","Image URL"',
    ];

    for (const [, group] of groups) {
      if (group.length < 2) continue;
      for (const p of group) {
        const esc = (s: string) => `"${(s ?? "").replace(/"/g, '""')}"`;
        rows.push([
          esc(group[0].name),
          esc(p.id),
          esc(p.name),
          esc(p.price),
          esc(p.brand),
          esc(p.category ?? ""),
          esc(p.mrp_price ?? ""),
          esc(p.url ?? ""),
          esc(p.image_url ?? ""),
        ].join(","));
      }
    }

    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="duplicate-products-${new Date().toISOString().slice(0, 10)}.csv"`);
    res.send(rows.join("\n"));
  } catch (err: any) {
    req.log.error({ err }, "Failed to export duplicates");
    res.status(500).json({ error: err.message });
  }
});

export default router;
