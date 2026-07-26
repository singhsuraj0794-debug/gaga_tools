import axios from "axios";

const LOCAL_SCRAPER_URL = process.env.LOCAL_SCRAPER_URL;

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

export function hasLocalScraper(): boolean {
  return !!LOCAL_SCRAPER_URL;
}
