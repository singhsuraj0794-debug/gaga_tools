import { spawn } from "node:child_process";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { pythonBin } from "./py";
import { logger } from "./logger.js";

/**
 * Per-product enrichment queueing.
 *
 * Category + attribute mapping must run WHILE scraping (the GPU is idle then),
 * not when the export is requested. Every scraped product is buffered and
 * flushed in small batches - one python process per product would cost more than
 * the agent itself.
 */
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.resolve(__dirname, "_enrich_queue.py");
const BUFFER = new Map<string, any[]>();
const FLUSH_AT = 10;

export function flushEnrichment(platform: string, force = false): void {
  const buf = BUFFER.get(platform);
  if (!buf || (!force && buf.length < FLUSH_AT)) return;
  const batch = buf.splice(0, buf.length);
  if (!batch.length) return;
  try {
    const tmp = path.join(os.tmpdir(), `enrich-${platform}-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
    fs.writeFileSync(tmp, JSON.stringify({ products: batch }), "utf-8");
    const child = spawn(pythonBin(), [SCRIPT, "enqueue", "--platform", platform, "--products", tmp],
      { cwd: __dirname, stdio: "ignore", windowsHide: true, detached: true });
    child.on("exit", () => { try { fs.unlinkSync(tmp); } catch { /* ignore */ } });
    child.unref();
    logger.info({ platform, products: batch.length }, "queued for background enrichment");
  } catch (err: any) {
    logger.warn({ err: err?.message }, "enrichment flush failed");
  }
}

/** Call for EVERY scraped product, as it completes. */
export function queueForEnrichment(platform: string, product: any): void {
  if (!product || product.status === "failed" || product.status === "blocked") return;
  const buf = BUFFER.get(platform) || [];
  buf.push(product);
  BUFFER.set(platform, buf);
  flushEnrichment(platform);
}
