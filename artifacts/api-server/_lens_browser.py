"""Google Lens via the user's Chrome (CDP) — zero API cost.

SearchAPI is abandoned (paid + monthly quota), so this is the ONLY reverse-search
path the Price Mapper uses.
"""
import json
import re
import sys
import threading
import time

from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"
_lens_lock = threading.Lock()
_pw = None
_browser = None

# Google Lens redirects to a JS-rendered Google Search page whose result links are
# NOT anchors (measured: 0 of 137 anchors carried a product URL, and there are no
# /url?q= links), so the URLs are extracted from the page HTML instead.
_URL_RX = re.compile(
    r'https?://[^"\'\s<>\\]*(?:amazon\.[a-z.]+|flipkart\.com|meesho\.com)[^"\'\s<>\\]*'
)


def _get_browser():
    """Get or create the persistent CDP connection. Returns browser or None."""
    global _pw, _browser
    if _browser and _browser.is_connected():
        return _browser
    try:
        _pw = sync_playwright().start()
        # Fail fast. Without a timeout connect_over_cdp waits its 180s default,
        # so one unhealthy Chrome stalled the whole search for three minutes and
        # then failed anyway.
        _browser = _pw.chromium.connect_over_cdp(CDP_URL, timeout=15000)
        return _browser
    except Exception:
        return None


def _sweep_lens_tabs(browser) -> None:
    """Close Lens/Google-Search tabs left behind by earlier runs.

    A failed search used to return before page.close(), so every error leaked a
    heavy Google Search tab into the shared Chrome. They piled up until the
    browser could not service a new navigation and each search burned its full
    timeout. 9222 is a dedicated monitor Chrome, so anything Lens/Google-Search
    there is ours to close.
    """
    for ctx in browser.contexts:
        for p in list(ctx.pages):
            try:
                url = p.url or ""
            except Exception:
                continue
            if "lens.google.com" in url or "google.com/search" in url:
                try:
                    p.close()
                except Exception:
                    pass


def _search_lens(image_url: str) -> dict:
    """Lens-search an image in the user's Chrome.

    Returns {"amazon": [urls], "flipkart": [urls], "meesho": [urls]}.
    """
    result = {"amazon": [], "flipkart": [], "meesho": []}
    if not image_url:
        return result

    with _lens_lock:
        browser = _get_browser()
        if not browser:
            print("LENS: Chrome not available", file=sys.stderr)
            return result

        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            # Clear tabs leaked by earlier failed runs before adding another.
            _sweep_lens_tabs(browser)
            page = ctx.new_page()
        except Exception as e:
            print(f"LENS open error: {e}", file=sys.stderr)
            return result

        html = ""
        try:
            # The Lens result page is a heavy Google Search page (~3MB, mostly
            # thumbnails) and this box is busy, so it kept hitting the goto
            # timeout. Only the HTML matters, so drop images/media/fonts.
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
            # Do NOT drop the shared browser on a page-level failure. The old code
            # set _browser = None here, so ONE slow page made every later search
            # print "Chrome not available" and return instantly empty — which is
            # why a run "failed once and then searched so long".
            print(f"LENS error: {e}", file=sys.stderr)
        finally:
            # ALWAYS close: the old code only closed on success, so each failure
            # leaked a heavy Google Search tab into the shared Chrome until it
            # stopped servicing new navigations entirely.
            try:
                page.close()
            except Exception:
                pass

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


if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "https://resize.gajab.com/V000049/Img_1783506573840_2njfjyw81tr.jpeg"
    print(json.dumps(_search_lens(img), indent=2))
