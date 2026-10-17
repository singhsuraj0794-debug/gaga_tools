"""Google Lens via user's Chrome browser (CDP connection) — zero API cost."""
import json, time, sys, threading, re
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

CDP_URL = "http://localhost:9222"
_lens_lock = threading.Lock()
_pw = None
_browser = None

# Google Lens now redirects to a JS-rendered Google Search page whose result
# links are NOT anchors (measured: 0 of 137 anchors carried a product URL, and
# there are no /url?q= links). The product URLs are still in the page HTML, so
# match them directly. Google's own ordering is kept (most relevant first).
_LENS_PATTERNS = (
    ("amazon",   re.compile(r'https://www\.amazon\.in/[^\s"\\<>]*?/dp/[A-Z0-9]{10}')),
    ("flipkart", re.compile(r'https://www\.flipkart\.com/[^\s"\\<>]*?/p/itm[A-Za-z0-9]+')),
    ("meesho",   re.compile(r'https://www\.meesho\.com/[^\s"\\<>]*?/p/[a-z0-9]{4,}')),
)

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
                page.wait_for_selector('a[href*="http"], [data-ved]', timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(3000)

            html = page.content()
            page.close()

            for key, rx in _LENS_PATTERNS:
                for m in rx.finditer(html):
                    link = m.group(0).split("?")[0].split("#")[0].rstrip("\\")
                    if link not in result[key]:
                        result[key].append(link)

            for k in result:
                result[k] = result[k][:3]
        except Exception as e:
            print(f"LENS error: {e}", file=sys.stderr)
            _browser = None  # Reset on error
    
    return result

if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "https://resize.gajab.com/V000049/Img_1783506573840_2njfjyw81tr.jpeg"
    r = _search_lens(img)
    print(json.dumps(r, indent=2))
