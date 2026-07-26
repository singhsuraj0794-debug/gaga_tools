import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { writeFileSync } from "node:fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const execFileAsync = promisify(execFile);
const SCRAPER_SCRIPT = path.resolve(__dirname, "dist/_meesho_scraper.py");

const url = process.argv[2];
const action = process.argv[3] || "extract";

if (!url) {
  console.log("Usage: node run-extract.mjs <url> [extract|scrape]");
  process.exit(1);
}

console.log(`Running ${action} for ${url}...`);
const start = Date.now();

try {
  const { stdout, stderr } = await execFileAsync("python3", [SCRAPER_SCRIPT, action, url], {
    env: { ...process.env },
    timeout: 900000,
    maxBuffer: 200 * 1024 * 1024,
  });
  
  const result = JSON.parse(stdout);
  const elapsed = ((Date.now() - start) / 1000).toFixed(1);
  
  if (action === "extract") {
    console.log(`Done in ${elapsed}s`);
    console.log(`Store: ${result.store_name || "?"}`);
    console.log(`Products: ${(result.products || []).length}`);
    console.log(`Errors: ${JSON.stringify(result.errors || [])}`);
    
    const outFile = `extract-result-${Date.now()}.json`;
    writeFileSync(path.resolve(__dirname, outFile), JSON.stringify(result, null, 2));
    console.log(`Saved to ${outFile}`);
  } else {
    console.log(`Done in ${elapsed}s`);
    console.log(`Title: ${(result.title || "").slice(0, 60)}`);
    console.log(`Price: ${result.price}`);
  }
} catch (err) {
  console.error(`Failed after ${((Date.now() - start) / 1000).toFixed(1)}s:`, err.message);
  process.exit(1);
}
