import { Router, type Request, type Response } from "express";
import { logger } from "../../lib/logger.js";
import ExcelJS from "exceljs";
import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import crypto from "node:crypto";

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
  status: "pending" | "running" | "completed" | "failed";
  total: number;
  completed: number;
  products: MeeshoDetailedProduct[];
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
  if (process.env.SCRAPER_API_KEY) env.SCRAPER_API_KEY = process.env.SCRAPER_API_KEY;
  if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;
  if (process.env.SCRAPPLEY_API_KEY) env.SCRAPPLEY_API_KEY = process.env.SCRAPPLEY_API_KEY;
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

    logger.info({ storeUrl }, "Extracting products from Meesho store");

    const result = await callPython("extract", storeUrl);

    if (result.status === "failed") {
      res.status(500).json({ error: result.error || "Failed to extract products" });
      return;
    }

    res.json({
      storeName: result.store_name || "",
      products: result.products || [],
      errors: result.errors || [],
      total: (result.products || []).length,
    });
  } catch (err: any) {
    logger.error({ err: err.message }, "Meesho extract failed");
    res.status(500).json({ error: err.message });
  }
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
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  res.json({
    status: job.status,
    total: job.total,
    completed: job.completed,
    products: job.products,
  });
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
