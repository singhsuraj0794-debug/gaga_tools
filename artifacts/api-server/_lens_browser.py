"""Google Lens via user's Chrome browser (CDP connection) — zero API cost."""
import json, time, sys
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

CDP_URL = "http://localhost:9222"

def _connect_browser():
    """Connect to user's Chrome via CDP. Returns browser or None."""
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        return pw, browser
    except Exception:
        return None, None

def _search_lens(image_url: str) -> dict:
    """Search image via Google Lens in user's Chrome. Returns {amazon:[urls], flipkart:[urls], meesho:[urls]}"""
    result = {"amazon": [], "flipkart": [], "meesho": []}
    if not image_url:
        return result
    
    pw, browser = _connect_browser()
    if not browser:
        print("LENS: Chrome not available", file=sys.stderr)
        return result
    
    try:
        contexts = browser.contexts
        if not contexts:
            ctx = browser.new_context()
        else:
            ctx = contexts[0]
        
        page = ctx.new_page()
        lens_url = f"https://lens.google.com/uploadbyurl?url={image_url}"
        
        page.goto(lens_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        
        links = page.evaluate("""() => {
            const r = [];
            document.querySelectorAll('a[href*="http"]').forEach(a => {
                const h = a.getAttribute('href') || '';
                const t = a.textContent?.trim()?.slice(0,80) || '';
                if (h.startsWith('http') && !h.includes('google.') && !h.includes('gstatic.') && t.length > 5) {
                    r.push({href: h.split('?')[0].split('#')[0], text: t});
                }
            });
            const seen = new Set();
            return r.filter(x => { if (seen.has(x.href)) return false; seen.add(x.href); return true; });
        }""")
        
        for item in links:
            link = item["href"]
            if "amazon.in" in link and "/dp/" in link:
                result["amazon"].append(link)
            elif "flipkart.com" in link and ("/p/" in link or "/product/" in link):
                result["flipkart"].append(link)
            elif "meesho.com" in link and "/p/" in link:
                result["meesho"].append(link)
        
        for k in result:
            result[k] = list(dict.fromkeys(result[k]))[:3]
        
        page.close()
    except Exception as e:
        print(f"LENS error: {e}", file=sys.stderr)
    finally:
        pass  # Keep browser connection open
    
    return result

if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "https://resize.gajab.com/V000049/Img_1783506573840_2njfjyw81tr.jpeg"
    r = _search_lens(img)
    print(json.dumps(r, indent=2))
