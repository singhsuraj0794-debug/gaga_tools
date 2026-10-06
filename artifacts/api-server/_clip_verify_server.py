"""
Persistent CLIP/EasyOCR verification server.

Loads CLIP + EasyOCR + MiniLM ONCE and keeps them resident in memory, then
serves /verify requests over a local HTTP socket. This removes the per-batch
model reload cost (~13s) that the one-shot `_clip_verify.py` CLI incurs, which
matters for large catalogues (hundreds/thousands of products).

Usage:
    python3 _clip_verify_server.py            # default port 8001
    CLIP_VERIFY_PORT=8002 python3 _clip_verify_server.py

Endpoints:
    GET  /health          -> {"status": "ok"}
    POST /verify          -> body {"products": [...], "useQwenVerify": bool}
                             response {"results": [...]}

The Node API (`/api/products/clip-verify`) proxies here when the server is
running and falls back to the one-shot CLI when it is not.
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _clip_verify import process_batch, load_image

PORT = int(os.environ.get("CLIP_VERIFY_PORT", "8001"))
MAX_WORKERS = int(os.environ.get("CLIP_VERIFY_WORKERS", "3"))


def _prefetch_images(products):
    """Download every image URL in the batch concurrently. Downloads are
    network-bound, so high concurrency (12 threads) is safe and dramatically
    cuts wall-clock time vs the 3 inference workers doing inline downloads.
    The bytes land in `load_image`'s cache, so inference workers never wait on
    the network."""
    urls = []
    for p in products:
        first = p.get("firstImageUrl") or ""
        if first:
            urls.append(first)
        urls.extend(u for u in (p.get("allImageUrls") or []) if u)
    urls = list(dict.fromkeys(u for u in urls if u))
    if not urls:
        return
    from concurrent.futures import ThreadPoolExecutor
    print(f"[SERVER] Prefetching {len(urls)} image(s) concurrently...", file=sys.stderr)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda u: load_image(u), urls))
    print(f"[SERVER] Prefetch done in {time.time()-t0:.1f}s", file=sys.stderr)


def _process_parallel(products, use_qwen_verify):
    """Process products across a thread pool. Model inference (torch/EasyOCR)
    releases the GIL, so threads give real speedup. Each worker handles a
    chunk of products via the existing process_batch (models are cached at
    module level and shared)."""
    n = len(products)
    if n <= 1:
        return process_batch(products, use_qwen_verify=use_qwen_verify)

    workers = min(MAX_WORKERS, n)
    chunk = max(1, (n + workers - 1) // workers)
    chunks = [products[i:i + chunk] for i in range(0, n, chunk)]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(process_batch, c, use_qwen_verify) for c in chunks]
        for fut in as_completed(futures):
            all_results.extend(fut.result()["results"])
    # Preserve original input order (threads may finish out of order).
    by_sku = {r["sku"]: r for r in all_results}
    ordered = [by_sku[p.get("sku")] for p in products if p.get("sku") in by_sku]
    ordered.extend(r for r in all_results if r not in ordered)
    return {"results": ordered}


class Handler(BaseHTTPRequestHandler):
    # Allow rebinding quickly after a restart (avoid TIME_WAIT "address in use").
    allow_reuse_address = True

    def _json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected (e.g. timed out) — nothing to send to

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/verify":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            data = json.loads(body)
            products = data.get("products", [])
            use_qwen = data.get("useQwenVerify", False)
            t0 = time.time()
            _prefetch_images(products)
            result = _process_parallel(products, use_qwen)
            print(f"[SERVER] processed {len(products)} products in {time.time()-t0:.1f}s", file=sys.stderr)
            self._json(200, result)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass  # silence per-request logging


def _warmup():
    print("[SERVER] Warming up models...", file=sys.stderr)
    steps = []
    try:
        from _clip_verify import get_clip
        get_clip()
        steps.append("CLIP")
    except Exception as e:
        print(f"[SERVER] CLIP warmup failed: {e}", file=sys.stderr)
    try:
        from _clip_verify import get_ocr_reader
        get_ocr_reader()
        steps.append("EasyOCR")
    except Exception as e:
        print(f"[SERVER] EasyOCR warmup failed: {e}", file=sys.stderr)
    try:
        from _hsn_suggest import get_claim_vecs
        get_claim_vecs()
        steps.append("MiniLM")
    except Exception as e:
        print(f"[SERVER] MiniLM warmup failed: {e}", file=sys.stderr)
    print(f"[SERVER] Ready ({', '.join(steps)})", file=sys.stderr)


def main():
    _warmup()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[SERVER] Failed to bind 127.0.0.1:{PORT}: {e}", file=sys.stderr)
        print("[SERVER] Another clip-verify server is probably already running. If not, free the port or set CLIP_VERIFY_PORT to a different value.", file=sys.stderr)
        sys.exit(1)
    print(f"[SERVER] clip-verify server listening on 127.0.0.1:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()
