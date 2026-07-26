import { Router, type Request, type Response } from "express";
import { logger } from "../../lib/logger.js";
import axios from "axios";
import ExcelJS from "exceljs";
import * as xlsx from "xlsx";
import multer from "multer";
import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { runLocalScraper, hasLocalScraper } from "../../lib/localScraper.js";
import {
  SearchEcommerceProductsBody,
  ExportProductsToExcelBody,
} from "@workspace/api-zod";
import type { EcommerceProduct } from "@workspace/api-zod";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRAPER_SCRIPT = path.resolve(__dirname, "_scraper.py");

const router = Router();
const RAPID_API_KEY = process.env.RAPIDAPI_KEY;

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
      if (val.includes("http") || val.includes("flipkart.com") || val.includes("meesho.com") || val.includes("amazon.")) {
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

const storage = multer.memoryStorage();
const upload = multer({ storage: storage });

interface FlipkartDetailedProduct {
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

function buildFlipkartProduct(pid: string, url: string, result: any): FlipkartDetailedProduct {
  if (result.status === "blocked") {
    return { id: pid, title: "Blocked by Flipkart", description: null, meta_description: null, imageUrl: null, images: [], hsn: null, gst: null, dimensions: null, weight: null, specifications: null, variants: null, price: null, url, status: "blocked", error: result.error || "Request blocked by Flipkart's CDN" };
  }
  if (result.status === "failed") {
    return { id: pid, title: "Failed to scrape", description: null, meta_description: null, imageUrl: null, images: [], hsn: null, gst: null, dimensions: null, weight: null, specifications: null, variants: null, price: null, url, status: "failed", error: result.error || "Scraping failed" };
  }
  return { id: pid, title: result.title || "Untitled Product", description: result.description || null, meta_description: result.meta_description || null, imageUrl: (result.images || [])[0] || null, images: result.images || [], hsn: result.hsn || null, gst: result.gst || null, dimensions: result.dimensions || null, weight: result.weight || null, specifications: result.specifications || null, variants: null, price: result.price || null, url, status: "success", error: null };
}

async function scrapeFlipkartProduct(url: string): Promise<FlipkartDetailedProduct> {
  const pid =
    url.match(/pid=([^&]+)/)?.[1] || url.split("/").pop()?.split("?")[0] || url;

  try {
    if (hasLocalScraper()) {
      const localResult = await runLocalScraper(url, "flipkart");
      if (localResult) {
        return buildFlipkartProduct(pid, url, localResult);
      }
    }

    logger.info({ url }, "Scraping Flipkart via Python subprocess");

    const env: Record<string, string> = { ...process.env as Record<string, string> };
    if (process.env.SCRAPER_PROXY) env.SCRAPER_PROXY = process.env.SCRAPER_PROXY;
    if (process.env.SCRAPING_SERVICE_URL) env.SCRAPING_SERVICE_URL = process.env.SCRAPING_SERVICE_URL;

    const { stdout } = await execFileAsync("python3", [SCRAPER_SCRIPT, url], {
      env,
      timeout: 180000,
      maxBuffer: 10 * 1024 * 1024,
    });

    const result = JSON.parse(stdout);
    return buildFlipkartProduct(pid, url, result);
  } catch (err: any) {
    logger.error({ err: err.message, url }, "Python scraper subprocess failed");
    return {
      id: pid,
      title: "Scraper error",
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
      status: "failed",
      error: `Scraper error: ${err.message}`,
    };
  }
}
async function searchFlipkart(query: string): Promise<EcommerceProduct[]> {
  const products: EcommerceProduct[] = [];
  const warnings: string[] = [];

  try {
    const response = await axios.get("https://real-time-flipkart-data2.p.rapidapi.com/search", {
      params: { keyword: query },
      headers: {
        "x-rapidapi-host": "real-time-flipkart-data2.p.rapidapi.com",
        "x-rapidapi-key": RAPID_API_KEY,
      },
      timeout: 30000,
    });

    if (response.data && Array.isArray(response.data)) {
      response.data.forEach((item: any, index: number) => {
        products.push({
          id: item.id || `flipkart-${index}`,
          title: item.title || item.name || "Untitled Product",
          imageUrl: item.image || item.imageUrl || null,
          description: item.description || item.summary || null,
          price: item.price || item.displayPrice || null,
          url: item.url || item.productUrl || null,
          platform: "flipkart",
        });
      });
    } else if (response.data?.data && Array.isArray(response.data.data)) {
      response.data.data.forEach((item: any, index: number) => {
        products.push({
          id: item.id || `flipkart-${index}`,
          title: item.title || item.name || "Untitled Product",
          imageUrl: item.image || item.imageUrl || null,
          description: item.description || item.summary || null,
          price: item.price || item.displayPrice || null,
          url: item.url || item.productUrl || null,
          platform: "flipkart",
        });
      });
    }
  } catch (err: any) {
    logger.error({ err: err.message }, "Flipkart search failed");
    warnings.push(`Flipkart search failed: ${err.message}`);
  }

  return products;
}

async function searchAmazon(query: string): Promise<EcommerceProduct[]> {
  const products: EcommerceProduct[] = [];
  const warnings: string[] = [];

  try {
    // TODO: Add Amazon RapidAPI integration here once you have the endpoint
    logger.warn("Amazon integration not fully implemented yet");
    warnings.push("Amazon integration coming soon");
  } catch (err: any) {
    logger.error({ err: err.message }, "Amazon search failed");
    warnings.push(`Amazon search failed: ${err.message}`);
  }

  return products;
}

router.post("/search", async (req: Request, res: Response): Promise<void> => {
  try {
    const parseResult = SearchEcommerceProductsBody.safeParse(req.body);
    if (!parseResult.success) {
      res.status(400).json({ error: "Invalid request body" });
      return;
    }
    const { query, platform } = parseResult.data;
    const warnings: string[] = [];
    let products: EcommerceProduct[] = [];

    if (!RAPID_API_KEY) {
      warnings.push("RAPID_API_KEY not set");
    }

    switch (platform.toLowerCase()) {
      case "flipkart":
        products = await searchFlipkart(query);
        break;
      case "amazon":
        products = await searchAmazon(query);
        break;
      default:
        res.status(400).json({ error: "Unsupported platform" });
        return;
    }

    const result = {
      products,
      warnings,
    };

    res.json(result);
  } catch (err: any) {
    logger.error({ err }, "Scraper search failed");
    res.status(500).json({ error: "Search failed" });
  }
});

router.post("/export", async (req: Request, res: Response): Promise<void> => {
  try {
    const parseResult = ExportProductsToExcelBody.safeParse(req.body);
    if (!parseResult.success) {
      res.status(400).json({ error: "Invalid request body" });
      return;
    }
    const { products, filename = "scraped-products.xlsx" } = parseResult.data;

    const workbook = new ExcelJS.Workbook();
    const worksheet = workbook.addWorksheet("Products");

    worksheet.columns = [
      { header: "Product ID", key: "id", width: 30 },
      { header: "Title", key: "title", width: 50 },
      { header: "Description", key: "description", width: 80 },
      { header: "Image URL", key: "imageUrl", width: 50 },
      { header: "Price", key: "price", width: 20 },
      { header: "Product URL", key: "url", width: 50 },
      { header: "Platform", key: "platform", width: 15 },
    ];

    products.forEach((product) => {
      worksheet.addRow({
        id: product.id,
        title: product.title,
        description: product.description,
        imageUrl: product.imageUrl,
        price: product.price,
        url: product.url,
        platform: product.platform,
      });
    });

    const headerRow = worksheet.getRow(1);
    headerRow.font = { bold: true };
    headerRow.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FF4F46E5" },
    };
    headerRow.eachCell((cell) => {
      cell.font = { color: { argb: "FFFFFFFF" }, bold: true };
    });

    res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

    await workbook.xlsx.write(res);
    res.end();
  } catch (err: any) {
    logger.error({ err }, "Excel export failed");
    res.status(500).json({ error: "Export failed" });
  }
});

// New endpoints for Flipkart detailed scraping
router.post("/flipkart/upload", upload.single("file"), async (req: Request, res: Response): Promise<void> => {
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
      .filter(url => typeof url === "string" && (url.includes("flipkart.com") || url.includes("http")))
      .map(url => {
        if (!url.startsWith("http")) {
          const cleaned = url.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/^flipkart\.com\//, "");
          return `https://www.flipkart.com/${cleaned}`;
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

router.post("/flipkart/scrape", async (req: Request, res: Response): Promise<void> => {
  try {
    const { urls } = req.body;
    if (!urls || !Array.isArray(urls)) {
      res.status(400).json({ error: "Invalid request, 'urls' array required" });
      return;
    }

    const products: FlipkartDetailedProduct[] = [];
    const errors: string[] = [];

    // Scrape with concurrency limit of 2 (be gentle on Flipkart)
    const concurrency = 2;
    const BATCH_DELAY_MS = 2000; // 2s delay between batches to avoid Flipkart blocking
    for (let i = 0; i < urls.length; i += concurrency) {
      const batch = urls.slice(i, i + concurrency);
      const batchPromises = batch.map(url => scrapeFlipkartProduct(url));
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

router.post("/flipkart/export", async (req: Request, res: Response): Promise<void> => {
  try {
    const { products, filename = "flipkart-products.xlsx" } = req.body;
    if (!products || !Array.isArray(products)) {
      res.status(400).json({ error: "Invalid request, 'products' array required" });
      return;
    }

    const workbook = new ExcelJS.Workbook();
    const worksheet = workbook.addWorksheet("Flipkart Products");

    const maxImages = Math.max(...products.map((p: FlipkartDetailedProduct) => p.images?.length || 0), 1);

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

    products.forEach((product: FlipkartDetailedProduct) => {
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
      fgColor: { argb: "FF28A745" },
    };
    headerRow.eachCell((cell) => {
      cell.font = { color: { argb: "FFFFFFFF" }, bold: true };
    });

    res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

    await workbook.xlsx.write(res);
    res.end();
  } catch (err: any) {
    logger.error({ err }, "Flipkart Excel export failed");
    res.status(500).json({ error: "Export failed: " + err.message });
  }
});

export default router;
