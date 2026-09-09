#!/usr/bin/env python3
"""
Persistent analysis server (Qwen VLM + HSN + text correction).

Loads the heavy models (Qwen2.5-VL-3B, CLIP, MiniLM, Marqo) ONCE and keeps them
resident, then serves /correct-text and /hsn-suggest over a local HTTP socket.

WHY: the cloudflared quick tunnel returns 524 after ~100s. The one-shot CLI
reloads Qwen (~60s) + CLIP on EVERY batch, so a 10-product Qwen batch easily
exceeds 100s and the request dies. Keeping the models resident makes each
request finish well under the limit.

Usage:
    python3 _analysis_server.py            # default port 8003
    ANALYSIS_PORT=8004 python3 _analysis_server.py

Endpoints:
    GET  /health              -> {"status": "ok"}
    POST /correct-text        -> body {"products": [...], "useQwen": bool}
                                response {"results": [{sku,title,description,log}]}
    POST /hsn-suggest         -> body {"products": [...], "imgbKey": "..."}
                                response (same shape as _hsn_suggest.py)

The Node API proxies here when the server is running and falls back to the
one-shot CLI when it is not.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor

PORT = int(os.environ.get("ANALYSIS_PORT", "8003"))
MAX_WORKERS = int(os.environ.get("ANALYSIS_WORKERS", "2"))

_models_ready = False


def _ensure_models():
    """Load the models used by text-correction + HSN once (lazy, on first use)."""
    global _models_ready
    if _models_ready:
        return
    print("[ANALYSIS] Loading models...", file=sys.stderr)
    t0 = time.time()
    # Qwen2.5-VL-3B (shared loader — loads exactly once in this process)
    from _qwen_model import get_model as qwen_get_model
    qwen_get_model()
    print(f"[ANALYSIS]   Qwen loaded in {time.time()-t0:.1f}s", file=sys.stderr)
    _models_ready = True


def _run_correct_text(products, use_qwen):
    from _hsn_suggest import correct_product_text

    def _one(p):
        corrected = correct_product_text(
            title=p.get("title", ""),
            description=p.get("description", ""),
            brand=p.get("brand", ""),
            category=p.get("category", ""),
            product_type_label=p.get("productTypeLabel", ""),
            images=p.get("images") or None,
            polish=False,
            v2_category=p.get("v2Category", ""),
            use_qwen=use_qwen,
        )
        return {
            "sku": p.get("sku", ""),
            "title": corrected.get("title"),
            "description": corrected.get("description"),
            "log": corrected.get("log", []),
        }

    # Parallel inference. MLX (Metal) releases the GIL, so threads give real
    # speedup — a bigger batch then fits inside the tunnel's ~100s limit.
    results = []
    if len(products) > 1 and MAX_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(_one, products))
    else:
        results = [_one(p) for p in products]
    return {"results": results}


def _run_hsn_suggest(products, imgb_key=""):
    from _hsn_suggest import process_products
    return process_products(products)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok" if _models_ready else "warming"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/correct-text", "/hsn-suggest"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            t0 = time.time()
            if self.path == "/correct-text":
                _ensure_models()
                products = data.get("products", [])
                use_qwen = data.get("useQwen", False)
                result = _run_correct_text(products, use_qwen)
            else:
                products = data.get("products", [])
                result = _run_hsn_suggest(products, data.get("imgbKey", ""))
            print(f"[ANALYSIS] {self.path} {len(products)} products in {time.time()-t0:.1f}s", file=sys.stderr)
            self._json(200, result)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[ANALYSIS] Failed to bind 127.0.0.1:{PORT}: {e}", file=sys.stderr)
        print("[ANALYSIS] Another analysis server is probably already running. Free the port or set ANALYSIS_PORT.", file=sys.stderr)
        sys.exit(1)
    print(f"[ANALYSIS] analysis server listening on 127.0.0.1:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ANALYSIS] Shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()