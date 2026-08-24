#!/usr/bin/env python3
"""
weekly_report.py — Weekly summary of the happy-flow checks.

Queries the last 7 days of happy_flow monitoring_runs from Supabase, computes
per-step pass rates and fail trends, flags the most problematic steps, and
sends ONE email (Gmail SMTP) + Slack summary.

Usage:
  python3 weekly_report.py [days]   (default 7)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from urllib.parse import quote
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from email_alert import send_email
from config import SUPABASE_URL, SUPABASE_KEY, SLACK_WEBHOOK_URL
from rca import generate_rca
from groq_summary import summarize

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7

STEP_LABELS = {
    "home_load": "Home Page",
    "home_products_populate": "Products Populate",
    "category_all_load": "Category: All",
    "category_home-kitchen_load": "Category: Home & Kitchen",
    "category_toys-games_load": "Category: Toys & Games",
    "category_fashion-accessories_load": "Category: Fashion",
    "category_electronics_load": "Category: Electronics",
    "product_detail_load": "Product Detail",
    "bargain_flow": "Bargain",
    "checkout_flow": "Checkout + Pay",
    "bargain2_flow": "2nd Bargain",
    "search_products": "Search",
    "banners_check": "Banners",
    "page_my_bargains": "My Bargains",
    "page_alerts_orders": "Alerts / Orders",
    "unknown": "Home Page (load failed)",
}


def _step_base(metric: str) -> str:
    # metric like "step_mweb_checkout_flow" -> "mweb:checkout_flow"
    if metric.startswith("step_"):
        metric = metric[5:]
    for plat in ("mweb_", "web_"):
        if metric.startswith(plat):
            return metric[len(plat):]
    return metric


def _platform(metric: str) -> str:
    if metric.startswith("step_mweb"):
        return "mweb"
    if metric.startswith("step_web"):
        return "web"
    return "?"


def _fetch(since: str) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/monitoring_runs?select=*&run_at=gte.{quote(since)}&page=eq.happy_flow&order=run_at.asc"
    req = urllib.request.Request(url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[WEEKLY] Query error: {e}")
        return []


def build_weekly(rows: list[dict]) -> dict:
    steps = defaultdict(lambda: {"pass": 0, "fail": 0, "degraded": 0, "total": 0, "last_error": "", "platforms": set()})
    daily_fail = defaultdict(int)
    for r in rows:
        status = r.get("status")
        if status not in ("pass", "fail", "degraded"):
            continue
        base = _step_base(r.get("metric", ""))
        plat = _platform(r.get("metric", ""))
        s = steps[base]
        s[status] += 1
        s["total"] += 1
        s["platforms"].add(plat)
        if status == "fail":
            s["last_error"] = (r.get("step_failed") or "").strip()
            day = (r.get("run_at") or "")[:10]
            daily_fail[day] += 1

    step_rows = []
    for base, s in steps.items():
        if s["total"] == 0:
            continue
        rate = round(s["pass"] / s["total"] * 100)
        if s["fail"] == 0 and s["degraded"] == 0:
            health = "green"
        elif rate >= 80:
            health = "yellow"
        else:
            health = "red"
        step_rows.append({
            "step": base,
            "label": STEP_LABELS.get(base, base),
            "platforms": sorted(s["platforms"]),
            "pass_rate": rate,
            "health": health,
            "pass": s["pass"],
            "fail": s["fail"],
            "degraded": s["degraded"],
            "total": s["total"],
            "last_error": s["last_error"],
        })

    step_rows.sort(key=lambda x: x["pass_rate"])
    problem_steps = [x for x in step_rows if x["health"] == "red"]
    total_fails = sum(x["fail"] for x in step_rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": DAYS,
        "steps": step_rows,
        "problem_steps": problem_steps,
        "total_fails": total_fails,
        "daily_fail": dict(sorted(daily_fail.items())),
    }


def format_email(w: dict) -> str:
    lines = [
        "Gajab Synthetic Monitor — Weekly Happy-Flow Summary",
        "=" * 55,
        f"Generated: {w['generated_at']}",
        f"Window: last {w['days']} days",
        f"Total happy-flow failures: {w['total_fails']}",
        "",
        "PER-STEP PASS RATE",
        "-" * 55,
    ]
    for s in w["steps"]:
        icon = {"green": "✅", "yellow": "🟡", "red": "🔴"}.get(s["health"], "⚪")
        plats = "/".join(s["platforms"])
        lines.append(f"{icon} {s['label']} [{plats}]: {s['pass_rate']}% pass "
                     f"({s['pass']} pass / {s['fail']} fail / {s['degraded']} degraded)")

    lines.append("")
    lines.append("MOST PROBLEMATIC STEPS (need attention)")
    lines.append("-" * 55)
    if w["problem_steps"]:
        for s in w["problem_steps"]:
            lines.append(f"🔴 {s['label']}: {s['pass_rate']}% pass ({s['fail']} fails / {s['total']} runs)")
            if s["last_error"]:
                lines.append(f"   Last error: {s['last_error']}")
                rca = generate_rca(f"step_{s['step']}", s["last_error"])
                if rca.get("probable_causes"):
                    lines.append("   Likely causes:")
                    for c in rca["probable_causes"][:3]:
                        lines.append(f"     - {c}")
                if rca.get("actions"):
                    lines.append("   Actions:")
                    for a in rca["actions"][:3]:
                        lines.append(f"     - {a}")
    else:
        lines.append("No steps are consistently failing this week.")

    if w["daily_fail"]:
        lines.append("")
        lines.append("FAILURES BY DAY")
        lines.append("-" * 55)
        for day, count in w["daily_fail"].items():
            bar = "#" * min(count, 40)
            lines.append(f"   {day}: {count:>3}  {bar}")

    lines.append("")
    lines.append("Note: this is an automated report from the Synthetic Monitor.")
    return "\n".join(lines)


def _groq_prompt(w: dict) -> str:
    lines = [
        "Summarize this weekly happy-flow monitoring report for a technical lead. Be concise and actionable: "
        "which user journeys are failing, the trend over the week, and the single most important fix. Plain English, no markdown.",
        "",
        f"Window: last {w['days']} days, {w['total_fails']} total happy-flow failures.",
        "Per-step pass rates:",
    ]
    for s in w["steps"]:
        lines.append(f"- {s['label']}: {s['pass_rate']}% pass ({s['fail']} fail, {s['degraded']} degraded)")
    lines.append("")
    lines.append("Most problematic steps:")
    for s in w["problem_steps"]:
        lines.append(f"- {s['label']}: {s['pass_rate']}% pass — {s['last_error'][:120]}")
    return "\n".join(lines)


def format_slack(w: dict) -> str:
    lines = [f"📊 *Weekly Happy-Flow Summary — {w['days']} days*", ""]
    for s in w["steps"]:
        icon = {"green": "✅", "yellow": "🟡", "red": "🔴"}.get(s["health"], "⚪")
        lines.append(f"{icon} {s['label']}: {s['pass_rate']}% ({s['fail']}F)")
    if w["problem_steps"]:
        lines.append("")
        lines.append("⚠️ *Needs attention:*")
        for s in w["problem_steps"]:
            lines.append(f"   • {s['label']} — {s['pass_rate']}% pass")
    return "\n".join(lines)


def main():
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).isoformat()
    rows = _fetch(since)
    if not rows:
        print(f"[WEEKLY] No happy-flow data in last {DAYS} days")
        return 0

    w = build_weekly(rows)
    email_body = format_email(w)

    # Natural-language executive summary via Groq
    groq_summary = summarize(_groq_prompt(w), "You are a concise, plain-English monitoring analyst. Report facts only.")
    if groq_summary:
        email_body = f"EXECUTIVE SUMMARY\n-----------------\n{groq_summary}\n\n{email_body}"

    print("=" * 60)
    print(email_body)
    print("=" * 60)

    subject = f"[GAJAB] Weekly Happy-Flow Summary — {w['days']} days"
    send_email(subject, email_body)

    if SLACK_WEBHOOK_URL:
        from slack_alert import send_alert
        slack_body = format_slack(w)
        if groq_summary:
            slack_body = f"🤖 *AI Summary:*\n{groq_summary}\n\n{slack_body}"
        send_alert(subject.replace("[GAJAB] ", ""), slack_body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
