/** Bounded-concurrency batching for the scrapers.
 *
 * The three scrapers used to hardcode `concurrency = 2` with a 2 s pause
 * between batches, so a 200-product store took ~8-10 minutes. The local scraper
 * server is a threaded HTTP server and the browsers accept several concurrent
 * CDP contexts, so this is tunable now:
 *
 *   SCRAPE_CONCURRENCY   workers in flight      (default 4, max 12)
 *   SCRAPE_BATCH_DELAY_MS pause between batches (default 300)
 *
 * Raise SCRAPE_CONCURRENCY on a beefy box (the Windows host handled 6 mixed
 * Amazon/Flipkart/Meesho workers with no failures); lower it if a marketplace
 * starts returning captchas.
 */
export function scrapeConcurrency(fallback = 4): number {
  const v = Number(process.env.SCRAPE_CONCURRENCY ?? "");
  if (!Number.isFinite(v) || v < 1) return fallback;
  return Math.min(Math.floor(v), 12);
}

export function scrapeBatchDelayMs(fallback = 300): number {
  const v = Number(process.env.SCRAPE_BATCH_DELAY_MS ?? "");
  if (!Number.isFinite(v) || v < 0) return fallback;
  return v;
}

/**
 * Cross-job limiter.
 *
 * Each job used to get its own `SCRAPE_CONCURRENCY` workers, so two Flipkart
 * jobs + one Amazon job ran 3 x N requests at once through the SAME Chrome
 * instances and IP - they starved each other and tripped rate limits. Every
 * item now takes a slot from one shared budget:
 *
 *   SCRAPE_GLOBAL_CONCURRENCY  total requests in flight across ALL jobs (default 10)
 *
 * A single job still gets its own concurrency; extra jobs queue behind the
 * budget instead of multiplying it.
 */
const GLOBAL_LIMIT = (() => {
  const v = Number(process.env.SCRAPE_GLOBAL_CONCURRENCY ?? "");
  return Number.isFinite(v) && v >= 1 ? Math.floor(v) : 10;
})();
let inFlight = 0;
const waiters: Array<() => void> = [];

async function acquireSlot(): Promise<void> {
  if (inFlight < GLOBAL_LIMIT) { inFlight += 1; return; }
  await new Promise<void>((resolve) => waiters.push(resolve));
  inFlight += 1;
}

function releaseSlot(): void {
  inFlight -= 1;
  const next = waiters.shift();
  if (next) next();
}

/** Current global scrape load, for the UI/diagnostics. */
export function scrapeLoad(): { inFlight: number; limit: number; queued: number } {
  return { inFlight, limit: GLOBAL_LIMIT, queued: waiters.length };
}

/** Run `worker` over `items` in bounded-concurrency batches, preserving order. */
export async function runBatched<T, R>(
  items: T[],
  worker: (item: T) => Promise<R>,
  concurrency = scrapeConcurrency(),
  delayMs = scrapeBatchDelayMs(),
): Promise<R[]> {
  const out: R[] = [];
  for (let i = 0; i < items.length; i += concurrency) {
    const chunk = items.slice(i, i + concurrency);
    out.push(...(await Promise.all(chunk.map(async (item) => {
      await acquireSlot();
      try {
        return await worker(item);
      } finally {
        releaseSlot();
      }
    }))));
    if (delayMs > 0 && i + concurrency < items.length) {
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  return out;
}
