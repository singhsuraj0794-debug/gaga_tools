"""Google Lens via user's Chrome browser (CDP connection) — zero API cost."""
import json, time, sys, threading
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

CDP_URL = "http://localhost:9222"
_lens_lock = threading.Lock()
_pw = None
_browser = None

def _get_browser():
    """Get or create persistent CDP connection. Returns browser or None."""
    global _pw, _browser
    if _browser and _browser.is_connected():
        return _browser
    try:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.connect_over_cdp(CDP_URL)
        return _browser
    except Exception:
        return None

def _search_lens(image_url: str) -> dict:
    """Search image via Google Lens in user's Chrome. Returns {amazon:[urls], flipkart:[urls], meesho:[urls]}"""
    result = {"amazon": [], "flipkart": [], "meesho": []}
    if not image_url:
        return result
    
    with _lens_lock:
        browser = _get_browser()
        if not browser:
            print("LENS: Chrome not available", file=sys.stderr)
            return result
        
        try:
            contexts = browser.contexts
            ctx = contexts[0] if contexts else browser.new_context()
            page = ctx.new_page()
            # Don't bring_to_front — user uses Safari, not Chrome
            
            lens_url = f"https://lens.google.com/uploadbyurl?url={image_url}"
            page.goto(lens_url, timeout=45000, wait_until="domcontentloaded")
            # Wait for actual results using selector, not timeout (works in background tabs)
            try:
                page.wait_for_selector('a[href*="http"]', timeout=15000)
            except:
                pass
            page.wait_for_timeout(2000)
            
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
            _browser = None  # Reset on error
    
    return result

if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "https://resize.gajab.com/V000049/Img_1783506573840_2njfjyw81tr.jpeg"
    r = _search_lens(img)
    print(json.dumps(r, indent=2))
