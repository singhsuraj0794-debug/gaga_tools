import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

const GATEWAY_BASE = "https://gatewayservice.gajab.com";
const GATEWAY_KEY = "8097571064818418";

async function main() {
  const url = `${GATEWAY_BASE}/product/api/list/custom-product-list?page=1&limit=10`;
  console.log("Fetching 10 items from gateway...");
  const { stdout } = await execFileAsync("curl", [
    "-s",
    "--max-time", "30",
    url,
    "-H", `key: ${GATEWAY_KEY}`,
    "-H", "Origin: https://gajab.com",
    "-H", "Referer: https://gajab.com/",
    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
  ]);
  
  const data = JSON.parse(stdout);
  console.log("First 3 items:");
  console.log(JSON.stringify(data.data.slice(0, 3), null, 2));
}

main().catch(console.error);
