#!/usr/bin/env python3
"""
happy_flow_summary.py — Happy-flow RESULTS summary (email + console).

Reports the actual end-user journey test results for every platform
(mweb, web, android): the latest run's step-by-step status, per-step pass
rates over the window, and the current failures with RCA.

This is a RESULTS report — not an infrastructure/update report.

Usage:
  python3 happy_flow_summary.py [hours]     (default 24)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from urllib.parse import quote
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from email_alert import send_email
from config import SUPABASE_URL, SUPABASE_KEY

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 24

PLATFORMS = [("mweb", "Mobile Web"), ("web", "Desktop Web"), ("android", "Android App")]

STEP_LABELS = {
    "home_load": "Home page loads",
    "home_products_populate": "Home products populate",
    "banners_check": "Banners / category tabs",
    "category_all_load": "Category: All",
    "category_home-kitchen_load": "Category: Home & Kitchen",
    "category_toys-games_load": "Category: Toys & Games",
    "category_fashion-accessories_load": "Category: Fashion",
    "category_electronics_load": "Category: Electronics",
    "category_load": "Category load",
    "product_detail_load": "Product detail (bargainable)",
    "bargain_flow": "Bargain flow",
    "checkout_flow": "Checkout + Pay",
    "bargain2_flow": "Second bargain",
    "page_my_bargains": "My Bargains page",
    "my_bargains": "My Bargains page",
    "page_alerts_orders": "Alerts / Orders page",
    "alerts_orders": "Alerts / Orders page",
    "search_products": "Search products",
}

MARK = {"pass": "PASS", "degraded": "DEGR", "fail": "FAIL"}


def _rest(path: str):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    return json.load(urllib.request.urlopen(req, timeout=60))


def _platform(metric: str) -> str | None:
    m = metric or ""
    for p, _ in PLATFORMS:
        if m.startswith(f"step_{p}_"):
            return p
    return None


def _step(metric: str, platform: str) -> str:
    return (metric or "").replace(f"step_{platform}_", "")


def _fetch(hours: int):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    q = (
        "monitoring_runs?select=run_at,page,metric,status,step_failed,duration_ms,details"
        f"&run_at=gte.{quote(since, safe='')}"
        "&page=in.(happy_flow,native_happy_flow)"
        "&order=run_at.desc&limit=3000"
    )
    rows = _rest(q)
    return [r for r in rows if r.get("page") in ("happy_flow", "native_happy_flow")]


def build(hours: int) -> dict:
    rows = _fetch(hours)
    out = {
        "generated_at": datetime.now(timezone.utc),
        "hours": hours,
        "platforms": {},
        "total_rows": len(rows),
    }
    for p, label in PLATFORMS:
        sub = [r for r in rows if _platform(r.get("metric", "")) == p]
        if not sub:
            out["platforms"][p] = {"label": label, "runs": 0, "latest": None, "steps": {}}
            continue
        latest_at = max(r["run_at"] for r in sub)
        latest = [r for r in sub if r["run_at"] == latest_at]
        steps_latest = []
        for r in sorted(latest, key=lambda x: x.get("metric", "")):
            st = r.get("status")
            steps_latest.append({
                "step": _step(r["metric"], p),
                "label": STEP_LABELS.get(_step(r["metric"], p), _step(r["metric"], p)),
                "status": st,
                "error": (r.get("step_failed") or "").strip(),
                "duration_ms": r.get("duration_ms"),
            })
        # per-step pass rate over window
        agg = defaultdict(lambda: {"pass": 0, "degraded": 0, "fail": 0})
        for r in sub:
            st = r.get("status")
            if st in agg[_step(r["metric"], p)]:
                agg[_step(r["metric"], p)][st] += 1
        rates = {}
        for st, c in agg.items():
            tot = c["pass"] + c["degraded"] + c["fail"]
            rates[st] = round((c["pass"] + 0.5 * c["degraded"]) / tot * 100) if tot else 0
        out["platforms"][p] = {
            "label": label,
            "runs": len({r["run_at"] for r in sub}),
            "latest": latest_at,
            "steps": steps_latest,
            "rates": rates,
            "totals": {
                "pass": sum(1 for r in sub if r.get("status") == "pass"),
                "degraded": sum(1 for r in sub if r.get("status") == "degraded"),
                "fail": sum(1 for r in sub if r.get("status") == "fail"),
            },
        }
    return out


def format_email(rep: dict) -> str:
    gen = rep["generated_at"].strftime("%d %b %Y, %H:%M UTC")
    lines = [
        "GAJAB — HAPPY FLOW RESULTS SUMMARY",
        "=" * 58,
        f"Window : last {rep['hours']}h",
        f"Generated: {gen}",
        "",
        "The happy flow replays the real user journey (home -> category -> product",
        "-> bargain -> checkout) on each platform and records PASS / DEGRADED / FAIL",
        "for every step.",
        "",
    ]
    # Overall snapshot
    lines.append("PLATFORM SNAPSHOT")
    lines.append("-" * 58)
    for p, _ in PLATFORMS:
        d = rep["platforms"][p]
        if not d.get("latest"):
            lines.append(f"  {d['label']:<14} no runs in window")
            continue
        t = d["totals"]
        lines.append(
            f"  {d['label']:<14} runs={d['runs']:<3} latest={d['latest'][:16]}  "
            f"[pass {t['pass']} / degraded {t['degraded']} / fail {t['fail']}]"
        )
    lines.append("")

    # Per-platform latest run detail
    for p, _ in PLATFORMS:
        d = rep["platforms"][p]
        if not d.get("latest"):
            continue
        lines.append(f"{d['label'].upper()} — LATEST RUN ({d['latest'][:16]})")
        lines.append("-" * 58)
        for s in d["steps"]:
            mark = MARK.get(s["status"], s["status"])
            dur = f" ({s['duration_ms']}ms)" if s.get("duration_ms") else ""
            lines.append(f"  [{mark}] {s['label']}{dur}")
            if s["status"] != "pass" and s["error"]:
                lines.append(f"          -> {s['error'][:150]}")
        worst = sorted(d["rates"].items(), key=lambda kv: kv[1])[:5]
        lines.append("")
        lines.append("  Step pass rates (window):")
        for st, rate in worst:
            lines.append(f"    {STEP_LABELS.get(st, st):<34} {rate}%")
        lines.append("")

    # Recurring failures across window
    fails = []
    for p, _ in PLATFORMS:
        d = rep["platforms"][p]
        for st, rate in d.get("rates", {}).items():
            if rate < 100:
                fails.append((rate, d["label"], STEP_LABELS.get(st, st)))
    fails.sort()
    lines.append("RECURRING ISSUES (steps not at 100% in window)")
    lines.append("-" * 58)
    if fails:
        for rate, label, step in fails:
            lines.append(f"  {rate:>3}%  [{label}] {step}")
    else:
        lines.append("  None — all steps passed every run.")
    lines.append("")
    lines.append("-" * 58)
    lines.append("Automated happy-flow results report. Source: synthetic monitor runs.")
    return "\n".join(lines)


def main():
    rep = build(HOURS)
    body = format_email(rep)
    print(body)
    print()
    send_email(
        f"[Gajab] Happy Flow results summary — last {HOURS}h",
        body,
    )


if __name__ == "__main__":
    main()
