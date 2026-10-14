import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { logger } from "./logger.js";

const _dir = path.dirname(fileURLToPath(import.meta.url));

export interface ScrapeJob<T = unknown> {
  id: string;
  type: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  total: number;
  completed: number;
  products: T[];
  errors?: string[];
  createdAt: Date | string;
  [key: string]: unknown;
}

/** A disk-backed job registry.
 *
 * Scrape jobs used to live only in a process-local Map, so any restart (a
 * deploy, a stack restart) silently threw away a run the user had waited on.
 * Writes are coalesced to one per 5s because a multi-thousand-product job is
 * megabytes and is touched on every product; terminal states write immediately
 * so a stop can never lose a finished result.
 *
 * Jobs are stored as JSON beside the build output (dist/ is rebuilt, so the
 * directory lives next to it) and reloaded at startup.
 */
export class JobStore<T = unknown> {
  readonly jobs = new Map<string, ScrapeJob<T>>();
  private readonly dir: string;
  private readonly label: string;
  private readonly dirty = new Set<string>();
  private timer: NodeJS.Timeout | null = null;

  constructor(dirName: string, label: string, envVar?: string) {
    this.label = label;
    this.dir = (envVar && process.env[envVar]) || path.resolve(_dir, "..", dirName);
    try {
      fs.mkdirSync(this.dir, { recursive: true });
    } catch {
      // best effort — persistence is a safety net, not a hard dependency
    }
  }

  persist(job: ScrapeJob<T>, immediate = false): void {
    if (immediate) {
      this.dirty.delete(job.id);
      this.write(job);
      return;
    }
    this.dirty.add(job.id);
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      for (const id of this.dirty) {
        const j = this.jobs.get(id);
        if (j) this.write(j);
      }
      this.dirty.clear();
    }, 5000);
  }

  private write(job: ScrapeJob<T>): void {
    try {
      fs.writeFileSync(path.join(this.dir, `${job.id}.json`), JSON.stringify(job));
    } catch (err: any) {
      logger.warn({ id: job.id, err: err.message }, `failed to persist ${this.label} job`);
    }
  }

  load(): void {
    let files: string[] = [];
    try {
      files = fs.readdirSync(this.dir);
    } catch {
      return;
    }
    for (const f of files) {
      if (!f.endsWith(".json")) continue;
      try {
        const raw = JSON.parse(fs.readFileSync(path.join(this.dir, f), "utf8")) as ScrapeJob<T>;
        if (raw && raw.id) this.jobs.set(raw.id, raw);
      } catch (err: any) {
        logger.warn({ file: f, err: err.message }, `skipping unreadable ${this.label} job file`);
      }
    }
  }

  /** Incremental polling: return only the products the client does not have yet.
   *
   * The UI reaches this API through the ngrok tunnel, and returning the whole
   * list on every poll meant a large job shipped megabytes per request until it
   * failed — which read as "scraping stopped after a few products".
   */
  slice(job: ScrapeJob<T>, sinceRaw: unknown): T[] {
    const since = Math.max(0, Number(sinceRaw ?? 0) || 0);
    return job.products.slice(since);
  }
}
