#!/usr/bin/env python3
"""Playwright-based search for Flipkart (bypasses E002 with real browser)."""
import json
import re
import sys
import time
import traceback
from urllib.parse import quote

BROWSER = None

def _get_browser():
    global BROWSER
    if BROWSER is None:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        BROWSER = p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ]
        )
    return BROWSER

def search_amazon(title: str) -> dict:
    browser = _get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-IN",
    )
    page = context.new_page()
    try:
        query = quote(title[:120])
        url = f"https://www.amazon.in/s?k={query}"
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector('[data-asin]:not([data-asin=""]), .s-result-item', timeout=10000)
        except:
            page.wait_for_timeout(3000)

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(400)

        products = page.evaluate("""
            () => {
                const items = document.querySelectorAll('[data-asin]:not([data-asin=""])');
                return Array.from(items).slice(0, 30).map(el => {
                    const asin = el.getAttribute('data-asin') || '';
                    // Try multiple selectors for product name
                    let name = '';
                    const h2spans = el.querySelectorAll('h2 a, h2 span, h2');
                    for (const el2 of h2spans) {
                        const t = (el2.textContent || '').trim();
                        if (t && t.length > name.length) name = t;
                    }
                    const priceWhole = el.querySelector('.a-price-whole')?.textContent?.trim() || '';
                    const priceSymbol = el.querySelector('.a-price-symbol')?.textContent?.trim() || '₹';
                    const price = priceWhole ? `${priceSymbol}${priceWhole}` : '';
                    const links = el.querySelectorAll('a[href*="/dp/"]');
                    const link = links[0] ? 'https://www.amazon.in' + links[0].getAttribute('href') : '';
                    const imgs = el.querySelectorAll('img.s-image');
                    const img = imgs[0] ? (imgs[0].src || '') : '';
                    return { name: name.slice(0, 300), price, url: link.split('?')[0], image: img, asin };
                }).filter(p => p.name);
            }
        """)
        
        # Remove duplicates by ASIN
        seen = set()
        unique = []
        for p in products:
            if p["asin"] and p["asin"] not in seen:
                seen.add(p["asin"])
                unique.append(p)
        
        return {"status": "success", "products": unique[:15]}
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}
    finally:
        try: context.close()
        except: pass

def search_flipkart(title: str) -> dict:
    browser = _get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-IN",
    )
    page = context.new_page()
    try:
        query = quote(title[:120])
        url = f"https://www.flipkart.com/search?q={query}"
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector('[data-id], a[title]', timeout=10000)
        except:
            page.wait_for_timeout(3000)

        products = page.evaluate("""
            () => {
                const items = document.querySelectorAll('[data-id]');
                return Array.from(items).slice(0, 15).map(el => {
                    const html = el.innerHTML;
                    const titleEl = el.querySelector('a[title]');
                    const name = (titleEl?.title || titleEl?.textContent?.trim() || '').slice(0, 300);
                    const img = el.querySelector('img[src*="http"]')?.src ||
                               el.querySelector('img[src*="//"]')?.getAttribute('data-src') || '';
                    const priceMatch = html.match(/\\u20B9[0-9,]+/);
                    const price = priceMatch ? priceMatch[0] : '';
                    const link = el.querySelector('a[href*="/p/"]');
                    const url = link?.href || link?.getAttribute('href') || '';
                    return { name, price, image: img, url };
                });
            }
        """)

        return {"status": "success", "products": products[:10]}
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}
    finally:
        try: context.close()
        except: pass

def search_meesho(title: str) -> dict:
    browser = _get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-IN",
    )
    page = context.new_page()
    try:
        query = quote(title[:120])
        url = f"https://www.meesho.com/search?q={query}"
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector('a[href*="/p/"]', timeout=15000)
        except:
            page.wait_for_timeout(3000)

        products = page.evaluate("""
            () => {
                const items = document.querySelectorAll('a[href*="/p/"]');
                const seen = new Set();
                return Array.from(items).map(el => {
                    const url = (el.href || '').split('?')[0];
                    if (seen.has(url)) return null;
                    seen.add(url);
                    const leaves = [...el.querySelectorAll('p, span, div')].filter(x => x.children.length === 0 && x.textContent.trim());
                    const texts = leaves.map(x => x.textContent.trim());
                    let name = '';
                    for (const t of texts) {
                        if (t.length > name.length && !/^₹?\\s*[\\d,]+(\\s*%\\s*off)?$/.test(t) && !/^\\d+(\\.\\d+)?$/.test(t) && t.length < 300) {
                            name = t;
                        }
                    }
                    let price = '';
                    for (const t of texts) {
                        const m = t.match(/₹\\s*[\\d,]+/);
                        if (m) { price = m[0]; break; }
                    }
                    const img = el.querySelector('img[src*="http"]')?.src || '';
                    return { name: name.slice(0, 300), price, image: img, url };
                }).filter(p => p && p.name && p.url);
            }
        """)

        return {"status": "success", "products": products[:10]}
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}
    finally:
        try: context.close()
        except: pass


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    title = sys.argv[2] if len(sys.argv) > 2 else ""
    if not action or not title:
        print(json.dumps({"status": "failed", "error": "Usage: _playwright_search.py <amazon|flipkart|meesho> <title>"}))
        sys.exit(0)
    try:
        if action == "amazon":
            result = search_amazon(title)
        elif action == "flipkart":
            result = search_flipkart(title)
        elif action == "meesho":
            result = search_meesho(title)
        else:
            result = {"status": "failed", "error": f"Unknown action: {action}"}
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"status": "failed", "error": f"{e}\n{traceback.format_exc()}"}))
