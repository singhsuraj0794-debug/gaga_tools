import http from "http";
import { createRequire } from "module";
import { URL } from "url";
const require = createRequire(import.meta.url);
const { createClient } = require("@supabase/supabase-js");
import { execFile } from "child_process";
import { promisify } from "util";
const execFileAsync = promisify(execFile);
import fs from "fs";
import path from "path";

const WS = "/Users/gajabmarketing/.local/ws";
const ENV_PATH = "/Users/gajabmarketing/Library/CloudStorage/GoogleDrive-gajab@aeliyamarine.com/My Drive/Apps/Product-Video-Scraper/artifacts/api-server/.env";
try {
  for (const line of fs.readFileSync(ENV_PATH,"utf8").split("\n")) {
    const t = line.trim();
    if (t && !t.startsWith("#")) { const eq = t.indexOf("="); if (eq > 0 && !process.env[t.slice(0,eq)]) process.env[t.slice(0,eq)] = t.slice(eq+1); }
  }
} catch {}
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
const SEARCHER_SCRIPT = path.join(WS, "_platform_searcher.py");

function json(res, code, obj) { res.writeHead(code, {"Content-Type":"application/json","access-control-allow-origin":"*","access-control-allow-methods":"GET,POST,OPTIONS","access-control-allow-headers":"Content-Type"}); res.end(JSON.stringify(obj)); }

async function callSearch(title, imageUrl, gajabPrice, gajabUrl) {
  try {
    const input = JSON.stringify({ title: title.slice(0,400), image: imageUrl||"", price: gajabPrice||"", url: gajabUrl||"" });
    const { stdout } = await execFileAsync("python3", ["-u", SEARCHER_SCRIPT, "search", input], { timeout: 240000, maxBuffer: 10*1024*1024 });
    return JSON.parse(stdout);
  } catch (e) { return { amazon:{status:"failed",error:"timeout"}, flipkart:{status:"skipped"}, meesho:{status:"skipped"} }; }
}
function getMatchTag(s) { if (s==null) return null; if (s>=85) return "Exact Match"; if (s>=70) return "Match"; if (s>=50) return "Similar"; return "Almost Similar"; }
function platMap(data) {
  const ok = data?.status === "success";
  return { url: ok ? data.url : null, price: ok && !data.unavailable ? data.price : null, match_score: ok ? (data.match_score??null) : null, dinov2_sim: ok ? (data.dinov2_sim??null) : null, clip_sim: ok ? (data.clip_sim??null) : null, match_tag: ok ? getMatchTag(data.match_score) : null, unavailable: ok && data.unavailable === true, source: ok ? (data.source||"search") : null };
}
process.on("uncaughtException", e => console.error("CRASH:", e.message));

// ---- Caches for server-side filtering ----
let _cache = { products: [], mappedIds: new Set(), dupIds: new Set(), ts: 0, loading: null };
async function _loadCaches() {
  if (Date.now() - _cache.ts < 60000 && _cache.products.length > 0) return _cache;
  if (_cache.loading) return _cache.loading;
  _cache.loading = (async () => {
    // Parallel fetch for speed
    const [prodPages, mapPages, dupPages] = await Promise.all([
      Promise.all(Array.from({length:30},(_,i)=>i).map(p => supabase.from("products").select("id,name,price,image_url,url,category").order("name").range(p*1000, p*1000+999))),
      Promise.all(Array.from({length:20},(_,i)=>i).map(p => supabase.from("price_mappings").select("gajab_product_id").or("amazon_url.neq.,flipkart_url.neq.,meesho_url.neq.").range(p*1000, p*1000+999))),
      Promise.all(Array.from({length:10},(_,i)=>i).map(p => supabase.from("product_duplicates").select("product_id").range(p*1000, p*1000+999))),
    ]);
    let products = [];
    for (const r of prodPages) if (r.data) products = products.concat(r.data);
    const mappedIds = new Set();
    for (const r of mapPages) if (r.data) for (const m of r.data) mappedIds.add(m.gajab_product_id);
    const dupIds = new Set();
    for (const r of dupPages) if (r.data) for (const d of r.data) dupIds.add(d.product_id);
    _cache = { products, mappedIds, dupIds, ts: Date.now(), loading: null };
    return _cache;
  })();
  return _cache.loading;
}
function _invalidateCaches() { _cache = { products: [], mappedIds: new Set(), dupIds: new Set(), ts: 0, loading: null }; }

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") { json(res, 204, {}); return; }
  const u = new URL(req.url, "http://localhost");

  // GET /api/price-mapper/products?page=&pageSize=&search=&filter=
  if (u.pathname === "/api/price-mapper/products" && req.method === "GET") {
    try {
      const page = parseInt(u.searchParams.get("page") || "1");
      const pageSize = parseInt(u.searchParams.get("pageSize") || "25");
      const search = (u.searchParams.get("search") || "").toLowerCase().trim();
      const filter = u.searchParams.get("filter") || "all";

      const c = await _loadCaches();
      let list = c.products;
      if (search) list = list.filter(p => (p.name || "").toLowerCase().includes(search));
      if (filter === "mapped") list = list.filter(p => c.mappedIds.has(p.id));
      else if (filter === "unmapped") list = list.filter(p => !c.mappedIds.has(p.id) && !c.dupIds.has(p.id));
      else if (filter === "duplicates") list = list.filter(p => c.dupIds.has(p.id));

      const total = list.length;
      const from = (page - 1) * pageSize;
      const products = list.slice(from, from + pageSize);
      json(res, 200, { products, total });
    } catch (e) { json(res, 500, { error: e.message }); }
    return;
  }

  // GET /api/price-mapper/mappings?ids=
  if (u.pathname === "/api/price-mapper/mappings" && req.method === "GET") {
    try {
      const idsParam = u.searchParams.get("ids") || "";
      let query = supabase.from("price_mappings").select("*");
      if (idsParam) {
        const ids = idsParam.split(",").map(s=>s.trim()).filter(Boolean);
        if (ids.length > 0 && ids.length <= 500) query = query.in("gajab_product_id", ids);
      } else {
        query = query.order("last_checked", {ascending:false}).limit(1000);
      }
      const { data, error } = await query;
      if (error) throw error;
      json(res, 200, { mappings: data || [] });
    } catch (e) { json(res, 200, { mappings: [] }); }
    return;
  }

  // GET /api/price-mapper/mappings-count
  if (u.pathname === "/api/price-mapper/mappings-count" && req.method === "GET") {
    try {
      const { count } = await supabase.from("price_mappings").select("gajab_product_id", {count:"exact", head:true}).or("amazon_url.neq.,flipkart_url.neq.,meesho_url.neq.");
      json(res, 200, { count: count || 0 });
    } catch { json(res, 200, { count: 0 }); }
    return;
  }

  // GET /api/price-mapper/duplicates
  if (u.pathname === "/api/price-mapper/duplicates" && req.method === "GET") {
    try {
      let all = [];
      for (let p = 0; p < 50; p++) {
        const { data, error } = await supabase.from("product_duplicates").select("product_id, duplicate_of, dinov2_score").range(p*1000, p*1000+999);
        if (error) throw error;
        if (!data || data.length === 0) break;
        all = all.concat(data);
        if (data.length < 1000) break;
      }
      json(res, 200, { duplicates: all });
    } catch { json(res, 200, { duplicates: [] }); }
    return;
  }

  // POST /api/price-mapper/compare
  if (u.pathname === "/api/price-mapper/compare" && req.method === "POST") {
    let b=""; req.on("data",c=>b+=c); req.on("end",async()=>{
      try {
        const { product } = JSON.parse(b);
        const r = await callSearch(product.name, product.image_url || product.imageUrl || "", product.price || "", product.url || "");
        const amz = platMap(r.amazon), ms = platMap(r.meesho), fk = platMap(r.flipkart);
        const m = {
          gajab_product_id: product.id, gajab_title: product.name,
          gajab_image_url: product.image_url || product.imageUrl || null,
          gajab_price: product.price || null, gajab_url: product.url || null,
          amazon_url: amz.url, amazon_price: amz.price, amazon_match_score: amz.match_score, amazon_dinov2: amz.dinov2_sim, amazon_clip: amz.clip_sim, amazon_match_tag: amz.match_tag, amazon_unavailable: amz.unavailable,
          meesho_url: ms.url, meesho_price: ms.price, meesho_match_score: ms.match_score, meesho_dinov2: ms.dinov2_sim, meesho_clip: ms.clip_sim, meesho_match_tag: ms.match_tag, meesho_unavailable: ms.unavailable,
          flipkart_url: fk.url, flipkart_price: fk.price, flipkart_match_score: fk.match_score, flipkart_dinov2: fk.dinov2_sim, flipkart_clip: fk.clip_sim, flipkart_match_tag: fk.match_tag, flipkart_unavailable: fk.unavailable,
          last_checked: new Date().toISOString(),
          search_error: { amazon: r.amazon?.status!=="success"?(r.amazon?.error||r.amazon?.status):null, meesho: r.meesho?.status!=="success"?(r.meesho?.error||r.meesho?.status):null, flipkart: r.flipkart?.status!=="success"?(r.flipkart?.error||r.flipkart?.status):null }
        };
        try {
          const { error: e1 } = await supabase.from("price_mappings").upsert(m, {onConflict:"gajab_product_id"});
          if (e1) {
            // Retry without optional Meesho score columns (may not exist yet)
            const { meesho_dinov2, meesho_clip, meesho_match_tag, meesho_unavailable, ...m2 } = m;
            await supabase.from("price_mappings").upsert(m2, {onConflict:"gajab_product_id"});
          }
          _invalidateCaches();
        } catch {}
        json(res, 200, { mapping: m });
      } catch (e) { json(res, 500, { error: e.message }); }
    });
    return;
  }

  // POST /api/price-mapper/save
  if (u.pathname === "/api/price-mapper/save" && req.method === "POST") {
    let b=""; req.on("data",c=>b+=c); req.on("end",async()=>{
      try {
        const { productId, platform, url: pu, price } = JSON.parse(b);
        const upd = { last_checked: new Date().toISOString() };
        if (platform === "amazon") { upd.amazon_url = pu; upd.amazon_price = price || null; }
        else if (platform === "flipkart") { upd.flipkart_url = pu; upd.flipkart_price = price || null; }
        else { upd.meesho_url = pu; upd.meesho_price = price || null; }
        await supabase.from("price_mappings").upsert({ gajab_product_id: productId, ...upd }, { onConflict: "gajab_product_id" }); _invalidateCaches();
        json(res, 200, { success: true });
      } catch { json(res, 200, { success: false }); }
    });
    return;
  }

  // GET /api/price-mapper/export-all — full export from Supabase with seller/category/brand
  if (u.pathname === "/api/price-mapper/export-all" && req.method === "GET") {
    try {
      // Fetch ALL price_mappings (paginated, 1000/page)
      const maps = [];
      let from = 0;
      for (;;) {
        const { data, error } = await supabase.from("price_mappings").select("*").range(from, from + 999);
        if (error) throw error;
        maps.push(...(data || []));
        if (!data || data.length < 1000) break;
        from += 1000;
      }
      const filtered = maps.filter(m => m.amazon_url || m.flipkart_url || m.meesho_url);

      // Fetch seller/category/brand for all products
      const prodMap = new Map();
      let pfrom = 0;
      for (;;) {
        const { data: prods, error } = await supabase.from("products").select("id,seller,category,brand").range(pfrom, pfrom + 999);
        if (error) throw error;
        for (const p of (prods || [])) prodMap.set(p.id, p);
        if (!prods || prods.length < 1000) break;
        pfrom += 1000;
      }

      const h = ["Gajab Product ID","Product Title","Seller","Category","Brand","Gajab Price","Gajab URL","Amazon URL","Amazon Price","Amazon Match Score","Amazon DINOv2","Amazon CLIP","Amazon Match Tag","Amazon Unavailable","Flipkart URL","Flipkart Price","Flipkart Match Score","Flipkart DINOv2","Flipkart CLIP","Flipkart Match Tag","Flipkart Unavailable","Meesho URL","Meesho Price","Meesho Match Score","Meesho DINOv2","Meesho CLIP","Meesho Match Tag","Last Checked"];
      const rows = [h.join(",")];
      for (const m of filtered) {
        const p = prodMap.get(m.gajab_product_id) || {};
        rows.push([m.gajab_product_id,m.gajab_title,p.seller,p.category,p.brand,m.gajab_price,m.gajab_url,m.amazon_url,m.amazon_price,m.amazon_match_score,m.amazon_dinov2!=null?Math.round(m.amazon_dinov2*100)+"%":null,m.amazon_clip!=null?Math.round(m.amazon_clip*100)+"%":null,m.amazon_match_tag,m.amazon_unavailable?"Yes":"No",m.flipkart_url,m.flipkart_price,m.flipkart_match_score,m.flipkart_dinov2!=null?Math.round(m.flipkart_dinov2*100)+"%":null,m.flipkart_clip!=null?Math.round(m.flipkart_clip*100)+"%":null,m.flipkart_match_tag,m.flipkart_unavailable?"Yes":"No",m.meesho_url,m.meesho_price,m.meesho_match_score,m.meesho_dinov2!=null?Math.round(m.meesho_dinov2*100)+"%":null,m.meesho_clip!=null?Math.round(m.meesho_clip*100)+"%":null,m.meesho_match_tag,m.last_checked].map(v=>v!=null?v===''?'':'"'+String(v).replace(/"/g,'""')+'"':"").join(","));
      }
      res.writeHead(200,{"Content-Type":"text/csv","Content-Disposition":`attachment; filename="price-mappings-all-(${filtered.length}).csv"`});
      res.end(rows.join("\n"));
    } catch (e) { json(res, 500, { error: e.message }); }
    return;
  }

  // POST /api/price-mapper/export
  if (u.pathname === "/api/price-mapper/export" && req.method === "POST") {
    let b=""; req.on("data",c=>b+=c); req.on("end",()=>{
      try {
        const { mappings } = JSON.parse(b);
        const h = ["Gajab Product ID","Product Title","Gajab Price","Gajab URL","Amazon URL","Amazon Price","Amazon Match Score","Amazon DINOv2","Amazon CLIP","Amazon Match Tag","Amazon Unavailable","Flipkart URL","Flipkart Price","Flipkart Match Score","Flipkart DINOv2","Flipkart CLIP","Flipkart Match Tag","Flipkart Unavailable","Meesho URL","Meesho Price","Meesho Match Score","Meesho DINOv2","Meesho CLIP","Meesho Match Tag","Last Checked"];
        const rows = [h.join(",")];
        for (const m of (mappings||[])) {
          rows.push([m.gajab_product_id,m.gajab_title,m.gajab_price,m.gajab_url,m.amazon_url,m.amazon_price,m.amazon_match_score,m.amazon_dinov2!=null?Math.round(m.amazon_dinov2*100)+"%":null,m.amazon_clip!=null?Math.round(m.amazon_clip*100)+"%":null,m.amazon_match_tag,m.amazon_unavailable?"Yes":"No",m.flipkart_url,m.flipkart_price,m.flipkart_match_score,m.flipkart_dinov2!=null?Math.round(m.flipkart_dinov2*100)+"%":null,m.flipkart_clip!=null?Math.round(m.flipkart_clip*100)+"%":null,m.flipkart_match_tag,m.flipkart_unavailable?"Yes":"No",m.meesho_url,m.meesho_price,m.meesho_match_score,m.meesho_dinov2!=null?Math.round(m.meesho_dinov2*100)+"%":null,m.meesho_clip!=null?Math.round(m.meesho_clip*100)+"%":null,m.meesho_match_tag,m.last_checked].map(v=>v!=null?'"'+String(v).replace(/"/g,'""')+'"':"").join(","));
        }
        res.writeHead(200,{"Content-Type":"text/csv","Content-Disposition":"attachment; filename=\"price-mappings.csv\""}); res.end(rows.join("\n"));
      } catch (e) { json(res, 500, { error: e.message }); }
    });
    return;
  }

  json(res, 404, { error: "Not found" });
});
server.listen(8080, () => process.stdout.write("Server :8080\n"));
