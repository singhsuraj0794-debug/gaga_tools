import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";
import crypto from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const execFileAsync = promisify(execFile);
const SCRAPER_SCRIPT = path.resolve(__dirname, "dist/_meesho_scraper.py");

const jobs = new Map();

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

async function callPython(action, url) {
  const { stdout } = await execFileAsync("python3", [SCRAPER_SCRIPT, action, url], {
    env: { ...process.env },
    timeout: 900000,
    maxBuffer: 200 * 1024 * 1024,
  });
  return JSON.parse(stdout);
}

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

const server = http.createServer(async (req, res) => {
  cors(res);
  res.setHeader("Content-Type", "application/json");

  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  try {
    // ── POST /api/scraper/meesho/extract ──────────────────────────────
    if (req.method === "POST" && req.url === "/api/scraper/meesho/extract") {
      const body = await readBody(req);
      const { url } = JSON.parse(body);
      if (!url || typeof url !== "string") {
        res.writeHead(400);
        res.end(JSON.stringify({ error: "Store URL required" }));
        return;
      }

      const jobId = crypto.randomBytes(8).toString("hex");
      jobs.set(jobId, { status: "running", result: null, started: Date.now() });

      res.writeHead(202);
      res.end(JSON.stringify({ jobId, status: "running", message: "Extraction started" }));

      // Process in background
      (async () => {
        try {
          const result = await callPython("extract", url.trim());
          jobs.set(jobId, { status: "completed", result, elapsed: Date.now() - jobs.get(jobId).started });
        } catch (err) {
          jobs.set(jobId, { status: "failed", error: err.message, elapsed: Date.now() - jobs.get(jobId).started });
        }
      })().catch(() => {});
      return;
    }

    // ── GET /api/scraper/meesho/extract/:id ───────────────────────────
    if (req.method === "GET" && req.url.startsWith("/api/scraper/meesho/extract/")) {
      const jobId = req.url.split("/").pop();
      const job = jobs.get(jobId);
      if (!job) {
        res.writeHead(404);
        res.end(JSON.stringify({ error: "Job not found" }));
        return;
      }
      res.writeHead(200);
      if (job.status === "completed") {
        // Spread Python result: { products, errors, store_name, total_pages, total_products }
        res.end(JSON.stringify({ status: "completed", ...job.result, elapsed_ms: job.elapsed }));
      } else if (job.status === "failed") {
        res.end(JSON.stringify({ status: "failed", error: job.error, elapsed_ms: job.elapsed }));
      } else {
        res.end(JSON.stringify({ status: "running", elapsed_ms: Date.now() - job.started }));
      }
      return;
    }

    // ── POST /api/scraper/meesho/scrape ───────────────────────────────
    if (req.method === "POST" && req.url === "/api/scraper/meesho/scrape") {
      const body = await readBody(req);
      const { urls, products: extractProducts } = JSON.parse(body);
      if (!urls || !Array.isArray(urls) || urls.length === 0) {
        res.writeHead(400);
        res.end(JSON.stringify({ error: "URLs array required" }));
        return;
      }

      // Build lookup of extract data by URL for fallback
      const extractLookup = {};
      if (extractProducts && Array.isArray(extractProducts)) {
        for (const p of extractProducts) {
          if (p.url) extractLookup[p.url] = p;
        }
      }

      const jobId = crypto.randomBytes(8).toString("hex");
      jobs.set(jobId, { status: "running", cancelled: false, results: [], total: urls.length, started: Date.now() });

      res.writeHead(202);
      res.end(JSON.stringify({ jobId, status: "running", total: urls.length }));

      const CONCURRENCY = 3;

      // Process in parallel batches
      (async () => {
        const results = [];
        for (let i = 0; i < urls.length; i += CONCURRENCY) {
          const job = jobs.get(jobId);
          if (job && job.cancelled) break;

          const batch = urls.slice(i, i + CONCURRENCY);

          const batchResults = await Promise.all(
            batch.map(async (url) => {
              const fallback = extractLookup[url] || null;

              // Try Python up to 2 times (ScraperAPI can fail intermittently)
              let scraped = null;
              for (let attempt = 0; attempt < 2; attempt++) {
                try { scraped = await callPython("scrape", url); } catch {}
                if (scraped && scraped.status === "success") break;
                if (attempt < 1) await new Promise(r => setTimeout(r, 2000)); // wait 2s before retry
              }

              if (scraped && scraped.status === "success") {
                return {
                  id: scraped.id || fallback?.id || "",
                  title: scraped.title || fallback?.title || "Untitled",
                  description: scraped.description || fallback?.description || null,
                  meta_description: scraped.meta_description || null,
                  imageUrl: (scraped.images || [])[0] || fallback?.imageUrl || null,
                  images: scraped.images?.length ? scraped.images : (fallback?.images || []),
                  hsn: scraped.hsn || null, gst: scraped.gst || null,
                  dimensions: scraped.dimensions || null,
                  weight: scraped.weight || null,
                  specifications: scraped.specifications || null,
                  variants: scraped.variants || null,
                  price: scraped.price || fallback?.price || null,
                  url, status: "success", error: null,
                };
              } else if (fallback) {
                return {
                  id: fallback.id || "", title: fallback.title || "Untitled",
                  description: fallback.description || null, meta_description: null,
                  imageUrl: fallback.imageUrl || null, images: fallback.images || [],
                  hsn: null, gst: null, dimensions: null, weight: null,
                  specifications: null, variants: null,
                  price: fallback.price || null, url, status: "extracted",
                  error: scraped?.error || "PDP not available",
                };
              } else {
                return {
                  id: "", title: "Failed to scrape", description: null, meta_description: null,
                  imageUrl: null, images: [], hsn: null, gst: null,
                  dimensions: null, weight: null,
                  specifications: null, variants: null, price: null,
                  url, status: "failed", error: scraped?.error || "Scraping failed",
                };
              }
            })
          );

          results.push(...batchResults);
          jobs.set(jobId, { status: "running", results: [...results], total: urls.length, started: jobs.get(jobId).started });
        }
        jobs.set(jobId, { status: "completed", results, total: urls.length, elapsed: Date.now() - jobs.get(jobId).started });
      })().catch(err => {
        jobs.set(jobId, { status: "failed", error: err.message, elapsed: Date.now() - jobs.get(jobId).started });
      });
      return;
    }

    // ── GET /api/scraper/meesho/scrape/:id ────────────────────────────
    if (req.method === "GET" && req.url.startsWith("/api/scraper/meesho/scrape/")) {
      const jobId = req.url.split("/").pop();
      const job = jobs.get(jobId);
      if (!job) {
        res.writeHead(404);
        res.end(JSON.stringify({ error: "Job not found" }));
        return;
      }
      res.writeHead(200);
      if (job.status === "completed") {
        res.end(JSON.stringify({ status: "completed", products: job.results, elapsed_ms: job.elapsed }));
      } else if (job.status === "failed") {
        res.end(JSON.stringify({ status: "failed", error: job.error, products: job.results || [] }));
      } else {
        res.end(JSON.stringify({ status: "running", products: job.results || [], total: job.total || 0, elapsed_ms: Date.now() - job.started }));
      }
      return;
    }

    // ── POST /api/scraper/meesho/export ───────────────────────────────
    if (req.method === "POST" && req.url === "/api/scraper/meesho/export") {
      const body = await readBody(req);
      const { products } = JSON.parse(body);
      if (!products || !Array.isArray(products) || products.length === 0) {
        res.writeHead(400);
        res.end(JSON.stringify({ error: "Products array required" }));
        return;
      }

      const m = await import("xlsx");
      const XLSX = m.default || m;

      // Dynamic image columns based on max images across all products
      const maxImages = Math.max(...products.map(p => (p.images || []).length), 0);
      const imageHeaders = Array.from({ length: maxImages }, (_, i) => `Image ${i + 1}`);

      const headers = ["#", "Title", "Price", "Description", "HSN", "GST", "Dimensions", "Weight", "Variants", "URL", "Status", ...imageHeaders];
      const rows = products.map((p, i) => {
        const images = p.images || [];
        return [
          i + 1,
          p.title || "",
          p.price || "",
          p.description || "",
          p.hsn || "",
          p.gst || "",
          p.dimensions || "",
          p.weight || "",
          p.variants || "",
          p.url || "",
          p.status || "",
          ...Array.from({ length: maxImages }, (_, j) => images[j] || ""),
        ];
      });

      const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);
      ws["!cols"] = headers.map((h, i) => {
        if (i === 1) return { wch: 45 }; // Title
        if (i === 3) return { wch: 50 }; // Description
        if (i === 9) return { wch: 50 }; // URL
        if (i >= 11) return { wch: 55 }; // Image columns
        return { wch: 15 };
      });
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Meesho Products");

      const buffer = XLSX.write(wb, { type: "buffer", bookType: "xlsx" });
      res.writeHead(200, {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": 'attachment; filename="meesho-products.xlsx"',
        "Content-Length": buffer.length,
      });
      res.end(buffer);
      return;
    }

    // ── POST /api/scraper/meesho/cancel/:id ───────────────────────────
    if (req.method === "POST" && req.url.startsWith("/api/scraper/meesho/cancel/")) {
      const jobId = req.url.split("/").pop();
      const job = jobs.get(jobId);
      if (!job) {
        res.writeHead(404);
        res.end(JSON.stringify({ error: "Job not found" }));
        return;
      }
      job.cancelled = true;
      job.status = "cancelled";
      res.writeHead(200);
      res.end(JSON.stringify({ status: "cancelled", products: job.results || [] }));
      return;
    }

    // ── POST /api/scraper/meesho/clear-cache ──────────────────────────
    if (req.method === "POST" && req.url === "/api/scraper/meesho/clear-cache") {
      const result = await callPython("clear-cache", "none");
      res.writeHead(200);
      res.end(JSON.stringify(result));
      return;
    }

    // ── GET /health ───────────────────────────────────────────────────
    if (req.method === "GET" && req.url === "/health") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok" }));
      return;
    }

    // ── 404 ───────────────────────────────────────────────────────────
    res.writeHead(404);
    res.end(JSON.stringify({ error: "Not found" }));
  } catch (err) {
    res.writeHead(500);
    res.end(JSON.stringify({ error: err.message }));
  }
});

const PORT = 8080;
server.listen(PORT, () => {
  console.log(`Server: http://localhost:${PORT}`);
  console.log(`POST /api/scraper/meesho/extract      - Start extract`);
  console.log(`GET  /api/scraper/meesho/extract/:id  - Poll extract status`);
  console.log(`POST /api/scraper/meesho/scrape       - Start scrape`);
  console.log(`GET  /api/scraper/meesho/scrape/:id   - Poll scrape status`);
  console.log(`POST /api/scraper/meesho/export        - Export to Excel`);
  console.log(`POST /api/scraper/meesho/clear-cache   - Clear cache`);
  console.log(`GET  /health                           - Health check`);
});
