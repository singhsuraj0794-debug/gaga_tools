#!/usr/bin/env python3
"""
local_scraper_server.py — Lightweight HTTP server that runs Flipkart/Amazon
scrapers locally.  Expose this via ngrok so your Render backend can offload
heavy Playwright work to your laptop.

Usage:
  python3 local_scraper_server.py          # listens on port 9000
  ngrok http 9000                          # expose to internet
  # Then set LOCAL_SCRAPER_URL on Render to the ngrok URL
"""

import json
import sys
import traceback
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 9000

# Import scrapers from the same directory
sys.path.insert(0, ".")
import _scraper as flipkart_scraper
import _amazon_scraper as amazon_scraper
import _meesho_scraper as meesho_scraper
import _platform_searcher


def _sweep_leaked_tabs():
    """Close browser tabs abandoned by earlier runs.

    stop-all.ps1 deliberately leaves Chrome running across restarts so logins
    and cookies survive, which also means its tabs do. _try_playwright used to
    close its tab only on the success path, so every goto() timeout left a
    product page behind — and those leaked tabs keep running JS, slowing each
    later navigation until more of them time out and leak in turn (21 Meesho
    product pages were found open after one run). This runs before we accept
    any request, so nothing in flight can be using them.
    """
    import urllib.request
    for base in (getattr(meesho_scraper, "SCRAPE_CDP_URL", None),
                 getattr(meesho_scraper, "EXTRACT_CDP_URL", None)):
        if not base:
            continue
        try:
            with urllib.request.urlopen(f"{base}/json", timeout=5) as r:
                targets = json.load(r)
        except Exception:
            continue  # Chrome not up yet, or the port isn't ours
        closed = 0
        for t in targets:
            if t.get("type") != "page":
                continue
            url = t.get("url") or ""
            if not url or url.startswith(("chrome://", "about:", "devtools://")):
                continue  # keep the default blank tab
            url = f"{base}/json/close/{t.get('id')}"
            try:
                urllib.request.urlopen(
                    urllib.request.Request(url, method="PUT"), timeout=5).read()
                closed += 1
            except Exception:
                try:
                    urllib.request.urlopen(url, timeout=5).read()  # older Chrome
                    closed += 1
                except Exception:
                    pass
        if closed:
            print(f"closed {closed} leaked tab(s) on {base}", flush=True)


class ScraperHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/scrape":
            self._handle_scrape()
        elif self.path == "/extract":
            self._handle_extract()
        elif self.path == "/search":
            self._handle_search()
        elif self.path.startswith("/api/"):
            # Reverse-proxy the pre-listing compute API (port 8090) through the
            # SAME permanent ngrok domain as the scraper. That gives the
            # validator a Compute API URL that never changes:
            #   https://<permanent-domain>/api/...
            self._proxy_api()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy_api()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def _proxy_api(self):
        """Forward /api/* to the local pre-listing API server on port 8090."""
        import urllib.request
        import urllib.error

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        target = "http://127.0.0.1:8090" + self.path
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        req = urllib.request.Request(target, data=body, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=1800) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            payload = e.read() if hasattr(e, "read") else b"{}"
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            msg = json.dumps({"error": f"proxy failed: {e}"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _handle_extract(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            url = body.get("url", "")
            page_num = int(body.get("page", 0)) or 0

            if not url:
                self._json(400, {"status": "failed", "error": "url required"})
                return

            # Amazon catalogue/brand-search extraction. The deployed API cannot
            # do this reliably (headless Chromium exceeds its proxy timeout and
            # memory), so the deployed route proxies here: this machine has a
            # residential IP and finishes a 220-product catalogue in ~30-60s.
            if "amazon." in url or "amzn." in url:
                start_page = int(body.get("page", 1) or 1)
                pages = int(body.get("pages", 0) or 0)
                print(f"[EXTRACT] amazon catalogue: {url} (start_page={start_page} pages={pages or 'all'})", flush=True)
                result = amazon_scraper.extract_products(
                    url, start_page=start_page, max_pages=pages or None,
                )
                self._json(200, {
                    "status": "success" if not result.get("error") else "failed",
                    "store_name": result.get("store_name", ""),
                    "storeName": result.get("store_name", ""),
                    "products": result.get("products") or [],
                    "total": len(result.get("products") or []),
                    "has_more": bool(result.get("has_more")),
                    "next_page": result.get("next_page"),
                    "error": result.get("error") or "",
                })
                return

            if page_num > 0:
                # Single-page chunk (fits under ngrok's ~60s limit)
                sort = str(body.get("sort") or "")
                print(f"[EXTRACT] meesho page {page_num}{' sort=' + sort if sort else ''}: {url}", flush=True)
                result = meesho_scraper.extract_page(url, page_num, sort)
                self._json(200, result)
                return

            print(f"[EXTRACT] meesho: {url}", flush=True)
            result = meesho_scraper.extract_store(url)
            self._json(200, result)

        except Exception as e:
            traceback.print_exc()
            self._json(500, {"status": "failed", "error": str(e)})

    def _handle_scrape(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            url = body.get("url", "")
            platform = body.get("platform", "flipkart")

            if not url:
                self._json(400, {"status": "failed", "error": "url required"})
                return

            print(f"[SCRAPER] {platform}: {url}", flush=True)

            if platform == "amazon":
                result = amazon_scraper.scrape(url)
            elif platform == "meesho":
                result = meesho_scraper.scrape_product(url)
            else:
                result = flipkart_scraper.scrape(url)

            self._json(200, result)

        except Exception as e:
            traceback.print_exc()
            self._json(500, {"status": "failed", "error": str(e)})

    def _handle_search(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            title = body.get("title", "")
            image_url = body.get("imageUrl", body.get("image_url", ""))
            gajab_price = body.get("price", "")
            gajab_url = body.get("url", "")

            if not title:
                self._json(400, {"status": "failed", "error": "title required"})
                return

            print(f"[SEARCH] {title[:80]}", flush=True)
            result = _platform_searcher.search_all(title, image_url, gajab_price, gajab_url)
            self._json(200, result)

        except Exception as e:
            traceback.print_exc()
            self._json(500, {"status": "failed", "error": str(e)})

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, ngrok-skip-browser-warning")
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[HTTP] {args[0]}", flush=True)


if __name__ == "__main__":
    # ThreadingHTTPServer, NOT HTTPServer: with the single-threaded server one
    # long request (a sheet-duplicates job, a catalogue extract) blocked every
    # other connection — including the /api/* proxy to the compute API — so the
    # validator reported "Not reachable" and scrapes timed out with 000 while the
    # process was still alive. daemon_threads lets the process exit cleanly.
    # Before serving: drop any tabs earlier runs left behind in the shared
    # Chrome (it outlives restarts on purpose).
    try:
        _sweep_leaked_tabs()
    except Exception:
        pass

    server = ThreadingHTTPServer(("0.0.0.0", PORT), ScraperHandler)
    server.daemon_threads = True
    print(f"Local scraper server listening on port {PORT} (threaded)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
