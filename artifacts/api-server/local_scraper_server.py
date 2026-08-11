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
import os
import sys
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9000

# Always use Webshare proxy for Meesho curl_cffi (rotating IPs, prevents blocks)
if "MEESHO_PROXY" not in os.environ:
    os.environ["MEESHO_PROXY"] = "http://uvuqatrj-in-rotate:fd9sp5s4yg8q@p.webshare.io:80"

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
        elif self.path == "/search":
            self._handle_search()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

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
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
