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
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9000

# Import scrapers from the same directory
sys.path.insert(0, ".")
import _scraper as flipkart_scraper
import _amazon_scraper as amazon_scraper
import _meesho_scraper as meesho_scraper
import _platform_searcher


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

            if page_num > 0:
                # Single-page chunk (fits under ngrok's ~60s limit)
                print(f"[EXTRACT] meesho page {page_num}: {url}", flush=True)
                result = meesho_scraper.extract_page(url, page_num)
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
    server = HTTPServer(("0.0.0.0", PORT), ScraperHandler)
    print(f"Local scraper server listening on port {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
