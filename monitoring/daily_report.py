#!/usr/bin/env python3
"""
daily_report.py — Consolidated daily health summary with scoring.

Queries the last 24h of monitoring_runs from Supabase, computes a health
score per flow + an overall score, flags flows that are "not in order"
(consistently failing even when the average is green), and sends ONE
email (Gmail SMTP) plus a Slack summary.

Usage:
  python3 daily_report.py [hours]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from urllib.parse import quote
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from email_alert import send_email
from config import SUPABASE_URL, SUPABASE_KEY, SLACK_WEBHOOK_URL
from rca import generate_rca
from groq_summary import summarize

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 24

# Flow group -> label + color thresholds
FLOW_LABELS = {
    "server": "Server Health",
    "api": "API Endpoints",
    "home": "Home Page (Lighthouse)",
    "category": "Category (Lighthouse)",
    "product_detail": "Product Detail (Lighthouse)",
    "happy_flow": "Happy Flow",
    "feature": "Feature Elements",
}


def _flow_of(page: str) -> str:
    if page.startswith("happy_flow"):
        return "happy_flow"
    if page.startswith("feature/"):
        return "feature"
    if page.startswith("server/"):
        return "server"
    if page.startswith("api/"):
        return "api"
    return page  # home, category, product_detail


def _rca_check_name(flow: str, metric: str) -> str:
    if flow in ("home", "category", "product_detail"):
        return f"lighthouse_{metric}"
    if flow == "happy_flow":
        return metric  # "step_mweb_checkout_flow" already contains "checkout_flow"
    if flow == "feature":
        return f"feature_{metric}"
    if flow == "server":
        return f"server_{metric}"
    if flow == "api":
        return f"api_{metric}"
    return metric


def _status_score(status: str) -> float:
    return {"pass": 1.0, "degraded": 0.5, "fail": 0.0}.get(status, 0.0)


def build_report(rows: list[dict]) -> dict:
    flows: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0, "degraded": 0, "total": 0, "checks": defaultdict(list)})
    for r in rows:
        status = r.get("status")
        if status not in ("pass", "fail", "degraded"):
            continue
        flow = _flow_of(r.get("page", ""))
        flows[flow][status] += 1
        flows[flow]["total"] += 1
        check = {
            "metric": r.get("metric", ""),
            "status": status,
            "step_failed": (r.get("step_failed") or "").strip(),
        }
        if status in ("fail", "degraded"):
            detail = check["step_failed"] or (f"{r.get('metric', '')} exceeded threshold" if flow in ("home", "category", "product_detail") else "")
            rca = generate_rca(_rca_check_name(flow, r.get("metric", "")), detail)
            check["rca"] = {
                "summary": rca.get("summary", ""),
                "causes": rca.get("probable_causes", [])[:3],
                "actions": rca.get("actions", [])[:3],
                "severity": rca.get("severity", "medium"),
            }
        flows[flow]["checks"][r.get("page", "")].append(check)

    # Per-flow scores
    flow_rows = []
    for flow, data in flows.items():
        if data["total"] == 0:
            continue
        score = round(sum(_status_score(s) for s in
                         ["pass"] * data["pass"] + ["degraded"] * data["degraded"] + ["fail"] * data["fail"]) / data["total"] * 100)
        if score < 60:
            status = "red"
        elif score < 90:
            status = "yellow"
        else:
            status = "green"
        flow_rows.append({
            "flow": flow,
            "label": FLOW_LABELS.get(flow, flow),
            "score": score,
            "status": status,
            "pass": data["pass"],
            "degraded": data["degraded"],
            "fail": data["fail"],
            "total": data["total"],
            "checks": data["checks"],
        })

    flow_rows.sort(key=lambda f: f["score"])

    # Overall score = weighted average across all checks
    total_checks = sum(f["total"] for f in flow_rows)
    overall = round(sum(f["score"] * f["total"] for f in flow_rows) / total_checks) if total_checks else 0

    # "Not in order" = flows failing below threshold even though the daily average is otherwise healthy
    not_in_order = [f for f in flow_rows if f["score"] < 60]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hours": HOURS,
        "overall": overall,
        "flows": flow_rows,
        "not_in_order": not_in_order,
        "total_checks": total_checks,
    }


def _groq_prompt(report: dict) -> str:
    lines = [
        "Summarize this synthetic-monitoring report for a technical lead. Be concise and actionable: "
        "what's healthy, what's broken, and the single most important thing to fix next. Use plain English, no markdown.",
        "",
        f"Overall health score: {report['overall']}/100 (last {report['hours']}h, {report['total_checks']} checks).",
        "Per-flow scores:",
    ]
    for f in report["flows"]:
        lines.append(f"- {f['label']}: {f['score']}/100 (pass {f['pass']}, fail {f['fail']}, degraded {f['degraded']})")
    lines.append("")
    lines.append("Failing checks and root causes:")
    for f in report["not_in_order"]:
        for checks in f["checks"].values():
            for c in checks:
                if c["status"] == "fail":
                    lines.append(f"- {f['label']} / {c['metric']}: {c['step_failed'] or '(threshold exceeded)'}")
                    rca = c.get("rca")
                    if rca and rca.get("causes"):
                        lines.append(f"    causes: {'; '.join(rca['causes'][:2])}")
    return "\n".join(lines)


def format_email(report: dict) -> str:
    lines = [
        "Gajab Synthetic Monitor — Daily Health Summary",
        "=" * 50,
        f"Generated: {report['generated_at']}",
        f"Window: last {report['hours']}h",
        f"Total checks: {report['total_checks']}",
        f"Overall health score: {report['overall']}/100",
        "",
        "PER-FLOW SCORES",
        "-" * 50,
    ]
    for f in report["flows"]:
        icon = {"green": "✅", "yellow": "🟡", "red": "🔴"}.get(f["status"], "⚪")
        lines.append(
            f"{icon} {f['label']}: {f['score']}/100 "
            f"(pass {f['pass']} / degraded {f['degraded']} / fail {f['fail']})"
        )

    lines.append("")
    lines.append("NOT IN ORDER (failing even when average is OK)")
    lines.append("-" * 50)
    if report["not_in_order"]:
        for f in report["not_in_order"]:
            lines.append(f"🔴 {f['label']} — {f['fail']} failing of {f['total']} checks")
            for page, checks in f["checks"].items():
                for c in checks:
                    if c["status"] != "fail":
                        continue
                    page_clean = page.replace("feature/", "").replace("server/", "").replace("api/", "")
                    lines.append(f"   • {page_clean} / {c['metric']}: {c['step_failed']}")
                    rca = c.get("rca")
                    if rca:
                        if rca.get("summary") and rca["summary"] != c["step_failed"]:
                            lines.append(f"       🔍 {rca['summary']}")
                        if rca.get("causes"):
                            lines.append("       Likely causes:")
                            for cause in rca["causes"]:
                                lines.append(f"         - {cause}")
                        if rca.get("actions"):
                            lines.append("       Actions:")
                            for action in rca["actions"]:
                                lines.append(f"         - {action}")
    else:
        lines.append("No flows are consistently failing today.")

    lines.append("")
    lines.append("Note: this is an automated report from the Synthetic Monitor.")
    return "\n".join(lines)


def format_slack(report: dict) -> str:
    lines = [
        f"📊 *Daily Health Summary — {report['overall']}/100*",
        f"_Window: last {report['hours']}h · {report['total_checks']} checks_",
        "",
    ]
    for f in report["flows"]:
        icon = {"green": "✅", "yellow": "🟡", "red": "🔴"}.get(f["status"], "⚪")
        lines.append(f"{icon} {f['label']}: {f['score']}/100 (P{f['pass']}/D{f['degraded']}/F{f['fail']})")
    if report["not_in_order"]:
        lines.append("")
        lines.append("⚠️ *Not in order:*")
        for f in report["not_in_order"]:
            lines.append(f"   • {f['label']} — {f['fail']} failing")
    return "\n".join(lines)


def _fetch_rows(since: str) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[REPORT] Missing SUPABASE_URL/SUPABASE_KEY")
        return []
    url = f"{SUPABASE_URL}/rest/v1/monitoring_runs?select=*&run_at=gte.{quote(since)}&order=run_at.asc"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[REPORT] Query error: {e}")
        return []


def main():
    since = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).isoformat()
    rows = _fetch_rows(since)

    if not rows:
        print(f"[REPORT] No data in last {HOURS}h")
        return 0

    report = build_report(rows)
    email_body = format_email(report)

    # Generate a natural-language executive summary via Groq
    groq_summary = summarize(_groq_prompt(report), "You are a concise, plain-English monitoring analyst. Report facts only.")
    if groq_summary:
        email_body = f"EXECUTIVE SUMMARY\n-----------------\n{groq_summary}\n\n{email_body}"

    print("=" * 60)
    print(email_body)
    print("=" * 60)

    # Send email
    subject = f"[GAJAB] Daily Health Summary — {report['overall']}/100"
    send_email(subject, email_body)

    # Send Slack summary
    if SLACK_WEBHOOK_URL:
        from slack_alert import send_alert
        slack_body = format_slack(report)
        if groq_summary:
            slack_body = f"🤖 *AI Summary:*\n{groq_summary}\n\n{slack_body}"
        send_alert(subject.replace("[GAJAB] ", ""), slack_body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
