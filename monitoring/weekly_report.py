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
    # metric like "step_mweb_checkout_flow" -> "checkout_flow"
    if metric.startswith("step_"):
        metric = metric[5:]
    for plat in ("mweb_", "web_"):
        if metric.startswith(plat):
            return metric[len(plat):]
    return metric


def _platform(metric: str) -> str:
    for plat in ("mweb", "web"):
        if metric.startswith(f"step_{plat}"):
            return plat
    return "?"


def _fetch(since: str, until: str | None = None) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    base = (f"{SUPABASE_URL}/rest/v1/monitoring_runs?select=*&run_at=gte.{quote(since)}"
            f"&page=eq.happy_flow&order=run_at.asc")
    if until:
        base += f"&run_at=lt.{quote(until)}"
    # PostgREST caps a response at 1000 rows by default — paginate so a busy
    # week is not silently truncated (which made the previous-week window look
    # empty / under-counted).
    out: list[dict] = []
    offset = 0
    page = 1000
    while True:
        url = f"{base}&limit={page}&offset={offset}"
        req = urllib.request.Request(url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                chunk = json.loads(resp.read())
        except Exception as e:
            print(f"[WEEKLY] Query error: {e}")
            break
        out.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
        if offset > 500000:  # safety
            break
    return out


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


def _compare(cur: dict, prev: dict) -> None:
    """Add week-over-week deltas and the carried-over (unfixed) steps.

    A step that was problematic in the PREVIOUS window and is still failing now
    is "carried over" — it must be called out at the top of the new report so
    last week's action items are visibly still open.
    """
    prev_by = {s["step"]: s for s in prev.get("steps", [])}
    for s in cur.get("steps", []):
        p = prev_by.get(s["step"])
        s["prev_pass_rate"] = p["pass_rate"] if p else None
        s["delta"] = (s["pass_rate"] - p["pass_rate"]) if p else None
        s["prev_fail"] = p["fail"] if p else None

    prev_red = {s["step"] for s in prev.get("problem_steps", [])}
    cur_by = {s["step"]: s for s in cur.get("steps", [])}
    cur_red = {s["step"] for s in cur.get("problem_steps", [])}

    # "Still open" = not green now — i.e. red OR yellow (fails **or**
    # degradations). A step flagged last week that is now merely degraded is
    # STILL not fixed; counting only `fail > 0` wrongly labelled the three
    # consistently-degraded steps (Bargain 0%, 2nd Bargain 17%, Checkout 38%)
    # as "FIXED".
    cur_bad = {s["step"] for s in cur.get("steps", []) if s.get("health") != "green"}
    carried = sorted(prev_red & cur_bad)
    newly = sorted(cur_red - prev_red)
    fixed = sorted(prev_red - cur_bad)

    cur["carried_over"] = [cur_by[x] for x in carried if x in cur_by]
    cur["new_problems"] = [cur_by[x] for x in newly if x in cur_by]
    cur["fixed"] = [prev_by[x] for x in fixed if x in prev_by]
    cur["had_prev_window"] = bool(prev.get("steps"))
    cur["prev_window"] = prev


def format_email(w: dict) -> str:
    prev_total = w.get("prev_window", {}).get("total_fails")
    total_txt = f"Total happy-flow failures: {w['total_fails']}"
    if w.get("had_prev_window") and prev_total is not None:
        total_txt += f"  (previous week: {prev_total})"
    lines = [
        "Gajab Synthetic Monitor — Weekly Happy-Flow Summary",
        "=" * 55,
        f"Generated: {w['generated_at']}",
        f"Window: last {w['days']} days",
        total_txt,
        "",
    ]

    # Headline: what was flagged last week and is STILL open.
    if w.get("had_prev_window"):
        lines.append("🚨 NOT FIXED SINCE LAST WEEK'S REPORT")
        lines.append("-" * 55)
        if w.get("carried_over"):
            for s in w["carried_over"]:
                prev = f"{s['prev_pass_rate']}%" if s.get("prev_pass_rate") is not None else "?"
                lines.append(
                    f"🔴 {s['label']}: still {s['pass_rate']}% pass (was {prev} last week), "
                    f"{s['fail']} fail this week"
                )
                if s.get("last_error"):
                    lines.append(f"   Last error: {s['last_error']}")
            lines.append("   -> these were reported last week and are STILL failing.")
        else:
            lines.append("None — every step flagged last week is now passing. 🎉")
        if w.get("fixed"):
            lines.append("")
            lines.append("✅ FIXED SINCE LAST WEEK: " + ", ".join(s["label"] for s in w["fixed"]))
        if w.get("new_problems"):
            lines.append("🆕 NEW THIS WEEK: " + ", ".join(s["label"] for s in w["new_problems"]))
        lines.append("")

    lines.append("PER-STEP PASS RATE  (delta vs last week)")
    lines.append("-" * 55)
    for s in w["steps"]:
        icon = {"green": "✅", "yellow": "🟡", "red": "🔴"}.get(s["health"], "⚪")
        plats = "/".join(s["platforms"])
        d = s.get("delta")
        dtxt = "" if d is None else (f"  Δ{d:+d}pt" if d else "  Δ0pt")
        lines.append(f"{icon} {s['label']} [{plats}]: {s['pass_rate']}% pass{dtxt} "
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
    if w.get("had_prev_window"):
        lines.append("")
        lines.append("Steps that were flagged LAST week and are STILL failing (call these out explicitly as unfixed):")
        if w.get("carried_over"):
            for s in w["carried_over"]:
                prev = f"{s['prev_pass_rate']}%" if s.get("prev_pass_rate") is not None else "?"
                lines.append(f"- {s['label']}: {s['pass_rate']}% now vs {prev} last week ({s['fail']} fails) — NOT FIXED")
        else:
            lines.append("- none")
    return "\n".join(lines)


def format_slack(w: dict) -> str:
    lines = [f"📊 *Weekly Happy-Flow Summary — {w['days']} days*", ""]
    if w.get("had_prev_window") and w.get("carried_over"):
        lines.append("🚨 *NOT FIXED since last week's report:*")
        for s in w["carried_over"]:
            prev = f"{s['prev_pass_rate']}%" if s.get("prev_pass_rate") is not None else "?"
            lines.append(f"   • *{s['label']}* — {s['pass_rate']}% pass (was {prev}), {s['fail']} fail")
        lines.append("")
    elif w.get("had_prev_window"):
        lines.append("✅ Nothing carried over — last week's issues are cleared.")
        lines.append("")
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
    now = datetime.now(timezone.utc)
    cur_since = (now - timedelta(days=DAYS)).isoformat()
    prev_since = (now - timedelta(days=2 * DAYS)).isoformat()
    rows = _fetch(cur_since)
    if not rows:
        print(f"[WEEKLY] No happy-flow data in last {DAYS} days")
        return 0
    # Previous window (the 7 days before this one) for the week-over-week
    # comparison + the carried-over "not fixed" highlighting.
    prev_rows = _fetch(prev_since, cur_since)

    w = build_weekly(rows)
    _compare(w, build_weekly(prev_rows))
    email_body = format_email(w)

    # Natural-language executive summary via Groq
    groq_summary = summarize(_groq_prompt(w), "You are a concise, plain-English monitoring analyst. Report facts only.")
    if groq_summary:
        email_body = f"EXECUTIVE SUMMARY\n-----------------\n{groq_summary}\n\n{email_body}"

    print("=" * 60)
    print(email_body)
    print("=" * 60)

    subject = f"[GAJAB] Weekly Happy-Flow Summary — {w['days']} days"
    n_unfixed = len(w.get("carried_over", []))
    if n_unfixed:
        subject += f" — {n_unfixed} unfixed from last week"
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
