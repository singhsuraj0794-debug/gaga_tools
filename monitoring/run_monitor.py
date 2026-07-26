#!/usr/bin/env python3
"""
run_monitor.py — Entry point for gajab.com synthetic monitoring.

Runs Lighthouse audits + happy-flow check, stores results in Supabase,
and alerts Slack on failures.
"""

import sys
import time
from datetime import datetime, timezone

from lighthouse_audit import audit_all_pages
from happy_flow import run_happy_flow
from supabase_client import SupabaseStore
from slack_alert import send_alert


def main():
    print("=" * 60, flush=True)
    print(f"[MONITOR] Run started at {datetime.now(timezone.utc).isoformat()}", flush=True)
    print("=" * 60, flush=True)

    store = SupabaseStore()
    t_start = time.time()

    failures = []

    # ── Part 1: Lighthouse audits ──
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

    # ── Part 2: Happy-flow check ──
    print("\n--- Happy-Flow Check ---", flush=True)
    flow_results = run_happy_flow()
    flow_overall = "pass"
    for step in flow_results:
        step_name = step["step"]
        step_status = step["status"]
        duration = step["duration_ms"]
        error = step.get("error")
        print(f"  {step_name}: status={step_status} duration={duration}ms", flush=True)
        store.store_flow_step("happy_flow", step_name, duration, step_status, error)
        if step_status in ("fail", "degraded"):
            flow_overall = step_status
            msg = f"Step '{step_name}' failed: {error}" if error else f"Step '{step_name}' degraded ({duration}ms)"
            failures.append(f"HappyFlow/{step_name}: {msg}")
            send_alert(f"Happy flow {step_status}: {step_name}", msg)
    print(f"  happy_flow overall: {flow_overall}", flush=True)

    # ── Summary ──
    elapsed = int((time.time() - t_start) * 1000)
    print("\n" + "=" * 60, flush=True)
    if failures:
        print(f"[MONITOR] FAILURES ({len(failures)}):", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
    else:
        print("[MONITOR] All checks passed", flush=True)
    print(f"[MONITOR] Run finished in {elapsed}ms", flush=True)
    print("=" * 60, flush=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
