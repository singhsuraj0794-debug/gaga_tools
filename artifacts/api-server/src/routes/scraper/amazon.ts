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
        return { ...emptyProduct(asin, url, localResult.title || "Untitled", "success", null), ...localResult };
      }
    }

    logger.info({ url }, "Scraping Amazon via Python subprocess");

    const env: Record<string, string> = { ...process.env as Record<string, string> };
    if (process.env.SCRAPER_PROXY) env.SCRAPER_PROXY = process.env.SCRAPER_PROXY;
    if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;

    const { stdout } = await execFileAsync("python3", [SCRAPER_SCRIPT, "scrape", url], {
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

    const env: Record<string, string> = { ...process.env as Record<string, string> };
    if (process.env.SCRAPER_PROXY) env.SCRAPER_PROXY = process.env.SCRAPER_PROXY;
    if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;

    const { stdout } = await execFileAsync("python3", [SCRAPER_SCRIPT, "extract", targetUrl], {
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

    // Scrape with concurrency limit of 2 (be gentle on Amazon)
    const concurrency = 2;
    const BATCH_DELAY_MS = 2000; // 2s delay between batches to avoid Amazon blocking
    for (let i = 0; i < urls.length; i += concurrency) {
      const batch = urls.slice(i, i + concurrency);
      const batchPromises = batch.map(url => scrapeAmazonProduct(url));
      const batchResults = await Promise.all(batchPromises);
      products.push(...batchResults);
      if (i + concurrency < urls.length) {
        await new Promise(resolve => setTimeout(resolve, BATCH_DELAY_MS));
      }
    }

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

router.post("/export", async (req: Request, res: Response): Promise<void> => {
  try {
    const { products, filename = "amazon-products.xlsx" } = req.body;
    if (!products || !Array.isArray(products)) {
      res.status(400).json({ error: "Invalid request, 'products' array required" });
      return;
    }

    const workbook = new ExcelJS.Workbook();
    const worksheet = workbook.addWorksheet("Amazon Products");

    const maxImages = Math.max(...products.map((p: AmazonDetailedProduct) => p.images?.length || 0), 1);

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

    products.forEach((product: AmazonDetailedProduct) => {
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
      fgColor: { argb: "FFFF9900" },
    };
    headerRow.eachCell((cell) => {
      cell.font = { color: { argb: "FFFFFFFF" }, bold: true };
    });

    res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

    await workbook.xlsx.write(res);
    res.end();
  } catch (err: any) {
    logger.error({ err }, "Amazon Excel export failed");
    res.status(500).json({ error: "Export failed: " + err.message });
  }
});

export default router;
