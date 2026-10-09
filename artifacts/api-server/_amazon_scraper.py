#!/usr/bin/env python3
from __future__ import annotations
"""
Amazon product scraper — called as subprocess by Node.js server.

Uses Playwright headless Chromium (same as Flipkart scraper),
falls back to curl_cffi with Chrome TLS impersonation, then ScraperAPI.
Env vars (passed from Node.js):
  SCRAPER_PROXY        — HTTP/HTTPS proxy URL
  SCRAPING_SERVICE_URL — ScraperAPI base URL
"""
import json
import logging
import os
import re
import sys
import time
import traceback
import warnings
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

logger = logging.getLogger(__name__)

from _spec_to_attribute import _split_axes  # noqa: E402

PROXY = os.environ.get("SCRAPER_PROXY", "")
SCRAPING_SERVICE_URL = os.environ.get("SCRAPING_SERVICE_URL", "")
# scrape.do — residential-proxy fetcher. The local IP is blocked by Amazon
# (both curl_cffi and Playwright get the "to discuss automated access" page),
# and the ScraperAPI quota can run out, so scrape.do is the reliable fallback.
SCRAPE_DO_TOKEN = os.environ.get("SCRAPE_DO_TOKEN", "")
# Dedicated scraper Chrome (started by start-chrome-scraper.sh) which routes
# through the Webshare proxy. This is the SAME technique the Flipkart scraper
# uses as its primary fetch: a real, non-automated browser over CDP. No external
# scraping API required.
# Amazon works from the LOCAL IP (the Webshare residential proxy is only
# needed for Flipkart/Meesho). Prefer the no-proxy Chrome (9225) and fall back
# to the proxy-enabled one (9223). When the Webshare quota is exhausted (402)
# the proxy path returns partial pages with no specifications, so the
# no-proxy browser is the reliable one for Amazon.
CDP_URLS = [
    os.environ.get("AMAZON_CDP_URL", "http://localhost:9225"),
    os.environ.get("SCRAPE_CDP_URL", "http://localhost:9223"),
]
SCRAPE_CDP_URL = CDP_URLS[0]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]

EMPTY_RESULT = {
    "status": "failed",
    "title": None,
    "description": None,
    "meta_description": None,
    "images": [],
    "price": None,
    "dimensions": None,
    "weight": None,
    "gst": None,
    "hsn": None,
    "specifications": None,
    "source_category_path": None,
}

# DOM extraction executed inside the Playwright page.
EXTRACT_JS = r"""() => {
  const q = (sel) => document.querySelector(sel);
  const qa = (sel) => Array.from(document.querySelectorAll(sel));
  const txt = (el) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null;

  const title = txt(q('#productTitle')) || txt(q('#title')) || txt(q('h1.a-size-large'));

  let price = null;
  const priceScopes = [
    '#corePrice_desktop',
    '#corePrice_feature_div',
    '#corePriceDisplay_desktop_feature_div',
    '#apex_desktop',
    '#buybox',
    '#ppd',
    '#rightCol',
  ];
  for (const scopeSel of priceScopes) {
    const scope = q(scopeSel);
    if (!scope) continue;
    const el = scope.querySelector('.a-price .a-offscreen') || scope.querySelector('.a-offscreen');
    const t = txt(el);
    if (t && /[₹$€£]/.test(t)) { price = t; break; }
  }
  if (!price) {
    for (const sel of ['#priceblock_ourprice', '#priceblock_dealprice', '#price_inside_buybox']) {
      const t = txt(q(sel));
      if (t) { price = t; break; }
    }
  }
  const avail = txt(q('#availability'));
  if (avail && /currently unavailable|out of stock/i.test(avail)) price = null;

  const images = [];
  const seen = new Set();
  const _baseUrl = (u) => {
    var m = u.match(/\._[A-Z0-9]+_\.(?=jpg|jpeg|png|webp$)/i);
    if (m) return u.replace(m[0], '._SL1500_.');
    var complex = u.replace(/\._.*_\.(?=jpg|jpeg|png|webp$)/i, '._SL1500_.');
    if (complex !== u) return complex;
    if (/\._[A-Z]/.test(u)) return u;
    return u.replace(/\.(jpg|jpeg|png|webp)$/i, '._SL1500_.$1');
  };
  const pushImg = (u) => {
    if (!u) return;
    const clean = _baseUrl(u);
    if (seen.has(clean)) return;
    if (clean.endsWith('.gif') || clean.includes('sprite') || clean.includes('360_icon')) return;
    seen.add(clean);
    images.push(clean);
  };
  const landing = q('#landingImage') || q('#imgBlkFront') || q('#main-image');
  if (landing) {
    const dyn = landing.getAttribute('data-a-dynamic-image');
    if (dyn) {
      try {
        const obj = JSON.parse(dyn);
        Object.keys(obj).forEach(pushImg);
      } catch (e) {}
    }
    pushImg(landing.getAttribute('data-old-hires'));
  }

  const bullets = [];
  qa('#feature-bullets ul li span.a-list-item').forEach((el) => {
    const t = txt(el);
    if (t && t.length > 2 && !/^make sure this fits/i.test(t) && !/^to view this video/i.test(t)) {
      bullets.push(t);
    }
  });

  let description = txt(q('#productDescription p')) || txt(q('#productDescription'));
  if (!description && bullets.length) description = bullets.join(' | ');

  const metaEl = q('meta[name="description"]');
  const meta_description = metaEl ? (metaEl.getAttribute('content') || '').trim() : null;

  const specs = {};
  const cleanKey = (s) => (s || '').replace(/[‎‏]/g, '').replace(/:\s*$/, '').trim();
  qa('#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, table#technicalSpecifications_section_1 tr, #prodDetails table tr').forEach((tr) => {
    const th = tr.querySelector('th');
    const td = tr.querySelector('td');
    const k = cleanKey(txt(th));
    const v = txt(td);
    if (k && v && k !== v && !specs[k]) specs[k] = v;
  });
  qa('#detailBullets_feature_div ul li').forEach((li) => {
    const bold = li.querySelector('span.a-text-bold');
    if (!bold) return;
    const k = cleanKey(bold.textContent);
    let v = txt(li) || '';
    v = v.replace(bold.textContent || '', '').replace(/[‎‏]/g, '').trim();
    if (k && v && !specs[k]) specs[k] = v;
  });
  qa('#poExpander table tr, #productOverview_feature_div table tr').forEach((tr) => {
    const tds = tr.querySelectorAll('td');
    if (tds.length >= 2) {
      const k = cleanKey(txt(tds[0]));
      const v = txt(tds[1]);
      if (k && v && !specs[k]) specs[k] = v;
    }
  });

  // Category breadcrumb — the marketplace taxonomy we later map onto Gajab's.
  // Amazon rotates this widget between templates: the canonical wayfinding div,
  // a container-only variant, .a-breadcrumb, the JS-filled
  // desktop-breadcrumbs placeholder, ARIA-labelled navs, and on some pages only
  // a breadcrumb-ish id/class. Take whichever candidate carries the most crumb
  // links rather than the first selector that happens to exist.
  let sourceCategoryPath = null;
  const BC_SELECTORS = [
    '#wayfinding-breadcrumbs_feature_div',
    '#wayfinding-breadcrumbs_container',
    '#desktop-breadcrumbs_feature_div',
    '.a-breadcrumb',
    'nav[aria-label*="readcrumb" i]',
    '[role="navigation"][aria-label*="readcrumb" i]',
    '[data-feature-name*="breadcrumb" i]',
    '#breadcrumbs',
    '[class*="breadcrumb" i]',
    '[id*="breadcrumb" i]',
  ];
  const crumbTexts = (el) => Array.from(el.querySelectorAll('a'))
    .map(a => (a.textContent || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean);
  let bestCrumbs = null;
  for (const sel of BC_SELECTORS) {
    let nodes = [];
    try { nodes = qa(sel); } catch (e) { nodes = []; }
    for (const el of nodes) {
      const parts = crumbTexts(el);
      if (parts.length >= 2 && (!bestCrumbs || parts.length > bestCrumbs.length)) {
        bestCrumbs = parts;
      }
    }
    if (bestCrumbs && bestCrumbs.length >= 4) break;
  }
  if (bestCrumbs) sourceCategoryPath = bestCrumbs.join(' > ');

  // Some templates ship JSON-LD instead of the markup widget.
  if (!sourceCategoryPath) {
    for (const s of qa('script[type="application/ld+json"]')) {
      let d; try { d = JSON.parse(s.textContent || ''); } catch (e) { continue; }
      const items = Array.isArray(d) ? d
        : (d && Array.isArray(d['@graph']) ? d['@graph'] : [d]);
      for (const it of items) {
        if (!it || it['@type'] !== 'BreadcrumbList') continue;
        const names = (it.itemListElement || [])
          .map(e => (e && (e.name || (e.item && e.item.name))) || '')
          .map(x => String(x).replace(/\s+/g, ' ').trim())
          .filter(Boolean);
        if (names.length >= 2) { sourceCategoryPath = names.join(' > '); break; }
      }
      if (sourceCategoryPath) break;
    }
  }

  return { title, price, images, bullets, description, meta_description, specs, sourceCategoryPath };
}"""


def _via_scrape_do(url: str) -> dict:
    """Fetch a product page through scrape.do (residential proxy + JS render)."""
    if not SCRAPE_DO_TOKEN:
        return _blocked("scrape.do token not configured")
    import requests
    from urllib.parse import quote

    target = f"https://api.scrape.do/?token={SCRAPE_DO_TOKEN}&url={quote(url, safe='')}"
    try:
        resp = requests.get(target, timeout=90)
        text = resp.text
        if resp.status_code != 200:
            return _blocked(f"scrape.do HTTP {resp.status_code}")
        if len(text) < 10000 or _is_bot_page(text, url):
            return _blocked("scrape.do returned non-product page")
        return _parse_html(text, url)
    except Exception as e:
        return _blocked(f"scrape.do error: {e}")


def scrape(url: str, attempt: int = 1, max_attempts: int = 3) -> dict:
    ua = USER_AGENTS[(attempt - 1) % len(USER_AGENTS)]

    # PRIMARY: the proxy-enabled Chrome over CDP — a real, non-automated
    # browser, exactly the technique the Flipkart scraper uses. Amazon blocks
    # this machine's direct requests, so the browser is the reliable path.
    result = _try_playwright(url, ua)
    if result and result.get("status") == "success":
        return result

    # Fallback: direct fetch (works from un-flagged IPs).
    html = _try_direct(url, ua)
    # The direct fetch can return HTTP 200 with a tiny bot/consent page; without
    # this check _parse_html would extract the page <title> and report success.
    if html and not _is_bot_page(html, url):
        parsed = _parse_html(html, url)
        if parsed.get("status") == "success":
            return parsed

    # Last-resort cloud fetchers (only used if the local browser is unavailable).
    result = _via_scrape_do(url)
    if result.get("status") == "success":
        return result

    result = _via_scraping_service(url)
    if result.get("status") == "success":
        return result

    if attempt < max_attempts:
        time.sleep(attempt * 5)
        return scrape(url, attempt=attempt + 1, max_attempts=max_attempts)

    return _blocked("All fetch methods failed")


def _is_bot_page(html: str, url: str = "") -> bool:
    if "validatecaptcha" in url.lower():
        return True
    lowered = html.lower()
    checks = [
        "enter the characters you see below",
        "type the characters you see in this image",
        "robot check",
        "api-services-support@amazon.com",
        "/errors/validatecaptcha",
        "to discuss automated access to amazon data",
    ]
    if any(c in lowered for c in checks):
        return True
    if len(html) < 10000 and "producttitle" not in lowered:
        return True
    return False


# Amazon serves these titles for non-product pages (homepage, sign-in, the
# "sorry" error page). When we see one, the /dp/<ASIN> path redirected and we
# must retry — previously these were returned as a "successful" scrape that
# contained only the homepage title + meta description.
_GENERIC_TITLE_MARKERS = (
    "online shopping site in india",
    "amazon.in: online shopping",
    "amazon.in: shop online",
    "amazon.com: online shopping",
    "amazon.com. spend less",
    "shop online for mobiles",
    "sorry! something went wrong",
    "amazon.com: sign in",
    "amazon.in: sign in",
)


def _extract_asin(url: str) -> str:
    for pat in (
        r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})",
        r"\b([A-Z0-9]{10})\b",
    ):
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return ""


def _looks_like_product_page(result: dict, html: str = "", url: str = "") -> bool:
    """True only if the fetched page is a real product detail page.

    Guards against Amazon redirecting an ASIN request to the homepage, a
    sign-in page, or a soft-block page — which previously produced a
    'successful' scrape with just a title and one-line description.
    """
    title = (result.get("title") or "").strip().lower()
    if not title or len(title) < 8:
        return False
    if any(mk in title for mk in _GENERIC_TITLE_MARKERS):
        return False
    if html:
        asin = _extract_asin(url)
        has_title_el = "producttitle" in html.lower()
        asin_in_html = bool(asin) and asin in html.upper()
        if not has_title_el and not asin_in_html:
            return False
    return True



def _try_playwright(url: str, ua: str = "") -> dict | None:
    """Fetch + extract via the proxy-enabled Chrome CDP (real browser).

    Primary technique, identical in spirit to the Flipkart scraper: connect to
    the dedicated scraper Chrome over CDP (port 9223, Webshare residential
    proxy) so Amazon's bot detection sees a real browser. Falls back to a local
    headless Chromium only if the CDP endpoint isn't reachable.
    """
    use_cdp = False
    cdp_url = SCRAPE_CDP_URL
    try:
        import urllib.request
        for candidate in CDP_URLS:
            try:
                urllib.request.urlopen(f"{candidate}/json/version", timeout=5)
                cdp_url = candidate
                use_cdp = True
                break
            except Exception:
                continue
    except Exception:
        use_cdp = False

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if use_cdp:
                browser = p.chromium.connect_over_cdp(cdp_url)
                context = browser.new_context(
                    user_agent=ua or USER_AGENTS[0],
                    viewport={"width": 1440, "height": 900},
                    locale="en-IN",
                )
                page = context.new_page()
                owns_browser = False
            else:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=ua or USER_AGENTS[0],
                    viewport={"width": 1440, "height": 900},
                    locale="en-IN",
                )
                page = context.new_page()
                owns_browser = True
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)

                # Product pages often autoplay a video in the hero. A playing
                # video keeps the page busy (and can cover the DOM), which made
                # scraping stall on the PDP. Pause every video and kill autoplay.
                try:
                    page.evaluate("""() => {
                        document.querySelectorAll('video').forEach(v => {
                            try {
                                v.pause();
                                v.autoplay = false;
                                v.removeAttribute('autoplay');
                                v.muted = true;
                                v.currentTime = 0;
                            } catch (e) {}
                        });
                    }""")
                except Exception:
                    pass

                # Amazon (and others) can open a full-screen VIDEO lightbox
                # ("VIDEOS | IMAGES") over the page, which blocks extraction.
                # Press Escape and click any close button, then re-pause videos.
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
                    page.evaluate("""() => {
                        const closers = document.querySelectorAll(
                            '[aria-label*="Close" i], button.a-button-close, .a-modal-close, [data-action="a-popover-close"]');
                        closers.forEach(b => { try { b.click(); } catch (e) {} });
                        document.querySelectorAll('video').forEach(v => {
                            try { v.pause(); v.autoplay = false; v.removeAttribute('autoplay'); v.muted = true; v.currentTime = 0; } catch (e) {}
                        });
                    }""")
                except Exception:
                    pass
                # Close any stale tabs left over from earlier runs so the browser
                # doesn't accumulate pages (it had leaked 7 tabs).
                try:
                    for other in context.pages:
                        if other is not page:
                            try:
                                other.close()
                            except Exception:
                                pass
                except Exception:
                    pass

                # Amazon "Continue shopping" interstitial
                try:
                    btn = page.query_selector(
                        "button:has-text('Continue shopping'), input[type='submit'][value*='Continue']"
                    )
                    if btn:
                        btn.click()
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

                if _is_bot_page(page.content(), page.url):
                    return None

                # Scroll down gradually to trigger lazy-loaded sections
                # (product details, tech specs, detail bullets)
                try:
                    page.evaluate(
                        """async () => {
                            for (let y = 0; y <= document.body.scrollHeight; y += 600) {
                                window.scrollTo(0, y);
                                await new Promise(r => setTimeout(r, 120));
                            }
                            window.scrollTo(0, 0);
                        }"""
                    )
                    page.wait_for_timeout(1000)
                except Exception:
                    pass

                # Expand "Product information" section if collapsed
                for link_text in ("See more product details", "Product information"):
                    try:
                        el = page.query_selector(f"text={link_text}")
                        if el:
                            el.click()
                            page.wait_for_timeout(1000)
                            break
                    except Exception:
                        continue

                try:
                    page.wait_for_selector("#productTitle", timeout=8000)
                except Exception:
                    pass

                # Close any lightbox that the scroll / "Product information"
                # click may have opened, before reading the DOM.
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass

                data = page.evaluate(EXTRACT_JS)

                # Click through thumbnail gallery to capture all full-res variant images
                try:
                    thumb_selector = "#altImages li.item, #altImages .a-button-thumbnail, li.imageThumbnail"
                    thumbs = page.query_selector_all(thumb_selector)
                    if thumbs:
                        existing_images = set(data.get("images") or [])
                        for thumb in thumbs[:12]:
                            try:
                                # SKIP the video thumbnail — clicking it opens a
                                # full-screen "VIDEOS | IMAGES" lightbox that
                                # covers the page and stalls extraction.
                                tcls = (thumb.get_attribute("class") or "").lower()
                                if ("video" in tcls
                                        or thumb.query_selector(".videoBlockIngress, .vse-video-thumbnail, [class*='video'], [data-video-url]")):
                                    continue
                                # JS click — Playwright's normal click() auto-waits
                                # for actionability, and when a thumbnail is even
                                # partly covered each click burns seconds (this is
                                # what made the scrape take ~190s).
                                thumb.evaluate("el => el.click()")
                                page.wait_for_timeout(250)
                                dyn_json = page.evaluate("""() => {
                                    var el = document.querySelector('#landingImage') ||
                                             document.querySelector('#imgBlkFront');
                                    return el ? (el.getAttribute('data-a-dynamic-image') || '') : '';
                                }""")
                                if dyn_json:
                                    obj = json.loads(dyn_json)
                                    for u in obj.keys():
                                        if u not in existing_images:
                                            existing_images.add(u)
                                            data.setdefault("images", []).append(u)
                            except Exception:
                                continue
                except Exception:
                    pass

                # In case a lightbox did open, close it before reading the page.
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    page.evaluate("""() => {
                        document.querySelectorAll(
                            '[aria-label*="Close" i], button.a-button-close, .a-modal-close, .vse-close-button'
                        ).forEach(b => { try { b.click(); } catch (e) {} });
                    }""")
                except Exception:
                    pass

                html = page.content()

                # The PDP gallery lives in JS data as a list of entries shaped
                #   {"hiRes":"…"|null,"thumb":"…","large":"…"}
                # Only the SELECTED image carries a non-null hiRes; every other
                # entry exposes just "large" (~400-700px) + a 38x50 "thumb".
                # The old code regexed only non-null "hiRes", so multi-image
                # listings came back with 1-2 images. Collect EVERY entry,
                # preferring hiRes, then large, then thumb.
                try:
                    existing = set(data.get("images") or [])
                    entry_re = re.compile(
                        r'"hiRes"\s*:\s*(?:"(https?://[^"]+)"|null)\s*,\s*'
                        r'"thumb"\s*:\s*"(https?://[^"]+)"\s*,\s*'
                        r'"large"\s*:\s*"(https?://[^"]+)"'
                    )
                    for m in entry_re.finditer(html):
                        url = m.group(1) or m.group(3) or m.group(2)
                        if url and url not in existing:
                            existing.add(url)
                            data.setdefault("images", []).append(url)
                    # Any non-null hiRes not already captured by a full entry.
                    for m in re.finditer(r'"hiRes"\s*:\s*"(https?://[^"]+)"', html):
                        u = m.group(1)
                        if u not in existing:
                            existing.add(u)
                            data.setdefault("images", []).append(u)
                except Exception:
                    pass
            finally:
                context.close()
                # Never close the shared Chrome when connected over CDP.
                if owns_browser:
                    browser.close()

            if data and data.get("title"):
                dom_result = _result_from_dom(data)
                if _looks_like_product_page(dom_result, html, url):
                    return dom_result
                return _blocked("Not a product page (Amazon redirected or soft-blocked)")
            return _parse_html(html, url)
    except Exception:
        return None


def _try_direct(url: str, ua: str = "") -> str:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return ""
    headers = {
        "User-Agent": ua or USER_AGENTS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        kwargs: dict = {"headers": headers, "impersonate": "chrome", "timeout": 30}
        if PROXY:
            kwargs["proxies"] = {"https": PROXY, "http": PROXY}
        resp = curl_requests.get(url, **kwargs)
        if resp.status_code == 200 and not _is_bot_page(resp.text):
            return resp.text
    except Exception:
        pass
    return ""


def _via_scraping_service(url: str) -> dict:
    import requests

    try:
        base = SCRAPING_SERVICE_URL.rstrip("/?&")
        if "?url=" in base or "&url=" in base or "url=" in base:
            target = f"{base}{url}"
        elif "?" in base:
            target = f"{base}&url={url}"
        else:
            target = f"{base}?url={url}"

        resp = requests.get(target, timeout=45)
        if resp.status_code == 200 and not _is_bot_page(resp.text) and len(resp.text) > 10000:
            return _parse_html(resp.text, url)
        return _blocked("Scraping service returned non-product page")
    except Exception as e:
        return _blocked(f"Scraping service error: {e}")


def _result_from_dom(data: dict) -> dict:
    result = dict(EMPTY_RESULT)
    result["status"] = "success"
    result["title"] = _clean(data.get("title") or "")[:500]

    bullets = [b for b in (data.get("bullets") or []) if b]
    description = data.get("description")
    if description:
        result["description"] = _clean(description)[:2000]
    elif bullets:
        result["description"] = _clean(" | ".join(bullets))[:2000]
    if bullets and result["description"] and len(result["description"]) < 200:
        result["description"] = _clean(" | ".join(bullets))[:2000]

    meta_desc = data.get("meta_description")
    if meta_desc:
        result["meta_description"] = _clean(meta_desc)[:2000]
    if not result["description"] and result["meta_description"]:
        result["description"] = result["meta_description"]

    images = data.get("images") or []
    seen: set[str] = set()
    deduped: list[str] = []
    import re as _im_re
    for img_url in images:
        clean = re.sub(r'\._.*_\.(?=jpg|jpeg|png|webp$)', '._SL1500_.', img_url, flags=re.I)
        if clean == img_url:
            if re.search(r'\._[A-Z]', img_url):
                clean = img_url
            else:
                clean = re.sub(r'\.(jpg|jpeg|png|webp)$', r'._SL1500_.\1', img_url, flags=re.I)
        if clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    result["images"] = deduped[:10]

    price = data.get("price")
    if price:
        result["price"] = _clean(price)

    specs = data.get("specs") or {}
    if specs:
        cleaned_specs: dict[str, str] = {}
        for k, v in specs.items():
            ck, cv = _clean(str(k)), _clean(str(v))
            if ck and cv:
                cleaned_specs[ck] = cv
        result["specifications"] = cleaned_specs
        _fill_derived(result, cleaned_specs)

    # Marketplace category breadcrumb (e.g. "Home Improvement > Power & Hand Tools
    # > ... > Clamp Sets") — mapped to a Gajab L1-L4 path at export time.
    scp = data.get("sourceCategoryPath")
    if scp:
        result["source_category_path"] = _clean(scp)

    return result


def _fill_derived(result: dict, specs: dict) -> None:
    for label, value in specs.items():
        ll = label.lower()
        if result["weight"] is None and "weight" in ll:
            result["weight"] = value
        if result["dimensions"] is not None or not ("dimension" in ll or ll == "size"):
            continue
        # 'Size' on Amazon is normally a VOLUME ('100 ml (Pack of 1)'), so only
        # accept it when it is genuinely a multi-axis size; a dimension label is
        # accepted either way, minus the field name Amazon echoes back into the
        # value ('Product Dimensions : 5 x 5 x 18 cm; 100 g').
        axes = _split_axes(value)
        if axes:
            result["dimensions"] = " x ".join(
                axes[k] for k in ("dim_length", "dim_width", "dim_height") if k in axes)
        elif "dimension" in ll:
            v = value.split(";")[0].strip()
            if ":" in v:
                head, tail = v.split(":", 1)
                if not re.search(r"\d", head):
                    v = tail.strip()
            result["dimensions"] = v
        if result["gst"] is None and "gst" in ll:
            result["gst"] = value
        if result["hsn"] is None and "hsn" in ll:
            result["hsn"] = value


# (?<![-\w]) keeps `data-csa-c-content-id="desktop-breadcrumbs"` from matching —
# that is a telemetry attribute whose value is only a widget slug.
# The second branch covers `<nav aria-label="Breadcrumb">`-style containers that
# carry the crumb as bare anchors with no id/class of their own.
_BREADCRUMB_SEL_RE = re.compile(
    r'(?<![-\w])(?:id|class)="([^"]*breadcrumb[^"]*)"'
    r'|aria-label="([^"]*(?:breadcrumb|category trail)[^"]*)"',
    re.IGNORECASE,
)
# Containers Amazon has actually used for the product crumb. Matched first, so a
# generic `class="breadcrumb"` elsewhere in the page cannot outrank the real one.
_BREADCRUMB_PRIMARY = ("wayfinding-breadcrumbs", "desktop-breadcrumbs",
                       "a-breadcrumb", "breadcrumbs")


def _breadcrumb_from_html(html: str) -> str | None:
    """Category breadcrumb from raw HTML.

    Amazon serves the crumb in several containers — the canonical
    ``#wayfinding-breadcrumbs_feature_div``, ``#wayfinding-breadcrumbs_container``,
    ``.a-breadcrumb``, the ``#desktop-breadcrumbs_*`` placeholder and, on some
    templates, only an element whose id/class merely mentions "breadcrumb".
    Scan every such container and keep whichever holds the most crumb links
    (a single anchor is not a chain), then fall back to JSON-LD
    ``BreadcrumbList`` when the markup widget is absent entirely.

    The old version hard-coded one id plus a brittle ``</div></div>`` tail, so
    any other template silently produced ``source_category_path = None``.
    """
    if not html:
        return None

    cands: dict[bool, list[list[str]]] = {True: [], False: []}  # True = primary
    for m in _BREADCRUMB_SEL_RE.finditer(html):
        name = ((m.group(1) or m.group(2)) or "").lower()
        if "flyout" in name or name.startswith("nav-") or "csa" in name:
            continue  # global navigation / telemetry, not a product crumb
        primary = any(tok in name for tok in _BREADCRUMB_PRIMARY)
        if m.group(2):
            primary = True  # aria-label="Breadcrumb" is unambiguous
        tail = html[m.end(): m.end() + 6000]
        first_ul = tail.find("<ul")
        first_close = tail.find("</div>")
        # An empty widget (`<div id="wayfinding…"></div>`) is followed by the
        # NEXT unrelated list in the document; slicing to it would file e.g.
        # "Mobiles > Electronics" from the header nav as the product crumb.
        if first_ul != -1 and first_close != -1 and first_close < first_ul:
            continue
        bounds = [b for b in (
            tail.find("</ul>"), tail.find("</nav>"),
            tail.find("</ol>"), tail.find("</div>"),
        ) if b != -1]
        chunk = tail[:min(bounds)] if bounds else tail[:2000]
        parts = [
            _clean(re.sub(r"<[^>]+>", "", a))
            for a in re.findall(r"<a[^>]*>(.*?)</a>", chunk, re.DOTALL | re.IGNORECASE)
        ]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            cands[primary].append(parts)

    for tier in (True, False):
        # prefer the DEEPEST chain; ties go to the first container in the page
        chains = sorted(cands[tier], key=len, reverse=True)
        if chains:
            return " > ".join(chains[0])

    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else None
        if items is None and isinstance(data, dict):
            g = data.get("@graph")
            items = g if isinstance(g, list) else [data]
        for it in items or []:
            if not isinstance(it, dict) or it.get("@type") != "BreadcrumbList":
                continue
            names: list[str] = []
            for e in it.get("itemListElement") or []:
                if not isinstance(e, dict):
                    continue
                item = e.get("item") if isinstance(e.get("item"), dict) else {}
                raw = e.get("name") or item.get("name") or item.get("title")
                if raw is not None:
                    t = _clean(str(raw))
                    if t:
                        names.append(t)
            if len(names) >= 2:
                return " > ".join(names)
    return None


def _parse_html(html: str, url: str) -> dict:
    result = dict(EMPTY_RESULT)

    m = re.search(r'<span[^>]*id="productTitle"[^>]*>(.*?)</span>', html, re.DOTALL)
    if m:
        result["title"] = _clean(re.sub(r"<[^>]+>", "", m.group(1)))[:500]

    if not result["title"]:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if m:
            t = _clean(re.sub(r"<[^>]+>", "", m.group(1)))
            t = re.sub(r"\s*[:\-–]?\s*Amazon\.(in|com).*$", "", t, flags=re.IGNORECASE).strip()
            if t:
                result["title"] = t[:500]

    m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if m:
        result["meta_description"] = _clean(m.group(1))[:2000]

    # Category breadcrumb (marketplace taxonomy → mapped to Gajab at export).
    # The wayfinding div is only one of several templates; see _breadcrumb_from_html.
    bc = _breadcrumb_from_html(html)
    if bc:
        result["source_category_path"] = bc

    # Price
    for pat in (
        r'class="a-offscreen">([^<]*[₹$][^<]*)</span>',
        r'id="priceblock_(?:ourprice|dealprice)"[^>]*>(.*?)</span>',
        r'id="price_inside_buybox"[^>]*>(.*?)</span>',
    ):
        m = re.search(pat, html, re.DOTALL)
        if m:
            price = _clean(re.sub(r"<[^>]+>", "", m.group(1)))
            if price:
                result["price"] = price
                break

    # Images: hi-res URLs embedded in page JS + dynamic image JSON
    images: list[str] = []
    seen: set[str] = set()

    def _base_url(u: str) -> str:
        clean = re.sub(r'\._.*_\.(?=jpg|jpeg|png|webp$)', '._SL1500_.', u, flags=re.I)
        if clean != u:
            return clean
        if re.search(r'\._[A-Z]', u):
            return u
        return re.sub(r'\.(jpg|jpeg|png|webp)$', r'._SL1500_.\1', u, flags=re.I)

    def _push(u: str) -> None:
        u = u.strip().replace("\\/", "/")
        base = _base_url(u)
        if not u or base in seen or u.endswith(".gif") or "sprite" in u:
            return
        seen.add(base)
        images.append(base)

    for m in re.finditer(r'"hiRes"\s*:\s*"(https?://[^"]+)"', html):
        _push(m.group(1))
    m = re.search(r'data-a-dynamic-image="({.*?})"', html, re.DOTALL)
    if m:
        try:
            import html as _html_mod
            dyn = json.loads(_html_mod.unescape(m.group(1)))
            for u in dyn.keys():
                _push(u)
        except (json.JSONDecodeError, ValueError):
            pass
    m = re.search(r'id="landingImage"[^>]*src="([^"]+)"', html)
    if m:
        _push(m.group(1))
    result["images"] = images[:10]

    # Feature bullets
    bullets: list[str] = []
    fb = re.search(r'id="feature-bullets"(.*?)(?:id="productDescription|<div id="productDescription|$)', html, re.DOTALL)
    section = fb.group(1) if fb else ""
    if section:
        for m in re.finditer(r'<span[^>]*class="a-list-item"[^>]*>(.*?)</span>', section, re.DOTALL):
            t = _clean(re.sub(r"<[^>]+>", "", m.group(1)))
            if t and len(t) > 2 and not re.match(r"^(Make sure this fits|To view this video)", t, re.IGNORECASE):
                bullets.append(t)

    m = re.search(r'id="productDescription"[^>]*>.*?<p[^>]*>(.*?)</p>', html, re.DOTALL)
    if m:
        desc = _clean(re.sub(r"<[^>]+>", "", m.group(1)))
        if desc:
            result["description"] = desc[:2000]
    if not result["description"] and bullets:
        result["description"] = _clean(" | ".join(bullets))[:2000]
    if not result["description"] and result["meta_description"]:
        result["description"] = result["meta_description"]

    # Specifications from detail tables
    specs: dict[str, str] = {}
    for table_match in re.finditer(
        r'<table[^>]*id="(productDetails_techSpec_section_1|productDetails_detailBullets_sections1|technicalSpecifications_section_1)"[^>]*>(.*?)</table>',
        html,
        re.DOTALL,
    ):
        body = table_match.group(2)
        for row in re.finditer(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
            row_html = row.group(1)
            th = re.search(r"<th[^>]*>(.*?)</th>", row_html, re.DOTALL)
            td = re.search(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)
            if th and td:
                k = _clean(re.sub(r"<[^>]+>", "", th.group(1))).strip(":").strip()
                v = _clean(re.sub(r"<[^>]+>", "", td.group(1)))
                if k and v and k != v and k not in specs:
                    specs[k] = v

    # Detail bullets (label in bold span)
    for m in re.finditer(
        r'<span[^>]*class="a-text-bold"[^>]*>(.*?)</span>\s*<span[^>]*>(.*?)</span>',
        html,
        re.DOTALL,
    ):
        k = _clean(re.sub(r"<[^>]+>", "", m.group(1)))
        k = re.sub(r"[‎‏]", "", k).strip(":").strip()
        v = _clean(re.sub(r"<[^>]+>", "", m.group(2)))
        v = re.sub(r"[‎‏]", "", v).strip()
        if k and v and k != v and len(k) < 80 and k not in specs:
            specs[k] = v

    if specs:
        result["specifications"] = specs
        _fill_derived(result, specs)

    if not result["title"]:
        return _blocked("Could not extract product data")
    if not _looks_like_product_page(result, html, url):
        return _blocked("Not a product page (Amazon redirected or soft-blocked)")
    result["status"] = "success"
    return result


def _blocked(msg: str) -> dict:
    result = dict(EMPTY_RESULT)
    result["status"] = "blocked"
    result["error"] = msg
    return result


def _clean(s: str) -> str:
    import html as _html_mod
    s = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", s)
    return re.sub(r"\s+", " ", _html_mod.unescape(s)).strip()


EXTRACT_MAX_PAGES = 400          # merchant catalogues can run to hundreds of pages
EXTRACT_MAX_PRODUCTS = 20000     # hard cap on returned links
EXTRACT_SCROLL_WAIT = 0.3


def _search_brand_hits(brand: str) -> int:
    """Return how many products the brand-filtered search returns (0 = wrong)."""
    import re as _re
    import urllib.request as _ur
    from urllib.parse import quote_plus as _qp
    url = f"https://www.amazon.in/s?rh=p_4%3A{_qp(brand)}"
    try:
        req = _ur.Request(url, headers={
            "User-Agent": USER_AGENTS[0],
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        })
        html = _ur.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception:
        return 0
    return len(set(_re.findall(r"/dp/([A-Z0-9]{10})", html)))


def _brand_candidates(store_url: str, path_name: str) -> list[str]:
    """Every plausible display name for a store, best guess first.

    Shared by the cheap HTTP probe and the browser probe so both consider the
    same set (page-title brand, joined single-letter tokens, path-slug splits).
    """
    out: list[str] = []

    def add(v: str):
        v = " ".join((v or "").split())
        if v and v not in out:
            out.append(v)

    title_brand = _brand_from_store_url(store_url)
    if title_brand:
        # 'B M BROTHERS' -> also try 'BM BROTHERS' (join single-letter tokens)
        add(title_brand)
        add(" ".join(re.sub(r"\b([A-Za-z])\b\s+", r"\1", title_brand).split()))
    for c in _brand_filter_candidates(path_name):
        add(c)
    return out


def _brand_hits_browser(page, brand: str, asin_re) -> int:
    """How many products the brand-filtered search renders in a real browser.

    The bare-HTTP probe (_search_brand_hits) is best-effort only: Amazon 503s
    it and otherwise serves a JS shell with zero result cards, so it returns 0
    for EVERY candidate — which used to disable the brand rewrite entirely and
    leave the scraper on the un-paginated storefront (40 of 220 products).
    """
    from urllib.parse import quote_plus as _qp
    url = f"https://www.amazon.in/s?rh=p_4%3A{_qp(brand)}&s=popularity-rank"
    try:
        page.goto(url, wait_until="commit", timeout=45000)
        try:
            page.wait_for_selector('[data-component-type="s-search-result"]', timeout=9000)
        except Exception:
            pass
        page.wait_for_timeout(700)
        html = page.content()
    except Exception:
        return 0
    return len(set(asin_re.findall(html)))


def _resolve_brand_filter_browser(page, store_url: str, path_name: str):
    """Return (brand, search_url) using the browser; ("", store_url) if none.

    Runs only when the HTTP probe could not confirm a brand — which is the
    common case now, so this is the path that actually keeps the catalogue
    complete.
    """
    import re as _re
    asin_re = _re.compile(r"/dp/([A-Z0-9]{10})")
    best, best_hits = "", 0
    for c in _brand_candidates(store_url, path_name):
        hits = _brand_hits_browser(page, c, asin_re)
        logger.info("brand candidate %r -> %s products (browser)", c, hits)
        if hits > best_hits:
            best, best_hits = c, hits
        if hits >= 20:      # a real catalogue is rarely smaller
            break
    if not best:
        return "", store_url
    from urllib.parse import quote_plus as _qp
    return best, f"https://www.amazon.in/s?rh=p_4%3A{_qp(best)}&s=popularity-rank"


def _brand_filter_candidates(path_name: str) -> list[str]:
    """Candidate brand strings for a store, best guess first.

    Amazon's p_4 brand filter is picky: for the BMBROTHERS store only
    'BM BROTHERS' returns the 220-product catalogue, while the page title gives
    'B M BROTHERS' (0) and the path slug 'BMBROTHERS' (0). So generate the
    obvious variants and let the caller verify which one actually works.
    """
    import re as _re
    raw = (path_name or "").strip()
    out: list[str] = []

    def add(v: str):
        v = " ".join((v or "").split())
        if v and v not in out:
            out.append(v)

    if raw:
        # BMBROTHERS -> "BM BROTHERS" (split the trailing all-caps word off)
        m = _re.match(r"^([A-Z]{2,3})([A-Z][a-z].*)$", raw)
        if m:
            add(f"{m.group(1)} {m.group(2)}")
        # BMBROTHERS -> "BM BROTHERS" via a known two-letter acronym split
        for acr in ("BM", "MB", "SM", "RS"):
            if raw.startswith(acr) and len(raw) > len(acr):
                add(f"{acr} {raw[len(acr):]}")
        add(raw.replace("_", " ").replace("-", " "))
        add(raw)
    return out


def _resolve_brand_filter(store_url: str, path_name: str) -> str:
    """Pick the p_4 brand string that actually returns the store's catalogue."""
    best = ""
    best_hits = 0
    for c in _brand_candidates(store_url, path_name):
        hits = _search_brand_hits(c)
        logger.info("brand candidate %r -> %s products", c, hits)
        if hits > best_hits:
            best, best_hits = c, hits
        if hits >= 20:      # good enough; a real catalogue is rarely smaller
            break
    return best


def _brand_from_store_url(store_url: str) -> str:
    """Resolve the brand display name for an Amazon Brand Store URL.

    /stores/<NAME>/page/<id> renders a single server-rendered storefront page
    (~25-40 tiles, no pagination, no s-search-result cards), so a 200-product
    store yielded only ~25 links. The same catalogue IS exposed by the
    brand-filtered search (?rh=p_4:<BRAND>), which paginates normally.

    The path slug ('BMBROTHERS') does NOT work as a filter — Amazon needs the
    display name ('BM BROTHERS'), which is in the page <title>.
    """
    import re as _re
    import urllib.request as _ur
    try:
        req = _ur.Request(
            store_url,
            headers={
                "User-Agent": USER_AGENTS[0],
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        html = _ur.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception:
        return ""
    m = _re.search(r"<title>\s*Amazon\.in\s*:\s*([^<]+?)\s*</title>", html, _re.I)
    if m:
        return m.group(1).strip()
    m2 = _re.search(r'"brandName"\s*:\s*"([^"]{2,60})"', html)
    if m2:
        return m2.group(1).strip()
    return ""


def extract_products(store_url: str, start_page: int = 1, max_pages: int | None = None) -> dict:
    """Extract all product links from an Amazon search / category / store page.

    Returns dict with:
      store_name: str
      products: list[dict] — each with url, title, imageUrl, price
      has_more: bool — True when the page cap stopped us early (more pages exist)
      next_page: int | None — the page to request next

    `start_page` / `max_pages` chunk the pagination: a full merchant catalogue can
    run to hundreds of pages and take >300s, which the ngrok tunnel kills (503),
    so the caller walks it in bounded chunks.
    """
    from urllib.parse import urlparse

    # Determine store name from URL
    parsed = urlparse(store_url)
    domain = parsed.netloc.replace("www.", "")
    path_parts = [p for p in parsed.path.split("/") if p]
    store_name = ""
    if "dp" in path_parts or "product" in path_parts:
        # Single product page, not a store
        return {
            "store_name": "",
            "products": [],
            "error": "URL appears to be a product page, not a store/search page",
        }
    if "stores" in path_parts and len(path_parts) >= 2:
        store_name = path_parts[1].replace("-", " ").title()[:40]
    elif path_parts and path_parts[0] == "sp":
        # Seller profile page — rewrite to merchant search
        import urllib.parse as _urlparse
        qs = _urlparse.parse_qs(parsed.query)
        seller_id = qs.get("seller", [None])[0]
        if seller_id:
            store_url = f"https://{parsed.netloc}/s?i=merchant-items&me={seller_id}&s=popularity-rank&fs=true"
            parsed = urlparse(store_url)
            path_parts = ["s"]
            store_name = "Seller Store"
        else:
            store_name = "Seller Profile"
    elif "b" in path_parts:
        store_name = "Category"
    elif "s" in path_parts:
        # Extract search term from query
        import urllib.parse as _urlparse
        qs = _urlparse.parse_qs(parsed.query)
        store_name = qs.get("k", ["Search"])[0][:40]
    else:
        store_name = domain[:30]

    # Seller storefront links (/l/<node>?me=<seller>, /b?...&me=<seller>, /sp)
    # render as a "Storefront" page: ~20 preview tiles in carousels, no
    # s-search-result cards and NO pagination — which is why a 4000-item
    # catalogue yielded only 21 links. The seller's full catalogue is exposed
    # by the merchant-items search, which paginates normally. Rewrite to it.
    import urllib.parse as _urlparse
    _qs = _urlparse.parse_qs(parsed.query)
    _me = (_qs.get("me", [None])[0] or "").strip()
    if _me and not path_parts[:1] == ["s"]:
        store_url = (
            f"https://{parsed.netloc}/s?i=merchant-items&me={_me}"
            "&s=popularity-rank&fs=true"
        )
        parsed = urlparse(store_url)
        path_parts = ["s"]
        if not store_name or store_name == domain[:30]:
            store_name = "Seller Store"

    # Amazon BRAND STORES (/stores/<NAME>/page/<id>) are a single server-rendered
    # storefront page: ~25-40 tiles, no s-search-result cards, no pagination. A
    # 200-product store therefore returned only ~25 links. The full catalogue is
    # exposed by the brand-filtered search (?rh=p_4:<BRAND>), which paginates.
    # The path slug does not work as a filter — Amazon needs the display name.
    _store_slug = ""
    if path_parts[:1] == ["stores"] and not _qs.get("rh"):
        _uf = _urlparse.quote_plus
        _store_slug = path_parts[1] if len(path_parts) > 1 else ""
        _brand = _resolve_brand_filter(store_url, _store_slug)
        if _brand:
            store_name = _brand[:40]
            store_url = (
                f"https://{parsed.netloc}/s?rh=p_4%3A{_uf(_brand)}"
                "&s=popularity-rank"
            )
            parsed = urlparse(store_url)
            path_parts = ["s"]
            _store_slug = ""
            logger.info("Brand store -> brand search: %s (%s)", store_url, _brand)
        else:
            # The bare-HTTP probe is unreliable (Amazon 503s it / serves a JS
            # shell), so "no brand found" does NOT mean "no brand exists".
            # Confirm the candidates in the browser before falling back to the
            # un-paginated storefront.
            logger.info("HTTP brand probe inconclusive for %r — will verify in browser",
                        _store_slug)

    products: list[dict] = []
    seen_asins: set[str] = set()

    EXTRACT_JS = r"""() => {
      const results = [];
      const seen = new Set();
      const cards = document.querySelectorAll('[data-component-type="s-search-result"]');
      cards.forEach(card => {
        const link = card.querySelector('a.a-link-normal.a-text-normal') || card.querySelector('h2 a');
        if (!link) return;
        let href = link.getAttribute('href') || '';
        let url = href.startsWith('http') ? href.split('?')[0] : 'https://www.amazon.in' + href.split('?')[0];
        if (!url.includes('/dp/') && !url.includes('/gp/product/')) return;
        if (seen.has(url)) return;
        seen.add(url);
        const h2 = card.querySelector('h2');
        const title = (link.textContent || '').trim();
        const img = (card.querySelector('img.s-image') || {}).getAttribute('src') || '';
        const pEl = card.querySelector('.a-price .a-offscreen') || card.querySelector('.a-price-whole');
        const price = pEl ? (pEl.textContent || '').trim() : '';
        results.push({url, title, imageUrl: img, price});
      });
      if (results.length === 0) {
        const fallbackSeen = new Set();
        document.querySelectorAll('[class*="tile"] a[href*="/dp/"], a[href*="/dp/"]').forEach(link => {
          let href = link.getAttribute('href') || '';
          let url = href.startsWith('http') ? href.split('?')[0] : 'https://www.amazon.in/' + href.replace(/^\/+/, '').split('?')[0];
          if (fallbackSeen.has(url)) return;
          fallbackSeen.add(url);
          // Derive title from URL slug if no text content
          let title = (link.textContent || '').trim();
          if (!title) {
            const aria = (link.getAttribute('aria-label') || '').trim();
            if (aria && aria.length > 15 && !/^(shop|view|click|see)/i.test(aria)) title = aria;
          }
          if (!title || title.length > 100) {
            const slug = url.split('/dp/')[0].split('/').pop() || '';
            title = decodeURIComponent(slug.replace(/[_-]/g, ' ').replace(/\s+/g, ' ').trim()) || '';
          }
          const tile = link.closest('[class*="tile"], [class*="card"]');
          const imgEl = tile ? tile.querySelector('img') : link.querySelector('img');
          const img = imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || '') : '';
          results.push({url, title: title.slice(0,120), imageUrl: img, price: ''});
        });
      }
      return results.filter(r => /\/[A-Z0-9]{10}(?:\/|$)/.test(r.url));
    }"""

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            # Prefer the CDP Chrome (matches scrape()): a real browser profile
            # survives Amazon's bot checks far better than a fresh headless one.
            browser = None
            cdp_used = None
            for candidate in CDP_URLS:
                try:
                    import urllib.request as _ur
                    _ur.urlopen(f"{candidate}/json/version", timeout=5)
                    browser = pw.chromium.connect_over_cdp(candidate)
                    cdp_used = candidate
                    break
                except Exception:
                    continue
            if browser is not None:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1440, "height": 900},
                    locale="en-IN",
                )
                page = context.new_page()
            else:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1440, "height": 900},
                    locale="en-IN",
                )
                page = context.new_page()
            logger.info("Catalogue extraction using %s", cdp_used or "headless")

            def _collect() -> int:
                """Scroll, extract ASINs from the page, return total seen."""
                prev = len(seen_asins)
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(EXTRACT_SCROLL_WAIT)
                    data = page.evaluate(EXTRACT_JS) or []
                    before = len(seen_asins)
                    for p in data:
                        m = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", p.get("url", ""))
                        asin = m.group(1) if m else ""
                        if asin and asin not in seen_asins:
                            seen_asins.add(asin)
                            products.append(p)
                    if len(seen_asins) == before:
                        break
                return len(seen_asins) - prev

            if _store_slug:
                _brand, _brand_url = _resolve_brand_filter_browser(
                    page, store_url, _store_slug)
                if _brand:
                    store_name = _brand[:40]
                    store_url = _brand_url
                    logger.info("Brand store -> brand search (verified in browser): %s",
                                store_url)
                else:
                    logger.warning(
                        "No brand filter matched %r in the browser either — "
                        "falling back to the storefront", _store_slug)

            _start = max(1, int(start_page or 1))
            _chunk = int(max_pages) if max_pages else EXTRACT_MAX_PAGES
            _last = min(EXTRACT_MAX_PAGES, _start + _chunk - 1)
            _first_url = (
                store_url if _start <= 1
                else f"{store_url}{'&' if '?' in store_url else '?'}page={_start}"
            )
            logger.info("Navigating to store URL: %s (pages %s-%s)", _first_url, _start, _last)
            page.goto(_first_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)

            if _is_bot_page(page.content(), page.url):
                logger.warning("Blocked on store URL: %s", store_url)
                context.close() if cdp_used is None else None
                if cdp_used is None:
                    browser.close()
                else:
                    page.close()
                return {"store_name": store_name, "products": [], "error": "Blocked by Amazon"}

            _collect()

            # Paginate via the &page=N query param — far more reliable than
            # clicking the next button, and it keeps working for merchant
            # catalogues with hundreds of pages. Stop when a page yields no new
            # ASINs (Amazon returns "no results" past the last page).
            base_url = store_url
            sep = "&" if "?" in base_url else "?"
            page_no = _start
            stall = 0
            has_more = False
            while page_no < _last:
                page_no += 1
                loaded = False
                # A page that comes back empty is usually throttling/backoff, not
                # the end of the catalogue — reload it once before counting it as
                # a stall. Without this, two bad pages near the middle silently
                # truncated the catalogue (e.g. 130 of 220 products).
                for _try in range(2):
                    try:
                        # 'commit' resolves as soon as the response starts — far
                        # cheaper than domcontentloaded on a heavy page, and it
                        # keeps the whole catalogue extraction well inside the
                        # deployed proxy timeout (Render was taking 120-200s and
                        # could be cut off mid-catalogue).
                        page.goto(f"{base_url}{sep}page={page_no}",
                                  wait_until="commit", timeout=45000)
                        # wait for the result grid, best-effort
                        try:
                            page.wait_for_selector('[data-component-type="s-search-result"]',
                                                   timeout=12000)
                        except Exception:
                            pass
                        page.wait_for_timeout(800 + _try * 1500)
                        loaded = True
                        break
                    except Exception:
                        time.sleep(2)
                if not loaded:
                    break
                new = _collect()
                if new == 0:
                    stall += 1
                    logger.info("Catalogue page %s added nothing (stall %s/3)", page_no, stall)
                    if stall >= 3:
                        break
                else:
                    stall = 0
                if page_no % 5 == 0:
                    logger.info("Catalogue page %s: %s products so far",
                                page_no, len(seen_asins))
                time.sleep(0.8)   # gentle pacing to avoid mid-catalogue throttling
            else:
                # the loop ran to the chunk cap without stalling => more pages exist
                has_more = _last < EXTRACT_MAX_PAGES

            if cdp_used is None:
                context.close()
                browser.close()
            else:
                try:
                    page.close()
                except Exception:
                    pass
    except Exception as exc:
        return {"store_name": store_name, "products": products, "error": str(exc)}

    return {
        "store_name": store_name,
        "products": products[:EXTRACT_MAX_PRODUCTS],
        "error": "",
        "has_more": has_more,
        "next_page": (_last + 1) if has_more else None,
    }



if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "scrape"
    url = sys.argv[2] if len(sys.argv) > 2 else ""

    if not url:
        print(json.dumps({"status": "failed", "error": "No URL provided"}))
        sys.exit(1)

    try:
        if action == "extract":
            result = extract_products(url)
            print(json.dumps(result))
        else:
            result = scrape(url)
            print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
        sys.exit(1)
