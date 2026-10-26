import axios from "axios";

// The scraper runs on the same box as the API, so default to it. It used to be
// env-only (exported by windows-common.ps1); started any other way, EVERY scrape
// silently took the fallback path and spawned a fresh Python process per
// product - the "scraping is slow / products blocked" report.
const LOCAL_SCRAPER_URL = process.env.LOCAL_SCRAPER_URL || "http://localhost:9000";

/** True when the local scraper is reached over loopback (no proxy in between). */
function isLoopbackLocalScraper(): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/.test(LOCAL_SCRAPER_URL || "");
}

export async function runLocalScraper(url: string, platform: "flipkart" | "amazon" | "meesho"): Promise<any> {
  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/scrape`,
      { url, platform },
      { timeout: 120000 },
    );
    return data;
  } catch { return null; }
}

export async function runLocalSearch(title: string, imageUrl: string, price: string, productUrl: string): Promise<any> {
  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/search`,
      { title, imageUrl, price, url: productUrl },
      { timeout: 300000 },
    );
    return data;
  } catch { return null; }
}

export async function runLocalExtract(url: string): Promise<any> {  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/extract`,
      { url },
      { timeout: 900000 },
    );
    return data;
  } catch { return null; }
}

/** Enumerate a whole Meesho store through its own JSON API (one call).
 *
 * The storefront HTML stops paginating after ~2 pages (~40 products) however big
 * the shop is, so paging it across sort orders under-counted badly (a 308-product
 * shop yielded 57). The local server drives /api/v1/meri-shop/feed instead, which
 * is offset-paginated and returns the entire catalogue. Generous timeout: it is a
 * page load plus one throttled request per 20 products. */
export async function runLocalExtractStore(storeUrl: string): Promise<any> {
  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/extract-store`,
      { url: storeUrl },
      { timeout: isLoopbackLocalScraper() ? 240000 : 55000 },
    );
    return data;
  } catch { return null; }
}

export async function runLocalExtractPage(url: string, page: number, sort: string = ""): Promise<any> {
  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/extract`,
      { url, page, sort },
      // extract_page() retries 3x and can take ~122s (30s goto + 10s poll per
      // attempt). Over loopback give it room — measuring a single page took
      // 55.2s, so the old fixed 55000 aborted mid-flight, returned null, and
      // three consecutive nulls ended the whole job as "Extraction failed".
      // Through the ngrok tunnel stay under its ~60s request cap.
      { timeout: isLoopbackLocalScraper() ? 150000 : 55000 },
    );
    return data;
  } catch { return null; }
}

/** Block until the local scraper server answers, or the timeout expires.
 *
 * The refetch and map jobs resume as soon as the API process starts, which on a
 * stack restart is before :9000 is listening. Without this every scrape failed
 * instantly and the whole queue burned through in minutes with no prices updated.
 */
export async function waitForLocalScraper(timeoutMs = 300000): Promise<boolean> {
  if (!LOCAL_SCRAPER_URL) return false;
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const { status } = await axios.get(`${LOCAL_SCRAPER_URL}/healthz`, { timeout: 5000 });
      if (status === 200) return true;
    } catch { /* not up yet */ }
    if (Date.now() >= deadline) return false;
    await new Promise((r) => setTimeout(r, 5000));
  }
}

export function hasLocalScraper(): boolean {
  return !!LOCAL_SCRAPER_URL;
}
