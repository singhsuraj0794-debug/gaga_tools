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
    out.push(...(await Promise.all(chunk.map(worker))));
    if (delayMs > 0 && i + concurrency < items.length) {
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  return out;
}
