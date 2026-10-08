# Synthetic Monitor — Week-on-Week Report

**Period:** 2026-07-26 → 2026-10-08  
**Source:** Supabase `monitoring_runs`  
**Rows analysed:** 63,540 checks across 12 ISO weeks  
**Generated:** 08 Oct 2026 17:23 UTC

---

## 1. Executive summary

- Overall pass rate went **40.1% → 81.7%** over the period (best week **2026-W41** at 81.7%).
- **Infrastructure:** API 96.5%, server 97.1% in 2026-W41.
- **Web performance:** Lighthouse score 49.0 → 56.8 (+15.9%) comparing 2026-W40+2026-W41 against 2026-W38+2026-W39.
- **The remaining drag is `lighthouse`**, ~83% of failures in the last 2 weeks.
- `home` is the weakest Lighthouse page (score 47.4).

---

## 2. Overall health — week on week

| Week | Checks | Pass | Degraded | Fail | Pass rate |
|---|---:|---:|---:|---:|---:|
| 2026-W30 | 504 | 202 | 45 | 257 | **40.1%** |
| 2026-W31 | 8,066 | 4,632 | 649 | 2,785 | **57.4%** |
| 2026-W32 | 6,185 | 3,533 | 506 | 2,146 | **57.1%** |
| 2026-W33 | 10,002 | 6,602 | 767 | 2,633 | **66.0%** |
| 2026-W34 | 13,017 | 10,476 | 728 | 1,813 | **80.5%** |
| 2026-W35 | 4,216 | 3,132 | 264 | 820 | **74.3%** |
| 2026-W36 | 4,352 | 3,100 | 309 | 943 | **71.2%** |
| 2026-W37 | 3,719 | 2,682 | 238 | 799 | **72.1%** |
| 2026-W38 | 3,796 | 2,728 | 217 | 851 | **71.9%** |
| 2026-W39 | 5,066 | 3,774 | 747 | 545 | **74.5%** |
| 2026-W40 | 3,009 | 2,336 | 412 | 261 | **77.6%** |
| 2026-W41 | 1,608 | 1,314 | 173 | 121 | **81.7%** |

---

## 3. Pass rate by check family

| Family | W30 | W31 | W32 | W33 | W34 | W35 | W36 | W37 | W38 | W39 | W40 | W41 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **happy_flow** | 68.3% | 74.7% | 79.4% | 83.3% | 87.4% | 84.0% | 81.5% | 84.5% | 83.6% | 82.6% | 87.6% | 97.8% |
| **feature** | 58.8% | 55.5% | 49.4% | 50.9% | 74.0% | 74.1% | 74.3% | 73.5% | 75.0% | 92.1% | 98.7% | 100.0% |
| **lighthouse** | 6.1% | 44.6% | 48.2% | 54.1% | 71.1% | 51.7% | 36.6% | 36.3% | 48.2% | 38.6% | 46.6% | 44.0% |
| **api** | 76.5% | 77.8% | 75.9% | 86.1% | 97.0% | 92.8% | 98.8% | 97.4% | 98.6% | 98.1% | 95.8% | 96.5% |
| **server** | 55.2% | 48.1% | 44.6% | 62.1% | 95.2% | 95.5% | 93.8% | 97.8% | 97.7% | 97.7% | 98.5% | 97.1% |
| **meta** | 0.0% | 0.0% | 0.0% | 24.6% | 33.7% | 32.2% | 30.1% | 32.1% | 33.3% | 30.6% | 27.8% | 27.5% |
| **native_happy_flow** | – | – | – | – | – | 66.8% | 70.2% | 86.4% | 40.6% | 92.7% | 65.9% | 100.0% |

---

## 4. Performance metrics — weekly averages

| Metric | W30 | W31 | W32 | W33 | W34 | W35 | W36 | W37 | W38 | W39 | W40 | W41 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Lighthouse performance score | 42.9 | 47.9 | 43.6 | 48.3 | 61 | 48.2 | 36.5 | 37.8 | 46.3 | 51.7 | 57.6 | 56 |
| LCP (ms) | 13707.2 | 8511.1 | 9344.2 | 6515 | 2952.7 | 3553.9 | 5047.6 | 4424.4 | 4745.2 | 3991 | 3076.5 | 3087.9 |
| Total Blocking Time (ms) | 599.9 | 391.8 | 520.8 | 472 | 349 | 533.6 | 734.5 | 726.6 | 447.1 | 327.5 | 263.3 | 268.3 |
| Speed Index (ms) | 19618.8 | 16658.4 | 19352.7 | 13417.5 | 5279.3 | 6567.3 | 8237.2 | 7036.5 | 7396.8 | 8383.2 | 7928.6 | 7437.1 |
| API response (ms) | 1802.9 | 1396.8 | 1737.7 | 1319.9 | 718.4 | 1066.6 | 709 | 859.5 | 691.1 | 744.1 | 715.5 | 803.8 |
| Server response (ms) | 1120.9 | 1364.7 | 1942.5 | 1550 | 1200.1 | 1133.2 | 1438.8 | 1225.7 | 973.2 | 1137.6 | 934.3 | 1020.7 |
| happy-flow step (ms) | 5563.4 | 7904.7 | 8648.3 | 7955.8 | 8542.3 | 9580.4 | 9124.7 | 9117.1 | 8213 | 11177 | 12613.6 | 11847.8 |

---

## 5. Has performance improved recently? — 2026-W40+2026-W41 vs 2026-W38+2026-W39

| Metric | 2026-W38+2026-W39 | 2026-W40+2026-W41 | Change | Verdict |
|---|---:|---:|---:|---|
| Lighthouse performance score | 49 | 56.8 | +15.9% | ✅ improved |
| LCP (ms) | 4368.1 | 3082.2 | -29.4% | ✅ improved |
| Total Blocking Time (ms) | 387.3 | 265.8 | -31.4% | ✅ improved |
| Speed Index (ms) | 7890 | 7682.9 | -2.6% | ✅ improved |
| API response (ms) | 717.6 | 759.6 | +5.9% | ⚠️ worse |
| Server response (ms) | 1055.4 | 977.5 | -7.4% | ✅ improved |
| happy-flow step (ms) | 9695 | 12230.7 | +26.2% | ⚠️ worse |
| **Overall pass rate** | 73.4% | 79.1% | +5.7 pts | ✅ improved |

---

## 6. Lighthouse performance score by page

| Page | W30 | W31 | W32 | W33 | W34 | W35 | W36 | W37 | W38 | W39 | W40 | W41 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| home | 43.6 | 47.3 | 35.3 | 43.6 | 61.3 | 48.9 | 35.9 | 37.6 | 51.3 | 48.7 | 54.1 | 53.1 |
| category | 40.6 | 43.6 | 49.4 | 52.4 | 61.7 | 47.9 | 37.7 | 39.1 | 48.2 | 55.3 | 53.1 | 52.1 |
| product_detail | 44.4 | 52.7 | 45.8 | 48.5 | 60 | 47.8 | 35.9 | 36.7 | 39.3 | 51.1 | 65.5 | 62.8 |

---

## 7. Where the remaining failures are (last 2 weeks)

| Check | Checks | Fail | Degraded |
|---|---:|---:|---:|
| `si_ms` | 272 | 131 | 131 |
| `lcp_ms` | 217 | 75 | 75 |
| `total_duration_ms` | 148 | 0 | 107 |
| `tbt_ms` | 184 | 43 | 43 |
| `cls` | 180 | 38 | 38 |
| `performance_score` | 171 | 30 | 30 |
| `step_web_bargain_flow` | 50 | 0 | 16 |
| `step_mweb_bargain2_flow` | 50 | 0 | 16 |
| `step_mweb_checkout_flow` | 50 | 0 | 16 |
| `step_mweb_bargain_flow` | 50 | 0 | 16 |
| `step_web_search_products` | 50 | 0 | 14 |
| `response_time_ms` | 450 | 3 | 11 |
| `step_web_bargain2_flow` | 50 | 0 | 13 |
| `step_web_checkout_flow` | 50 | 0 | 13 |
| `step_web_category_all_load` | 50 | 0 | 10 |
| `status_code` | 250 | 3 | 7 |
| `step_mweb_category_all_load` | 50 | 0 | 9 |
| `step_android_checkout_flow` | 20 | 6 | 2 |
| `step_android_search_products` | 20 | 0 | 7 |
| `elem_overall_visible` | 7 | 7 | 0 |
| `step_android_home_load` | 20 | 5 | 0 |
| `step_android_home_products_populate` | 20 | 5 | 0 |
| `step_android_banners_check` | 20 | 5 | 0 |
| `step_android_category_load` | 20 | 5 | 0 |
| `step_android_product_detail_load` | 20 | 5 | 0 |

---

## 8. Native Android app

| Week | Checks | Pass | Degraded | Fail | Pass rate |
|---|---:|---:|---:|---:|---:|
| 2026-W30 | – | – | – | – | – |
| 2026-W31 | – | – | – | – | – |
| 2026-W32 | – | – | – | – | – |
| 2026-W33 | – | – | – | – | – |
| 2026-W34 | – | – | – | – | – |
| 2026-W35 | 199 | 133 | 3 | 63 | **66.8%** |
| 2026-W36 | 376 | 264 | 26 | 86 | **70.2%** |
| 2026-W37 | 44 | 38 | 4 | 2 | **86.4%** |
| 2026-W38 | 286 | 116 | 23 | 147 | **40.6%** |
| 2026-W39 | 330 | 306 | 8 | 16 | **92.7%** |
| 2026-W40 | 176 | 116 | 9 | 51 | **65.9%** |
| 2026-W41 | 44 | 44 | 0 | 0 | **100.0%** |

---

## 9. Findings and recommended actions

| # | Finding | Impact | Recommendation |
|---|---|---|---|
| 1 | `home` is the weakest Lighthouse page (score 47.4) | High | Preload the above-the-fold hero image (`fetchpriority=high`); stop lazy-loading it |
| 2 | `lighthouse` accounts for ~83% of recent failures | High | Prioritise that family in the next performance pass |
| 3 | `step_web_bargain_flow` degraded 16× in the last 2 weeks | Medium | Check the step's time budget / selectors |
| 4 | `step_mweb_bargain2_flow` degraded 16× in the last 2 weeks | Medium | Check the step's time budget / selectors |

---

## 10. Methodology and caveats

- Data is every row in `monitoring_runs` between the first and last timestamps above; weeks are ISO (Monday–Sunday).
- A check is `pass`, `degraded` or `fail`. `degraded` usually means slow-but-working (time budget exceeded) or a data-dependent skip, not a hard break.
- **2026-W41 may be a partial week** and can shift as more runs land.
- Weekly averages blend mobile (mweb) and desktop (web) runs; Lighthouse is mobile-only by configuration.
- Run volume varies between weeks, so compare *percentages*, not raw counts.
