import { Router, type Request, type Response } from "express";
import { logger } from "../../lib/logger.js";
import ExcelJS from "exceljs";
import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import crypto from "node:crypto";
import { runLocalScraper, runLocalExtract, runLocalExtractPage, hasLocalScraper } from "../../lib/localScraper.js";

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
  variants: string | null;
  price: string | null;
  url: string;
  status: string;
  error: string | null;
}

interface ScrapeJob {
  id: string;
  type: "scrape" | "extract";
  status: "pending" | "running" | "completed" | "failed";
  total: number;
  completed: number;
  products: MeeshoDetailedProduct[];
  storeName?: string;
  errors?: string[];
  createdAt: Date;
}

const jobs = new Map<string, ScrapeJob>();

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

  const { stdout } = await execFileAsync("python3", [SCRAPER_SCRIPT, action, url], {
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
        variants: localResult.variants || null,
        price: localResult.price || null,
        url,
        status: "success",
        error: null,
      };
    }
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
      total: 1,
      completed: 0,
      products: [],
      storeName: "",
      errors: [],
      createdAt: new Date(),
    };
    jobs.set(jobId, job);

    (async () => {
      job.status = "running";

      // Map a raw product to the detailed product shape
      const mapProduct = (p: any): MeeshoDetailedProduct => ({
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
      });

      const seen = new Set<string>();
      const allProducts: MeeshoDetailedProduct[] = [];
      let storeName = "";

      if (hasLocalScraper()) {
        // Paginate via the local Chrome-CDP scraper in sub-60s chunks (ngrok limit)
        let page = 1;
        let consecutiveEmpty = 0;
        const MAX_PAGES = 300;
        while (page <= MAX_PAGES) {
          const chunk = await runLocalExtractPage(storeUrl, page);
          if (!chunk) {
            job.errors = job.errors || [];
            job.errors.push(`Failed to reach local scraper on page ${page}`);
            consecutiveEmpty++;
          } else if (chunk.products && chunk.products.length > 0) {
            consecutiveEmpty = 0;
            for (const p of chunk.products) {
              const url = p.url || "";
              if (!url || seen.has(url)) continue;
              seen.add(url);
              allProducts.push(mapProduct(p));
            }
          } else {
            consecutiveEmpty++;
          }
          job.completed = allProducts.length;
          if (consecutiveEmpty >= 3) break;  // end of products
          page++;
        }
        job.products = allProducts;
        job.storeName = storeName;
        job.errors = job.errors || [];
        job.total = allProducts.length;
        job.completed = allProducts.length;
        job.status = "completed";
      } else {
        // No local scraper — try Render's Python (best-effort)
        const result = await callPython("extract", storeUrl).catch((e) => ({ status: "failed", error: e.message }));
        if (result && result.status !== "failed") {
          job.products = (result.products || []).map(mapProduct);
          job.storeName = result.store_name || "";
          job.errors = result.errors || [];
          job.completed = job.products.length;
          job.total = job.products.length;
          job.status = "completed";
        } else {
          job.status = "failed";
          job.errors = [result?.error || "Failed to extract products (no Chrome CDP available)"];
        }
      }
      logger.info({ jobId, total: job.products.length }, "Meesho extract job completed");
    })().catch((err: any) => {
      logger.error({ jobId, err: err.message }, "Meesho extract job failed");
      job.status = "failed";
      job.errors = [err.message];
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
    products: job.products,
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
      createdAt: new Date(),
    };
    jobs.set(jobId, job);

    logger.info({ jobId, total: urls.length }, "Starting background scrape job");

    (async () => {
      job.status = "running";
      // Process 2 at a time (ScraperAPI can handle this with shorter timeouts)
      const concurrency = 2;
      for (let i = 0; i < urls.length; i += concurrency) {
        const batch = urls.slice(i, i + concurrency);
        const results = await Promise.all(batch.map(url => scrapeProduct(url)));
        for (const product of results) {
          job.products.push(product);
          job.completed++;
        }
      }
      job.status = "completed";
      logger.info({ jobId, completed: job.completed, total: job.total }, "Scrape job completed");
    })().catch((err: any) => {
      logger.error({ jobId, err: err.message }, "Background scrape job failed");
      job.status = "failed";
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
    res.json({ status: "failed", total: 0, completed: 0, products: [], error: "Job not found" });
    return;
  }
  res.json({
    status: job.status,
    total: job.total,
    completed: job.completed,
    products: job.products,
  });
});

router.post("/cancel/:jobId", (req: Request, res: Response): void => {
  const job = jobs.get(req.params.jobId as string);
  if (job) {
    job.status = "cancelled";
  }
  res.json({ status: "cancelled" });
});

router.post("/export", async (req: Request, res: Response): Promise<void> => {
  try {
    const { products, filename = "meesho-products.xlsx" } = req.body;
    if (!products || !Array.isArray(products)) {
      res.status(400).json({ error: "Products array required" });
      return;
    }

    const workbook = new ExcelJS.Workbook();
    const worksheet = workbook.addWorksheet("Meesho Products");

    const maxImages = Math.max(...products.map((p: MeeshoDetailedProduct) => p.images?.length || 0), 1);

    const columns: any[] = [
      { header: "Product ID", key: "id", width: 30 },
      { header: "Title", key: "title", width: 50 },
      { header: "Description", key: "description", width: 80 },
      { header: "Meta Description", key: "meta_description", width: 80 },
    ];
    for (let i = 1; i <= maxImages; i++) {
      columns.push({ header: `Image ${i}`, key: `image${i}`, width: 60 });
    }
    columns.push(
      { header: "HSN", key: "hsn", width: 20 },
      { header: "GST", key: "gst", width: 15 },
      { header: "Dimensions", key: "dimensions", width: 30 },
      { header: "Weight", key: "weight", width: 20 },
      { header: "Specifications", key: "specifications", width: 100 },
      { header: "Variants", key: "variants", width: 100 },
      { header: "Price", key: "price", width: 20 },
      { header: "Product URL", key: "url", width: 80 },
    );
    worksheet.columns = columns;

    products.forEach((product: MeeshoDetailedProduct) => {
      const specsStr = product.specifications
        ? Object.entries(product.specifications).map(([k, v]) => `${k}: ${v}`).join("\n")
        : "";
      const row: any = {
        id: product.id,
        title: product.title,
        description: product.description,
        meta_description: product.meta_description,
        hsn: product.hsn,
        gst: product.gst,
        dimensions: product.dimensions,
        weight: product.weight,
        specifications: specsStr,
        variants: product.variants,
        price: product.price,
        url: product.url,
      };
      (product.images || []).forEach((img: string, i: number) => {
        row[`image${i + 1}`] = img;
      });
      worksheet.addRow(row);
    });

    const headerRow = worksheet.getRow(1);
    headerRow.font = { bold: true };
    headerRow.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FFFF6B35" },
    };
    headerRow.eachCell((cell) => {
      cell.font = { color: { argb: "FFFFFFFF" }, bold: true };
    });

    res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

    await workbook.xlsx.write(res);
    res.end();
  } catch (err: any) {
    logger.error({ err }, "Meesho Excel export failed");
    res.status(500).json({ error: "Export failed: " + err.message });
  }
});

export default router;
