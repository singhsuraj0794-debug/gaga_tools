import { Router, type Request, type Response } from "express";
import { logger } from "../../lib/logger.js";
import ExcelJS from "exceljs";
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import crypto from "node:crypto";
import { runLocalScraper, runLocalExtract, runLocalExtractPage, hasLocalScraper } from "../../lib/localScraper.js";
import { sendGajabExport, sendGajabSummary } from "./gajabExport";
import { pythonBin } from "../../lib/py";
import { runBatched } from "../../lib/scrapeBatch";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRAPER_SCRIPT = path.resolve(__dirname, "_meesho_scraper.py");

const router = Router();

interface MeeshoDetailedProduct {
  id: string;
  title: string;
  description: string | null;
  meta_description: string | null;
  imageUrl: string | null;
  images: string[];
  hsn: string | null;
  gst: string | null;
  dimensions: string | null;
  weight: string | null;
  specifications: Record<string, string> | null;
  source_category_path?: string | null;
  source_catalog_name?: string | null;
  variants: string | null;
  price: string | null;
  url: string;
  status: string;
  error: string | null;
}

interface ScrapeJob {
  id: string;
  type: "scrape" | "extract";
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "interrupted" | "paused";
  total: number;
  completed: number;
  products: MeeshoDetailedProduct[];
  storeName?: string;
  errors?: string[];
  /** Extract jobs only: how many store pages have been walked so far. */
  pages?: number;
  /** Extract jobs: the store being walked, so an interrupted run can resume. */
  storeUrl?: string;
  /** Extract jobs: next page to fetch per sort order — the resume point. */
  progress?: Record<string, number>;
  /** Scrape jobs: the full URL list, so an interrupted run can finish the rest. */
  urls?: string[];
  createdAt: Date;
}

const jobs = new Map<string, ScrapeJob>();

// ── persistence ────────────────────────────────────────────────────────────
// Jobs used to live only in memory, so any restart (a deploy, or a stack
// restart) silently threw away a run the user had waited 20+ minutes for — the
// "I only got 1600 of 4280" report. Persist each job as JSON next to the build
// output (dist/ is rebuilt, so keep the dir beside it) and reload at startup.
const JOBS_DIR = process.env.MEESHO_JOBS_DIR || path.resolve(__dirname, "..", "meesho_jobs");
try {
  fs.mkdirSync(JOBS_DIR, { recursive: true });
} catch {
  // best effort — persistence is a safety net, not a hard dependency
}

function _writeJob(job: ScrapeJob): void {
  try {
    fs.writeFileSync(path.join(JOBS_DIR, `${job.id}.json`), JSON.stringify(job));
  } catch (err: any) {
    logger.warn({ id: job.id, err: err.message }, "failed to persist Meesho job");
  }
}

const _dirty = new Set<string>();
let _flushTimer: NodeJS.Timeout | null = null;

/** Persist a job. Writes are coalesced (one per 5s) because a 4k-product job is
 *  a few MB and it is touched on every page; terminal states pass
 *  immediate=true so a stop can never lose the finished result. */
function persistJob(job: ScrapeJob, immediate = false): void {
  if (immediate) {
    _dirty.delete(job.id);
    _writeJob(job);
    return;
  }
  _dirty.add(job.id);
  if (_flushTimer) return;
  _flushTimer = setTimeout(() => {
    _flushTimer = null;
    for (const id of _dirty) {
      const j = jobs.get(id);
      if (j) _writeJob(j);
    }
    _dirty.clear();
  }, 5000);
}

function loadJobs(): void {
  let files: string[] = [];
  try {
    files = fs.readdirSync(JOBS_DIR);
  } catch {
    return;
  }
  for (const f of files) {
    if (!f.endsWith(".json")) continue;
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(JOBS_DIR, f), "utf8")) as ScrapeJob;
      if (raw && raw.id) jobs.set(raw.id, raw);
    } catch (err: any) {
      logger.warn({ file: f, err: err.message }, "skipping unreadable Meesho job file");
    }
  }
}

function generateJobId(): string {
  return crypto.randomBytes(8).toString("hex");
}

async function callPython(action: string, url: string): Promise<any> {
  const env: Record<string, string> = { ...process.env as Record<string, string> };
  if (process.env.SCRAPE_DO_TOKEN) env.SCRAPE_DO_TOKEN = process.env.SCRAPE_DO_TOKEN;
  if (process.env.SCRAPER_PROXY) env.SCRAPER_PROXY = process.env.SCRAPER_PROXY;
  if (process.env.SCRAPERAPI_KEY) env.SCRAPERAPI_KEY = process.env.SCRAPERAPI_KEY;
  else if (process.env.SCRAPER_API_KEY) env.SCRAPERAPI_KEY = process.env.SCRAPER_API_KEY;
  if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;
  const timeout = 900000;
  const maxBuffer = action === "extract" ? 200 * 1024 * 1024 : 50 * 1024 * 1024;

  const { stdout } = await execFileAsync(pythonBin(), [SCRAPER_SCRIPT, action, url], {
    env,
    timeout,
    maxBuffer,
  });

  return JSON.parse(stdout);
}

async function scrapeProduct(url: string): Promise<MeeshoDetailedProduct> {
  if (hasLocalScraper()) {
    const localResult = await runLocalScraper(url, "meesho");
    if (localResult && localResult.status === "success") {
      return {
        id: localResult.id || "",
        title: localResult.title || "Untitled",
        description: localResult.description || null,
        meta_description: localResult.meta_description || null,
        imageUrl: (localResult.images || [])[0] || null,
        images: localResult.images || [],
        hsn: localResult.hsn || null,
        gst: localResult.gst || null,
        dimensions: localResult.dimensions || null,
        weight: localResult.weight || null,
        specifications: localResult.specifications || null,
        source_category_path: localResult.source_category_path || null,
        source_catalog_name: localResult.source_catalog_name || null,
        variants: localResult.variants || null,
        price: localResult.price || null,
        url,
        status: "success",
        error: null,
      };
    }
    if (localResult) {
      // The local server ran the very same scraper and returned a definitive
      // failure. Falling through would re-run it in a subprocess with a 900s
      // timeout — the same work paid for twice, which is what let a handful of
      // slow products stall a whole batch. Report it and move on.
      return {
        id: "", title: "Failed to scrape", description: null, meta_description: null,
        imageUrl: null, images: [], hsn: null, gst: null, dimensions: null, weight: null,
        specifications: null, variants: null, price: null, url,
        status: localResult.status || "failed",
        error: localResult.error || "Scraping failed",
      };
    }
    // localResult === null => the local server was unreachable or timed out.
  }

  const result = await callPython("scrape", url).catch(() => null);
  if (result && result.status === "success") {
    return {
      id: result.id || "",
      title: result.title || "Untitled",
      description: result.description || null,
      meta_description: result.meta_description || null,
      imageUrl: (result.images || [])[0] || null,
      images: result.images || [],
      hsn: result.hsn || null,
      gst: result.gst || null,
      dimensions: result.dimensions || null,
      weight: result.weight || null,
      specifications: result.specifications || null,
      source_category_path: result.source_category_path || null,
      source_catalog_name: result.source_catalog_name || null,
      variants: result.variants || null,
      price: result.price || null,
      url,
      status: "success",
      error: null,
    };
  }
  return {
    id: "", title: "Failed to scrape", description: null, meta_description: null,
    imageUrl: null, images: [], hsn: null, gst: null, dimensions: null, weight: null,
    specifications: null, variants: null, price: null, url,
    status: "failed", error: result?.error || "Scraping failed",
  };
}

const EXTRACT_SORTS = ["", "popularity", "price_low_to_high"];
const EXTRACT_MAX_PAGES = 300;

/** Map a raw extracted link to the detailed product shape. */
function mapExtractProduct(p: any): MeeshoDetailedProduct {
  return {
    id: p.id || "",
    title: p.title || "Untitled",
    description: p.description || null,
    meta_description: p.meta_description || null,
    imageUrl: (p.images || [])[0] || p.imageUrl || null,
    images: p.images || [],
    hsn: p.hsn || null,
    gst: p.gst || null,
    dimensions: p.dimensions || null,
    weight: p.weight || null,
    specifications: p.specifications || null,
    variants: p.variants || null,
    price: p.price || null,
    url: p.url || "",
    status: "success",
    error: null,
  };
}

/** Walk every ordering of a store, unioning links into job.products.
 *
 *  Meesho caps store pagination at ~200 pages AND repeats products across them,
 *  so one ordering exposes only part of a large catalogue (measured: 218 pages
 *  -> 3490 unique of 4280). Different sort orders walk different windows, so we
 *  walk each and union. They run concurrently — sequentially a full store took
 *  ~55 min. Resumable: job.progress holds the next page per ordering, so a
 *  restart continues instead of re-walking from page 1. Node is single
 *  threaded, so the shared seen/allProducts merges are atomic.
 */
async function walkStore(job: ScrapeJob, storeUrl: string): Promise<void> {
  const allProducts = job.products;
  const seen = new Set(allProducts.map((p) => p.url));
  const progress = job.progress || (job.progress = {});
  await Promise.all(EXTRACT_SORTS.map(async (sort) => {
    let page = progress[sort] || 1;
    let consecutiveEmpty = 0;
    while (page <= EXTRACT_MAX_PAGES) {
      // An unreachable local scraper — or a DEAD extraction Chrome, which comes
      // back as a 200 with {"error": "Chrome CDP not available"} rather than a
      // transport failure — is NOT "end of store". Treating it as an empty page
      // made the job silently "complete" with 0 products (and the client show an
      // empty list), which is exactly what a crashed Chrome looked like.
      const unreachable = (c: any) =>
        !c || (typeof c.error === "string" && c.error.includes("Chrome CDP not available"));
      let chunk = await runLocalExtractPage(storeUrl, page, sort);
      for (let tries = 0; unreachable(chunk) && tries < 20; tries++) {
        await new Promise((r) => setTimeout(r, 5000));
        chunk = await runLocalExtractPage(storeUrl, page, sort);
      }
      if (unreachable(chunk)) {
        job.errors = job.errors || [];
        job.errors.push(
          `Extraction Chrome (CDP ${process.env.EXTRACT_CDP_URL || "9225"}) is not reachable at page ${page}` +
          `${sort ? ` (sort=${sort})` : ""} — restart the stack; stopping instead of reporting an empty store`,
        );
        throw new Error("extraction Chrome (CDP) not reachable");
      }
      if (chunk.products && chunk.products.length > 0) {
        consecutiveEmpty = 0;
        for (const p of chunk.products) {
          const url = p.url || "";
          if (!url || seen.has(url)) continue;
          seen.add(url);
          allProducts.push(mapExtractProduct(p));
        }
      } else {
        consecutiveEmpty++;
      }
      progress[sort] = page + 1;   // the resume point for this ordering
      job.pages = (job.pages || 0) + 1;
      job.total = allProducts.length;
      job.completed = allProducts.length;
      persistJob(job);
      if (consecutiveEmpty >= 3) break;  // end of this ordering
      page++;
    }
  }));
}

const SCRAPE_MAX_ROUNDS = 3;

// Meesho is the only platform that throttles, and it does so hard: at the shared
// SCRAPE_CONCURRENCY (8) the block rate climbs until almost everything comes
// back blocked (measured: success fell 73% -> 2% mid-run). 3 keeps the unproxied
// Chrome fast while staying under Meesho's rate limit.
const MEESHO_CONCURRENCY = Math.max(1, Number(process.env.MEESHO_SCRAPE_CONCURRENCY ?? 3) || 3);

/** A product that came back blocked/failed, or with no images, is worth another
 *  attempt: Meesho's block is intermittent (a fresh request usually succeeds),
 *  so retrying recovers most of them. Amazon does the same. */
function needsRetry(p: MeeshoDetailedProduct | undefined): boolean {
  return !p || p.status !== "success" || !(p.images || []).length;
}

/** One pass over `urls` at bounded concurrency, stopping cleanly on pause.
 *  Returns false if it stopped early (paused/cancelled), true if it finished. */
async function scrapePass(job: ScrapeJob, urls: string[]): Promise<boolean> {
  for (let i = 0; i < urls.length; i += MEESHO_CONCURRENCY) {
    // Checked between chunks, so a pause takes effect within a few products
    // instead of cancelling the job (which cannot then be resumed).
    if (job.status === "paused" || job.status === "cancelled") return false;
    const chunk = urls.slice(i, i + MEESHO_CONCURRENCY);
    await Promise.all(chunk.map(async (url) => {
      const product = await scrapeProduct(url);
      const idx = job.products.findIndex((p) => p.url === url);
      if (idx >= 0) job.products[idx] = product;
      else job.products.push(product);
      job.completed = job.products.length;
      persistJob(job);
    }));
  }
  return true;
}

/** Scrape every URL that does not already have a product in the job, then retry
 *  the stragglers for a few rounds. Skipping the finished ones is what makes a
 *  pause/resume cheap: resuming re-runs this and every done URL is skipped. */
async function runScrapeJob(job: ScrapeJob, urls: string[]): Promise<void> {
  if (job.status === "paused") return;   // resuming is an explicit action
  job.status = "running";
  persistJob(job, true);

  // "done" means SUCCESSFULLY scraped. Counting blocked/failed rows as done was
  // a bug: they were then skipped on resume, so a job could never recover the
  // products that Meesho throttled earlier — which is exactly what happened
  // (742 blocked rows that a plain re-scrape now fetches fine).
  const done = new Set(
    job.products
      .filter((p) => p.status === "success" && (p.images || []).length > 0)
      .map((p) => p.url),
  );
  let pending = urls.filter((u) => !done.has(u));

  for (let round = 0; round < SCRAPE_MAX_ROUNDS && pending.length; round++) {
    const roundUrls = pending;
    const ok = await scrapePass(job, roundUrls);
    if (!ok) return;   // paused/cancelled — leave the status as the user set it

    const byUrl = new Map(job.products.map((p) => [p.url, p]));
    pending = roundUrls.filter((u) => needsRetry(byUrl.get(u)));
    if (pending.length && round < SCRAPE_MAX_ROUNDS - 1) {
      logger.info({ jobId: job.id, retry: pending.length, round: round + 2 }, "retrying blocked Meesho products");
      await new Promise((r) => setTimeout(r, 3000));
      if (job.status === "paused" || job.status === "cancelled") return;
    }
  }
  job.status = "completed";
  job.completed = job.products.length;
  persistJob(job, true);
}

// ── resume work that was mid-flight when the process last stopped ───────────
// A job persisted as "running" cannot still be running — this process just
// started — so pick it back up rather than throwing the work away.
loadJobs();
for (const job of jobs.values()) {
  if (job.status !== "running" && job.status !== "pending") continue;
  if (job.type === "extract" && job.storeUrl && hasLocalScraper()) {
    logger.info({ jobId: job.id, found: job.products.length }, "resuming interrupted Meesho extract job");
    walkStore(job, job.storeUrl)
      .then(() => {
        job.status = "completed";
        job.total = job.products.length;
        job.completed = job.products.length;
        persistJob(job, true);
        logger.info({ jobId: job.id, total: job.products.length }, "resumed Meesho extract job completed");
      })
      .catch((err: any) => {
        // Stopped, not finished — keep the products found so far and say so.
        job.status = "interrupted";
        job.errors = [err.message];
        persistJob(job, true);
        logger.warn({ jobId: job.id, found: job.products.length }, "resumed Meesho extract job stopped");
      });
  } else if (job.type === "scrape" && job.urls && hasLocalScraper()) {
    logger.info(
      { jobId: job.id, done: job.products.length, total: job.urls.length },
      "resuming interrupted Meesho scrape job",
    );
    runScrapeJob(job, job.urls).catch((err: any) => {
      job.status = "failed";
      job.errors = [err.message];
      persistJob(job, true);
    });
  } else {
    job.status = "interrupted";
    persistJob(job, true);
  }
}

router.post("/extract", async (req: Request, res: Response): Promise<void> => {
  try {
    const { url } = req.body;
    if (!url || typeof url !== "string") {
      res.status(400).json({ error: "Store URL required" });
      return;
    }

    // Normalize URL
    let storeUrl = url.trim();
    if (!storeUrl.startsWith("http")) {
      storeUrl = `https://www.${storeUrl}`;
    }
    if (!storeUrl.includes("meesho.com")) {
      res.status(400).json({ error: "Only Meesho URLs are supported" });
      return;
    }

    logger.info({ storeUrl }, "Starting Meesho store extraction job");

    const jobId = generateJobId();
    const job: ScrapeJob = {
      id: jobId,
      type: "extract",
      status: "pending",
      total: 0,
      completed: 0,
      products: [],
      storeName: "",
      errors: [],
      storeUrl,
      progress: {},
      createdAt: new Date(),
    };
    jobs.set(jobId, job);
    persistJob(job, true);

    (async () => {
      job.status = "running";
      persistJob(job, true);

      if (hasLocalScraper()) {
        await walkStore(job, storeUrl);
        job.status = "completed";
      } else {
        // No local scraper — try Render's Python (best-effort)
        const result = await callPython("extract", storeUrl).catch((e) => ({ status: "failed", error: e.message }));
        if (result && result.status !== "failed") {
          job.products = (result.products || []).map(mapExtractProduct);
          job.storeName = result.store_name || "";
          job.errors = result.errors || [];
          job.status = "completed";
        } else {
          job.status = "failed";
          job.errors = [result?.error || "Failed to extract products (no Chrome CDP available)"];
        }
      }
      job.total = job.products.length;
      job.completed = job.products.length;
      persistJob(job, true);
      logger.info({ jobId, total: job.products.length }, "Meesho extract job completed");
    })().catch((err: any) => {
      logger.error({ jobId, err: err.message }, "Meesho extract job failed");
      job.status = "failed";
      job.errors = [err.message];
      persistJob(job, true);
    });

    res.json({ jobId, status: "pending" });
  } catch (err: any) {
    logger.error({ err: err.message }, "Meesho extract submit failed");
    res.status(500).json({ error: err.message });
  }
});

router.get("/extract/:jobId", (req: Request, res: Response): void => {
  const job = jobs.get(req.params.jobId as string);
  if (!job || job.type !== "extract") {
    res.json({ status: "failed", total: 0, completed: 0, products: [], errors: ["Job not found"] });
    return;
  }
  res.json({
    status: job.status,
    storeName: job.storeName || "",
    total: job.total,
    completed: job.completed,
    pages: job.pages || 0,
    count: job.products.length,
    // Incremental: `since` is how many the client already has (see /scrape/:jobId).
    products: job.products.slice(Math.max(0, Number(req.query.since ?? 0) || 0)),
    errors: job.errors || [],
  });
});

router.post("/clear-cache", async (_req: Request, res: Response): Promise<void> => {
  try {
    const result = await callPython("clear-cache", "none");
    res.json({ status: "success", message: result.message || "Cache cleared" });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

router.post("/scrape", async (req: Request, res: Response): Promise<void> => {
  try {
    const { urls } = req.body;
    if (!urls || !Array.isArray(urls) || urls.length === 0) {
      res.status(400).json({ error: "URLs array required" });
      return;
    }

    const jobId = generateJobId();
    const job: ScrapeJob = {
      id: jobId,
      type: "scrape",
      status: "pending",
      total: urls.length,
      completed: 0,
      products: [],
      urls,
      createdAt: new Date(),
    };
    jobs.set(jobId, job);
    persistJob(job, true);

    logger.info({ jobId, total: urls.length }, "Starting background scrape job");

    runScrapeJob(job, urls)
      .then(() => {
        logger.info({ jobId, completed: job.completed, total: job.total }, "Scrape job completed");
      })
      .catch((err: any) => {
        logger.error({ jobId, err: err.message }, "Background scrape job failed");
        job.status = "failed";
        job.errors = [err.message];
        persistJob(job, true);
      });

    res.json({ jobId, total: urls.length });
  } catch (err: any) {
    logger.error({ err: err.message }, "Meesho scrape submit failed");
    res.status(500).json({ error: err.message });
  }
});

router.get("/scrape/:jobId", (req: Request, res: Response): void => {
  const job = jobs.get(req.params.jobId as string);
  if (!job) {
    res.json({ status: "failed", total: 0, completed: 0, count: 0, products: [], error: "Job not found" });
    return;
  }
  // Incremental polling: the client passes how many products it already holds so
  // each poll ships only the new ones. Returning the whole list every 3s meant a
  // 4k-product job pushed ~5 MB per poll through the ngrok tunnel until the
  // request failed — which read as "scraping stopped after a few products".
  const since = Math.max(0, Number(req.query.since ?? 0) || 0);
  res.json({
    status: job.status,
    total: job.total,
    completed: job.completed,
    count: job.products.length,
    products: job.products.slice(since),
  });
});

router.post("/cancel/:jobId", (req: Request, res: Response): void => {
  const job = jobs.get(req.params.jobId as string);
  if (job) {
    job.status = "cancelled";
    persistJob(job, true);
  }
  res.json({ status: "cancelled" });
});

// POST /api/scraper/meesho/scrape/:jobId/pause — stop cleanly, keep the job.
// Unlike /cancel this is resumable: the runner notices between chunks, leaves
// the products already scraped in place, and the counter does not reset.
router.post("/scrape/:jobId/pause", (req: Request, res: Response): void => {
  const job = jobs.get(req.params.jobId as string);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  if (job.status === "running" || job.status === "pending") {
    job.status = "paused";
    persistJob(job, true);
    logger.info({ jobId: job.id, completed: job.completed, total: job.total }, "Meesho scrape job paused");
  }
  res.json({ status: job.status, completed: job.completed, total: job.total });
});

// POST /api/scraper/meesho/scrape/:jobId/resume — continue the SAME job.
// runScrapeJob skips every URL that already has a product, so resuming picks up
// where it stopped without re-walking or re-scraping what is done.
router.post("/scrape/:jobId/resume", (req: Request, res: Response): void => {
  const job = jobs.get(req.params.jobId as string);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  if (job.status !== "paused") {
    res.json({ status: job.status, completed: job.completed, total: job.total });
    return;
  }
  const urls = (job.urls as string[]) || [];
  job.status = "running";
  persistJob(job, true);
  logger.info({ jobId: job.id, completed: job.completed, total: job.total }, "Meesho scrape job resumed");
  runScrapeJob(job, urls).catch((err: any) => {
    job.status = "failed";
    job.errors = [err.message];
    persistJob(job, true);
  });
  res.json({ status: "running", completed: job.completed, total: job.total });
});

router.post("/category-summary", async (req: Request, res: Response): Promise<void> => {
  try {
    const { products } = req.body;
    if (!products || !Array.isArray(products)) {
      res.status(400).json({ error: "Products array required" });
      return;
    }
    // Pre-export: how many categories this scraped file holds, and how many
    // products / attribute columns each resulting sheet will get.
    await sendGajabSummary(products, res);
  } catch (err: any) {
    logger.error({ err }, "Category summary failed");
    res.status(500).json({ error: "Category summary failed: " + err.message });
  }
});

router.post("/export", async (req: Request, res: Response): Promise<void> => {
  try {
    const { products, filename = "meesho-products.xlsx" } = req.body;
    if (!products || !Array.isArray(products)) {
      res.status(400).json({ error: "Products array required" });
      return;
    }
    // Gajab ProductImport layout — see gajabExport.ts
    await sendGajabExport(products, filename, res);
  } catch (err: any) {
    logger.error({ err }, "Meesho Excel export failed");
    res.status(500).json({ error: "Export failed: " + err.message });
  }
});

export default router;
