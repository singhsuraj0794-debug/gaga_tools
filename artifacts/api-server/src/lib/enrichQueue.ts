import path from "node:path";
import fs from "node:fs";
import { logger } from "./logger.js";

/**
 * Per-product enrichment queueing.
 *
 * Category + attribute mapping must run WHILE scraping (the GPU is idle then),
 * not when the export is requested. Each scraped product is buffered and written
 * to a SPOOL FILE that the background worker ingests; no child process is spawned
 * per flush (that failed silently and is slow).
 */
const SPOOL_DIR = process.env.ENRICH_SPOOL_DIR || "C:\\gajab\\enrich_spool";
const BUFFER = new Map<string, any[]>();
const FLUSH_AT = 5;

export function flushEnrichment(platform: string, force = false): void {
  const buf = BUFFER.get(platform);
  if (!buf || (!force && buf.length < FLUSH_AT)) return;
  const batch = buf.splice(0, buf.length);
  if (!batch.length) return;
  try {
    fs.mkdirSync(SPOOL_DIR, { recursive: true });
    const name = `${platform}-${Date.now()}-${Math.random().toString(36).slice(2)}.json`;
    const tmp = path.join(SPOOL_DIR, `.${name}.tmp`);
    const final = path.join(SPOOL_DIR, name);
    fs.writeFileSync(tmp, JSON.stringify({ platform, products: batch }), "utf-8");
    fs.renameSync(tmp, final);          // atomic: the worker never sees a half file
    logger.info({ platform, products: batch.length }, "spooled for background enrichment");
  } catch (err: any) {
    logger.warn({ err: err?.message }, "enrichment spool failed");
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
