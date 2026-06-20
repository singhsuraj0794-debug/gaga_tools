import { Router, type Request, type Response } from "express";
import { logger } from "../../lib/logger.js";
import axios from "axios";
import ExcelJS from "exceljs";
import * as xlsx from "xlsx";
import multer from "multer";
import { chromium, type Browser, type Page } from "playwright";
import { load } from "cheerio";
import {
  SearchEcommerceProductsBody,
  SearchEcommerceProductsResponse,
  ExportProductsToExcelBody,
} from "@workspace/api-zod";
import type { EcommerceProduct } from "@workspace/api-zod";

const router = Router();
const RAPID_API_KEY = "e4f0168123msh21c83ca8fa786cap141b25jsn6b69c0e25be1";

const storage = multer.memoryStorage();
const upload = multer({ storage: storage });

interface FlipkartDetailedProduct {
  id: string;
  title: string;
  description: string | null;
  imageUrl: string | null;
  hsn: string | null;
  gst: string | null;
  dimensions: string | null;
  weight: string | null;
  variants: string | null;
  price: string | null;
  url: string;
}

let browser: Browser | null = null;

async function getBrowser(): Promise<Browser> {
  if (!browser) {
    browser = await chromium.launch({
      headless: true });
  }
  return browser;
}

async function scrapeFlipkartProduct(url: string): Promise<FlipkartDetailedProduct> {
  try {
    logger.info({ url }, "Starting to scrape Flipkart product");
    
    const browser = await getBrowser();
    const page = await browser.newPage({
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    });

    // Navigate to the product page
    await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(2000); // Wait a bit for content to load

    const content = await page.content();
    const $ = load(content);

    // Extract product ID
    let productId = "";
    const pidMatch = url.match(/pid=([^&]+)/);
    if (pidMatch) {
      productId = pidMatch[1];
    } else {
      const parts = url.split("/");
      productId = parts[parts.length - 1].split("?")[0];
    }

    // Extract title
    const title = $("h1 span.B_NuCI").first().text().trim() || "Untitled Product";

    // Extract price
    const price = $("div._30jeq3._16Jk6d").first().text().trim() || null;

    // Extract image URL
    const imageUrl = $("img._396cs4._2amPTt._3qGpsk").first().attr("src") || $("div._2c7aJz img").first().attr("src") || null;

    // Extract description
    const description = $("div._1mXcCf").text().trim() || $("div._3mX-Xb").text().trim() || null;

    // Extract specifications
    let hsn: string | null = null;
    let gst: string | null = null;
    let dimensions: string | null = null;
    let weight: string | null = null;
    let variants: string | null = null;

    // Try to find specifications from product details table
    $("tr").each((_, row) => {
      const cells = $(row).find("td");
      if (cells.length >= 2) {
        const key = $(cells[0]).text().trim().toLowerCase();
        const value = $(cells[1]).text().trim();
        
        if (key.includes("hsn")) hsn = value;
        if (key.includes("gst")) gst = value;
        if (key.includes("dimension") || key.includes("size")) dimensions = value;
        if (key.includes("weight")) weight = value;
      }
    });

    await page.close();

    logger.info({ productId, title }, "Successfully scraped product");

    return {
      id: productId,
      title,
      description,
      imageUrl,
      hsn,
      gst,
      dimensions,
      weight,
      variants,
      price,
      url,
    };
  } catch (err: any) {
    logger.error({ err: err.message, url }, "Failed to scrape Flipkart product");
    // Return minimal product with error info
    return {
      id: url,
      title: "Failed to scrape product",
      description: err.message,
      imageUrl: null,
      hsn: null,
      gst: null,
      dimensions: null,
      weight: null,
      variants: null,
      price: null,
      url: url,
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

async function searchMeesho(query: string): Promise<EcommerceProduct[]> {
  const products: EcommerceProduct[] = [];
  const warnings: string[] = [];

  try {
    // TODO: Add Meesho RapidAPI integration here once you have the endpoint
    logger.warn("Meesho integration not fully implemented yet");
    warnings.push("Meesho integration coming soon");
  } catch (err: any) {
    logger.error({ err: err.message }, "Meesho search failed");
    warnings.push(`Meesho search failed: ${err.message}`);
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
      case "meesho":
        products = await searchMeesho(query);
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
    const data = xlsx.utils.sheet_to_json(worksheet);

    // Extract URLs - assume first column contains URLs or look for "url" column
    let urls: string[] = [];
    if (data.length > 0) {
      const firstRow = data[0] as any;
      // Check if there's a column with "url" or "link" in name
      const urlKeys = Object.keys(firstRow).filter(key => 
        key.toLowerCase().includes("url") || key.toLowerCase().includes("link")
      );
      
      if (urlKeys.length > 0) {
        urls = data.map((row: any) => String(row[urlKeys[0]])).filter(Boolean);
      } else {
        // If no URL column, assume first column is URLs
        urls = data.map((row: any) => String(Object.values(row)[0])).filter(Boolean);
      }
    }

    // Validate URLs
    const validUrls = urls.filter(url => 
      typeof url === "string" && (url.includes("flipkart.com") || url.includes("http"))
    );

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

    // Scrape each URL with concurrency limit (to avoid hitting rate limits)
    const concurrency = 3;
    for (let i = 0; i < urls.length; i += concurrency) {
      const batch = urls.slice(i, i + concurrency);
      const batchPromises = batch.map(url => scrapeFlipkartProduct(url));
      const batchResults = await Promise.all(batchPromises);
      products.push(...batchResults);
    }

    res.json({ products, errors });
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

    worksheet.columns = [
      { header: "Product ID", key: "id", width: 30 },
      { header: "Title", key: "title", width: 50 },
      { header: "Description", key: "description", width: 80 },
      { header: "Image URL", key: "imageUrl", width: 50 },
      { header: "HSN", key: "hsn", width: 20 },
      { header: "GST", key: "gst", width: 15 },
      { header: "Dimensions", key: "dimensions", width: 30 },
      { header: "Weight", key: "weight", width: 20 },
      { header: "Variants", key: "variants", width: 100 },
      { header: "Price", key: "price", width: 20 },
      { header: "Product URL", key: "url", width: 80 },
    ];

    products.forEach((product: FlipkartDetailedProduct) => {
      worksheet.addRow({
        id: product.id,
        title: product.title,
        description: product.description,
        imageUrl: product.imageUrl,
        hsn: product.hsn,
        gst: product.gst,
        dimensions: product.dimensions,
        weight: product.weight,
        variants: product.variants,
        price: product.price,
        url: product.url,
      });
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
