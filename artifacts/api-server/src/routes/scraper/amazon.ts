import { Router, type Request, type Response } from "express";
import { logger } from "../../lib/logger.js";
import ExcelJS from "exceljs";
import * as xlsx from "xlsx";
import multer from "multer";
import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { runLocalScraper, hasLocalScraper } from "../../lib/localScraper.js";
import { sendGajabExport, sendGajabSummary } from "./gajabExport";
import { pythonBin } from "../../lib/py";
import { runBatched } from "../../lib/scrapeBatch";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRAPER_SCRIPT = path.resolve(__dirname, "_amazon_scraper.py");

const router = Router();

const storage = multer.memoryStorage();
const upload = multer({ storage: storage });

interface AmazonDetailedProduct {
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

/** Find the column index that contains the most URL-like values. */
function _findUrlColumn(rows: any[][]): number {
  let bestIdx = -1;
  let bestScore = 0;
  const nonEmpty = rows.filter(r => r.length > 0);
  if (nonEmpty.length === 0) return -1;
  const minCols = Math.min(...nonEmpty.map(r => r.length));
  if (minCols <= 0) return -1;
  for (let col = 0; col < minCols; col++) {
    let score = 0;
    for (const row of rows) {
      const val = String(row[col] ?? "");
      if (val.includes("http") || val.includes("amazon.") || val.includes("amzn.")) {
        score++;
      }
    }
    if (score > bestScore) {
      bestScore = score;
      bestIdx = col;
    }
  }
  return bestIdx;
}

function emptyProduct(id: string, url: string, title: string, status: string, error: string): AmazonDetailedProduct {
  return {
    id,
    title,
    description: null,
    meta_description: null,
    imageUrl: null,
    images: [],
    hsn: null,
    gst: null,
    dimensions: null,
    weight: null,
    specifications: null,
    variants: null,
    price: null,
    url,
    status,
    error,
  };
}

async function scrapeAmazonProduct(url: string): Promise<AmazonDetailedProduct> {
  const asin =
    url.match(/\/dp\/([A-Z0-9]{10})/)?.[1] ||
    url.match(/\/gp\/product\/([A-Z0-9]{10})/)?.[1] ||
    url.split("/").pop()?.split("?")[0] ||
    url;

  try {
    if (hasLocalScraper()) {
      const localResult = await runLocalScraper(url, "amazon");
      if (localResult) {
        if (localResult.status === "blocked") return emptyProduct(asin, url, "Blocked by Amazon", "blocked", localResult.error);
        if (localResult.status === "failed") return emptyProduct(asin, url, "Failed to scrape", "failed", localResult.error);
        return { ...emptyProduct(asin, url, localResult.title || "Untitled", "success", ""), ...localResult };
      }
    }

    logger.info({ url }, "Scraping Amazon via Python subprocess");

    const env: Record<string, string> = { ...process.env as Record<string, string> };
    if (process.env.SCRAPER_PROXY) env.SCRAPER_PROXY = process.env.SCRAPER_PROXY;
    if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;

    const { stdout } = await execFileAsync(pythonBin(), [SCRAPER_SCRIPT, "scrape", url], {
      env,
      timeout: 180000,
      maxBuffer: 10 * 1024 * 1024,
    });

    const result = JSON.parse(stdout);

    if (result.status === "blocked") {
      logger.warn({ url }, "Amazon blocked — " + (result.error || "unknown"));
      return emptyProduct(asin, url, "Blocked by Amazon", "blocked", result.error || "Request blocked by Amazon (captcha)");
    }

    if (result.status === "failed") {
      logger.error({ url }, "Scraper failed — " + (result.error || "unknown"));
      return emptyProduct(asin, url, "Failed to scrape", "failed", result.error || "Scraping failed");
    }

    logger.info({ url, title: result.title }, "Scraped successfully");

    return {
      id: asin,
      title: result.title || "Untitled Product",
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
      variants: null,
      price: result.price || null,
      url,
      status: "success",
      error: null,
    };
  } catch (err: any) {
    logger.error({ err: err.message, url }, "Python scraper subprocess failed");
    return emptyProduct(asin, url, "Scraper error", "failed", `Scraper error: ${err.message}`);
  }
}

/** Walk an Amazon catalogue in bounded page chunks via the tunnel. Each chunk
 *  is ~45s (15 pages), so it fits the tunnel's 300s limit; the whole catalogue
 *  is aggregated across chunks. Shared by the sync route and the job. */
async function walkAmazonCatalogue(
  targetUrl: string,
  onProgress: (total: number, pages: number) => void = () => {},
): Promise<{ storeName: string; products: any[]; error: string }> {
  const tunnel = (process.env.SCRAPER_TUNNEL_URL || "https://headphone-shudder-lavender.ngrok-free.dev").replace(/\/+$/, "");
  const CHUNK_PAGES = 15;
  const MAX_TOTAL_PAGES = 400;
  const all: any[] = [];
  const seen = new Set<string>();
  let storeName = "";
  let nextPage: number | null = 1;
  let lastErr = "";
  let guard = 0;
  while (tunnel && nextPage && nextPage <= MAX_TOTAL_PAGES && guard++ < 60) {
    try {
      const r = await fetch(`${tunnel}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" },
        body: JSON.stringify({ url: targetUrl, page: nextPage, pages: CHUNK_PAGES }),
        signal: AbortSignal.timeout(300000),
      });
      if (!r.ok) {
        let bodyText = "";
        try { bodyText = await r.text(); } catch { /* ignore */ }
        // ngrok returns 404 ERR_NGROK_3200 when the reserved domain's tunnel is
        // down (the Windows host that serves it is off/asleep). Surface that
        // clearly instead of a bare "tunnel HTTP 404".
        if (r.status === 404 && /offline|ERR_NGROK_3200/i.test(bodyText)) {
          lastErr = "Scraper host is OFFLINE — the ngrok tunnel is down. Start the Gajab stack on the Windows PC (desktop-deghg21).";
        } else {
          lastErr = `tunnel HTTP ${r.status}`;
        }
        break;
      }
      const d: any = await r.json();
      storeName = d.storeName || d.store_name || storeName;
      const prods: any[] = d.products || [];
      let added = 0;
      for (const p of prods) {
        if (p?.url && !seen.has(p.url)) { seen.add(p.url); all.push(p); added++; }
      }
      onProgress(all.length, guard);
      if (prods.length === 0 && d.error) { lastErr = d.error; break; }
      if (!d.has_more || !d.next_page) break;
      if (added === 0) break;            // no progress — stop rather than loop
      nextPage = Number(d.next_page);
    } catch (e: any) {
      lastErr = e.message;
      break;
    }
  }
  return { storeName, products: all, error: all.length ? "" : (lastErr || "No products found") };
}

// Amazon extract as a background job: the walk makes several ~45s tunnel calls,
// and a single HTTP request past ~100s is cut off by the host (502). The client
// starts a job and polls.
type AmazonExtractJob = {
  status: "running" | "completed" | "failed";
  startedAt: number;
  total: number;
  pages: number;
  storeName: string;
  products: any[];
  error?: string;
};
const amazonExtractJobs = new Map<string, AmazonExtractJob>();
const AMAZON_EXTRACT_JOB_TTL_MS = 30 * 60 * 1000;

function _normalizeAmazonStoreUrl(url: string): string {
  let u = url.trim();
  if (!u.startsWith("http")) u = `https://www.${u}`;
  return u;
}

router.post("/extract/job", async (req: Request, res: Response): Promise<void> => {
  try {
    const { url } = req.body as { url?: string };
    if (!url || typeof url !== "string") {
      res.status(400).json({ error: "URL required" });
      return;
    }
    const targetUrl = _normalizeAmazonStoreUrl(url);
    const jobId = `ax-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const job: AmazonExtractJob = {
      status: "running", startedAt: Date.now(), total: 0, pages: 0, storeName: "", products: [],
    };
    amazonExtractJobs.set(jobId, job);
    for (const [k, v] of amazonExtractJobs) {
      if (Date.now() - v.startedAt > AMAZON_EXTRACT_JOB_TTL_MS) amazonExtractJobs.delete(k);
    }
    logger.info({ jobId, url: targetUrl }, "Amazon extract job started");
    void walkAmazonCatalogue(targetUrl, (total, pages) => { job.total = total; job.pages = pages; })
      .then((r) => {
        job.storeName = r.storeName;
        job.products = r.products;
        job.total = r.products.length;
        job.status = r.products.length ? "completed" : "failed";
        if (!r.products.length) job.error = r.error || "No products found";
        logger.info({ jobId, count: r.products.length }, "Amazon extract job done");
      })
      .catch((e: any) => { job.status = "failed"; job.error = e.message; });
    res.json({ jobId });
  } catch (err: any) {
    logger.error({ err }, "Amazon extract job start failed");
    res.status(500).json({ error: err.message });
  }
});

router.get("/extract/job/:jobId", (req: Request, res: Response): void => {
  const job = amazonExtractJobs.get(String(req.params.jobId));
  if (!job) {
    res.status(404).json({ error: "job not found" });
    return;
  }
  res.json({
    status: job.status,
    total: job.total,
    pages: job.pages,
    storeName: job.storeName,
    products: job.status === "completed" ? job.products : [],
    error: job.error || "",
    elapsedMs: Date.now() - job.startedAt,
  });
});

router.post("/extract", async (req: Request, res: Response): Promise<void> => {
  try {
    const { url } = req.body;
    if (!url || typeof url !== "string") {
      res.status(400).json({ error: "URL required" });
      return;
    }

    let targetUrl = url.trim();
    if (!targetUrl.startsWith("http")) {
      targetUrl = `https://www.${targetUrl}`;
    }
    if (!targetUrl.includes("amazon.") && !targetUrl.includes("amzn.")) {
      res.status(400).json({ error: "Only Amazon URLs are supported" });
      return;
    }

    logger.info({ url: targetUrl }, "Extracting products from Amazon store/search page");

    // Prefer the local scraper tunnel (residential IP, finishes a 220-product
    // catalogue in 30-60s). Extracting on this host is unreliable: headless
    // Chromium exceeds the proxy timeout (~100s) and the memory limit, which is
    // what produced short catalogues and 502/503s.
    const agg = await walkAmazonCatalogue(targetUrl);
    if (agg.products.length > 0) {
      logger.info({ count: agg.products.length, via: "tunnel" }, "Amazon extract via tunnel (chunked)");
      res.json({ storeName: agg.storeName, products: agg.products, total: agg.products.length, error: "" });
      return;
    }
    logger.warn({ err: agg.error }, "Tunnel chunked extract yielded nothing — falling back");

    const env: Record<string, string> = { ...process.env as Record<string, string> };
    if (process.env.SCRAPER_PROXY) env.SCRAPER_PROXY = process.env.SCRAPER_PROXY;
    if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;

    const { stdout } = await execFileAsync(pythonBin(), [SCRAPER_SCRIPT, "extract", targetUrl], {
      env,
      timeout: 300000,
      maxBuffer: 10 * 1024 * 1024,
    });

    const result = JSON.parse(stdout);
    const products = result.products || [];

    res.json({
      storeName: result.store_name || "",
      products,
      total: products.length,
      error: result.error || "",
    });
  } catch (err: any) {
    logger.error({ err: err.message }, "Amazon extract failed");
    res.status(500).json({ error: err.message });
  }
});

router.post("/upload", upload.single("file"), async (req: Request, res: Response): Promise<void> => {
  try {
    if (!req.file) {
      res.status(400).json({ error: "No file uploaded" });
      return;
    }

    // Read Excel file from buffer
    const workbook = xlsx.read(req.file.buffer, { type: "buffer" });
    const sheetName = workbook.SheetNames[0];
    const worksheet = workbook.Sheets[sheetName];

    // Parse as array of arrays to handle any column structure
    const rows: any[][] = xlsx.utils.sheet_to_json(worksheet, { header: 1 });

    // Collect all cell values that look like URLs
    const allCells: string[] = [];
    for (const row of rows) {
      if (!Array.isArray(row)) continue;
      for (const cell of row) {
        if (cell != null && typeof cell === "string" && cell.trim()) {
          allCells.push(cell.trim());
        }
      }
    }

    // Find column index that contains the most URL-like values
    const columnIndex = _findUrlColumn(rows);
    let urls: string[];
    if (columnIndex >= 0) {
      // Use the detected URL column (skip header row if it contains "url"/"link")
      const startRow = typeof rows[0]?.[columnIndex] === "string" &&
        (rows[0][columnIndex].toLowerCase().includes("url") ||
         rows[0][columnIndex].toLowerCase().includes("link")) ? 1 : 0;
      urls = rows.slice(startRow).map(r => String(r[columnIndex] ?? "")).filter(Boolean);
    } else {
      // Fallback: use all cells that look like URLs
      urls = allCells;
    }

    // Validate and normalize URLs
    const validUrls = urls
      .filter(url => typeof url === "string" && (url.includes("amazon.") || url.includes("amzn.") || url.includes("http")))
      .map(url => {
        if (!url.startsWith("http")) {
          const cleaned = url.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/^(amzn\.in|amazon\.[a-z.]+)\//, "");
          return `https://www.amazon.in/${cleaned}`;
        }
        return url;
      });

    res.json({
      totalUrls: urls.length,
      validUrls: validUrls.length,
      urls: validUrls
    });
  } catch (err: any) {
    logger.error({ err }, "Failed to read Excel file");
    res.status(500).json({ error: "Failed to read Excel file: " + err.message });
  }
});

router.post("/scrape", async (req: Request, res: Response): Promise<void> => {
  try {
    const { urls } = req.body;
    if (!urls || !Array.isArray(urls)) {
      res.status(400).json({ error: "Invalid request, 'urls' array required" });
      return;
    }

    const products: AmazonDetailedProduct[] = [];
    const errors: string[] = [];

    // Bounded concurrency (SCRAPE_CONCURRENCY, default 4) — was hardcoded 2
    // with a 2s pause, which made big stores take ~10 minutes.
    products.push(...(await runBatched(urls, (url) => scrapeAmazonProduct(url))));

    res.json({
      products,
      errors,
      total: urls.length,
    });
  } catch (err: any) {
    logger.error({ err }, "Failed to scrape products");
    res.status(500).json({ error: "Failed to scrape products: " + err.message });
  }
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
    const { products, filename = "amazon-products.xlsx" } = req.body;
    if (!products || !Array.isArray(products)) {
      res.status(400).json({ error: "Invalid request, 'products' array required" });
      return;
    }
    // Gajab ProductImport layout: fixed template columns + one column per
    // attribute that "Attributes Final With Valid Entries.xlsx" defines for each
    // product's mapped category.
    await sendGajabExport(products, filename, res);
  } catch (err: any) {
    logger.error({ err }, "Amazon Excel export failed");
    res.status(500).json({ error: "Export failed: " + err.message });
  }
});

export default router;
