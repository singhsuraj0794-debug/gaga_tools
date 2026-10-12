import axios from "axios";

const LOCAL_SCRAPER_URL = process.env.LOCAL_SCRAPER_URL;

/** True when the local scraper is reached over loopback (no proxy in between). */
function isLoopbackLocalScraper(): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/.test(LOCAL_SCRAPER_URL || "");
}

export async function runLocalScraper(url: string, platform: "flipkart" | "amazon"): Promise<any> {
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

export async function runLocalExtract(url: string): Promise<any> {
  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/extract`,
      { url },
      { timeout: 900000 },
    );
    return data;
  } catch { return null; }
}

export async function runLocalExtractPage(url: string, page: number): Promise<any> {
  if (!LOCAL_SCRAPER_URL) return null;
  try {
    const { data } = await axios.post(
      `${LOCAL_SCRAPER_URL}/extract`,
      { url, page },
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

export function hasLocalScraper(): boolean {
  return !!LOCAL_SCRAPER_URL;
}
