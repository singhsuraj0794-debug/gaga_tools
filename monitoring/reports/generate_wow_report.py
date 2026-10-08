#!/usr/bin/env python3
"""generate_wow_report.py — rebuild the multi-section Week-on-Week report
(monitoring/reports/synthetic-monitor-weekly-report.md) from Supabase.

Sections mirror the original hand-built report:
  1 executive summary, 2 overall health by ISO week, 3 pass rate by family,
  4 performance metrics (weekly averages), 5 recent-2-weeks vs prior-2-weeks,
  6 Lighthouse score by page, 7 remaining failures (last 2 weeks),
  8 native Android, 9 findings, 10 methodology.

Usage: python3 generate_wow_report.py [output.md]
"""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_URL, SUPABASE_KEY  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("synthetic-monitor-weekly-report.md")

FAMILIES = ["happy_flow", "feature", "lighthouse", "api", "server", "meta", "native_happy_flow"]
LIGHTHOUSE_PAGES = ["home", "category", "product_detail"]
NATIVE_FAMILY = "native_happy_flow"
PERF_METRICS = [
    ("Lighthouse performance score", lambda r: r["page"] in LIGHTHOUSE_PAGES and r["metric"] == "performance_score"),
    ("LCP (ms)", lambda r: r["page"] in LIGHTHOUSE_PAGES and r["metric"] == "lcp_ms"),
    ("Total Blocking Time (ms)", lambda r: r["page"] in LIGHTHOUSE_PAGES and r["metric"] == "tbt_ms"),
    ("Speed Index (ms)", lambda r: r["page"] in LIGHTHOUSE_PAGES and r["metric"] == "si_ms"),
    ("API response (ms)", lambda r: r["page"].startswith("api/") and r["metric"] == "response_time_ms"),
    ("Server response (ms)", lambda r: r["page"].startswith("server/") and r["metric"] == "response_time_ms"),
    ("happy-flow step (ms)", lambda r: r["page"] == "happy_flow" and r["metric"].startswith("step_")),
]


def family_of(page: str) -> str:
    root = page.split("/")[0]
    if root in LIGHTHOUSE_PAGES:
        return "lighthouse"
    if root == "monitor":
        return "meta"
    return root


def fetch_all() -> list[dict]:
    out: list[dict] = []
    off = 0
    while True:
        url = (f"{SUPABASE_URL}/rest/v1/monitoring_runs?select=page,metric,value,status,run_at"
               f"&order=run_at.asc&limit=1000&offset={off}")
        req = urllib.request.Request(url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            ch = json.loads(r.read())
        out += ch
        if len(ch) < 1000:
            break
        off += 1000
    return out


def week_key(iso: str) -> str:
    # Python 3.9's fromisoformat rejects 5-digit microseconds, so parse the
    # date part directly.
    y, mo, d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
    y2, w, _ = datetime(y, mo, d).isocalendar()
    return f"{y2}-W{w:02d}"


def pass_rate(p: int, d: int, f: int) -> float:
    t = p + d + f
    return round(p / t * 100, 1) if t else 0.0


def main() -> None:
    rows = fetch_all()
    if not rows:
        raise SystemExit("no rows")
    for r in rows:
        r["week"] = week_key(r["run_at"])
        r["family"] = family_of(r["page"])
        try:
            r["val"] = float(r["value"]) if r["value"] is not None else None
        except (TypeError, ValueError):
            r["val"] = None

    weeks = sorted({r["week"] for r in rows})
    first_ts, last_ts = rows[0]["run_at"], rows[-1]["run_at"]

    # per week overall
    wk = {w: Counter() for w in weeks}
    for r in rows:
        wk[r["week"]][r["status"]] += 1

    # per week per family
    fam = defaultdict(Counter)  # (week, family) -> status
    for r in rows:
        fam[(r["week"], r["family"])][r["status"]] += 1

    # performance weekly averages
    perf = defaultdict(dict)  # week -> metric label -> mean
    for label, pred in PERF_METRICS:
        byweek = defaultdict(list)
        for r in rows:
            if r["val"] is not None and pred(r):
                byweek[r["week"]].append(r["val"])
        for w, vals in byweek.items():
            perf[w][label] = round(mean(vals), 1)

    # lighthouse score by page
    lh = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["page"] in LIGHTHOUSE_PAGES and r["metric"] == "performance_score" and r["val"] is not None:
            lh[r["page"]][r["week"]].append(r["val"])

    # native per week
    native = {w: Counter() for w in weeks}
    for r in rows:
        if r["family"] == NATIVE_FAMILY:
            native[r["week"]][r["status"]] += 1

    recent2, prior2 = weeks[-2:], weeks[-4:-2]
    def window_avg(label, wks):
        vals = [perf[w][label] for w in wks if label in perf[w]]
        return round(mean(vals), 1) if vals else None
    def window_pass(wks):
        p = sum(wk[w]["pass"] for w in wks); d = sum(wk[w]["degraded"] for w in wks); f = sum(wk[w]["fail"] for w in wks)
        return pass_rate(p, d, f)

    L: list[str] = []
    A = L.append
    A("# Synthetic Monitor — Week-on-Week Report")
    A("")
    A(f"**Period:** {first_ts[:10]} → {last_ts[:10]}  ")
    A("**Source:** Supabase `monitoring_runs`  ")
    A(f"**Rows analysed:** {len(rows):,} checks across {len(weeks)} ISO weeks  ")
    A(f"**Generated:** {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')} UTC")
    A("")
    A("---")
    A("")

    # 1 Executive summary
    first, last = weeks[0], weeks[-1]
    r_first = pass_rate(wk[first]["pass"], wk[first]["degraded"], wk[first]["fail"])
    r_last = pass_rate(wk[last]["pass"], wk[last]["degraded"], wk[last]["fail"])
    best = max(weeks, key=lambda w: pass_rate(wk[w]["pass"], wk[w]["degraded"], wk[w]["fail"]))
    best_r = pass_rate(wk[best]["pass"], wk[best]["degraded"], wk[best]["fail"])
    A("## 1. Executive summary")
    A("")
    A(f"- Overall pass rate went **{r_first}% → {r_last}%** over the period (best week **{best}** at {best_r}%).")
    # infra
    def fam_rate(w, f):
        c = fam[(w, f)]; return pass_rate(c["pass"], c["degraded"], c["fail"])
    A(f"- **Infrastructure:** API {fam_rate(last,'api')}%, server {fam_rate(last,'server')}% in {last}.")
    # lighthouse recent vs prior
    lh_recent = window_avg("Lighthouse performance score", recent2)
    lh_prior = window_avg("Lighthouse performance score", prior2)
    if lh_recent and lh_prior:
        A(f"- **Web performance:** Lighthouse score {lh_prior} → {lh_recent} ({'+' if lh_recent>=lh_prior else ''}{round((lh_recent-lh_prior)/lh_prior*100,1)}%) comparing {'+'.join(recent2)} against {'+'.join(prior2)}.")
    # dominant failure source last 2 weeks
    failfam = Counter()
    for r in rows:
        if r["week"] in recent2 and r["status"] == "fail":
            failfam[r["family"]] += 1
    tot_fail = sum(failfam.values()) or 1
    if failfam:
        topfam, topn = failfam.most_common(1)[0]
        A(f"- **The remaining drag is `{topfam}`**, ~{round(topn/tot_fail*100)}% of failures in the last 2 weeks.")
    # weakest lighthouse page
    page_scores = {p: mean(v for w in weeks for v in lh[p].get(w, [])) for p in LIGHTHOUSE_PAGES if lh[p]}
    if page_scores:
        wp = min(page_scores, key=page_scores.get)
        A(f"- `{wp}` is the weakest Lighthouse page (score {round(page_scores[wp],1)}).")
    A("")
    A("---")
    A("")

    # 2 Overall health by week
    A("## 2. Overall health — week on week")
    A("")
    A("| Week | Checks | Pass | Degraded | Fail | Pass rate |")
    A("|---|---:|---:|---:|---:|---:|")
    for w in weeks:
        c = wk[w]; n = c["pass"] + c["degraded"] + c["fail"]
        A(f"| {w} | {n:,} | {c['pass']:,} | {c['degraded']:,} | {c['fail']:,} | **{pass_rate(c['pass'],c['degraded'],c['fail'])}%** |")
    A("")
    A("---")
    A("")

    # 3 Pass rate by family
    A("## 3. Pass rate by check family")
    A("")
    A("| Family | " + " | ".join(w.split("-")[1] for w in weeks) + " |")
    A("|---|" + "---:|" * len(weeks))
    for f in FAMILIES:
        cells = []
        for w in weeks:
            c = fam[(w, f)]
            cells.append(f"{pass_rate(c['pass'],c['degraded'],c['fail'])}%" if (c["pass"]+c["degraded"]+c["fail"]) else "–")
        A(f"| **{f}** | " + " | ".join(cells) + " |")
    A("")
    A("---")
    A("")

    # 4 Performance metrics weekly averages
    A("## 4. Performance metrics — weekly averages")
    A("")
    A("| Metric | " + " | ".join(w.split("-")[1] for w in weeks) + " |")
    A("|---|" + "---:|" * len(weeks))
    for label, _ in PERF_METRICS:
        cells = [f"{perf[w][label]:g}" if label in perf[w] else "–" for w in weeks]
        A(f"| {label} | " + " | ".join(cells) + " |")
    A("")
    A("---")
    A("")

    # 5 Recent vs prior
    A(f"## 5. Has performance improved recently? — {'+'.join(recent2)} vs {'+'.join(prior2)}")
    A("")
    A("| Metric | " + "+".join(prior2) + " | " + "+".join(recent2) + " | Change | Verdict |")
    A("|---|---:|---:|---:|---|")
    for label, _ in PERF_METRICS:
        a, b = window_avg(label, prior2), window_avg(label, recent2)
        if a is None or b is None:
            continue
        chg = round((b - a) / a * 100, 1) if a else 0.0
        worse_if_up = label not in ("Lighthouse performance score",)
        good = (chg <= 0) if worse_if_up else (chg >= 0)
        A(f"| {label} | {a:g} | {b:g} | {chg:+.1f}% | {'✅ improved' if good else '⚠️ worse'} |")
    pr_a, pr_b = window_pass(prior2), window_pass(recent2)
    A(f"| **Overall pass rate** | {pr_a}% | {pr_b}% | {pr_b-pr_a:+.1f} pts | {'✅ improved' if pr_b>=pr_a else '⚠️ worse'} |")
    A("")
    A("---")
    A("")

    # 6 Lighthouse score by page
    A("## 6. Lighthouse performance score by page")
    A("")
    A("| Page | " + " | ".join(w.split("-")[1] for w in weeks) + " |")
    A("|---|" + "---:|" * len(weeks))
    for p in LIGHTHOUSE_PAGES:
        cells = [f"{round(mean(lh[p][w]),1):g}" if lh[p].get(w) else "–" for w in weeks]
        A(f"| {p} | " + " | ".join(cells) + " |")
    A("")
    A("---")
    A("")

    # 7 Remaining failures last 2 weeks
    A("## 7. Where the remaining failures are (last 2 weeks)")
    A("")
    A("| Check | Checks | Fail | Degraded |")
    A("|---|---:|---:|---:|")
    cnt = defaultdict(Counter)
    for r in rows:
        if r["week"] in recent2:
            cnt[r["metric"]][r["status"]] += 1
    for metric, c in sorted(cnt.items(), key=lambda kv: -(kv[1]["fail"] + kv[1]["degraded"]))[:25]:
        n = c["pass"] + c["degraded"] + c["fail"]
        if c["fail"] + c["degraded"] == 0:
            continue
        A(f"| `{metric}` | {n} | {c['fail']} | {c['degraded']} |")
    A("")
    A("---")
    A("")

    # 8 Native Android
    A("## 8. Native Android app")
    A("")
    A("| Week | Checks | Pass | Degraded | Fail | Pass rate |")
    A("|---|---:|---:|---:|---:|---:|")
    for w in weeks:
        c = native[w]; n = c["pass"] + c["degraded"] + c["fail"]
        if n == 0:
            A(f"| {w} | – | – | – | – | – |")
        else:
            A(f"| {w} | {n} | {c['pass']} | {c['degraded']} | {c['fail']} | **{pass_rate(c['pass'],c['degraded'],c['fail'])}%** |")
    A("")
    A("---")
    A("")

    # 9 Findings
    A("## 9. Findings and recommended actions")
    A("")
    A("| # | Finding | Impact | Recommendation |")
    A("|---|---|---|---|")
    i = 0
    if page_scores:
        wp = min(page_scores, key=page_scores.get)
        i += 1
        A(f"| {i} | `{wp}` is the weakest Lighthouse page (score {round(page_scores[wp],1)}) | High | Preload the above-the-fold hero image (`fetchpriority=high`); stop lazy-loading it |")
    if failfam:
        i += 1
        A(f"| {i} | `{topfam}` accounts for ~{round(topn/tot_fail*100)}% of recent failures | High | Prioritise that family in the next performance pass |")
    # any consistently degraded happy-flow step in last 2 weeks
    deg = Counter()
    for r in rows:
        if r["week"] in recent2 and r["family"] == "happy_flow" and r["status"] == "degraded":
            deg[r["metric"]] += 1
    for metric, n in deg.most_common(2):
        i += 1
        A(f"| {i} | `{metric}` degraded {n}× in the last 2 weeks | Medium | Check the step's time budget / selectors |")
    A("")
    A("---")
    A("")

    # 10 Methodology
    A("## 10. Methodology and caveats")
    A("")
    A("- Data is every row in `monitoring_runs` between the first and last timestamps above; weeks are ISO (Monday–Sunday).")
    A("- A check is `pass`, `degraded` or `fail`. `degraded` usually means slow-but-working (time budget exceeded) or a data-dependent skip, not a hard break.")
    A(f"- **{weeks[-1]} may be a partial week** and can shift as more runs land.")
    A("- Weekly averages blend mobile (mweb) and desktop (web) runs; Lighthouse is mobile-only by configuration.")
    A("- Run volume varies between weeks, so compare *percentages*, not raw counts.")
    A("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT}  ({len(rows):,} rows, {len(weeks)} weeks)")


if __name__ == "__main__":
    main()
