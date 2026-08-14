#!/usr/bin/env python3
import os
import sys
import time
from datetime import datetime, timezone

from lighthouse_audit import audit_all_pages
from server_health import check_health as check_server_health
from api_monitor import monitor_apis
from supabase_client import SupabaseStore
from slack_alert import send_alert
from rca import generate_rca, format_rca_for_slack


def main():
    print("=" * 60, flush=True)
    print(f"[MONITOR] Run started at {datetime.now(timezone.utc).isoformat()}", flush=True)
    print("=" * 60, flush=True)

    store = SupabaseStore()
    t_start = time.time()
    failures = []

    MODE = os.environ.get("MONITOR_MODE", "full")
    # full: everything (local dev)
    # health: server health + API (India, light/latency checks)
    # browser: lighthouse + happy flow + feature checks (US, heavy browser)
    RUN_HEALTH = MODE in ("full", "health")
    RUN_BROWSER = MODE in ("full", "browser")
    health_results = []
    api_results = []
    audit_results = []
    flow_results = []
    feature_results = []

    # ── Part 1: Server Health ──
    print("\n--- Server Health ---", flush=True)
    health_results = check_server_health() if RUN_HEALTH else []
    for r in health_results:
        store.store_result(
            page_or_flow=f"server/{r['service']}",
            metric="response_time_ms",
            value=float(r["duration_ms"]),
            status=r["status"],
            step_failed=r.get("error"),
            duration_ms=r["duration_ms"],
        )
        if r["status"] == "fail":
            rca = generate_rca(f"server_{r['service']}", f"{r['service']}: {r.get('error', 'unreachable')}")
            failures.append(f"Server/{r['service']}: {r.get('error', 'unreachable')}\n  RCA: {rca['summary']}\n  Actions: {'; '.join(rca['actions'][:3])}")
            send_alert(f"Server health failed: {r['service']}", format_rca_for_slack(rca, f"Server/{r['service']}"))

    # ── Part 2: API Monitoring ──
    print("\n--- API Monitoring ---", flush=True)
    api_results = monitor_apis() if RUN_HEALTH else []
    for r in api_results:
        store.store_result(
            page_or_flow=f"api/{r['api']}",
            metric="response_time_ms",
            value=float(r["duration_ms"]),
            status=r["status"],
            step_failed=r.get("error"),
            duration_ms=r["duration_ms"],
        )
        if r["status"] == "fail":
            failures.append(f"API/{r['api']}: {r.get('error', 'error')}")
            send_alert(f"API failed: {r['api']}", f"HTTP {r.get('status_code')} | {r['duration_ms']}ms\n{r.get('error', '')}")
        store.store_result(
            page_or_flow=f"api/{r['api']}",
            metric="status_code",
            value=float(r.get("status_code") or 0),
            status=r["status"],
        )

    # ── Part 3: Lighthouse Audits ──
    print("\n--- Lighthouse Audits ---", flush=True)
    audit_results = audit_all_pages() if RUN_BROWSER else []
    for result in audit_results:
        page = result["page"]
        metrics = result["metrics"]
        violations = result["violations"]
        violated_metrics = set()
        for v in violations:
            for m in metrics:
                if m in v:
                    violated_metrics.add(m)
        print(f"  {page}: violations={violations} metrics={metrics}", flush=True)
        for metric_name, metric_value in metrics.items():
            metric_status = "fail" if metric_name in violated_metrics else "pass"
            store.store_result(page_or_flow=page, metric=metric_name, value=metric_value, status=metric_status)
        if violations:
            page_violations = [v for v in violations]
            for v in violations:
                metric_key = v.split("=")[0] if "=" in v else v
                rca = generate_rca(f"lighthouse_{metric_key}", v)
                failures.append(f"Lighthouse/{page}: {v}\n  RCA: {rca['summary']}\n  Actions: {'; '.join(rca['actions'][:3])}")
                send_alert(
                    f"Lighthouse audit issues: {page}",
                    format_rca_for_slack(rca, f"Lighthouse/{page}"),
                )

    # ── Part 4: Happy-Flow Check ──
    print("\n--- Happy-Flow Check ---", flush=True)
    print(f"[MONITOR] happy_flow version: 2026-08-12-fix", flush=True)
    try:
        if RUN_BROWSER:
            from happy_flow import run_happy_flow
            flow_results = run_happy_flow()
        else:
            flow_results = []
    except Exception as e:
        print(f"[MONITOR] HAPPY FLOW CRASHED: {e}", flush=True)
        import traceback; traceback.print_exc()
        flow_results = []
    flow_overall = "pass"
    # First pass: upload videos for each platform
    video_urls = {}  # {"mweb": url, "web": url}
    for step in flow_results:
        if step.get("step", "").endswith("session_recording") and step.get("video_path"):
            step_name = step["step"]
            platform = "mweb" if step_name.startswith("mweb_") else "web" if step_name.startswith("web_") else "unknown"
            if platform not in video_urls:
                video_urls[platform] = store.upload_video(step["video_path"], platform=platform)
                print(f"[MONITOR] Video uploaded for {platform}: {video_urls[platform]}", flush=True)

    # Second pass: store all steps with video URL attached
    for step in flow_results:
        step_name = step["step"]
        if step_name.endswith("session_recording"):
            continue
        step_status = step["status"]
        duration = step["duration_ms"]
        error = step.get("error")
        detail = step.get("detail", "")
        screenshot = step.get("screenshot") or {}
        failure_reason = step.get("failure_reason")
        console_errors = step.get("console_errors", [])
        sub_steps = step.get("sub_steps", [])
        # Determine which platform this step belongs to
        platform = "mweb" if step_name.startswith("mweb_") else "web" if step_name.startswith("web_") else None
        details = {
            "failure_reason": failure_reason or error,
            "console_errors": console_errors,
            "sub_steps": sub_steps,
            "detail": detail,
            "url": step.get("url"),
            "product_count": step.get("product_count"),
        }
        # Upload screenshot to Supabase Storage
        ss_path = screenshot.get("path")
        if ss_path:
            ss_url = store.upload_screenshot(ss_path, platform=platform or "")
            if ss_url:
                details["screenshot_url"] = ss_url
        # Attach the correct platform's video URL
        if platform and platform in video_urls and video_urls[platform]:
            details["session_recording_url"] = video_urls[platform]
        store.store_flow_step("happy_flow", step_name, duration, step_status, error or failure_reason or detail[:200] if detail else error, details)
        if step_status in ("fail", "degraded"):
            flow_overall = step_status
            rca_context = error or failure_reason or detail or f"Degraded ({duration}ms)"
            rca = generate_rca(f"happy_flow_{step_name}", rca_context, console_errors)
            msg = f"Step '{step_name}' failed: {error}" if error else f"Step '{step_name}' degraded ({duration}ms)"
            failures.append(f"HappyFlow/{step_name}: {msg}\n  RCA: {rca['summary']}\n  Actions: {'; '.join(rca['actions'][:3])}")
            send_alert(f"Happy flow {step_status}: {step_name}", format_rca_for_slack(rca, f"HappyFlow/{step_name}"))
            details["rca"] = {"summary": rca["summary"], "causes": rca["probable_causes"][:3], "actions": rca["actions"][:3]}

    print(f"  happy_flow overall: {flow_overall}", flush=True)

    # ── Part 5: Element-Level Feature Checks ──
    print("\n--- Feature Element Checks ---", flush=True)
    if RUN_BROWSER:
        from feature_checks import run_feature_checks
        feature_results = run_feature_checks()
    else:
        feature_results = []
    for r in feature_results:
        match_count = r.get("match_count")
        store.store_result(
            page_or_flow=f"feature/{r['page']}",
            metric=f"elem_{r['check']}_{r.get('check_type','visible')}",
            value=1.0 if r["status"] == "pass" else 0.0,
            status=r["status"],
            step_failed=r.get("error") or (f"Found {match_count}, expected >= {r.get('min',1)}" if match_count is not None and r["status"] != "pass" else None),
            duration_ms=r["duration_ms"],
            details={"check": r["check"], "check_type": r.get("check_type","visible"), "match_count": match_count, "min_expected": r.get("min")},
        )
        if r["status"] == "fail":
            rca = generate_rca(f"feature_{r['check']}", r.get("error", "missing"))
            failures.append(f"Feature/{r['page']}/{r['check']}: {r.get('error', 'missing')}\n  RCA: {rca['summary']}\n  Actions: {'; '.join(rca['actions'][:3])}")
    failed_features = [r for r in feature_results if r["status"] == "fail"]
    if failed_features:
        rca = generate_rca("feature_checks", f"{len(failed_features)} elements failed")
        send_alert("Feature checks failed", format_rca_for_slack(rca, "Feature Checks"))

    # ── Summary ──
    elapsed = int((time.time() - t_start) * 1000)
    print("\n" + "=" * 60, flush=True)
    print(f"[MONITOR] SUMMARY", flush=True)
    print(f"  Server health: {len(health_results)} services", flush=True)
    print(f"  API endpoints: {len(api_results)} endpoints", flush=True)
    print(f"  Lighthouse: {len(audit_results)} pages", flush=True)
    print(f"  Happy flow: {len(flow_results)} steps", flush=True)
    print(f"  Feature checks: {len(feature_results)} elements", flush=True)
    if failures:
        print(f"  FAILURES ({len(failures)}):", flush=True)
        for f in failures:
            print(f"    - {f}", flush=True)
    else:
        print("  All checks passed", flush=True)
    print(f"  Duration: {elapsed}ms", flush=True)
    print("=" * 60, flush=True)

    store.store_result(
        page_or_flow="monitor",
        metric="total_duration_ms",
        value=float(elapsed),
        status="degraded" if failures else "pass",
        duration_ms=elapsed,
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
