import { Router, type Request, type Response } from "express";
import ExcelJS from "exceljs";
import { logger } from "../../lib/logger.js";
import { getSupabase } from "../../lib/supabase.js";
import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { runLocalSearch, hasLocalScraper } from "../../lib/localScraper.js";
import { runBatched } from "../../lib/scrapeBatch.js";
import { JobStore, type ScrapeJob } from "../../lib/scrapeJobStore.js";
import { pythonBin } from "../../lib/py";
import crypto from "node:crypto";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRAPER_SCRIPT = path.resolve(__dirname, "../_meesho_scraper.py");
const SEARCHER_SCRIPT = path.resolve(__dirname, "../_platform_searcher.py");

function getMatchTag(score: number | null): string | null {
  if (score == null) return null;
  if (score >= 85) return "Exact Match";
  if (score >= 70) return "Match";
  if (score >= 50) return "Similar";
  return "Almost Similar";
}

function platformMapping(platform: string, data: any) {
  const success = data?.status === "success";
  const unavailable = success && data.unavailable === true;
  const source = success ? (data.source || "search") : null;
  return {
    url: success ? data.url : null,
    price: success && !unavailable ? data.price : null,
    match_score: success ? (data.match_score ?? null) : null,
    dinov2_sim: success ? (data.dinov2_sim ?? null) : null,
    clip_sim: success ? (data.clip_sim ?? null) : null,
    match_tag: success ? getMatchTag(data.match_score) : null,
    unavailable,
    source,
  };
}

const router = Router();

interface PlatformPrice {
  meesho?: { url: string; price: string | null };
  flipkart?: { url: string; price: string | null };
  amazon?: { url: string; price: string | null };
}

async function callScraper(productUrl: string): Promise<any> {
  const env: Record<string, string> = { ...process.env as Record<string, string> };
  if (process.env.SCRAPE_DO_TOKEN) env.SCRAPE_DO_TOKEN = process.env.SCRAPE_DO_TOKEN;

  const { stdout } = await execFileAsync(pythonBin(), [SCRAPER_SCRIPT, "scrape", productUrl], {
    env,
    timeout: 30000,
    maxBuffer: 10 * 1024 * 1024,
  });

  return JSON.parse(stdout);
}

async function callSearch(title: string, imageUrl: string, gajabPrice: string = "", gajabUrl: string = ""): Promise<any> {
  if (hasLocalScraper()) {
    const localResult = await runLocalSearch(title, imageUrl, gajabPrice, gajabUrl);
    if (localResult) return localResult;
  }

  const env: Record<string, string> = { ...process.env as Record<string, string> };

  try {
    const maxTitle = title.length > 400 ? title.slice(0, 400) : title;
    const input = `${maxTitle}|${imageUrl}|${gajabPrice}|${gajabUrl}`;
    const { stdout } = await execFileAsync(pythonBin(), [SEARCHER_SCRIPT, "search", input], {
      env,
      timeout: 180000,
      maxBuffer: 10 * 1024 * 1024,
    });
    return JSON.parse(stdout);
  } catch (err: any) {
    logger.warn({ err: err.message, title: title.slice(0, 100) }, "Platform search subprocess failed");
    return { amazon: { status: "failed", error: "Search timed out" }, flipkart: { status: "skipped" }, meesho: { status: "skipped" } };
  }
}

// ── in-memory cache ─────────────────────────────────────────────────────────
// The products table is ~30k rows. Querying Supabase per request meant a COUNT
// plus a sorted page every time (~1-4s measured) — and the mapped/unmapped
// filter was impossible to express in SQL, because 18k+ mapped ids is far too
// many for a not-in list (which is why `filter=unmapped` was silently ignored).
// Load the three small tables once, hold them for 60s, answer from memory.
interface PmCache {
  products: any[];
  mappedIds: Set<string>;
  dupIds: Set<string>;
  ts: number;
  loading: Promise<PmCache> | null;
}
let _cache: PmCache = { products: [], mappedIds: new Set(), dupIds: new Set(), ts: 0, loading: null };

const CACHE_TTL_MS = 60_000;
const PRODUCT_PAGES = 35;   // 35k-row ceiling
const MAPPING_PAGES = 25;   // 25k
const DUP_PAGES = 10;

async function loadCaches(): Promise<PmCache> {
  if (Date.now() - _cache.ts < CACHE_TTL_MS && _cache.products.length > 0) return _cache;
  if (_cache.loading) return _cache.loading;
  _cache.loading = (async () => {
    const supabase = getSupabase()!;
    const pages = (n: number) => Array.from({ length: n }, (_, i) => i);
    const [prodPages, mapPages, dupPages] = await Promise.all([
      Promise.all(pages(PRODUCT_PAGES).map((p) =>
        supabase.from("products").select("id,name,price,image_url,url,category").order("name").range(p * 1000, p * 1000 + 999))),
      Promise.all(pages(MAPPING_PAGES).map((p) =>
        supabase.from("price_mappings").select("gajab_product_id")
          // "Mapped" means the product actually got a price source. Rows where
          // every platform failed carry no url and are not mapped — the old
          // server filtered these out and the UI badge requires a url too, so
          // without this the Mapped tab and its count disagreed with the rows.
          .or("amazon_url.not.is.null,flipkart_url.not.is.null,meesho_url.not.is.null")
          // ORDER before range: without a stable order, offset pagination is
          // non-deterministic and silently skips rows (this was dropping
          // recently-mapped products from the Mapped set entirely).
          .order("gajab_product_id")
          .range(p * 1000, p * 1000 + 999))),
      Promise.all(pages(DUP_PAGES).map((p) =>
        supabase.from("product_duplicates").select("product_id")
          .order("product_id")
          .range(p * 1000, p * 1000 + 999))),
    ]);
    const products: any[] = [];
    for (const r of prodPages) if (r.data) products.push(...r.data);
    const mappedIds = new Set<string>();
    for (const r of mapPages) if (r.data) for (const m of r.data) mappedIds.add(m.gajab_product_id);
    const dupIds = new Set<string>();
    for (const r of dupPages) if (r.data) for (const d of r.data) dupIds.add(d.product_id);
    _cache = { products, mappedIds, dupIds, ts: Date.now(), loading: null };
    logger.info(
      { products: products.length, mapped: mappedIds.size, duplicates: dupIds.size },
      "price-mapper cache loaded",
    );
    return _cache;
  })();
  return _cache.loading;
}

// GET /api/price-mapper/products — list Gajab.com products from Supabase (paginated)
router.get("/products", async (req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const page = Math.max(1, parseInt(req.query.page as string, 10) || 1);
    const pageSize = Math.min(200, Math.max(1, parseInt(req.query.pageSize as string, 10) || 25));
    const search = (req.query.search as string || "").trim().toLowerCase();
    const filter = ((req.query.filter as string) || "all").toLowerCase();
    const fromIdx = parseInt(req.query.from as string, 10) || 0;
    const toIdx = parseInt(req.query.to as string, 10) || 0;

    // Serve from the in-memory cache (see loadCaches) — a page is now a slice,
    // not two Supabase round trips.
    const c = await loadCaches();
    let list = c.products;
    if (search) list = list.filter((p) => (p.name || "").toLowerCase().includes(search));
    if (filter === "mapped") list = list.filter((p) => c.mappedIds.has(p.id));
    else if (filter === "unmapped") list = list.filter((p) => !c.mappedIds.has(p.id) && !c.dupIds.has(p.id));
    else if (filter === "duplicates") list = list.filter((p) => c.dupIds.has(p.id));

    const total = list.length;
    const from = fromIdx > 0 ? fromIdx - 1 : (page - 1) * pageSize;
    const to = toIdx > fromIdx ? toIdx : from + pageSize;

    res.json({
      products: list.slice(from, to),
      total,
      page,
      pageSize,
    });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to fetch products");
    res.status(500).json({ error: err.message });
  }
});

interface PmProduct {
  id: string; name: string; image_url?: string; imageUrl?: string; price?: string; url?: string;
}

/** Search every platform for one Gajab product and upsert the mapping.
 *  Shared by POST /compare and the bulk map-all job. */
async function compareProduct(product: PmProduct): Promise<Record<string, any>> {
  const supabase = getSupabase();
  const productImageUrl = product.image_url || product.imageUrl || "";
  const searchResult = await callSearch(product.name, productImageUrl, product.price || "", product.url || "");

  const amz = platformMapping("amazon", searchResult.amazon);
  const ms = platformMapping("meesho", searchResult.meesho);
  const fk = platformMapping("flipkart", searchResult.flipkart);

  const mapping: Record<string, any> = {
    gajab_product_id: product.id,
    gajab_title: product.name,
    gajab_image_url: productImageUrl || null,
    gajab_price: product.price || null,
    gajab_url: product.url || null,
    amazon_url: amz.url,
    amazon_price: amz.price,
    amazon_match_score: amz.match_score,
    amazon_dinov2: amz.dinov2_sim,
    amazon_clip: amz.clip_sim,
    amazon_match_tag: amz.match_tag,
    amazon_unavailable: amz.unavailable,
    meesho_url: ms.url,
    meesho_price: ms.price,
    meesho_match_score: ms.match_score,
    flipkart_url: fk.url,
    flipkart_price: fk.price,
    flipkart_match_score: fk.match_score,
    flipkart_dinov2: fk.dinov2_sim,
    flipkart_clip: fk.clip_sim,
    flipkart_match_tag: fk.match_tag,
    flipkart_unavailable: fk.unavailable,
    last_checked: new Date().toISOString(),
    search_error: {
      amazon: searchResult.amazon?.status !== "success" ? (searchResult.amazon?.error || searchResult.amazon?.status) : null,
      meesho: searchResult.meesho?.status !== "success" ? (searchResult.meesho?.error || searchResult.meesho?.status) : null,
      flipkart: searchResult.flipkart?.status !== "success" ? (searchResult.flipkart?.error || searchResult.flipkart?.status) : null,
    },
  };

  if (supabase) {
    try {
      const { error: dbErr } = await supabase.from("price_mappings").upsert(mapping, { onConflict: "gajab_product_id" });
      if (dbErr) {
        logger.warn({ err: dbErr.message, code: dbErr.code }, "Failed to save price mapping");
      } else {
        // Keep the cache's mapped set current so the unmapped filter drops this
        // product immediately, without paying for a full reload.
        _cache.mappedIds.add(product.id);
      }
    } catch (dbErr: any) {
      logger.warn({ err: dbErr.message }, "Failed to save price mapping (table may not exist)");
    }
  }
  return mapping;
}

// POST /api/price-mapper/compare — find a Gajab product on Meesho
router.post("/compare", async (req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }
    const { product } = req.body as { product: PmProduct };
    if (!product || !product.id || !product.name) {
      res.status(400).json({ error: "Product with id and name required" });
      return;
    }

    logger.info({ productId: product.id, name: product.name }, "Comparing prices across platforms");
    const mapping = await compareProduct(product);
    res.json({ mapping, search_errors: mapping.search_error });
  } catch (err: any) {
    logger.error({ err: err.message }, "Price comparison failed");
    res.status(500).json({ error: err.message });
  }
});

// POST /api/price-mapper/fetch-price — scrape price from a platform URL
router.post("/fetch-price", async (req: Request, res: Response): Promise<void> => {
  try {
    const { url, platform } = req.body as { url: string; platform: string };
    if (!url) {
      res.status(400).json({ error: "URL required" });
      return;
    }

    if (platform === "meesho") {
      const data = await callScraper(url);
      if (data.status === "success") {
        res.json({ price: data.price, title: data.title, imageUrl: data.imageUrl, images: data.images });
      } else {
        res.status(502).json({ error: data.error || "Failed to scrape" });
      }
    } else {
      res.status(400).json({ error: `Platform '${platform}' not yet supported for auto-fetch` });
    }
  } catch (err: any) {
    logger.error({ err: err.message }, "Fetch price failed");
    res.status(500).json({ error: err.message });
  }
});

// POST /api/price-mapper/save — save a manual price mapping
router.post("/save", async (req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }
    const { productId, platform, url, price } = req.body as {
      productId: string;
      platform: string;
      url: string;
      price: string;
    };

    if (!productId || !platform || !url) {
      res.status(400).json({ error: "productId, platform, and url required" });
      return;
    }

    const updateField: Record<string, any> = {};
    if (platform === "meesho") {
      updateField.meesho_url = url;
      updateField.meesho_price = price || null;
    } else if (platform === "flipkart") {
      updateField.flipkart_url = url;
      updateField.flipkart_price = price || null;
    } else if (platform === "amazon") {
      updateField.amazon_url = url;
      updateField.amazon_price = price || null;
    }
    updateField.last_checked = new Date().toISOString();

    const { error } = await supabase
      .from("price_mappings")
      .upsert({ gajab_product_id: productId, ...updateField }, { onConflict: "gajab_product_id" });

    if (error) {
      logger.warn({ err: error.message }, "Failed to save to price_mappings table (may not exist yet)");
    }
    res.json({ success: true });
  } catch (err: any) {
    logger.warn({ err: err.message }, "Failed to save mapping");
    res.json({ success: false, error: err.message });
  }
});

// GET /api/price-mapper/mappings — get stored price mappings (optionally by ids)
router.get("/mappings", async (req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }

    const idsParam = (req.query.ids as string || "").trim();
    if (idsParam) {
      const ids = idsParam.split(",").map(s => s.trim()).filter(Boolean).slice(0, 200);
      if (ids.length === 0) {
        res.json({ mappings: [] });
        return;
      }
      const { data, error } = await supabase
        .from("price_mappings")
        .select("*")
        .in("gajab_product_id", ids);
      if (error) {
        if (error.message?.includes("price_mappings")) {
          res.json({ mappings: [] });
          return;
        }
        throw error;
      }
      res.json({ mappings: data || [] });
      return;
    }

    // Legacy: full load (paginated)
    const PAGE_LIMIT = 1000;
    let allMappings: any[] = [];
    let page = 0;

    while (true) {
      const from = page * PAGE_LIMIT;
      const to = from + PAGE_LIMIT - 1;
      const { data, error } = await supabase
        .from("price_mappings")
        .select("*")
        .order("last_checked", { ascending: false })
        .range(from, to);

      if (error) {
        if (error.message?.includes("price_mappings")) {
          res.json({ mappings: [] });
          return;
        }
        throw error;
      }
      if (!data || data.length === 0) break;
      allMappings = allMappings.concat(data);
      if (data.length < PAGE_LIMIT) break;
      page++;
    }

    res.json({ mappings: allMappings });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to fetch mappings");
    res.json({ mappings: [] });
  }
});

// GET /api/price-mapper/mappings-count — count of mapped products
router.get("/mappings-count", async (_req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }
    const { count, error } = await supabase
      .from("price_mappings")
      .select("gajab_product_id", { count: "exact", head: true })
      // Same definition of "mapped" as the filter/cache: needs a price source.
      .or("amazon_url.not.is.null,flipkart_url.not.is.null,meesho_url.not.is.null");
    if (error) {
      res.json({ count: 0 });
      return;
    }
    res.json({ count: count ?? 0 });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to count mappings");
    res.json({ count: 0 });
  }
});

// GET /api/price-mapper/duplicates — get all product duplicates
router.get("/duplicates", async (_req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }
    const { data, error } = await supabase
      .from("product_duplicates")
      .select("product_id, duplicate_of, dinov2_score");
    if (error) throw error;
    res.json({ duplicates: data || [] });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to fetch duplicates");
    res.json({ duplicates: [] });
  }
});

// ── bulk "map every unmapped product" job (persisted, resumable) ────────────
// A compare takes ~30s, so mapping ~12k products is tens of hours — far too long
// for a browser loop that dies on a refresh. Run it as a persisted background job
// (the same store the scrapers use): progress survives a restart and the client
// can watch it advance.
const mapJobs = new JobStore<Record<string, any>>(
  "price_mapper_jobs", "price-mapper", "PRICE_MAPPER_JOBS_DIR",
);

const MAP_CONCURRENCY = Math.max(1, Number(process.env.PRICE_MAPPER_CONCURRENCY ?? 3) || 3);

async function runMapJob(job: ScrapeJob<Record<string, any>>, queue: PmProduct[]): Promise<void> {
  job.status = "running";
  mapJobs.persist(job, true);
  const done = new Set(job.products.map((p) => p.gajab_product_id));
  const pending = queue.filter((p) => !done.has(p.id));
  await runBatched(
    pending,
    async (product) => {
      if (job.status === "cancelled") return null;
      const mapping = await compareProduct(product);
      job.products.push(mapping);
      job.completed = job.products.length;
      mapJobs.persist(job);
      return mapping;
    },
    MAP_CONCURRENCY,
    0,
  );
  if (job.status !== "cancelled") job.status = "completed";
  job.completed = job.products.length;
  mapJobs.persist(job, true);
}

// POST /api/price-mapper/map-all — start a background job over every unmapped product
router.post("/map-all", async (req: Request, res: Response): Promise<void> => {
  try {
    const supabase = getSupabase();
    if (!supabase) {
      res.status(500).json({ error: "Supabase not configured" });
      return;
    }
    const c = await loadCaches();
    const limit = Math.max(0, Number((req.body || {}).limit ?? 0) || 0);
    let queue = c.products.filter((p) => !c.mappedIds.has(p.id) && !c.dupIds.has(p.id));
    if (limit > 0) queue = queue.slice(0, limit);

    const job: ScrapeJob<Record<string, any>> = {
      id: crypto.randomBytes(8).toString("hex"),
      type: "price-map",
      status: "pending",
      total: queue.length,
      completed: 0,
      products: [],
      queue,
      errors: [],
      createdAt: new Date(),
    };
    mapJobs.jobs.set(job.id, job);
    mapJobs.persist(job, true);
    logger.info({ jobId: job.id, total: queue.length, concurrency: MAP_CONCURRENCY }, "Starting price-map job");

    runMapJob(job, queue).catch((err: any) => {
      logger.error({ jobId: job.id, err: err.message }, "price-map job failed");
      job.status = "failed";
      job.errors = [err.message];
      mapJobs.persist(job, true);
    });

    res.json({ jobId: job.id, total: queue.length });
  } catch (err: any) {
    logger.error({ err: err.message }, "price-map submit failed");
    res.status(500).json({ error: err.message });
  }
});

router.get("/map-all/:jobId", (req: Request, res: Response): void => {
  const job = mapJobs.jobs.get(req.params.jobId as string);
  if (!job) {
    res.json({ status: "failed", total: 0, completed: 0, count: 0, products: [], error: "Job not found" });
    return;
  }
  res.json({
    status: job.status,
    total: job.total,
    completed: job.completed,
    count: job.products.length,
    products: mapJobs.slice(job, req.query.since),
    errors: job.errors || [],
  });
});

router.post("/map-all/:jobId/cancel", (req: Request, res: Response): void => {
  const job = mapJobs.jobs.get(req.params.jobId as string);
  if (job) {
    job.status = "cancelled";
    mapJobs.persist(job, true);
  }
  res.json({ status: "cancelled" });
});

// Resume a price-map job that was running when the process last stopped.
mapJobs.load();
for (const job of mapJobs.jobs.values()) {
  if (job.status !== "running" && job.status !== "pending") continue;
  const queue = (job.queue as PmProduct[]) || [];
  if (queue.length) {
    logger.info({ jobId: job.id, done: job.products.length, total: queue.length }, "resuming price-map job");
    runMapJob(job, queue).catch((err: any) => {
      job.status = "interrupted";
      job.errors = [err.message];
      mapJobs.persist(job, true);
    });
  } else {
    job.status = "interrupted";
    mapJobs.persist(job, true);
  }
}

// POST /api/price-mapper/export — export mappings to Excel
router.post("/export", async (req: Request, res: Response): Promise<void> => {
  try {
    const { mappings, filename = "price-mappings.xlsx" } = req.body;
    if (!mappings || !Array.isArray(mappings)) {
      res.status(400).json({ error: "Mappings array required" });
      return;
    }

    const workbook = new ExcelJS.Workbook();
    const worksheet = workbook.addWorksheet("Price Mappings");

    worksheet.columns = [
      { header: "Gajab Product ID", key: "gajab_product_id", width: 30 },
      { header: "Product Title", key: "gajab_title", width: 60 },
      { header: "Gajab Price", key: "gajab_price", width: 20 },
      { header: "Gajab URL", key: "gajab_url", width: 60 },
      { header: "Amazon URL", key: "amazon_url", width: 60 },
      { header: "Amazon Price", key: "amazon_price", width: 20 },
      { header: "Amazon Match Score", key: "amazon_match_score", width: 18 },
      { header: "Amazon DINOv2", key: "amazon_dinov2", width: 14 },
      { header: "Amazon CLIP", key: "amazon_clip", width: 14 },
      { header: "Amazon Match Tag", key: "amazon_match_tag", width: 18 },
      { header: "Amazon Unavailable", key: "amazon_unavailable", width: 18 },
      { header: "Flipkart URL", key: "flipkart_url", width: 60 },
      { header: "Flipkart Price", key: "flipkart_price", width: 20 },
      { header: "Flipkart Match Score", key: "flipkart_match_score", width: 18 },
      { header: "Flipkart DINOv2", key: "flipkart_dinov2", width: 16 },
      { header: "Flipkart CLIP", key: "flipkart_clip", width: 14 },
      { header: "Flipkart Match Tag", key: "flipkart_match_tag", width: 20 },
      { header: "Flipkart Unavailable", key: "flipkart_unavailable", width: 20 },
      { header: "Meesho URL", key: "meesho_url", width: 60 },
      { header: "Meesho Price", key: "meesho_price", width: 20 },
      { header: "Meesho Match Score", key: "meesho_match_score", width: 18 },
      { header: "Last Checked", key: "last_checked", width: 25 },
    ];

    for (const m of mappings) {
      worksheet.addRow({
        gajab_product_id: m.gajab_product_id,
        gajab_title: m.gajab_title,
        gajab_price: m.gajab_price,
        gajab_url: m.gajab_url,
        amazon_url: m.amazon_url,
        amazon_price: m.amazon_price,
        amazon_match_score: m.amazon_match_score,
        amazon_dinov2: m.amazon_dinov2 != null ? Math.round(m.amazon_dinov2 * 100) + "%" : null,
        amazon_clip: m.amazon_clip != null ? Math.round(m.amazon_clip * 100) + "%" : null,
        amazon_match_tag: m.amazon_match_tag || null,
        amazon_unavailable: m.amazon_unavailable ? "Yes" : "No",
        flipkart_url: m.flipkart_url,
        flipkart_price: m.flipkart_price,
        flipkart_match_score: m.flipkart_match_score,
        flipkart_dinov2: m.flipkart_dinov2 != null ? Math.round(m.flipkart_dinov2 * 100) + "%" : null,
        flipkart_clip: m.flipkart_clip != null ? Math.round(m.flipkart_clip * 100) + "%" : null,
        flipkart_match_tag: m.flipkart_match_tag || null,
        flipkart_unavailable: m.flipkart_unavailable ? "Yes" : "No",
        meesho_url: m.meesho_url,
        meesho_price: m.meesho_price,
        meesho_match_score: m.meesho_match_score,
        last_checked: m.last_checked,
      });
    }

    const headerRow = worksheet.getRow(1);
    headerRow.font = { bold: true };
    headerRow.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FF6366F1" },
    };
    headerRow.eachCell((cell) => {
      cell.font = { color: { argb: "FFFFFFFF" }, bold: true };
    });

    res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

    await workbook.xlsx.write(res);
    res.end();
  } catch (err: any) {
    logger.error({ err }, "Price mapper Excel export failed");
    res.status(500).json({ error: "Export failed: " + err.message });
  }
});

export default router;
