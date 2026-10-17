#!/usr/bin/env python3
"""Lehlah price comparison (STRICT): reverse-image search each Gajab product,
verify the SAME product with DINOv2 + CLIP + SigLIP (min cosine across models),
and report the lowest price across Amazon/Flipkart/Meesho. Products with no
verified same-product match are left blank (not filled with a lookalike).

Gajab price is scraped from the Gajab product page JSON-LD.

Usage: python3 _lehlah_compare.py products.json out.xlsx [cos_min]
"""
import json
import io
import re
import sys
import os
import threading
import requests
from datetime import datetime
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright

# ---- vision models (DINOv2 / CLIP / SigLIP) ----
MODEL_IDS = {
    "dino": "facebook/dinov2-small",
    "clip": "openai/clip-vit-base-patch32",
    "siglip": "google/siglip-base-patch16-224",
}
_model_names = list(MODEL_IDS)
_models = {}
_img_cache = {}
_emb_cache = {}
_lock = threading.Lock()
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

SUPABASE_URL = "https://headphone-shudder-lavender.ngrok-free.dev"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6ImdhamFiLWxvY2FsIiwiaWF0IjoxNzkyMjYwODIyLCJleHAiOjIxMDc2MjA4MjJ9.MFh31KWtIz71frM1boP1hy3jnCiDPPdyweD412CrX7Y"
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
COS_MIN = 0.85  # min cosine across ALL models to call it the SAME product


def load_models():
    if _models:
        return
    from transformers import AutoImageProcessor, AutoModel
    for name, mid in MODEL_IDS.items():
        try:
            proc = AutoImageProcessor.from_pretrained(mid)
            model = AutoModel.from_pretrained(mid)
            model.eval()
            _models[name] = {"processor": proc, "model": model}
        except Exception as e:
            print(f"  model {name} failed: {str(e)[:60]}", flush=True)
            _model_names.remove(name)


def fetch_image(url):
    if not url:
        return None
    with _lock:
        if url in _img_cache:
            return _img_cache[url]
    img = None
    try:
        req = Request(url, headers=BROWSER_HEADERS)
        with urlopen(req, timeout=20) as resp:
            from PIL import Image
            img = Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception:
        img = None
    with _lock:
        _img_cache[url] = img
    return img


def embed(url):
    with _lock:
        if url in _emb_cache:
            return _emb_cache[url]
    out = {}
    img = fetch_image(url)
    if img is not None:
        try:
            import numpy as np
            import torch
            for name in _model_names:
                m = _models.get(name)
                if not m:
                    continue
                inputs = m["processor"](images=img, return_tensors="pt")
                with torch.no_grad():
                    if name == "dino":
                        vec = m["model"](**inputs).last_hidden_state.mean(dim=1)[0]
                    else:
                        vec = m["model"].get_image_features(**inputs)[0]
                    vec = torch.nn.functional.normalize(vec, dim=0)
                out[name] = vec.cpu().numpy().astype("float32")
        except Exception:
            pass
    with _lock:
        _emb_cache[url] = out
    return out


def similarity(url_a, url_b):
    """Return (semantic_min, cosines_dict) across all models. min=0 if missing."""
    ea, eb = embed(url_a), embed(url_b)
    import numpy as np
    cos = {}
    for name in _model_names:
        if name in ea and name in eb:
            cos[name] = round(float(np.dot(ea[name], eb[name])), 4)
    if not cos:
        return 0.0, cos
    return min(cos.values()), cos


# ---- Gajab price (scrape JSON-LD) ----
def gajab_price(url):
    if not url:
        return None
    # try DB first
    item_id = url.rstrip("/").split("/")[-1]
    for pid in (item_id, f"gajab-{item_id}"):
        try:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/products?id=eq.{pid}&select=price", headers=H, timeout=10)
            d = r.json()
            if d and d[0].get("price"):
                return d[0]["price"]
        except Exception:
            pass
    # scrape the product page
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
        m = re.search(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)', r.text)
        if m:
            return f"₹{m.group(1).rstrip('.0') or m.group(1)}"
    except Exception:
        pass
    return None


def lens_search(ctx, image_url):
    result = {"amazon": [], "flipkart": [], "meesho": []}
    if not image_url:
        return result
    try:
        page = ctx.new_page()
        page.goto(f"https://lens.google.com/uploadbyurl?url={image_url}",
                  timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        html = page.evaluate("() => document.documentElement.outerHTML")
        page.close()
    except Exception as e:
        print(f"  lens error: {str(e)[:60]}", flush=True)
        return result
    html = html.replace("&quot;", '"').replace("&amp;", "&")
    raw = re.findall(r'https?://[^"\'\s<>\\]*(?:amazon\.[a-z.]+|flipkart\.com|meesho\.com)[^"\'\s<>\\]*', html)
    seen = set()
    for link in raw:
        link = link.split("?")[0].split("#")[0].rstrip('"\'')
        if link in seen:
            continue
        seen.add(link)
        if "amazon.in" in link and "/dp/" in link:
            result["amazon"].append(link)
        elif "flipkart.com" in link and ("/p/" in link or "/product/" in link):
            result["flipkart"].append(link)
        elif "meesho.com" in link and "/p/" in link:
            result["meesho"].append(link)
    return result


def _dlog(msg):
    try:
        with open("/tmp/lehlah_dbg.log", "a") as f: f.write(msg + "\n")
    except Exception: pass

def detail(page, url, plat, _retry=1):
    try:
        page.goto(url, timeout=35000, wait_until="domcontentloaded")
        # wait for the product image to actually render (retry on flaky loads)
        try:
            if plat == "amazon":
                page.wait_for_selector("#landingImage[src], #landingImage[data-old-hires], meta[property='og:image']", timeout=15000)
            elif plat == "flipkart":
                page.wait_for_selector("meta[property='og:image'], img[src*='rukminim']", timeout=15000)
            else:
                page.wait_for_selector("meta[property='og:image'], img[src*='images.meesho.com']", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        if plat == "amazon":
            d = page.evaluate("""() => {
                const li = document.querySelector('#landingImage');
                let img = (li && li.getAttribute('data-old-hires')) || '';
                if (!img && li) {
                    const dyn = li.getAttribute('data-a-dynamic-image') || '';
                    try { img = Object.keys(JSON.parse(dyn))[0] || ''; } catch(e) {}
                }
                if (!img) {
                    const h = document.documentElement.innerHTML;
                    const m = h.match(/"hiRes":"(https:[^"]+)"/) || h.match(/"large":"(https:[^"]+)"/);
                    if (m) img = m[1].replace(/\\\\u002F/g, '/');
                }
                if (!img) { img = (li && li.src) || document.querySelector('meta[property="og:image"]')?.content || ''; }
                const buybox = document.querySelector('#desktop_buybox') || document.querySelector('#buybox') || document.querySelector('#ppd');
                const scope = buybox || document.body;
                const unavail = /currently unavailable|out of stock|not available|no featured offers|temporarily out of stock/i.test(scope.innerText || '');
                let price = '';
                const sels = ['#corePriceDisplay_desktop_feature_div .a-price .a-offscreen',
                              '#corePrice_feature_div .a-price .a-offscreen',
                              '#apex_desktop .a-price .a-offscreen',
                              '#price_inside_buybox', '#priceblock_ourprice',
                              '#tp_price_block_total_price_ww',
                              '#desktop_buybox .a-price .a-offscreen'];
                for (const s of sels) { const e = document.querySelector(s); if (e && e.textContent.trim()) { price = e.textContent.trim(); break; } }
                if (!price) {
                    const c = document.querySelector('#corePriceDisplay_desktop_feature_div') || document.querySelector('#corePrice_feature_div');
                    if (c) { const m = (c.innerText||'').match(/₹\\s?[0-9,]+/); if (m) price = m[0].replace(/\\s/g,''); }
                }
                const title = document.querySelector('#productTitle')?.textContent?.trim() || document.title;
                return { price, img: img || '', title, unavailable: unavail };
            }""")
        elif plat == "flipkart":
            d = page.evaluate("""() => {
                const js = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map(s=>s.textContent).join("\\n");
                let price = '';
                const m = js.match(/"price"\\s*:\\s*"?([0-9.]+)/);
                if (m) price = '₹' + parseFloat(m[1]);
                if (!price) { const m2 = document.body.innerText.match(/₹\\s?([0-9,]+)/); if (m2) price = '₹' + m2[1]; }
                const img = document.querySelector('meta[property="og:image"]')?.content || '';
                return { price, img: img || '', title: document.title };
            }""")
        else:
            d = page.evaluate("""() => {
                let price = '';
                const js = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map(s=>s.textContent).join("\\n");
                const m = js.match(/"price"\\s*:\\s*"?([0-9.]+)/);
                if (m) price = '₹' + m[1];
                if (!price) { for (const el of document.querySelectorAll('span,div')) { const t=(el.textContent||'').trim(); const mm=t.match(/^₹\\s?([0-9,]+)$/); if (mm){price='₹'+mm[1];break;} } }
                const img = document.querySelector('meta[property="og:image"]')?.content || document.querySelector('img[src*="images.meesho.com"]')?.src || document.querySelector('img')?.src || '';
                const title = document.querySelector('h1')?.textContent?.trim() || document.title;
                return { price, img: img || '', title };
            }""")
        if _retry and not d.get("img"):
            import time as _t; _t.sleep(1.5)
            return detail(page, url, plat, _retry=_retry - 1)
        return d
    except Exception:
        if _retry:
            try:
                page.reload(timeout=30000)
            except Exception:
                pass
            import time as _t; _t.sleep(1.0)
            return detail(page, url, plat, _retry=_retry - 1)
        return {}


def text_search(plat, query):
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_playwright_search.py")
    try:
        proc = subprocess.run([sys.executable, script, plat, query], capture_output=True, text=True, timeout=120)
        data = json.loads(proc.stdout)
        return data.get("products", []) if data.get("status") == "success" else []
    except Exception:
        return []


def title_from_link(link):
    try:
        slug = link.split("/product-detail/")[1].split("/")[0]
        return re.sub(r"-\d+$", "", slug).replace("-", " ").strip()
    except Exception:
        return ""


def parse_price(s):
    if not s:
        return None
    m = re.search(r"([\d]+(?:\.[\d]+)?)", str(s).replace(",", ""))
    return float(m.group(1)) if m else None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lehlah.json"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/Downloads/lehlah_price_comparison.xlsx")
    cos_min = float(sys.argv[3]) if len(sys.argv) > 3 else COS_MIN
    products = json.load(open(src))
    total = len(products)
    print(f"Lehlah: {total} products (cos_min={cos_min})", flush=True)
    load_models()
    print(f"  models loaded: {list(_models.keys())}", flush=True)

    pw = sync_playwright().start()
    cdp = pw.chromium.connect_over_cdp("http://localhost:9222")
    ctx = cdp.contexts[0] if cdp.contexts else cdp.new_context()
    page = ctx.new_page()

    results = []
    for i, p in enumerate(products, 1):
        link, image = p.get("link", ""), p.get("image", "")
        item_id = link.rstrip("/").split("/")[-1] if link else ""
        pid = item_id if item_id.startswith("gajab-") else f"gajab-{item_id}"
        title = title_from_link(link)
        gp = gajab_price(link)
        row = {"Gajab Link": link, "Gajab Product ID": item_id, "Product Title": title,
               "Gajab Price": gp, "Image": image}
        try:
            rev = lens_search(ctx, image)
        except Exception:
            rev = {"amazon": [], "flipkart": [], "meesho": []}
        query = title

        best = {}
        for plat in ("amazon", "flipkart", "meesho"):
            cands = []  # (sem_min, cosines, url, price, img, title, source)
            def consider(url, price, img, ttl, source):
                if not img:
                    return
                sm, cos = similarity(image, img)
                cands.append([sm, cos, url, price, img, ttl, source])
            for url in rev.get(plat, [])[:4]:
                try:
                    d = detail(page, url, plat)
                    try:
                        open("/tmp/lh.log","a").write(f"{plat} ok={'1' if d.get('img') else '0'} title={(d.get('title') or '')[:25]} img={(d.get('img') or '')[:30]} {url[:45]}\n")
                    except Exception: pass
                except Exception as _e:
                    try: open("/tmp/lh.log","a").write(f"{plat} EXC {str(_e)[:80]} {url[:45]}\n")
                    except Exception: pass
                    continue
                consider(url, d.get("price"), d.get("img", ""), d.get("title", ""), "revimg")
            # text-search fallback only to FIND candidates; still must pass strict check
            if not any(c[0] >= cos_min for c in cands):
                try:
                    for c in text_search(plat, query)[:3]:
                        consider(c.get("url", ""), c.get("price"), c.get("image", ""), c.get("name", ""), "text")
                except Exception:
                    pass
            ok = [c for c in cands if c[0] >= cos_min]
            if ok:
                ok.sort(key=lambda c: (-c[0], parse_price(c[3]) or 1e9))
                c = ok[0]
                best[plat] = {"url": c[2], "price": c[3], "cos": c[0], "cosines": c[1],
                              "title": c[5], "source": c[6]}
        for plat in ("amazon", "flipkart", "meesho"):
            key = {"amazon": "Amazon", "flipkart": "Flipkart", "meesho": "Meesho"}[plat]
            b = best.get(plat, {})
            row[f"{key} URL"] = b.get("url", "")
            row[f"{key} Title"] = b.get("title", "")
            row[f"{key} Price"] = b.get("price")
            row[f"{key} DINOv2"] = b.get("cosines", {}).get("dino")
            row[f"{key} CLIP"] = b.get("cosines", {}).get("clip")
            row[f"{key} SigLIP"] = b.get("cosines", {}).get("siglip")
            row[f"{key} Match (min)"] = round(b["cos"], 4) if b.get("cos") is not None else None

        cands = [(parse_price(row[f"{k} Price"]), k) for k in ("Amazon", "Flipkart", "Meesho") if row.get(f"{k} URL")]
        cands = [(v, k) for v, k in cands if v is not None]
        if cands:
            lp, lk = min(cands)
            row["Lowest Platform"] = lk
            row["Lowest Price"] = lp
        else:
            row["Lowest Platform"] = ""
            row["Lowest Price"] = None

        results.append(row)
        print(f"[{i}/{total}] {title[:42]} -> " +
              ", ".join(f"{k}:{('Y' if best.get(k.lower()) else '-')}" for k in ("Amazon", "Flipkart", "Meesho")) +
              f" | gajab={gp} | lowest {row['Lowest Platform']} {row['Lowest Price']}", flush=True)

    import openpyxl
    from openpyxl.styles import Font
    page.close(); pw.stop()
    cols = ["Gajab Link", "Gajab Product ID", "Product Title", "Gajab Price", "Image",
            "Amazon URL", "Amazon Title", "Amazon Price", "Amazon DINOv2", "Amazon CLIP", "Amazon SigLIP", "Amazon Match (min)",
            "Flipkart URL", "Flipkart Title", "Flipkart Price", "Flipkart DINOv2", "Flipkart CLIP", "Flipkart SigLIP", "Flipkart Match (min)",
            "Meesho URL", "Meesho Title", "Meesho Price", "Meesho DINOv2", "Meesho CLIP", "Meesho SigLIP", "Meesho Match (min)",
            "Lowest Platform", "Lowest Price"]
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Price Comparison"
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in results:
        ws.append([r.get(c) for c in cols])
    for i, c in enumerate(cols, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 30 if "URL" in c else 16
    wb.save(out)
    print(f"\nSaved: {out}", flush=True)


if __name__ == "__main__":
    main()
