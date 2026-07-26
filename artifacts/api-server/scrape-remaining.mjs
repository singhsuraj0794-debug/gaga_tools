import { createClient } from "@supabase/supabase-js";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const SUPABASE_URL = "https://okxyskmjsmtykblrtmyi.supabase.co";
const SUPABASE_KEY = "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp";
const GATEWAY_KEY = "8097571064818418";

function parseProductData(d) {
  const cats = d.Category ?? [];
  let price = d.price;
  let mrpPrice = d.mrpPrice;
  if ((!price || Number(price) === 0) && Array.isArray(d.skuList)) {
    const defaultSku = d.skuList.find((s) => s.isDefault === 1) ?? d.skuList[0];
    if (defaultSku) {
      if (!price || Number(price) === 0) price = defaultSku.price;
      if (!mrpPrice || Number(mrpPrice) === 0) mrpPrice = defaultSku.mrpPrice;
    }
  }
  return {
    price: price ?? undefined,
    mrpPrice: mrpPrice ?? undefined,
    category: cats.length > 0 ? cats[0].categoryName : undefined,
    brandName: d.brandName ?? undefined,
  };
}

async function scrapeOne(supabase, p) {
  try {
    const url = p.url;
    const parts = url.replace("https://gajab.com/product-detail/", "").split("/");
    if (parts.length < 2) return;
    const slug = parts[0];
    const itemId = parts[1];
    const apiUrl = `https://gatewayservice.gajab.com/product/api/product-store/product/${slug}/${itemId}?pincode=`;
    const { stdout } = await execFileAsync("curl", [
      "-s", "--max-time", "15", apiUrl,
      "-H", `key: ${GATEWAY_KEY}`,
      "-H", "Content-type: application/json",
      "-H", "Origin: https://gajab.com",
      "-H", "Referer: https://gajab.com/"
    ], { maxBuffer: 1024 * 1024 });
    const resp = JSON.parse(stdout);
    const d = resp.data;
    if (!d) { console.log(`  No data for ${p.id}`); return; }
    const pd = parseProductData(d);
    if (!pd) return;
    const updates = {};
    const priceNum = Number(pd.price);
    const mrpNum = Number(pd.mrpPrice);
    if (priceNum > 0 && mrpNum > 0) {
      updates.price = `₹${priceNum.toLocaleString("en-IN")}`;
      updates.mrp_price = `₹${mrpNum.toLocaleString("en-IN")}`;
    } else if (priceNum > 0) {
      updates.price = `₹${priceNum.toLocaleString("en-IN")}`;
      updates.mrp_price = `₹${priceNum.toLocaleString("en-IN")}`;
    }
    if (pd.category) updates.category = pd.category;
    if (pd.brandName) updates.brand = pd.brandName;
    console.log(`  ${p.id}: price=${priceNum} mrp=${mrpNum} cat=${pd.category} brand=${pd.brandName}`);
    if (Object.keys(updates).length > 0) {
      const { error } = await supabase.from("products").update(updates).eq("id", p.id);
      if (error) console.log(`  Update error: ${error.message}`);
      else console.log(`  ✓ Updated ${p.id}`);
    }
  } catch (err) {
    console.log(`  Error: ${err?.message?.slice(0, 100)}`);
  }
}

async function main() {
  const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
  const { data: products, error } = await supabase
    .from("products")
    .select("id, url, price, mrp_price")
    .or("price.is.null,mrp_price.is.null")
    .limit(50);
  if (error) { console.error("Fetch error:", error); return; }
  console.log(`Found ${products.length} products with null prices:\n`);
  for (const p of products) {
    console.log(`Processing: ${p.id}`);
    console.log(`  URL: ${p.url?.slice(0, 80)}`);
    await scrapeOne(supabase, p);
    console.log();
  }
  console.log("Done");
}

main().catch(console.error);
