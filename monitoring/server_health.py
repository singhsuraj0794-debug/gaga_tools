from __future__ import annotations
import time
import urllib.request
import json

HEALTH_ENDPOINTS = {
    "api_server": "https://product-video-scraper-api.onrender.com/api/healthz",
    "gajab_home": "https://gajab.com/",
    "gajab_gateway": "https://gatewayservice.gajab.com/customer/api/customer/mobile-send-otp-new",
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
            if e.code >= 500:
                status = "fail"
            else:
                status = "degraded"
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
