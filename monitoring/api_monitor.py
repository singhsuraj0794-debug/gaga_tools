from __future__ import annotations
import time
import urllib.request
import json

# Gajab.com public-facing API endpoints to monitor
API_ENDPOINTS = [
    {
        "name": "Home Page (gajab.com/)",
        "method": "GET",
        "url": "https://gajab.com/",
    },
    {
        "name": "Category Page (gajab.com/product-list/all)",
        "method": "GET",
        "url": "https://gajab.com/product-list/all",
    },
    {
        "name": "Gateway Service (gatewayservice.gajab.com)",
        "method": "POST",
        "url": "https://gatewayservice.gajab.com/customer/api/customer/mobile-send-otp-new",
        "body": json.dumps({"mobileNumber": "9876543210", "isLogin": 1}),
        "content_type": "application/json",
    },
    {
        "name": "Product Store API",
        "method": "GET",
        "url": "https://gatewayservice.gajab.com/product/api/product-store/product/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914?pincode=400001",
    },
    {
        "name": "Image CDN (resize.gajab.com)",
        "method": "GET",
        "url": "https://resize.gajab.com/",
    },
]


def call_api(entry: dict) -> dict:
    t0 = time.time()
    result_status = "pass"
    error = None
    status_code = None
    response_size = None
    try:
        data_bytes = entry.get("body", None)
        if isinstance(data_bytes, str):
            data_bytes = data_bytes.encode()
        req = urllib.request.Request(
            entry["url"],
            data=data_bytes,
            method=entry.get("method", "GET"),
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
                "Accept": "application/json",
                "Content-Type": entry.get("content_type", "application/json"),
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        duration = int((time.time() - t0) * 1000)
        status_code = resp.status
        raw = resp.read()
        response_size = len(raw)
        if status_code >= 500:
            result_status = "fail"
            error = f"HTTP {status_code}"
        elif duration > 5000:
            result_status = "degraded"
            error = f"Slow ({duration}ms)"
    except urllib.error.HTTPError as e:
        duration = int((time.time() - t0) * 1000)
        status_code = e.code
        result_status = "degraded" if e.code < 500 else "fail"
        error = f"HTTP {e.code}"
    except Exception as e:
        duration = int((time.time() - t0) * 1000)
        result_status = "fail"
        error = str(e)[:100]

    return {
        "api": entry["name"],
        "method": entry["method"],
        "url": entry["url"],
        "status": result_status,
        "duration_ms": duration,
        "status_code": status_code,
        "response_size_bytes": response_size,
        "error": error,
    }


def monitor_apis() -> list[dict]:
    print("  --- Gajab.com API Checks ---", flush=True)
    results = []
    for entry in API_ENDPOINTS:
        result = call_api(entry)
        results.append(result)
        icon = "✅" if result["status"] == "pass" else "⚠️" if result["status"] == "degraded" else "❌"
        detail = f"{result['duration_ms']}ms"
        if result.get("status_code"):
            detail += f" | HTTP {result['status_code']}"
        if result.get("error"):
            detail += f" | {result['error']}"
        if result.get("response_size_bytes"):
            detail += f" | {result['response_size_bytes']}b"
        print(f"    {icon} {result['api']}: {result['status']} ({detail})", flush=True)
    return results
