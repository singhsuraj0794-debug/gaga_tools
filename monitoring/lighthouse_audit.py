from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path
from config import LIGHTHOUSE_CLI, THRESHOLDS, PAGESPEED_API_KEY, PAGESPEED_URLS
from urllib.request import Request, urlopen
from urllib.parse import urlencode


LAB_METRICS_MAP = {
    "performance": ("performance_score", lambda r: r["categories"]["performance"]["score"] * 100),
    "largest-contentful-paint": ("lcp_ms", lambda r: r["audits"]["largest-contentful-paint"]["numericValue"]),
    "cumulative-layout-shift": ("cls", lambda r: r["audits"]["cumulative-layout-shift"]["numericValue"]),
    "total-blocking-time": ("tbt_ms", lambda r: r["audits"]["total-blocking-time"]["numericValue"]),
    "interactive": ("si_ms", lambda r: r["audits"]["interactive"]["numericValue"]),
    "max-potential-fid": ("inp_ms", lambda r: r["audits"].get("max-potential-fid", {}).get("numericValue", 0)),
}


def run_lighthouse(url: str) -> dict | None:
    output_path = f"/tmp/lighthouse_{int(time.time())}.json"
    cmd = [
        LIGHTHOUSE_CLI,
        url,
        "--output=json",
        f"--output-path={output_path}",
        "--chrome-flags=--headless --no-sandbox --disable-gpu",
        "--preset=desktop",
        "--quiet",
    ]
    print(f"[LIGHTHOUSE] Running audit on {url}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[LIGHTHOUSE] CLI failed for {url}: {result.stderr[:500]}")
        # Fallback: try getting from the output path
        pass
    report_path = Path(output_path)
    if not report_path.exists():
        # lighthouse writes to a file with spaces/special chars replaced
        for p in Path("/tmp").glob("lighthouse_*.json"):
            report_path = p
            break
    if report_path.exists():
        with open(report_path) as f:
            data = json.load(f)
        report_path.unlink(missing_ok=True)
        return data
    print(f"[LIGHTHOUSE] No report file found for {url}")
    return None


def extract_metrics(report: dict) -> dict:
    metrics = {}
    for audit_key, (name, extractor) in LAB_METRICS_MAP.items():
        try:
            metrics[name] = round(extractor(report), 2)
        except (KeyError, TypeError, ValueError) as e:
            print(f"[LIGHTHOUSE] Failed to extract {audit_key}: {e}")
    return metrics


def check_thresholds(metrics: dict) -> tuple[str, list[str]]:
    violations = []
    if metrics.get("performance_score", 100) < THRESHOLDS["performance_score"] * 100:
        violations.append(f"performance_score={metrics.get('performance_score')} < {THRESHOLDS['performance_score']*100}")
    if metrics.get("lcp_ms", 0) > THRESHOLDS["lcp_ms"]:
        violations.append(f"lcp_ms={metrics.get('lcp_ms')} > {THRESHOLDS['lcp_ms']}")
    if metrics.get("cls", 0) > THRESHOLDS["cls"]:
        violations.append(f"cls={metrics.get('cls')} > {THRESHOLDS['cls']}")
    if metrics.get("tbt_ms", 0) > THRESHOLDS["tbt_ms"]:
        violations.append(f"tbt_ms={metrics.get('tbt_ms')} > {THRESHOLDS['tbt_ms']}")
    if metrics.get("si_ms", 0) > THRESHOLDS["si_ms"]:
        violations.append(f"si_ms={metrics.get('si_ms')} > {THRESHOLDS['si_ms']}")
    status = "fail" if violations else "pass"
    return status, violations


def run_pagespeed_insights(url: str) -> dict | None:
    if not PAGESPEED_API_KEY:
        print("[PAGESPEED] Skipping — no API key configured")
        return None
    params = urlencode({"url": url, "key": PAGESPEED_API_KEY, "strategy": "MOBILE"})
    request_url = f"https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?{params}"
    print(f"[PAGESPEED] Fetching field data for {url}")
    try:
        req = Request(request_url)
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data
    except Exception as e:
        print(f"[PAGESPEED] API error for {url}: {e}")
        return None


def extract_field_metrics(psi_data: dict) -> dict:
    metrics = {}
    try:
        lh = psi_data.get("lighthouseResult", {})
        metrics["performance_score"] = round(lh.get("categories", {}).get("performance", {}).get("score", 0) * 100, 2)
    except Exception:
        pass
    try:
        crux = psi_data.get("loadingExperience", {})
        metrics["field_lcp_ms"] = crux.get("metrics", {}).get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile", 0)
        metrics["field_cls"] = crux.get("metrics", {}).get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile", 0)
        metrics["field_fid_ms"] = crux.get("metrics", {}).get("FIRST_INPUT_DELAY_MS", {}).get("percentile", 0)
    except Exception:
        pass
    return metrics


def audit_all_pages() -> list[dict]:
    results = []
    for page_name, url in [("home", "https://gajab.com/"), ("category", "https://gajab.com/product-list/all"), ("product_detail", "https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914")]:
        report = run_lighthouse(url)
        if not report:
            results.append({"page": page_name, "metrics": {}, "status": "fail", "violations": ["lighthouse audit failed"]})
            continue
        metrics = extract_metrics(report)
        status, violations = check_thresholds(metrics)
        results.append({"page": page_name, "metrics": metrics, "status": status, "violations": violations})
        # Once daily: field data
        psi = run_pagespeed_insights(url)
        if psi:
            field_metrics = extract_field_metrics(psi)
            results.append({"page": f"{page_name}_field", "metrics": field_metrics, "status": "pass", "violations": []})
    return results
