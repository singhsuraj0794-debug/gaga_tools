#!/usr/bin/env python3
import sys
import time
from datetime import datetime, timezone

from lighthouse_audit import audit_all_pages
from happy_flow import run_happy_flow
from server_health import check_health as check_server_health
from api_monitor import monitor_apis
from feature_checks import run_feature_checks
from supabase_client import SupabaseStore
from slack_alert import send_alert


def main():
    print("=" * 60, flush=True)
    print(f"[MONITOR] Run started at {datetime.now(timezone.utc).isoformat()}", flush=True)
    print("=" * 60, flush=True)

    store = SupabaseStore()
    t_start = time.time()
    failures = []

    # ── Part 1: Server Health ──
    print("\n--- Server Health ---", flush=True)
    health_results = check_server_health()
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
            failures.append(f"Server/{r['service']}: {r.get('error', 'unreachable')}")
            send_alert(f"Server health failed: {r['service']}", f"Status: {r['status']}\n{r.get('error', '')}")

    # ── Part 2: API Monitoring ──
    print("\n--- API Monitoring ---", flush=True)
    api_results = monitor_apis()
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
            value=float(r.get("status_code", 0)),
            status=r["status"],
        )

    # ── Part 3: Lighthouse Audits ──
    print("\n--- Lighthouse Audits ---", flush=True)
    audit_results = audit_all_pages()
    for result in audit_results:
        page = result["page"]
        metrics = result["metrics"]
        status = result["status"]
        violations = result["violations"]
        print(f"  {page}: status={status} metrics={metrics}", flush=True)
        store.store_audit_results(page, metrics, status)
        if status == "fail":
            failures.append(f"Lighthouse/{page}: {', '.join(violations)}")
            send_alert(
                f"Lighthouse audit failed: {page}",
                f"Status: {status}\nMetrics: {metrics}\nViolations: {', '.join(violations)}",
            )
        elif violations:
            failures.append(f"Lighthouse/{page}: degraded - {', '.join(violations)}")
            send_alert(
                f"Lighthouse degraded: {page}",
                f"Metrics: {metrics}\nViolations: {', '.join(violations)}",
            )

    # ── Part 4: Happy-Flow Check ──
    print("\n--- Happy-Flow Check ---", flush=True)
    flow_results = run_happy_flow()
    flow_overall = "pass"
    for step in flow_results:
        step_name = step["step"]
        step_status = step["status"]
        duration = step["duration_ms"]
        error = step.get("error")
        detail = step.get("detail", "")
        print(f"  {step_name}: status={step_status} duration={duration}ms", flush=True)
        store.store_flow_step("happy_flow", step_name, duration, step_status, error or detail[:200] if detail else error)
        if step_status in ("fail", "degraded"):
            flow_overall = step_status
            msg = f"Step '{step_name}' failed: {error}" if error else f"Step '{step_name}' degraded ({duration}ms)"
            failures.append(f"HappyFlow/{step_name}: {msg}")
            send_alert(f"Happy flow {step_status}: {step_name}", msg)

    print(f"  happy_flow overall: {flow_overall}", flush=True)

    # ── Part 5: Element-Level Feature Checks ──
    print("\n--- Feature Element Checks ---", flush=True)
    feature_results = run_feature_checks()
    for r in feature_results:
        store.store_result(
            page_or_flow=f"feature/{r['page']}",
            metric=f"element_{r['check']}",
            value=float(r["duration_ms"]),
            status=r["status"],
            step_failed=r.get("error"),
            duration_ms=r["duration_ms"],
        )
        if r["status"] == "fail":
            failures.append(f"Feature/{r['page']}/{r['check']}: {r.get('error', 'missing')}")
    failed_features = [r for r in feature_results if r["status"] == "fail"]
    if failed_features:
        send_alert("Feature checks failed", f"{len(failed_features)} elements failed out of {len(feature_results)}")

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
