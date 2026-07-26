from __future__ import annotations
import time
import urllib.request
import json

API_ENDPOINTS = [
    {"name": "Health Check (healthz)", "method": "GET", "url": "https://product-video-scraper-api.onrender.com/api/healthz"},
    {"name": "Products Status (products/status)", "method": "GET", "url": "https://product-video-scraper-api.onrender.com/api/products/status"},
    {"name": "Price Mappings (price-mapper/mappings)", "method": "GET", "url": "https://product-video-scraper-api.onrender.com/api/price-mapper/mappings"},
]


def call_api(entry: dict) -> dict:
    t0 = time.time()
    status = "pass"
    error = None
    status_code = None
    response_size = None
    try:
        req = urllib.request.Request(
            entry["url"],
            method=entry.get("method", "GET"),
            headers={"User-Agent": "gajab-monitor/1.0", "Accept": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        duration = int((time.time() - t0) * 1000)
        status_code = resp.status
        data = resp.read()
        response_size = len(data)
        if status_code >= 500:
            status = "fail"
            error = f"HTTP {status_code}"
        elif duration > 5000:
            status = "degraded"
    except urllib.error.HTTPError as e:
        duration = int((time.time() - t0) * 1000)
        status_code = e.code
        status = "degraded" if e.code < 500 else "fail"
        error = f"HTTP {e.code}"
    except Exception as e:
        duration = int((time.time() - t0) * 1000)
        status = "fail"
        error = str(e)[:100]

    return {
        "api": entry["name"],
        "method": entry["method"],
        "url": entry["url"],
        "status": status,
        "duration_ms": duration,
        "status_code": status_code,
        "response_size_bytes": response_size,
        "error": error,
    }


def monitor_apis() -> list[dict]:
    print("  --- API Endpoint Checks ---", flush=True)
    results = []
    for entry in API_ENDPOINTS:
        result = call_api(entry)
        results.append(result)
        icon = "✅" if result["status"] == "pass" else "⚠️" if result["status"] == "degraded" else "❌"
        detail = f"{result['duration_ms']}ms"
        if result.get("status_code"):
            detail += f" | HTTP {result['status_code']}"
        if result.get("response_size_bytes"):
            detail += f" | {result['response_size_bytes']}b"
        if result.get("error"):
            detail += f" | {result['error']}"
        print(f"    {icon} {result['api']}: {result['status']} ({detail})", flush=True)
    return results
