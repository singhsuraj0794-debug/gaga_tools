"""Google Lens via the user's Chrome (CDP) - zero API cost.

SearchAPI is abandoned (paid + monthly quota), so this is the ONLY reverse-search
path the Price Mapper uses.

Concurrency: Lens is the per-product floor (~20s), and bulk mapping runs many
products at once, so searches run on a small persistent pool. Each pool thread
keeps its OWN Playwright + CDP connection because Playwright's sync API is bound
to the thread that created it, and the pool reuses its threads - a thread-local
browser on a ThreadingHTTPServer would instead be built and leaked per request.
"""
import json
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright

CDP_URL = os.environ.get("LENS_CDP_URL", "http://localhost:9222")

# Concurrent Lens pages. Measured: 4 at once is ~4x faster (20.2s -> 5.1s per
# image) but Google returns far fewer URLs (32 -> 9 across 4 images), which
# starves the platform search. 2 is the balance; raise LENS_CONCURRENCY to trade
# recall for speed.
LENS_CONCURRENCY = max(1, int(os.environ.get("LENS_CONCURRENCY", "2") or 2))

# Google Lens redirects to a JS-rendered Google Search page whose result links are
# NOT anchors (measured: 0 of 137 anchors carried a product URL, and there are no
# /url?q= links), so the URLs are extracted from the page HTML instead.
_URL_RX = re.compile(
    r'https?://[^"\'\s<>\\]*(?:amazon\.[a-z.]+|flipkart\.com|meesho\.com)[^"\'\s<>\\]*'
)

_tls = threading.local()
_lens_pool = ThreadPoolExecutor(max_workers=LENS_CONCURRENCY, thread_name_prefix="lens")


def _empty() -> dict:
    return {"amazon": [], "flipkart": [], "meesho": []}


def _worker_browser():
    """This pool thread's own CDP connection (Playwright sync API is thread-bound)."""
    b = getattr(_tls, "browser", None)
    if b is not None:
        try:
            if b.is_connected():
                return b
        except Exception:
            pass
        try:
            if getattr(_tls, "pw", None) is not None:
                _tls.pw.stop()
        except Exception:
            pass
        _tls.pw = None
        _tls.browser = None
    # Chrome down is a NORMAL state (it restarts with the stack). Retrying on
    # every product used to start a Playwright driver per call and leave it
    # running when the connect failed: 393 orphaned node processes (~18 GB) put
    # the whole machine into a page-file OutOfMemory. Stop the driver on failure
    # and back off for a bit instead of hammering.
    if time.time() < getattr(_tls, "retry_at", 0.0):
        return None
    pw = None
    try:
        pw = sync_playwright().start()
        # Fail fast: without a timeout connect_over_cdp waits its 180s default, so
        # one unhealthy Chrome stalled the whole search for three minutes.
        b = pw.chromium.connect_over_cdp(CDP_URL, timeout=15000)
        _tls.pw = pw
        _tls.browser = b
        return b
    except Exception as e:
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
        _tls.pw = None
        _tls.browser = None
        _tls.retry_at = time.time() + 30.0
        print(f"LENS: connect failed (backing off 30s): {e}", file=sys.stderr)
        return None


def _extract(html: str) -> dict:
    """Pull amazon/flipkart/meesho product URLs out of the Lens result HTML."""
    result = _empty()
    html = html.replace("&quot;", '"').replace("&amp;", "&")
    seen = set()
    for link in _URL_RX.findall(html):
        link = link.split("?")[0].split("#")[0].rstrip('"\'\\')
        if link in seen:
            continue
        seen.add(link)
        if "amazon.in" in link and "/dp/" in link:
            result["amazon"].append(link)
        elif "flipkart.com" in link and ("/p/" in link or "/product/" in link):
            result["flipkart"].append(link)
        elif "meesho.com" in link and "/p/" in link:
            result["meesho"].append(link)
    for k in result:
        result[k] = result[k][:3]
    return result


def _run_lens(image_url: str) -> dict:
    """Runs ON a pool thread. Returns the URL dict (empty on any failure)."""
    browser = _worker_browser()
    if not browser:
        print("LENS: Chrome not available", file=sys.stderr)
        return _empty()

    page = None
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        # The Lens result page is a heavy Google Search page (~3MB, mostly
        # thumbnails), so drop images/media/fonts - only the HTML is needed.
        try:
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
        except Exception:
            pass

        page.goto(
            f"https://lens.google.com/uploadbyurl?url={image_url}",
            timeout=60000,
            wait_until="domcontentloaded",
        )
        try:
            page.wait_for_selector('a[href*="http"], [data-ved]', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        html = page.evaluate("() => document.documentElement.outerHTML")
    except Exception as e:
        # Do NOT drop the connection on a page-level failure - the old code set
        # the module browser to None here, so one slow page made every later
        # search return "Chrome not available" instantly.
        print(f"LENS error: {e}", file=sys.stderr)
        return _empty()
    finally:
        # ALWAYS close: only closing on success leaked a heavy Google Search tab
        # per failure until the shared Chrome stopped servicing navigations.
        if page is not None:
            try:
                page.close()
            except Exception:
                pass

    return _extract(html)


def _search_lens(image_url: str) -> dict:
    """Lens-search an image.

    Returns {"amazon": [urls], "flipkart": [urls], "meesho": [urls]}.
    """
    if not image_url:
        return _empty()
    try:
        return _lens_pool.submit(_run_lens, image_url).result(timeout=180)
    except Exception as e:
        print(f"LENS pool error: {e}", file=sys.stderr)
        return _empty()


if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "https://resize.gajab.com/V000049/Img_1783506573840_2njfjyw81tr.jpeg"
    print(json.dumps(_search_lens(img), indent=2))
