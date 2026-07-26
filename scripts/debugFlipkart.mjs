import { chromium } from "playwright";
import { load } from "cheerio";

const url = "https://www.flipkart.com/kuber-industries-13-inch-round-beige-jute-placemat-set-4/p/itmfcbdfe7e7b66c";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
});

await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(2000);

const content = await page.content();
const $ = load(content);

// Find title - try multiple selectors
console.log("=== TITLE SELECTORS ===");
console.log("h1 span:", $("h1 span").first().text().slice(0, 80));
console.log("h1:", $("h1").first().text().slice(0, 80));
// Find text with class containing common Flipkart patterns
$("h1").each((i, el) => console.log(`h1[${i}] classes:`, $(el).attr("class"), "text:", $(el).text().slice(0, 60)));

console.log("\n=== PRICE SELECTORS ===");
// Look for price patterns
$("[class*='price'], [class*='Price']").each((i, el) => {
  const text = $(el).text().trim();
  if (text.includes("₹") && text.length < 30) console.log(`price[${i}]:`, $(el).attr("class"), "=", text);
});

console.log("\n=== SPEC TABLE ===");
// Find spec tables
$("table tr").slice(0, 10).each((i, row) => {
  const cells = $(row).find("td");
  if (cells.length >= 2) {
    console.log(`row[${i}]:`, $(cells[0]).text().trim(), "->", $(cells[1]).text().trim().slice(0, 50));
  }
});

// Also check li items in specs
console.log("\n=== SPEC LIST ITEMS ===");
$("li").each((i, el) => {
  const text = $(el).text().trim();
  if ((text.toLowerCase().includes("hsn") || text.toLowerCase().includes("gst") || text.toLowerCase().includes("weight")) && text.length < 100) {
    console.log(`spec li[${i}]:`, text);
  }
});

await browser.close();
