from __future__ import annotations
import time
import urllib.request
import json

HEALTH_ENDPOINTS = {
    "gajab.com (main)": "https://gajab.com/",
    "gajab.com (category)": "https://gajab.com/product-list/all",
    "gatewayservice.gajab.com": "https://gatewayservice.gajab.com/product/api/product-store/product/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914?pincode=400001",
    "resize.gajab.com (CDN)": "https://resize.gajab.com/storeLogo/Gajab_og_banner_1770188098122.jpeg",
}


def check_health(threshold_ms: int = 5000) -> list[dict]:
    results = []
    for name, url in HEALTH_ENDPOINTS.items():
        t0 = time.time()
        status = "pass"
        status_code = None
        error = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            resp = urllib.request.urlopen(req, timeout=15)
            duration = int((time.time() - t0) * 1000)
            status_code = resp.status
            if status_code >= 500:
                status = "fail"
                error = f"HTTP {status_code}"
            elif duration > threshold_ms:
                status = "degraded"
                error = f"Slow response: {duration}ms"
        except urllib.error.HTTPError as e:
            duration = int((time.time() - t0) * 1000)
            status_code = e.code
            status = "degraded" if e.code < 500 else "fail"
            error = f"HTTP {e.code}"
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            status = "fail"
            error = str(e)[:100]

        results.append({
            "service": name,
            "url": url,
            "status": status,
            "duration_ms": duration,
            "status_code": status_code,
            "error": error,
        })
        icon = "✅" if status == "pass" else "⚠️" if status == "degraded" else "❌"
        print(f"  {icon} {name}: {status} ({duration}ms) {error or ''}", flush=True)
    return results
