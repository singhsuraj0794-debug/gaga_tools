import { chromium } from "playwright";

const url = "https://www.flipkart.com/kuber-industries-13-inch-round-beige-jute-placemat-set-4/p/itmfcbdfe7e7b66c";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  viewport: { width: 1280, height: 800 },
  extraHTTPHeaders: {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
  }
});
const page = await context.newPage();

// Hide webdriver
await page.addInitScript(() => {
  Object.defineProperty(navigator, "webdriver", { get: () => undefined });
});

await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(4000);

// Check what's on the page
const title = await page.title();
console.log("Page title:", title);

const bodyText = await page.evaluate(() => document.body.innerText);
console.log("Body preview (first 500 chars):", bodyText.slice(0, 500));

// Check for product name in page
const hasProduct = bodyText.includes("kuber") || bodyText.includes("Kuber") || bodyText.includes("placemat");
console.log("Contains product info:", hasProduct);

// Try to get JSON-LD structured data
const jsonld = await page.evaluate(() => {
  const scripts = document.querySelectorAll('script[type="application/ld+json"]');
  return Array.from(scripts).map(s => s.textContent).join("\n");
});
if (jsonld) {
  console.log("\nJSON-LD data found:", jsonld.slice(0, 500));
}

await browser.close();
