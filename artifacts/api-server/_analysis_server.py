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

# This process runs BOTH torch models (CLIP / DINOv2 / Marqo for image
# attributes) AND MLX models (Qwen2.5-VL for text correction) in one Python
# process. Mixing torch-MPS and MLX-Metal on the same Apple GPU aborts with:
#   MPSNDArray ... Error: buffer is not large enough. Must be 9216 bytes
# which segfaults the server and forces the slow one-shot CLI fallback.
# Force torch onto CPU so MLX alone owns the Metal device.
try:
    import torch as _torch
    _torch.backends.mps.is_available = lambda: False
    _torch.backends.mps.is_built = lambda: False
except Exception:
    pass

PORT = int(os.environ.get("ANALYSIS_PORT", "8003"))
# MUST stay 1: MLX (Qwen) uses the Metal GPU and is not safe to call from
# multiple threads — concurrent access aborts with
#   AGXG16GFamilyCommandBuffer ... 'A command encoder is already encoding'
# which kills the server. CLIP/DINOv2/Marqo run on CPU here (see torch guard
# above) so a single worker keeps the GPU to one caller.
MAX_WORKERS = int(os.environ.get("ANALYSIS_WORKERS", "1"))

_models_ready = False
_marqo_ready = False


def _ensure_models(use_qwen: bool = False):
    """Warm the models actually needed by this request.

    Qwen2.5-VL-3B costs ~3GB resident. It is only needed when the caller asks
    for Qwen text correction, so loading it eagerly (as before) wasted ~3GB
    during image-only runs and pushed the 16GB machine into swap — the cause of
    'fast at first, then slow'. It now loads lazily on first real use.
    """
    global _models_ready
    if not use_qwen or _models_ready:
        return
    print("[ANALYSIS] Loading Qwen (first Qwen request)...", file=sys.stderr)
    t0 = time.time()
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


def _run_category_validate(products):
    from _category_validate import process_products as validate_products
    return validate_products(products)


# ── Category Validation background job ─────────────────────────────────────
# Large sheets (thousands of rows) take hours with Marqo and would blow the
# tunnel's request limit. Jobs run in a background thread, write progress +
# results to disk, and the API polls them. A single global lock serialises
# jobs so the GPU/CPU is never oversubscribed.

import threading

_CAT_JOB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_jobs")
_CAT_JOBS = {}
_CAT_LOCK = threading.Lock()


def _cat_job_path(job_id):
    return os.path.join(_CAT_JOB_DIR, job_id)


def _cat_write(job_id, name, obj):
    d = _cat_job_path(job_id)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, name))


def _cat_read(job_id, name):
    p = os.path.join(_cat_job_path(job_id), name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _run_category_job(job_id, products):
    from _category_validate import process_products as validate_products

    total = len(products)
    state = {"jobId": job_id, "status": "running", "phase": "marqo",
             "done": 0, "total": total, "message": "Marqo classification"}
    _cat_write(job_id, "progress", state)

    try:
        # Products run in a thread pool (CATEGORY_WORKERS, default 4) so one
        # stalled image download can't freeze the batch. on_progress streams
        # partial results to disk without serialising the work.
        def on_progress(done, snapshot):
            state["done"] = done
            if done % 5 == 0 or done == total:
                _cat_write(job_id, "progress", state)
                _cat_write(job_id, "results", {"results": snapshot})

        out = validate_products(products, on_progress=on_progress)
        results = out.get("results", [])
        _cat_write(job_id, "results", {"results": results})

        # Marqo is the source of truth — Qwen no longer overrides
        # `recommended`/`predicted`/`status`; it produced generic catch-all
        # picks (e.g. "Precious Jewellery > gemstone") that overrode correct
        # Marqo results.
        state["phase"] = "qwen"
        state["message"] = "Done (Marqo authoritative)"
        state["done"] = 0
        state["total"] = 0
        _cat_write(job_id, "progress", state)

        state["status"] = "completed"
        state["phase"] = "done"
        state["message"] = "Complete"
        _cat_write(job_id, "progress", state)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        state["status"] = "failed"
        state["message"] = str(e)
        _cat_write(job_id, "progress", state)


def product_images_for(result, product):
    """Return the image URLs used for a product (sheet images, else gateway)."""
    imgs = product.get("images")
    if imgs:
        return imgs
    try:
        from _category_validate import product_images
        return product_images(product.get("url", ""))
    except Exception:
        return []


def _cat_start_job(products):
    import uuid
    job_id = uuid.uuid4().hex[:12]
    _CAT_JOBS[job_id] = {"status": "running"}
    t = threading.Thread(target=_run_category_job, args=(job_id, products), daemon=True)
    t.start()
    return job_id


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
            # Marqo (category validation) and Qwen (lazy, only for text
            # correction) warm independently — report ok once Marqo is up,
            # otherwise /health stays "warming" forever on a box that never
            # asks for Qwen.
            self._json(200, {"status": "ok" if (_marqo_ready or _models_ready) else "warming"})
        elif self.path.startswith("/category-validate/job/"):
            # GET /category-validate/job/<jobId>[/progress|/results]
            parts = self.path.strip("/").split("/")
            job_id = parts[2] if len(parts) > 2 else ""
            what = parts[3] if len(parts) > 3 else "progress"
            if not job_id:
                self._json(400, {"error": "jobId required"})
                return
            data = _cat_read(job_id, "progress" if what != "results" else "results")
            if data is None:
                self._json(404, {"error": "job not found"})
                return
            self._json(200, data)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/correct-text", "/hsn-suggest", "/category-validate", "/category-validate/job"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            t0 = time.time()
            if self.path == "/correct-text":
                products = data.get("products", [])
                use_qwen = data.get("useQwen", False)
                _ensure_models(use_qwen=bool(use_qwen))
                result = _run_correct_text(products, use_qwen)
            elif self.path == "/category-validate/job":
                products = data.get("products", [])
                job_id = _cat_start_job(products)
                print(f"[ANALYSIS] category job {job_id} started ({len(products)} products)", file=sys.stderr)
                self._json(200, {"jobId": job_id})
                return
            elif self.path == "/category-validate":
                products = data.get("products", [])
                result = _run_category_validate(products)
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


def _warm_marqo():
    """Load the taxonomy + Marqo model + label cache at startup.

    First request used to pay the ~8-15s cold model load (and 90s+ if the
    label cache was missing). Doing it once in a daemon thread keeps that cost
    off every job; the load/cache locks make it safe to race a real request.
    """
    global _marqo_ready
    try:
        t0 = time.time()
        import _category_validate as cv
        cv._load_taxonomy()
        from _marqo_classifier import _ensure_model, _encode_labels
        if _ensure_model():
            _encode_labels([p["full"] for p in cv._index.get("paths", [])])
        _marqo_ready = True
        print(f"[ANALYSIS] Marqo warmed in {time.time() - t0:.1f}s", file=sys.stderr)
    except Exception as e:
        print(f"[ANALYSIS] warm-up failed: {e}", file=sys.stderr)


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[ANALYSIS] Failed to bind 127.0.0.1:{PORT}: {e}", file=sys.stderr)
        print("[ANALYSIS] Another analysis server is probably already running. Free the port or set ANALYSIS_PORT.", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("ANALYSIS_NO_WARM") != "1":
        threading.Thread(target=_warm_marqo, daemon=True).start()
    print(f"[ANALYSIS] analysis server listening on 127.0.0.1:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ANALYSIS] Shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()